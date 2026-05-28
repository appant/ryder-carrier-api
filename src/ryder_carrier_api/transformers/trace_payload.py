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
        # Bare-minimum guard — Ryder accepts the payload only if these are present:
        #   loadNumber, traces[].time.dateTime, traces[].coordinate.{lat,lon}.
        # Everything else (resourceId, resourceType, stopsequenceNumber, city, state)
        # is sent when available, omitted otherwise — no fake fallbacks.
        ship_id = row.get("SHIP_ID")
        source_time = row.get("SOURCE_CREATED_AT_UTC")
        lat = row.get("CURRENT_LOCATION_LATITUDE")
        lon = row.get("CURRENT_LOCATION_LONGITUDE")
        if not ship_id or source_time is None or lat is None or lon is None:
            raise SkipRow(
                f"Missing required field (loadNumber/dateTime/coordinate) "
                f"for load {ship_id}"
            )

        load_number = str(ship_id)
        iana_tz = row.get("SOURCE_CREATED_AT_TIMEZONE")
        resource_id, resource_type = _resolve_resource(row)

        trace: dict[str, Any] = {
            "time": {
                "dateTime": format_ryder_datetime(source_time),
                "timeZoneCode": short_timezone_code(source_time, iana_tz),
                "timeZoneOffset": utc_offset_string(source_time, iana_tz),
            },
            "coordinate": {
                "latitude": float(lat),
                "longitude": float(lon),
            },
        }
        if resource_id:
            trace["resourceId"] = resource_id
        if resource_type:
            trace["resourceType"] = resource_type
        stop_sequence_raw = row.get("SEQUENCE")
        if stop_sequence_raw is not None and int(stop_sequence_raw) >= 1:
            trace["stopsequenceNumber"] = int(stop_sequence_raw)

        payload: dict[str, Any] = {
            "loadNumber": load_number,
            "source": "carrier",
            "traces": [trace],
        }

        city = row.get("CURRENT_LOCATION_CITY")
        if city:
            payload["eventCity"] = city
        state = row.get("CURRENT_LOCATION_STATE")
        if state:
            payload["eventState"] = state

        # Natural key still uses resource_id when present, falls back to coords + time
        # so unresolved-resource rows still get a stable, unique key for dedup.
        key = natural_key_hash(
            "trace",
            load_number,
            resource_id or f"{lat},{lon}",
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


class SkipRow(Exception):  # noqa: N818 — existing name; renaming ripples across imports
    """Raised when a row cannot produce a valid payload (e.g. no resource id).

    The service catches this and increments a metric instead of failing the tick.
    """
