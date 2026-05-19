"""Persistent state for resume / dedup of scraped posts.

Stores a tiny JSON file mapping seen post URLs to a timestamp. Allows the
scraper to skip already-downloaded posts on subsequent runs.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rss_blog_archiver.logging_setup import get_logger

logger = get_logger(__name__)


class StateStore:
    """Simple JSON-backed key/value store of seen post URLs."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"seen_urls": {}, "version": 1}
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to read state %s: %s — starting fresh", self.path, exc)
            return {"seen_urls": {}, "version": 1}
        data.setdefault("seen_urls", {})
        data.setdefault("version", 1)
        return data

    def has(self, url: str) -> bool:
        with self._lock:
            return url in self._data["seen_urls"]

    def mark(self, url: str, *, title: str | None = None) -> None:
        with self._lock:
            self._data["seen_urls"][url] = {
                "title": title,
                "ts": datetime.now(tz=timezone.utc).isoformat(),
            }

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            tmp.replace(self.path)
            logger.debug("State saved: %d seen URLs -> %s", len(self._data["seen_urls"]), self.path)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data["seen_urls"])
