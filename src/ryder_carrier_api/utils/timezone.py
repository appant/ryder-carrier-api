"""Timezone helpers for Ryder payloads.

Ryder requires:
  - `dateTime`: ISO 8601 with offset (e.g. "2026-05-26T01:00:00.0000000+00:00")
  - `timeZoneCode`: short code (e.g. "EST", "CST", "HST")
  - `timeZoneOffset`: e.g. "UTC-5" or "UTC-10"

MasterMind stores `*_AT_UTC` as naive UTC timestamps and `*_TIMEZONE` as
IANA names like `America/Chicago`. We convert here.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


def to_utc_aware(value: datetime) -> datetime:
    """Make a datetime timezone-aware in UTC. Treats naive values as already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def format_ryder_datetime(value: datetime, iana_tz_name: str | None) -> str:
    """Format a UTC datetime for the Ryder API.

    Ryder expects the value in the local timezone (per their spec the
    'Update must be provided in Local Date-Time').
    Output example: '2026-05-26T01:00:00.0000000+00:00'
    """
    utc_value = to_utc_aware(value)
    if iana_tz_name:
        local = utc_value.astimezone(ZoneInfo(iana_tz_name))
    else:
        local = utc_value
    # Pad microseconds to 7 digits to match Ryder's example format.
    return local.strftime("%Y-%m-%dT%H:%M:%S.%f0") + _offset_string(local)


def short_timezone_code(value: datetime, iana_tz_name: str | None) -> str:
    """Return the short timezone code (e.g. 'EST', 'CDT') for the local time."""
    if iana_tz_name is None:
        return "UTC"
    utc_value = to_utc_aware(value)
    local = utc_value.astimezone(ZoneInfo(iana_tz_name))
    return local.tzname() or "UTC"


def utc_offset_string(value: datetime, iana_tz_name: str | None) -> str:
    """Return the offset relative to UTC in 'UTC±N' form (e.g. 'UTC-5')."""
    if iana_tz_name is None:
        return "UTC+0"
    utc_value = to_utc_aware(value)
    local = utc_value.astimezone(ZoneInfo(iana_tz_name))
    offset = local.utcoffset()
    if offset is None:
        return "UTC+0"
    total_minutes = int(offset.total_seconds() // 60)
    hours = total_minutes // 60
    minutes = abs(total_minutes % 60)
    sign = "+" if hours >= 0 else "-"
    if minutes == 0:
        return f"UTC{sign}{abs(hours)}"
    return f"UTC{sign}{abs(hours)}:{minutes:02d}"


def _offset_string(local: datetime) -> str:
    offset = local.utcoffset()
    if offset is None:
        return "+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    hours = abs(total_minutes) // 60
    minutes = abs(total_minutes) % 60
    return f"{sign}{hours:02d}:{minutes:02d}"
