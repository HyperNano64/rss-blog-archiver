"""Tests for the interactive selector's parsing logic."""

from __future__ import annotations

import pytest

from rss_blog_archiver.selector import _parse_selection_syntax


class TestParseSelectionSyntax:
    def test_single(self) -> None:
        assert _parse_selection_syntax("1", 10) == [1]

    def test_comma_list(self) -> None:
        assert _parse_selection_syntax("1,3,5", 10) == [1, 3, 5]

    def test_range(self) -> None:
        assert _parse_selection_syntax("1-5", 10) == [1, 2, 3, 4, 5]

    def test_mixed(self) -> None:
        assert _parse_selection_syntax("1,3,5-7,10", 10) == [1, 3, 5, 6, 7, 10]

    def test_clamps_to_total(self) -> None:
        # Items past `total` are dropped silently.
        assert _parse_selection_syntax("8-15", 10) == [8, 9, 10]

    def test_inverted_range(self) -> None:
        # "5-1" should still produce 1..5.
        assert _parse_selection_syntax("5-1", 10) == [1, 2, 3, 4, 5]

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError):
            _parse_selection_syntax("abc", 10)
