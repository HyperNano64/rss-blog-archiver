"""Unit tests for Scraper helpers (date filter, config defaults)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from rss_blog_archiver.models import Post
from rss_blog_archiver.scraper import ScrapeConfig, Scraper
from rss_blog_archiver.writers import MarkdownWriter


def _post(year: int, month: int = 1, day: int = 1) -> Post:
    return Post(
        title=f"{year}-{month:02d}-{day:02d}",
        url=f"https://example.com/{year}/{month:02d}/{day:02d}",
        published=datetime(year, month, day, tzinfo=timezone.utc),
    )


class TestDateFilter:
    def _make(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> Scraper:
        config = ScrapeConfig(
            url="https://example.com", output_dir=Path("/tmp/out"),
            since=since, until=until,
        )
        return Scraper(config, [MarkdownWriter()])

    def test_no_bounds_passes_all(self) -> None:
        scraper = self._make()
        assert scraper._passes_date_filter(_post(2020)) is True
        assert scraper._passes_date_filter(_post(2099)) is True

    def test_since_lower_bound(self) -> None:
        since = datetime(2023, 1, 1, tzinfo=timezone.utc)
        scraper = self._make(since=since)
        assert scraper._passes_date_filter(_post(2022)) is False
        assert scraper._passes_date_filter(_post(2024)) is True

    def test_until_upper_bound(self) -> None:
        until = datetime(2023, 12, 31, tzinfo=timezone.utc)
        scraper = self._make(until=until)
        assert scraper._passes_date_filter(_post(2023, 6)) is True
        assert scraper._passes_date_filter(_post(2024)) is False

    def test_both_bounds(self) -> None:
        scraper = self._make(
            since=datetime(2023, 1, 1, tzinfo=timezone.utc),
            until=datetime(2023, 12, 31, tzinfo=timezone.utc),
        )
        assert scraper._passes_date_filter(_post(2023, 6)) is True
        assert scraper._passes_date_filter(_post(2022, 6)) is False
        assert scraper._passes_date_filter(_post(2024, 1)) is False


class TestScrapeConfigDefaults:
    def test_legacy_mode_property(self) -> None:
        config = ScrapeConfig(url="x", output_dir=Path("/tmp"), formats=["EPUB", "PDF"])
        assert config.mode == "EPUB"

    def test_empty_formats(self) -> None:
        config = ScrapeConfig(url="x", output_dir=Path("/tmp"), formats=[])
        assert config.mode == "MD"


def _seed_image(images_dir: Path, name: str = "001.jpg") -> Path:
    images_dir.mkdir(parents=True, exist_ok=True)
    path = images_dir / name
    Image.new("RGB", (20, 30), color=(120, 80, 50)).save(path, "JPEG")
    return path


class TestRecordCombinedChapter:
    """Routing logic for ``--combined`` across content modes."""

    def _scraper(self, *, content_mode: str, combined: bool) -> Scraper:
        config = ScrapeConfig(
            url="https://example.com", output_dir=Path("/tmp/out"),
            content_mode=content_mode, combined=combined,
        )
        return Scraper(config, [MarkdownWriter()])

    def test_combined_off_no_op(self, tmp_path: Path) -> None:
        scraper = self._scraper(content_mode="novel", combined=False)
        scraper._record_combined_chapter(
            post=_post(2024), html="<p>x</p>",
            images_dir=tmp_path, chapter_number=1,
        )
        assert scraper._chapters == []
        assert scraper._comic_chapters == []

    def test_novel_mode_records_epub_chapter(self, tmp_path: Path) -> None:
        scraper = self._scraper(content_mode="novel", combined=True)
        images_dir = tmp_path / "img"
        _seed_image(images_dir)

        scraper._record_combined_chapter(
            post=_post(2024), html="<p>chapter body</p>",
            images_dir=images_dir, chapter_number=5,
        )
        assert len(scraper._chapters) == 1
        assert scraper._chapters[0].chapter_number == 5
        assert scraper._chapters[0].html == "<p>chapter body</p>"
        assert len(scraper._chapters[0].image_paths) == 1
        # Comic chapters list stays empty in novel mode.
        assert scraper._comic_chapters == []

    def test_comic_mode_records_cbz_chapter(self, tmp_path: Path) -> None:
        scraper = self._scraper(content_mode="comic", combined=True)
        images_dir = tmp_path / "img"
        _seed_image(images_dir, "001.jpg")
        _seed_image(images_dir, "002.jpg")

        scraper._record_combined_chapter(
            post=_post(2024), html="",  # comic mode often has no HTML
            images_dir=images_dir, chapter_number=3,
        )
        assert scraper._chapters == []
        assert len(scraper._comic_chapters) == 1
        assert scraper._comic_chapters[0].chapter_number == 3
        assert len(scraper._comic_chapters[0].image_paths) == 2

    def test_comic_mode_skips_image_less_chapter(self, tmp_path: Path) -> None:
        scraper = self._scraper(content_mode="comic", combined=True)
        # No images on disk, even though the dir exists.
        (tmp_path / "img").mkdir()
        scraper._record_combined_chapter(
            post=_post(2024), html="",
            images_dir=tmp_path / "img", chapter_number=1,
        )
        assert scraper._comic_chapters == []

    def test_novel_mode_skips_empty_html(self, tmp_path: Path) -> None:
        scraper = self._scraper(content_mode="novel", combined=True)
        scraper._record_combined_chapter(
            post=_post(2024), html="",
            images_dir=tmp_path, chapter_number=1,
        )
        assert scraper._chapters == []
