"""Interactive post selection using ``rich``.

Launched when the user passes ``-i`` / ``--interactive``. After the adapter
has been detected, ``run_interactive_selection`` shows a top-level menu:

    [1] Scrape all posts
    [2] Browse by label / tag
    [3] Browse all titles individually
    [4] Filter by date range
    [0] Quit

Each branch returns a :class:`SelectionResult` with the concrete list of
:class:`~rss_blog_archiver.models.Post` instances the scraper should
process — plus any derived filters that should be applied (label, date
range, max_posts).

The selector deliberately does no scraping itself; it only talks to the
adapter to enumerate posts and labels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, IntPrompt, Prompt
from rich.table import Table

from rss_blog_archiver.adapters.base import BaseAdapter
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.utils import safe_parse_date

logger = get_logger(__name__)

console = Console()


@dataclass(slots=True)
class SelectionResult:
    """Outcome of the interactive selection step."""

    posts: list[Post] = field(default_factory=list)
    """Explicit list of posts to scrape; empty means 'scrape everything'."""

    label: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    max_posts: int | None = None
    cancelled: bool = False


def run_interactive_selection(
    adapter: BaseAdapter,
    *,
    feed_url: str,
    content_mode: str,
    formats: list[str],
) -> SelectionResult:
    """Drive the interactive menu and return the user's choice."""
    _print_header(adapter, content_mode=content_mode, formats=formats)

    while True:
        choice = Prompt.ask(
            "[bold]What do you want to scrape?[/bold]",
            choices=["1", "2", "3", "4", "0"],
            default="1",
        )
        if choice == "0":
            return SelectionResult(cancelled=True)
        if choice == "1":
            return SelectionResult()  # scrape everything
        if choice == "2":
            result = _choose_by_label(adapter, feed_url=feed_url)
        elif choice == "3":
            result = _choose_by_title(adapter, feed_url=feed_url)
        elif choice == "4":
            result = _choose_by_date_range(adapter, feed_url=feed_url)
        else:  # pragma: no cover - defensive
            continue
        if result is not None:
            return result


# ----------------------------------------------------------------------
# Top-level UI
# ----------------------------------------------------------------------
def _print_header(
    adapter: BaseAdapter, *, content_mode: str, formats: list[str]
) -> None:
    detection = adapter.detection
    rows = [
        ("Adapter", adapter.name),
        ("Feed URL", detection.feed_url if detection else "?"),
        ("Confidence", f"{detection.confidence:.2f}" if detection else "?"),
        ("Content mode", content_mode),
        ("Output formats", ", ".join(formats)),
    ]
    table = Table(show_header=False, box=box.SIMPLE_HEAVY)
    table.add_column(style="bold cyan")
    table.add_column()
    for k, v in rows:
        table.add_row(k, str(v))
    console.print(Panel(table, title="Detected blog", border_style="cyan"))
    console.print()
    console.print("  [bold]1[/bold]) Scrape all posts")
    console.print("  [bold]2[/bold]) Browse by label / tag")
    console.print("  [bold]3[/bold]) Browse all titles individually")
    console.print("  [bold]4[/bold]) Filter by date range")
    console.print("  [bold]0[/bold]) Quit\n")


# ----------------------------------------------------------------------
# Branches
# ----------------------------------------------------------------------
def _choose_by_label(
    adapter: BaseAdapter, *, feed_url: str
) -> SelectionResult | None:
    base_url = (adapter.detection.base_url if adapter.detection else feed_url) or feed_url
    labels = adapter.fetch_labels(base_url)
    if not labels:
        console.print("[yellow]No labels found for this blog.[/yellow]")
        return None

    table = Table(title="Available labels", box=box.SIMPLE)
    table.add_column("#", style="cyan", justify="right")
    table.add_column("Label")
    for index, label in enumerate(labels, start=1):
        table.add_row(str(index), label)
    console.print(table)

    raw = Prompt.ask("Pick a label number (or 'b' to go back)", default="b")
    if raw.strip().lower() in {"b", "back"}:
        return None
    try:
        index = int(raw)
    except ValueError:
        console.print("[red]Invalid number.[/red]")
        return None
    if not (1 <= index <= len(labels)):
        console.print("[red]Out of range.[/red]")
        return None
    label = labels[index - 1]

    # After picking a label, optionally narrow to specific titles within it.
    posts = list(_enumerate_posts(adapter, feed_url=feed_url, label=label))
    if not posts:
        console.print(f"[yellow]No posts found under label {label!r}.[/yellow]")
        return SelectionResult(label=label)
    if not Confirm.ask(
        f"Found {len(posts)} posts under {label!r}. Pick titles individually?",
        default=False,
    ):
        return SelectionResult(label=label)
    selected = _pick_from_list(posts)
    if selected is None:
        return None
    return SelectionResult(posts=selected, label=label)


