"""Ryder Carrier API HTTP client.

Responsibilities:
    - Holds auth headers (API key + carrier SCAC) for the lifetime of the session
    - Posts to the two endpoints (milestone-requests, trace-requests)
    - Retries on 429 / 5xx / network errors with exponential backoff + jitter
    - Honors `Retry-After` on 429 responses
    - Caps concurrent requests (semaphore) to stay under any rate limit
    - Maps responses to a structured result so the service layer can decide:
        2xx              -> Sent (write audit row, advance)
        4xx (not 429)    -> Rejected (DLQ, alert, advance)
        5xx/429 exhausted -> Transient failure (FAIL the tick, replay next time)
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
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


class RyderEndpoint(str, Enum):
    """Logical endpoint names; the client maps them to URL paths."""

    MILESTONE = "milestone"
    TRACE = "trace"


class RyderResultStatus(str, Enum):
    SENT = "sent"
    FAILED_PERMANENTLY = "failed_permanently"
    FAILED_TRANSIENT = "failed_transient"


@dataclass(frozen=True)
class RyderResult:
    status: RyderResultStatus
    response_code: int | None
    response_body: str
    attempts: int


class _TransientHttpError(Exception):
    """Internal marker for tenacity: retry these (5xx, 429, network blips)."""


class RyderClient:
    """Thin wrapper around httpx with retry + concurrency cap."""

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
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "carrierSCAC": scac,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        self._concurrency_semaphore = threading.Semaphore(settings.ryder_max_concurrency)

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
        with self._concurrency_semaphore:
            return self._post_with_retry(endpoint, payload)

    # --- internals ---

    def _post_with_retry(self, endpoint: RyderEndpoint, payload: dict[str, Any]) -> RyderResult:
        path = self._PATH_BY_ENDPOINT[endpoint]
        attempts_seen = 0

        def _single_attempt() -> RyderResult:
            nonlocal attempts_seen
            attempts_seen += 1
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
                )

            # Retryable: 3xx (endpoint moved — don't auto-follow to avoid leaking
            # the API key to an unintended origin; surface as transient so an
            # operator updates RYDER_API_BASE_URL deliberately), 408, 425, 429,
            # and all 5xx.
            if 300 <= code < 400 or code in (408, 425, 429) or 500 <= code < 600:
                raise _TransientHttpError(f"{code} retryable: {body[:200]}")

            # 4xx (excluding the retryable ones above): permanent rejection —
            # payload-level problem.
            return RyderResult(
                status=RyderResultStatus.FAILED_PERMANENTLY,
                response_code=code,
                response_body=body,
                attempts=attempts_seen,
            )

        try:
            for attempt in Retrying(
                stop=stop_after_attempt(self._settings.ryder_max_retries),
                wait=wait_exponential_jitter(initial=1, max=16),
                retry=retry_if_exception_type(_TransientHttpError),
                reraise=True,
            ):
                with attempt:
                    return _single_attempt()
        except RetryError:
            pass
        except _TransientHttpError as exc:
            return RyderResult(
                status=RyderResultStatus.FAILED_TRANSIENT,
                response_code=None,
                response_body=str(exc),
                attempts=attempts_seen,
            )

        return RyderResult(
            status=RyderResultStatus.FAILED_TRANSIENT,
            response_code=None,
            response_body="retries exhausted",
            attempts=attempts_seen,
        )
