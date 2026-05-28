from datetime import UTC, datetime

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
    assert result.tzinfo is UTC


def test_aware_converted_to_utc() -> None:
    from zoneinfo import ZoneInfo

    aware = datetime(2026, 4, 2, 7, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
    result = to_utc_aware(aware)
    assert result.tzinfo is UTC
    assert result.hour == 12  # 07:00 CDT = 12:00 UTC


# --- format_ryder_datetime ---
# Ryder validates dateTime against ^...T..:..:..Z$ — UTC, whole seconds, trailing 'Z'.
# The local zone is carried by timeZoneCode / timeZoneOffset, not by dateTime.


def test_format_emits_utc_zulu() -> None:
    """Naive values are treated as UTC and emitted as 'YYYY-MM-DDTHH:MM:SSZ'."""
    utc = datetime(2026, 4, 2, 12, 0, 0)
    assert format_ryder_datetime(utc) == "2026-04-02T12:00:00Z"


def test_format_drops_fractional_seconds() -> None:
    utc = datetime(2026, 4, 13, 11, 30, 0, 123456)
    assert format_ryder_datetime(utc) == "2026-04-13T11:30:00Z"


def test_format_converts_aware_input_to_utc() -> None:
    """A non-UTC aware datetime is converted to UTC before formatting."""
    from zoneinfo import ZoneInfo

    aware = datetime(2026, 4, 2, 7, 0, 0, tzinfo=ZoneInfo("America/Chicago"))
    assert format_ryder_datetime(aware) == "2026-04-02T12:00:00Z"  # 07:00 CDT = 12:00 UTC


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
