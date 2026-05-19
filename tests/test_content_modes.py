"""Tests for content extraction strategies (default / novel / comic)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bs4 import BeautifulSoup

from rss_blog_archiver.content_modes import (
    ComicMode,
    DefaultMode,
    NovelMode,
    build_content_mode,
    detect_chapter_number,
)
from rss_blog_archiver.models import Post


def _make_post(title: str = "Chapter 1: Start", html: str = "") -> Post:
    return Post(
        title=title,
        url="https://example.blogspot.com/2024/01/post.html",
        published=datetime(2024, 1, 1, tzinfo=timezone.utc),
        html=html,
    )


class TestDetectChapterNumber:
    @pytest.mark.parametrize(
        "title,expected",
        [
            ("Chapter 5: Adventure", 5),
            ("ch. 12 - Side Story", 12),
            ("Bab 7", 7),
            ("Episode 3 — The Trip", 3),
            ("Jilid 2", 2),
            ("Story #42", 42),
            ("My random post", None),
            ("", None),
            ("Chapter NaN", None),
        ],
    )
    def test_various(self, title: str, expected: int | None) -> None:
        assert detect_chapter_number(title) == expected


class TestDefaultMode:
    def test_extracts_main_content_and_images(self) -> None:
        html = (
            '<div class="post-body">'
            '<p>Hello world</p>'
            '<img src="https://example.com/a.jpg">'
            '</div>'
        )
        post = _make_post(html=html)
        result = DefaultMode().extract(post, BeautifulSoup(html, "lxml"))
        assert "Hello world" in result.html
        assert "https://example.com/a.jpg" in result.image_urls


class TestNovelMode:
    def test_strips_nav_links(self) -> None:
        html = (
            '<div class="post-body">'
            '<p>Chapter intro</p>'
            '<p><a href="prev.html">Previous Chapter</a></p>'
            '<p><a href="next.html">Bab Selanjutnya</a></p>'
            '<p>Story body...</p>'
            '</div>'
        )
        post = _make_post("Chapter 3: Test", html=html)
        result = NovelMode().extract(post, BeautifulSoup(html, "lxml"))
        assert "Story body" in result.html
        assert "Previous Chapter" not in result.html
        assert "Selanjutnya" not in result.html
        assert result.chapter_number == 3

    def test_keeps_normal_anchor_text(self) -> None:
        html = (
            '<div class="post-body">'
            '<p>Visit <a href="https://example.com">our site</a> for more.</p>'
            '</div>'
        )
        post = _make_post(html=html)
        result = NovelMode().extract(post, BeautifulSoup(html, "lxml"))
        assert "our site" in result.html


class TestComicMode:
    def test_collects_image_urls_only(self) -> None:
        html = (
            '<div class="post-body">'
            '<p>Lots of text we should ignore</p>'
            '<img src="https://example.com/page1.jpg">'
            '<img src="https://example.com/page2.jpg">'
            '<img data-src="https://example.com/page3.jpg">'
            '</div>'
        )
        post = _make_post(html=html)
        result = ComicMode().extract(post, BeautifulSoup(html, "lxml"))
        assert result.html == ""
        assert len(result.image_urls) == 3
        assert result.image_urls[0].endswith("page1.jpg")
        assert result.image_urls[-1].endswith("page3.jpg")

    def test_preserves_image_order(self) -> None:
        html = (
            '<div class="post-body">'
            + "".join(
                f'<img src="https://example.com/p{i:02d}.jpg">' for i in range(1, 11)
            )
            + '</div>'
        )
        post = _make_post(html=html)
        result = ComicMode().extract(post, BeautifulSoup(html, "lxml"))
        assert [u.rsplit("/", 1)[-1] for u in result.image_urls] == [
            f"p{i:02d}.jpg" for i in range(1, 11)
        ]


class TestBuildContentMode:
    def test_returns_proper_subclasses(self) -> None:
        assert isinstance(build_content_mode("default"), DefaultMode)
        assert isinstance(build_content_mode("novel"), NovelMode)
        assert isinstance(build_content_mode("comic"), ComicMode)
        assert isinstance(build_content_mode("manga"), ComicMode)

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError):
            build_content_mode("podcast")
