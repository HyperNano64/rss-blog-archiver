"""Tests for :mod:`rss_blog_archiver.async_image_downloader`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from rss_blog_archiver.async_http_client import AsyncHttpClient
from rss_blog_archiver.async_image_downloader import download_images_async

# Minimal valid PNG (1x1 transparent) — enough to satisfy `image/*`
# content-type checks in production code paths that read the body.
PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe"
    b"\xdc\xccY\xe7\x00\x00\x00\x00IEND\xaeB`\x82"
)


def _png_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=PNG_BYTES,
            headers={"Content-Type": "image/png"},
        )

    return httpx.MockTransport(handler)


def test_download_images_async_writes_files(tmp_path: Path) -> None:
    html = (
        '<div>'
        '<img src="https://cdn.example.com/img/a.png">'
        '<img data-src="https://cdn.example.com/img/b.png">'
        '</div>'
    )
    soup = BeautifulSoup(html, "lxml")
    transport = _png_transport()

    async def go() -> dict[str, Path]:
        async with AsyncHttpClient(transport=transport) as http:
            return await download_images_async(
                soup,
                base_url="https://cdn.example.com/",
                output_dir=tmp_path,
                http=http,
                concurrency=4,
            )

    downloaded = asyncio.run(go())
    assert len(downloaded) == 2
    for path in downloaded.values():
        assert path.exists() and path.read_bytes() == PNG_BYTES

    # The HTML refs were rewritten to local filenames (no scheme).
    srcs = [img.get("src") for img in soup.find_all("img")]
    assert all(not src.startswith("http") for src in srcs)


def test_download_images_async_skips_data_uris(tmp_path: Path) -> None:
    html = '<div><img src="data:image/png;base64,AAAA"></div>'
    soup = BeautifulSoup(html, "lxml")
    transport = _png_transport()

    async def go() -> dict[str, Path]:
        async with AsyncHttpClient(transport=transport) as http:
            return await download_images_async(
                soup,
                base_url="https://example.com/",
                output_dir=tmp_path,
                http=http,
            )

    assert asyncio.run(go()) == {}


def test_download_images_async_handles_404(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text="missing")

    transport = httpx.MockTransport(handler)
    html = '<div><img src="https://example.com/missing.png"></div>'
    soup = BeautifulSoup(html, "lxml")

    async def go() -> dict[str, Path]:
        async with AsyncHttpClient(transport=transport) as http:
            return await download_images_async(
                soup,
                base_url="https://example.com/",
                output_dir=tmp_path,
                http=http,
            )

    assert asyncio.run(go()) == {}


def test_download_images_async_dedupes_same_url(tmp_path: Path) -> None:
    """Two ``<img>`` with the same URL should only download once."""
    html = (
        '<div>'
        '<img src="https://example.com/x.png">'
        '<img src="https://example.com/x.png">'
        '<img src="https://example.com/x.png">'
        '</div>'
    )
    soup = BeautifulSoup(html, "lxml")
    transport = _png_transport()

    async def go() -> dict[str, Path]:
        async with AsyncHttpClient(transport=transport) as http:
            return await download_images_async(
                soup,
                base_url="https://example.com/",
                output_dir=tmp_path,
                http=http,
            )

    downloaded = asyncio.run(go())
    assert len(downloaded) == 1
    # But all three imgs were rewritten.
    srcs = [img.get("src") for img in soup.find_all("img")]
    assert len(set(srcs)) == 1
    assert all(not s.startswith("http") for s in srcs)
