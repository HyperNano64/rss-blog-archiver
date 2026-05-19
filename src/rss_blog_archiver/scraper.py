"""Orchestrates the end-to-end scrape: adapter -> feed pages -> per-post
download, image extraction, output write, and metadata persistence."""

from __future__ import annotations

import csv
import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from bs4 import BeautifulSoup
from tqdm import tqdm

from rss_blog_archiver.adapters import BaseAdapter, detect_adapter
from rss_blog_archiver.adapters.base import AdapterDetectionResult
from rss_blog_archiver.extractors.content import extract_main_content, strip_noise
from rss_blog_archiver.extractors.images import download_images
from rss_blog_archiver.http_client import HttpClient
from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.models import Post
from rss_blog_archiver.state import StateStore
from rss_blog_archiver.utils import host_from_url, sanitize_filename
from rss_blog_archiver.writers import BaseWriter, WriterContext

logger = get_logger(__name__)


@dataclass(slots=True)
class ScrapeConfig:
    url: str
    output_dir: Path
    mode: str = "MD"
    download_images: bool = True
    max_workers: int = 5
    max_posts: int | None = None
    label: str | None = None
    resume: bool = True
    timeout: tuple[float, float] = (10.0, 30.0)
    rate_limit_interval: float = 0.0
    metadata_format: str = "json"  # json | csv | both


class Scraper:
    def __init__(self, config: ScrapeConfig, writer: BaseWriter, http: HttpClient | None = None) -> None:
        self.config = config
        self.writer = writer
        self.http = http or HttpClient(
            timeout=config.timeout, rate_limit_interval=config.rate_limit_interval
        )
        self._metadata_lock = threading.Lock()
        self._metadata: list[dict] = []

    # ------------------------------------------------------------------
    def run(self) -> None:
        config = self.config
        config.output_dir.mkdir(parents=True, exist_ok=True)

        adapter = detect_adapter(config.url, self.http)
        detection = adapter.detection or AdapterDetectionResult(False, 0.0, config.url, config.url)
        logger.info(
            "Using %s adapter (confidence=%.2f, feed=%s)",
            adapter.name, detection.confidence, detection.feed_url,
        )

        state_path = config.output_dir / ".rba_state" / "seen_urls.json"
        state = StateStore(state_path)

        site_root = config.output_dir / sanitize_filename(host_from_url(detection.base_url or config.url))
        site_root.mkdir(parents=True, exist_ok=True)

        feed_url = detection.feed_url or config.url
        post_counter = 0
        progress = tqdm(desc="posts", unit="post", total=config.max_posts)

        with ThreadPoolExecutor(max_workers=config.max_workers) as executor:
            futures = []
            for page in adapter.iter_pages(
                feed_url=feed_url, label=config.label, max_posts=config.max_posts
            ):
                for post in page.posts:
                    if config.resume and state.has(post.url):
                        logger.debug("Skipping seen post %s", post.url)
                        continue
                    post_counter += 1
                    futures.append(
                        executor.submit(
                            self._process_post,
                            adapter, post, post_counter, site_root,
                        )
                    )
                    if config.max_posts is not None and post_counter >= config.max_posts:
                        break
                if config.max_posts is not None and post_counter >= config.max_posts:
                    break

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
        logger.info("Done. Processed %d posts.", len(self._metadata))

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

        # Prefer HTML from the feed (Blogspot includes it). Otherwise fetch
        # the rendered page and run our heuristic extractor.
        if post.html:
            soup = BeautifulSoup(post.html, "lxml")
            content_element = soup
        else:
            soup = adapter.fetch_post_html(post.url)
            if soup is None:
                logger.warning("Could not fetch %s", post.url)
                return None
            element = extract_main_content(soup)
            if element is None:
                logger.warning("No main content found for %s", post.url)
                return None
            content_element = strip_noise(element)

        images_dir: Path | None = None
        if config.download_images:
            images_dir = post_folder / "images"
            download_images(
                content_element, base_url=post.url,
                output_dir=images_dir, http=self.http,
            )

        # Use the (possibly rewritten) HTML as final content.
        if hasattr(content_element, "encode_contents"):
            html = content_element.encode_contents().decode("utf-8")
        else:
            html = str(content_element)

        try:
            self.writer.write(
                WriterContext(
                    post=post,
                    content_html=html,
                    output_dir=post_folder,
                    images_dir=images_dir,
                )
            )
        except Exception as exc:
            logger.warning("Writer failed for %s: %s", post.url, exc)

        record = {
            "title": post.title,
            "url": post.url,
            "published": post.published.isoformat(),
            "author": post.author,
            "labels": post.labels,
            "folder": str(post_folder.relative_to(config.output_dir)),
            "has_content": bool(html),
        }
        with self._metadata_lock:
            self._metadata.append(record)
        return record

    # ------------------------------------------------------------------
    def _persist_metadata(self, output_dir: Path) -> None:
        if not self._metadata:
            return
        fmt = self.config.metadata_format.lower()
        if fmt in {"json", "both"}:
            target = output_dir / "metadata.json"
            with target.open("w", encoding="utf-8") as fh:
                json.dump(self._metadata, fh, indent=2, ensure_ascii=False)
            logger.info("Wrote %s", target)
        if fmt in {"csv", "both"}:
            target = output_dir / "metadata.csv"
            keys = sorted({k for record in self._metadata for k in record})
            with target.open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
                writer.writeheader()
                for record in self._metadata:
                    row = {k: (",".join(v) if isinstance(v, list) else v) for k, v in record.items()}
                    writer.writerow(row)
            logger.info("Wrote %s", target)
