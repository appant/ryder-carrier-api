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
    "Driver Arrival": "X3",
    "Driver Departure": "X1",
    "Hook Loaded": "AF",
    "Hook Empty": "AF",
    "Drop Loaded": "CP",
    "Drop Empty": "CP",
    "Drop Unloading Begin": "X6",
    "In-Gate Loaded": "X3",
    "In-Gate Empty": "X3",
    "Out-Gate Loaded": "X1",
    "Terminal Arrival": "X3",
    "Terminal Departure": "X1",
    "Bobtail In": "X3",
    "Bobtail Out": "X1",
    "Notification": "A9",
    # Defaults to "A9" (General Status Update) if not mapped
}

# Default reason code when MasterMind doesn't provide one.
DEFAULT_REASON_CODE = "NS"


class MilestonePayloadTransformer(PayloadTransformer):
    def transform(self, row: dict[str, Any]) -> TransformedPayload:
        # Bare-minimum guard — only skip the row when one of Ryder's hard requirements
        # is missing. Other fields (city/state/reason/stopsequence) are still sent
        # as-is when absent, since Ryder accepts the payload without them.
        ship_id = row.get("SHIP_ID")
        actual_time = row.get("ACTUAL_EVENT_AT_UTC")
        event_type = row.get("EVENT_TYPE") or ""
        if not ship_id or actual_time is None:
            raise SkipRow(
                f"Missing required field (loadNumber/dateTime) "
                f"for load {ship_id}, event {event_type}"
            )

        load_number = str(ship_id)
        event_code = EVENT_TYPE_TO_CODE.get(event_type, "A9")
        reason_code = row.get("LATE_ARRIVAL_REASON_CODE") or DEFAULT_REASON_CODE
        iana_tz = row.get("ACTUAL_TIMEZONE")

        payload: dict[str, Any] = {
            "eventCode": event_code,
            "reasonCode": reason_code,
            "loadNumber": load_number,
            "source": "carrier",
            "time": {
                "dateTime": format_ryder_datetime(actual_time),
                "timeZoneCode": short_timezone_code(actual_time, iana_tz),
                "timeZoneOffset": utc_offset_string(actual_time, iana_tz),
            },
        }

        # Optional fields — only include when we have a real value. No fake fallbacks.
        city = row.get("LOCALITY")
        if city:
            payload["eventCity"] = city

        state = row.get("ADMINISTRATIVE_AREA1_CODE")
        if state:
            payload["eventState"] = state

        stop_sequence_raw = row.get("SEQUENCE")
        if stop_sequence_raw is not None and int(stop_sequence_raw) >= 1:
            payload["stopsequenceNumber"] = int(stop_sequence_raw)

        key = natural_key_hash(
            "milestone",
            load_number,
            event_type,
            actual_time.isoformat(),
        )
        return TransformedPayload(natural_key=key, payload=payload)
