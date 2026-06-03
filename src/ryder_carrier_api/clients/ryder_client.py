"""Ryder Carrier API HTTP client.

Responsibilities:
    - Holds auth headers (API key + carrier SCAC) for the lifetime of the session
    - Posts to the two endpoints (milestone-requests, trace-requests)
    - Retries on 429 / 5xx / network errors with exponential backoff + jitter
    - Honors `Retry-After` on 429 / 503 responses (seconds or HTTP-date); if the
      header asks for longer than `ryder_retry_after_cap_seconds`, stops retrying
      in-process and defers the row to the next run instead of camping on a worker
    - Paces outgoing requests with a token bucket (`ryder_max_rps`) to stay under
      Ryder's per-SCAC rate limit; overall concurrency is bounded by the caller's
      thread pool
    - Maps responses to a structured result so the service layer can decide:
        2xx              -> Sent (write audit row, advance)
        4xx (not 429)    -> Rejected (DLQ, alert, advance)
        5xx/429 exhausted -> Transient failure (FAIL the tick, replay next time)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any, ClassVar

import httpx
from tenacity import (
    RetryError,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ..config import AppSettings
from ..secrets.base import SecretProvider
from ..utils.rate_limiter import TokenBucket


class RyderEndpoint(StrEnum):
    """Logical endpoint names; the client maps them to URL paths."""

    MILESTONE = "milestone"
    TRACE = "trace"


class RyderResultStatus(StrEnum):
    SENT = "sent"
    FAILED_PERMANENTLY = "failed_permanently"
    FAILED_TRANSIENT = "failed_transient"


@dataclass(frozen=True)
class RyderResult:
    status: RyderResultStatus
    response_code: int | None
    response_body: str
    attempts: int
    # True if any attempt was rate-limited (HTTP 429), regardless of the final
    # outcome — lets the service layer count/alert on throttling distinctly from
    # generic transient failures.
    throttled: bool = False


class _TransientHttpError(Exception):
    """Internal marker for tenacity: retry these (5xx, 429, network blips).

    Carries the parsed Retry-After delay (seconds) when the response supplied
    one, so the wait policy can honor it instead of using plain backoff.
    """

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class _RetryAfterExceedsCapError(Exception):
    """A retryable response asked us to wait longer than we'll hold a worker.

    Not retryable in-process: we stop and let the watermark replay the row on
    the next scheduled run (the rate/outage window will have cleared by then).
    """

    def __init__(self, code: int, retry_after: float) -> None:
        super().__init__(f"{code} Retry-After {retry_after}s exceeds cap")
        self.code = code
        self.retry_after = retry_after


class RyderClient:
    """Thin wrapper around httpx with retry + token-bucket rate limiting."""

    _PATH_BY_ENDPOINT: ClassVar[dict[RyderEndpoint, str]] = {
        RyderEndpoint.MILESTONE: "/loads/milestone-requests",
        RyderEndpoint.TRACE: "/loads/trace-requests",
    }

    def __init__(self, settings: AppSettings, secrets: SecretProvider) -> None:
        self._settings = settings
        api_key = secrets.get(settings.secret_name_ryder_api_key)
        scac = secrets.get(settings.secret_name_ryder_scac)
        self._http = httpx.Client(
            base_url=settings.ryder_api_base_url,
            timeout=settings.ryder_timeout_seconds,
            follow_redirects=True,
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "carrierSCAC": scac,
                "Accept": "application/json",
            },
        )
        # Proactive rate cap, shared by all worker threads. Replaces the old
        # concurrency semaphore (which equalled the thread count and so never
        # actually limited anything). Concurrency is bounded by the thread pool.
        self._rate_limiter = TokenBucket(settings.ryder_max_rps)

    # --- lifecycle ---

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> RyderClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    # --- public API ---

    def post(self, endpoint: RyderEndpoint, payload: dict[str, Any]) -> RyderResult:
        """Post a payload and return a structured result.

        Retries transient failures internally; never raises for 4xx.
        Service layer decides what to do based on the returned status.
        """
        return self._post_with_retry(endpoint, payload)

    # --- internals ---

    def _post_with_retry(self, endpoint: RyderEndpoint, payload: dict[str, Any]) -> RyderResult:
        path = self._PATH_BY_ENDPOINT[endpoint]
        attempts_seen = 0
        saw_throttle = False
        cap = float(self._settings.ryder_retry_after_cap_seconds)

        def _single_attempt() -> RyderResult:
            nonlocal attempts_seen, saw_throttle
            attempts_seen += 1
            # Pace every outgoing request (including retries) under the rate cap.
            self._rate_limiter.acquire()
            try:
                response = self._http.post(path, json=payload)
            except httpx.HTTPError as exc:
                raise _TransientHttpError(f"network/transport error: {exc}") from exc

            code = response.status_code
            body = response.text

            if 200 <= code < 300:
                return RyderResult(
                    status=RyderResultStatus.SENT,
                    response_code=code,
                    response_body=body,
                    attempts=attempts_seen,
                    throttled=saw_throttle,
                )

            # Retryable: 408, 425, 429, and all 5xx.
            # 3xx redirects are followed automatically by httpx (follow_redirects=True)
            # so they never surface here.
            if code == 429 or code in (408, 425) or 500 <= code < 600:
                if code == 429:
                    saw_throttle = True
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                if retry_after is not None and retry_after > cap:
                    # Too long to hold a worker — defer to the next scheduled run.
                    raise _RetryAfterExceedsCapError(code, retry_after)
                raise _TransientHttpError(
                    f"{code} retryable: {body[:200]}", retry_after=retry_after
                )

            # 4xx (excluding the retryable ones above): permanent rejection —
            # payload-level problem.
            return RyderResult(
                status=RyderResultStatus.FAILED_PERMANENTLY,
                response_code=code,
                response_body=body,
                attempts=attempts_seen,
                throttled=saw_throttle,
            )

        # Wait policy: honor a server-supplied Retry-After (capped), otherwise
        # fall back to exponential backoff with jitter. Jitter matters on the
        # fallback path — it de-syncs the worker threads that were all throttled
        # at once so they don't retry in lockstep.
        fallback_wait = wait_exponential_jitter(initial=1, max=16)

        def _wait(retry_state: Any) -> float:
            outcome = retry_state.outcome
            exc = outcome.exception() if outcome is not None else None
            if isinstance(exc, _TransientHttpError) and exc.retry_after is not None:
                return min(float(exc.retry_after), cap)
            return fallback_wait(retry_state)

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._settings.ryder_max_retries),
                wait=_wait,
                retry=retry_if_exception_type(_TransientHttpError),
                reraise=True,
            ):
                with attempt:
                    return _single_attempt()
        except RetryError:
            pass
        except _RetryAfterExceedsCapError as exc:
            return RyderResult(
                status=RyderResultStatus.FAILED_TRANSIENT,
                response_code=exc.code,
                response_body=(f"deferred: Retry-After {exc.retry_after}s exceeds {cap}s cap"),
                attempts=attempts_seen,
                throttled=saw_throttle,
            )
        except _TransientHttpError as exc:
            return RyderResult(
                status=RyderResultStatus.FAILED_TRANSIENT,
                response_code=None,
                response_body=str(exc),
                attempts=attempts_seen,
                throttled=saw_throttle,
            )

        return RyderResult(
            status=RyderResultStatus.FAILED_TRANSIENT,
            response_code=None,
            response_body="retries exhausted",
            attempts=attempts_seen,
            throttled=saw_throttle,
        )


def _parse_retry_after(value: str | None) -> float | None:
    """Parse a Retry-After header into a non-negative delay in seconds.

    Handles both legal forms — an integer number of seconds, or an HTTP-date —
    and returns None when the header is absent or unparseable (caller then falls
    back to exponential backoff). Non-finite values (inf/nan) are rejected.
    """
    if not value:
        return None
    value = value.strip()
    try:
        seconds = float(value)
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when is None:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(tz=UTC)).total_seconds())
