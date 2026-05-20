"""Async HTTP client built on ``httpx``.

Mirrors the surface of :class:`rss_blog_archiver.http_client.HttpClient`
so that the async pipeline can stand in wherever the sync one used to.

What it adds over a plain ``httpx.AsyncClient``:

- retry on 429 / 5xx with exponential backoff,
- explicit ``Retry-After`` header honor (seconds and HTTP-date),
- per-host minimum-interval rate limiting (async, ``asyncio.Lock``),
- per-host concurrency limiting via ``asyncio.Semaphore`` (so a single
  blog never sees more than ``max_concurrency`` in-flight requests),
- spoofed User-Agent matching the sync client.

The module intentionally does *not* expose ``httpx``'s streaming API yet
— a future PR can add ``aiter_bytes`` for very large downloads.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from rss_blog_archiver.http_client import DEFAULT_TIMEOUT, DEFAULT_USER_AGENT
from rss_blog_archiver.logging_setup import get_logger

logger = get_logger(__name__)

_RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


# ---------------------------------------------------------------------------
# Per-host helpers
# ---------------------------------------------------------------------------
def _host_key(url: str | httpx.URL | None) -> str:
    if url is None:
        return ""
    if isinstance(url, httpx.URL):
        return url.host.lower()
    try:
        return urlparse(str(url)).netloc.lower()
    except Exception:
        return ""


class AsyncRateLimiter:
    """Async minimum-interval limiter, keyed by host.

    Unlike :class:`rss_blog_archiver.http_client.RateLimiter`, this waits
    via ``asyncio.sleep`` so it does not block the event loop.
    """

    def __init__(self, min_interval: float = 0.0) -> None:
        self._min_interval = max(0.0, float(min_interval))
        self._last_call: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str | httpx.URL | None = None) -> None:
        if self._min_interval <= 0:
            return
        host = _host_key(url)
        async with self._lock:
            loop = asyncio.get_event_loop()
            now = loop.time()
            last = self._last_call.get(host, 0.0)
            elapsed = now - last
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_call[host] = asyncio.get_event_loop().time()


class HostSemaphorePool:
    """``asyncio.Semaphore`` per host.

    Caller obtains the semaphore for the host of a URL and uses it as an
    async context manager. Default semaphore size is the configured
    ``max_concurrency`` (number of in-flight requests per host).
    """

    def __init__(self, max_concurrency: int) -> None:
        self._max = max(1, int(max_concurrency))
        self._sems: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, url: str | httpx.URL | None) -> asyncio.Semaphore:
        host = _host_key(url)
        async with self._lock:
            sem = self._sems.get(host)
            if sem is None:
                sem = asyncio.Semaphore(self._max)
                self._sems[host] = sem
            return sem


# ---------------------------------------------------------------------------
# Retry-After parsing
# ---------------------------------------------------------------------------
def _parse_retry_after(header_value: str | None) -> float | None:
    if not header_value:
        return None
    header_value = header_value.strip()
    if not header_value:
        return None
    # Numeric seconds form.
    try:
        return max(0.0, float(header_value))
    except ValueError:
        pass
    # HTTP-date form.
    try:
        target = parsedate_to_datetime(header_value)
    except (TypeError, ValueError):
        return None
    if target is None:
        return None
    import datetime as _dt

    now = _dt.datetime.now(_dt.timezone.utc)
    if target.tzinfo is None:
        target = target.replace(tzinfo=_dt.timezone.utc)
    return max(0.0, (target - now).total_seconds())


# ---------------------------------------------------------------------------
# Public client
# ---------------------------------------------------------------------------
class AsyncHttpClient:
    """Async drop-in for :class:`HttpClient`.

    Use as an async context manager:

    .. code-block:: python

        async with AsyncHttpClient(rate_limit_interval=1.0) as http:
            resp = await http.get("https://example.com/")
    """

    def __init__(
        self,
        *,
        retries: int = 5,
        backoff_factor: float = 0.5,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limit_interval: float = 0.0,
        max_concurrency: int = 5,
        verify_ssl: bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._retries = max(0, int(retries))
        self._backoff_factor = max(0.0, float(backoff_factor))
        connect_timeout, read_timeout = timeout
        self._timeout = httpx.Timeout(
            connect=connect_timeout, read=read_timeout,
            write=read_timeout, pool=read_timeout,
        )
        headers = {
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        }
        client_kwargs: dict[str, Any] = {
            "headers": headers,
            "timeout": self._timeout,
            "verify": verify_ssl,
            "follow_redirects": True,
        }
        if transport is not None:
            client_kwargs["transport"] = transport
        self._client = httpx.AsyncClient(**client_kwargs)
        self._limiter = AsyncRateLimiter(rate_limit_interval)
        self._semaphores = HostSemaphorePool(max_concurrency)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> AsyncHttpClient:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------
    # Public requests
    # ------------------------------------------------------------------
    async def get(
        self, url: str, *, headers: Mapping[str, str] | None = None, **kwargs: Any,
    ) -> httpx.Response:
        return await self._request("GET", url, headers=headers, **kwargs)

    async def head(
        self, url: str, *, headers: Mapping[str, str] | None = None, **kwargs: Any,
    ) -> httpx.Response:
        return await self._request("HEAD", url, headers=headers, **kwargs)

    # ------------------------------------------------------------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        attempt = 0
        last_exc: Exception | None = None
        sem = await self._semaphores.acquire(url)
        async with sem:
            while True:
                await self._limiter.acquire(url)
                logger.debug("%s %s (attempt %d)", method, url, attempt + 1)
                try:
                    response = await self._client.request(
                        method, url, headers=dict(headers or {}), **kwargs,
                    )
                except httpx.RequestError as exc:
                    last_exc = exc
                    if attempt >= self._retries:
                        raise
                    await self._backoff_sleep(attempt, None)
                    attempt += 1
                    continue

                if response.status_code not in _RETRYABLE_STATUS:
                    return response
                if attempt >= self._retries:
                    return response  # surfaced to caller; let them inspect
                retry_after = _parse_retry_after(response.headers.get("Retry-After"))
                with contextlib.suppress(Exception):
                    await response.aclose()
                await self._backoff_sleep(attempt, retry_after)
                attempt += 1

        # Unreachable: the loop only exits via return/raise.
        if last_exc is not None:  # pragma: no cover - defensive
            raise last_exc
        raise RuntimeError("AsyncHttpClient._request exited unexpectedly")

    async def _backoff_sleep(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None and retry_after > 0:
            await asyncio.sleep(retry_after)
            return
        # Exponential backoff with mild jitter.
        delay = self._backoff_factor * (2 ** attempt)
        jitter = random.uniform(0.0, max(0.05, delay * 0.1))
        await asyncio.sleep(delay + jitter)
