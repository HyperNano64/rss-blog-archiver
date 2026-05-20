"""Tests for the PDF backend factory + resolution logic.

These tests are deliberately I/O-free: we don't actually invoke WeasyPrint
or wkhtmltopdf during the unit run. Real rendering is exercised by smoke
tests outside of CI (and via the integration scenarios documented in the
README). The goal here is to lock in the contract: backend validation,
auto-fallback order, error messages, and writer wiring.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from rss_blog_archiver.models import Post
from rss_blog_archiver.writers import PdfWriter, build_writers
from rss_blog_archiver.writers import pdf_writer as pdf_writer_mod
from rss_blog_archiver.writers.base import WriterContext


def _ctx(tmp_path: Path) -> WriterContext:
    return WriterContext(
        post=Post(
            title="Hello",
            url="https://example.blogspot.com/p.html",
            published=datetime(2024, 1, 1, tzinfo=timezone.utc),
            author="A",
        ),
        content_html="<p>hi</p>",
        output_dir=tmp_path,
    )


class TestBackendResolution:
    def test_invalid_backend_rejected(self) -> None:
        with pytest.raises(ValueError):
            PdfWriter(backend="reportlab")

    def test_auto_prefers_weasyprint_when_available(self) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=True),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value="/x"),
        ):
            w = PdfWriter(backend="auto")
        assert w.resolved_backend == "weasyprint"

    def test_auto_falls_back_to_wkhtmltopdf(self) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=False),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value="/usr/bin/wk"),
        ):
            w = PdfWriter(backend="auto")
        assert w.resolved_backend == "wkhtmltopdf"

    def test_auto_returns_none_when_neither_present(self) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=False),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value=None),
        ):
            w = PdfWriter(backend="auto")
        assert w.resolved_backend is None

    def test_explicit_weasyprint_unavailable_raises_on_write(
        self, tmp_path: Path
    ) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=False),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value="/usr/bin/wk"),
        ):
            w = PdfWriter(backend="weasyprint")
        assert w.resolved_backend is None
        with pytest.raises(RuntimeError, match="No PDF backend"):
            w.write(_ctx(tmp_path))

    def test_explicit_wkhtmltopdf_unavailable_raises_on_write(
        self, tmp_path: Path
    ) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=True),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value=None),
        ):
            w = PdfWriter(backend="wkhtmltopdf")
        assert w.resolved_backend is None
        with pytest.raises(RuntimeError, match="No PDF backend"):
            w.write(_ctx(tmp_path))


class TestBuildWritersThreadsBackend:
    def test_backend_is_threaded_through_factory(self) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=True),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value=None),
        ):
            writers = build_writers("default", ["PDF"], pdf_backend="weasyprint")
        assert len(writers) == 1
        assert isinstance(writers[0], PdfWriter)
        assert writers[0].requested_backend == "weasyprint"
        assert writers[0].resolved_backend == "weasyprint"

    def test_comic_pdf_unaffected_by_backend(self) -> None:
        # Comic PDF uses Pillow, not WeasyPrint/wkhtmltopdf. Asking for
        # weasyprint backend must not turn the comic-PDF writer into a
        # weasyprint writer.
        from rss_blog_archiver.writers import ComicPdfWriter

        writers = build_writers("comic", ["CBZ", "PDF"], pdf_backend="weasyprint")
        assert any(isinstance(w, ComicPdfWriter) for w in writers)
        assert not any(isinstance(w, PdfWriter) for w in writers)

    def test_default_backend_is_auto(self) -> None:
        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=True),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value=None),
        ):
            writers = build_writers("default", ["PDF"])
        assert writers[0].requested_backend == "auto"
        assert writers[0].resolved_backend == "weasyprint"


class TestWeasyPrintInvocation:
    def test_calls_weasyprint_with_base_url(self, tmp_path: Path) -> None:
        # Inject a fake weasyprint module via the import machinery patch.
        # We just verify the writer drives the API the way we expect.
        fake_module = type(
            "FakeWeasyPrint",
            (),
            {},
        )()
        recorded = {}

        class FakeHTML:
            def __init__(self, *, string: str, base_url: str) -> None:
                recorded["string"] = string
                recorded["base_url"] = base_url

            def write_pdf(self, target: str) -> None:
                Path(target).write_bytes(b"%PDF-FAKE")

        fake_module.HTML = FakeHTML  # type: ignore[attr-defined]

        with (
            patch.object(pdf_writer_mod, "_weasyprint_available", return_value=True),
            patch.object(pdf_writer_mod, "_detect_wkhtmltopdf", return_value=None),
            patch("importlib.import_module", return_value=fake_module),
        ):
            w = PdfWriter(backend="weasyprint")
            output = w.write(_ctx(tmp_path))
        assert output.exists()
        assert output.read_bytes().startswith(b"%PDF")
        assert recorded["base_url"].startswith("file://")
        assert "<title>Hello</title>" in recorded["string"]
