"""Async batch image downloader.

Drop-in for the per-post image step in :mod:`rss_blog_archiver.scraper`
when the async pipeline is enabled. Reuses the same URL-discovery,
filename-sanitization, and content-type-checking heuristics as the sync
:mod:`rss_blog_archiver.extractors.images` module so the two paths
produce identical artifacts.

Concurrency model:
- One :class:`asyncio.Semaphore` per call (size = ``concurrency``).
- All discovered images for a post are downloaded in parallel within
  that semaphore.
- The :class:`AsyncHttpClient` itself adds a *per-host* semaphore on top,
  so a 50-image post on one CDN never sees more than
  ``--max-concurrency`` in-flight requests to that host.
"""

from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from rss_blog_archiver.async_http_client import AsyncHttpClient
from rss_blog_archiver.extractors.images import _LAZY_ATTRS, _OK_IMAGE_EXTS, _best_image_url
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_url_to_filename

logger = get_logger(__name__)

_OK_IMAGE_MIME_PREFIX = "image/"


def _is_image_response(response: httpx.Response, url: str) -> bool:
    content_type = (
        response.headers.get("Content-Type", "").lower().split(";", 1)[0].strip()
    )
    if content_type.startswith(_OK_IMAGE_MIME_PREFIX):
        return True
    ext = Path(url).suffix.lower()
    return ext in _OK_IMAGE_EXTS


async def download_images_async(
    content: Tag | BeautifulSoup,
    *,
    base_url: str,
    output_dir: Path,
    http: AsyncHttpClient,
    concurrency: int = 8,
    rewrite_src: bool = True,
) -> dict[str, Path]:
    """Download every ``<img>`` in *content* concurrently.

    Mirrors :func:`rss_blog_archiver.extractors.images.download_images`
    but issues all GETs in parallel. Returns ``{absolute_url: local_path}``
    for the images that were successfully written to disk.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Walk the DOM once to build (absolute_url -> list[<img>]) so we can
    # rewrite every reference to the same image after a single download.
    refs: dict[str, list[Tag]] = {}
    for img in content.find_all("img"):
        candidate = _best_image_url(img)
        if not candidate or candidate.startswith("data:"):
            continue
        absolute = urljoin(base_url, candidate)
        ext = Path(absolute).suffix.lower()
        if ext and ext not in _OK_IMAGE_EXTS:
            logger.debug("Skipping non-image URL %s", absolute)
            continue
        refs.setdefault(absolute, []).append(img)

    if not refs:
        return {}

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def fetch(url: str) -> tuple[str, Path | None]:
        async with sem:
            return url, await _download_one(url, output_dir, http)

    results = await asyncio.gather(*(fetch(u) for u in refs))

    downloaded: dict[str, Path] = {}
    for url, path in results:
        if path is None:
            continue
        downloaded[url] = path
        if rewrite_src:
            for img in refs[url]:
                img["src"] = path.name
                for attr in _LAZY_ATTRS:
                    if attr in img.attrs:
                        del img.attrs[attr]
                if "srcset" in img.attrs:
                    del img.attrs["srcset"]
    return downloaded


async def _download_one(
    url: str, output_dir: Path, http: AsyncHttpClient
) -> Path | None:
    try:
        response = await http.get(url)
    except httpx.HTTPError as exc:
        logger.warning("Error fetching image %s: %s", url, exc)
        return None

    if response.status_code >= 400:
        logger.warning("Image %s returned HTTP %d", url, response.status_code)
        return None

    if not _is_image_response(response, url):
        logger.debug(
            "Skipping non-image content at %s (%s)",
            url, response.headers.get("Content-Type"),
        )
        return None

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
        target.write_bytes(response.content)
    except OSError as exc:
        logger.warning("Failed to write image %s: %s", target, exc)
        return None
    logger.debug("Downloaded image %s -> %s", url, target)
    return target
