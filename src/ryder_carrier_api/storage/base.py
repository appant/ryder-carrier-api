"""Abstract storage interfaces — watermark cursor and sent-audit dedup/DLQ.

Concrete implementation (Azure Table Storage) lives in `table_storage.py`.
Other backends (Cosmos DB, SQL) would slot in by inheriting these ABCs.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

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

    `sent` and `failed_permanently` are terminal: those rows are skipped on
    replay. Transient failures are never written — they cause the tick to
    fail so the watermark stalls and the window replays next time.
    """

    SENT = "sent"
    FAILED_PERMANENTLY = "failed_permanently"


@dataclass(frozen=True)
class AuditEntry:
    pipeline: str
    natural_key: str  # sha256 hash of loadNumber + event_type + actual_event_at
    status: AuditStatus
    response_code: int | None
    response_body: str
    sent_at_utc: datetime | None
    failed_at_utc: datetime | None


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
