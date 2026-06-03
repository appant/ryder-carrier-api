"""Tests for the puller resilience pattern: watermark + audit dedup + retry."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

from ryder_carrier_api.clients.ryder_client import (
    RyderEndpoint,
    RyderResult,
    RyderResultStatus,
)
from ryder_carrier_api.config import AppSettings
from ryder_carrier_api.services.base import PullerService, RunStatus
from ryder_carrier_api.storage.base import AuditEntry, AuditStatus, WatermarkRecord
from ryder_carrier_api.storage.in_memory import (
    InMemoryAuditStore,
    InMemoryDeadLetterStore,
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
        if row.get("boom"):
            raise ValueError("test transform error")
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
        watermark_max_lookback_minutes=4320,
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


def test_transient_failure_writes_retrying_not_terminal() -> None:
    """A transient failure records a RETRYING row (to count attempts), but
    RETRYING is non-terminal — replay still reprocesses it, so no data is lost.
    The old 'no audit row' invariant is replaced by 'not terminal'."""
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
    entry = audit.get("trace", "kT")
    assert entry is not None
    assert entry.status == AuditStatus.RETRYING
    assert entry.transient_attempts == 1


def test_dedup_skips_rows_already_sent() -> None:
    """A row whose natural_key is already audit=sent should be skipped."""
    audit = InMemoryAuditStore()
    # Pre-seed audit with k1 already sent.
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


def test_throttled_rows_are_counted() -> None:
    """A row that was rate-limited (then sent) is flagged in rows_throttled."""
    watermarks = InMemoryWatermarkStore()
    throttled_then_sent = RyderResult(
        status=RyderResultStatus.SENT,
        response_code=200,
        response_body="ok",
        attempts=2,
        throttled=True,
    )
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "k1"}, {"key": "k2"}]),
        ryder=_FakeRyder([throttled_then_sent, _sent()]),
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
    )
    result = puller.run()
    assert result.rows_sent == 2
    assert result.rows_throttled == 1
    assert watermarks.get("trace") is not None


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


def _puller_with_watermarks(watermarks: InMemoryWatermarkStore) -> _TestPuller:
    return _TestPuller(
        settings=_settings(),  # lookback=4320 min, overlap=5 min
        snowflake=_FakeSnowflake([]),
        ryder=_FakeRyder([]),
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
    )


def test_cold_start_looks_back_exactly_the_lookback_window() -> None:
    """First run (no watermark) looks back exactly watermark_max_lookback_minutes."""
    puller = _puller_with_watermarks(InMemoryWatermarkStore())
    run_started = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    assert puller._compute_cursor_start(run_started) == run_started - timedelta(minutes=4320)


def test_steady_state_resumes_from_watermark_minus_overlap_not_lookback() -> None:
    """With a watermark, resume from (watermark - overlap) regardless of how
    small the cold-start lookback is — no per-run floor clamp, so no data is
    dropped between runs even when lookback is 1 minute."""
    watermarks = InMemoryWatermarkStore()
    watermarks.set(
        WatermarkRecord(
            pipeline="trace",
            last_synced_at_utc=datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
            last_run_status="success",
            last_run_at_utc=datetime(2026, 1, 1, 11, 30, tzinfo=UTC),
        )
    )
    puller = _puller_with_watermarks(watermarks)
    run_started = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    # 11:30 watermark - 5 min overlap = 11:25, NOT clamped to run_started - lookback.
    assert puller._compute_cursor_start(run_started) == datetime(2026, 1, 1, 11, 25, tzinfo=UTC)


def _settings_with_catchup(cap_minutes: int) -> AppSettings:
    return AppSettings(
        snowflake_account="x",
        snowflake_database="x",
        watermark_overlap_minutes=5,
        watermark_max_catchup_minutes=cap_minutes,
    )  # type: ignore[call-arg]


def _puller_with(settings: AppSettings, watermarks: InMemoryWatermarkStore) -> _TestPuller:
    return _TestPuller(
        settings=settings,
        snowflake=_FakeSnowflake([]),
        ryder=_FakeRyder([]),
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
    )


def test_catchup_capped_when_watermark_older_than_max() -> None:
    """After a long outage, the cursor is clamped to the catch-up cap rather than
    replaying an ever-growing window."""
    watermarks = InMemoryWatermarkStore()
    watermarks.set(
        WatermarkRecord(
            pipeline="trace",
            last_synced_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),  # ~36h before the run
            last_run_status="success",
            last_run_at_utc=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
        )
    )
    puller = _puller_with(_settings_with_catchup(60), watermarks)  # 1-hour cap
    run_started = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    # watermark - overlap would be 2025-12-31 23:55, but the 60-min cap clamps the
    # cursor forward to run_started - 60min = 11:00.
    assert puller._compute_cursor_start(run_started) == datetime(2026, 1, 2, 11, 0, tzinfo=UTC)


def test_catchup_not_capped_within_window() -> None:
    """A watermark within the catch-up window resumes normally (overlap only)."""
    watermarks = InMemoryWatermarkStore()
    watermarks.set(
        WatermarkRecord(
            pipeline="trace",
            last_synced_at_utc=datetime(2026, 1, 2, 11, 30, tzinfo=UTC),
            last_run_status="success",
            last_run_at_utc=datetime(2026, 1, 2, 11, 30, tzinfo=UTC),
        )
    )
    puller = _puller_with(_settings_with_catchup(60), watermarks)
    run_started = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)
    # 11:30 - 5min overlap = 11:25, within the 60-min cap (floor 11:00) → unchanged.
    assert puller._compute_cursor_start(run_started) == datetime(2026, 1, 2, 11, 25, tzinfo=UTC)


# --- Dead-letter isolation (option B) ---


def _settings_max_attempts(n: int) -> AppSettings:
    return AppSettings(
        snowflake_account="x",
        snowflake_database="x",
        watermark_max_lookback_minutes=4320,
        watermark_overlap_minutes=5,
        ryder_max_transient_attempts=n,
    )  # type: ignore[call-arg]


def test_4xx_rejection_dead_letters_payload_and_advances() -> None:
    """A single Ryder rejection is isolated: payload captured to the dead-letter
    store, watermark advances (doesn't stall the other rows)."""
    watermarks = InMemoryWatermarkStore()
    dlq = InMemoryDeadLetterStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "kR"}]),
        ryder=_FakeRyder([_permanent()]),
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
        dead_letter=dlq,
    )
    result = puller.run()
    assert result.status == RunStatus.SUCCESS
    assert result.rows_dlq == 1
    assert watermarks.get("trace") is not None  # advanced past the bad row
    record = dlq.records[("trace", "kR")]
    assert record.reason == "ryder_rejected"
    assert record.response_code == 400
    assert record.payload == {"loadNumber": "kR"}


