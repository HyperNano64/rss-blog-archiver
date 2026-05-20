"""Tests for the series-level CombinedCbzWriter (Phase 3 PR #5).

Verifies:
- continuous, lexically-sorted page numbering across chapters
- ComicInfo.xml at the archive root with series-level metadata
- chapter-order sort respects detected chapter numbers, then published date
- empty / malformed chapters are skipped gracefully
"""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest
from PIL import Image

from rss_blog_archiver.models import Post
from rss_blog_archiver.writers import CombinedCbzWriter, CombinedComicChapter


def _make_post(title: str, published: datetime | None = None) -> Post:
    return Post(
        title=title,
        url=f"https://example.blogspot.com/{title.replace(' ', '-').lower()}.html",
        published=published or datetime(2024, 1, 1, tzinfo=timezone.utc),
        author="Scanlator A",
    )


def _seed_images(images_dir: Path, count: int) -> list[Path]:
    images_dir.mkdir(parents=True, exist_ok=True)
    out: list[Path] = []
    for i in range(1, count + 1):
        path = images_dir / f"chap_page_{i:03d}.jpg"
        Image.new("RGB", (40, 60), color=(20 * i, 80, 120)).save(path, "JPEG")
        out.append(path)
    return out


def _list_archive_members(cbz_path: Path) -> list[str]:
    with zipfile.ZipFile(cbz_path) as zf:
        return zf.namelist()


