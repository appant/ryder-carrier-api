"""Thread-safe token-bucket rate limiter.

Used by RyderClient to cap the *outgoing* request rate below Ryder's
per-SCAC API limit, so we throttle ourselves proactively instead of
discovering the limit via 429s. One bucket is shared across all worker
threads in a run; ``acquire()`` blocks until a token is available.

The clock and sleep functions are injectable so the limiter can be tested
deterministically without real time passing.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class TokenBucket:
    """Classic token bucket: refills at ``rate`` tokens/sec up to ``capacity``.

    ``acquire()`` removes one token, blocking (and sleeping) until one is
    available. Thread-safe: the lock is held only for the brief token
    accounting, never across the sleep, so workers don't serialize on it.
    """

    def __init__(
        self,
        rate: float,
        capacity: float | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self._rate = float(rate)
        # Default burst = one second's worth of tokens: smooths a brief burst
        # after idle while still staying under a per-second limit.
        self._capacity = float(capacity) if capacity is not None else max(1.0, self._rate)
        self._tokens = self._capacity
        self._monotonic = monotonic
        self._sleep = sleep
        self._last = monotonic()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        """Block until a token is available, then consume one."""
        while True:
            with self._lock:
                now = self._monotonic()
                self._tokens = min(self._capacity, self._tokens + (now - self._last) * self._rate)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                wait = (1.0 - self._tokens) / self._rate
            self._sleep(wait)
