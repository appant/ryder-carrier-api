"""Timezone helpers for Ryder payloads.

Ryder requires:
  - `dateTime`: UTC, whole-second precision, trailing 'Z' (e.g. "2026-05-19T21:09:00Z").
    The API validates against ^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$
    so no fractional seconds and no numeric offset are allowed.
  - `timeZoneCode`: short code for the local zone (e.g. "EST", "CST", "HST")
  - `timeZoneOffset`: local zone offset (e.g. "UTC-5" or "UTC-10")

MasterMind stores `*_AT_UTC` as naive UTC timestamps and `*_TIMEZONE` as
IANA names like `America/Chicago`. We convert here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def to_utc_aware(value: datetime) -> datetime:
    """Make a datetime timezone-aware in UTC. Treats naive values as already UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def format_ryder_datetime(value: datetime) -> str:
    """Format a datetime for the Ryder API `dateTime` field.

    Ryder validates this field against
    ``^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$`` — UTC,
    whole-second precision, trailing 'Z'. No fractional seconds, no offset.
    The local zone is reported separately via `timeZoneCode` / `timeZoneOffset`.

    Output example: '2026-05-19T21:09:00Z'
    """
    return to_utc_aware(value).strftime("%Y-%m-%dT%H:%M:%SZ")


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
