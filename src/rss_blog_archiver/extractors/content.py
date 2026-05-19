"""Heuristic extraction of the *main* post body from a blog page.

Looks for the common content container classes used by Blogspot, WordPress,
and a handful of popular themes. Falls back to the full `<article>` /
`<main>` / `<body>` if none match.
"""

from __future__ import annotations

from bs4 import BeautifulSoup, Tag

# Ordered by how strong a signal each selector is.
_CONTENT_SELECTORS: tuple[str, ...] = (
    "div.post-body",
    "div.entry-content",
    "div.post-entry",
    "div.post-content",
    "div.entry",
    "article .post-body",
    "article .entry-content",
    "article",
    "main",
)


def extract_main_content(soup: BeautifulSoup) -> Tag | None:
    """Return the best-guess main content element, or ``None`` if not found."""
    for selector in _CONTENT_SELECTORS:
        element = soup.select_one(selector)
        if element is not None:
            return element
    return soup.body


def strip_noise(element: Tag) -> Tag:
    """Remove obvious noise tags (script/style/nav/share buttons) in place."""
    for tag_name in ("script", "style", "noscript", "iframe[hidden]"):
        for tag in element.select(tag_name):
            tag.decompose()

    # Common share/related-posts containers.
    noise_class_substrings = (
        "share", "social", "related", "comments", "sidebar", "advert",
        "ads-", "post-share", "addthis", "sharedaddy", "jp-relatedposts",
    )
    for tag in list(element.find_all(True)):
        cls = " ".join(tag.get("class", []))
        if any(s in cls for s in noise_class_substrings):
            tag.decompose()
    return element
