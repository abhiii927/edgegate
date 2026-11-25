from __future__ import annotations

import time

from limiter import RateLimiter


def test_burst_allow_and_excess_blocked() -> None:
    limiter = RateLimiter(max_tokens=3, refill_rate=0.1)
    assert limiter.is_allowed("10.0.0.1") is True
    assert limiter.is_allowed("10.0.0.1") is True
    assert limiter.is_allowed("10.0.0.1") is True
    assert limiter.is_allowed("10.0.0.1") is False


def test_token_refill_over_time() -> None:
    limiter = RateLimiter(max_tokens=2, refill_rate=20.0)
    assert limiter.is_allowed("10.0.0.2") is True
    assert limiter.is_allowed("10.0.0.2") is True
    assert limiter.is_allowed("10.0.0.2") is False

    time.sleep(0.06)  # ~1.2 tokens replenished at 20 tokens/sec
    assert limiter.is_allowed("10.0.0.2") is True


def test_ttl_and_lru_eviction() -> None:
    limiter = RateLimiter(max_tokens=2, refill_rate=1.0, ttl_seconds=0.05, max_buckets=2)
    assert limiter.is_allowed("10.0.0.1") is True
    assert limiter.is_allowed("10.0.0.2") is True
    assert limiter.is_allowed("10.0.0.3") is True
    assert len(limiter.buckets) == 2
    assert "10.0.0.1" not in limiter.buckets

    time.sleep(0.06)
    assert limiter.is_allowed("10.0.0.4") is True
    assert list(limiter.buckets.keys()) == ["10.0.0.4"]
