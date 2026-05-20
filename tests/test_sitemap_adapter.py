"""Tests for SitemapAdapter (Phase 3 PR #5).

Covers detection (matched only when sitemap is reachable), iter_pages
(returns one page with every URL found), fetch_labels (empty), and the
prefer_sitemap parameter on detect_adapter.
"""

from __future__ import annotations

import responses

from rss_blog_archiver.adapters import (
    BlogspotAdapter,
    SitemapAdapter,
    WordPressAdapter,
    detect_adapter,
)
from rss_blog_archiver.http_client import HttpClient

_SITEMAP_URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://blog.example.com/2024/01/one.html</loc></url>
  <url><loc>https://blog.example.com/2024/02/two.html</loc></url>
  <url><loc>https://blog.example.com/2024/03/three.html</loc></url>
</urlset>
"""


def _register_sitemap_routes(base: str = "https://blog.example.com") -> None:
    """Register the standard mock routes used by SitemapAdapter."""
    responses.add(responses.GET, f"{base}/sitemap.xml",
                  body=_SITEMAP_URLSET, content_type="application/xml")
    for path in (
        "/sitemap_index.xml", "/wp-sitemap.xml",
        "/sitemap-posts-1.xml", "/atom.xml",
    ):
        responses.add(responses.GET, f"{base}{path}", status=404)


def _register_all_sitemap_404(base: str = "https://blog.example.com") -> None:
    for path in (
        "/sitemap.xml", "/sitemap_index.xml", "/wp-sitemap.xml",
        "/sitemap-posts-1.xml", "/atom.xml",
    ):
        responses.add(responses.GET, f"{base}{path}", status=404)


class TestSitemapAdapter:
    @responses.activate
    def test_detect_matches_when_sitemap_reachable(self) -> None:
        _register_sitemap_routes()
        adapter = SitemapAdapter(HttpClient())
        result = adapter.detect("https://blog.example.com/")
        assert result.matched is True
        assert result.confidence == 0.4
        assert result.base_url == "https://blog.example.com"

    @responses.activate
    def test_detect_misses_when_no_sitemap(self) -> None:
        _register_all_sitemap_404()
        adapter = SitemapAdapter(HttpClient())
        result = adapter.detect("https://blog.example.com/")
        assert result.matched is False
        assert result.confidence == 0.0

    @responses.activate
    def test_iter_pages_yields_every_url(self) -> None:
        _register_sitemap_routes()
        adapter = SitemapAdapter(HttpClient())
        pages = list(adapter.iter_pages(feed_url="https://blog.example.com/"))
        assert len(pages) == 1
        posts = pages[0].posts
        assert {p.url for p in posts} == {
            "https://blog.example.com/2024/01/one.html",
            "https://blog.example.com/2024/02/two.html",
            "https://blog.example.com/2024/03/three.html",
        }
        # Placeholder titles derived from URL slugs.
        titles = {p.title for p in posts}
        assert "one" in titles
        assert "two" in titles
        assert "three" in titles

    @responses.activate
    def test_iter_pages_respects_max_posts(self) -> None:
        _register_sitemap_routes()
        adapter = SitemapAdapter(HttpClient())
        pages = list(adapter.iter_pages(
            feed_url="https://blog.example.com/", max_posts=2,
        ))
        posts = pages[0].posts
        assert len(posts) == 2

    @responses.activate
    def test_fetch_labels_returns_empty(self) -> None:
        _register_sitemap_routes()
        adapter = SitemapAdapter(HttpClient())
        assert adapter.fetch_labels("https://blog.example.com") == []

    @responses.activate
    def test_iter_pages_emits_nothing_when_no_sitemap(self) -> None:
        _register_all_sitemap_404()
        adapter = SitemapAdapter(HttpClient())
        assert list(adapter.iter_pages(feed_url="https://blog.example.com/")) == []


class TestDetectAdapterPreferSitemap:
    @responses.activate
    def test_prefer_sitemap_picks_sitemap_adapter_first(self) -> None:
        """When --prefer-sitemap is set, SitemapAdapter is picked even if WP would also match."""
        _register_sitemap_routes()

        adapter = detect_adapter(
            "https://blog.example.com/",
            HttpClient(),
            prefer_sitemap=True,
        )
        assert isinstance(adapter, SitemapAdapter)

    @responses.activate
    def test_prefer_sitemap_off_does_not_use_sitemap_when_wp_matches(self) -> None:
        """Without the flag, WordPress is tried first even if sitemap is reachable."""
        # Make Blogspot detect FAIL, WP detect SUCCEED.
        responses.add(
            responses.GET, "https://blog.example.com/",
            body=(
                "<html><head>"
                "<meta name='generator' content='WordPress 6.4'/>"
                "<link rel='https://api.w.org/' href='https://blog.example.com/wp-json/'/>"
                "</head><body>WP page</body></html>"
            ),
            content_type="text/html",
            status=200,
        )
        # Sitemap also reachable, but should NOT be picked.
        _register_sitemap_routes()

        adapter = detect_adapter(
            "https://blog.example.com/",
            HttpClient(),
            prefer_sitemap=False,
        )
        # Either Blogspot or WordPress; what matters is it's not SitemapAdapter.
        assert not isinstance(adapter, SitemapAdapter)
        assert isinstance(adapter, (BlogspotAdapter, WordPressAdapter))
