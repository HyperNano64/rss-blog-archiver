"""The Textual application that powers ``rba --tui``.

Layout (single screen):

    +-- Header (blog metadata) ----------------------------------+
    | Labels (1fr)  | Titles (2fr, multi-select)  | Preview (2fr)|
    |---------------+-----------------------------+--------------|
    | [ ] All       | [x] Chapter 1: ...          | Title        |
    | [ ] Novel A   | [ ] Chapter 2: ...          | Published    |
    | [ ] Novel B   |  ...                        | Author       |
    +-- Footer (status + key hints) -----------------------------+

The label list and the title list are :class:`textual.widgets.SelectionList`
instances. The title list is the primary selection target — its selected
keys are the post URLs that will be returned in
:class:`~rss_blog_archiver.selector.SelectionResult`.

Adapter I/O (label fetch + post enumeration) runs in background workers
so the UI stays responsive. The fetch is synchronous (HTTP via
:class:`~rss_blog_archiver.http_client.HttpClient`) but textual's worker
infrastructure offloads it to a thread.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Footer, Header, SelectionList, Static
from textual.worker import Worker

from rss_blog_archiver.adapters.base import BaseAdapter
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.selector import SelectionResult

logger = get_logger(__name__)


# Sentinel label value used for "no label filter — show every post".
_ALL_LABEL = "__all__"


@dataclass(slots=True)
class _TitleEntry:
    """Internal cache row keyed by post URL."""

    post: Post


class RbaTuiApp(App[SelectionResult]):
    """Full-screen TUI for browsing labels and titles before a scrape."""

    CSS = """
    Screen {
        layout: vertical;
    }

    #meta {
        height: 3;
        background: $panel;
        color: $text;
        padding: 0 1;
        content-align: left middle;
        border-bottom: solid $accent;
    }

    #body {
        height: 1fr;
    }

    #labels {
        width: 1fr;
        border: solid $panel-lighten-1;
    }

    #titles {
        width: 2fr;
        border: solid $panel-lighten-1;
    }

    #preview {
        width: 2fr;
        border: solid $panel-lighten-1;
        padding: 0 1;
    }

    #status {
        height: 1;
        background: $panel;
        color: $text-muted;
        padding: 0 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter", "confirm", "Scrape selection", priority=True),
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+a", "select_all", "Select all titles"),
        Binding("ctrl+n", "select_none", "Clear selection"),
        Binding("tab", "focus_next", "Next panel", show=False),
        Binding("shift+tab", "focus_previous", "Previous panel", show=False),
    ]

    def __init__(
        self,
        adapter: BaseAdapter,
        *,
        feed_url: str,
        content_mode: str,
        formats: list[str],
        max_titles_per_label: int | None = None,
    ) -> None:
        super().__init__()
        self._adapter = adapter
        self._feed_url = feed_url
        self._content_mode = content_mode
        self._formats = formats
        self._max_titles_per_label = max_titles_per_label
        # url -> _TitleEntry for the currently-shown title list. Used for
        # preview lookup and to translate selected URLs back to Posts on
        # confirm.
        self._title_cache: dict[str, _TitleEntry] = {}
        # Tracks which label was active when titles were loaded, so the
        # ``confirm`` action knows whether to record it on SelectionResult.
        self._active_label: str | None = None

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield Static(self._format_meta_line(), id="meta")
        with Horizontal(id="body"):
            yield SelectionList[str](id="labels")
            yield SelectionList[str](id="titles")
            yield Vertical(
                Static("Highlight a title to preview.", id="preview"),
            )
        yield Static(
            "loading labels…", id="status",
        )
        yield Footer()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_mount(self) -> None:
        self.title = "rss-blog-archiver"
        self.sub_title = self._safe_blog_host()
        labels_widget = self.query_one("#labels", SelectionList)
        labels_widget.border_title = "Labels"
        titles_widget = self.query_one("#titles", SelectionList)
        titles_widget.border_title = "Titles"
        preview = self.query_one("#preview", Static)
        preview.border_title = "Preview"

        # Kick off the label load. We initially populate the title list
        # with the unfiltered post stream so users who don't care about
        # labels can confirm immediately.
        self.run_worker(self._load_labels, thread=True, exclusive=True)
        self.run_worker(
            lambda: self._load_titles(label=None),
            thread=True,
            exclusive=True,
            group="titles",
        )

    # ------------------------------------------------------------------
    # Workers (run in background threads)
    # ------------------------------------------------------------------
    def _load_labels(self) -> list[str]:
        base_url = self._base_url_for_labels()
        try:
            labels = self._adapter.fetch_labels(base_url)
        except Exception as exc:
            logger.warning("Label fetch failed: %s", exc)
            return []
        return sorted({lbl for lbl in labels if lbl})

    def _load_titles(self, *, label: str | None) -> list[Post]:
        """Enumerate post titles for the active label.

        Honors :attr:`_max_titles_per_label` as a soft cap so the title
        list stays responsive on very large blogs. Users can clear it
        with a scrape that doesn't use ``--max-posts``.
        """
        posts: list[Post] = []
        try:
            for page in self._adapter.iter_pages(
                feed_url=self._feed_url,
                label=label,
                max_posts=self._max_titles_per_label,
            ):
                posts.extend(page.posts)
                if (
                    self._max_titles_per_label is not None
                    and len(posts) >= self._max_titles_per_label
                ):
                    break
        except Exception as exc:
            logger.warning("Title fetch failed: %s", exc)
        if self._max_titles_per_label is not None:
            posts = posts[: self._max_titles_per_label]
        return posts

    # ------------------------------------------------------------------
    # Worker callbacks
    # ------------------------------------------------------------------
    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        worker = event.worker
        if not worker.is_finished:
            return
        if worker.error is not None:
            logger.warning("TUI worker failed: %s", worker.error)
            return
        if worker.group == "titles":
            posts = worker.result or []
            self._render_titles(posts)
            return
        result = worker.result
        if isinstance(result, list) and worker.group != "titles":
            # The default group is the label loader.
            self._render_labels(result)

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def _render_labels(self, labels: list[str]) -> None:
        widget = self.query_one("#labels", SelectionList)
        widget.clear_options()
        widget.add_option(("All posts", _ALL_LABEL, True))
        for label in labels:
            widget.add_option((label, label, False))
        self._set_status(
            f"{len(labels)} labels · select one and press Enter to filter titles"
        )

    def _render_titles(self, posts: Iterable[Post]) -> None:
        widget = self.query_one("#titles", SelectionList)
        widget.clear_options()
        self._title_cache.clear()
        count = 0
        for post in posts:
            self._title_cache[post.url] = _TitleEntry(post=post)
            widget.add_option((self._format_title_row(post), post.url, False))
            count += 1
        self._set_status(
            f"{count} titles loaded · space to toggle · Ctrl+A all · Enter to scrape"
        )

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------
    def on_selection_list_selection_highlighted(
        self, event: SelectionList.SelectionHighlighted
    ) -> None:
        if event.selection_list.id == "titles":
            url = event.selection.value
            entry = self._title_cache.get(url)
            if entry is not None:
                self._render_preview(entry.post)
            return
        if event.selection_list.id == "labels":
            # No-op: label switching happens explicitly via Enter on the
            # label panel (so users can browse labels without thrashing
            # the title fetch). See :meth:`action_confirm`.
            return

    # ------------------------------------------------------------------
    # Actions (key bindings)
    # ------------------------------------------------------------------
    def action_confirm(self) -> None:
        """Enter: either apply the focused label filter, or finish."""
        focused = self.focused
        if focused is not None and focused.id == "labels":
            labels_widget = self.query_one("#labels", SelectionList)
            chosen = labels_widget.selected
            label = None
            if chosen:
                # SelectionList.selected returns *every* selected value.
                # In single-select usage we pick the most recently
                # highlighted one; otherwise the first non-sentinel.
                for value in chosen:
                    if value != _ALL_LABEL:
                        label = value
                        break
            self._active_label = label
            self._set_status(
                f"loading titles for {label or 'All posts'}…"
            )
            self.run_worker(
                lambda lbl=label: self._load_titles(label=lbl),
                thread=True,
                exclusive=True,
                group="titles",
            )
            self.query_one("#titles", SelectionList).focus()
            return

        titles_widget = self.query_one("#titles", SelectionList)
        selected_urls = list(titles_widget.selected)
        if not selected_urls:
            # "Confirm with empty selection" means "scrape everything
            # for the active label" (which may be None == every post).
            self.exit(SelectionResult(label=self._active_label))
            return
        posts = [
            self._title_cache[url].post
            for url in selected_urls
            if url in self._title_cache
        ]
        self.exit(SelectionResult(posts=posts, label=self._active_label))

    def action_cancel(self) -> None:
        self.exit(SelectionResult(cancelled=True))

    def action_select_all(self) -> None:
        widget = self.query_one("#titles", SelectionList)
        widget.select_all()
        self._set_status(f"{len(widget.selected)} titles selected")

    def action_select_none(self) -> None:
        widget = self.query_one("#titles", SelectionList)
        widget.deselect_all()
        self._set_status("0 titles selected")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _render_preview(self, post: Post) -> None:
        widget = self.query_one("#preview", Static)
        lines = [
            f"[b]{_escape_markup(post.title) or '(no title)'}[/b]",
            "",
            f"URL:        {post.url}",
        ]
        if post.published.year > 1970:  # placeholder posts use epoch
            lines.append(f"Published:  {post.published.isoformat()}")
        if post.author:
            lines.append(f"Author:     {_escape_markup(post.author)}")
        if post.labels:
            lines.append("Labels:     " + ", ".join(
                _escape_markup(lbl) for lbl in post.labels
            ))
        if post.summary:
            snippet = post.summary.strip().replace("\n", " ")
            if len(snippet) > 400:
                snippet = snippet[:400] + "…"
            lines.append("")
            lines.append(_escape_markup(snippet))
        widget.update("\n".join(lines))

    def _set_status(self, text: str) -> None:
        try:
            widget = self.query_one("#status", Static)
        except Exception:
            return
        widget.update(text)

    def _format_meta_line(self) -> str:
        bits = [
            f"host: {self._safe_blog_host()}",
            f"adapter: {self._adapter.name}",
            f"mode: {self._content_mode}",
            f"formats: {','.join(self._formats)}",
        ]
        return "  ·  ".join(bits)

    def _format_title_row(self, post: Post) -> str:
        title = post.title.strip() or "(no title)"
        if post.published.year > 1970:
            return f"{post.published.date().isoformat()}  {title}"
        return title

    def _safe_blog_host(self) -> str:
        detection = self._adapter.detection
        if detection and detection.base_url:
            return detection.base_url
        return self._feed_url

    def _base_url_for_labels(self) -> str:
        detection = self._adapter.detection
        if detection and detection.base_url:
            return detection.base_url
        return self._feed_url


def _escape_markup(text: str) -> str:
    """Make user-supplied strings safe to inject into Textual markup."""
    return text.replace("[", r"\[")


def run_tui_selection(
    adapter: BaseAdapter,
    *,
    feed_url: str,
    content_mode: str,
    formats: list[str],
    max_titles_per_label: int | None = None,
) -> SelectionResult:
    """Drive the full-screen TUI and return the user's choice.

    Always returns a :class:`SelectionResult` so callers can use the same
    plumbing as the rich-based picker. If the user cancels (Esc or
    Ctrl+C) the result has ``cancelled=True``.
    """
    app = RbaTuiApp(
        adapter,
        feed_url=feed_url,
        content_mode=content_mode,
        formats=formats,
        max_titles_per_label=max_titles_per_label,
    )
    result = app.run()
    if isinstance(result, SelectionResult):
        return result
    # Textual returns None when the user closes the app without calling
    # exit() with a value (e.g. Ctrl+C straight to the terminal). Treat
    # it as a cancel rather than re-raising.
    return SelectionResult(cancelled=True)