def _choose_by_title(
    adapter: BaseAdapter, *, feed_url: str
) -> SelectionResult | None:
    posts = list(_enumerate_posts(adapter, feed_url=feed_url, label=None))
    if not posts:
        console.print("[yellow]No posts found.[/yellow]")
        return None
    selected = _pick_from_list(posts)
    if selected is None:
        return None
    return SelectionResult(posts=selected)


def _choose_by_date_range(
    adapter: BaseAdapter, *, feed_url: str
) -> SelectionResult | None:
    since_raw = Prompt.ask(
        "Start date (YYYY-MM-DD) [empty = no lower bound]", default=""
    )
    until_raw = Prompt.ask(
        "End date   (YYYY-MM-DD) [empty = no upper bound]", default=""
    )
    since = _parse_date_input(since_raw)
    until = _parse_date_input(until_raw)
    if since is None and until is None:
        console.print("[yellow]No bounds supplied — falling back to 'all'.[/yellow]")
    return SelectionResult(since=since, until=until)


# ----------------------------------------------------------------------
# Generic paginated picker
# ----------------------------------------------------------------------
def _pick_from_list(posts: list[Post], *, page_size: int = 25) -> list[Post] | None:
    """Paginated picker that returns the user-selected subset.

    Selection syntax (entered as one line):
        1,3,5        -> posts #1, #3, #5 on this page
        1-10         -> a range
        1-10,15,20-25 -> mix
        all          -> every post (across all pages)
        n / next     -> next page
        p / prev     -> previous page
        b / back     -> abandon this menu
    """
    total = len(posts)
    pages = max(1, (total + page_size - 1) // page_size)
    page = 0
    selected: dict[int, Post] = {}

    while True:
        start = page * page_size
        end = min(start + page_size, total)
        table = Table(
            title=f"Posts {start + 1}-{end} of {total}  (page {page + 1}/{pages})",
            box=box.SIMPLE,
        )
        table.add_column("#", style="cyan", justify="right")
        table.add_column("Date", style="dim")
        table.add_column("Title")
        for idx in range(start, end):
            post = posts[idx]
            date = post.published.date().isoformat() if post.published else "?"
            marker = "[green]*[/green] " if (idx + 1) in selected else "  "
            table.add_row(f"{marker}{idx + 1}", date, post.title)
        console.print(table)
        if selected:
            console.print(f"[dim]Currently selected: {len(selected)} posts[/dim]")

        raw = Prompt.ask(
            "Pick (e.g. '1,3,5-10'), 'all', 'n' next, 'p' prev, 'done', 'b' back",
            default="done",
        )
        raw = raw.strip().lower()
        if raw in {"b", "back"}:
            return None
        if raw in {"done", "d"}:
            if not selected:
                console.print("[red]Nothing selected.[/red]")
                continue
            return [selected[i] for i in sorted(selected)]
        if raw in {"all"}:
            return posts
        if raw in {"n", "next"}:
            page = min(page + 1, pages - 1)
            continue
        if raw in {"p", "prev"}:
            page = max(page - 1, 0)
            continue
        # Otherwise parse selection syntax.
        try:
            picked_indices = _parse_selection_syntax(raw, total)
        except ValueError as exc:
            console.print(f"[red]Bad input: {exc}[/red]")
            continue
        for idx in picked_indices:
            selected[idx] = posts[idx - 1]
        console.print(
            f"[green]Added {len(picked_indices)} item(s). "
            f"Total selected: {len(selected)}.[/green]"
        )


def _parse_selection_syntax(raw: str, total: int) -> list[int]:
    """Parse '1,3,5-10' into a list of 1-based indices clamped to ``[1, total]``."""
    indices: list[int] = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            a, b = chunk.split("-", 1)
            start = int(a.strip())
            end = int(b.strip())
            if start > end:
                start, end = end, start
            for i in range(start, end + 1):
                if 1 <= i <= total:
                    indices.append(i)
        else:
            i = int(chunk)
            if 1 <= i <= total:
                indices.append(i)
    return indices


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def _enumerate_posts(
    adapter: BaseAdapter, *, feed_url: str, label: str | None
) -> list[Post]:
    """Flatten ``adapter.iter_pages`` into a plain list."""
    out: list[Post] = []
    for page in adapter.iter_pages(feed_url=feed_url, label=label, max_posts=None):
        out.extend(page.posts)
    return out


def _parse_date_input(value: str) -> datetime | None:
    value = value.strip()
    if not value:
        return None
    try:
        dt = safe_parse_date(value)
    except Exception:
        return None
    # Force tz-aware to make comparisons safe.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def confirm_int(prompt: str, default: int | None = None) -> int | None:
    """Helper retained for backward-compat with tests; uses Rich IntPrompt."""
    try:
        return IntPrompt.ask(prompt, default=default)
    except KeyboardInterrupt:
        return None
