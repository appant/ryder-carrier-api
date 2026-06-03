"""Unit tests for TableStorageAuditStore cleanup batching (no Azure needed —
the table client is mocked)."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from ryder_carrier_api.storage import table_storage as ts


def _store_with_entities(
    entities: list[dict[str, str]],
) -> tuple[ts.TableStorageAuditStore, MagicMock]:
    client = MagicMock()
    client.query_entities.return_value = iter(entities)
    # Patch only during construction; afterwards store._client is the mock.
    with patch.object(ts, "_build_table_client", return_value=client):
        store = ts.TableStorageAuditStore(
            storage_account_url="https://x.table.core.windows.net",
            table_name="sentaudit",
        )
    return store, client


def test_delete_older_than_batches_deletes_in_100s() -> None:
    entities = [{"PartitionKey": "trace", "RowKey": f"k{i}"} for i in range(250)]
    store, client = _store_with_entities(entities)

    deleted = store.delete_older_than("trace", datetime(2026, 1, 1, tzinfo=UTC))

    assert deleted == 250
    # 250 rows -> transactional batches of 100, 100, 50.
    assert client.submit_transaction.call_count == 3
    sizes = [len(call.args[0]) for call in client.submit_transaction.call_args_list]
    assert sizes == [100, 100, 50]
    # Batches succeeding means we never fall back to per-row deletes.
    client.delete_entity.assert_not_called()
    # The query projects only the keys — not the (up to 32 KB) response body.
    assert client.query_entities.call_args.kwargs["select"] == ["PartitionKey", "RowKey"]


def test_delete_older_than_empty_is_noop() -> None:
    store, client = _store_with_entities([])
    assert store.delete_older_than("trace", datetime(2026, 1, 1, tzinfo=UTC)) == 0
    client.submit_transaction.assert_not_called()


def test_delete_batch_falls_back_to_per_row_on_transaction_error() -> None:
    entities = [{"PartitionKey": "trace", "RowKey": f"k{i}"} for i in range(3)]
    store, client = _store_with_entities(entities)
    client.submit_transaction.side_effect = ts.TableTransactionError(message="boom")

    deleted = store.delete_older_than("trace", datetime(2026, 1, 1, tzinfo=UTC))

    assert deleted == 3  # all three deleted via the per-row fallback
    assert client.delete_entity.call_count == 3
