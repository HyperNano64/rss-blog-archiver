"""Full-screen Textual TUI for browsing labels + titles before a scrape.

The rich-based menu in :mod:`rss_blog_archiver.selector` stays as the
default `-i` experience for terminals that don't support full-screen
apps. The TUI here is opt-in via `--tui`.

Public surface mirrors :func:`rss_blog_archiver.selector.run_interactive_selection`:
both return a :class:`~rss_blog_archiver.selector.SelectionResult` so
the CLI can plug either driver into the scrape pipeline.
"""

from __future__ import annotations

from rss_blog_archiver.tui.app import RbaTuiApp, run_tui_selection

__all__ = ["RbaTuiApp", "run_tui_selection"]
