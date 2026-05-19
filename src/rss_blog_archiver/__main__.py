"""Allow running the package as a module: `python -m rss_blog_archiver ...`."""

from __future__ import annotations

from rss_blog_archiver.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
