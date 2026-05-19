# Changelog

All notable changes are documented here. This project follows
[Semantic Versioning](https://semver.org/spec/v2.0.0.html) loosely; each
"Phase" is a milestone-shaped release.

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
