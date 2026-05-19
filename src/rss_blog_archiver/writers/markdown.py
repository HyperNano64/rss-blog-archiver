"""Convert post HTML to Markdown via the `markdownify` library."""

from __future__ import annotations

from pathlib import Path

from markdownify import markdownify

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)


class MarkdownWriter(BaseWriter):
    extension = ".md"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"
        md_body = markdownify(context.content_html, heading_style="ATX", bullets="-")
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
