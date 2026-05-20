"""Tests for the Markdown image alt-text preprocessor."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rss_blog_archiver.models import Post
from rss_blog_archiver.writers import MarkdownWriter, WriterContext
from rss_blog_archiver.writers.markdown import (
    _escape_brackets,
    _fix_image_alt_text,
    _humanize_image_url,
)


class TestFixImageAltText:
    def test_keeps_existing_alt(self) -> None:
        html = '<p><img src="a.jpg" alt="Cover art"></p>'
        out = _fix_image_alt_text(html)
        assert 'alt="Cover art"' in out

    def test_falls_back_to_title(self) -> None:
        html = '<p><img src="a.jpg" title="Sunset over Tokyo"></p>'
        out = _fix_image_alt_text(html)
        assert 'alt="Sunset over Tokyo"' in out

    def test_falls_back_to_figcaption(self) -> None:
        html = (
            "<figure>"
            '<img src="a.jpg">'
            "<figcaption>Map of the region</figcaption>"
            "</figure>"
        )
        out = _fix_image_alt_text(html)
        assert 'alt="Map of the region"' in out

    def test_figcaption_only_inside_same_figure(self) -> None:
        # An <img> NOT wrapped in <figure> must not steal a sibling caption.
        html = (
            '<img src="https://cdn.example.com/x/banner-hero.png">'
            "<figure>"
            '<img src="https://cdn.example.com/x/other.png">'
            "<figcaption>Caption B</figcaption>"
            "</figure>"
        )
        out = _fix_image_alt_text(html)
        # First img should NOT be tagged with Caption B; it gets filename alt.
        assert 'alt="banner hero"' in out
        # Second img inside <figure> should get Caption B.
        assert 'alt="Caption B"' in out

    def test_falls_back_to_filename(self) -> None:
        html = '<img src="https://i0.wp.com/example.com/2024/05/chapter-01-cover.jpg">'
        out = _fix_image_alt_text(html)
        assert 'alt="chapter cover"' in out or 'alt="chapter 01 cover"' in out

    def test_handles_data_src_for_lazy_loaded(self) -> None:
        html = '<img data-src="https://cdn.example.com/lazy/hero_banner.png">'
        out = _fix_image_alt_text(html)
        assert 'alt="hero banner"' in out

    def test_escapes_brackets_in_alt(self) -> None:
        html = '<img src="a.jpg" alt="See [chapter 1]">'
        out = _fix_image_alt_text(html)
        assert "\\[chapter 1\\]" in out

    def test_empty_html_passthrough(self) -> None:
        assert _fix_image_alt_text("") == ""
        assert _fix_image_alt_text("<p>no images</p>") == "<p>no images</p>"

    def test_humanize_image_url_corner_cases(self) -> None:
        assert _humanize_image_url("") == "image"
        assert _humanize_image_url("https://x.com/") == "image"
        assert _humanize_image_url("https://x.com/Chapter%2001%20-%20Cover.jpg")
        assert _humanize_image_url("ch_01.png") == "ch"

    def test_escape_brackets_helper(self) -> None:
        assert _escape_brackets("a [b] c") == "a \\[b\\] c"


class TestMarkdownWriterEndToEnd:
    def test_writes_meaningful_alt_into_markdown(self, tmp_path: Path) -> None:
        post = Post(
            title="My Chapter",
            url="https://example.blogspot.com/p.html",
            published=datetime(2024, 1, 1, tzinfo=timezone.utc),
            author="A",
        )
        html = (
            '<p>Intro.</p>'
            '<img src="https://cdn.example.com/ch1/page-001.jpg">'
            '<figure><img src="x.png"><figcaption>End scene</figcaption></figure>'
        )
        ctx = WriterContext(post=post, content_html=html, output_dir=tmp_path)
        out = MarkdownWriter().write(ctx)
        text = out.read_text(encoding="utf-8")
        # Filename-derived alt for the first <img>:
        assert "![page" in text or "![page 001](" in text
        # Figcaption-derived alt for the figure-wrapped <img>:
        assert "![End scene](" in text
