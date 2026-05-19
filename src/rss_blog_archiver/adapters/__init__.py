"""CMS-specific adapters.

Each adapter knows how to detect the CMS, locate its feed, paginate through
posts, fetch labels/tags, and extract the main post body from rendered HTML.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.adapters.blogspot import BlogspotAdapter
from rss_blog_archiver.adapters.wordpress import WordPressAdapter

if TYPE_CHECKING:
    from rss_blog_archiver.http_client import HttpClient

__all__ = [
    "AdapterDetectionResult",
    "BaseAdapter",
    "BlogspotAdapter",
    "WordPressAdapter",
    "detect_adapter",
]


def detect_adapter(url: str, http: HttpClient) -> BaseAdapter:
    """Detect the CMS for *url* and return the appropriate adapter instance.

    Order matters: Blogspot first (it's more uniquely identifiable), then
    WordPress. Falls back to a WordPress adapter (which is more general) when
    detection is ambiguous.
    """
    for AdapterCls in (BlogspotAdapter, WordPressAdapter):
        adapter = AdapterCls(http)
        result = adapter.detect(url)
        if result.matched:
            adapter.detection = result
            return adapter
    # Default: WordPress-style RSS as the best fallback.
    fallback = WordPressAdapter(http)
    fallback.detection = AdapterDetectionResult(matched=False, confidence=0.0,
                                                feed_url=url, base_url=url)
    return fallback
