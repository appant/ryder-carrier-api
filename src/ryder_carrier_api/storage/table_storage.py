"""Azure Table Storage implementations of WatermarkStore and AuditStore."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import ResourceNotFoundError
from azure.data.tables import TableClient, TableServiceClient, UpdateMode
from azure.identity import DefaultAzureCredential

from .base import (
    AuditEntry,
    AuditStatus,
    AuditStore,
    WatermarkRecord,
    WatermarkStore,
)

# Common partition key for the single-pipeline-per-row watermark table.
# Keeps everything in one partition for fast lookup; volume is trivial.
_WATERMARK_PARTITION = "pipeline"


def _build_table_client(
    storage_account_url: str,
    table_name: str,
    credential: DefaultAzureCredential | None = None,
    connection_string: str | None = None,
) -> TableClient:
    """Create a TableClient, ensuring the table exists.

    If `connection_string` is provided, uses shared-key auth (Azurite path).
    Otherwise uses `endpoint + DefaultAzureCredential` (real Azure path).
    """
    if connection_string:
        service = TableServiceClient.from_connection_string(connection_string)
    else:
        service = TableServiceClient(
            endpoint=storage_account_url,
            credential=credential or DefaultAzureCredential(),
        )
    service.create_table_if_not_exists(table_name)
    return service.get_table_client(table_name)


# =============================================================================
# Watermark
# =============================================================================


class TableStorageWatermarkStore(WatermarkStore):
    def __init__(
        self,
        storage_account_url: str,
        table_name: str,
        credential: DefaultAzureCredential | None = None,
        connection_string: str | None = None,
    ) -> None:
        self._client = _build_table_client(
            storage_account_url, table_name, credential, connection_string
        )

    def get(self, pipeline: str) -> WatermarkRecord | None:
        try:
            entity = self._client.get_entity(partition_key=_WATERMARK_PARTITION, row_key=pipeline)
        except ResourceNotFoundError:
            return None
        return WatermarkRecord(
            pipeline=pipeline,
            last_synced_at_utc=_to_utc(entity["last_synced_at_utc"]),
            last_run_status=entity.get("last_run_status", ""),
            last_run_at_utc=_to_utc(entity["last_run_at_utc"]),
        )

    def set(self, record: WatermarkRecord) -> None:
        entity: dict[str, Any] = {
            "PartitionKey": _WATERMARK_PARTITION,
            "RowKey": record.pipeline,
            "last_synced_at_utc": record.last_synced_at_utc,
            "last_run_status": record.last_run_status,
            "last_run_at_utc": record.last_run_at_utc,
        }
        self._client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)


# =============================================================================
# Audit
# =============================================================================


class TableStorageAuditStore(AuditStore):
    """One Table Storage table holds both 'sent' and 'failed_permanently' rows.

    PartitionKey = pipeline name (`trace` / `milestone`)
    RowKey       = natural key hash (sha256)
    """

    def __init__(
        self,
        storage_account_url: str,
        table_name: str,
        credential: DefaultAzureCredential | None = None,
        connection_string: str | None = None,
    ) -> None:
        self._client = _build_table_client(
            storage_account_url, table_name, credential, connection_string
        )

    def get(self, pipeline: str, natural_key: str) -> AuditEntry | None:
        try:
            entity = self._client.get_entity(partition_key=pipeline, row_key=natural_key)
        except ResourceNotFoundError:
            return None
        return _entity_to_entry(entity)

    def upsert(self, entry: AuditEntry) -> None:
        entity: dict[str, Any] = {
            "PartitionKey": entry.pipeline,
            "RowKey": entry.natural_key,
            "status": entry.status.value,
            "response_code": entry.response_code,
            "response_body": _truncate(entry.response_body, 32_000),
            "sent_at_utc": entry.sent_at_utc,
            "failed_at_utc": entry.failed_at_utc,
        }
        self._client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)

    def delete_older_than(self, pipeline: str, cutoff_utc: datetime) -> int:
        """Delete audit rows whose terminal timestamp is older than the cutoff.

        Table Storage does not support batch deletes across partitions, but
        within a partition we can issue batched delete operations. For our
        volume (a few thousand deletes per month) plain per-row delete is
        fine and simpler.
        """
        cutoff_iso = cutoff_utc.isoformat()
        query = (
            f"PartitionKey eq '{pipeline}' and "
            f"((sent_at_utc lt datetime'{cutoff_iso}') or "
            f"(failed_at_utc lt datetime'{cutoff_iso}'))"
        )
        deleted = 0
        for entity in self._client.query_entities(query_filter=query):
            self._client.delete_entity(
                partition_key=entity["PartitionKey"],
                row_key=entity["RowKey"],
            )
            deleted += 1
        return deleted


# =============================================================================
# Helpers
# =============================================================================


def _entity_to_entry(entity: dict[str, Any]) -> AuditEntry:
    return AuditEntry(
        pipeline=entity["PartitionKey"],
        natural_key=entity["RowKey"],
        status=AuditStatus(entity["status"]),
        response_code=entity.get("response_code"),
        response_body=entity.get("response_body", ""),
        sent_at_utc=_to_utc_optional(entity.get("sent_at_utc")),
        failed_at_utc=_to_utc_optional(entity.get("failed_at_utc")),
    )


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _to_utc_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _to_utc(value)


def _truncate(s: str, max_len: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= max_len else s[:max_len] + "...[truncated]"
