"""Abstract puller service.

Each concrete puller orchestrates one cadence:
    1. Read watermark
    2. Read rows from Snowflake using cursor + overlap + max-lookback
    3. For each row: check audit dedup → transform → POST → write audit
    4. If all rows accounted for (sent or DLQ'd): advance watermark
    5. Otherwise: leave watermark for replay
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed
from concurrent.futures import wait as futures_wait
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import structlog

from ..clients.ryder_client import RyderClient, RyderEndpoint, RyderResultStatus
from ..clients.snowflake_client import SnowflakeClient
from ..config import AppSettings
from ..storage.base import (
    AuditEntry,
    AuditStatus,
    AuditStore,
    WatermarkRecord,
    WatermarkStore,
)
from ..transformers.base import PayloadTransformer
from ..transformers.trace_payload import SkipRow
from ..utils.logging import get_logger

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
    ) -> None:
        self._settings = settings
        self._snowflake = snowflake
        self._ryder = ryder
        self._watermarks = watermarks
        self._audit = audit
        self._transformer = transformer
        self._sql = sql
        self._candidates_sql = candidates_sql

    # --- public ---

    def run(self) -> RunResult:
        run_started = _now_utc()
        cursor_start = self._compute_cursor_start(run_started)
        window_hours = round((run_started - cursor_start).total_seconds() / 3600, 2)
        log = logger.bind(
            pipeline=self.pipeline_name,
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

        seen = sent = dedup = dlq = invalid = transient = 0
        max_workers = self._settings.ryder_max_concurrency

        def _tally(outcome: str) -> None:
            nonlocal sent, dedup, dlq, invalid, transient
            if outcome == "sent":
                sent += 1
            elif outcome == "dedup":
                dedup += 1
            elif outcome == "dlq":
                dlq += 1
            elif outcome == "invalid":
                invalid += 1
            elif outcome == "transient":
                transient += 1

        def _drain(futures) -> None:  # accepts set or as_completed iterator
            """Collect results from completed futures, update counters."""
            nonlocal seen, dlq
            for f in futures:
                seen += 1
                try:
                    _tally(f.result())
                except Exception as exc:
                    log.exception("row_failed_unexpected", error=str(exc))
                    dlq += 1

        params = self._build_query_params(cursor_start, run_started)
        self._log_candidate_counts(params, log=log)

        # Process rows concurrently — at most max_workers in-flight at any time.
        # Snowflake rows are fetched lazily so memory stays bounded regardless of
        # total result size. Counter updates happen on the main thread (no locks needed).
        pending: set = set()
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
            )
            return RunResult(
                status=RunStatus.FAILED_TRANSIENT,
                rows_seen=seen,
                rows_sent=sent,
                rows_skipped_dedup=dedup,
                rows_dlq=dlq,
                rows_skipped_invalid=invalid,
                rows_transient_failed=transient,
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
            rows_skipped_invalid=invalid,
        )
        return RunResult(
            status=status,
            rows_seen=seen,
            rows_sent=sent,
            rows_skipped_dedup=dedup,
            rows_dlq=dlq,
            rows_skipped_invalid=invalid,
            rows_transient_failed=0,
        )

    # --- to be overridden by subclasses ---

    @abstractmethod
    def _build_query_params(self, cursor_start: datetime, run_started: datetime) -> dict[str, Any]:
        """Return bind params for the SQL query."""

    # --- internals ---

    def _compute_cursor_start(self, run_started: datetime) -> datetime:
        """Compute the lower-bound timestamp for the pull window.

        Uses last successful watermark minus overlap; clamped to max lookback
        so a long outage doesn't trigger an unbounded scan.
        """
        wm = self._watermarks.get(self.pipeline_name)
        overlap = timedelta(minutes=self._settings.watermark_overlap_minutes)
        max_lookback = timedelta(hours=self._settings.watermark_max_lookback_hours)

        if wm is None:
            return run_started - max_lookback

        unclamped = wm.last_synced_at_utc - overlap
        floor = run_started - max_lookback
        return max(unclamped, floor)

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

    def _handle_row(self, row: dict[str, Any], *, log: structlog.stdlib.BoundLogger) -> str:
        """Process one row. Returns the outcome label for counters."""
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
            return "invalid"

        log.info(
            "ryder_payload",
            message=(
                f"Sending to Ryder /{self.endpoint.value} for ship_id={ship_id} "
                f"(natural_key={transformed.natural_key})"
            ),
            natural_key=transformed.natural_key,
            payload=transformed.payload,
        )

        existing = self._audit.get(self.pipeline_name, transformed.natural_key)
        if existing is not None:
            log.info(
                "row_skipped_dedup",
                message=f"Skipped (already sent) natural_key={transformed.natural_key}",
                natural_key=transformed.natural_key,
            )
            return "dedup"

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

        if result.status == RyderResultStatus.SENT:
            self._audit.upsert(
                AuditEntry(
                    pipeline=self.pipeline_name,
                    natural_key=transformed.natural_key,
                    status=AuditStatus.SENT,
                    response_code=result.response_code,
                    response_body=result.response_body[:8000],
                    sent_at_utc=_now_utc(),
                    failed_at_utc=None,
                )
            )
            return "sent"

        if result.status == RyderResultStatus.FAILED_PERMANENTLY:
            log.error(
                "row_failed_permanently",
                response_code=result.response_code,
                response_body=result.response_body[:500],
                natural_key=transformed.natural_key,
            )
            self._audit.upsert(
                AuditEntry(
                    pipeline=self.pipeline_name,
                    natural_key=transformed.natural_key,
                    status=AuditStatus.FAILED_PERMANENTLY,
                    response_code=result.response_code,
                    response_body=result.response_body[:8000],
                    sent_at_utc=None,
                    failed_at_utc=_now_utc(),
                )
            )
            return "dlq"

        # Transient — don't write audit; the tick will fail to advance and replay.
        log.warning(
            "row_failed_transient",
            response_code=result.response_code,
            attempts=result.attempts,
            natural_key=transformed.natural_key,
        )
        return "transient"


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