from datetime import datetime

import pytest

from ryder_carrier_api.transformers.trace_payload import (
    SkipRow,
    TracePayloadTransformer,
)


def _base_row(**overrides):
    row = {
        "CUSTOMER_ORDER_NUMBER": "0034759307",
        "CURRENT_LOCATION_CITY": "CHICAGO",
        "CURRENT_LOCATION_STATE": "IL",
        "SOURCE_CREATED_AT_UTC": datetime(2026, 4, 2, 12, 0, 0),
        "SOURCE_CREATED_AT_TIMEZONE": "America/Chicago",
        "CURRENT_LOCATION_LATITUDE": 41.844043,
        "CURRENT_LOCATION_LONGITUDE": -87.736063,
        "SEQUENCE": 1,
        "TRACKING_UPDATES_TRAILER_NUMBER": "AMBIMUS53",
        "STOP_EVENTS_TRAILER_NUMBERS_FIRST": None,
        "DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER": None,
        "STOP_EVENTS_TRACTOR_NUMBER": None,
        "DRIVER_ASSIGNMENTS_DRIVER1_NAME": None,
    }
    row.update(overrides)
    return row


def test_happy_path_trailer_resolution() -> None:
    t = TracePayloadTransformer()
    out = t.transform(_base_row())
    p = out.payload
    assert p["loadNumber"] == "0034759307"
    assert p["source"] == "carrier"
    assert p["eventCity"] == "CHICAGO"
    assert p["eventState"] == "IL"
    assert len(p["traces"]) == 1
    trace = p["traces"][0]
    assert trace["resourceId"] == "AMBIMUS53"
    assert trace["resourceType"] == "Trailer"
    assert trace["stopsequenceNumber"] == 1
    assert trace["time"]["dateTime"] == "2026-04-02T07:00:00.0000000-05:00"
    assert trace["time"]["timeZoneCode"] == "CDT"
    assert trace["time"]["timeZoneOffset"] == "UTC-5"
    assert trace["coordinate"] == {"latitude": 41.844043, "longitude": -87.736063}


def test_resource_resolution_falls_back_to_stop_events_trailer() -> None:
    t = TracePayloadTransformer()
    out = t.transform(
        _base_row(
            TRACKING_UPDATES_TRAILER_NUMBER=None,
            STOP_EVENTS_TRAILER_NUMBERS_FIRST="STOPTRL99",
        )
    )
    assert out.payload["traces"][0]["resourceId"] == "STOPTRL99"
    assert out.payload["traces"][0]["resourceType"] == "Trailer"


def test_resource_resolution_falls_back_to_tractor() -> None:
    t = TracePayloadTransformer()
    out = t.transform(
        _base_row(
            TRACKING_UPDATES_TRAILER_NUMBER=None,
            STOP_EVENTS_TRAILER_NUMBERS_FIRST=None,
            DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER="TRK42",
        )
    )
    assert out.payload["traces"][0]["resourceId"] == "TRK42"
    assert out.payload["traces"][0]["resourceType"] == "Truck"


def test_resource_resolution_falls_back_to_driver_name() -> None:
    t = TracePayloadTransformer()
    out = t.transform(
        _base_row(
            TRACKING_UPDATES_TRAILER_NUMBER=None,
            STOP_EVENTS_TRAILER_NUMBERS_FIRST=None,
            DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER=None,
            STOP_EVENTS_TRACTOR_NUMBER=None,
            DRIVER_ASSIGNMENTS_DRIVER1_NAME="Jane Doe",
        )
    )
    assert out.payload["traces"][0]["resourceId"] == "Jane Doe"
    assert out.payload["traces"][0]["resourceType"] == "Driver"


def test_unknown_sentinel_treated_as_null() -> None:
    """Literal 'UNKNOWN' values are not valid resource ids."""
    t = TracePayloadTransformer()
    out = t.transform(
        _base_row(
            TRACKING_UPDATES_TRAILER_NUMBER="UNKNOWN",
            STOP_EVENTS_TRAILER_NUMBERS_FIRST="REAL123",
        )
    )
    assert out.payload["traces"][0]["resourceId"] == "REAL123"


def test_empty_string_treated_as_null() -> None:
    t = TracePayloadTransformer()
    out = t.transform(
        _base_row(
            TRACKING_UPDATES_TRAILER_NUMBER="",
            STOP_EVENTS_TRAILER_NUMBERS_FIRST="REAL456",
        )
    )
    assert out.payload["traces"][0]["resourceId"] == "REAL456"


def test_skiprow_when_no_resource_identifier() -> None:
    t = TracePayloadTransformer()
    with pytest.raises(SkipRow):
        t.transform(
            _base_row(
                TRACKING_UPDATES_TRAILER_NUMBER=None,
                STOP_EVENTS_TRAILER_NUMBERS_FIRST=None,
                DRIVER_ASSIGNMENTS_TRACTOR_IDENTIFIER=None,
                STOP_EVENTS_TRACTOR_NUMBER=None,
                DRIVER_ASSIGNMENTS_DRIVER1_NAME=None,
            )
        )


def test_null_sequence_coerced_to_one() -> None:
    t = TracePayloadTransformer()
    out = t.transform(_base_row(SEQUENCE=None))
    assert out.payload["traces"][0]["stopsequenceNumber"] == 1


def test_zero_sequence_coerced_to_one() -> None:
    """Ryder rejects 0."""
    t = TracePayloadTransformer()
    out = t.transform(_base_row(SEQUENCE=0))
    assert out.payload["traces"][0]["stopsequenceNumber"] == 1


def test_natural_key_stable_for_same_inputs() -> None:
    t = TracePayloadTransformer()
    a = t.transform(_base_row()).natural_key
    b = t.transform(_base_row()).natural_key
    assert a == b


def test_natural_key_differs_per_timestamp() -> None:
    t = TracePayloadTransformer()
    a = t.transform(_base_row()).natural_key
    b = t.transform(_base_row(SOURCE_CREATED_AT_UTC=datetime(2026, 4, 2, 13, 0, 0))).natural_key
    assert a != b
