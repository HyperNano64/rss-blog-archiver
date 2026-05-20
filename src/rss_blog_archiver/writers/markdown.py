"""Convert post HTML to Markdown via the `markdownify` library.

Includes an image alt-text preprocessor: ``markdownify`` happily emits
``![](url)`` for ``<img>`` tags without a usable ``alt`` attribute. That
breaks screen readers, downstream Markdown-to-EPUB/PDF pipelines, and
e-readers that show alt text as fallback when an image fails to load.

:func:`_fix_image_alt_text` walks the HTML and fills missing/empty
``alt`` attributes from (in order):

1. existing ``alt`` (when present and non-empty)
2. ``title`` attribute
3. nearest enclosing ``<figure>``'s ``<figcaption>`` text
4. a humanized version of the image filename in ``src``/``data-src``

It also escapes ``[`` / ``]`` characters inside alt text so that the
final Markdown ``![alt](url)`` syntax stays well-formed.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlparse

from bs4 import BeautifulSoup
from markdownify import markdownify

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)

_FILENAME_CLEAN_RE = re.compile(r"[_\-\s]+")
_TRAILING_NUM_RE = re.compile(r"\s+\d+$")


class MarkdownWriter(BaseWriter):
    extension = ".md"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"
        cleaned_html = _fix_image_alt_text(context.content_html)
        md_body = markdownify(cleaned_html, heading_style="ATX", bullets="-")
        front_matter = "\n".join(
            [
                "---",
                f"title: {post.title!r}",
                f"url: {post.url}",
                f"published: {post.published.isoformat()}",
                f"author: {post.author}" if post.author else "",
                f"labels: {post.labels}" if post.labels else "",
                "---",
                "",
            ]
        )
        target.write_text(front_matter + md_body, encoding="utf-8")
        logger.debug("Wrote Markdown: %s", target)
        return target


# ---------------------------------------------------------------------------
# Public helper (exposed for tests + reuse by other writers)
# ---------------------------------------------------------------------------
def _fix_image_alt_text(html: str) -> str:
    """Return ``html`` with all ``<img>`` tags carrying a meaningful ``alt``.

    The transformation is idempotent. Tags that already have a non-empty
    ``alt`` attribute are left untouched apart from ``[`` / ``]`` escaping.
    """
    if not html or "<img" not in html.lower():
        return html
    soup = BeautifulSoup(html, "html.parser")
    for img in soup.find_all("img"):
        alt = _derive_alt(img)
        img["alt"] = _escape_brackets(alt)
    return str(soup)


def _derive_alt(img) -> str:
    existing = (img.get("alt") or "").strip()
    if existing:
        return existing
    title = (img.get("title") or "").strip()
    if title:
        return title
    # Walk up to find enclosing <figure>, then its <figcaption>.
    figure = img.find_parent("figure")
    if figure:
        cap = figure.find("figcaption")
        if cap:
            text = cap.get_text(" ", strip=True)
            if text:
                return text
    # Fall back to humanized filename from the most useful src candidate.
    src = (
        img.get("src")
        or img.get("data-src")
        or img.get("data-lazy-src")
        or img.get("data-original")
        or ""
    )
    return _humanize_image_url(src)


def _humanize_image_url(url: str) -> str:
    if not url:
        return "image"
    parsed = urlparse(url)
    path = parsed.path or url
    name = PurePosixPath(unquote(path)).stem
    if not name:
        return "image"
    cleaned = _FILENAME_CLEAN_RE.sub(" ", name).strip()
    if not cleaned:
        return "image"
    cleaned = _TRAILING_NUM_RE.sub("", cleaned).strip() or cleaned
    return cleaned[:120]


def _escape_brackets(text: str) -> str:
    return text.replace("[", "\\[").replace("]", "\\]")
