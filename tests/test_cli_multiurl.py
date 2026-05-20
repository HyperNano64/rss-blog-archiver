"""Tests for the CLI multi-URL batch flow (Phase 3 PR #5).

We mock Scraper.run so the tests don't touch the network. The goal is
to verify that the CLI correctly:

- accepts N positional URLs
- builds a fresh ScrapeConfig per URL with the right ``url`` value
- continues to the next URL after one fails (returns rc=1 overall)
- forwards --prefer-sitemap into ScrapeConfig.prefer_sitemap
- rejects -i combined with multiple URLs
"""

from __future__ import annotations

from typing import Any, ClassVar

import pytest

from rss_blog_archiver import cli
from rss_blog_archiver.scraper import ScrapeConfig


class _FakeScraper:
    """Captures the ScrapeConfig and pretends to process N posts."""

    instances: ClassVar[list[_FakeScraper]] = []

    def __init__(self, config: ScrapeConfig, writers: list[Any]) -> None:
        self.config = config
        self.writers = writers
        # The CLI reads .metadata after run() — return a sentinel count of 3.
        self._metadata = [
            {"title": "p1", "url": config.url + "/p1"},
            {"title": "p2", "url": config.url + "/p2"},
            {"title": "p3", "url": config.url + "/p3"},
        ]
        _FakeScraper.instances.append(self)

    @property
    def metadata(self) -> list[dict]:
        return list(self._metadata)

    def run(self) -> None:
        pass


class _RaisingScraper(_FakeScraper):
    """A scraper that raises mid-run to exercise the error branch."""

    def run(self) -> None:  # type: ignore[override]
        raise RuntimeError("boom")


@pytest.fixture(autouse=True)
def _reset_fake() -> None:
    _FakeScraper.instances.clear()
    yield
    _FakeScraper.instances.clear()


class TestMultiUrlBatch:
    def test_runs_once_per_url(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)

        rc = cli.main([
            "https://a.example.com/", "https://b.example.com/",
            "https://c.example.com/",
            "--output-dir", str(tmp_path),
        ])
        assert rc == 0
        assert len(_FakeScraper.instances) == 3
        assert [s.config.url for s in _FakeScraper.instances] == [
            "https://a.example.com/",
            "https://b.example.com/",
            "https://c.example.com/",
        ]

    def test_single_url_keeps_working(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """Backwards compatibility: 1 URL still works exactly like before."""
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)
        rc = cli.main([
            "https://only.example.com/",
            "--output-dir", str(tmp_path),
        ])
        assert rc == 0
        assert len(_FakeScraper.instances) == 1

    def test_continues_after_error(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """A failure in one URL must not stop later URLs."""
        instances: list[Any] = []

        def factory(config: ScrapeConfig, writers: list[Any]) -> Any:
            inst = (
                _RaisingScraper(config, writers)
                if "fail" in config.url
                else _FakeScraper(config, writers)
            )
            instances.append(inst)
            return inst

        monkeypatch.setattr(cli, "Scraper", factory)
        rc = cli.main([
            "https://good1.example.com/",
            "https://fail.example.com/",
            "https://good2.example.com/",
            "--output-dir", str(tmp_path),
        ])
        # rc=1 because one URL failed.
        assert rc == 1
        # But all 3 scrapers were constructed (continued past the failure).
        assert len(instances) == 3
        assert [s.config.url for s in instances] == [
            "https://good1.example.com/",
            "https://fail.example.com/",
            "https://good2.example.com/",
        ]

    def test_prefer_sitemap_flag_threads_to_config(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)
        rc = cli.main([
            "https://a.example.com/", "https://b.example.com/",
            "--prefer-sitemap",
            "--output-dir", str(tmp_path),
        ])
        assert rc == 0
        assert all(s.config.prefer_sitemap for s in _FakeScraper.instances)

    def test_interactive_rejected_with_multiple_urls(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """-i + multiple URLs should fail fast with rc=2, no Scraper built."""
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)
        rc = cli.main([
            "https://a.example.com/", "https://b.example.com/",
            "-i",
            "--output-dir", str(tmp_path),
        ])
        assert rc == 2
        assert _FakeScraper.instances == []

    def test_no_urls_returns_1(self, tmp_path: Any) -> None:
        rc = cli.main(["--output-dir", str(tmp_path)])
        assert rc == 1


class TestScrapeConfigPropagation:
    """Sanity checks: per-URL ScrapeConfig has the right shared options."""

    def test_content_and_formats_propagate(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)
        cli.main([
            "https://a.example.com/", "https://b.example.com/",
            "--content", "novel",
            "--format", "MD,EPUB",
            "--output-dir", str(tmp_path),
        ])
        for inst in _FakeScraper.instances:
            assert inst.config.content_mode == "novel"
            assert inst.config.formats == ["MD", "EPUB"]

    def test_combined_flag_propagates(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        monkeypatch.setattr(cli, "Scraper", _FakeScraper)
        cli.main([
            "https://a.example.com/",
            "--content", "comic", "--format", "CBZ", "--combined",
            "--output-dir", str(tmp_path),
        ])
        assert _FakeScraper.instances[0].config.combined is True
        assert _FakeScraper.instances[0].config.content_mode == "comic"
