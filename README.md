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
# 2. Clone & install (WeasyPrint is bundled — PDF works out of the box once GTK is present):
git clone https://github.com/HyperNano64/rss-blog-archiver.git
cd rss-blog-archiver
python -m venv .venv
.venv\Scripts\activate
pip install -e .
#
# (Optional) WeasyPrint needs the GTK 3 runtime on Windows. Quickest path:
#   https://github.com/tschoonj/GTK-for-Windows-Runtime-Environment-Installer/releases
# After installing, restart your shell so the new DLLs are picked up.
#
# (Optional escape hatch) If you'd rather use the legacy wkhtmltopdf binary:
#   1. Install from https://wkhtmltopdf.org/downloads.html  (64-bit installer)
#   2. Run with:  rba <url> --format PDF --pdf-backend wkhtmltopdf
```

### Linux / macOS

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e .
# WeasyPrint extras (system libraries):
sudo apt-get install libpango-1.0-0 libpangoft2-1.0-0 libcairo2   # Debian/Ubuntu
brew install pango cairo                                          # macOS
#
# Optional: keep wkhtmltopdf available as an alternate backend:
sudo apt-get install wkhtmltopdf            # Debian/Ubuntu
brew install --cask wkhtmltopdf             # macOS
```

### PDF backend

The PDF writer for default / novel content modes supports two backends:

| Backend       | What it is                                              | Pros                                       | Cons                                          |
|---------------|---------------------------------------------------------|--------------------------------------------|-----------------------------------------------|
| `weasyprint` (default) | Pure-Python renderer, ships with `pip install`. | No external binary path to chase down.     | Needs the GTK 3 runtime on Windows.           |
| `wkhtmltopdf`          | Legacy binary called via `pdfkit`.              | No Python-side deps once binary is installed. | Hard-coded path issues, harder Windows setup. |

Switch with `--pdf-backend {auto,weasyprint,wkhtmltopdf}`. `auto` picks
WeasyPrint when importable, otherwise falls back to wkhtmltopdf.

The **comic** content mode's PDF output is rendered with Pillow (one image
per page) and is unaffected by `--pdf-backend`.

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
| `--pdf-backend`       | `auto`, `weasyprint`, or `wkhtmltopdf` (default `auto`)  |
| `--prefer-sitemap`    | Use `/sitemap.xml` as primary URL source (before RSS / REST) |
| `-i` / `--interactive`| Rich-based numbered menu before scraping (single URL)    |
| `-t` / `--tui`        | Full-screen Textual TUI before scraping (single URL)     |
| `--async`             | Enable async pipeline (`httpx` + `asyncio`)              |
| `--max-concurrency N` | Per-host in-flight HTTP cap for async mode (default 8)   |
| `--log-file PATH`     | Write rotating UTF-8 log file                            |
| `-v` / `-q`           | Verbose / quiet console                                  |

### Content modes vs. output formats

`--content` decides *what* to extract from each post; `--format` decides
*how* it's written to disk. The two axes are independent.

| Content    | Allowed formats     | Notes                                          |
|------------|---------------------|------------------------------------------------|
| `default`  | `MD`, `TXT`, `EPUB`, `PDF` | Phase 0 behavior                          |
| `novel`    | `MD`, `TXT`, `EPUB`, `PDF` | Strips "Next/Prev Chapter" / "Bab" nav, detects chapter numbers, supports `--combined` |
| `comic`    | `CBZ`, `PDF`              | Images-only; CBZ + PDF emitted per chapter (one chapter per post). PDF uses Pillow — neither WeasyPrint nor wkhtmltopdf is involved. |

### Async pipeline (Phase 3)

The default execution path is synchronous (`requests` + `ThreadPoolExecutor`)
and is identical to Phase 0/1/2 behavior. Adding `--async` switches the
per-post pipeline to `httpx.AsyncClient` + `asyncio`:

- image downloads within a single post run in parallel (one
  `asyncio.Semaphore` per call),
