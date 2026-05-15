"""In-memory WatermarkStore and AuditStore for local development.

State is lost when the process exits — never use in production. Useful when
you want to exercise the full pipeline locally without standing up Azurite or
an Azure storage account.
"""

from __future__ import annotations

from datetime import datetime

from .base import (
    AuditEntry,
    AuditStore,
    WatermarkRecord,
    WatermarkStore,
)


class InMemoryWatermarkStore(WatermarkStore):
    def __init__(self) -> None:
        self._records: dict[str, WatermarkRecord] = {}

    def get(self, pipeline: str) -> WatermarkRecord | None:
        return self._records.get(pipeline)

    def set(self, record: WatermarkRecord) -> None:
        self._records[record.pipeline] = record


class InMemoryAuditStore(AuditStore):
    def __init__(self) -> None:
        self._entries: dict[tuple[str, str], AuditEntry] = {}

    def get(self, pipeline: str, natural_key: str) -> AuditEntry | None:
        return self._entries.get((pipeline, natural_key))

    def upsert(self, entry: AuditEntry) -> None:
        self._entries[(entry.pipeline, entry.natural_key)] = entry

    def delete_older_than(self, pipeline: str, cutoff_utc: datetime) -> int:
        deleted = 0
        for key in list(self._entries.keys()):
            if key[0] != pipeline:
                continue
            entry = self._entries[key]
            terminal = entry.sent_at_utc or entry.failed_at_utc
            if terminal is not None and terminal < cutoff_utc:
                del self._entries[key]
                deleted += 1
        return deleted
