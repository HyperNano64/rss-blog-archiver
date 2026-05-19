"""Unit tests for Scraper helpers (date filter, config defaults)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

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
