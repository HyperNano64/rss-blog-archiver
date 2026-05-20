"""Unit tests for :mod:`rss_blog_archiver.async_http_client`.

We use ``httpx.MockTransport`` to drive request/response cycles without
touching the network. The transport receives every outgoing
``httpx.Request`` and synchronously returns an ``httpx.Response``, so it
is perfect for asserting retry / backoff / header behavior in pure
unit-test land.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest

from rss_blog_archiver.async_http_client import (
    AsyncHttpClient,
    AsyncRateLimiter,
    HostSemaphorePool,
    _host_key,
    _parse_retry_after,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _scripted_transport(responses):
    """Build a MockTransport that yields responses in order.

    ``responses`` is a list of ``httpx.Response`` objects or callables
    ``(httpx.Request) -> httpx.Response``.
    """
    iterator = iter(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        try:
            entry = next(iterator)
        except StopIteration:
            return httpx.Response(500, text="unexpected extra request")
        if callable(entry):
            return entry(request)
        return entry

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# _host_key
# ---------------------------------------------------------------------------
def test_host_key_lowercases_and_handles_none() -> None:
    assert _host_key("https://Example.COM/path") == "example.com"
    assert _host_key(None) == ""
    assert _host_key(httpx.URL("https://AlPhA.org/")) == "alpha.org"


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------
def test_parse_retry_after_numeric() -> None:
    assert _parse_retry_after("0") == 0.0
    assert _parse_retry_after("7") == 7.0
    assert _parse_retry_after("  12.5  ") == 12.5


def test_parse_retry_after_handles_empty_and_bad_input() -> None:
    assert _parse_retry_after(None) is None
    assert _parse_retry_after("") is None
    assert _parse_retry_after("not-a-date") is None


def test_parse_retry_after_handles_http_date_far_future() -> None:
    # "Wed, 21 Oct 2099 07:28:00 GMT" — definitely > 0 seconds away.
    seconds = _parse_retry_after("Wed, 21 Oct 2099 07:28:00 GMT")
    assert seconds is not None
    assert seconds > 0


# ---------------------------------------------------------------------------
# AsyncRateLimiter
# ---------------------------------------------------------------------------
def test_async_rate_limiter_zero_interval_is_passthrough() -> None:
    async def go() -> None:
        limiter = AsyncRateLimiter(0.0)
        # Many acquires must complete near-instantly with no sleeping.
        for _ in range(10):
            await limiter.acquire("https://x")

    asyncio.run(go())


def test_async_rate_limiter_enforces_minimum_interval() -> None:
    async def go() -> float:
        limiter = AsyncRateLimiter(0.05)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        await limiter.acquire("https://example.com/a")
        await limiter.acquire("https://example.com/b")
        await limiter.acquire("https://example.com/c")
        return loop.time() - t0

    elapsed = asyncio.run(go())
    # Two enforced gaps of ~0.05s each -> at least 0.08s wall time.
    assert elapsed >= 0.08


def test_async_rate_limiter_is_per_host() -> None:
    async def go() -> float:
        limiter = AsyncRateLimiter(0.05)
        loop = asyncio.get_event_loop()
        t0 = loop.time()
        await limiter.acquire("https://host-a/")
        await limiter.acquire("https://host-b/")  # different host -> no wait
        return loop.time() - t0

    elapsed = asyncio.run(go())
    assert elapsed < 0.05


# ---------------------------------------------------------------------------
# HostSemaphorePool
# ---------------------------------------------------------------------------
def test_host_semaphore_pool_returns_same_sem_per_host() -> None:
    async def go() -> None:
        pool = HostSemaphorePool(3)
        a1 = await pool.acquire("https://a.example/")
        a2 = await pool.acquire("https://A.example/path")  # same host, lowercased
        b1 = await pool.acquire("https://b.example/")
        assert a1 is a2
        assert a1 is not b1

    asyncio.run(go())


def test_host_semaphore_pool_caps_concurrency() -> None:
    async def go() -> int:
        pool = HostSemaphorePool(2)
        sem = await pool.acquire("https://x.example/")
        in_flight = 0
        peak = 0

        async def slot() -> None:
            nonlocal in_flight, peak
            async with sem:
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.02)
                in_flight -= 1

        await asyncio.gather(*(slot() for _ in range(6)))
        return peak

    peak = asyncio.run(go())
    assert peak == 2


# ---------------------------------------------------------------------------
# AsyncHttpClient behavior
# ---------------------------------------------------------------------------
def test_async_http_client_returns_2xx_with_one_request() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)

    async def go() -> None:
        async with AsyncHttpClient(transport=transport) as http:
            resp = await http.get("https://example.com/ping")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

    asyncio.run(go())
    assert calls == ["/ping"]


def test_async_http_client_retries_on_500_then_succeeds() -> None:
    transport = _scripted_transport([
        httpx.Response(500, text="boom"),
        httpx.Response(502, text="bad gw"),
        httpx.Response(200, text="ok"),
    ])

    async def go() -> httpx.Response:
        async with AsyncHttpClient(
            transport=transport, retries=3, backoff_factor=0.0,
        ) as http:
            return await http.get("https://example.com/")

    resp = asyncio.run(go())
    assert resp.status_code == 200


def test_async_http_client_gives_up_after_retry_budget() -> None:
    transport = _scripted_transport([
        httpx.Response(503) for _ in range(10)
    ])

    async def go() -> httpx.Response:
        async with AsyncHttpClient(
            transport=transport, retries=2, backoff_factor=0.0,
        ) as http:
            return await http.get("https://example.com/")

    resp = asyncio.run(go())
    # After exhausting retries the last response is surfaced to caller.
    assert resp.status_code == 503


def test_async_http_client_honors_retry_after_numeric() -> None:
    transport = _scripted_transport([
        httpx.Response(429, headers={"Retry-After": "0"}),
        httpx.Response(200, text="ok"),
    ])

    async def go() -> int:
        async with AsyncHttpClient(
            transport=transport, retries=2, backoff_factor=0.0,
        ) as http:
            resp = await http.get("https://example.com/")
            return resp.status_code

    assert asyncio.run(go()) == 200


def test_async_http_client_raises_on_network_error_after_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nope", request=request)

    transport = httpx.MockTransport(handler)

    async def go() -> None:
        async with AsyncHttpClient(
            transport=transport, retries=1, backoff_factor=0.0,
        ) as http:
            await http.get("https://example.com/")

    with pytest.raises(httpx.RequestError):
        asyncio.run(go())


def test_async_http_client_per_host_semaphore_limits_concurrency() -> None:
    """All requests to the same host must respect ``max_concurrency``."""
    in_flight = 0
    peak = 0
    lock = asyncio.Lock()

    async def handler_async(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        async with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        async with lock:
            in_flight -= 1
        return httpx.Response(200, text="ok")

    # MockTransport handler must be sync, so route through asyncio.
    def handler(request: httpx.Request) -> httpx.Response:
        return asyncio.get_event_loop().run_until_complete(  # pragma: no cover
            handler_async(request)
        )

    # Use AsyncHTTPTransport pattern: build via an inner async client.
    class _CountingTransport(httpx.AsyncBaseTransport):
        async def handle_async_request(
            self, request: httpx.Request
        ) -> httpx.Response:
            return await handler_async(request)

    transport = _CountingTransport()

    async def go() -> int:
        async with AsyncHttpClient(
            transport=transport, max_concurrency=3,
        ) as http:
            await asyncio.gather(*(
                http.get(f"https://example.com/{i}") for i in range(10)
            ))
            return peak

    observed_peak = asyncio.run(go())
    assert observed_peak <= 3