def test_transform_error_dead_letters_raw_row_and_advances() -> None:
    """A non-SkipRow transform error captures the raw row and advances."""
    watermarks = InMemoryWatermarkStore()
    dlq = InMemoryDeadLetterStore()
    puller = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "kB", "boom": True}]),
        ryder=_FakeRyder([]),  # never reached — transform throws first
        watermarks=watermarks,
        audit=InMemoryAuditStore(),
        transformer=_IdentityTransformer(),
        sql="",
        dead_letter=dlq,
    )
    result = puller.run()
    assert result.status == RunStatus.SUCCESS
    assert result.rows_dlq == 1
    assert watermarks.get("trace") is not None
    assert len(dlq.records) == 1
    record = next(iter(dlq.records.values()))
    assert record.reason == "row_error"
    assert record.raw_row == {"key": "kB", "boom": True}


def test_transient_exhausted_dead_letters_and_advances() -> None:
    """A row that keeps failing transiently is dead-lettered after
    ryder_max_transient_attempts, so it can't stall the shared watermark forever.
    Tick A records RETRYING (frozen); tick B exhausts the budget and advances."""
    watermarks = InMemoryWatermarkStore()
    audit = InMemoryAuditStore()
    dlq = InMemoryDeadLetterStore()
    settings = _settings_max_attempts(2)

    # Tick A — attempt 1 of 2: still retrying, watermark stalls.
    result_a = _TestPuller(
        settings=settings,
        snowflake=_FakeSnowflake([{"key": "kP"}]),
        ryder=_FakeRyder([_transient()]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
        dead_letter=dlq,
    ).run()
    assert result_a.status == RunStatus.FAILED_TRANSIENT
    assert watermarks.get("trace") is None
    assert audit.get("trace", "kP").status == AuditStatus.RETRYING
    assert ("trace", "kP") not in dlq.records  # not given up on yet

    # Tick B — attempt 2 of 2: exhausted → dead-letter + advance.
    result_b = _TestPuller(
        settings=settings,
        snowflake=_FakeSnowflake([{"key": "kP"}]),
        ryder=_FakeRyder([_transient()]),
        watermarks=watermarks,
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
        dead_letter=dlq,
    ).run()
    assert result_b.status == RunStatus.SUCCESS
    assert result_b.rows_dlq == 1
    assert watermarks.get("trace") is not None  # no longer stalled
    assert audit.get("trace", "kP").status == AuditStatus.FAILED_PERMANENTLY
    record = dlq.records[("trace", "kP")]
    assert record.reason == "transient_exhausted"
    assert record.payload == {"loadNumber": "kP"}


def test_retrying_row_is_retried_not_dedup_skipped() -> None:
    """A pre-existing RETRYING row must be reprocessed (not dedup-skipped like a
    terminal row), and on success becomes SENT."""
    audit = InMemoryAuditStore()
    audit.upsert(
        AuditEntry(
            pipeline="trace",
            natural_key="kRetry",
            status=AuditStatus.RETRYING,
            response_code=503,
            response_body="boom",
            sent_at_utc=None,
            failed_at_utc=datetime.now(tz=UTC),
            transient_attempts=1,
        )
    )
    ryder = _FakeRyder([_sent()])
    result = _TestPuller(
        settings=_settings(),
        snowflake=_FakeSnowflake([{"key": "kRetry"}]),
        ryder=ryder,
        watermarks=InMemoryWatermarkStore(),
        audit=audit,
        transformer=_IdentityTransformer(),
        sql="",
    ).run()
    assert result.rows_skipped_dedup == 0  # NOT skipped
    assert result.rows_sent == 1  # retried and delivered
    assert len(ryder.posted) == 1
    assert audit.get("trace", "kRetry").status == AuditStatus.SENT
