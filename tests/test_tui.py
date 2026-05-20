"""Unit tests for the Textual TUI selection screen."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone

import pytest
from textual.widgets import SelectionList

from rss_blog_archiver.adapters.base import AdapterDetectionResult, BaseAdapter
from rss_blog_archiver.models import FeedPage, Post
from rss_blog_archiver.selector import SelectionResult
from rss_blog_archiver.tui import RbaTuiApp


# ---------------------------------------------------------------------------
# Fake adapter — keeps the tests hermetic, no HTTP.
# ---------------------------------------------------------------------------
class _FakeAdapter(BaseAdapter):
    name = "fake"

    def __init__(self, posts: list[Post], labels: list[str]) -> None:
        self._posts = posts
        self._labels = labels
        # Skip the BaseAdapter HTTP wiring — the TUI never touches self.http.
        self.detection = AdapterDetectionResult(
            matched=True,
            confidence=1.0,
            feed_url="https://example.com/feed",
            base_url="https://example.com",
        )

    def detect(self, url: str) -> AdapterDetectionResult:
        return self.detection

    def iter_pages(
        self,
        *,
        feed_url: str,
        label: str | None = None,
        max_posts: int | None = None,
    ) -> Iterator[FeedPage]:
        posts = self._posts
        if label is not None:
            posts = [p for p in posts if label in p.labels]
        if max_posts is not None:
            posts = posts[:max_posts]
        yield FeedPage(posts=posts, next_cursor=None)

    def fetch_labels(self, base_url: str) -> list[str]:
        return list(self._labels)


def _make_post(idx: int, *, labels: list[str] | None = None) -> Post:
    return Post(
        title=f"Chapter {idx}: title",
        url=f"https://example.com/{idx}",
        published=datetime(2024, 1, idx, tzinfo=timezone.utc),
        summary=f"summary for post {idx}",
        labels=list(labels or []),
    )


def _build_app(
    posts: list[Post] | None = None,
    labels: list[str] | None = None,
) -> RbaTuiApp:
    adapter = _FakeAdapter(
        posts=posts if posts is not None else [_make_post(i) for i in range(1, 4)],
        labels=labels if labels is not None else ["NovelA", "NovelB"],
    )
    return RbaTuiApp(
        adapter,
        feed_url="https://example.com/feed",
        content_mode="novel",
        formats=["EPUB"],
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_titles_load_on_mount() -> None:
    app = _build_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        # Give workers a chance to settle.
        await app.workers.wait_for_complete()
        await pilot.pause()
        titles = app.query_one("#titles", SelectionList)
        assert len(titles._options) == 3
        labels = app.query_one("#labels", SelectionList)
        # Includes the "All posts" sentinel plus two real labels.
        assert len(labels._options) == 3


@pytest.mark.asyncio
async def test_select_all_then_confirm_returns_every_post() -> None:
    posts = [_make_post(i) for i in range(1, 6)]
    app = _build_app(posts=posts)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#titles", SelectionList).focus()
        await pilot.pause()
        app.action_select_all()
        await pilot.pause()
        app.action_confirm()
        await pilot.pause()
    result = app.return_value
    assert isinstance(result, SelectionResult)
    assert len(result.posts) == 5
    assert {p.url for p in result.posts} == {p.url for p in posts}


@pytest.mark.asyncio
async def test_cancel_returns_cancelled_result() -> None:
    app = _build_app()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.action_cancel()
        await pilot.pause()
    result = app.return_value
    assert isinstance(result, SelectionResult)
    assert result.cancelled is True
    assert result.posts == []


@pytest.mark.asyncio
async def test_confirm_with_no_selection_returns_empty_posts() -> None:
    """Empty selection means 'scrape everything for the active label'."""
    app = _build_app()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.query_one("#titles", SelectionList).focus()
        await pilot.pause()
        app.action_confirm()
        await pilot.pause()
    result = app.return_value
    assert isinstance(result, SelectionResult)
    assert result.cancelled is False
    assert result.posts == []  # signals 'scrape everything'


@pytest.mark.asyncio
async def test_select_none_clears_selection() -> None:
    app = _build_app()
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        app.action_select_all()
        await pilot.pause()
        titles = app.query_one("#titles", SelectionList)
        assert len(titles.selected) > 0
        app.action_select_none()
        await pilot.pause()
        assert len(titles.selected) == 0


@pytest.mark.asyncio
async def test_label_filter_reloads_titles() -> None:
    """Confirming on the labels panel reloads the title list filtered by label."""
    posts = [
        _make_post(1, labels=["NovelA"]),
        _make_post(2, labels=["NovelB"]),
        _make_post(3, labels=["NovelA", "NovelB"]),
    ]
    app = _build_app(posts=posts, labels=["NovelA", "NovelB"])
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Select the NovelA label, then trigger confirm on the labels widget.
        labels_widget = app.query_one("#labels", SelectionList)
        labels_widget.focus()
        await pilot.pause()
        labels_widget.select(labels_widget.get_option_at_index(1))  # "NovelA"
        await pilot.pause()
        app.action_confirm()
        await app.workers.wait_for_complete()
        await pilot.pause()
        titles = app.query_one("#titles", SelectionList)
        # Only the two posts tagged NovelA should remain.
        assert len(titles._options) == 2
        assert app._active_label == "NovelA"
        app.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_max_titles_per_label_caps_loaded_titles() -> None:
    posts = [_make_post(i) for i in range(1, 11)]  # 10 posts
    adapter = _FakeAdapter(posts=posts, labels=[])
    app = RbaTuiApp(
        adapter,
        feed_url="https://example.com/feed",
        content_mode="default",
        formats=["MD"],
        max_titles_per_label=3,
    )
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        titles = app.query_one("#titles", SelectionList)
        assert len(titles._options) == 3
        app.action_cancel()
        await pilot.pause()


@pytest.mark.asyncio
async def test_preview_panel_updates_on_highlight() -> None:
    posts = [
        _make_post(1),
        Post(
            title="Special title",
            url="https://example.com/special",
            published=datetime(2024, 6, 15, tzinfo=timezone.utc),
            summary="A unique summary text",
            author="Translator-X",
        ),
    ]
    app = _build_app(posts=posts)
    async with app.run_test() as pilot:
        await app.workers.wait_for_complete()
        await pilot.pause()
        # Move highlight to the second title to trigger the preview update.
        titles = app.query_one("#titles", SelectionList)
        titles.focus()
        await pilot.pause()
        titles.highlighted = 1
        await pilot.pause()
        preview = app.query_one("#preview")
        rendered = str(preview.render())
        assert "Special title" in rendered
        assert "Translator-X" in rendered
        app.action_cancel()
        await pilot.pause()
