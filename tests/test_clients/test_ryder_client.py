from __future__ import annotations

import httpx
import pytest
import respx

from ryder_carrier_api.clients.ryder_client import (
    RyderClient,
    RyderEndpoint,
    RyderResultStatus,
)
from ryder_carrier_api.config import AppSettings
from ryder_carrier_api.secrets.base import SecretProvider


class _FakeSecrets(SecretProvider):
    def get(self, name: str) -> str:
        return {
            "ryder-api-key": "test-key",
            "ryder-carrier-scac": "USMM",
        }.get(name, "")


def _settings() -> AppSettings:
    return AppSettings(
        snowflake_account="x",
        snowflake_database="x",
        ryder_api_base_url="https://api.example.test/v1",
        ryder_max_retries=3,
        ryder_max_concurrency=1,
        ryder_timeout_seconds=5,
    )  # type: ignore[call-arg]


def _client() -> RyderClient:
    return RyderClient(settings=_settings(), secrets=_FakeSecrets())


@respx.mock
def test_200_classified_as_sent() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        200, json={"ok": True}
    )
    result = _client().post(RyderEndpoint.TRACE, {"loadNumber": "1"})
    assert result.status == RyderResultStatus.SENT
    assert result.response_code == 200
    assert result.attempts == 1


@respx.mock
def test_401_classified_as_permanent_no_retry() -> None:
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        401, json={"error": "invalid key"}
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_PERMANENTLY
    assert result.response_code == 401
    assert result.attempts == 1  # never retried
    assert route.call_count == 1


@respx.mock
def test_400_classified_as_permanent() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").respond(400)
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_PERMANENTLY


@respx.mock
def test_500_retried_then_classified_as_transient() -> None:
    """5xx is transient — should retry up to ryder_max_retries times."""
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(500)
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT
    assert route.call_count == 3  # matches ryder_max_retries


@respx.mock
def test_429_retried_as_transient() -> None:
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(429)
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT
    assert route.call_count == 3


@respx.mock
def test_3xx_classified_as_transient_not_followed() -> None:
    """3xx must surface as transient so an operator updates the base URL deliberately
    (instead of httpx auto-following and leaking the API key cross-origin)."""
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        301, headers={"Location": "https://attacker.example.com/"}
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT
    assert route.call_count == 3


@respx.mock
def test_408_classified_as_transient() -> None:
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(408)
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT
    assert route.call_count == 3


@respx.mock
def test_transient_then_success_returns_sent() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").mock(
        side_effect=[
            httpx.Response(503),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.SENT
    assert result.attempts == 2


@respx.mock
def test_network_error_treated_as_transient() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").mock(
        side_effect=httpx.ConnectError("boom")
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT


@respx.mock
def test_milestone_uses_correct_endpoint() -> None:
    route = respx.post(
        "https://api.example.test/v1/loads/milestone-requests"
    ).respond(200, json={})
    _client().post(RyderEndpoint.MILESTONE, {})
    assert route.called


@respx.mock
def test_auth_headers_set_from_secrets() -> None:
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(200)
    _client().post(RyderEndpoint.TRACE, {})
    request = route.calls[0].request
    assert request.headers["Ocp-Apim-Subscription-Key"] == "test-key"
    assert request.headers["carrierSCAC"] == "USMM"


@respx.mock
def test_response_body_captured_in_result() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        400, text="bad loadNumber"
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert "bad loadNumber" in result.response_body
