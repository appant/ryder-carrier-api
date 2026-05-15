from datetime import datetime, timedelta, timezone

from ryder_carrier_api.storage.base import (
    AuditEntry,
    AuditStatus,
    WatermarkRecord,
)
from ryder_carrier_api.storage.in_memory import (
    InMemoryAuditStore,
    InMemoryWatermarkStore,
)


# --- Watermark ---


def test_watermark_get_returns_none_when_unset() -> None:
    store = InMemoryWatermarkStore()
    assert store.get("trace") is None


def test_watermark_set_then_get_roundtrip() -> None:
    store = InMemoryWatermarkStore()
    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    record = WatermarkRecord(
        pipeline="trace",
        last_synced_at_utc=now,
        last_run_status="success",
        last_run_at_utc=now,
    )
    store.set(record)
    assert store.get("trace") == record


def test_watermark_set_overwrites() -> None:
    store = InMemoryWatermarkStore()
    t1 = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 4, 2, 13, 0, 0, tzinfo=timezone.utc)
    store.set(WatermarkRecord(pipeline="trace", last_synced_at_utc=t1,
                              last_run_status="success", last_run_at_utc=t1))
    store.set(WatermarkRecord(pipeline="trace", last_synced_at_utc=t2,
                              last_run_status="success", last_run_at_utc=t2))
    assert store.get("trace").last_synced_at_utc == t2


def test_watermark_pipelines_are_independent() -> None:
    store = InMemoryWatermarkStore()
    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    store.set(WatermarkRecord(pipeline="trace", last_synced_at_utc=now,
                              last_run_status="success", last_run_at_utc=now))
    assert store.get("milestone") is None


# --- Audit ---


def _audit(pipeline: str, key: str, status: AuditStatus, t: datetime) -> AuditEntry:
    return AuditEntry(
        pipeline=pipeline,
        natural_key=key,
        status=status,
        response_code=200 if status == AuditStatus.SENT else 400,
        response_body="",
        sent_at_utc=t if status == AuditStatus.SENT else None,
        failed_at_utc=t if status == AuditStatus.FAILED_PERMANENTLY else None,
    )


def test_audit_get_returns_none_when_unset() -> None:
    store = InMemoryAuditStore()
    assert store.get("trace", "abc") is None


def test_audit_upsert_then_get() -> None:
    store = InMemoryAuditStore()
    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    entry = _audit("trace", "abc", AuditStatus.SENT, now)
    store.upsert(entry)
    assert store.get("trace", "abc") == entry


def test_audit_upsert_replaces_existing() -> None:
    store = InMemoryAuditStore()
    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    store.upsert(_audit("trace", "abc", AuditStatus.SENT, now))
    store.upsert(_audit("trace", "abc", AuditStatus.FAILED_PERMANENTLY, now))
    assert store.get("trace", "abc").status == AuditStatus.FAILED_PERMANENTLY


def test_audit_pipeline_and_key_both_part_of_id() -> None:
    store = InMemoryAuditStore()
    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    store.upsert(_audit("trace", "abc", AuditStatus.SENT, now))
    assert store.get("milestone", "abc") is None
    assert store.get("trace", "different") is None


def test_audit_delete_older_than_filters_by_pipeline_and_cutoff() -> None:
    store = InMemoryAuditStore()
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 4, 2, tzinfo=timezone.utc)
    cutoff = datetime(2026, 3, 1, tzinfo=timezone.utc)

    store.upsert(_audit("trace", "old1", AuditStatus.SENT, old))
    store.upsert(_audit("trace", "old2", AuditStatus.FAILED_PERMANENTLY, old))
    store.upsert(_audit("trace", "new", AuditStatus.SENT, new))
    store.upsert(_audit("milestone", "old-other-pipeline", AuditStatus.SENT, old))

    deleted = store.delete_older_than("trace", cutoff)
    assert deleted == 2
    assert store.get("trace", "old1") is None
    assert store.get("trace", "old2") is None
    assert store.get("trace", "new") is not None
    # milestone untouched
    assert store.get("milestone", "old-other-pipeline") is not None
