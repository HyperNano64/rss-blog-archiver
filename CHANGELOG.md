# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) loosely; each
"Phase" is a milestone-shaped release.

## [0.5.0] - Phase 3 PR #5: multi-URL batch + sitemap-first + combined CBZ

### Added
- **Multi-URL batch.** `rba url1 url2 url3 …` processes each URL
  sequentially in one invocation. A summary table is printed at the
  end (`ok` / `error: ClassName` / `interrupted`, posts processed,
  URL). A failure in one URL is logged but does NOT stop later URLs;
  the overall exit code becomes `1` if any URL errored.
- **`--prefer-sitemap` flag + `SitemapAdapter`.** When set, the new
  adapter is tried first in `detect_adapter()`, using `/sitemap.xml`
  / `/sitemap_index.xml` / `/wp-sitemap.xml` etc. as the primary post
  URL source. Useful for blogs whose RSS feed is empty, truncated, or
  disabled. Placeholder titles are derived from URL slugs; the
  content strategy then extracts real titles from each fetched page.
- **Combined CBZ for `--content comic --combined`.** New
  `CombinedCbzWriter` packs every chapter's images into a single
  `.cbz` with continuous, zero-padded page numbering (`00001.jpg` …
  `99999.jpg`) lexically sortable across the whole series.
  `ComicInfo.xml` at the archive root carries series-level
  metadata (`Title` / `Series` / `Writer` / `PageCount` / `Count` /
  per-chapter manifest in `Notes`).

### Changed
- CLI positional `url` → `urls` (`nargs="*"`). Single-URL invocations
  remain backwards-compatible. `-i / --interactive` now requires
  exactly one URL.
- `Scraper` exposes a read-only `metadata` property used by the CLI
  to populate the batch summary.
- `_maybe_write_combined()` now picks the writer based on
  `content_mode`: comic → `CombinedCbzWriter`, novel/default →
  `CombinedEpubWriter`.

### Tests
- 28 new tests (`test_combined_cbz.py`, `test_sitemap_adapter.py`,
  `test_cli_multiurl.py`, plus extra cases in `test_scraper.py`).
  Total test count: 154 (126 before, all still passing).

## [0.4.0] - Phase 3 PR #4: async pipeline

### Added
- **Async pipeline** built on `httpx.AsyncClient` + `asyncio`, opt-in
  via `--async`. The default sync path is unchanged so existing
  invocations behave identically.
- `AsyncHttpClient` mirrors the sync `HttpClient` API: retry on
  429/5xx, exponential backoff with jitter, explicit `Retry-After`
  header honor (numeric seconds or HTTP-date), spoofed User-Agent.
- `AsyncRateLimiter` (per-host) replaces blocking `time.sleep` with
  `asyncio.sleep` so the event loop stays free.
- `HostSemaphorePool` — one `asyncio.Semaphore` per host, sized by
  `--max-concurrency` (default 8). Caps total in-flight requests
  against the same blog regardless of how many tasks fan out.
- `download_images_async()` — concurrent per-post image download with
  dedup of duplicate URLs and atomic `<img src>` rewrite.
- `Scraper.run_async()` — concurrent post processing. Adapter feed
  iteration stays sync (sequential by nature); per-post HTML fetches
  and sync writers run via `asyncio.to_thread` so they don't block
  the loop.
- CLI flags `--async` (opt-in) and `--max-concurrency N` (default 8,
  ignored in sync mode).

### Changed
- Added `httpx>=0.27.0` to runtime dependencies.
- Version bumped to `0.4.0`.

### Tests
- 19 new tests (`test_async_http_client.py`,
  `test_async_image_downloader.py`) using `httpx.MockTransport`. Total
  test count: 126 (107 before, all still passing).

## [0.3.0] - Phase 2: pure-Python PDF + Markdown alt-text fix

### Added
- **WeasyPrint PDF backend** — pure-Python renderer, no external binary
  required. WeasyPrint is now a hard runtime dependency and is the
  default backend for `--format PDF` in `default` / `novel` content
  modes.
- `--pdf-backend {auto,weasyprint,wkhtmltopdf}` CLI flag.
  - `auto` (default): prefers WeasyPrint, falls back to wkhtmltopdf if
    WeasyPrint is unavailable. If neither is available the writer emits
    a clear, actionable error message instead of crashing.
  - `weasyprint`: force pure-Python rendering.
  - `wkhtmltopdf`: force the legacy `wkhtmltopdf` binary path.
- Markdown writer now fills in **image alt text** before invoking
  `markdownify`. Order of precedence:
  1. existing non-empty `alt` attribute,
  2. `title` attribute,
  3. text content of the enclosing `<figure>`'s `<figcaption>`,
  4. humanized version of the image filename (handles `src`,
     `data-src`, `data-lazy-src`, `data-original`).
  `[` and `]` characters are escaped so the resulting
  `![alt](url)` syntax stays well-formed.

