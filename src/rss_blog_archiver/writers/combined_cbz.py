"""Combined CBZ writer: bundle every chapter's images into a single CBZ.

Use case: a manga / komik series translated chapter-by-chapter on a
Blogspot / WordPress site. Instead of one ``.cbz`` per chapter, this
writer concatenates every chapter's images into one large CBZ with a
continuous, zero-padded numbering scheme (``00001.jpg`` … ``00999.jpg``).

The output also contains:

- ``ComicInfo.xml`` at the archive root with *series-level* metadata
  (Title / Series, Writer = combined author, ``PageCount`` = total
  pages, plus a ``Notes`` field listing every chapter for traceability).
- One leading "chapter break" entry per chapter (a tiny PNG with the
  chapter title rasterised on top) is **NOT** added — many readers
  treat random-looking interleaved images badly, and the source posts
  may already contain a title page. We just preserve order.
"""

from __future__ import annotations

import zipfile
from dataclasses import dataclass
from pathlib import Path

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.utils import sanitize_filename

logger = get_logger(__name__)


_COMIC_INFO_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<ComicInfo xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
           xmlns:xsd="http://www.w3.org/2001/XMLSchema">
    <Title>{title}</Title>
    <Series>{series}</Series>
    <Writer>{writer}</Writer>
    <PageCount>{page_count}</PageCount>
    <Count>{chapter_count}</Count>
    <LanguageISO>id</LanguageISO>
    <Notes>{notes}</Notes>
</ComicInfo>
"""


_VALID_IMAGE_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
})


@dataclass(slots=True)
class CombinedComicChapter:
    """One chapter inside a combined CBZ archive.

    ``image_paths`` should be ordered as they appear in the source post
    (the scraper guarantees this by ordering its downloaded files with
    a numeric prefix).
    """

    post: Post
    image_paths: list[Path]
    chapter_number: int | None = None


class CombinedCbzWriter:
    """Pack every chapter's images into one ``.cbz`` archive.

    Unlike :class:`~rss_blog_archiver.writers.cbz_writer.CbzWriter`, this
    writer is invoked once at the end of a scrape — it needs all chapter
    image lists in memory to assign continuous page numbers.
    """

    extension = ".cbz"

    def write(
        self,
        *,
        title: str,
        author: str,
        chapters: list[CombinedComicChapter],
        output_dir: Path,
    ) -> Path:
        if not chapters:
            raise ValueError("No chapters to write")

        ordered = _sort_chapters(chapters)
        total_pages = sum(len(ch.image_paths) for ch in ordered)
        if total_pages == 0:
            raise ValueError("No images across all chapters; nothing to bundle")
        # Pad the page index wide enough for the *real* total — this keeps
        # the archive lexically sorted in every common reader.
        page_width = max(3, len(str(total_pages)))

        target = output_dir / f"{sanitize_filename(title)}{self.extension}"

        # Build a one-line chapter manifest used inside ComicInfo.xml so
        # readers that surface "Notes" let the user inspect which posts
        # contributed which pages.
        manifest_lines: list[str] = []
        page_cursor = 1
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for chapter in ordered:
                pages = [
                    p for p in chapter.image_paths
                    if p.exists() and p.suffix.lower() in _VALID_IMAGE_EXTS
                ]
                if not pages:
                    continue
                first = page_cursor
                for image_path in pages:
                    arcname = f"{page_cursor:0{page_width}d}{image_path.suffix.lower()}"
                    zf.write(image_path, arcname=arcname)
                    page_cursor += 1
                last = page_cursor - 1
                manifest_lines.append(
                    f"pages {first:0{page_width}d}-{last:0{page_width}d}: "
                    f"{chapter.post.title}"
                )

            comic_info = _COMIC_INFO_TEMPLATE.format(
                title=_xml_escape(title),
                series=_xml_escape(title),
                writer=_xml_escape(author),
                page_count=page_cursor - 1,
                chapter_count=len(ordered),
                notes=_xml_escape("\n".join(manifest_lines)),
            )
            zf.writestr("ComicInfo.xml", comic_info)

        logger.info(
            "Wrote combined CBZ %s (%d chapters, %d pages)",
            target, len(ordered), page_cursor - 1,
        )
        return target


def _sort_chapters(
    chapters: list[CombinedComicChapter],
) -> list[CombinedComicChapter]:
    """Same ordering rules as :class:`CombinedEpubWriter`."""
    def key(ch: CombinedComicChapter) -> tuple[int, int, float]:
        has_no_number = 0 if ch.chapter_number is not None else 1
        return (
            has_no_number,
            ch.chapter_number if ch.chapter_number is not None else 0,
            ch.post.published.timestamp() if ch.post.published else 0.0,
        )
    return sorted(chapters, key=key)


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
