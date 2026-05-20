"""Sitemap-first adapter — uses ``/sitemap.xml`` as the primary index.

This adapter is a thin wrapper around
:func:`rss_blog_archiver.adapters.sitemap.discover_sitemap_urls`. It is
useful when:

- a blog hosts an unusual CMS without RSS / REST feeds,
- the RSS feed has been disabled by the site owner,
- the user explicitly wants the canonical "all crawlable URLs" view
  instead of the feed (some feeds are truncated to N most-recent posts).

Unlike :class:`BlogspotAdapter` / :class:`WordPressAdapter`, this
adapter knows *nothing* about post metadata — every :class:`Post` it
yields has only a URL, with placeholder title/published values until
the scraper fetches the page and the content strategy extracts real
metadata from the rendered HTML.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from urllib.parse import urlparse

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.adapters.sitemap import discover_sitemap_urls
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import FeedPage, Post

logger = get_logger(__name__)


class SitemapAdapter(BaseAdapter):
    """Use the site's sitemap as the post URL source."""

    name = "sitemap"

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------
    def detect(self, url: str) -> AdapterDetectionResult:
        """Probe common sitemap locations.

        Returns ``matched=True`` only if at least one sitemap is reachable.
        Confidence is low (0.4) so :func:`detect_adapter` still prefers
        Blogspot / WordPress when those match.
        """
        base = _site_root(url)
        urls = discover_sitemap_urls(base, http=self.http, max_urls=1)
        matched = bool(urls)
        return AdapterDetectionResult(
            matched=matched,
            confidence=0.4 if matched else 0.0,
            feed_url=base,
            base_url=base,
            notes=[f"sitemap probe returned {len(urls)} URL(s)"],
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
        """Yield a single :class:`FeedPage` containing every sitemap URL.

        Sitemaps are not paginated like RSS feeds, so we return everything
        in one shot. The caller (Scraper) is responsible for honoring
        ``max_posts`` via its own slicing.
        """
        if label is not None:
            logger.info(
                "SitemapAdapter ignores --label (sitemaps don't expose tags)",
            )
        base = _site_root(feed_url)
        urls = discover_sitemap_urls(base, http=self.http, max_urls=max_posts)
        if not urls:
            logger.warning("Sitemap discovery returned 0 URLs for %s", base)
            return

        # Build placeholder posts. The scraper will fetch each URL and the
        # content strategy will extract real titles/dates from the HTML.
        epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
        posts = [
            Post(
                title=_url_to_placeholder_title(u),
                url=u,
                published=epoch,
            )
            for u in urls
        ]
        yield FeedPage(posts=posts, next_cursor=None)

    # ------------------------------------------------------------------
    # Labels
    # ------------------------------------------------------------------
    def fetch_labels(self, base_url: str) -> list[str]:
        """Sitemaps don't expose tags/categories."""
        return []


def _site_root(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path  # accept ``example.com`` w/o scheme
    return f"{scheme}://{netloc}"


def _url_to_placeholder_title(url: str) -> str:
    """Turn ``https://blog.example.com/2024/03/my-post.html`` into ``my-post``.

    Good-enough placeholder; the real title is recovered when the post HTML
    is fetched and content-extracted.
    """
    path = urlparse(url).path.rstrip("/")
    if not path:
        return url
    slug = path.rsplit("/", 1)[-1]
    if slug.endswith(".html"):
        slug = slug[:-5]
    slug = slug.replace("-", " ").replace("_", " ").strip()
    return slug or url
