"""Cross-platform PDF writer with two backends.

- ``weasyprint``  — pure-Python renderer. No external binary; works the same
  on Windows, Linux, and macOS. **Preferred** default.
- ``wkhtmltopdf`` — calls the legacy ``wkhtmltopdf`` binary (auto-detected or
  pointed at via ``RBA_WKHTMLTOPDF``). Kept as an escape hatch.

The user picks a backend with ``--pdf-backend {auto,weasyprint,wkhtmltopdf}``.
``auto`` tries WeasyPrint first (since it has no external system binary
once GTK is installed) and falls back to wkhtmltopdf if WeasyPrint can't
import. If neither is available, ``write()`` raises a clear error.
"""

from __future__ import annotations

import importlib
import os
import shutil
from pathlib import Path
from typing import ClassVar

from jinja2 import Template

from rss_blog_archiver.logging_setup import get_logger
from rss_blog_archiver.utils import sanitize_filename
from rss_blog_archiver.writers.base import BaseWriter, WriterContext

logger = get_logger(__name__)

_PDF_TEMPLATE = Template(
    """<!DOCTYPE html>
<html lang="{{ language }}">
<head>
    <meta charset="UTF-8">
    <title>{{ title }}</title>
    <style>{{ css }}</style>
</head>
<body>
    <h1>{{ title }}</h1>
    {% if author %}<p class="meta"><em>by {{ author }}</em></p>{% endif %}
    <p class="meta"><a href="{{ url }}">{{ url }}</a></p>
    <hr/>
    <div>{{ content|safe }}</div>
</body>
</html>"""
)

_PDF_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: 'Segoe UI', 'Helvetica Neue', Arial, sans-serif; font-size: 12pt;
       line-height: 1.55; color: #222; }
