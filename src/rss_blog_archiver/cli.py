"""Command-line interface for rss-blog-archiver."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from rss_blog_archiver import __version__
from rss_blog_archiver.adapters import detect_adapter
from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger, setup_logging
from rss_blog_archiver.scraper import ScrapeConfig, Scraper
from rss_blog_archiver.selector import SelectionResult, run_interactive_selection
from rss_blog_archiver.utils import safe_parse_date
from rss_blog_archiver.writers import build_writers

logger = get_logger(__name__)

_CONTENT_CHOICES = ("default", "novel", "comic", "manga")
_FORMAT_CHOICES = ("MD", "TXT", "EPUB", "PDF", "CBZ")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rss-blog-archiver",
        description=(
            "Archive Blogspot or WordPress blog posts. Supports novel mode "
            "(text + images) and comic/manga mode (images only as CBZ / PDF)."
        ),
    )
    parser.add_argument("url", nargs="?", help="Blog URL or feed URL")
    parser.add_argument(
        "--output-dir", default="downloaded_posts", type=Path,
        help="Base directory for output (default: ./downloaded_posts)",
    )

    # Content mode + format. ``--mode`` is kept as a Phase 0 alias for ``--format``.
    parser.add_argument(
        "--content", choices=_CONTENT_CHOICES, default="default",
        help=(
            "Content extraction strategy. "
            "'novel' strips nav links and detects chapter numbers; "
            "'comic'/'manga' downloads images only in order (default: default)."
        ),
    )
    parser.add_argument(
        "--format", dest="formats", default=None,
        help=(
            "Comma-separated output formats. "
            "Valid for default/novel: MD, TXT, EPUB, PDF. "
            "Valid for comic/manga: CBZ, PDF (or both: --format CBZ,PDF). "
            "Default: MD (default/novel) or CBZ,PDF (comic)."
        ),
    )
    parser.add_argument(
        "--mode", dest="legacy_mode", choices=["PDF", "TXT", "MD", "EPUB"], default=None,
        help="DEPRECATED: alias for --format (kept for backward compatibility).",
    )
    parser.add_argument(
        "--combined", action="store_true",
        help=(
            "Write a single combined EPUB containing all scraped posts as "
            "chapters (in addition to the per-post output). Best with --content novel."
        ),
    )
    parser.add_argument(
        "--combined-title", default=None,
        help="Title for the --combined EPUB (default: most common label).",
    )
    parser.add_argument(
        "--combined-author", default=None,
        help="Author for the --combined EPUB (default: first post's author).",
    )

    # Interactive + listing.
    parser.add_argument(
        "-i", "--interactive", action="store_true",
        help="Launch an interactive menu after detecting the blog.",
    )
    parser.add_argument(
        "--list-labels", action="store_true",
        help="List available labels/tags for the blog and exit",
    )
    parser.add_argument(
        "--list-titles", action="store_true",
        help=(
            "List all post titles (numbered) for the blog and exit. "
            "Honors --label, --since, --until."
        ),
    )

    # Filters.
    parser.add_argument(
        "--max-posts", type=int, default=None,
        help="Maximum total posts to scrape (default: unlimited)",
    )
    parser.add_argument(
        "--label", default=None,
        help="Only fetch posts matching this label/tag/category",
    )
    parser.add_argument(
        "--since", default=None,
        help="Only include posts published on/after this date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--until", default=None,
        help="Only include posts published on/before this date (YYYY-MM-DD).",
    )

    # Behavior.
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip image download (default: images are downloaded)",
    )
    parser.add_argument(
        "--max-workers", type=int, default=5,
        help="Number of concurrent post workers (default: 5)",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="Re-download posts even if they were scraped in a previous run",
    )
    parser.add_argument(
        "--timeout", type=float, nargs=2, metavar=("CONNECT", "READ"),
        default=(10.0, 30.0), help="Connect/read timeout in seconds (default: 10 30)",
    )
    parser.add_argument(
        "--rate-limit", type=float, default=0.0,
        help="Minimum seconds between HTTP requests per-host (default: 0).",
    )
    parser.add_argument(
        "--metadata-format", choices=["json", "csv", "both"], default="json",
        help="Format for the metadata file (default: json)",
    )
    parser.add_argument(
        "--pdf-backend", choices=["auto", "weasyprint", "wkhtmltopdf"], default="auto",
        help=(
            "PDF rendering backend for default/novel modes. "
            "'weasyprint' is pure-Python (no external binary needed). "
            "'wkhtmltopdf' uses the legacy binary. "
            "'auto' (default) prefers WeasyPrint and falls back to wkhtmltopdf."
        ),
    )
    parser.add_argument(
        "--async", dest="use_async", action="store_true",
        help=(
            "Use the async pipeline (httpx + asyncio). Image downloads "
            "within a post and HTML fetches across posts run concurrently "
            "with a per-host concurrency cap. Default: sync (off)."
        ),
    )
    parser.add_argument(
        "--max-concurrency", type=int, default=8,
        help=(
            "Async pipeline only: maximum number of in-flight HTTP "
            "requests per host (default: 8). Also caps the parallel "
            "image-download fan-out within a single post."
        ),
    )
    parser.add_argument(
        "--log-file", type=Path, default=None,
        help="Append detailed logs to this file (rotating, UTF-8)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="Verbose console output (DEBUG level)",
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="Suppress console output (file logs unaffected)",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(level=level, log_file=args.log_file, quiet=args.quiet)

    if not args.url:
        parser.print_help()
        return 1

    if args.list_labels:
        return _print_labels(args)
    if args.list_titles:
        return _print_titles(args)

    content = "comic" if args.content == "manga" else args.content
    formats = _resolve_formats(args, content)
    if formats is None:
        return 2

    try:
        writers = build_writers(content, formats, pdf_backend=args.pdf_backend)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    since = _parse_date_or_die(args.since, "--since")
    until = _parse_date_or_die(args.until, "--until")
    if since is False or until is False:
        return 2

    config = ScrapeConfig(
        url=args.url,
        output_dir=args.output_dir,
        content_mode=content,
        formats=formats,
        combined=args.combined,
        combined_title=args.combined_title,
        combined_author=args.combined_author,
        download_images=not args.no_images,
        max_workers=args.max_workers,
        max_posts=args.max_posts,
        label=args.label,
        since=since,
        until=until,
        resume=not args.no_resume,
        timeout=tuple(args.timeout),  # type: ignore[arg-type]
        rate_limit_interval=args.rate_limit,
        metadata_format=args.metadata_format,
        use_async=args.use_async,
        max_concurrency=args.max_concurrency,
    )

    if args.interactive:
        try:
            selection = _run_interactive(args, config, content, formats)
        except KeyboardInterrupt:
            logger.warning("Interactive selection cancelled by user")
            return 130
        if selection.cancelled:
            logger.info("Cancelled.")
            return 0
        if selection.posts:
            config.explicit_posts = selection.posts
        if selection.label and not config.label:
            config.label = selection.label
        if selection.since and not config.since:
            config.since = selection.since
        if selection.until and not config.until:
            config.until = selection.until

    scraper = Scraper(config, writers)
    try:
        scraper.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Unrecoverable error: %s", exc)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _resolve_formats(args: argparse.Namespace, content: str) -> list[str] | None:
    """Decide the format list from --format / --mode / defaults."""
    raw = args.formats
    if raw is None and args.legacy_mode is not None:
        raw = args.legacy_mode
    if raw is None:
        return ["CBZ", "PDF"] if content == "comic" else ["MD"]
    parts = [p.strip().upper() for p in raw.split(",") if p.strip()]
    if not parts:
        logger.error("--format must specify at least one format")
        return None
    for part in parts:
        if part not in _FORMAT_CHOICES:
            logger.error(
                "Unknown format %r; valid: %s", part, ", ".join(_FORMAT_CHOICES)
            )
            return None
    return parts


def _parse_date_or_die(raw: str | None, flag: str) -> datetime | None | bool:
    """Return parsed datetime, ``None`` if not supplied, or ``False`` on error."""
    if raw is None:
        return None
    try:
        dt = safe_parse_date(raw)
    except Exception as exc:
        logger.error("Could not parse %s=%r: %s", flag, raw, exc)
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _run_interactive(
    args: argparse.Namespace,
    config: ScrapeConfig,
    content: str,
    formats: list[str],
) -> SelectionResult:
    http = HttpClient(
        timeout=config.timeout, rate_limit_interval=config.rate_limit_interval
    )
    adapter = detect_adapter(config.url, http)
    if adapter.detection is None or not adapter.detection.matched:
        logger.error("Could not detect a supported CMS for %s", config.url)
        return SelectionResult(cancelled=True)
    feed_url = adapter.detection.feed_url
    return run_interactive_selection(
        adapter, feed_url=feed_url, content_mode=content, formats=formats,
    )


def _print_labels(args: argparse.Namespace) -> int:
    http = HttpClient(timeout=tuple(args.timeout), rate_limit_interval=args.rate_limit)
    adapter = detect_adapter(args.url, http)
    if adapter.detection is None or not adapter.detection.matched:
        logger.error("Could not detect a supported CMS for %s", args.url)
        return 2
    labels = adapter.fetch_labels(adapter.detection.base_url)
    if not labels:
        print("No labels found.")
        return 0
    for label in labels:
        print(label)
    return 0


def _print_titles(args: argparse.Namespace) -> int:
    http = HttpClient(timeout=tuple(args.timeout), rate_limit_interval=args.rate_limit)
    adapter = detect_adapter(args.url, http)
    if adapter.detection is None or not adapter.detection.matched:
        logger.error("Could not detect a supported CMS for %s", args.url)
        return 2
    since = _parse_date_or_die(args.since, "--since")
    until = _parse_date_or_die(args.until, "--until")
    if since is False or until is False:
        return 2

    index = 0
    feed_url = adapter.detection.feed_url
    for page in adapter.iter_pages(
        feed_url=feed_url, label=args.label, max_posts=args.max_posts
    ):
        for post in page.posts:
            published = post.published
            if published and published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            if since is not None and published and published < since:
                continue
            if until is not None and published and published > until:
                continue
            index += 1
            date_str = published.date().isoformat() if published else "?"
            print(f"{index:5d}\t{date_str}\t{post.title}\t{post.url}")
    if index == 0:
        print("No posts found.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
