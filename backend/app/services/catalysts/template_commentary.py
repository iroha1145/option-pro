"""Zacks template articles that scheduled news analysis skips.

Zacks publishes a steady stream of templated market commentary: daily price
recaps, rank screens, search-trend pieces and blog digests. Their titles follow
a few fixed patterns, and a paid analysis of one adds nothing the title does not
already say. The same source also publishes real news ("NVS Wins FDA Nod for
Label Expansion..."), so the rules match a title template or the generator's
tracking parameter in the link, never the whole source.

Matching ignores case and punctuation: curly and straight apostrophes, a
missing question mark, hyphens and the separator before the suffix (colon,
dash, vertical bar, or the question mark of an earlier sentence) do not change
the verdict.
"""

from __future__ import annotations

import re
from urllib.parse import unquote_plus, urlsplit


TEMPLATE_COMMENTARY_REASON = "template_commentary"

# Endings after the last separator, from the October 2026 Zacks titles.
ZACKS_TEMPLATE_SUFFIXES = (
    "What You Should Know",
    "Here's Why",
    "What Investors Need to Know",
    "Some Information for Investors",
    "Should You Buy?",
    "Facts to Know Before Betting on It",
    "Important Facts to Note",
    "Wall Street Expects Earnings Growth",
    "Here is What You Should Know",
    "What Does It Mean for the Stock?",
    "Key Facts",
    "Some Facts Worth Knowing",
    "Key Insights",
    "Here is What You Need to Know",
    "What to Know Ahead of Next Week's Release",
    "Which Stock Is the Better Value Option?",
    "What You Need to Know",
    "Some Facts to Consider",
)

# Whole-title templates, matched against the normalized title (lower case,
# letters and digits separated by single spaces). "... Earnings Expected to
# Grow: Should You Buy?" is covered by its suffix above.
ZACKS_TEMPLATE_PATTERNS = (
    r"\boutpaced the stock market today\b",
    r"\bdips more than broader market\b",
    r"^investors heavily search\b",
    r"\bis a top ranked (?:value|growth) stock\b",
    r"\bshows fast paced momentum\b",
    r"^best\b.*\bstocks to buy for\b",
    r"^the zacks analyst blog highlights\b",
    r"^should\b.*\betf\b.*\bbe on your investing radar\b",
    r"^(?:bull|bear) of the day\b",
    r"^what makes\b.*\ba new buy stock\b",
)

# Query-string markers of machine-generated articles: Zacks links to pages
# written by the Yseop generator carry it in their tracking parameter, for
# example "SWK vs. LECO: Which Stock Is the Better Value Option?".
ZACKS_TEMPLATE_URL_MARKERS = ("yseop_template",)

# A colon, a vertical bar, a spaced dash, or a question or exclamation mark
# that ends an earlier sentence ("Is It Time to Buy? Here's Why").
_SEPARATOR = re.compile(r"\s*(?:[:：|]|\s[-–—]\s|[?!？！](?=\s))\s*")


def _normalize(text: str) -> str:
    folded = re.sub(r"['‘’`´]", "", text.casefold())
    return " ".join(re.sub(r"[^0-9a-z]+", " ", folded).split())


_SUFFIXES = frozenset(_normalize(suffix) for suffix in ZACKS_TEMPLATE_SUFFIXES)
_PATTERNS = tuple(re.compile(pattern) for pattern in ZACKS_TEMPLATE_PATTERNS)


def _generated_link(url: object) -> bool:
    if not isinstance(url, str):
        return False
    try:
        query = unquote_plus(urlsplit(url).query).casefold()
    except ValueError:
        return False
    return any(marker in query for marker in ZACKS_TEMPLATE_URL_MARKERS)


def template_commentary_reason(
    source: object, title: object, url: object = None,
) -> str | None:
    """``template_commentary`` for a Zacks template article, otherwise None."""

    if not isinstance(source, str) or "zacks" not in source.casefold():
        return None
    if _generated_link(url):
        return TEMPLATE_COMMENTARY_REASON
    if not isinstance(title, str) or not title.strip():
        return None
    parts = _SEPARATOR.split(title.strip())
    if len(parts) > 1 and _normalize(parts[0]) and _normalize(parts[-1]) in _SUFFIXES:
        return TEMPLATE_COMMENTARY_REASON
    normalized = _normalize(title)
    if any(pattern.search(normalized) for pattern in _PATTERNS):
        return TEMPLATE_COMMENTARY_REASON
    return None
