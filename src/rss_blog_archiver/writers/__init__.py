"""Output writers: PDF, EPUB, Markdown, plain text, CBZ, Comic-PDF, Combined-EPUB."""

from __future__ import annotations

from collections.abc import Callable

from rss_blog_archiver.writers.base import BaseWriter, WriterContext
from rss_blog_archiver.writers.cbz_writer import CbzWriter
from rss_blog_archiver.writers.combined_epub import CombinedChapter, CombinedEpubWriter
from rss_blog_archiver.writers.comic_pdf_writer import ComicPdfWriter
from rss_blog_archiver.writers.epub_writer import EpubWriter
from rss_blog_archiver.writers.markdown import MarkdownWriter
from rss_blog_archiver.writers.pdf_writer import PdfWriter
from rss_blog_archiver.writers.text import TextWriter

__all__ = [
    "BaseWriter",
    "CbzWriter",
    "CombinedChapter",
    "CombinedEpubWriter",
    "ComicPdfWriter",
    "EpubWriter",
    "MarkdownWriter",
    "PdfWriter",
    "TextWriter",
    "WriterContext",
    "build_writer",
    "build_writers",
]


# Per-post writers keyed by (content_mode, format). ``content_mode`` is one of
# ``default`` / ``novel`` / ``comic``; ``format`` is the user-facing string.
# Values are zero-arg callables so that PdfWriter can be parameterized with a
# backend choice without leaking that detail into the registry.
_WRITERS: dict[tuple[str, str], Callable[[], BaseWriter]] = {
    ("default", "MD"): MarkdownWriter,
    ("default", "TXT"): TextWriter,
    ("default", "EPUB"): EpubWriter,
    ("default", "PDF"): PdfWriter,
    ("novel", "MD"): MarkdownWriter,
    ("novel", "TXT"): TextWriter,
    ("novel", "EPUB"): EpubWriter,
    ("novel", "PDF"): PdfWriter,
    ("comic", "CBZ"): CbzWriter,
    ("comic", "PDF"): ComicPdfWriter,
}


def build_writer(mode: str, *, pdf_backend: str = "auto") -> BaseWriter:
    """Backward-compatible builder (Phase 0 signature).

    Maps the old ``--mode`` value (PDF/TXT/MD/EPUB) onto the default content
    mode. Kept so the CLI alias ``--mode`` continues to work.
    """
    fmt = mode.upper()
    key = ("default", fmt)
    if key not in _WRITERS:
        valid = sorted({k[1] for k in _WRITERS if k[0] == "default"})
        raise ValueError(f"Unknown output format {mode!r}; expected one of {valid}")
    return _instantiate(_WRITERS[key], fmt, pdf_backend=pdf_backend)


def build_writers(
    content: str,
    formats: list[str],
    *,
    pdf_backend: str = "auto",
) -> list[BaseWriter]:
    """Return a list of per-post writers for the given content + format set.

    ``content`` is one of ``default`` / ``novel`` / ``comic``.
    ``formats`` is a list of uppercase format strings (e.g. ``["CBZ", "PDF"]``).
    ``pdf_backend`` selects the PDF rendering engine for the default/novel
    PDF writer; valid values are ``auto``, ``weasyprint``, ``wkhtmltopdf``.

    Raises ``ValueError`` if any (content, format) pair is unsupported.
    """
    content_key = content.lower().strip()
    if content_key not in {"default", "novel", "comic"}:
        raise ValueError(
            f"Unknown content mode {content!r}; expected default | novel | comic"
        )
    if not formats:
        raise ValueError("At least one output format is required")

    writers: list[BaseWriter] = []
    seen: set[tuple[str, str]] = set()
    for fmt in formats:
        fmt_key = fmt.upper().strip()
        pair = (content_key, fmt_key)
        if pair in seen:
            continue
        seen.add(pair)
        if pair not in _WRITERS:
            valid = sorted({k[1] for k in _WRITERS if k[0] == content_key})
            raise ValueError(
                f"Format {fmt_key!r} is not supported for --content {content_key!r}; "
                f"valid formats: {valid}"
            )
        writers.append(_instantiate(_WRITERS[pair], fmt_key, pdf_backend=pdf_backend))
    return writers


def _instantiate(
    factory: Callable[[], BaseWriter], fmt: str, *, pdf_backend: str
) -> BaseWriter:
    """Build a writer instance, threading ``pdf_backend`` to PdfWriter only.

    The comic PDF writer uses Pillow internally and is unaffected by the
    PDF backend selection.
    """
    if factory is PdfWriter:
        return PdfWriter(backend=pdf_backend)
    return factory()
