"""Cross-platform PDF writer.

The original script hard-coded the Windows-only ``wkhtmltopdf.exe`` path,
which crashed on Linux/macOS. This writer:

- auto-detects ``wkhtmltopdf`` via :func:`shutil.which`,
- accepts an override via the ``RBA_WKHTMLTOPDF`` environment variable,
- gracefully degrades to a clear error message when neither is available
  (rather than crashing the whole batch).

A future iteration may add an optional WeasyPrint backend (pure Python, no
external binary needed) — exposed via the ``weasyprint`` extra.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pdfkit
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
body { font-family: 'Segoe UI', Arial, sans-serif; font-size: 13pt;
       line-height: 1.55; margin: 24px; color: #222; }
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


class PdfWriter(BaseWriter):
    extension = ".pdf"

    def __init__(self) -> None:
        self.binary = _detect_wkhtmltopdf()

    def write(self, context: WriterContext) -> Path:
        post = context.post
        target = context.output_dir / f"{sanitize_filename(post.title)}{self.extension}"
        if self.binary is None:
            raise RuntimeError(
                "wkhtmltopdf binary not found. Install it from "
                "https://wkhtmltopdf.org/downloads.html (Windows users: pick "
                "the 64-bit installer) or set the RBA_WKHTMLTOPDF environment "
                "variable to its absolute path."
            )

        html = _PDF_TEMPLATE.render(
            title=post.title,
            author=post.author,
            url=post.url,
            content=context.content_html,
            css=_PDF_CSS,
            language="id",
        )
        temp_html = target.with_suffix(".html")
        temp_html.write_text(html, encoding="utf-8")
        try:
            config = pdfkit.configuration(wkhtmltopdf=self.binary)
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
        logger.debug("Wrote PDF: %s", target)
        return target


def _detect_wkhtmltopdf() -> str | None:
    override = os.environ.get("RBA_WKHTMLTOPDF")
    if override and Path(override).exists():
        return override
    found = shutil.which("wkhtmltopdf")
    if found:
        return found
    # Common Windows install locations.
    for candidate in (
        r"C:\Program Files\wkhtmltopdf\bin\wkhtmltopdf.exe",
        r"C:\Program Files (x86)\wkhtmltopdf\bin\wkhtmltopdf.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None
