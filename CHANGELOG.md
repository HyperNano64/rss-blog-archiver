# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) loosely; each
"Phase" is a milestone-shaped release.

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
