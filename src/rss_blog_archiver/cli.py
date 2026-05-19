"""Command-line interface for rss-blog-archiver."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rss_blog_archiver import __version__
from rss_blog_archiver.adapters import detect_adapter
from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger, setup_logging
from rss_blog_archiver.scraper import ScrapeConfig, Scraper
from rss_blog_archiver.writers import build_writer

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rss-blog-archiver",
        description=(
            "Archive Blogspot or WordPress blog posts as PDF / TXT / MD / EPUB."
        ),
    )
    parser.add_argument("url", nargs="?", help="Blog URL or feed URL")
    parser.add_argument(
        "--output-dir", default="downloaded_posts", type=Path,
        help="Base directory for output (default: ./downloaded_posts)",
    )
    parser.add_argument(
        "--mode", choices=["PDF", "TXT", "MD", "EPUB"], default="MD",
        help="Output format (default: MD)",
    )
    parser.add_argument(
        "--no-images", action="store_true",
        help="Skip image download (default: images are downloaded)",
    )
    parser.add_argument(
        "--max-posts", type=int, default=None,
        help="Maximum total posts to scrape (default: unlimited)",
    )
    parser.add_argument(
        "--max-workers", type=int, default=5,
        help="Number of concurrent post workers (default: 5)",
    )
    parser.add_argument(
        "--label", default=None,
        help="Only fetch posts matching this label/tag/category",
    )
    parser.add_argument(
        "--list-labels", action="store_true",
        help="List available labels/tags for the blog and exit",
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
        help="Minimum seconds between HTTP requests (default: 0, no throttle)",
    )
    parser.add_argument(
        "--metadata-format", choices=["json", "csv", "both"], default="json",
        help="Format for the metadata file (default: json)",
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

    try:
        writer = build_writer(args.mode)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    config = ScrapeConfig(
        url=args.url,
        output_dir=args.output_dir,
        mode=args.mode,
        download_images=not args.no_images,
        max_workers=args.max_workers,
        max_posts=args.max_posts,
        label=args.label,
        resume=not args.no_resume,
        timeout=tuple(args.timeout),  # type: ignore[arg-type]
        rate_limit_interval=args.rate_limit,
        metadata_format=args.metadata_format,
    )
    scraper = Scraper(config, writer)
    try:
        scraper.run()
    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
        return 130
    except Exception as exc:
        logger.exception("Unrecoverable error: %s", exc)
        return 1
    return 0


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


if __name__ == "__main__":
    sys.exit(main())
