from ryder_carrier_api.utils.natural_key import natural_key_hash


def test_deterministic() -> None:
    a = natural_key_hash("trace", "LOAD1", "TRL", "2026-04-02T12:00:00")
    b = natural_key_hash("trace", "LOAD1", "TRL", "2026-04-02T12:00:00")
    assert a == b


def test_different_inputs_produce_different_hashes() -> None:
    a = natural_key_hash("trace", "LOAD1", "TRL", "2026-04-02T12:00:00")
    b = natural_key_hash("trace", "LOAD2", "TRL", "2026-04-02T12:00:00")
    assert a != b


def test_order_matters() -> None:
    a = natural_key_hash("trace", "LOAD1", "TRL")
    b = natural_key_hash("trace", "TRL", "LOAD1")
    assert a != b


def test_none_treated_as_empty_string() -> None:
    a = natural_key_hash("trace", None, "TRL")  # type: ignore[arg-type]
    b = natural_key_hash("trace", "", "TRL")
    assert a == b


def test_returns_hex_sha256() -> None:
    h = natural_key_hash("anything")
    assert len(h) == 64
    int(h, 16)  # would raise if not hex
