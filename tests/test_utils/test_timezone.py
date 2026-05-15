from datetime import datetime, timezone

from ryder_carrier_api.utils.timezone import (
    format_ryder_datetime,
    short_timezone_code,
    to_utc_aware,
    utc_offset_string,
)


# --- to_utc_aware ---


def test_naive_treated_as_utc() -> None:
    naive = datetime(2026, 4, 2, 12, 0, 0)
    result = to_utc_aware(naive)
    assert result.tzinfo is timezone.utc


def test_aware_converted_to_utc() -> None:
    from zoneinfo import ZoneInfo

    aware = datetime(2026, 4, 2, 7, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
    result = to_utc_aware(aware)
    assert result.tzinfo is timezone.utc
    assert result.hour == 12  # 07:00 CDT = 12:00 UTC


# --- format_ryder_datetime ---


def test_format_chicago_in_dst() -> None:
    """April is CDT (UTC-5), not CST (UTC-6)."""
    utc = datetime(2026, 4, 2, 12, 0, 0)
    out = format_ryder_datetime(utc, "America/Chicago")
    assert out == "2026-04-02T07:00:00.0000000-05:00"


def test_format_no_tz_returns_utc() -> None:
    utc = datetime(2026, 4, 2, 12, 0, 0)
    out = format_ryder_datetime(utc, None)
    assert out == "2026-04-02T12:00:00.0000000+00:00"


def test_format_new_york_in_dst() -> None:
    """April is EDT (UTC-4)."""
    utc = datetime(2026, 4, 13, 11, 30, 0)
    out = format_ryder_datetime(utc, "America/New_York")
    assert out == "2026-04-13T07:30:00.0000000-04:00"


# --- short_timezone_code ---


def test_short_code_chicago_dst() -> None:
    utc = datetime(2026, 4, 2, 12, 0, 0)
    assert short_timezone_code(utc, "America/Chicago") == "CDT"


def test_short_code_chicago_std() -> None:
    """January is CST."""
    utc = datetime(2026, 1, 15, 12, 0, 0)
    assert short_timezone_code(utc, "America/Chicago") == "CST"


def test_short_code_none_returns_utc() -> None:
    assert short_timezone_code(datetime(2026, 1, 1), None) == "UTC"


# --- utc_offset_string ---


def test_offset_chicago_dst() -> None:
    utc = datetime(2026, 4, 2, 12, 0, 0)
    assert utc_offset_string(utc, "America/Chicago") == "UTC-5"


def test_offset_chicago_std() -> None:
    utc = datetime(2026, 1, 15, 12, 0, 0)
    assert utc_offset_string(utc, "America/Chicago") == "UTC-6"


def test_offset_none_returns_utc_plus_zero() -> None:
    assert utc_offset_string(datetime(2026, 1, 1), None) == "UTC+0"
