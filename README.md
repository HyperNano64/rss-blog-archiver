# rss-blog-archiver

> Modern, robust archiver for **Blogspot** and **WordPress** blogs.
> Exports posts as PDF / EPUB / Markdown / plain text, downloads images,
> supports resume, label filtering, and parallel scraping. Windows-first
> but fully cross-platform.

## Why

This project is a from-scratch rewrite of a 2-year-old hobby script that
scraped Blogspot / WordPress / Joomla / Drupal feeds. The original worked
well in 2023 but accumulated a long list of bugs (deprecated `urllib3` API,
hard-coded Windows wkhtmltopdf path, faked Markdown output, missing timeouts,
broken pagination for non-Blogspot sites, lazy-loaded images missed, ...).

`rss-blog-archiver` keeps the original's spirit but:
- focuses on the two CMSes that matter most (Blogspot + WordPress),
- exploits Blogger's GData feeds API in depth (JSON output, label filter,
  static pages, comments, pagination cap of 500 entries/request),
- uses WordPress's REST API first, RSS as fallback,
- ships proper output writers (real Markdown via `markdownify`, real text via
  `html2text`, EPUB with UUID identifier and correct mime per image, PDF
  with auto-detected `wkhtmltopdf`),
- adds resume / dedup, rate limiting, `tqdm` progress, and a real CLI.

## Installation

### Windows (priority platform)

```powershell
# 1. Install Python 3.12 from https://www.python.org/downloads/
# 2. Install wkhtmltopdf (only if you want PDF output):
#    https://wkhtmltopdf.org/downloads.html  (pick the 64-bit installer)
# 3. Clone & install:
git clone https://github.com/HyperNano64/rss-blog-archiver.git
cd rss-blog-archiver
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

### Linux / macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
# For PDF output:
sudo apt-get install wkhtmltopdf            # Debian/Ubuntu
brew install --cask wkhtmltopdf             # macOS
```

## Usage

```bash
# Show available labels first
rss-blog-archiver https://example.blogspot.com/ --list-labels

# Archive entire blog as Markdown
rss-blog-archiver https://example.blogspot.com/ --mode MD

# Archive only posts under a specific label, as EPUB, with images
rss-blog-archiver https://example.blogspot.com/ \
    --mode EPUB \
    --label "Indonesia" \
    --max-posts 50

# WordPress site, PDF output, polite rate limit
rss-blog-archiver https://wp.example.com/ \
    --mode PDF \
    --rate-limit 1.5 \
    --output-dir D:\Archive\wp_example
```

Run `rss-blog-archiver --help` for the full flag list.

### CLI cheatsheet

| Flag                  | Description                                              |
|-----------------------|----------------------------------------------------------|
| `--mode`              | `PDF`, `TXT`, `MD`, `EPUB` (default `MD`)                |
| `--output-dir`        | Base directory (default `./downloaded_posts`)            |
| `--max-posts N`       | Stop after `N` posts                                     |
| `--max-workers N`     | Concurrent post workers (default 5)                      |
| `--label LABEL`       | Filter by label/tag/category                             |
| `--list-labels`       | Print available labels and exit                          |
| `--no-images`         | Skip image download                                      |
| `--no-resume`         | Disable dedup / resume from `seen_urls.json`             |
| `--rate-limit S`      | Minimum seconds between HTTP calls (default 0)           |
| `--timeout C R`       | Connect / read timeout (default `10 30`)                 |
| `--metadata-format`   | `json`, `csv`, or `both`                                 |
| `--log-file PATH`     | Write rotating UTF-8 log file                            |
| `-v` / `-q`           | Verbose / quiet console                                  |

## How the Blogspot adapter works

Blogger's GData feeds endpoint (`/feeds/posts/default`) is the richest
data source available:
- `?alt=json&v=2` returns JSON instead of Atom (faster to parse).
- `start-index=N&max-results=500` paginates with up to 500 entries per
  request — Blogger caps it at 500.
- `/-/{label}` filters by a single label, e.g.
  `/feeds/posts/default/-/Python%20Tutorial`.
- `/feeds/pages/default` for the (typically small) set of *static pages*.
- `/feeds/comments/default` for all comments on the blog.

Detection covers both `*.blogspot.com` and custom domains (we check
`<meta name="generator">` plus probe the feed endpoint).

## How the WordPress adapter works

We use the WP REST API at `/wp-json/wp/v2/posts` whenever available:
- `per_page=100&page=N` is the supported pagination.
- `X-WP-TotalPages` header tells us when to stop.
- `_embed=true` brings author + categories + tags inline.
- Falls back to classic RSS at `/feed/` (with `/page/N/feed/` pagination)
  when the REST API is blocked or absent.

## Development

```bash
pip install -e .[dev]
ruff check src tests
pytest
```

CI runs on Ubuntu and Windows for Python 3.10, 3.11, and 3.12.

## Status

Phase 0 (stabilization + Blogspot/WordPress focus). Next phases on the
roadmap:
- Phase 1: rate-limit policy per host, sitemap fallback, smarter
  `Retry-After` handling.
- Phase 2: WeasyPrint backend for pure-Python PDF, multi-post EPUB book,
  Markdown image alt-text fixes.
- Phase 3: async (`httpx` + `asyncio`), multi-URL batch, date filters,
  Ghost / Substack / Medium adapters.

## License

MIT — see [LICENSE](LICENSE).
