"""Sitemap-based fallback URL discovery.

Used when the primary adapter's feed / REST API is empty, blocked, or
incomplete. Reads ``/sitemap.xml`` (or ``/wp-sitemap.xml`` for WP), follows
sitemap-index files, and returns a flat list of post URLs.

We deliberately keep this lightweight: we only return URLs (no titles /
dates) and let the caller plug them into its existing per-post fetch path.
"""

from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from bs4 import BeautifulSoup

from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger

logger = get_logger(__name__)

# Common sitemap locations to probe, in priority order.
_SITEMAP_CANDIDATES: tuple[str, ...] = (
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/wp-sitemap.xml",
    "/sitemap-posts-1.xml",
    "/atom.xml",
)


def discover_sitemap_urls(
    base_url: str,
    *,
    http: HttpClient,
    max_urls: int | None = None,
) -> list[str]:
    """Return all post URLs found by walking the site's sitemap(s).

    *base_url* should be the site root (e.g. ``https://example.com``).
    Trailing path / query / fragment are stripped automatically.
    """
    root = _site_root(base_url)
    seen: set[str] = set()
    out: list[str] = []
    queue: list[str] = [root + path for path in _SITEMAP_CANDIDATES]

    while queue:
        sitemap_url = queue.pop(0)
        if sitemap_url in seen:
            continue
        seen.add(sitemap_url)
        body = _fetch(sitemap_url, http=http)
        if not body:
            continue
        soup = BeautifulSoup(body, "xml")
        # Sitemap index file: links to other sitemaps via <sitemap><loc>.
        nested = [loc.get_text(strip=True) for loc in soup.select("sitemap > loc")]
        if nested:
            queue.extend(nested)
            continue
        # Regular sitemap: <url><loc>.
        for loc in soup.select("url > loc"):
            url = loc.get_text(strip=True)
            if url and url not in out:
                out.append(url)
                if max_urls is not None and len(out) >= max_urls:
                    return out
    logger.info("Sitemap discovery found %d URLs under %s", len(out), root)
    return out


def _fetch(url: str, *, http: HttpClient) -> str | None:
    try:
        response = http.get(url)
    except Exception as exc:
        logger.debug("Sitemap fetch failed for %s: %s", url, exc)
        return None
    if not response.ok:
        return None
    content_type = response.headers.get("Content-Type", "")
    # Some servers return 200 + an HTML 404 page; reject if it doesn't look
    # like XML.
    if "xml" not in content_type.lower() and "<urlset" not in response.text \
            and "<sitemapindex" not in response.text:
        return None
    return response.text


def _site_root(url: str) -> str:
    parsed = urlparse(url)
    scheme = parsed.scheme or "https"
    netloc = parsed.netloc or parsed.path  # handle "example.com" without scheme
    return urlunparse((scheme, netloc, "", "", "", ""))
