"""WordPress adapter — REST API first, RSS fallback.

WordPress is detected via the standard ``<link rel="https://api.w.org/">``
header / link tag, the ``X-Powered-By`` / ``Link`` HTTP headers, the
``<meta name="generator">`` tag, and the presence of ``/wp-json/`` or
``/wp-content/`` references in the rendered page.

When the REST API is reachable we use ``/wp-json/wp/v2/posts`` because it
returns structured JSON with ``content.rendered`` HTML, categories, tags,
author info, and pagination headers (``X-WP-Total``, ``X-WP-TotalPages``).
Falls back to the classic RSS feed at ``/feed/`` (and ``?paged=N`` style
pagination) when REST is blocked or missing.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import feedparser
from bs4 import BeautifulSoup

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import FeedPage, Post
from rss_blog_archiver.utils import safe_parse_date

logger = get_logger(__name__)

_WP_GENERATOR_RE = re.compile(r"wordpress", re.IGNORECASE)
_REST_API_LINK_RE = re.compile(r"api\.w\.org", re.IGNORECASE)
_PER_PAGE_REST = 100  # WordPress REST API caps at 100.


class WordPressAdapter(BaseAdapter):
    """Adapter for WordPress sites (self-hosted or .com)."""

    name = "wordpress"

    def __init__(self, http) -> None:  # type: ignore[no-untyped-def]
        super().__init__(http)
        self._rest_root: str | None = None  # filled in by detect()

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def detect(self, url: str) -> AdapterDetectionResult:
        base = _to_base_url(url)
        notes: list[str] = []

        try:
            response = self.http.get(base)
        except Exception as exc:
            notes.append(f"failed to fetch: {exc}")
            return AdapterDetectionResult(False, 0.0, "", base, notes)

        if not response.ok:
            notes.append(f"HTTP {response.status_code}")
            return AdapterDetectionResult(False, 0.0, "", base, notes)

        html = response.text

        # Signal 1: <meta name="generator" content="WordPress ...">
        soup = BeautifulSoup(html, "lxml")
        meta = soup.find("meta", attrs={"name": "generator"})
        gen = (meta.get("content") if meta and meta.has_attr("content") else "") or ""
        meta_match = bool(_WP_GENERATOR_RE.search(gen))
        if meta_match:
            notes.append(f"<meta name=generator> matches: {gen}")

        # Signal 2: REST API discovery link / header.
        rest_link = soup.find("link", attrs={"rel": "https://api.w.org/"})
        link_header = response.headers.get("Link", "")
        rest_root: str | None = None
        if rest_link and rest_link.get("href"):
            rest_root = rest_link["href"]
            notes.append("found api.w.org link tag")
        elif _REST_API_LINK_RE.search(link_header):
            match = re.search(r"<([^>]+)>;\s*rel=\"https://api\.w\.org/\"", link_header)
            if match:
                rest_root = match.group(1)
                notes.append("found api.w.org in Link header")

        # Signal 3: wp-content / wp-json references in body.
        body_signal = ("wp-content/" in html) or ("/wp-json/" in html)
        if body_signal:
            notes.append("wp-content / wp-json references in body")

        if rest_root is None:
            # Try the conventional path.
            candidate = urljoin(base, "/wp-json/")
            try:
                head = self.http.head(candidate)
                if head.ok:
                    rest_root = candidate
                    notes.append("/wp-json/ responds")
            except Exception:
                pass

        confidence = 0.0
        if meta_match:
            confidence += 0.6
        if rest_root:
            confidence += 0.4
        if body_signal:
            confidence += 0.2
        confidence = min(confidence, 0.99)

        if confidence < 0.3:
            return AdapterDetectionResult(False, confidence, "", base, notes)

        self._rest_root = rest_root
        feed_url = rest_root or urljoin(base, "/feed/")
        return AdapterDetectionResult(
            matched=True, confidence=confidence, feed_url=feed_url, base_url=base, notes=notes
        )

    # ------------------------------------------------------------------
    # Pagination
    # ------------------------------------------------------------------
    def iter_pages(
        self,
        *,
        feed_url: str,
        label: str | None = None,
        max_posts: int | None = None,
    ) -> Iterator[FeedPage]:
        rest_root = self._rest_root or self._maybe_rest_root(feed_url)
        if rest_root:
            yield from self._iter_rest(rest_root, label=label, max_posts=max_posts)
            return
        yield from self._iter_rss(feed_url, label=label, max_posts=max_posts)

    def _maybe_rest_root(self, feed_url: str) -> str | None:
        # Heuristic: a /wp-json/... URL passed as feed_url.
        if "/wp-json/" in feed_url:
            return feed_url
        return None

    # --- REST path -----------------------------------------------------
    def _iter_rest(
        self,
        rest_root: str,
        *,
        label: str | None,
        max_posts: int | None,
    ) -> Iterator[FeedPage]:
        page = 1
        emitted = 0
        category_ids: list[int] = []
        if label:
            category_ids = self._resolve_category_ids(rest_root, label)
            if not category_ids:
                logger.warning("Label %r not found via REST; returning no posts", label)
                return

        while True:
            if max_posts is not None and emitted >= max_posts:
                return
            page_size = _PER_PAGE_REST
            if max_posts is not None:
                page_size = min(page_size, max_posts - emitted)

            params = {"per_page": str(page_size), "page": str(page), "_embed": "true"}
            if category_ids:
                params["categories"] = ",".join(map(str, category_ids))

            url = _add_query(urljoin(rest_root, "wp/v2/posts"), **params)
            logger.info("Fetching WordPress REST page %d", page)
            response = self.http.get(url)
            if response.status_code == 400 and page > 1:
                # Out of range — WP returns 400 once we exceed total pages.
                return
            if not response.ok:
                logger.warning("REST returned HTTP %d at %s", response.status_code, url)
                return
            try:
                data = response.json()
            except ValueError:
                return
            if not isinstance(data, list) or not data:
                return

            posts = [self._parse_rest_post(item) for item in data]
            posts = [p for p in posts if p is not None]
            yield FeedPage(posts=posts, next_cursor=page + 1)
            emitted += len(posts)

            total_pages_hdr = response.headers.get("X-WP-TotalPages")
            try:
                total_pages = int(total_pages_hdr) if total_pages_hdr else 0
            except ValueError:
                total_pages = 0
            if total_pages and page >= total_pages:
                return
            if len(data) < page_size:
                return
            page += 1

    def _parse_rest_post(self, item: dict[str, Any]) -> Post | None:
        if not isinstance(item, dict):
            return None
        link = item.get("link")
        if not link:
            return None

        title = ""
        title_obj = item.get("title")
        if isinstance(title_obj, dict):
            title = _strip_html(title_obj.get("rendered", "") or "")
        elif isinstance(title_obj, str):
            title = _strip_html(title_obj)
        title = title or "untitled"

        html = ""
        content = item.get("content")
        if isinstance(content, dict):
            html = content.get("rendered", "") or ""

        summary = ""
        excerpt = item.get("excerpt")
        if isinstance(excerpt, dict):
            summary = _strip_html(excerpt.get("rendered", "") or "")

        published = safe_parse_date(item.get("date_gmt") or item.get("date") or "")

        author_name = ""
        embedded = item.get("_embedded") or {}
        authors = embedded.get("author") if isinstance(embedded, dict) else None
        if isinstance(authors, list) and authors:
            author_name = authors[0].get("name", "") if isinstance(authors[0], dict) else ""

        labels: list[str] = []
        terms = embedded.get("wp:term") if isinstance(embedded, dict) else None
        if isinstance(terms, list):
            for term_group in terms:
                if not isinstance(term_group, list):
                    continue
                for term in term_group:
                    if isinstance(term, dict) and term.get("name"):
                        labels.append(term["name"])

        return Post(
            title=title,
            url=link,
            published=published,
            html=html,
            summary=summary,
            author=author_name,
            labels=labels,
            comments_feed=None,
            extras={"wp_post_id": item.get("id")},
        )

    def _resolve_category_ids(self, rest_root: str, label: str) -> list[int]:
        ids: list[int] = []
        for taxonomy in ("categories", "tags"):
            url = _add_query(urljoin(rest_root, f"wp/v2/{taxonomy}"), search=label, per_page="20")
            try:
                response = self.http.get(url)
                if not response.ok:
                    continue
                data = response.json()
            except ValueError:
                continue
            if isinstance(data, list):
                for item in data:
                    if isinstance(item, dict) and item.get("name", "").lower() == label.lower():
                        ids.append(int(item["id"]))
        return ids

    # --- RSS fallback --------------------------------------------------
    def _iter_rss(
        self,
        feed_url: str,
        *,
        label: str | None,
        max_posts: int | None,
    ) -> Iterator[FeedPage]:
        if label:
            feed_url = self._rss_label_url(feed_url, label)
        emitted = 0
        page = 1
        seen: set[str] = set()

        while True:
            if max_posts is not None and emitted >= max_posts:
                return
            page_url = feed_url if page == 1 else _wp_paged_url(feed_url, page)
            logger.info("Fetching WordPress RSS page %d", page)
            response = self.http.get(page_url)
            if not response.ok:
                return
            parsed = feedparser.parse(response.content)
            entries = parsed.entries or []
            if not entries:
                return
            posts: list[Post] = []
            for entry in entries:
                link = getattr(entry, "link", None)
                if not link or link in seen:
                    continue
                seen.add(link)
                posts.append(
                    Post(
                        title=getattr(entry, "title", "untitled") or "untitled",
                        url=link,
                        published=safe_parse_date(getattr(entry, "published", None)),
                        html=getattr(entry, "content", [{"value": ""}])[0]["value"]
                        if getattr(entry, "content", None) else getattr(entry, "summary", "") or "",
                        summary=getattr(entry, "summary", "") or "",
                        author=getattr(entry, "author", "") or "",
                        labels=[t.term for t in getattr(entry, "tags", []) if getattr(t, "term", None)],
                    )
                )
            if not posts:
                return
            yield FeedPage(posts=posts, next_cursor=page + 1)
            emitted += len(posts)
            page += 1

    def _rss_label_url(self, feed_url: str, label: str) -> str:
        base = feed_url
        if base.endswith("/feed/"):
            base = base[: -len("/feed/")]
        elif base.endswith("/feed"):
            base = base[: -len("/feed")]
        return f"{base}/tag/{quote(label, safe='')}/feed/"

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def fetch_labels(self, base_url: str) -> list[str]:
        rest_root = self._rest_root or urljoin(base_url, "/wp-json/")
        labels: set[str] = set()
        for taxonomy in ("categories", "tags"):
            page = 1
            while True:
                url = _add_query(
                    urljoin(rest_root, f"wp/v2/{taxonomy}"),
                    per_page=str(_PER_PAGE_REST),
                    page=str(page),
                )
                try:
                    response = self.http.get(url)
                    if not response.ok:
                        break
                    data = response.json()
                except ValueError:
                    break
                if not isinstance(data, list) or not data:
                    break
                for item in data:
                    if isinstance(item, dict) and item.get("name"):
                        labels.add(item["name"])
                if len(data) < _PER_PAGE_REST:
                    break
                page += 1
        return sorted(labels)


def _strip_html(html: str) -> str:
    """Cheap text stripper for short fields like title."""
    return re.sub(r"<[^>]+>", "", html or "").strip()


def _to_base_url(url: str) -> str:
    parsed = urlsplit(url)
    if not parsed.scheme:
        url = "https://" + url
        parsed = urlsplit(url)
    return urlunsplit((parsed.scheme, parsed.netloc, "/", "", ""))


def _add_query(url: str, **params: str) -> str:
    parsed = urlsplit(url)
    existing = [parsed.query] if parsed.query else []
    encoded = "&".join(f"{k}={quote(v, safe='')}" for k, v in params.items())
    existing.append(encoded)
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "&".join(existing), parsed.fragment))


def _wp_paged_url(feed_url: str, page: int) -> str:
    parsed = urlsplit(feed_url)
    path = parsed.path
    if path.endswith("/feed/") or path.endswith("/feed"):
        # Inject /page/N/ before /feed[/].
        base = path.rstrip("/")
        if base.endswith("/feed"):
            base = base[: -len("/feed")]
        path = f"{base}/page/{page}/feed/"
        return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment))
    # Fallback: append ?paged=
    return _add_query(feed_url, paged=str(page))
