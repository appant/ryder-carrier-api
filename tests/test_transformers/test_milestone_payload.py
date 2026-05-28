from datetime import datetime

import pytest

from ryder_carrier_api.transformers.milestone_payload import (
    MilestonePayloadTransformer,
)
from ryder_carrier_api.transformers.trace_payload import SkipRow


def _base_row(**overrides):
    row = {
        "SHIP_ID": "2620444042",
        "EVENT_TYPE": "Driver Arrival",
        "LATE_ARRIVAL_REASON_CODE": None,
        "ACTUAL_EVENT_AT_UTC": datetime(2026, 4, 2, 12, 0, 0),
        "ACTUAL_TIMEZONE": "America/Chicago",
        "LOCALITY": "CHICAGO",
        "ADMINISTRATIVE_AREA1_CODE": "IL",
        "SEQUENCE": 1,
    }
    row.update(overrides)
    return row


def test_driver_arrival_maps_to_x3() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row())
    assert out.payload["eventCode"] == "X3"


def test_driver_departure_maps_to_x1() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(EVENT_TYPE="Driver Departure"))
    assert out.payload["eventCode"] == "X1"


def test_notification_maps_to_a9() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(EVENT_TYPE="Notification"))
    assert out.payload["eventCode"] == "A9"


def test_unmapped_event_type_defaults_to_a9() -> None:
    """A9 = General Status Update — safe fallback."""
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(EVENT_TYPE="Some New Event Type"))
    assert out.payload["eventCode"] == "A9"


def test_null_event_type_defaults_to_a9() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(EVENT_TYPE=None))
    assert out.payload["eventCode"] == "A9"


def test_default_reason_code_when_null() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(LATE_ARRIVAL_REASON_CODE=None))
    assert out.payload["reasonCode"] == "NS"


def test_reason_code_passed_through_when_set() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(LATE_ARRIVAL_REASON_CODE="CHS"))
    assert out.payload["reasonCode"] == "CHS"


def test_skiprow_when_city_missing() -> None:
    t = MilestonePayloadTransformer()
    with pytest.raises(SkipRow):
        t.transform(_base_row(LOCALITY=None))


def test_skiprow_when_state_missing() -> None:
    t = MilestonePayloadTransformer()
    with pytest.raises(SkipRow):
        t.transform(_base_row(ADMINISTRATIVE_AREA1_CODE=None))


def test_full_payload_shape() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row())
    p = out.payload
    assert set(p.keys()) == {
        "eventCode",
        "reasonCode",
        "loadNumber",
        "source",
        "eventCity",
        "eventState",
        "time",
        "stopsequenceNumber",
    }
    assert p["loadNumber"] == "2620444042"
    assert p["source"] == "carrier"
    assert p["eventCity"] == "CHICAGO"
    assert p["eventState"] == "IL"
    assert p["stopsequenceNumber"] == 1
    assert p["time"] == {
        "dateTime": "2026-04-02T12:00:00Z",
        "timeZoneCode": "CDT",
        "timeZoneOffset": "UTC-5",
    }


def test_natural_key_distinguishes_event_types_on_same_load() -> None:
    t = MilestonePayloadTransformer()
    arrival = t.transform(_base_row(EVENT_TYPE="Driver Arrival")).natural_key
    departure = t.transform(_base_row(EVENT_TYPE="Driver Departure")).natural_key
    assert arrival != departure


def test_null_sequence_coerced_to_one() -> None:
    t = MilestonePayloadTransformer()
    out = t.transform(_base_row(SEQUENCE=None))
    assert out.payload["stopsequenceNumber"] == 1
