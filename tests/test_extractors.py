"""Tests for the content/image extractors."""

from __future__ import annotations

from bs4 import BeautifulSoup

from rss_blog_archiver.extractors.content import extract_main_content, strip_noise
from rss_blog_archiver.extractors.images import _best_image_url, discover_image_urls


def _make_soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "lxml")


class TestExtractMainContent:
    def test_prefers_post_body(self) -> None:
        soup = _make_soup(
            '<html><body>'
            '<div class="post-body">main</div>'
            '<div class="entry-content">other</div>'
            '</body></html>'
        )
        element = extract_main_content(soup)
        assert element is not None
        assert "main" in element.get_text()

    def test_falls_back_to_entry_content(self) -> None:
        soup = _make_soup(
            '<html><body><article><div class="entry-content">wp content</div></article></body></html>'
        )
        element = extract_main_content(soup)
        assert element is not None
        assert "wp content" in element.get_text()

    def test_returns_body_when_no_class_match(self) -> None:
        soup = _make_soup("<html><body><p>plain</p></body></html>")
        element = extract_main_content(soup)
        assert element is not None
        assert "plain" in element.get_text()


class TestStripNoise:
    def test_removes_script_and_share(self) -> None:
        soup = _make_soup(
            '<div class="post-body">'
            '<p>keep</p>'
            '<script>alert(1)</script>'
            '<div class="post-share-buttons">share</div>'
            '<div class="addthis_inline_share_toolbox">x</div>'
            '</div>'
        )
        content = extract_main_content(soup)
        assert content is not None
        strip_noise(content)
        text = content.get_text()
        assert "keep" in text
        assert "alert" not in text
        assert "share" not in text


class TestBestImageUrl:
    def test_uses_data_src_over_src(self) -> None:
        img = _make_soup('<img src="placeholder.gif" data-src="real.jpg">').find("img")
        assert img is not None
        assert _best_image_url(img) == "real.jpg"

    def test_parses_srcset_widest(self) -> None:
        html = '<img src="default.jpg" srcset="small.jpg 480w, big.jpg 1600w, mid.jpg 800w">'
        img = _make_soup(html).find("img")
        assert img is not None
        # data-src/lazy fallbacks are absent; srcset wins.
        assert _best_image_url(img) == "big.jpg"

    def test_falls_back_to_src(self) -> None:
        img = _make_soup('<img src="only.png">').find("img")
        assert img is not None
        assert _best_image_url(img) == "only.png"


class TestDiscoverImageUrls:
    def test_resolves_relative_and_dedupes(self) -> None:
        soup = _make_soup(
            '<div>'
            '<img src="/a/foo.jpg">'
            '<img data-src="/a/foo.jpg">'
            '<img src="https://cdn.example.com/bar.png">'
            '<img src="data:image/png;base64,XXXX">'
            '</div>'
        )
        urls = discover_image_urls(soup, base_url="https://blog.example.com/post-1")
        assert "https://blog.example.com/a/foo.jpg" in urls
        assert "https://cdn.example.com/bar.png" in urls
        # data: URIs are skipped, duplicates collapsed.
        assert len(urls) == 2
