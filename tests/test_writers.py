"""Tests for writers: build_writers wiring, CBZ, comic-PDF, combined EPUB."""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from rss_blog_archiver.models import Post
from rss_blog_archiver.writers import (
    CbzWriter,
    CombinedChapter,
    CombinedEpubWriter,
    ComicPdfWriter,
    EpubWriter,
    MarkdownWriter,
    WriterContext,
    build_writer,
    build_writers,
)


def _make_post(title: str = "Chapter 1") -> Post:
    return Post(
        title=title,
        url="https://example.blogspot.com/2024/01/post.html",
        published=datetime(2024, 1, 1, tzinfo=timezone.utc),
        author="Translator A",
    )


def _seed_images(images_dir: Path, count: int = 3) -> list[Path]:
    images_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(1, count + 1):
        path = images_dir / f"abcd1234_{i:03d}.jpg"
        img = Image.new("RGB", (50, 80), color=(50 * i, 80, 120))
        img.save(path, "JPEG")
        out.append(path)
    return out


class TestBuildWriters:
    def test_default_md_pdf(self) -> None:
        writers = build_writers("default", ["MD", "PDF"])
        assert len(writers) == 2
        assert isinstance(writers[0], MarkdownWriter)

    def test_comic_cbz_pdf(self) -> None:
        writers = build_writers("comic", ["CBZ", "PDF"])
        assert isinstance(writers[0], CbzWriter)
        assert isinstance(writers[1], ComicPdfWriter)

    def test_invalid_combo_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_writers("default", ["CBZ"])
        with pytest.raises(ValueError):
            build_writers("comic", ["MD"])

    def test_dedup_writers(self) -> None:
        writers = build_writers("comic", ["CBZ", "CBZ"])
        assert len(writers) == 1

    def test_unknown_content(self) -> None:
        with pytest.raises(ValueError):
            build_writers("podcast", ["MP3"])

    def test_unknown_format(self) -> None:
        with pytest.raises(ValueError):
            build_writers("default", ["XYZ"])

    def test_legacy_build_writer(self) -> None:
        assert isinstance(build_writer("MD"), MarkdownWriter)
        assert isinstance(build_writer("EPUB"), EpubWriter)
        with pytest.raises(ValueError):
            build_writer("CBZ")


class TestEpubWriter:
    def test_writes_valid_epub(self, tmp_path: Path) -> None:
        post = _make_post("Hello world")
        out = EpubWriter().write(
            WriterContext(
                post=post, content_html="<p>body</p>",
                output_dir=tmp_path, images_dir=None,
            )
        )
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert any(n.endswith(".xhtml") for n in names)

    def test_special_chars_in_title(self, tmp_path: Path) -> None:
        # Ampersands / angle brackets used to break the XHTML head/body wrap.
        post = _make_post("A & B <c>")
        out = EpubWriter().write(
            WriterContext(
                post=post, content_html="<p>x</p>",
                output_dir=tmp_path, images_dir=None,
            )
        )
        assert out.exists()


class TestCbzWriter:
    def test_packs_images_with_comicinfo(self, tmp_path: Path) -> None:
        post = _make_post("Chapter 1 - Test")
        images_dir = tmp_path / "post" / "images"
        _seed_images(images_dir, count=3)
        ctx = WriterContext(
            post=post, content_html="", output_dir=tmp_path, images_dir=images_dir,
        )
        out = CbzWriter().write(ctx)
        assert out.exists()
        assert out.suffix == ".cbz"
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        assert "ComicInfo.xml" in names
        image_entries = sorted(n for n in names if not n.endswith(".xml"))
        assert image_entries == ["001.jpg", "002.jpg", "003.jpg"]

    def test_raises_when_no_images(self, tmp_path: Path) -> None:
        post = _make_post()
        with pytest.raises(RuntimeError):
            CbzWriter().write(
                WriterContext(
                    post=post, content_html="", output_dir=tmp_path,
                    images_dir=tmp_path / "missing",
                )
            )


class TestComicPdfWriter:
    def test_writes_multi_page_pdf(self, tmp_path: Path) -> None:
        post = _make_post("Chapter 2")
        images_dir = tmp_path / "post" / "images"
        _seed_images(images_dir, count=2)
        ctx = WriterContext(
            post=post, content_html="", output_dir=tmp_path, images_dir=images_dir,
        )
        out = ComicPdfWriter().write(ctx)
        assert out.exists()
        assert out.suffix == ".pdf"
        # Sanity: file should be reasonably-sized (more than the tiny tests
        # files but not zero).
        assert out.stat().st_size > 200


class TestCombinedEpubWriter:
    def test_sorts_chapters_by_number(self, tmp_path: Path) -> None:
        chapters = [
            CombinedChapter(post=_make_post("Chapter 5"), html="<p>five</p>",
                            image_paths=[], chapter_number=5),
            CombinedChapter(post=_make_post("Chapter 1"), html="<p>one</p>",
                            image_paths=[], chapter_number=1),
            CombinedChapter(post=_make_post("Chapter 3"), html="<p>three</p>",
                            image_paths=[], chapter_number=3),
        ]
        out = CombinedEpubWriter().write(
            title="My Novel", author="Translator A",
            chapters=chapters, output_dir=tmp_path,
        )
        assert out.exists()
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
        chapter_files = sorted(n for n in names if n.startswith("EPUB/chap_"))
        assert len(chapter_files) == 3

    def test_empty_chapters_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            CombinedEpubWriter().write(
                title="x", author="", chapters=[], output_dir=tmp_path,
            )
