"""Snowflake stop-event row → Ryder Milestone request body.

Ryder schema (Milestone):
    {
      "eventCode":   str,    // required, EDI214 code (e.g. "X1", "X3")
      "reasonCode":  str,    // required, EDI214 reason code
      "loadNumber":  str,    // required
      "source":      "carrier",
      "eventCity":   str,
      "eventState":  str,
      "time": {
        "dateTime":       str,
        "timeZoneCode":   str,
        "timeZoneOffset": str
      },
      "stopsequenceNumber": int  // required, never 0
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
from .trace_payload import SkipRow

# Mapping from MasterMind EVENT_TYPE → Ryder EDI214 eventCode.
# Source: api_mappings/milestone_api_mapping.md + RyderCarrierAPI-milestone-reason-codes.pdf
EVENT_TYPE_TO_CODE: dict[str, str] = {
    "Driver Arrival":            "X3",
    "Driver Departure":          "X1",
    "Hook Loaded":               "AF",
    "Hook Empty":                "AF",
    "Drop Loaded":               "CP",
    "Drop Empty":                "CP",
    "Drop Unloading Begin":      "X6",
    "In-Gate Loaded":            "X3",
    "In-Gate Empty":             "X3",
    "Out-Gate Loaded":           "X1",
    "Terminal Arrival":          "X3",
    "Terminal Departure":        "X1",
    "Bobtail In":                "X3",
    "Bobtail Out":               "X1",
    "Notification":              "A9",
    # Defaults to "A9" (General Status Update) if not mapped
}

# Default reason code when MasterMind doesn't provide one.
DEFAULT_REASON_CODE = "NS"


class MilestonePayloadTransformer(PayloadTransformer):
    def transform(self, row: dict[str, Any]) -> TransformedPayload:
        load_number = str(row["CUSTOMER_ORDER_NUMBER"])
        event_type = row.get("EVENT_TYPE") or ""
        event_code = EVENT_TYPE_TO_CODE.get(event_type, "A9")
        reason_code = row.get("LATE_ARRIVAL_REASON_CODE") or DEFAULT_REASON_CODE
        actual_time = row["ACTUAL_EVENT_AT_UTC"]
        iana_tz = row.get("ACTUAL_TIMEZONE")
        stop_sequence = _coerce_stop_sequence(row.get("SEQUENCE"))

        city = row.get("LOCALITY")
        state = row.get("ADMINISTRATIVE_AREA1_CODE")
        if not city or not state:
            raise SkipRow(f"Missing city/state for load {load_number}, event {event_type}")

        payload = {
            "eventCode": event_code,
            "reasonCode": reason_code,
            "loadNumber": load_number,
            "source": "carrier",
            "eventCity": city,
            "eventState": state,
            "time": {
                "dateTime": format_ryder_datetime(actual_time, iana_tz),
                "timeZoneCode": short_timezone_code(actual_time, iana_tz),
                "timeZoneOffset": utc_offset_string(actual_time, iana_tz),
            },
            "stopsequenceNumber": stop_sequence,
        }

        key = natural_key_hash(
            "milestone",
            load_number,
            event_type,
            actual_time.isoformat(),
        )
        return TransformedPayload(natural_key=key, payload=payload)


def _coerce_stop_sequence(value: Any) -> int:
    if value is None:
        return 1
    n = int(value)
    return n if n >= 1 else 1
