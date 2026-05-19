"""HTTP client with retry, timeout, User-Agent, and basic rate limiting.

Centralizes all outbound HTTP traffic to ensure consistent behavior:
- proper retry on 429/500/502/503/504,
- explicit connect/read timeout,
- spoofed UA (default ``requests`` UA is often blocked),
- optional minimum-interval throttle between requests,
- ``Retry-After`` header respect via urllib3's ``Retry`` class.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rss_blog_archiver.logging_setup import get_logger

logger = get_logger(__name__)

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "rss-blog-archiver/0.1 (+https://github.com/HyperNano64/rss-blog-archiver)"
)

# (connect, read) in seconds. Lenient by default; tweakable per-call.
DEFAULT_TIMEOUT: tuple[float, float] = (10.0, 30.0)


class RateLimiter:
    """Very small token-bucket / minimum-interval limiter.

    Thread-safe; cheap; works well for the scrape pattern of "many sequential
    requests against the same host".
    """

    def __init__(self, min_interval: float = 0.0) -> None:
        self._min_interval = max(0.0, float(min_interval))
        self._last_call: float = 0.0
        self._lock = threading.Lock()

    def acquire(self) -> None:
        if self._min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                sleep_for = self._min_interval - elapsed
                time.sleep(sleep_for)
            self._last_call = time.monotonic()


class HttpClient:
    """Thin wrapper around :class:`requests.Session` with sane defaults."""

    def __init__(
        self,
        *,
        retries: int = 5,
        backoff_factor: float = 0.5,
        timeout: tuple[float, float] = DEFAULT_TIMEOUT,
        user_agent: str = DEFAULT_USER_AGENT,
        rate_limit_interval: float = 0.0,
        verify_ssl: bool = True,
    ) -> None:
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Language": "en-US,en;q=0.9,id;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        self.session.verify = verify_ssl
        retry = Retry(
            total=retries,
            connect=retries,
            read=retries,
            status=retries,
            backoff_factor=backoff_factor,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"HEAD", "GET", "OPTIONS"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=20, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        self._limiter = RateLimiter(rate_limit_interval)

    def get(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        self._limiter.acquire()
        logger.debug("GET %s", url)
        return self.session.get(url, **kwargs)

    def head(self, url: str, **kwargs: Any) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        kwargs.setdefault("allow_redirects", True)
        self._limiter.acquire()
        logger.debug("HEAD %s", url)
        return self.session.head(url, **kwargs)

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> HttpClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
