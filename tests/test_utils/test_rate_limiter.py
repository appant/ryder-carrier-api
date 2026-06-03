"""Tests for the token-bucket rate limiter (deterministic — injected clock)."""

from __future__ import annotations

import pytest

from ryder_carrier_api.utils.rate_limiter import TokenBucket


class _FakeClock:
    """Virtual clock: sleep() advances time instead of blocking."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_initial_burst_up_to_capacity_does_not_wait() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=10, capacity=5, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    assert clock.sleeps == []  # full bucket → all immediate


def test_blocks_when_empty_then_refills_at_rate() -> None:
    clock = _FakeClock()
    bucket = TokenBucket(rate=10, capacity=1, monotonic=clock.monotonic, sleep=clock.sleep)
    bucket.acquire()  # consumes the single token immediately
    bucket.acquire()  # empty → wait one token / 10 rps = 0.1s
    assert clock.sleeps == pytest.approx([0.1])


def test_steady_state_rate_is_capped() -> None:
    """After the initial token, acquisitions are paced at ~1/rate seconds each."""
    clock = _FakeClock()
    bucket = TokenBucket(rate=4, capacity=1, monotonic=clock.monotonic, sleep=clock.sleep)
    for _ in range(5):
        bucket.acquire()
    # 1 free (initial token) + 4 that each waited 0.25s (1/4 rps).
    assert clock.sleeps == pytest.approx([0.25, 0.25, 0.25, 0.25])


def test_rejects_nonpositive_rate() -> None:
    with pytest.raises(ValueError):
        TokenBucket(rate=0)
