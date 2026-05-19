# rss-blog-archiver

> Modern, robust archiver for **Blogspot** and **WordPress** blogs, with
> dedicated **novel** and **comic / manga** modes. Exports posts as
> PDF / EPUB / Markdown / plain text / CBZ, downloads images, supports
> resume, label filtering, date range filtering, parallel scraping,
> and an interactive picker. Windows-first but fully cross-platform.

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
rba https://example.blogspot.com/ --list-labels

# List every post title with index/date (great with --label / --since / --until)
rba https://example.blogspot.com/ --list-titles --label "Novel A"

# Default mode: archive entire blog as Markdown (Phase 0 behavior)
rba https://example.blogspot.com/ --format MD

# Novel mode + EPUB, combined into a single book
rba https://example.blogspot.com/ \
    --content novel --format EPUB --combined \
    --label "My Novel" \
    --combined-title "My Novel - Full Volume"

# Comic / manga mode: write BOTH .cbz and .pdf per chapter
rba https://komik.example.blogspot.com/ \
    --content comic --format CBZ,PDF \
    --label "Chapter 1"

# Interactive picker (works on top of any of the above):
rba https://example.blogspot.com/ -i --content novel --format EPUB

# Date-range filter
rba https://wp.example.com/ \
    --since 2024-01-01 --until 2024-06-30 \
    --format PDF
```

Run `rba --help` for the full flag list.

### CLI cheatsheet

| Flag                  | Description                                              |
|-----------------------|----------------------------------------------------------|
| `--content`           | `default`, `novel`, or `comic` / `manga` (default `default`) |
| `--format`            | Comma-separated list, e.g. `MD`, `EPUB`, `CBZ,PDF`        |
| `--mode` *(deprecated)* | Alias for single-format `--format`                     |
| `--combined`          | Emit one combined EPUB containing all chapters           |
| `--combined-title`    | Title for the combined EPUB                              |
| `--combined-author`   | Author for the combined EPUB                             |
| `-i` / `--interactive`| Launch the menu-driven picker after blog detection       |
| `--list-labels`       | Print available labels and exit                          |
| `--list-titles`       | Print every post title (numbered) and exit               |
| `--output-dir`        | Base directory (default `./downloaded_posts`)            |
| `--max-posts N`       | Stop after `N` posts                                     |
| `--max-workers N`     | Concurrent post workers (default 5)                      |
| `--label LABEL`       | Filter by label/tag/category                             |
| `--since YYYY-MM-DD`  | Only posts published on/after this date                  |
| `--until YYYY-MM-DD`  | Only posts published on/before this date                 |
| `--no-images`         | Skip image download                                      |
| `--no-resume`         | Disable dedup / resume from `seen_urls.json`             |
| `--rate-limit S`      | Minimum seconds between HTTP calls per host (default 0)  |
| `--timeout C R`       | Connect / read timeout (default `10 30`)                 |
| `--metadata-format`   | `json`, `csv`, or `both`                                 |
| `--log-file PATH`     | Write rotating UTF-8 log file                            |
| `-v` / `-q`           | Verbose / quiet console                                  |

### Content modes vs. output formats

`--content` decides *what* to extract from each post; `--format` decides
*how* it's written to disk. The two axes are independent.

| Content    | Allowed formats     | Notes                                          |
|------------|---------------------|------------------------------------------------|
| `default`  | `MD`, `TXT`, `EPUB`, `PDF` | Phase 0 behavior                          |
| `novel`    | `MD`, `TXT`, `EPUB`, `PDF` | Strips "Next/Prev Chapter" / "Bab" nav, detects chapter numbers, supports `--combined` |
| `comic`    | `CBZ`, `PDF`              | Images-only; CBZ + PDF emitted per chapter (one chapter per post). PDF uses Pillow — no `wkhtmltopdf` needed. |

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

Phase 1 (content modes + interactive picker + combined EPUB + per-host
rate limiter + sitemap fallback). Next on the roadmap:
- Phase 2: WeasyPrint backend for pure-Python PDF, Markdown image alt-text
  fixes, smarter `Retry-After` handling.
- Phase 3: async (`httpx` + `asyncio`), multi-URL batch, Ghost / Substack /
  Medium adapters.

## License

MIT — see [LICENSE](LICENSE).
