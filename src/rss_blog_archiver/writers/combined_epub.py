"""Combined EPUB writer: bundle many posts into a single .epub book.

Use case: serial fiction / novels translated chapter-by-chapter on a
Blogspot/WordPress site. Instead of one ``.epub`` per blog post, we build a
single book whose chapters are ordered by detected chapter number
(falling back to publication date).
"""

from __future__ import annotations

import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path

from ebooklib import epub

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.utils import sanitize_filename

logger = get_logger(__name__)


@dataclass(slots=True)
class CombinedChapter:
    """One chapter inside a combined EPUB book."""

    post: Post
    html: str
    image_paths: list[Path]
    chapter_number: int | None = None


class CombinedEpubWriter:
    """Build one EPUB book from a list of chapters.

    Unlike :class:`BaseWriter`, this writer is invoked once at the end of a
    scrape rather than per-post — it needs all chapters in memory to build
    the spine + TOC.
    """

    extension = ".epub"

    def __init__(self, *, language: str = "id") -> None:
        self.language = language

    def write(
        self,
        *,
        title: str,
        author: str,
        chapters: list[CombinedChapter],
        output_dir: Path,
    ) -> Path:
        if not chapters:
            raise ValueError("No chapters to write")

        ordered = _sort_chapters(chapters)

        book = epub.EpubBook()
        book.set_identifier(str(uuid.uuid4()))
        book.set_title(title)
        book.set_language(self.language)
        if author:
            book.add_author(author)

        spine: list[object] = ["nav"]
        toc: list[epub.Link] = []

        for index, chapter in enumerate(ordered, start=1):
            chapter_title = chapter.post.title or f"Chapter {index}"
            file_name = f"chap_{index:04d}.xhtml"
            content = _wrap_chapter_html(chapter_title, chapter.html)

            chapter_item = epub.EpubHtml(
                title=chapter_title, file_name=file_name, lang=self.language
            )
            chapter_item.content = content
            book.add_item(chapter_item)
            spine.append(chapter_item)
            toc.append(epub.Link(file_name, chapter_title, f"chap_{index:04d}"))

            for image_path in chapter.image_paths:
                if not image_path.exists():
                    continue
                mime, _ = mimetypes.guess_type(image_path.name)
                with image_path.open("rb") as fh:
                    book.add_item(
                        epub.EpubItem(
                            uid=f"img_{image_path.stem}",
                            file_name=f"images/{image_path.name}",
                            media_type=mime or "image/jpeg",
                            content=fh.read(),
                        )
                    )

        book.toc = tuple(toc)
        book.spine = spine
        book.add_item(epub.EpubNcx())
        book.add_item(epub.EpubNav())

        target = output_dir / f"{sanitize_filename(title)}{self.extension}"
        epub.write_epub(target, book)
        logger.info("Wrote combined EPUB %s (%d chapters)", target, len(ordered))
        return target


def _sort_chapters(chapters: list[CombinedChapter]) -> list[CombinedChapter]:
    """Sort by detected chapter number (asc), with publication date as tiebreaker."""
    def key(ch: CombinedChapter) -> tuple[int, int, float]:
        # Use a 3-tuple so "no chapter number" entries sort consistently AFTER
        # numbered chapters when chapter numbers exist for some entries.
        has_no_number = 0 if ch.chapter_number is not None else 1
        return (
            has_no_number,
            ch.chapter_number if ch.chapter_number is not None else 0,
            ch.post.published.timestamp() if ch.post.published else 0.0,
        )
    return sorted(chapters, key=key)


def _wrap_chapter_html(title: str, body_html: str) -> str:
    safe_title = (
        title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
    # No leading `<?xml ... ?>`: lxml errors on unicode strings carrying an
    # encoding declaration, which propagates into ebooklib's nav step.
    return (
        "<!DOCTYPE html>"
        "<html xmlns='http://www.w3.org/1999/xhtml'><head>"
        f"<meta charset='utf-8'/><title>{safe_title}</title>"
        f"</head><body><h1>{safe_title}</h1>{body_html}</body></html>"
    )
