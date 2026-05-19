"""EPUB writer with proper UUID identifier, language detection, and mime
type per image."""

from __future__ import annotations

import mimetypes
import uuid
from pathlib import Path

from ebooklib import epub

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)


class EpubWriter(BaseWriter):
    extension = ".epub"
    default_language = "id"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"

        book = epub.EpubBook()
        book.set_identifier(f"urn:uuid:{uuid.uuid4()}")
        book.set_title(post.title)
        book.set_language(self.default_language)
        if post.author:
            book.add_author(post.author)

        chapter = epub.EpubHtml(
            title=post.title,
            file_name="chap.xhtml",
            lang=self.default_language,
        )

        # Convert any local image references to EPUB-relative paths, embed the
        # image bytes as manifest items with the correct mime type.
        body_html = context.content_html
        if context.images_dir and context.images_dir.exists():
            body_html = self._embed_images(book, body_html, context.images_dir)

        chapter.content = (
            f"<?xml version='1.0' encoding='utf-8'?>"
            f"<!DOCTYPE html>"
            f"<html xmlns='http://www.w3.org/1999/xhtml'><head>"
            f"<meta charset='utf-8'/><title>{post.title}</title>"
            f"</head><body><h1>{post.title}</h1>{body_html}</body></html>"
        )

        book.add_item(chapter)
        book.toc = (epub.Link(chapter.file_name, post.title, "chap_1"),)
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())
        book.spine = ["nav", chapter]

        try:
            epub.write_epub(str(target), book)
        except Exception as exc:
            logger.warning("Failed to write EPUB %s: %s", target, exc)
            raise
        logger.debug("Wrote EPUB: %s", target)
        return target

    def _embed_images(self, book: epub.EpubBook, html: str, images_dir: Path) -> str:
        idx = 0
        for image_path in sorted(images_dir.iterdir()):
            if not image_path.is_file():
                continue
            mime, _ = mimetypes.guess_type(str(image_path))
            if not mime or not mime.startswith("image/"):
                continue
            file_name = f"images/{image_path.name}"
            with image_path.open("rb") as fh:
                data = fh.read()
            item = epub.EpubItem(
                uid=f"img_{idx}",
                file_name=file_name,
                media_type=mime,
                content=data,
            )
            book.add_item(item)
            # Rewrite any reference of just the filename to its epub-relative
            # path so the chapter HTML resolves the image correctly.
            html = html.replace(f'src="{image_path.name}"', f'src="{file_name}"')
            html = html.replace(f"src='{image_path.name}'", f"src='{file_name}'")
            idx += 1
        return html
