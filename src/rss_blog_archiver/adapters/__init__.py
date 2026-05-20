"""CMS-specific adapters.

Each adapter knows how to detect the CMS, locate its feed, paginate through
posts, fetch labels/tags, and extract the main post body from rendered HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.adapters.blogspot import BlogspotAdapter
from rss_blog_archiver.adapters.sitemap_adapter import SitemapAdapter
from rss_blog_archiver.adapters.wordpress import WordPressAdapter

if TYPE_CHECKING:
    from rss_blog_archiver.http_client import HttpClient

__all__ = [
    "AdapterDetectionResult",
    "BaseAdapter",
    "BlogspotAdapter",
    "SitemapAdapter",
    "WordPressAdapter",
    "detect_adapter",
]


def detect_adapter(
    url: str,
    http: HttpClient,
    *,
    prefer_sitemap: bool = False,
) -> BaseAdapter:
    """Detect the CMS for *url* and return the appropriate adapter instance.

    Order matters: by default Blogspot first (more uniquely identifiable),
    then WordPress, then SitemapAdapter as a structural fallback. Falls
    back to a WordPress adapter (which is the most general RSS handler)
    when nothing matches.

    When ``prefer_sitemap=True`` the SitemapAdapter is tried *first*; this
    is useful for blogs whose feed is empty / truncated / disabled but
    whose sitemap is intact.
    """
    if prefer_sitemap:
        order: tuple[type[BaseAdapter], ...] = (
            SitemapAdapter, BlogspotAdapter, WordPressAdapter,
        )
    else:
        order = (BlogspotAdapter, WordPressAdapter, SitemapAdapter)

    for AdapterCls in order:
        adapter = AdapterCls(http)
        result = adapter.detect(url)
        if result.matched:
            adapter.detection = result
            return adapter

    # Default: WordPress-style RSS as the best fallback. (Sitemap detection
    # already ran in the loop above; if it didn't match, the URL is unlikely
    # to have a usable structure anyway.)
    fallback = WordPressAdapter(http)
    fallback.detection = AdapterDetectionResult(matched=False, confidence=0.0,
                                                feed_url=url, base_url=url)
    return fallback