h1 { font-size: 22pt; text-align: center; margin: 0 0 16px 0; }
h2 { font-size: 17pt; }
h3 { font-size: 14pt; }
p { margin: 0 0 10px 0; text-align: justify; }
.meta { color: #666; font-size: 10pt; text-align: center; margin: 4px 0; }
a { color: #1a4dad; text-decoration: none; }
img { max-width: 100%; height: auto; display: block; margin: 14px auto; }
blockquote { border-left: 4px solid #ccc; padding-left: 12px; color: #555;
             margin: 12px 0; }
table { width: 100%; border-collapse: collapse; margin: 10px 0; }
table, th, td { border: 1px solid #aaa; padding: 6px; }
pre, code { font-family: 'Consolas', 'Menlo', monospace; font-size: 11pt; }
"""


# ---------------------------------------------------------------------------
# Public writer
# ---------------------------------------------------------------------------
class PdfWriter(BaseWriter):
    """Render a post to PDF via the configured backend.

    Parameters
    ----------
    backend
        One of ``"auto"`` (default), ``"weasyprint"``, ``"wkhtmltopdf"``.
        ``"auto"`` resolves at instantiation time: WeasyPrint if importable,
        else wkhtmltopdf if its binary is on PATH.
    """

    extension = ".pdf"
    _VALID_BACKENDS: ClassVar[frozenset[str]] = frozenset(
        {"auto", "weasyprint", "wkhtmltopdf"}
    )

    def __init__(self, backend: str = "auto") -> None:
        backend = backend.lower().strip()
        if backend not in self._VALID_BACKENDS:
            raise ValueError(
                f"Unknown PDF backend {backend!r}; expected one of "
                f"{sorted(self._VALID_BACKENDS)}"
            )
        self.requested_backend = backend
        self.resolved_backend = _resolve_backend(backend)
        self._wkhtmltopdf_binary: str | None = None
        if self.resolved_backend == "wkhtmltopdf":
            self._wkhtmltopdf_binary = _detect_wkhtmltopdf()

    # ------------------------------------------------------------------
    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = (
            context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"
        )
        html = _PDF_TEMPLATE.render(
            title=post.title,
            author=post.author,
            url=post.url,
            content=context.content_html,
            css=_PDF_CSS,
            language="id",
        )

        if self.resolved_backend == "weasyprint":
            return self._write_with_weasyprint(html, target, context)
        if self.resolved_backend == "wkhtmltopdf":
            return self._write_with_wkhtmltopdf(html, target)

        raise RuntimeError(
            "No PDF backend is available. Install WeasyPrint "
            "(`pip install weasyprint` — Windows users need the GTK 3 runtime, "
            "see https://doc.courtbouillon.org/weasyprint/stable/first_steps.html), "
            "or install wkhtmltopdf from https://wkhtmltopdf.org/downloads.html."
        )

    # ------------------------------------------------------------------
    # Backends
    # ------------------------------------------------------------------
    def _write_with_weasyprint(
        self, html: str, target: Path, context: WriterContext
    ) -> Path:
        try:
            weasyprint = importlib.import_module("weasyprint")
        except Exception as exc:  # pragma: no cover - exercised when WP missing
            raise RuntimeError(
                "WeasyPrint backend selected but the `weasyprint` package "
                "could not be imported. Install it with `pip install weasyprint` "
                "(on Windows you also need the GTK 3 runtime — see "
                "https://doc.courtbouillon.org/weasyprint/stable/first_steps.html). "
                f"Underlying error: {exc!r}"
            ) from exc
        # Resolve relative image src against the per-post output directory so
        # downloaded images render correctly without baking absolute paths
        # into the HTML.
        base_url = context.output_dir.as_uri() + "/"
        weasyprint.HTML(string=html, base_url=base_url).write_pdf(str(target))
        logger.debug("Wrote PDF via WeasyPrint: %s", target)
        return target

    def _write_with_wkhtmltopdf(self, html: str, target: Path) -> Path:
        if self._wkhtmltopdf_binary is None:
            raise RuntimeError(
                "wkhtmltopdf binary not found. Install it from "
                "https://wkhtmltopdf.org/downloads.html (Windows users: pick "
                "the 64-bit installer) or set the RBA_WKHTMLTOPDF environment "
                "variable to its absolute path. Alternatively, run with "
                "`--pdf-backend weasyprint`."
            )
        import pdfkit  # lazy import: only needed when using this backend

        temp_html = target.with_suffix(".html")
        temp_html.write_text(html, encoding="utf-8")
        try:
            config = pdfkit.configuration(wkhtmltopdf=self._wkhtmltopdf_binary)
            options = {
                "encoding": "UTF-8",
                "enable-local-file-access": None,
                "quiet": "",
            }
            pdfkit.from_file(
                str(temp_html), str(target), configuration=config, options=options
            )
        finally:
            if temp_html.exists():
                temp_html.unlink()
        logger.debug("Wrote PDF via wkhtmltopdf: %s", target)
        return target


# ---------------------------------------------------------------------------
# Backend resolution helpers
# ---------------------------------------------------------------------------
def _resolve_backend(requested: str) -> str | None:
    """Pick a concrete backend or return ``None`` if nothing is available.

    ``auto`` prefers WeasyPrint, falling back to wkhtmltopdf.
    """
    if requested == "weasyprint":
        return "weasyprint" if _weasyprint_available() else None
    if requested == "wkhtmltopdf":
        return "wkhtmltopdf" if _detect_wkhtmltopdf() else None
    # auto
    if _weasyprint_available():
        return "weasyprint"
    if _detect_wkhtmltopdf():
        return "wkhtmltopdf"
    return None


def _weasyprint_available() -> bool:
    try:
        importlib.import_module("weasyprint")
    except Exception:
        return False
    return True


def _detect_wkhtmltopdf() -> str | None:
    override = os.environ.get("RBA_WKHTMLTOPDF")
    if override and Path(override).exists():
        return override
    found = shutil.which("wkhtmltopdf")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
        r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None
