"""Tests for the Blogspot adapter — exercised through mocked HTTP responses."""

from __future__ import annotations

from typing import Any

import pytest
import responses

from rss_blog_archiver.adapters.blogspot import BlogspotAdapter
from rss_blog_archiver.http_client import HttpClient


@pytest.fixture
def http() -> HttpClient:
    return HttpClient(retries=0, rate_limit_interval=0.0)


def _entry(post_id: int, *, title: str, labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "id": {"$t": f"tag:blogger.com,1999:blog-1.post-{post_id}"},
        "title": {"$t": title, "type": "text"},
        "published": {"$t": "2024-05-19T10:00:00+00:00"},
        "updated": {"$t": "2024-05-19T11:00:00+00:00"},
        "content": {"$t": f"<p>body of {title}</p>", "type": "html"},
        "summary": {"$t": f"summary of {title}"},
        "author": [{"name": {"$t": "Author One"}}],
        "category": [{"term": label} for label in (labels or [])],
        "link": [
            {"rel": "alternate", "type": "text/html",
             "href": f"https://example.blogspot.com/2024/05/post-{post_id}.html"},
            {"rel": "replies", "type": "application/atom+xml",
             "href": f"https://example.blogspot.com/feeds/{post_id}/comments/default"},
        ],
    }


def _feed_payload(entries: list[dict[str, Any]], *, categories: list[str] | None = None) -> dict[str, Any]:
    feed = {
        "feed": {
            "openSearch$totalResults": {"$t": str(len(entries))},
            "openSearch$startIndex": {"$t": "1"},
            "openSearch$itemsPerPage": {"$t": str(len(entries))},
            "entry": entries,
        }
    }
    if categories:
        feed["feed"]["category"] = [{"term": c} for c in categories]
    return feed


class TestDetect:
    @responses.activate
    def test_detects_blogspot_subdomain_without_http(self, http: HttpClient) -> None:
        responses.add(
            responses.GET,
            "https://example.blogspot.com/",
            body="<html></html>",
            status=200,
        )
        adapter = BlogspotAdapter(http)
        result = adapter.detect("https://example.blogspot.com/")
        assert result.matched
        assert result.feed_url.endswith("/feeds/posts/default")
        assert result.confidence >= 0.9

    @responses.activate
    def test_detects_custom_domain_via_generator_meta(self, http: HttpClient) -> None:
        responses.add(
            responses.GET,
            "https://blog.example.com/",
            body='<html><head><meta name="generator" content="blogger"></head></html>',
            status=200,
        )
        responses.add(
            responses.HEAD,
            "https://blog.example.com/feeds/posts/default",
            status=200,
            headers={"Content-Type": "application/atom+xml; charset=UTF-8"},
        )
        adapter = BlogspotAdapter(http)
        result = adapter.detect("https://blog.example.com/")
        assert result.matched
        assert result.confidence >= 0.9

    @responses.activate
    def test_rejects_non_blogspot(self, http: HttpClient) -> None:
        responses.add(
            responses.GET,
            "https://example.org/",
            body="<html><head><title>Some site</title></head></html>",
            status=200,
        )
        responses.add(
            responses.HEAD,
            "https://example.org/feeds/posts/default",
            status=404,
        )
        adapter = BlogspotAdapter(http)
        result = adapter.detect("https://example.org/")
        assert not result.matched


class TestIterPages:
    @responses.activate
    def test_paginates_until_empty(self, http: HttpClient) -> None:
        # First call: 2 entries (under page size -> end).
        responses.add(
            responses.GET,
            "https://example.blogspot.com/feeds/posts/default",
            json=_feed_payload([_entry(1, title="One"), _entry(2, title="Two")]),
            status=200,
        )
        adapter = BlogspotAdapter(http)
        pages = list(
            adapter.iter_pages(
                feed_url="https://example.blogspot.com/feeds/posts/default",
                max_posts=2,
            )
        )
        assert len(pages) == 1
        assert len(pages[0].posts) == 2
        assert pages[0].posts[0].title == "One"
        assert pages[0].posts[0].html.startswith("<p>body of One")
        assert pages[0].posts[0].comments_feed is not None

    @responses.activate
    def test_respects_max_posts(self, http: HttpClient) -> None:
        responses.add(
            responses.GET,
            "https://example.blogspot.com/feeds/posts/default",
            json=_feed_payload([_entry(i, title=f"Post {i}") for i in range(1, 11)]),
            status=200,
        )
        adapter = BlogspotAdapter(http)
        pages = list(
            adapter.iter_pages(
                feed_url="https://example.blogspot.com/feeds/posts/default",
                max_posts=3,
            )
        )
        all_posts = [p for page in pages for p in page.posts]
        assert len(all_posts) <= 3


class TestFetchLabels:
    @responses.activate
    def test_extracts_categories(self, http: HttpClient) -> None:
        responses.add(
            responses.GET,
            "https://example.blogspot.com/feeds/posts/default",
            json=_feed_payload([], categories=["python", "indonesia", "tutorial"]),
            status=200,
        )
        adapter = BlogspotAdapter(http)
        labels = adapter.fetch_labels("https://example.blogspot.com/")
        assert labels == ["indonesia", "python", "tutorial"]


class TestLabelFeedUrl:
    def test_unified_format(self, http: HttpClient) -> None:
        adapter = BlogspotAdapter(http)
        # subdomain
        assert adapter._label_feed_url(
            "https://example.blogspot.com/feeds/posts/default", "Python"
        ) == "https://example.blogspot.com/feeds/posts/default/-/Python"
        # custom domain with space
        assert adapter._label_feed_url(
            "https://blog.example.com/feeds/posts/default", "Python Tutorial"
        ) == "https://blog.example.com/feeds/posts/default/-/Python%20Tutorial"