### Changed
- `PdfWriter` is now backend-aware: same class for both backends; the
  HTML template and CSS are shared. CSS now declares an `@page` rule so
  WeasyPrint paginates A4 by default.
- `build_writers(content, formats, *, pdf_backend="auto")` and
  `build_writer(mode, *, pdf_backend="auto")` accept an optional
  keyword-only `pdf_backend` argument. The comic-PDF writer is
  unaffected (it uses Pillow internally).
- Bumped runtime requirement: `weasyprint>=60.0`.

### Notes
- On Windows, WeasyPrint relies on the GTK 3 runtime (Pango / Cairo).
  See https://doc.courtbouillon.org/weasyprint/stable/first_steps.html
  for the recommended install. If you would rather not install GTK,
  pass `--pdf-backend wkhtmltopdf` to keep the existing flow.
- `pdfkit` and `wkhtmltopdf` are still bundled / supported — switching
  backends is a single CLI flag away.

## [0.2.0] - Phase 1: content modes, interactive picker, sitemap, combined EPUB

### Added
- `--content {default,novel,comic,manga}` content extraction strategies:
  - **novel**: strips "Previous Chapter" / "Next Chapter" / "Bab Sebelumnya"
    style navigation links and detects chapter numbers from titles.
  - **comic** / **manga**: image-only mode; ignores body text entirely and
    keeps every `<img>` in document order.
- `--format` accepts a comma-separated list (e.g. `--format CBZ,PDF`) so a
  single run can produce multiple output artifacts per post.
- New writers:
  - `CbzWriter` — packs the post's images into a `.cbz` with a numbered
    `001.jpg, 002.jpg, ...` layout plus a `ComicInfo.xml` metadata file.
  - `ComicPdfWriter` — one-image-per-page PDF using Pillow (no
    `wkhtmltopdf` required).
  - `CombinedEpubWriter` — one EPUB book containing every scraped post as
    a chapter, sorted by detected chapter number then publication date.
    Enabled with `--combined` (best with `--content novel`).
- Interactive menu via `rich` (`-i` / `--interactive`):
  scrape all / browse by label / browse all titles / filter by date.
  Selection syntax supports comma + range + `all` (e.g. `1,3,5-10`).
- Sitemap fallback adapter (`rss_blog_archiver.adapters.sitemap`) that
  walks `/sitemap.xml`, `/sitemap_index.xml`, `/wp-sitemap.xml`, etc.
- Date range filter: `--since YYYY-MM-DD` / `--until YYYY-MM-DD`. Works
  both in CLI and interactive mode.
- `--list-titles`: numbered listing of all post titles (respects `--label`
  and date filters). Useful for scripting.
- Per-host rate limiter — requests to different hosts no longer block each
  other; same-host requests still respect `--rate-limit`.

### Changed
- CLI now exposes `--format` (preferred). `--mode` is kept as a
  backward-compatible alias for the Phase 0 spelling.
- `Scraper` accepts a list of per-post writers (was a single writer) so
  comic mode can emit CBZ and PDF in the same run.
- `ScrapeConfig` gained `content_mode`, `formats`, `combined`,
  `combined_title`, `combined_author`, `since`, `until`, and
  `explicit_posts`.
- README and CLI help updated to describe the content / format matrix.

### Fixed
- `EpubWriter` no longer emits a leading `<?xml ?>` declaration in chapter
  HTML. lxml rejects unicode strings that contain an encoding declaration,
  which made `ebooklib`'s nav-generation step blow up with
  `lxml.etree.ParserError: Document is empty` once a chapter included
  page-list markers.
- Title characters `<` / `>` / `&` are XML-escaped before being written
  into the EPUB chapter wrapper.

### Tests
- Added 46 new unit tests covering content modes, build_writers wiring,
  CBZ / comic-PDF / combined-EPUB output, the selection-syntax parser,
  scraper date filter, per-host rate limiter, and sitemap walking.

## [0.1.0] - Phase 0: foundation rewrite

### Added
- Fresh `src/` layout package with adapters/extractors/writers split,
  Blogspot + WordPress adapters, real Markdown / TXT / EPUB / PDF writers,
  retry-aware HTTP client, resume state, parallel `ThreadPoolExecutor`,
  `tqdm` progress bar, rotating UTF-8 logging, full CLI, GitHub Actions
  CI matrix (Ubuntu + Windows x Python 3.10/3.11/3.12).
- 38 unit tests covering adapter detection, pagination, label fetching,
  content extraction, image discovery, filename sanitization, and date
  parsing.
