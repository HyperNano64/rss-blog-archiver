"""Tests for the sitemap fallback adapter."""

from __future__ import annotations

import responses

from rss_blog_archiver.adapters.sitemap import discover_sitemap_urls
from rss_blog_archiver.http_client import HttpClient

_SITEMAP_INDEX = """<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap>
    <loc>https://blog.example.com/sitemap-posts-1.xml</loc>
  </sitemap>
</sitemapindex>
"""

_POSTS_SITEMAP = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://blog.example.com/2024/01/one.html</loc></url>
  <url><loc>https://blog.example.com/2024/02/two.html</loc></url>
</urlset>
"""


class TestDiscoverSitemapUrls:
    @responses.activate
    def test_walks_sitemap_index(self) -> None:
        responses.add(
            responses.GET, "https://blog.example.com/sitemap.xml",
            body=_SITEMAP_INDEX, content_type="application/xml",
        )
        responses.add(
            responses.GET, "https://blog.example.com/sitemap-posts-1.xml",
            body=_POSTS_SITEMAP, content_type="application/xml",
        )
        # Other candidate URLs return 404 — they're optional.
        for path in ("/sitemap_index.xml", "/wp-sitemap.xml", "/atom.xml"):
            responses.add(
                responses.GET, f"https://blog.example.com{path}",
                status=404,
            )

        urls = discover_sitemap_urls("https://blog.example.com", http=HttpClient())
        assert "https://blog.example.com/2024/01/one.html" in urls
        assert "https://blog.example.com/2024/02/two.html" in urls

    @responses.activate
    def test_returns_empty_on_all_404(self) -> None:
        for path in (
            "/sitemap.xml", "/sitemap_index.xml",
            "/wp-sitemap.xml", "/sitemap-posts-1.xml", "/atom.xml",
        ):
            responses.add(
                responses.GET, f"https://blog.example.com{path}",
                status=404,
            )
        urls = discover_sitemap_urls("https://blog.example.com", http=HttpClient())
        assert urls == []