class TestCombinedCbzWriter:
    def test_continuous_page_numbering(self, tmp_path: Path) -> None:
        """Pages from chapter 2 continue numbering where chapter 1 left off."""
        ch1_imgs = _seed_images(tmp_path / "ch1", count=3)
        ch2_imgs = _seed_images(tmp_path / "ch2", count=2)

        chapters = [
            CombinedComicChapter(
                post=_make_post("Chapter 1"),
                image_paths=ch1_imgs,
                chapter_number=1,
            ),
            CombinedComicChapter(
                post=_make_post("Chapter 2"),
                image_paths=ch2_imgs,
                chapter_number=2,
            ),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        target = CombinedCbzWriter().write(
            title="My Series", author="Scanlator A",
            chapters=chapters, output_dir=out_dir,
        )

        assert target.exists()
        assert target.suffix == ".cbz"

        members = sorted(_list_archive_members(target))
        # 5 pages + 1 ComicInfo.xml
        image_members = [m for m in members if m != "ComicInfo.xml"]
        assert len(image_members) == 5
        # Lexical sort must equal numeric reading order.
        assert image_members == sorted(image_members)
        # Page indices must be continuous (1..5), zero-padded.
        for index, name in enumerate(image_members, start=1):
            stem = name.rsplit(".", 1)[0]
            assert int(stem) == index, f"Expected page #{index}, got {name!r}"

    def test_comic_info_xml_metadata(self, tmp_path: Path) -> None:
        """ComicInfo.xml contains Title, Series, total PageCount, and per-chapter notes."""
        imgs_a = _seed_images(tmp_path / "a", count=2)
        imgs_b = _seed_images(tmp_path / "b", count=3)

        chapters = [
            CombinedComicChapter(
                post=_make_post("Ch 1"), image_paths=imgs_a, chapter_number=1,
            ),
            CombinedComicChapter(
                post=_make_post("Ch 2"), image_paths=imgs_b, chapter_number=2,
            ),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        target = CombinedCbzWriter().write(
            title="Series Name", author="Author X",
            chapters=chapters, output_dir=out_dir,
        )
        with zipfile.ZipFile(target) as zf:
            comic_info = zf.read("ComicInfo.xml").decode("utf-8")

        assert "<Title>Series Name</Title>" in comic_info
        assert "<Series>Series Name</Series>" in comic_info
        assert "<Writer>Author X</Writer>" in comic_info
        assert "<PageCount>5</PageCount>" in comic_info
        assert "<Count>2</Count>" in comic_info
        # Per-chapter notes manifest references chapter titles.
        assert "Ch 1" in comic_info
        assert "Ch 2" in comic_info

    def test_chapter_ordering_by_number(self, tmp_path: Path) -> None:
        """When chapters are passed out of order they are sorted by chapter number."""
        imgs_a = _seed_images(tmp_path / "a", count=1)
        imgs_b = _seed_images(tmp_path / "b", count=1)
        imgs_c = _seed_images(tmp_path / "c", count=1)

        # Pass in reverse order
        chapters = [
            CombinedComicChapter(post=_make_post("Ch 3"), image_paths=imgs_c, chapter_number=3),
            CombinedComicChapter(post=_make_post("Ch 1"), image_paths=imgs_a, chapter_number=1),
            CombinedComicChapter(post=_make_post("Ch 2"), image_paths=imgs_b, chapter_number=2),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        target = CombinedCbzWriter().write(
            title="Order Series", author="X",
            chapters=chapters, output_dir=out_dir,
        )
        # The pages must come out in the chapter-1, chapter-2, chapter-3 order.
        # The manifest notes encode this; checking via ComicInfo is enough.
        with zipfile.ZipFile(target) as zf:
            comic_info = zf.read("ComicInfo.xml").decode("utf-8")
        notes_section = comic_info.split("<Notes>")[1].split("</Notes>")[0]
        assert notes_section.find("Ch 1") < notes_section.find("Ch 2") < notes_section.find("Ch 3")

    def test_ordering_falls_back_to_published_date(self, tmp_path: Path) -> None:
        """Chapters without numbers sort by published date."""
        imgs_a = _seed_images(tmp_path / "a", count=1)
        imgs_b = _seed_images(tmp_path / "b", count=1)

        chapters = [
            CombinedComicChapter(
                post=_make_post(
                    "Late",
                    published=datetime(2024, 6, 1, tzinfo=timezone.utc),
                ),
                image_paths=imgs_b,
            ),
            CombinedComicChapter(
                post=_make_post(
                    "Early",
                    published=datetime(2024, 1, 1, tzinfo=timezone.utc),
                ),
                image_paths=imgs_a,
            ),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        target = CombinedCbzWriter().write(
            title="Date Series", author="X",
            chapters=chapters, output_dir=out_dir,
        )
        with zipfile.ZipFile(target) as zf:
            comic_info = zf.read("ComicInfo.xml").decode("utf-8")
        notes_section = comic_info.split("<Notes>")[1].split("</Notes>")[0]
        assert notes_section.find("Early") < notes_section.find("Late")

    def test_empty_chapter_skipped(self, tmp_path: Path) -> None:
        """Chapters with no images are silently dropped from the archive."""
        imgs_a = _seed_images(tmp_path / "a", count=2)

        chapters = [
            CombinedComicChapter(
                post=_make_post("Real"), image_paths=imgs_a, chapter_number=1,
            ),
            CombinedComicChapter(
                post=_make_post("Empty"), image_paths=[], chapter_number=2,
            ),
        ]
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        target = CombinedCbzWriter().write(
            title="With Empty", author="X",
            chapters=chapters, output_dir=out_dir,
        )
        with zipfile.ZipFile(target) as zf:
            members = zf.namelist()
        # 2 pages + ComicInfo.xml.
        assert len([m for m in members if m != "ComicInfo.xml"]) == 2
        comic_info = zipfile.ZipFile(target).read("ComicInfo.xml").decode("utf-8")
        assert "<PageCount>2</PageCount>" in comic_info

    def test_raises_when_all_chapters_empty(self, tmp_path: Path) -> None:
        """If every chapter is image-less the writer must raise."""
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with pytest.raises(ValueError, match="No images"):
            CombinedCbzWriter().write(
                title="Empty", author="X",
                chapters=[
                    CombinedComicChapter(
                        post=_make_post("X"), image_paths=[], chapter_number=1,
                    ),
                ],
                output_dir=out_dir,
            )

    def test_raises_when_no_chapters(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        with pytest.raises(ValueError, match="No chapters"):
            CombinedCbzWriter().write(
                title="Empty", author="X", chapters=[], output_dir=out_dir,
            )
