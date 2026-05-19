"""Tests for the WordPress adapter."""

from __future__ import annotations

import pytest
import responses

from rss_blog_archiver.adapters.wordpress import WordPressAdapter
from rss_blog_archiver.http_client import HttpClient


@pytest.fixture
def http() -> HttpClient:
    return HttpClient(retries=0, rate_limit_interval=0.0)


@responses.activate
def test_detect_via_generator_meta(http: HttpClient) -> None:
    responses.add(
        responses.GET,
        "https://wp.example.com/",
        body=(
            '<html><head>'
            '<meta name="generator" content="WordPress 6.5">'
            '<link rel="https://api.w.org/" href="https://wp.example.com/wp-json/">'
            '</head><body>wp-content/themes/x</body></html>'
        ),
        status=200,
    )
    adapter = WordPressAdapter(http)
    result = adapter.detect("https://wp.example.com/")
    assert result.matched
    assert result.confidence >= 0.7
    assert "wp-json" in result.feed_url


@responses.activate
def test_detect_falls_back_to_wp_json_probe(http: HttpClient) -> None:
    responses.add(
        responses.GET,
        "https://wp.example.com/",
        body='<html><head><meta name="generator" content="WordPress 6.5"></head></html>',
        status=200,
    )
    responses.add(
        responses.HEAD,
        "https://wp.example.com/wp-json/",
        status=200,
    )
    adapter = WordPressAdapter(http)
    result = adapter.detect("https://wp.example.com/")
    assert result.matched


@responses.activate
def test_iter_rest_pagination(http: HttpClient) -> None:
    rest_root = "https://wp.example.com/wp-json/"
    # Detect first.
    responses.add(
        responses.GET,
        "https://wp.example.com/",
        body=(
            '<html><head>'
            '<meta name="generator" content="WordPress 6.5">'
            f'<link rel="https://api.w.org/" href="{rest_root}">'
            '</head></html>'
        ),
        status=200,
    )
    # Page 1: 2 posts, header signals 1 total page.
    responses.add(
        responses.GET,
        f"{rest_root}wp/v2/posts",
        json=[
            {
                "id": 1,
                "link": "https://wp.example.com/?p=1",
                "title": {"rendered": "Post 1"},
                "content": {"rendered": "<p>one</p>"},
                "excerpt": {"rendered": "one"},
                "date_gmt": "2024-01-01T00:00:00",
            },
            {
                "id": 2,
                "link": "https://wp.example.com/?p=2",
                "title": {"rendered": "Post 2"},
                "content": {"rendered": "<p>two</p>"},
                "excerpt": {"rendered": "two"},
                "date_gmt": "2024-01-02T00:00:00",
            },
        ],
        headers={"X-WP-Total": "2", "X-WP-TotalPages": "1"},
        status=200,
    )
    adapter = WordPressAdapter(http)
    detection = adapter.detect("https://wp.example.com/")
    assert detection.matched

    pages = list(adapter.iter_pages(feed_url=detection.feed_url, max_posts=10))
    assert len(pages) == 1
    posts = pages[0].posts
    assert [p.title for p in posts] == ["Post 1", "Post 2"]
    assert posts[0].html.startswith("<p>one")
