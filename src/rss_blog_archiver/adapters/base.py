"""Abstract base class for CMS adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from bs4 import BeautifulSoup, Tag

from rss_blog_archiver.extractors.content import extract_main_content, strip_noise
from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.models import FeedPage


@dataclass(slots=True)
class AdapterDetectionResult:
    """Outcome of an adapter's CMS detection check."""

    matched: bool
    confidence: float
    feed_url: str
    base_url: str
    notes: list[str] = field(default_factory=list)


class BaseAdapter(ABC):
    """Abstract interface for per-CMS scraping logic."""

    name: str = "base"

    def __init__(self, http: HttpClient) -> None:
        self.http = http
        self.detection: AdapterDetectionResult | None = None

    # --- Detection -----------------------------------------------------

    @abstractmethod
    def detect(self, url: str) -> AdapterDetectionResult:
        """Inspect *url* and decide whether this adapter can handle it."""

    # --- Pagination ----------------------------------------------------

    @abstractmethod
    def iter_pages(
        self,
        *,
        feed_url: str,
        label: str | None = None,
        max_posts: int | None = None,
    ) -> Iterator[FeedPage]:
        """Yield consecutive :class:`FeedPage` objects until the feed ends."""

    # --- Labels / tags -------------------------------------------------

    @abstractmethod
    def fetch_labels(self, base_url: str) -> list[str]:
        """Return the available labels / tags for the blog at *base_url*."""

    # --- Per-post fetch ------------------------------------------------

    def fetch_post_html(self, post_url: str) -> BeautifulSoup | None:
        """Return parsed HTML of the post page."""
        response = self.http.get(post_url)
        if not response.ok:
            return None
        response.encoding = response.encoding or response.apparent_encoding
        return BeautifulSoup(response.text, "lxml")

    def extract_content(self, soup: BeautifulSoup) -> Tag | None:
        """Extract the main post body from a fetched post page."""
        element = extract_main_content(soup)
        if element is None:
            return None
        return strip_noise(element)
