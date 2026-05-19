"""Blogspot (Blogger) adapter — deep exploitation of the GData feeds API.

Blogger exposes a rich, undocumented-but-extremely-stable GData feeds API
under the path ``/feeds/posts/default``. We use this as the primary data
source because it gives:

- the full HTML content of every post (no need to re-fetch the rendered page
  for the body),
- structured author / category / comments-feed information,
- pagination via ``start-index`` and ``max-results`` (capped at 500),
- per-label filtering via ``/feeds/posts/default/-/{label}``,
- static pages via ``/feeds/pages/default``,
- comments via ``/feeds/comments/default``,
- JSON output via ``?alt=json`` (avoids XML parsing entirely).

Detection works on both ``*.blogspot.com`` and **custom domains** that point
at Blogger by:
1. checking the ``<meta name="generator">`` tag of the root HTML page, and
2. verifying that ``/feeds/posts/default`` returns an Atom XML response.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import FeedPage, Post
from rss_blog_archiver.utils import is_blogspot_host, safe_parse_date

logger = get_logger(__name__)

_MAX_RESULTS_PER_PAGE = 500
_GENERATOR_RE = re.compile(r"blogger", re.IGNORECASE)


class BlogspotAdapter(BaseAdapter):
    """Adapter for Blogger / Blogspot blogs (including custom domains)."""

    name = "blogspot"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def detect(self, url: str) -> AdapterDetectionResult:
        base = _to_base_url(url)
        notes: list[str] = []

        # Fast path: the URL is already on the blogspot.com domain.
        if is_blogspot_host(url):
            notes.append("host matches blogspot.com")
            feed_url = urljoin(base, "/feeds/posts/default")
            return AdapterDetectionResult(
                matched=True, confidence=0.99, feed_url=feed_url, base_url=base, notes=notes
            )

        # Custom domain: inspect HTML for the Blogger generator meta tag
        # and verify the feed endpoint responds with XML.
        try:
            response = self.http.get(base)
        except Exception as exc:
            notes.append(f"failed to fetch base url: {exc}")
            return AdapterDetectionResult(False, 0.0, "", base, notes)

        if response.ok:
            soup = BeautifulSoup(response.text, "lxml")
            meta = soup.find("meta", attrs={"name": "generator"})
            content = (meta.get("content") if meta and meta.has_attr("content") else "") or ""
            if _GENERATOR_RE.search(content):
                notes.append(f"<meta name=generator> matches: {content}")
                feed_url = urljoin(base, "/feeds/posts/default")
                if self._feed_responds(feed_url):
                    return AdapterDetectionResult(
                        matched=True,
                        confidence=0.95,
                        feed_url=feed_url,
                        base_url=base,
                        notes=notes,
                    )

        # Last resort: just probe /feeds/posts/default directly.
        feed_url = urljoin(base, "/feeds/posts/default")
        if self._feed_responds(feed_url):
            notes.append("feed endpoint responded with XML")
            return AdapterDetectionResult(
                matched=True, confidence=0.7, feed_url=feed_url, base_url=base, notes=notes
            )

        return AdapterDetectionResult(False, 0.0, "", base, notes)

    def _feed_responds(self, feed_url: str) -> bool:
        try:
            response = self.http.head(feed_url)
        except Exception:
            return False
        if not response.ok:
            return False
        content_type = response.headers.get("Content-Type", "").lower()
        return "xml" in content_type or "atom" in content_type

    # ------------------------------------------------------------------
    # Pagination (JSON GData feed)
    # ------------------------------------------------------------------
    def iter_pages(
        self,
        *,
        feed_url: str,
        label: str | None = None,
        max_posts: int | None = None,
    ) -> Iterator[FeedPage]:
        # Optionally narrow to a label.
        base_feed = feed_url
        if label:
            base_feed = self._label_feed_url(feed_url, label)

        emitted = 0
        start_index = 1
        seen_urls: set[str] = set()

        while True:
            if max_posts is not None and emitted >= max_posts:
                return

            page_size = _MAX_RESULTS_PER_PAGE
            if max_posts is not None:
                page_size = min(page_size, max_posts - emitted)

            url = _add_query(
                base_feed,
                alt="json",
                v="2",
                **{"start-index": str(start_index), "max-results": str(page_size)},
            )
            logger.info("Fetching Blogspot JSON feed page start=%d (label=%s)", start_index, label)
            response = self.http.get(url)
            if not response.ok:
                logger.warning("Feed returned HTTP %d at %s", response.status_code, url)
                return

            try:
                payload: dict[str, Any] = response.json()
            except ValueError as exc:
                logger.warning("Failed to parse JSON feed: %s", exc)
                return

            posts = list(_parse_json_entries(payload))
            if not posts:
                return

            # Defensive cap: even if the server returns more entries than we
            # asked for, we never emit more than `max_posts` total.
            if max_posts is not None:
                remaining = max_posts - emitted
                if remaining <= 0:
                    return
                posts = posts[:remaining]

            # Drop dupes that span pages (defensive: some blogs return overlap).
            unique: list[Post] = []
            for post in posts:
                if post.url in seen_urls:
                    continue
                seen_urls.add(post.url)
                unique.append(post)

            yield FeedPage(posts=unique, next_cursor=start_index + len(posts))
            emitted += len(unique)

            if len(posts) < page_size:
                return
            start_index += len(posts)

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def fetch_labels(self, base_url: str) -> list[str]:
        """Return labels found in the JSON feed metadata."""
        url = _add_query(urljoin(base_url, "/feeds/posts/default"), alt="json", **{"max-results": "0"})
        try:
            response = self.http.get(url)
            payload = response.json() if response.ok else {}
        except (ValueError, Exception) as exc:
            logger.warning("Failed to fetch label list: %s", exc)
            return []

        labels: set[str] = set()
        for category in payload.get("feed", {}).get("category", []) or []:
            term = category.get("term") if isinstance(category, dict) else None
            if term:
                labels.add(term)
        return sorted(labels)

    # ------------------------------------------------------------------
    # Optional: comments + static pages
    # ------------------------------------------------------------------
    def fetch_pages(self, base_url: str, *, max_pages: int | None = None) -> Iterator[Post]:
        """Yield static Blogger pages (``/feeds/pages/default``)."""
        feed = urljoin(base_url, "/feeds/pages/default")
        for page in self._iter_generic_feed(feed, max_posts=max_pages):
            yield from page.posts

    def fetch_comments(self, base_url: str, *, max_comments: int | None = None) -> Iterator[Post]:
        """Yield blog-wide comments (``/feeds/comments/default``)."""
        feed = urljoin(base_url, "/feeds/comments/default")
        for page in self._iter_generic_feed(feed, max_posts=max_comments):
            yield from page.posts

    def _iter_generic_feed(self, feed_url: str, *, max_posts: int | None) -> Iterator[FeedPage]:
        emitted = 0
        start_index = 1
        while True:
            if max_posts is not None and emitted >= max_posts:
                return
            page_size = _MAX_RESULTS_PER_PAGE
            if max_posts is not None:
                page_size = min(page_size, max_posts - emitted)
            url = _add_query(
                feed_url,
                alt="json",
                v="2",
                **{"start-index": str(start_index), "max-results": str(page_size)},
            )
            response = self.http.get(url)
            if not response.ok:
                return
            try:
                payload = response.json()
            except ValueError:
                return
            posts = list(_parse_json_entries(payload))
            if not posts:
                return
            yield FeedPage(posts=posts, next_cursor=start_index + len(posts))
            emitted += len(posts)
            if len(posts) < page_size:
                return
            start_index += len(posts)

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------
    def _label_feed_url(self, feed_url: str, label: str) -> str:
        """Build a label-filtered feed URL.

        Blogger label feeds live at ``/feeds/posts/default/-/{label}`` for
        BOTH ``*.blogspot.com`` and custom domains. The original script had
        a buggy fork for custom domains — we use the unified format.
        """
        encoded = quote(label, safe="")
        parsed = urlsplit(feed_url)
        path = parsed.path
        # Strip any existing /-/label suffix.
        path = re.sub(r"/-/.+$", "", path)
        if not path.endswith("/feeds/posts/default"):
            path = path.rstrip("/") + "/feeds/posts/default"
        path = f"{path}/-/{encoded}"
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))


# ----------------------------------------------------------------------
# JSON feed entry parsing
# ----------------------------------------------------------------------
def _parse_json_entries(payload: dict[str, Any]) -> Iterator[Post]:
    feed = payload.get("feed") or {}
    for entry in feed.get("entry", []) or []:
        post = _parse_one_entry(entry)
        if post is not None:
            yield post


def _parse_one_entry(entry: dict[str, Any]) -> Post | None:
    if not isinstance(entry, dict):
        return None

    title = _gd_text(entry.get("title"))
    if not title:
        title = "untitled"

    # Alternate link is the canonical post URL.
    post_url: str | None = None
    comments_feed: str | None = None
    for link in entry.get("link", []) or []:
        rel = link.get("rel")
        href = link.get("href")
        if rel == "alternate" and href:
            post_url = href
        elif rel == "replies" and link.get("type") == "application/atom+xml" and href:
            comments_feed = href
    if not post_url:
        return None

    published = safe_parse_date(_gd_text(entry.get("published")))
    html = _gd_text(entry.get("content")) or _gd_text(entry.get("summary"))
    summary = _gd_text(entry.get("summary"))
    authors = []
    for author in entry.get("author", []) or []:
        name = _gd_text(author.get("name"))
        if name:
            authors.append(name)
    labels: list[str] = []
    for category in entry.get("category", []) or []:
        term = category.get("term") if isinstance(category, dict) else None
        if term:
            labels.append(term)

    return Post(
        title=title,
        url=post_url,
        published=published,
        html=html,
        summary=summary,
        author=", ".join(authors),
        labels=labels,
        comments_feed=comments_feed,
        extras={"blogger_entry_id": _gd_text(entry.get("id"))},
    )


def _gd_text(value: Any) -> str:
    """Unwrap Blogger's ``{"$t": "..."}`` GData wrapper into a plain string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("$t", "") or "")
    return str(value)


# ----------------------------------------------------------------------
# URL utilities
# ----------------------------------------------------------------------
def _to_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _add_query(url: str, **params: str) -> str:
    parsed = urlsplit(url)
    existing: list[str] = [parsed.query] if parsed.query else []
    encoded = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    existing.append(encoded)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(existing), parsed.fragment))
