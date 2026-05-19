"""Plain-text writer using `html2text` for nicer line wrapping."""

from __future__ import annotations

from pathlib import Path

import html2text

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = False
_h2t.body_width = 100


class TextWriter(BaseWriter):
    extension = ".txt"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"
        text = _h2t.handle(context.content_html)
        header = "\n".join(
            [
                post.title,
                "=" * min(len(post.title), 100),
                f"URL: {post.url}",
                f"Published: {post.published.isoformat()}",
                f"Author: {post.author}" if post.author else "",
                "",
                "",
            ]
        )
        target.write_text(header + text, encoding="utf-8")
        logger.debug("Wrote text: %s", target)
        return target
