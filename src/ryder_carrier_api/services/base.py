"""Abstract puller service.

Each concrete puller orchestrates one cadence:
    1. Read watermark
    2. Read rows from Snowflake using watermark cursor + overlap
       (cold-start lookback only on the first run)
    3. For each row: check audit dedup → transform → POST → write audit
    4. If all rows accounted for (sent or DLQ'd): advance watermark
    5. Otherwise: leave watermark for replay
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import uuid4

import structlog

from ..clients.ryder_client import RyderClient, RyderEndpoint, RyderResult, RyderResultStatus
from ..clients.snowflake_client import SnowflakeClient
from ..config import AppSettings
from ..storage.base import (
    AuditEntry,
    AuditStatus,
    AuditStore,
    DeadLetterRecord,
    DeadLetterStore,
    NullDeadLetterStore,
    TransientStorageError,
    WatermarkRecord,
    WatermarkStore,
)
from ..transformers.base import PayloadTransformer, TransformedPayload
from ..transformers.trace_payload import SkipRow
from ..utils.logging import get_logger
from ..utils.natural_key import natural_key_hash

logger = get_logger(__name__)


class RunStatus(StrEnum):
    SUCCESS = "success"
    FAILED_TRANSIENT = "failed_transient"
    NO_DATA = "no_data"


@dataclass(frozen=True)
class RunResult:
    status: RunStatus
    rows_seen: int
    rows_sent: int
    rows_skipped_dedup: int
    rows_dlq: int
    rows_skipped_invalid: int
    rows_transient_failed: int
    rows_throttled: int = 0


class PullerService(ABC):
    """Template-method orchestrator. Subclasses only need to declare:
    - pipeline_name (e.g. "trace")
    - endpoint (Ryder endpoint enum)
    - SQL string
    - transformer
    """

    pipeline_name: str
    endpoint: RyderEndpoint

    def __init__(
        self,
        settings: AppSettings,
        snowflake: SnowflakeClient,
        ryder: RyderClient,
        watermarks: WatermarkStore,
        audit: AuditStore,
        transformer: PayloadTransformer,
        sql: str,
        candidates_sql: str | None = None,
        dead_letter: DeadLetterStore | None = None,
    ) -> None:
        self._settings = settings
        self._snowflake = snowflake
        self._ryder = ryder
        self._watermarks = watermarks
        self._audit = audit
        self._transformer = transformer
        self._sql = sql
        self._candidates_sql = candidates_sql
        self._dead_letter = dead_letter or NullDeadLetterStore()

    # --- public ---

    def run(self) -> RunResult:
        run_started = _now_utc()
        run_id = str(uuid4())
        log = logger.bind(run_id=run_id, pipeline=self.pipeline_name)
        cursor_start = self._compute_cursor_start(run_started, log=log)
        window_hours = round((run_started - cursor_start).total_seconds() / 3600, 2)
        log = log.bind(
            cursor_start=cursor_start.isoformat(),
            run_started=run_started.isoformat(),
            window_hours=window_hours,
        )
        log.info(
            "puller_run_start",
            message=(
                f"Running {self.pipeline_name} for window "
                f"{cursor_start.isoformat()} → {run_started.isoformat()} "
                f"({window_hours}h, {self._settings.ryder_max_concurrency} workers)"
            ),
        )

        seen = sent = dedup = dlq = invalid = transient = throttled = 0
        dlq_codes: dict[str, int] = {}
        max_workers = self._settings.ryder_max_concurrency

        def _tally(result: tuple[str, bool]) -> None:
            nonlocal sent, dedup, dlq, invalid, transient, throttled
            outcome, was_throttled = result
            if was_throttled:
                throttled += 1
            if outcome == "sent":
                sent += 1
            elif outcome.startswith("dlq"):
                dlq += 1
                reason = outcome[4:] if ":" in outcome else "unknown"
                dlq_codes[reason] = dlq_codes.get(reason, 0) + 1
            elif outcome == "dedup":
                dedup += 1
            elif outcome == "invalid":
                invalid += 1
            elif outcome == "transient":
                transient += 1

        def _drain(futures: Any) -> None:  # accepts set or as_completed iterator
            """Collect results from completed futures, update counters."""
            nonlocal seen, transient
            for f in futures:
                seen += 1
                try:
                    _tally(f.result())
                except Exception as exc:
                    # Unexpected escape from _handle_row (a bug we didn't
                    # classify). Fail closed — count transient so the watermark
                    # stalls and we replay, rather than silently dropping the row.
                    log.exception("row_failed_unexpected", error=str(exc))
                    transient += 1

        params = self._build_query_params(cursor_start, run_started)
        self._log_candidate_counts(params, log=log)

        # Process rows concurrently — at most max_workers in-flight at any time.
        # Snowflake rows are fetched lazily so memory stays bounded regardless of
        # total result size. Counter updates happen on the main thread (no locks needed).
        pending: set[Any] = set()
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            for row in self._snowflake.fetch_rows(self._sql, params=params):
                if len(pending) >= max_workers:
                    done, pending = futures_wait(pending, return_when=FIRST_COMPLETED)
                    _drain(done)
                pending.add(executor.submit(self._handle_row, row, log=log))
            _drain(as_completed(pending))

        if transient > 0:
            log.warning(
                "puller_run_transient_failures",
                rows_transient_failed=transient,
                rows_sent=sent,
                rows_seen=seen,
                rows_throttled=throttled,
            )
            return RunResult(
                status=RunStatus.FAILED_TRANSIENT,
                rows_seen=seen,
                rows_sent=sent,
                rows_skipped_dedup=dedup,
                rows_dlq=dlq,
                rows_skipped_invalid=invalid,
                rows_transient_failed=transient,
                rows_throttled=throttled,
            )

        # All accounted for — advance the watermark.
        self._watermarks.set(
            WatermarkRecord(
                pipeline=self.pipeline_name,
                last_synced_at_utc=run_started,
                last_run_status="success",
                last_run_at_utc=run_started,
            )
        )
        status = RunStatus.NO_DATA if seen == 0 else RunStatus.SUCCESS
        log.info(
            "puller_run_complete",
            rows_seen=seen,
            rows_sent=sent,
            rows_skipped_dedup=dedup,
            rows_dlq=dlq,
            rows_dlq_by_reason=dlq_codes,
            rows_skipped_invalid=invalid,
            rows_throttled=throttled,
        )
        return RunResult(
            status=status,
            rows_seen=seen,
            rows_sent=sent,
            rows_skipped_dedup=dedup,
            rows_dlq=dlq,
            rows_skipped_invalid=invalid,
            rows_transient_failed=0,
            rows_throttled=throttled,
        )

    # --- to be overridden by subclasses ---

    @abstractmethod
    def _build_query_params(self, cursor_start: datetime, run_started: datetime) -> dict[str, Any]:
        """Return bind params for the SQL query."""

    # --- internals ---

    def _compute_cursor_start(
        self, run_started: datetime, *, log: structlog.stdlib.BoundLogger = logger
    ) -> datetime:
        """Compute the lower-bound timestamp for the pull window.

        Cold start (no watermark yet): look back exactly
        ``watermark_max_lookback_minutes`` from now. Prod sets this to 1 so the
        very first run doesn't backfill historical data.

        Steady state (watermark exists): resume from the last successful
        watermark minus the overlap buffer, so no rows are missed between runs
        (audit dedup guards against duplicates).

        Catch-up cap: after a long outage the watermark can fall far behind
        ``now``. We refuse to replay more than ``watermark_max_catchup_minutes``
        — re-querying an ever-growing window risks the run timing out before it
        can even dedup-scan the backlog (a livelock). Beyond the cap we clamp
        the cursor forward and log ``puller_catchup_capped`` so the deliberately
        skipped gap is visible rather than silent.
        """
        wm = self._watermarks.get(self.pipeline_name)

        if wm is None:
            cold_start = timedelta(minutes=self._settings.watermark_max_lookback_minutes)
            return run_started - cold_start

        overlap = timedelta(minutes=self._settings.watermark_overlap_minutes)
        cursor = wm.last_synced_at_utc - overlap

        floor = run_started - timedelta(minutes=self._settings.watermark_max_catchup_minutes)
        if cursor < floor:
            skipped_hours = round((floor - cursor).total_seconds() / 3600, 2)
            log.warning(
                "puller_catchup_capped",
                message=(
                    f"Watermark older than the "
                    f"{self._settings.watermark_max_catchup_minutes}-minute catch-up cap; "
                    f"skipping {skipped_hours}h ({cursor.isoformat()} → {floor.isoformat()})."
                ),
                watermark=wm.last_synced_at_utc.isoformat(),
                capped_cursor_start=floor.isoformat(),
                skipped_hours=skipped_hours,
            )
            return floor
        return cursor

    def _log_candidate_counts(
        self, params: dict[str, Any], *, log: structlog.stdlib.BoundLogger
    ) -> None:
        """Emit a `puller_candidates` log line with before/after Ship ID counts.

        Skipped when either:
          - the diagnostics flag is off (flip after Ship ID remap is stable), or
          - the subclass didn't supply a candidates SQL.

        Any query failure is logged but never aborts the run — diagnostics
        must never block the real pull.
        """
        if not self._settings.enable_candidate_diagnostics or self._candidates_sql is None:
            return
        try:
            rows = list(self._snowflake.fetch_rows(self._candidates_sql, params=params))
        except Exception as exc:
            log.warning("puller_candidates_query_failed", error=str(exc))
            return
        if not rows:
            log.info("puller_candidates", rows_before_ship_id_filter=0, rows_with_ship_id=0)
            return
        log.info("puller_candidates", **{k.lower(): v for k, v in rows[0].items()})

    def _handle_row(
        self, row: dict[str, Any], *, log: structlog.stdlib.BoundLogger
    ) -> tuple[str, bool]:
        """Process one row. Returns (outcome label, was_throttled) for counters.

        Failure handling, by kind:
          - transform/data error (deterministic) -> dead-letter the raw row, advance
          - Ryder 4xx rejection                  -> dead-letter the payload, advance
          - transient (Ryder 5xx/429, storage blip) -> stall + replay, counting
            attempts; after `ryder_max_transient_attempts` a persistently-failing
            row is dead-lettered so it can't stall the shared watermark forever
        """
        jsonable_row = _jsonable(row)
        ship_id = jsonable_row.get("SHIP_ID")
        log.info(
            "snowflake_row",
            message=f"Pulled row from Snowflake (ship_id={ship_id})",
            row=jsonable_row,
        )

        try:
            transformed = self._transformer.transform(row)
        except SkipRow as exc:
            log.warning(
                "row_skipped_invalid",
                message=f"Skipped row (ship_id={ship_id}): {exc}",
                reason=str(exc),
            )
            return "invalid", False
        except Exception as exc:
            # A non-SkipRow transform error is deterministic — it would fail the
            # same way on every replay. Capture the source row and advance rather
            # than stalling the whole pipeline on one bad record.
            log.exception("row_transform_error", ship_id=ship_id, error=str(exc))
            self._safe_dead_letter(
                DeadLetterRecord(
                    pipeline=self.pipeline_name,
                    key=self._fallback_key(jsonable_row),
                    reason="row_error",
                    failed_at_utc=_now_utc(),
                    raw_row=jsonable_row,
                    error=str(exc),
                ),
                log=log,
            )
            return "dlq:row_error", False

        log.info(
            "ryder_payload",
            message=(
                f"Sending to Ryder /{self.endpoint.value} for ship_id={ship_id} "
                f"(natural_key={transformed.natural_key})"
            ),
            natural_key=transformed.natural_key,
            payload=transformed.payload,
        )

        try:
            existing = self._audit.get(self.pipeline_name, transformed.natural_key)
        except TransientStorageError as exc:
            # Couldn't even check dedup — nothing was sent, so just replay.
            log.warning(
                "audit_read_failed_transient",
                natural_key=transformed.natural_key,
                error=str(exc),
            )
            return "transient", False

        prior_attempts = 0
        if existing is not None:
            if existing.status in (AuditStatus.SENT, AuditStatus.FAILED_PERMANENTLY):
                log.info(
                    "row_skipped_dedup",
                    message=(
                        f"Skipped (terminal: {existing.status.value}) "
                        f"natural_key={transformed.natural_key}"
                    ),
                    natural_key=transformed.natural_key,
                )
                return "dedup", False
            # RETRYING is non-terminal — carry the attempt count forward and retry.
            prior_attempts = existing.transient_attempts

        result = self._ryder.post(self.endpoint, transformed.payload)
        log.info(
            "ryder_response",
            message=(
                f"Ryder responded {result.response_code} "
                f"({result.status.value}) for natural_key={transformed.natural_key}"
            ),
            natural_key=transformed.natural_key,
            response_code=result.response_code,
            response_body=result.response_body[:2000],
            attempts=result.attempts,
            status=result.status.value,
        )

        if result.throttled:
            log.warning(
                "ryder_throttled",
                message=f"Ryder rate-limited (429) for natural_key={transformed.natural_key}",
                natural_key=transformed.natural_key,
                response_code=result.response_code,
                attempts=result.attempts,
            )

        if result.status == RyderResultStatus.SENT:
            try:
                self._audit.upsert(self._audit_entry(transformed, result, AuditStatus.SENT))
            except TransientStorageError as exc:
                # Delivered, but we couldn't record it. Do NOT replay (that would
                # risk a duplicate at Ryder) — count it as sent. A rare unrelated
                # replay could re-send; acceptable vs. a guaranteed duplicate.
                log.error(
                    "audit_write_failed_after_send",
                    natural_key=transformed.natural_key,
                    error=str(exc),
                )
            return "sent", result.throttled

        if result.status == RyderResultStatus.FAILED_PERMANENTLY:
            log.error(
                "row_failed_permanently",
                response_code=result.response_code,
                response_body=result.response_body[:500],
                natural_key=transformed.natural_key,
            )
            self._safe_dead_letter(
                DeadLetterRecord(
                    pipeline=self.pipeline_name,
                    key=transformed.natural_key,
                    reason="ryder_rejected",
                    failed_at_utc=_now_utc(),
                    payload=transformed.payload,
                    response_code=result.response_code,
                    response_body=result.response_body[:8000],
                    load_number=transformed.load_number,
                ),
                log=log,
            )
            if not self._mark_terminal_failed(transformed, result, log=log):
                return "transient", result.throttled
            return f"dlq:{result.response_code}", result.throttled

        # Transient — bounded retry across runs before giving up.
        attempts = prior_attempts + 1
        if attempts >= self._settings.ryder_max_transient_attempts:
            log.error(
                "row_transient_exhausted",
                natural_key=transformed.natural_key,
                attempts=attempts,
                response_code=result.response_code,
            )
            self._safe_dead_letter(
                DeadLetterRecord(
                    pipeline=self.pipeline_name,
                    key=transformed.natural_key,
                    reason="transient_exhausted",
                    failed_at_utc=_now_utc(),
                    payload=transformed.payload,
                    response_code=result.response_code,
                    response_body=result.response_body[:8000],
                    error=f"{attempts} consecutive transient failures",
                    load_number=transformed.load_number,
                ),
                log=log,
            )
            if not self._mark_terminal_failed(transformed, result, log=log):
                return "transient", result.throttled
            return "dlq:transient_exhausted", result.throttled

        log.warning(
            "row_failed_transient",
            response_code=result.response_code,
            attempts=result.attempts,
            transient_attempts=attempts,
            natural_key=transformed.natural_key,
        )
        # Persist the incremented attempt count so the next run resumes it. A
        # storage blip here just means the count doesn't advance this round — the
        # row is transient regardless, so we replay either way.
        try:
            self._audit.upsert(
                self._audit_entry(transformed, result, AuditStatus.RETRYING, attempts=attempts)
            )
        except TransientStorageError as exc:
            log.warning(
                "audit_write_failed_transient",
                natural_key=transformed.natural_key,
                error=str(exc),
            )
        return "transient", result.throttled

    # --- audit / dead-letter helpers ---

    def _audit_entry(
        self,
        transformed: TransformedPayload,
        result: RyderResult,
        status: AuditStatus,
        *,
        attempts: int = 0,
    ) -> AuditEntry:
        now = _now_utc()
        return AuditEntry(
            pipeline=self.pipeline_name,
            natural_key=transformed.natural_key,
            status=status,
            response_code=result.response_code,
            response_body=result.response_body[:8000],
            sent_at_utc=now if status == AuditStatus.SENT else None,
            failed_at_utc=None if status == AuditStatus.SENT else now,
            load_number=transformed.load_number,
            event_type=transformed.event_type,
            event_code=transformed.event_code,
            transient_attempts=attempts,
        )

    def _mark_terminal_failed(
        self,
        transformed: TransformedPayload,
        result: RyderResult,
        *,
        log: structlog.stdlib.BoundLogger,
    ) -> bool:
        """Write the terminal FAILED_PERMANENTLY audit row. Returns False if a
        transient storage error blocked it — the caller then replays to retry
        (the dead-letter blob is already written, so the retry is idempotent)."""
        try:
            self._audit.upsert(
                self._audit_entry(transformed, result, AuditStatus.FAILED_PERMANENTLY)
            )
            return True
        except TransientStorageError as exc:
            log.warning(
                "audit_write_failed_transient",
                natural_key=transformed.natural_key,
                error=str(exc),
            )
            return False

    def _safe_dead_letter(
        self, record: DeadLetterRecord, *, log: structlog.stdlib.BoundLogger
    ) -> None:
        """Persist a dead-letter record; never let a DLQ-store failure crash the
        row handler — we still want to mark the row terminal and keep moving."""
        try:
            self._dead_letter.put(record)
            log.info("row_dead_lettered", key=record.key, reason=record.reason)
        except Exception as exc:
            log.error(
                "dead_letter_write_failed",
                key=record.key,
                reason=record.reason,
                error=str(exc),
            )

    @staticmethod
    def _fallback_key(jsonable_row: dict[str, Any]) -> str:
        """Deterministic dead-letter key for rows that failed before a
        natural_key existed (transform errors)."""
        return "rowerror-" + natural_key_hash(json.dumps(jsonable_row, sort_keys=True, default=str))


def _now_utc() -> datetime:
    return datetime.now(tz=UTC)


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce Snowflake row values into JSON-serializable forms for logging."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "__float__") and not isinstance(v, int | float | bool):
            out[k] = float(v)
        else:
            out[k] = v
    return out
