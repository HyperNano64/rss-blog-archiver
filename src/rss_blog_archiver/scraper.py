"""Orchestrates the end-to-end scrape: adapter -> feed pages -> per-post
download, image extraction, output write, metadata persistence.

Phase 1 features:
- ``content_mode`` (default / novel / comic) drives extraction strategy
- multiple per-post writers (e.g. CBZ + PDF for comic) in a single run
- optional ``CombinedEpubWriter`` build for novel mode (``--combined``)
- date range filter (``since`` / ``until``)
- explicit post list (from the interactive selector) bypasses pagination
"""

from __future__ import annotations

import csv
import json
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from tqdm import tqdm

from rss_blog_archiver.adapters import BaseAdapter, detect_adapter
from rss_blog_archiver.adapters.base import AdapterDetectionResult
from rss_blog_archiver.content_modes import (
    ContentMode,
    ExtractedContent,
    build_content_mode,
)
from rss_blog_archiver.extractors.images import download_images
from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.state import StateStore
from rss_blog_archiver.utils import host_from_url, sanitize_filename
from rss_blog_archiver.writers import (
    BaseWriter,
    CombinedChapter,
    CombinedEpubWriter,
    WriterContext,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class ScrapeConfig:
    """Runtime configuration for a single scrape run."""

    url: str
    output_dir: Path

    # Content + output
    content_mode: str = "default"  # default | novel | comic
    formats: list[str] = field(default_factory=lambda: ["MD"])
    combined: bool = False
    combined_title: str | None = None
    combined_author: str | None = None

    # Filters
    label: str | None = None
    max_posts: int | None = None
    since: datetime | None = None
    until: datetime | None = None
    explicit_posts: list[Post] | None = None
    """If supplied (e.g. by the interactive selector), skip pagination and
    process only these posts."""

    # Behavior toggles
    download_images: bool = True
    resume: bool = True
    max_workers: int = 5
    timeout: tuple[float, float] = (10.0, 30.0)
    rate_limit_interval: float = 0.0
    metadata_format: str = "json"

    # Back-compat alias for callers still using ``mode``.
    @property
    def mode(self) -> str:
        return self.formats[0] if self.formats else "MD"


class Scraper:
    """End-to-end scrape orchestrator."""

    def __init__(
        self,
        config: ScrapeConfig,
        writers: list[BaseWriter],
        http: HttpClient | None = None,
    ) -> None:
        self.config = config
        self.writers = writers
        self.http = http or HttpClient(
            timeout=config.timeout, rate_limit_interval=config.rate_limit_interval
        )
        self.content_strategy: ContentMode = build_content_mode(config.content_mode)
        self._metadata_lock = threading.Lock()
        self._metadata: list[dict] = []
        self._chapters_lock = threading.Lock()
        self._chapters: list[CombinedChapter] = []

    # ------------------------------------------------------------------
    def run(self) -> None:
        config = self.config
        config.output_dir.mkdir(parents=True, exist_ok=True)

        adapter = detect_adapter(config.url, self.http)
        detection = adapter.detection or AdapterDetectionResult(
            False, 0.0, config.url, config.url
        )
        logger.info(
            "Using %s adapter (confidence=%.2f, feed=%s)",
            adapter.name, detection.confidence, detection.feed_url,
        )

        state_path = config.output_dir / ".rba_state" / "seen_urls.json"
        state = StateStore(state_path)

        site_root = config.output_dir / sanitize_filename(
            host_from_url(detection.base_url or config.url)
        )
        site_root.mkdir(parents=True, exist_ok=True)

        feed_url = detection.feed_url or config.url
        progress = tqdm(desc="posts", unit="post", total=config.max_posts)
        processed = 0

        post_source = self._iter_post_source(adapter, feed_url=feed_url)

        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = []
            for index, post in enumerate(post_source, start=1):
                if config.max_posts is not None and processed >= config.max_posts:
                    break
                if not self._passes_date_filter(post):
                    continue
                if config.resume and state.has(post.url):
                    logger.debug("Skipping seen post %s", post.url)
                    continue
                processed += 1
                futures.append(
                    executor.submit(
                        self._process_post, adapter, post, index, site_root,
                    )
                )

            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    logger.exception("Post processing failed: %s", exc)
                    continue
                if result is not None:
                    state.mark(result["url"], title=result.get("title"))
                progress.update(1)

        progress.close()
        state.save()
        self._persist_metadata(config.output_dir)
        self._maybe_write_combined(site_root)
        logger.info("Done. Processed %d posts.", len(self._metadata))

    # ------------------------------------------------------------------
    # Post source: explicit list, or feed pagination.
    # ------------------------------------------------------------------
    def _iter_post_source(
        self, adapter: BaseAdapter, *, feed_url: str
    ) -> Iterable[Post]:
        config = self.config
        if config.explicit_posts is not None:
            yield from config.explicit_posts
            return

        for page in adapter.iter_pages(
            feed_url=feed_url, label=config.label, max_posts=config.max_posts,
        ):
            yield from page.posts

    def _passes_date_filter(self, post: Post) -> bool:
        config = self.config
        if config.since is None and config.until is None:
            return True
        published = post.published
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        if config.since is not None and published < config.since:
            return False
        return not (config.until is not None and published > config.until)

    # ------------------------------------------------------------------
    # Per-post pipeline.
    # ------------------------------------------------------------------
    def _process_post(
        self,
        adapter: BaseAdapter,
        post: Post,
        index: int,
        site_root: Path,
    ) -> dict | None:
        config = self.config
        post_folder = site_root / sanitize_filename(f"{index:04d} - {post.title}")
        post_folder.mkdir(parents=True, exist_ok=True)

        soup: BeautifulSoup | None = None
        if not post.html:
            soup = adapter.fetch_post_html(post.url)
            if soup is None:
                logger.warning("Could not fetch %s", post.url)
                return None

        extracted: ExtractedContent = self.content_strategy.extract(post, soup)
        if not extracted.html and not extracted.image_urls:
            logger.warning("No content extracted for %s", post.url)
            return None

        images_dir: Path | None = None
        if config.download_images and extracted.image_urls:
            images_dir = post_folder / "images"
            images_dir.mkdir(parents=True, exist_ok=True)
            self._download_post_images(extracted, post.url, images_dir)

        # Run every per-post writer; collect any writer failures but don't
        # let one bad writer kill the whole post.
        writer_errors: list[str] = []
        for writer in self.writers:
            try:
                writer.write(
                    WriterContext(
                        post=post,
                        content_html=extracted.html,
                        output_dir=post_folder,
                        images_dir=images_dir,
                    )
                )
            except Exception as exc:
                writer_errors.append(f"{writer.__class__.__name__}: {exc}")
                logger.warning(
                    "Writer %s failed for %s: %s",
                    writer.__class__.__name__, post.url, exc,
                )

        if config.combined and extracted.html:
            local_images: list[Path] = []
            if images_dir and images_dir.exists():
                local_images = sorted(images_dir.iterdir())
            with self._chapters_lock:
                self._chapters.append(
                    CombinedChapter(
                        post=post,
                        html=extracted.html,
                        image_paths=local_images,
                        chapter_number=extracted.chapter_number,
                    )
                )

        record = {
            "title": post.title,
            "url": post.url,
            "published": post.published.isoformat() if post.published else "",
            "author": post.author,
            "labels": post.labels,
            "chapter_number": extracted.chapter_number,
            "folder": str(post_folder.relative_to(config.output_dir)),
            "image_count": len(extracted.image_urls),
            "has_content": bool(extracted.html),
            "writer_errors": writer_errors,
        }
        with self._metadata_lock:
            self._metadata.append(record)
        return record

    # ------------------------------------------------------------------
    def _download_post_images(
        self, extracted: ExtractedContent, base_url: str, images_dir: Path
    ) -> None:
        """Download images, using a sequence prefix so CBZ ordering is correct.

        For non-comic modes the in-place HTML rewrite (done by
        ``download_images``) handles MD/EPUB image references. For comic
        mode we still call ``download_images`` because it does the dedup +
        streaming logic — we just rely on filename ordering afterward.
        """
        # We re-parse the HTML each time because the extractor may have
        # returned just URLs (comic mode) without producing an HTML element.
        from bs4 import BeautifulSoup as _BS

        if extracted.html:
            soup = _BS(extracted.html, "lxml")
            download_images(
                soup, base_url=base_url, output_dir=images_dir, http=self.http,
            )
            # Persist the rewritten HTML back into the extracted result so
            # downstream writers see the local image paths.
            extracted.html = soup.encode_contents().decode("utf-8") \
                if hasattr(soup, "encode_contents") else str(soup)
            return

        # Comic mode: build a minimal soup from the URL list so the existing
        # download helper can be reused.
        synthetic = _BS(
            "<div>" + "".join(f'<img src="{u}">' for u in extracted.image_urls) + "</div>",
            "lxml",
        )
        download_images(
            synthetic, base_url=base_url, output_dir=images_dir, http=self.http,
        )

    # ------------------------------------------------------------------
    def _maybe_write_combined(self, site_root: Path) -> None:
        """If ``--combined`` was set, emit a single EPUB containing every chapter."""
        config = self.config
        if not config.combined or not self._chapters:
            return
        title = config.combined_title or self._guess_combined_title()
        author = config.combined_author or self._guess_combined_author()
        writer = CombinedEpubWriter()
        try:
            writer.write(
                title=title, author=author, chapters=self._chapters,
                output_dir=site_root,
            )
        except Exception as exc:
            logger.exception("Combined EPUB write failed: %s", exc)

    def _guess_combined_title(self) -> str:
        if self._chapters:
            # Use the most common label (if any) or the host as a fallback.
            labels = [
                lbl for ch in self._chapters for lbl in ch.post.labels
            ]
            if labels:
                from collections import Counter
                most_common, _ = Counter(labels).most_common(1)[0]
                return most_common
        return host_from_url(self.config.url) or "Combined"

    def _guess_combined_author(self) -> str:
        for ch in self._chapters:
            if ch.post.author:
                return ch.post.author
        return ""

    # ------------------------------------------------------------------
    def _persist_metadata(self, output_dir: Path) -> None:
        if not self._metadata:
            return
        fmt = self.config.metadata_format.lower()
        if fmt in {"json", "both"}:
            target = output_dir / "metadata.json"
            with target.open("w", encoding="utf-8") as fh:
                json.dump(self._metadata, fh, indent=2, ensure_ascii=False, default=str)
            logger.info("Wrote %s", target)
        if fmt in {"csv", "both"}:
            target = output_dir / "metadata.csv"
            keys = sorted({k for record in self._metadata for k in record})
            with target.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                for record in self._metadata:
                    row = {
                        k: (",".join(v) if isinstance(v, list) else v)
                        for k, v in record.items()
                    }
                    writer.writerow(row)
            logger.info("Wrote %s", target)
