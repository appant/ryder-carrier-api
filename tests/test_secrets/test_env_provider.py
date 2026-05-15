import pytest

from ryder_carrier_api.secrets.env_provider import EnvSecretProvider


def test_lookup_maps_hyphen_to_underscore_and_uppercases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SNOWFLAKE_USER", "svc_test")
    assert EnvSecretProvider().get("snowflake-user") == "svc_test"


def test_missing_raises_keyerror(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONEXISTENT_SECRET", raising=False)
    with pytest.raises(KeyError):
        EnvSecretProvider().get("nonexistent-secret")


def test_empty_string_treated_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EMPTY_SECRET", "")
    with pytest.raises(KeyError):
        EnvSecretProvider().get("empty-secret")


def test_get_optional_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOT_SET", raising=False)
    assert EnvSecretProvider().get_optional("not-set", default="fallback") == "fallback"
