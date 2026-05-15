"""Tests for the puller resilience pattern: watermark + audit dedup + retry."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from ryder_carrier_api.clients.ryder_client import (
    RyderEndpoint,
    RyderResult,
    RyderResultStatus,
)
from ryder_carrier_api.config import AppSettings
from ryder_carrier_api.services.base import PullerService, RunStatus
from ryder_carrier_api.storage.in_memory import (
    InMemoryAuditStore,
    InMemoryWatermarkStore,
)
from ryder_carrier_api.transformers.base import PayloadTransformer, TransformedPayload
from ryder_carrier_api.transformers.trace_payload import SkipRow

# --- Fakes ---


class _FakeSnowflake:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetch_rows(self, sql: str, params: dict | None = None) -> Iterator[dict]:
        yield from self._rows


class _FakeRyder:
    def __init__(self, results: list[RyderResult]) -> None:
        self._results = list(results)
        self.posted: list[dict] = []

    def post(self, endpoint: RyderEndpoint, payload: dict) -> RyderResult:
        self.posted.append(payload)
        return self._results.pop(0)


class _IdentityTransformer(PayloadTransformer):
    """Transforms a row {"key": "...", "skip": bool} into a payload and natural key."""

    def transform(self, row: dict) -> TransformedPayload:
        if row.get("skip"):
            raise SkipRow("test skip")
        return TransformedPayload(
            natural_key=row["key"],
            payload={"loadNumber": row["key"]},
        )


# --- Concrete test puller ---


class _TestPuller(PullerService):
    pipeline_name = "trace"
    endpoint = RyderEndpoint.TRACE

    def _build_query_params(self, cursor_start: datetime, run_started: datetime) -> dict:
        return {}


def _settings() -> AppSettings:
    return AppSettings(
        snowflake_account="x",
        snowflake_database="x",
        watermark_max_lookback_hours=72,
        watermark_overlap_minutes=5,
    )  # type: ignore[call-arg]


def _sent() -> RyderResult:
    return RyderResult(
        status=RyderResultStatus.SENT,
        response_code=200,
        response_body="ok",
        attempts=1,
    )


def _permanent() -> RyderResult:
    return RyderResult(
        status=RyderResultStatus.FAILED_PERMANENTLY,
        response_code=400,
        response_body="bad",
        attempts=1,
    )


def _transient() -> RyderResult:
    return RyderResult(
        status=RyderResultStatus.FAILED_TRANSIENT,
        response_code=None,
        response_body="boom",
        attempts=5,
    )


# --- Tests ---


def test_zero_rows_advances_watermark_with_no_data_status() -> None:
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([]),
        ryder=_FakeRyder([]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.status == RunStatus.NO_DATA
    assert result.rows_seen == 0
    assert watermarks.get("trace") is not None  # advanced


def test_all_sent_advances_watermark_and_writes_audit() -> None:
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()
    rows = [{"key": "k1"}, {"key": "k2"}]
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake(rows),
        ryder=_FakeRyder([_sent(), _sent()]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.status == RunStatus.SUCCESS
    assert result.rows_seen == 2
    assert result.rows_sent == 2
    assert result.rows_transient_failed == 0
    assert watermarks.get("trace") is not None
    assert audit.get("trace", "k1") is not None
    assert audit.get("trace", "k2") is not None


def test_permanent_failures_advance_watermark_and_go_to_dlq() -> None:
    """4xx rejections shouldn't block the pipeline — they're terminal."""
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=_FakeRyder([_permanent(), _sent()]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.status == RunStatus.SUCCESS
    assert result.rows_sent == 1
    assert result.rows_dlq == 1
    assert watermarks.get("trace") is not None


def test_transient_failure_does_not_advance_watermark() -> None:
    """The core resilience invariant: transient failures stall the watermark
    so the next tick replays the same window."""
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=_FakeRyder([_sent(), _transient()]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.status == RunStatus.FAILED_TRANSIENT
    assert result.rows_sent == 1
    assert result.rows_transient_failed == 1
    assert watermarks.get("trace") is None  # NOT advanced


def test_transient_failure_does_not_write_audit_row() -> None:
    """Critical: transient must NOT write an audit row, or replay would dedup-skip
    the row and lose data permanently."""
    audit = InMemoryAuditStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "kT"}]),
        ryder=_FakeRyder([_transient()]),
        watermarks=InMemoryWatermarkStore(),
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    puller.run()
    assert audit.get("trace", "kT") is None


def test_dedup_skips_rows_already_sent() -> None:
    """A row whose natural_key is already audit=sent should be skipped."""
    audit = InMemoryAuditStore()
    # Pre-seed audit with k1 already sent.
    from ryder_carrier_api.storage.base import AuditEntry, AuditStatus

    audit.upsert(
        AuditEntry(
            pipeline="trace",
            natural_key="k1",
            status=AuditStatus.SENT,
            response_code=200,
            response_body="ok",
            sent_at_utc=datetime.now(tz=UTC),
            failed_at_utc=None,
        )
    )
    fake_ryder = _FakeRyder([_sent()])  # only one POST expected
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=fake_ryder,
        watermarks=InMemoryWatermarkStore(),
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.rows_skipped_dedup == 1
    assert result.rows_sent == 1
    assert len(fake_ryder.posted) == 1  # k1 was skipped, only k2 posted


def test_replay_after_transient_does_not_double_send() -> None:
    """The full safety dance:
    1) Tick A: rows k1,k2 — k1 sent, k2 transient. Watermark stalls.
    2) Tick B: same rows replay — k1 caught by dedup, k2 retried.
    """
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()

    # Tick A
    ryder_a = _FakeRyder([_sent(), _transient()])
    puller_a = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=ryder_a,
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result_a = puller_a.run()
    assert result_a.status == RunStatus.FAILED_TRANSIENT
    assert watermarks.get("trace") is None  # stalled

    # Tick B — replay
    ryder_b = _FakeRyder([_sent()])  # k1 is dedup-skipped; only k2 posts
    puller_b = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=ryder_b,
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    )
    result_b = puller_b.run()
    assert result_b.status == RunStatus.SUCCESS
    assert result_b.rows_skipped_dedup == 1  # k1
    assert result_b.rows_sent == 1  # k2
    assert len(ryder_b.posted) == 1
    assert ryder_b.posted[0]["loadNumber"] == "k2"
    assert watermarks.get("trace") is not None  # now advanced


def test_skiprow_counted_as_invalid_does_not_block_watermark() -> None:
    watermarks = InMemoryWatermarkStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1", "skip": True}, {"key": "k2"}]),
        ryder=_FakeRyder([_sent()]),
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.rows_skipped_invalid == 1
    assert result.rows_sent == 1
    assert watermarks.get("trace") is not None
