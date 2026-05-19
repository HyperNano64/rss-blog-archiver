"""Output writers: PDF, EPUB, Markdown, plain text."""

from rss_blog_archiver.writers.base import BaseWriter, WriterContext
from rss_blog_archiver.writers.epub_writer import EpubWriter
from rss_blog_archiver.writers.markdown import MarkdownWriter
from rss_blog_archiver.writers.pdf_writer import PdfWriter
from rss_blog_archiver.writers.text import TextWriter

__all__ = [
    "BaseWriter",
    "EpubWriter",
    "MarkdownWriter",
    "PdfWriter",
    "TextWriter",
    "WriterContext",
    "build_writer",
]


def build_writer(mode: str) -> BaseWriter:
    mode_upper = mode.upper()
    mapping: dict[str, type[BaseWriter]] = {
        "MD": MarkdownWriter,
        "TXT": TextWriter,
        "EPUB": EpubWriter,
        "PDF": PdfWriter,
    }
    if mode_upper not in mapping:
        raise ValueError(f"Unknown output mode {mode!r}; expected one of {sorted(mapping)}")
    return mapping[mode_upper]()
