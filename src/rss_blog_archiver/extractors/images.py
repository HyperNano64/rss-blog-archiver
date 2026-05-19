"""Image discovery and download with lazy-load support.

Improvements over the original script:
- Discovers ``data-src``, ``data-lazy-src``, ``data-original``, and parses
  ``srcset`` to pick the highest-resolution candidate.
- Verifies image type via HTTP ``Content-Type`` (and the URL extension as a
  fallback) BEFORE writing to disk.
- Prepends an 8-char hash to filenames to avoid collisions between two
  different URLs that share a basename.
- Streams large images instead of buffering in memory.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup, Tag

from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_url_to_filename

logger = get_logger(__name__)

_LAZY_ATTRS: tuple[str, ...] = ("data-src", "data-lazy-src", "data-original", "data-img")

_OK_IMAGE_EXTS: frozenset[str] = frozenset(
    {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg", ".avif"}
)

_OK_IMAGE_MIME_PREFIX = "image/"


def _best_image_url(img: Tag) -> str | None:
    """Pick the best image URL from an ``<img>`` tag."""
    for attr in _LAZY_ATTRS:
        val = img.get(attr)
        if val and isinstance(val, str) and val.strip():
            return val.strip()

    srcset = img.get("srcset")
    if isinstance(srcset, str) and srcset.strip():
        # Pick the candidate with the largest declared width.
        best_url: str | None = None
        best_width = -1
        for chunk in srcset.split(","):
            parts = chunk.strip().split()
            if not parts:
                continue
            url = parts[0]
            width = 0
            if len(parts) > 1 and parts[1].endswith("w"):
                try:
                    width = int(parts[1][:-1])
                except ValueError:
                    width = 0
            if width > best_width:
                best_width = width
                best_url = url
        if best_url:
            return best_url

    src = img.get("src")
    if isinstance(src, str) and src.strip():
        return src.strip()
    return None


def discover_image_urls(content: Tag | BeautifulSoup, base_url: str) -> list[str]:
    """Return absolute image URLs found inside *content*."""
    urls: list[str] = []
    seen: set[str] = set()
    for img in content.find_all("img"):
        candidate = _best_image_url(img)
        if not candidate:
            continue
        # Skip data: URIs entirely — they are typically placeholders.
        if candidate.startswith("data:"):
            continue
        absolute = urljoin(base_url, candidate)
        if absolute in seen:
            continue
        seen.add(absolute)
        urls.append(absolute)
    return urls


def _is_image_response(response: requests.Response, url: str) -> bool:
    content_type = response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
    if content_type.startswith(_OK_IMAGE_MIME_PREFIX):
        return True
    ext = Path(url).suffix.lower()
    return ext in _OK_IMAGE_EXTS


def download_images(
    content: Tag | BeautifulSoup,
    *,
    base_url: str,
    output_dir: Path,
    http: HttpClient,
    rewrite_src: bool = True,
) -> dict[str, Path]:
    """Download every image found in *content* into *output_dir*.

    Returns a mapping ``{absolute_url: local_path}`` for the images that were
    successfully downloaded. Optionally rewrites the ``src`` attribute of each
    ``<img>`` tag in-place so that subsequent writers (PDF, EPUB) reference
    the local file.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    downloaded: dict[str, Path] = {}

    for img in content.find_all("img"):
        candidate = _best_image_url(img)
        if not candidate or candidate.startswith("data:"):
            continue
        absolute = urljoin(base_url, candidate)

        # Cheap pre-check using URL extension. Reject obvious non-images.
        ext = Path(absolute).suffix.lower()
        if ext and ext not in _OK_IMAGE_EXTS:
            logger.debug("Skipping non-image URL %s", absolute)
            continue

        local = downloaded.get(absolute)
        if local is None:
            local = _download_one(absolute, output_dir, http)
            if local is None:
                continue
            downloaded[absolute] = local

        if rewrite_src:
            img["src"] = local.name
            # Clear lazy attrs so writers don't see stale values.
            for attr in _LAZY_ATTRS:
                if attr in img.attrs:
                    del img.attrs[attr]
            if "srcset" in img.attrs:
                del img.attrs["srcset"]
    return downloaded


def _download_one(url: str, output_dir: Path, http: HttpClient) -> Path | None:
    try:
        response = http.get(url, stream=True)
    except requests.RequestException as exc:
        logger.warning("Error fetching image %s: %s", url, exc)
        return None

    if not response.ok:
        logger.warning("Image %s returned HTTP %d", url, response.status_code)
        response.close()
        return None

    if not _is_image_response(response, url):
        logger.debug("Skipping non-image content at %s (%s)",
                     url, response.headers.get("Content-Type"))
        response.close()
        return None

    # Use the response's Content-Type to guess an extension when the URL had
    # none (common for CDN URLs).
    ext = Path(url).suffix.lower()
    if ext not in _OK_IMAGE_EXTS:
        guessed = mimetypes.guess_extension(
            response.headers.get("Content-Type", "").split(";", 1)[0].strip()
        )
        ext = guessed.lower() if guessed else ".bin"

    filename = sanitize_url_to_filename(url, default_ext=ext)
    if not filename.lower().endswith(ext):
        filename = f"{filename}{ext}"
    target = output_dir / filename

    try:
        with target.open("wb") as fh:
            for chunk in response.iter_content(chunk_size=64 * 1024):
                if chunk:
                    fh.write(chunk)
    except OSError as exc:
        logger.warning("Failed to write image %s: %s", target, exc)
        response.close()
        return None
    response.close()
    logger.debug("Downloaded image %s -> %s", url, target)
    return target
