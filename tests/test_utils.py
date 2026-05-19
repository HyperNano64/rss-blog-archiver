"""Tests for the pure helper functions in :mod:`rss_blog_archiver.utils`."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from rss_blog_archiver.utils import (
    host_from_url,
    is_blogspot_host,
    safe_parse_date,
    sanitize_filename,
    sanitize_url_to_filename,
)


class TestSanitizeFilename:
    def test_strips_illegal_chars(self) -> None:
        assert sanitize_filename('a/b\\c:d*e?f"g<h>i|j') == "abcdefghij"

    def test_normalizes_whitespace(self) -> None:
        assert sanitize_filename("  hello\n\tworld  ") == "hello world"

    def test_trims_trailing_dot_and_space(self) -> None:
        assert sanitize_filename("Some name.   ") == "Some name"
        assert sanitize_filename("Foo. ") == "Foo"

    def test_truncates_long_names_preserving_extension(self) -> None:
        long = "x" * 300 + ".jpg"
        out = sanitize_filename(long, max_length=50)
        assert len(out) <= 50
        assert out.endswith(".jpg")

    def test_avoids_windows_reserved_names(self) -> None:
        assert sanitize_filename("CON") == "_CON"
        assert sanitize_filename("nul.txt") == "_nul.txt"

    def test_empty_yields_placeholder(self) -> None:
        out = sanitize_filename("")
        assert out.startswith("untitled_")
        assert len(out) > len("untitled_")

    def test_only_illegal_chars_yields_placeholder(self) -> None:
        out = sanitize_filename('//\\:?*"<>|')
        assert out.startswith("untitled_")


class TestSanitizeUrlToFilename:
    def test_includes_hash_prefix(self) -> None:
        out = sanitize_url_to_filename("https://example.com/a/foo.jpg")
        assert "_foo.jpg" in out
        assert len(out.split("_", 1)[0]) == 8

    def test_two_distinct_urls_yield_different_names(self) -> None:
        a = sanitize_url_to_filename("https://example.com/a/foo.jpg")
        b = sanitize_url_to_filename("https://example.com/b/foo.jpg")
        assert a != b

    def test_handles_missing_basename(self) -> None:
        out = sanitize_url_to_filename("https://example.com/")
        assert out  # not empty


class TestHostFromUrl:
    def test_strips_www(self) -> None:
        assert host_from_url("https://www.example.com/x") == "example.com"

    def test_returns_unknown_when_missing(self) -> None:
        assert host_from_url("not a url") == "unknown_host"


class TestIsBlogspotHost:
    def test_matches_blogspot_subdomain(self) -> None:
        assert is_blogspot_host("https://myblog.blogspot.com/2020/01/post.html")

    def test_does_not_match_custom_domain(self) -> None:
        assert not is_blogspot_host("https://blog.example.com/")


class TestSafeParseDate:
    def test_parses_iso(self) -> None:
        dt = safe_parse_date("2024-05-19T12:34:56Z")
        assert dt.year == 2024 and dt.month == 5 and dt.day == 19

    def test_invalid_falls_back_to_now(self) -> None:
        dt = safe_parse_date("not-a-date")
        assert isinstance(dt, datetime)

    def test_none_falls_back_to_now(self) -> None:
        dt = safe_parse_date(None)
        assert dt.tzinfo is not None

    @pytest.mark.parametrize("v", ["", "garbage", "Mon, 32 Foo 2099"])
    def test_handles_garbage_inputs(self, v: str) -> None:
        out = safe_parse_date(v)
        assert isinstance(out, datetime)
        # Always returns tz-aware in UTC on fallback.
        assert out.tzinfo is timezone.utc
