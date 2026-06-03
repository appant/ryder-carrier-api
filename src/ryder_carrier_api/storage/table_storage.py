"""Azure Table Storage implementations of WatermarkStore and AuditStore."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from azure.core.exceptions import (
    HttpResponseError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.data.tables import (
    TableClient,
    TableServiceClient,
    TableTransactionError,
    UpdateMode,
)
from azure.identity import DefaultAzureCredential

from .base import (
    AuditEntry,
    AuditStatus,
    AuditStore,
    TransientStorageError,
    WatermarkRecord,
    WatermarkStore,
)

# HTTP statuses worth treating as transient (retry / replay) rather than a
# permanent storage failure.
_TRANSIENT_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _is_transient_azure_error(exc: Exception) -> bool:
    """True for Azure errors worth retrying / treating as transient (network,
    timeouts, throttling, 5xx); False for permanent ones (4xx like a bad or
    oversized entity)."""
    if isinstance(exc, ServiceRequestError | ServiceResponseError):
        return True
    if isinstance(exc, HttpResponseError):
        return exc.status_code in _TRANSIENT_STATUS
    return False


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
        except (ServiceRequestError, ServiceResponseError, HttpResponseError) as exc:
            if _is_transient_azure_error(exc):
                raise TransientStorageError(f"audit get failed: {exc}") from exc
            raise
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
            "load_number": entry.load_number or "",
            "event_type": entry.event_type or "",
            "event_code": entry.event_code or "",
            "transient_attempts": entry.transient_attempts,
        }
        self._upsert_with_retry(entity)

    def _upsert_with_retry(self, entity: dict[str, Any], attempts: int = 3) -> None:
        """Upsert with a short in-run retry on transient errors.

        Matters most for the write that records a *successful* send: a brief
        Table Storage blip there must not force a replay (which could double-send
        to Ryder). If it still fails after the retries, raise
        ``TransientStorageError`` so the caller decides what to do.
        """
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                self._client.upsert_entity(entity=entity, mode=UpdateMode.REPLACE)
                return
            except (ServiceRequestError, ServiceResponseError, HttpResponseError) as exc:
                if not _is_transient_azure_error(exc):
                    raise
                last_exc = exc
                if i < attempts - 1:
                    time.sleep(0.2 * (i + 1))
        raise TransientStorageError(
            f"audit upsert failed after {attempts} attempts: {last_exc}"
        ) from last_exc

    def delete_older_than(self, pipeline: str, cutoff_utc: datetime) -> int:
        """Delete audit rows whose terminal timestamp is older than the cutoff.

        Two things keep the monthly purge cheap as the table grows:
          - the query projects only the keys (``select``) so we don't drag back
            up-to-32 KB response bodies just to delete by key, and
          - deletes go out in transactional batches of up to 100 (all share the
            pipeline PartitionKey) — ~100x fewer round-trips than per-row deletes,
            which is what kept the purge under its timeout at scale.
        """
        cutoff_iso = cutoff_utc.isoformat()
        query = (
            f"PartitionKey eq '{pipeline}' and "
            f"((sent_at_utc lt datetime'{cutoff_iso}') or "
            f"(failed_at_utc lt datetime'{cutoff_iso}'))"
        )
        deleted = 0
        batch: list[tuple[str, dict[str, str]]] = []
        for entity in self._client.query_entities(
            query_filter=query, select=["PartitionKey", "RowKey"]
        ):
            batch.append(
                ("delete", {"PartitionKey": entity["PartitionKey"], "RowKey": entity["RowKey"]})
            )
            if len(batch) >= 100:
                deleted += self._submit_delete_batch(batch)
                batch = []
        if batch:
            deleted += self._submit_delete_batch(batch)
        return deleted

    def _submit_delete_batch(self, batch: list[tuple[str, dict[str, str]]]) -> int:
        """Submit a transactional delete batch (<=100 same-partition ops). If the
        atomic batch is rejected, fall back to best-effort per-row deletes so one
        problematic row can't abort the whole monthly purge."""
        try:
            self._client.submit_transaction(batch)
            return len(batch)
        except TableTransactionError:
            deleted = 0
            for _action, key in batch:
                try:
                    self._client.delete_entity(
                        partition_key=key["PartitionKey"], row_key=key["RowKey"]
                    )
                    deleted += 1
                except ResourceNotFoundError:
                    continue
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
        load_number=entity.get("load_number") or None,
        event_type=entity.get("event_type") or None,
        event_code=entity.get("event_code") or None,
        transient_attempts=int(entity.get("transient_attempts") or 0),
    )


def _to_utc(value: datetime) -> datetime:
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return datetime(
        aware.year,
        aware.month,
        aware.day,
        aware.hour,
        aware.minute,
        aware.second,
        aware.microsecond,
        tzinfo=UTC,
    )


def _to_utc_optional(value: datetime | None) -> datetime | None:
    return None if value is None else _to_utc(value)


def _truncate(s: str, max_len: int) -> str:
    if s is None:
        return ""
    return s if len(s) <= max_len else s[:max_len] + "...[truncated]"
