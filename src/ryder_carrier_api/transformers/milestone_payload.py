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

# Mapping from MasterMind LATE_ARRIVAL_REASON_CODE → Ryder EDI214 reasonCode.
# Source: api_mappings/milestone_api_mapping.md
REASON_CODE_MAP: dict[str, str] = {
    "Accident": "AF",
    "Alternate Carrier Delivered - Carrier recovered load": "DP11",
    "Alternate Carrier Delivery": "DP11",
    "Auto Update - Reason Unknown": "BG",
    "Boarder Clearance": "CA",
    "Border Clearance": "CA",
    "Carrier - Hours of Service": "AH",
    "Carrier Dispatch Error": "D1",
    "Carrier Dispatch Error - Poor planning, incorrect dispatch": "D1",
    "Carrier Keying Error": "D1",
    "Carrier non compliance with shipper/consignee": "BG",
    "Consignee Related": "AG",
    "Customer Requested Future Delivery": "AD",
    "Customer Strike": "BG",
    "Customer Vacation": "B1",
    "Damaged": "A9",
    "Driver Not Available": "D2",
    "Driver Related": "AH",
    "Driver related - Driver overslept, misread information, arrived to incorrect location": "AH",
    "Drop Trailer": "BG",
    "Exceeds Service Limitations": "BG",
    "Flatcar Shortage": "BO",
    "Held Pending Appointment": "HB",
    "Held for Full Carrier Load": "HB",
    "Held per Shipper": "AM",
    "Hold due to Customs Documentation Problems": "AS",
    "Holiday - Closed": "AN",
    "Incorrect Address": "CR1",
    "Insufficient Delivery Time": "BH",
    "Insufficient Pickup Time": "AX",
    "Insufficient Pickup/Lead Time": "AX",
    "Insufficient Time to Complete Delivery": "BH",
    "International Non-carrier Delay": "CA",
    "Load Shifted": "BP",
    "Mechanical Breakdown": "AI",
    "Mechanical Breakdown - Tractor / Trailer / mechanical issue": "AI",
    "Mis-sort": "BG",
    "Missed Delivery": "BH",
    "Missed Pick Up - Carrier missed original scheduled pickup appointment": "D1",
    "Missed Pickup": "D1",
    "Missing Documents": "PW",
    "No Requested Arrival Date Provided by Shipper": "AM",
    "No Requested Arrival Time Provided by Shipper": "AM",
    "Non-Express Clearance Delay": "CA",
    "Normal Shipment": "NS",
    "Normal Status": "NS",
    "Normal Status (NS)": "NS",
    "Other": "BG",
    "Other - Carrier Related": "BG",
    "PAST CUT OFF TIME / SHORT LEAD TIME": "AW",
    "Past Cut-off Time": "AW",
    "Previous Stop - Driver held up on previous load": "AL",
    "Previous Stop Caused Delay": "AL",
    "Railroad Failed to Meet Schedule": "BO",
    "Refused by Customer": "BS",
    "Road Conditions": "BE",
    "Road Conditions - Traffic or construction": "BE",
    "Shipment Overweight": "BQ",
    "Shipper Related": "AM",
    "Trailer Class Not Available": "NE",
    "Trailer Not Usable Due to Prior Product": "NE",
    "Trailer not Available": "NE",
    "Unable to Locate": "BG",
    "Waiting Shipping Instructions": "AM",
    "Waiting for Customer Pick-up": "C1",
    "Weather or Natural Disaster Related": "AO",
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
        raw_reason = row.get("LATE_ARRIVAL_REASON_CODE")
        reason_code = (
            REASON_CODE_MAP.get(raw_reason, DEFAULT_REASON_CODE)
            if raw_reason
            else DEFAULT_REASON_CODE
        )
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
