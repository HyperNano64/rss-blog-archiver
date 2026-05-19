"""Centralized logging configuration.

Replaces the scattered `print()` calls and the ad-hoc `error_log.txt` writer
from the original script with a proper logging hierarchy.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)-7s] %(name)s: %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    *,
    level: int = logging.INFO,
    log_file: Path | None = None,
    quiet: bool = False,
) -> logging.Logger:
    """Configure the root logger of the package.

    Parameters
    ----------
    level:
        Minimum log level for the console handler.
    log_file:
        Optional file path. When given, a rotating file handler is attached
        (10 MB per file, 5 backups). Always written in UTF-8.
    quiet:
        If True, suppress all console output (only the file handler stays).
    """
    root = logging.getLogger("rss_blog_archiver")
    root.setLevel(logging.DEBUG)

    # Wipe any handlers from a previous setup call (relevant in tests).
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    if not quiet:
        console = logging.StreamHandler(stream=sys.stderr)
        console.setLevel(level)
        console.setFormatter(formatter)
        root.addHandler(console)

    if log_file is not None:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    # Tame noisy third-party loggers.
    for noisy in ("urllib3", "requests", "feedparser", "ebooklib"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return root


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the package namespace."""
    return logging.getLogger(f"rss_blog_archiver.{name}")
