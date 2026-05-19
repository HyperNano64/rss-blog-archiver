"""Domain dataclasses shared across adapters and writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class Post:
    """A normalized representation of a single blog post, independent of CMS."""

    title: str
    url: str
    published: datetime
    html: str = ""
    """Raw HTML of the *main content* (already extracted from the post body)."""

    summary: str = ""
    author: str = ""
    labels: list[str] = field(default_factory=list)
    comments_feed: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)
    """CMS-specific extras (e.g. Blogger entry ID, WordPress post ID)."""


@dataclass(slots=True)
class FeedPage:
    """One page of results returned by an adapter's pagination iterator."""

    posts: list[Post]
    next_cursor: str | int | None = None
    """Opaque cursor for the next page; ``None`` means end-of-feed."""
