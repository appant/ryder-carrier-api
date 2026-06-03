from __future__ import annotations

import httpx
import respx

from ryder_carrier_api.clients.ryder_client import (
    RyderClient,
    RyderEndpoint,
    RyderResultStatus,
    _parse_retry_after,
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
    respx.post("https://api.example.test/v1/loads/trace-requests").respond(200, json={"ok": True})
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
    assert result.throttled is True  # 429 seen → flagged even when it exhausts


@respx.mock
def test_3xx_followed_and_final_response_determines_outcome() -> None:
    """3xx redirects are followed automatically by httpx; the final response code
    determines the outcome (not the redirect itself)."""
    respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        301, headers={"Location": "https://api.example.test/v1/loads/trace-requests-new"}
    )
    respx.get("https://api.example.test/v1/loads/trace-requests-new").respond(
        200, json={"ok": True}
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.SENT
    assert result.response_code == 200


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
    route = respx.post("https://api.example.test/v1/loads/milestone-requests").respond(200, json={})
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


# --- Retry-After parsing ---


def test_parse_retry_after_integer_seconds() -> None:
    assert _parse_retry_after("120") == 120.0


def test_parse_retry_after_zero() -> None:
    assert _parse_retry_after("0") == 0.0


def test_parse_retry_after_absent_or_blank() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None


def test_parse_retry_after_unparseable() -> None:
    assert _parse_retry_after("soon") is None


def test_parse_retry_after_rejects_non_finite() -> None:
    assert _parse_retry_after("inf") is None
    assert _parse_retry_after("nan") is None


def test_parse_retry_after_http_date_future() -> None:
    from datetime import UTC, datetime, timedelta
    from email.utils import format_datetime

    future = datetime.now(tz=UTC) + timedelta(seconds=50)
    parsed = _parse_retry_after(format_datetime(future))
    assert parsed is not None
    assert 30 < parsed <= 51


def test_parse_retry_after_http_date_in_past_is_zero() -> None:
    assert _parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT") == 0.0


# --- Retry-After honored on 429 ---


@respx.mock
def test_429_with_short_retry_after_is_honored_then_succeeds() -> None:
    respx.post("https://api.example.test/v1/loads/trace-requests").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "1"}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.SENT
    assert result.attempts == 2
    assert result.throttled is True


@respx.mock
def test_429_with_long_retry_after_defers_without_retrying() -> None:
    """Retry-After beyond the cap → stop in-process, return transient, replay later."""
    route = respx.post("https://api.example.test/v1/loads/trace-requests").respond(
        429, headers={"Retry-After": "999"}
    )
    result = _client().post(RyderEndpoint.TRACE, {})
    assert result.status == RyderResultStatus.FAILED_TRANSIENT
    assert result.response_code == 429  # preserved, not masked as a generic transient
    assert result.throttled is True
    assert result.attempts == 1  # deferred immediately, never retried
    assert route.call_count == 1
