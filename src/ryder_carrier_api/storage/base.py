"""Abstract storage interfaces — watermark cursor and sent-audit dedup/DLQ.

Concrete implementation (Azure Table Storage) lives in `table_storage.py`.
Other backends (Cosmos DB, SQL) would slot in by inheriting these ABCs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

# =============================================================================
# Watermark
# =============================================================================


@dataclass(frozen=True)
class WatermarkRecord:
    pipeline: str
    last_synced_at_utc: datetime
    last_run_status: str
    last_run_at_utc: datetime


class WatermarkStore(ABC):
    """Single-row-per-pipeline cursor of the most recently synced timestamp."""

    @abstractmethod
    def get(self, pipeline: str) -> WatermarkRecord | None:
        """Return the watermark for this pipeline, or None if never run."""

    @abstractmethod
    def set(self, record: WatermarkRecord) -> None:
        """Persist the watermark. Idempotent — overwrites existing row."""


# =============================================================================
# Sent-audit
# =============================================================================


class AuditStatus(StrEnum):
    """Status of a row's delivery attempt.

    `sent` and `failed_permanently` are terminal — those rows are skipped on
    replay. `retrying` is NON-terminal: it records the count of consecutive
    transient failures for a natural key (so a row that keeps failing
    transiently is eventually dead-lettered instead of stalling the watermark
    forever) and is deliberately NOT skipped on replay.
    """

    SENT = "sent"
    FAILED_PERMANENTLY = "failed_permanently"
    RETRYING = "retrying"


@dataclass(frozen=True)
class AuditEntry:
    pipeline: str
    natural_key: str  # sha256 hash of loadNumber + event_type + actual_event_at
    status: AuditStatus
    response_code: int | None
    response_body: str
    sent_at_utc: datetime | None
    failed_at_utc: datetime | None
    load_number: str | None = None
    event_type: str | None = None
    event_code: str | None = None
    # Count of consecutive transient delivery failures for this key. Drives the
    # bounded-retry-then-dead-letter behavior; only meaningful for `retrying`.
    transient_attempts: int = 0


class AuditStore(ABC):
    """Tracks which natural keys have been delivered, used for dedup + DLQ.

    The same table serves two purposes:
      - `status=sent`              -> skip; already delivered (idempotency)
      - `status=failed_permanently` -> skip; Ryder rejected; ops will review
    """

    @abstractmethod
    def get(self, pipeline: str, natural_key: str) -> AuditEntry | None:
        """Return the audit row if present, else None."""

    @abstractmethod
    def upsert(self, entry: AuditEntry) -> None:
        """Insert or replace the audit row."""

    @abstractmethod
    def delete_older_than(self, pipeline: str, cutoff_utc: datetime) -> int:
        """Delete rows whose terminal timestamp is older than the cutoff.

        Returns the number of rows deleted.
        """


class TransientStorageError(Exception):
    """A storage operation failed for a transient/retryable reason (throttling,
    5xx, network). Callers treat this like a transient delivery failure — stall
    the watermark and replay — rather than silently dropping the row."""


# =============================================================================
# Dead-letter — full payload capture for rows we give up on
# =============================================================================


@dataclass(frozen=True)
class DeadLetterRecord:
    """Everything needed to inspect and re-POST a failed row after a fix."""

    pipeline: str
    key: str  # natural_key, or a fallback hash when the failure pre-dates it
    reason: str  # "ryder_rejected" | "transient_exhausted" | "row_error"
    failed_at_utc: datetime
    payload: dict[str, Any] | None = None  # the body we built/sent, if any
    raw_row: dict[str, Any] | None = None  # the source row, for transform failures
    response_code: int | None = None
    response_body: str | None = None
    error: str | None = None
    load_number: str | None = None


class DeadLetterStore(ABC):
    """Persists the full context of a dead-lettered row so ops can inspect and
    replay it. Keyed by (pipeline, key); re-failing the same key overwrites."""

    @abstractmethod
    def put(self, record: DeadLetterRecord) -> None:
        """Persist (or overwrite) the dead-letter record."""


class NullDeadLetterStore(DeadLetterStore):
    """No-op sink — used when no blob storage is configured (e.g. unit tests)."""

    def put(self, record: DeadLetterRecord) -> None:
        return None
