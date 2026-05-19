"""CBZ writer — Comic Book ZIP for komik / manga output.

A CBZ file is just a ZIP archive of images named in display order
(``001.jpg``, ``002.jpg``, ...). Most comic readers (CDisplayEx, Simple
Comic, YACReader, ComicRack) recognize the extension and read images
sequentially. We also add a `ComicInfo.xml` metadata file inside the
archive (recognized by ComicRack/YACReader) so the reader can show title,
author, and series info.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)

_COMIC_INFO_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <Title>{title}</Title>
    <Series>{series}</Series>
    <Writer>{writer}</Writer>
    <Web>{url}</Web>
    <PageCount>{page_count}</PageCount>
    <LanguageISO>id</LanguageISO>
    <Notes>{notes}</Notes>
</ComicInfo>
"""


class CbzWriter(BaseWriter):
    """Pack downloaded images into a `.cbz` archive."""

    extension = ".cbz"

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"

        if not context.images_dir or not context.images_dir.exists():
            raise RuntimeError(
                f"CBZ writer requires downloaded images, but none found for {post.url!r}"
            )

        images = _ordered_image_files(context.images_dir)
        if not images:
            raise RuntimeError(
                f"CBZ writer found no usable images in {context.images_dir}"
            )

        comic_info = _COMIC_INFO_TEMPLATE.format(
            title=_xml_escape(post.title),
            series=_xml_escape(post.author or post.title),
            writer=_xml_escape(post.author),
            url=_xml_escape(post.url),
            page_count=len(images),
            notes=_xml_escape(f"Scraped by rss-blog-archiver from {post.url}"),
        )

        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for index, image_path in enumerate(images, start=1):
                arcname = f"{index:03d}{image_path.suffix.lower()}"
                zf.write(image_path, arcname=arcname)
            zf.writestr("ComicInfo.xml", comic_info)
        logger.info("Wrote CBZ %s (%d pages)", target, len(images))
        return target


def _ordered_image_files(images_dir: Path) -> list[Path]:
    """Return image files sorted so they form a reasonable reading order.

    Filenames produced by :func:`sanitize_url_to_filename` start with an
    8-char hash followed by ``_<basename>`` — sorting alphabetically is
    not guaranteed to be the order they appeared in the post. We rely on
    the caller (the comic-aware scraper) to store images in a way that
    sorting by name corresponds to document order. See the scraper's
    ``_process_comic_post`` for the prefix-based naming.
    """
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
    items = [
        path for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in valid_exts
    ]
    return sorted(items, key=lambda p: p.name)


def _xml_escape(value: str | None) -> str:
    if not value:
        return ""
    return (
        value.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&apos;")
    )
