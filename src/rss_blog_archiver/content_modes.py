"""Content extraction strategies (default, novel, comic).

A *content mode* tells the scraper how to interpret a post's body:

- ``default``: extract the main content block as-is (Phase 0 behavior).
- ``novel``: extract chapter text + inline images, strip serial-fiction
  navigation links (Previous Chapter / Next Chapter / Bab Sebelumnya).
- ``comic`` (or ``manga``): ignore text entirely; only collect ordered
  image URLs. Output writers will produce CBZ / PDF galleries.

The mode is orthogonal to the *output format* (PDF / EPUB / MD / TXT / CBZ).
A user can pair ``--content novel --format EPUB`` or ``--content comic
--format CBZ`` freely; writers know which combinations make sense and will
raise a clear error for nonsensical combinations.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import ClassVar

from bs4 import BeautifulSoup, Tag

from rss_blog_archiver.extractors.content import extract_main_content, strip_noise
from rss_blog_archiver.extractors.images import discover_image_urls
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post

logger = get_logger(__name__)


@dataclass(slots=True)
class ExtractedContent:
    """Result of running a :class:`ContentMode` over a post."""

    html: str = ""
    """Cleaned post HTML (empty for pure-image modes like ``comic``)."""

    image_urls: list[str] = field(default_factory=list)
    """Absolute image URLs in document order."""

    chapter_number: int | None = None
    """Detected chapter / episode number (novel mode), if any."""

    extras: dict[str, str] = field(default_factory=dict)


# Patterns used to detect chapter numbering in titles.
_CHAPTER_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bchapter\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bch\.?\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bep(?:isode)?\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bbab\s*(\d+)", re.IGNORECASE),
    re.compile(r"\bjilid\s*(\d+)", re.IGNORECASE),
    re.compile(r"#\s*(\d+)\b"),
)

# Anchor-text substrings that mean "next chapter" / "previous chapter".
# Indonesian + English. Matched case-insensitively.
_NAV_LINK_PHRASES: tuple[str, ...] = (
    "previous chapter", "next chapter", "previous page", "next page",
    "prev chapter", "prev ch", "next ch",
    "bab sebelumnya", "bab selanjutnya", "halaman sebelumnya",
    "halaman selanjutnya", "kembali ke daftar isi", "daftar isi",
    "chapter list", "table of contents",
    "back to toc", "next →", "← prev",
    "lanjut", "sebelumnya", "selanjutnya",
)


class ContentMode(ABC):
    """Abstract content extraction strategy."""

    name: ClassVar[str] = "base"

    @abstractmethod
    def extract(self, post: Post, soup: BeautifulSoup | None) -> ExtractedContent:
        """Apply the strategy and return :class:`ExtractedContent`."""


class DefaultMode(ContentMode):
    """Phase 0 default: extract main content + image URLs, no special cleanup."""

    name: ClassVar[str] = "default"

    def extract(self, post: Post, soup: BeautifulSoup | None) -> ExtractedContent:
        element = _pick_content_element(post, soup)
        if element is None:
            return ExtractedContent()
        strip_noise(element)
        image_urls = discover_image_urls(element, base_url=post.url)
        return ExtractedContent(html=_element_html(element), image_urls=image_urls)


class NovelMode(ContentMode):
    """Serial-fiction mode: clean text + inline images, strip nav links,
    detect chapter number from title."""

    name: ClassVar[str] = "novel"

    def extract(self, post: Post, soup: BeautifulSoup | None) -> ExtractedContent:
        element = _pick_content_element(post, soup)
        if element is None:
            return ExtractedContent(chapter_number=detect_chapter_number(post.title))
        strip_noise(element)
        _strip_chapter_nav_links(element)
        image_urls = discover_image_urls(element, base_url=post.url)
        return ExtractedContent(
            html=_element_html(element),
            image_urls=image_urls,
            chapter_number=detect_chapter_number(post.title),
        )


class ComicMode(ContentMode):
    """Image-only mode for komik / manga: collect ordered image URLs.

    Aliased to ``manga`` so both spellings work from the CLI.
    """

    name: ClassVar[str] = "comic"

    def extract(self, post: Post, soup: BeautifulSoup | None) -> ExtractedContent:
        element = _pick_content_element(post, soup)
        if element is None:
            return ExtractedContent(chapter_number=detect_chapter_number(post.title))
        # No text strip — we still want to traverse <img> tags inside scripts'
        # neighborhood, but we DO drop script/style to avoid duplicates.
        for tag_name in ("script", "style", "noscript"):
            for tag in element.select(tag_name):
                tag.decompose()
        image_urls = discover_image_urls(element, base_url=post.url)
        return ExtractedContent(
            html="",
            image_urls=image_urls,
            chapter_number=detect_chapter_number(post.title),
        )


_MODES: dict[str, type[ContentMode]] = {
    "default": DefaultMode,
    "novel": NovelMode,
    "comic": ComicMode,
    "manga": ComicMode,
}


def build_content_mode(name: str) -> ContentMode:
    """Return a content-mode strategy instance for the given short name."""
    key = name.lower().strip()
    if key not in _MODES:
        raise ValueError(
            f"Unknown content mode {name!r}; expected one of {sorted(set(_MODES))}"
        )
    return _MODES[key]()


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def detect_chapter_number(title: str) -> int | None:
    """Return the first chapter/episode/bab number found in *title*, or None."""
    if not title:
        return None
    for pattern in _CHAPTER_PATTERNS:
        match = pattern.search(title)
        if match:
            try:
                return int(match.group(1))
            except (ValueError, IndexError):
                continue
    return None


def _pick_content_element(post: Post, soup: BeautifulSoup | None) -> Tag | None:
    """Choose the working soup root, preferring feed-provided HTML."""
    if post.html:
        # Feed already gave us the body HTML. Wrap once.
        parsed = BeautifulSoup(post.html, "lxml")
        # If the wrapping <html>/<body> was added by lxml, descend into body.
        if parsed.body is not None:
            return parsed.body
        return parsed
    if soup is None:
        return None
    return extract_main_content(soup)


def _strip_chapter_nav_links(element: Tag) -> None:
    """Remove anchors whose visible text matches a nav phrase."""
    for anchor in list(element.find_all("a")):
        text = anchor.get_text(strip=True).lower()
        if not text:
            continue
        if any(phrase in text for phrase in _NAV_LINK_PHRASES):
            # Drop the surrounding paragraph if the anchor is its only child.
            parent = anchor.parent
            anchor.decompose()
            if parent and parent.name in ("p", "div", "center") and not parent.get_text(strip=True):
                parent.decompose()


def _element_html(element: Tag) -> str:
    """Return HTML inside *element* without the wrapping tag itself."""
    if hasattr(element, "encode_contents"):
        return element.encode_contents().decode("utf-8")
    return str(element)
