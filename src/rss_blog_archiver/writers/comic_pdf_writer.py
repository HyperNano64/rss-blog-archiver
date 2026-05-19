"""Cross-platform comic PDF writer (one image per page) using Pillow.

Pillow's ``Image.save(..., save_all=True, append_images=...)`` produces a
PDF without requiring `wkhtmltopdf` or any external binary, which keeps
this path portable on Windows / Linux / macOS.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext
from rss_blog_archiver.writers.cbz_writer import _ordered_image_files

logger = get_logger(__name__)


class ComicPdfWriter(BaseWriter):
    """Pack downloaded images into a multi-page PDF (one image per page)."""

    extension = ".pdf"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"

        if not context.images_dir or not context.images_dir.exists():
            raise RuntimeError(
                f"Comic PDF writer requires downloaded images, but none found "
                f"for {post.url!r}"
            )

        images = _ordered_image_files(context.images_dir)
        if not images:
            raise RuntimeError(
                f"Comic PDF writer found no usable images in {context.images_dir}"
            )

        opened: list[Image.Image] = []
        try:
            for path in images:
                img = Image.open(path)
                # PDFs only accept RGB / L modes; convert anything else.
                if img.mode in ("RGBA", "P", "LA"):
                    img = img.convert("RGB")
                opened.append(img)

            first, rest = opened[0], opened[1:]
            first.save(
                target,
                format="PDF",
                save_all=True,
                append_images=rest,
                resolution=144.0,
                title=post.title,
                author=post.author or "",
            )
        finally:
            for img in opened:
                img.close()

        logger.info("Wrote comic PDF %s (%d pages)", target, len(images))
        return target
