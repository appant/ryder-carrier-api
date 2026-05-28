"""Snowflake trace row → Ryder GPS Trace request body.

Ryder schema (Resource Location):
    {
      "loadNumber":   str,                      // required
      "source":       "carrier",                // required, hardcoded
      "eventCity":    str,                      // required
      "eventState":   str,                      // required, 2-letter
      "traces": [
        {
          "time": {
            "dateTime":       str (ISO 8601 + offset),
            "timeZoneCode":   str (e.g. "CDT"),
            "timeZoneOffset": str (e.g. "UTC-5")
          },
          "stopsequenceNumber": int,            // required, never 0
          "resourceId":         str,            // required (Trailer/Truck/Driver id)
          "resourceType":       "Truck" | "Trailer" | "Driver",
          "coordinate": {
            "latitude":  float,
            "longitude": float
          }
        }
      ]
    }
"""

from __future__ import annotations

from typing import Any

from ..utils.natural_key import natural_key_hash
from ..utils.timezone import (
    format_ryder_datetime,
    short_timezone_code,
    utc_offset_string,
)
from .base import PayloadTransformer, TransformedPayload

_UNKNOWN_SENTINEL = "UNKNOWN"


class TracePayloadTransformer(PayloadTransformer):
    def transform(self, row: dict[str, Any]) -> TransformedPayload:
        resource_id, resource_type = _resolve_resource(row)
        if resource_id is None:
            raise SkipRow(f"No resource identifier for load {row.get('CUSTOMER_ORDER_NUMBER')}")

        load_number = str(row["CUSTOMER_ORDER_NUMBER"])
        source_time = row["SOURCE_CREATED_AT_UTC"]
        iana_tz = row.get("SOURCE_CREATED_AT_TIMEZONE")
        stop_sequence = _coerce_stop_sequence(row.get("SEQUENCE"))

        payload = {
            "loadNumber": load_number,
            "source": "carrier",
            "eventCity": row["CURRENT_LOCATION_CITY"],
            "eventState": row["CURRENT_LOCATION_STATE"],
            "traces": [
                {
                    "time": {
                        "dateTime": format_ryder_datetime(source_time),
                        "timeZoneCode": short_timezone_code(source_time, iana_tz),
                        "timeZoneOffset": utc_offset_string(source_time, iana_tz),
                    },
                    "stopsequenceNumber": stop_sequence,
                    "resourceId": resource_id,
                    "resourceType": resource_type,
                    "coordinate": {
                        "latitude": float(row["CURRENT_LOCATION_LATITUDE"]),
                        "longitude": float(row["CURRENT_LOCATION_LONGITUDE"]),
                    },
                }
            ],
        }

        key = natural_key_hash(
            "trace",
            load_number,
            resource_id,
            source_time.isoformat(),
        )
        return TransformedPayload(natural_key=key, payload=payload)


def _resolve_resource(row: dict[str, Any]) -> tuple[str | None, str | None]:
    """Walk the COALESCE chain (see api_mappings/trace_api_mapping.md).

    Order: Trailer first, then Truck, then Driver.
    """
    # Step 1 + 2: Trailer
    trailer = _first_nonempty(
        row.get("TRACKING_UPDATES_TRAILER_NUMBER"),
        row.get("STOP_EVENTS_TRAILER_NUMBERS_FIRST"),
    )
    if trailer:
        return trailer, "Trailer"

    # Step 3 + 4: Truck
    tractor = _first_nonempty(
        row.get("DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER"),
        row.get("STOP_EVENTS_TRACTOR_NUMBER"),
    )
    if tractor:
        return tractor, "Truck"

    # Step 5: Driver
    driver_name = row.get("DRIVER_ASSIGNMENTS_DRIVER1_NAME")
    if driver_name and driver_name.strip():
        return driver_name.strip(), "Driver"

    return None, None


def _first_nonempty(*values: str | None) -> str | None:
    """Return the first non-null, non-'UNKNOWN', non-empty value."""
    for v in values:
        if v is None:
            continue
        stripped = str(v).strip()
        if stripped and stripped.upper() != _UNKNOWN_SENTINEL:
            return stripped
    return None


def _coerce_stop_sequence(value: Any) -> int:
    """Ryder rejects 0 or null. Return at least 1."""
    if value is None:
        return 1
    n = int(value)
    return n if n >= 1 else 1


class SkipRow(Exception):  # noqa: N818 — existing name; renaming ripples across imports
    """Raised when a row cannot produce a valid payload (e.g. no resource id).

    The service catches this and increments a metric instead of failing the tick.
    """