- post HTML fetches across posts run in parallel (one task per post),
- a *per-host* `asyncio.Semaphore` caps total in-flight requests against
  the same blog (`--max-concurrency`, default 8),
- adapter feed iteration stays sync — Atom/REST pagination is sequential
  by nature and parallelizing it gives no real win,
- writers (Pillow, WeasyPrint, ebooklib) stay sync; the scraper offloads
  them to threads via `asyncio.to_thread` so they don't block the loop.

Example:

```bash
rba https://example.blogspot.com/ --content novel --format MD --async --max-concurrency 12
```

### Multi-URL batch (Phase 3)

Pass any number of URLs in one invocation — each is processed sequentially
and gets its own folder under `--output-dir`. A failure on one URL is
logged and the run continues; the overall exit code becomes `1` if any
URL errored. A summary table is printed at the end.

```bash
rba \
  https://kaoritranslation.blogspot.com/ \
  https://another-novel.blogspot.com/ \
  https://manga-scanlation.example.com/ \
  --content novel --format MD,EPUB
```

`-i` / `--interactive` requires exactly one URL — the menu doesn't make
sense for a batch.

### Sitemap-first discovery (Phase 3)

`--prefer-sitemap` promotes the new `SitemapAdapter` to the front of the
CMS detection chain. It walks `/sitemap.xml`, `/sitemap_index.xml`,
`/wp-sitemap.xml`, etc., follows sitemap-index files, and yields every
post URL as a placeholder `Post` (with a slug-derived title). The
content strategy then extracts real titles, dates, and bodies from each
fetched page. Useful when:

- a blog disables / truncates its RSS feed,
- you want the full sitemap rather than the N most-recent posts,
- the CMS is one we don't have a dedicated adapter for yet but follows
  the sitemap standard.

```bash
rba https://feedless-blog.example.com/ --prefer-sitemap --content novel --format MD
```

### Combined CBZ for series (Phase 3)

`--content comic --combined` packs every chapter's images into a single
`.cbz` archive with continuous, zero-padded page numbering across the
entire series. `ComicInfo.xml` at the archive root carries series-level
metadata (`Title`, `Series`, `Writer`, `PageCount`, `Count`,
per-chapter `Notes` manifest).

```bash
rba https://manga-blog.example.com/ \
  --content comic --format CBZ --combined \
  --combined-title "My Series Title"
```

Without `--combined`, comic mode still emits one `.cbz` per post (Phase 1
behavior).

### Full-screen TUI (Phase 3)

`-t` / `--tui` launches a Textual full-screen interface for browsing
labels + titles before scraping. The screen has three panels:

- **Labels** (left): select a label to filter the title list (`Enter`
  applies). Choosing "All posts" clears the filter.
- **Titles** (middle): multi-select with `Space`; `Ctrl+A` selects all
  visible titles, `Ctrl+N` clears the selection.
- **Preview** (right): live metadata for the currently-highlighted
  title (URL, published date, author, labels, summary excerpt).

Press `Enter` from the titles panel to start the scrape with the
selected posts (or with no titles selected to scrape every post under
the active label). `Esc` cancels.

```bash
rba https://kaoritranslation.blogspot.com/ --tui --content novel --format MD,EPUB
```

`-i` (the Phase 1 rich-based menu) stays available for terminals
without full-screen support. `--tui` and `-i` are mutually exclusive,
and both require a single URL.

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

Phase 3 lands in three reviewable PRs:
- **PR #4** — async pipeline (`httpx` + `asyncio`), per-host
  concurrency cap. Opt-in via `--async`. *(merged)*
- **PR #5** — multi-URL batch (`rba url1 url2 ...`),
  sitemap-first discovery (`--prefer-sitemap`), combined CBZ across
  posts (`--content comic --combined`). *(merged)*
- **PR #6 (this PR)** — full-screen Textual TUI (`-t` / `--tui`).

Phase 4 and beyond: Ghost / Substack / Medium adapters, Docker image,
PyPI release.

## License

MIT — see [LICENSE](LICENSE).
