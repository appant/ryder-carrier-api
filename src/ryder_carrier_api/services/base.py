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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
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


class RunStatus(str, Enum):
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
    ) -> None:
        self._settings = settings
        self._snowflake = snowflake
        self._ryder = ryder
        self._watermarks = watermarks
        self._audit = audit
        self._transformer = transformer
        self._sql = sql

    # --- public ---

    def run(self) -> RunResult:
        run_started = _now_utc()
        cursor_start = self._compute_cursor_start(run_started)
        log = logger.bind(
            pipeline=self.pipeline_name,
            cursor_start=cursor_start.isoformat(),
            run_started=run_started.isoformat(),
        )
        log.info("puller_run_start")

        seen = sent = dedup = dlq = invalid = transient = 0

        params = self._build_query_params(cursor_start, run_started)
        for row in self._snowflake.fetch_rows(self._sql, params=params):
            seen += 1
            outcome = self._handle_row(row, log=log)
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

    def _handle_row(self, row: dict[str, Any], *, log: structlog.stdlib.BoundLogger) -> str:
        """Process one row. Returns the outcome label for counters."""
        log.info("snowflake_row", row=_jsonable(row))
        try:
            transformed = self._transformer.transform(row)
        except SkipRow as exc:
            log.warning("row_skipped_invalid", reason=str(exc))
            return "invalid"

        log.info(
            "ryder_payload",
            natural_key=transformed.natural_key,
            payload=transformed.payload,
        )

        existing = self._audit.get(self.pipeline_name, transformed.natural_key)
        if existing is not None:
            log.info("row_skipped_dedup", natural_key=transformed.natural_key)
            return "dedup"

        result = self._ryder.post(self.endpoint, transformed.payload)
        log.info(
            "ryder_response",
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
