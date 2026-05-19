"""Generic helpers: filename / URL sanitization, safe date parsing, hashing."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from dateutil import parser as date_parser

from rss_blog_archiver.logging_setup import get_logger

logger = get_logger(__name__)

# Characters illegal in filenames on Windows. POSIX is more permissive but we
# strip the same set everywhere for portability.
_ILLEGAL_FILENAME = re.compile(r'[\\/:*?"<>|\x00-\x1f]')

# Reserved device names on Windows (case-insensitive, no extension).
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Safe default for cross-platform filename length. Windows MAX_PATH is 260
# overall, NTFS allows 255 per component. We aim well below to leave room for
# parent paths.
_FILENAME_MAX = 150


def sanitize_filename(name: str, *, max_length: int = _FILENAME_MAX) -> str:
    """Return a filesystem-safe filename derived from *name*.

    - Strips illegal characters (Windows + control chars).
    - Normalizes Unicode (NFC).
    - Collapses repeated whitespace.
    - Trims trailing dots/spaces (illegal on Windows).
    - Avoids reserved Windows device names.
    - Truncates to ``max_length`` characters, preserving any extension.
    - Falls back to a hashed placeholder when the result would be empty.
    """
    if not name:
        return _hash_placeholder("")

    cleaned = unicodedata.normalize("NFC", name)
    # Replace control chars (which match _ILLEGAL_FILENAME) with a space FIRST so
    # things like "hello\nworld" become "hello world" instead of "helloworld".
    cleaned = re.sub(r"[\x00-\x1f\t\n\r]", " ", cleaned)
    cleaned = _ILLEGAL_FILENAME.sub("", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.rstrip(". ")

    if not cleaned:
        return _hash_placeholder(name)

    stem, dot, ext = cleaned.rpartition(".")
    if dot and 0 < len(ext) <= 8 and "." not in ext:
        # Preserve extension; truncate the stem.
        stem_max = max(1, max_length - len(ext) - 1)
        cleaned = f"{stem[:stem_max].rstrip('. ')}.{ext}"
    else:
        cleaned = cleaned[:max_length].rstrip(". ")

    # Avoid reserved Windows device names (compared without extension).
    stem_only = cleaned.split(".", 1)[0].upper()
    if stem_only in _WINDOWS_RESERVED:
        cleaned = f"_{cleaned}"

    return cleaned or _hash_placeholder(name)


def sanitize_url_to_filename(url: str, *, default_ext: str = "") -> str:
    """Derive a filesystem-safe filename from a URL's path component.

    To avoid collisions between two different URLs that share a basename (e.g.
    ``/a/foo.jpg`` and ``/b/foo.jpg``), an 8-char hash of the full URL is
    prepended.
    """
    path = urlsplit(url).path
    basename = Path(path).name
    if not basename:
        basename = f"file{default_ext}"
    prefix = hashlib.sha1(url.encode("utf-8")).hexdigest()[:8]
    return sanitize_filename(f"{prefix}_{basename}")


def _hash_placeholder(seed: str) -> str:
    digest = hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()
    return f"untitled_{digest[:8]}"


def safe_parse_date(value: str | None) -> datetime:
    """Parse a date string with best-effort fallback to ``datetime.now``."""
    if not value:
        return datetime.now(tz=timezone.utc)
    try:
        parsed = date_parser.parse(value)
    except (ValueError, TypeError, OverflowError) as exc:
        logger.debug("Failed to parse date %r: %s", value, exc)
        return datetime.now(tz=timezone.utc)

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def host_from_url(url: str) -> str:
    """Return the network host of *url* with any ``www.`` prefix stripped."""
    host = urlsplit(url).netloc
    if host.startswith("www."):
        host = host[4:]
    return host or "unknown_host"


def is_blogspot_host(url: str) -> bool:
    """Return True if *url*'s host is on the well-known Blogspot domain.

    Note: this does NOT detect custom-domain Blogspot blogs. Custom domains
    are detected by inspecting the served HTML / feed (handled by the
    Blogspot adapter).
    """
    host = host_from_url(url)
    return host.endswith("blogspot.com") or host.endswith("blogger.com")
