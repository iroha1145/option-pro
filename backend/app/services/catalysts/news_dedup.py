"""Duplicate detection for collected news, ported from News-feed ``utils/dedup.py``.

The content hash formula is unchanged, so a headline stored while MacroLens
supplied the news hashes to the same value when a local source fetches it
again. Fuzzy title matching differs from News-feed in one place: a trailing
`` - Publisher`` suffix is removed from every source's title, not only from
Google News titles. The suffix never enters the content hash.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_TRACKING_KEYS = {
    "fbclid", "gclid", "dclid", "msclkid", "mc_cid", "mc_eid", "igshid",
    "vero_conv", "vero_id", "mkt_tok", "oly_anon_id", "oly_enc_id",
    "ref_src", "ref_url", "spm", "yclid",
}
_PUBLISHER_SUFFIX = re.compile(
    r"\s+[-–—|]\s+([A-Z][A-Za-z.&'’]*(?:\s+[A-Z&][A-Za-z.&'’]*){0,3})\s*\Z"
)
_MIN_HEADLINE_WORDS = 3


def normalize_title(title: str) -> str:
    """Normalize a news title for deduplication."""
    title = title.lower().strip()
    title = re.sub(r"[^\w\s]", " ", title, flags=re.UNICODE)
    title = re.sub(r"\s+", " ", title)
    return title.strip()


def normalize_url(url: str) -> str:
    """Canonicalize a URL and remove common analytics parameters."""
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return url.strip()

    host = parts.hostname.lower()
    port = parts.port
    if port and not ((parts.scheme == "http" and port == 80) or (parts.scheme == "https" and port == 443)):
        host = f"{host}:{port}"

    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.lower()
        if lowered.startswith("utm_") or lowered in _TRACKING_KEYS:
            continue
        query.append((key, value))
    query.sort()
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    return urlunsplit((parts.scheme.lower(), host, path, urlencode(query, doseq=True), ""))


def publication_bucket(published_at: object) -> str:
    """Map a publication timestamp to a UTC calendar-day bucket."""
    if not published_at:
        return "unknown"
    text = str(published_at).strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date().isoformat()
    except ValueError:
        match = re.match(r"\d{4}-\d{2}-\d{2}", text)
        return match.group(0) if match else "unknown"


def compute_content_hash(title: str, url: str = "", published_at: object = None) -> str:
    """Hash the normalized title and publication day, falling back to the URL."""
    normalized = normalize_title(title)
    if normalized:
        bucket = publication_bucket(published_at)
        if bucket != "unknown":
            normalized = f"title:{normalized}|day:{bucket}"
        elif url:
            normalized = f"title:{normalized}|url:{normalize_url(url)}"
        else:
            normalized = f"title:{normalized}|day:unknown"
    elif url:
        normalized = f"url:{normalize_url(url)}"

    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def similar_titles(left: str, right: str, threshold: float = 0.92) -> bool:
    if left == right:
        return True
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    left_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", left))
    right_numbers = set(re.findall(r"\b\d+(?:\.\d+)?%?\b", right))
    if left_numbers != right_numbers:
        return False
    union = left_tokens | right_tokens
    token_similarity = len(left_tokens & right_tokens) / len(union) if union else 0
    if token_similarity >= threshold:
        return True
    return token_similarity >= 0.75 and SequenceMatcher(
        None,
        left,
        right,
        autojunk=False,
    ).ratio() >= threshold


def strip_publisher_suffix(title: str) -> str:
    """Drop a trailing `` - Reuters`` style publisher name from a headline.

    Only a short run of capitalized words without digits counts as a
    publisher, and the headline before it must keep at least three words.
    """
    match = _PUBLISHER_SUFFIX.search(title)
    if match is None:
        return title
    headline = title[: match.start()]
    if len(headline.split()) < _MIN_HEADLINE_WORDS:
        return title
    return headline


def fuzzy_title(title: str) -> str:
    """The normalized title used for near-duplicate comparison."""
    return normalize_title(strip_publisher_suffix(title))


def titles_match(left: str, right: str) -> bool:
    """Compare two raw headlines the way the collector's fuzzy pool does."""
    left_title = fuzzy_title(left)
    right_title = fuzzy_title(right)
    return bool(left_title and right_title) and similar_titles(left_title, right_title)
