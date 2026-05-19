"""Tests for the HTTP client's per-host rate limiter."""

from __future__ import annotations

import time

from rss_blog_archiver.http_client import RateLimiter


class TestRateLimiterPerHost:
    def test_no_throttle_when_interval_zero(self) -> None:
        limiter = RateLimiter(0.0)
        start = time.monotonic()
        for _ in range(5):
            limiter.acquire("https://example.com/")
        assert time.monotonic() - start < 0.05

    def test_per_host_independence(self) -> None:
        # 0.1 s interval per host. Two different hosts should NOT delay each
        # other.
        limiter = RateLimiter(0.1)
        start = time.monotonic()
        limiter.acquire("https://a.com/")
        limiter.acquire("https://b.com/")
        assert time.monotonic() - start < 0.05

    def test_same_host_throttled(self) -> None:
        limiter = RateLimiter(0.1)
        start = time.monotonic()
        limiter.acquire("https://a.com/")
        limiter.acquire("https://a.com/")
        elapsed = time.monotonic() - start
        assert elapsed >= 0.09  # allow ~10% jitter on slow CI
