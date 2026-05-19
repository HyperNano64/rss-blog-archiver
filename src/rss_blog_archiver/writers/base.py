"""Writer interface + shared context dataclass."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from rss_blog_archiver.models import Post


@dataclass(slots=True)
class WriterContext:
    """Per-post context passed to a writer."""

    post: Post
    content_html: str
    """Final HTML to render (already has image src rewritten to local paths)."""

    output_dir: Path
    """Directory dedicated to this single post."""

    images_dir: Path | None = None


class BaseWriter(ABC):
    """Abstract output writer.

    Each writer is responsible for producing a single artifact (PDF, EPUB,
    Markdown, or plain text) for a given post into its dedicated folder.
    """

    extension: str = ""

    @abstractmethod
    def write(self, context: WriterContext) -> Path:
        """Render the post and return the path of the produced file."""
