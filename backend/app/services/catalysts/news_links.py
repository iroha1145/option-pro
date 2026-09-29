"""Resolve public Google News article links to their publisher URLs.

The caller owns network policy and supplies a bounded, pinned HTTPS transport.
Only Google's article page and its fixed public batch endpoint are requested.
"""

from __future__ import annotations

import base64
from html.parser import HTMLParser
import ipaddress
import json
import re
import time
from typing import Callable
from urllib.parse import quote, urlencode, urlsplit, urlunsplit


_ID = re.compile(r"[A-Za-z0-9_-]{16,4096}\Z")
_SIGNATURE = re.compile(r"[A-Za-z0-9_-]{8,256}\Z")
_TS = re.compile(r"[0-9]{9,13}\Z")
_MAX_PAGE = 2_000_000
_MAX_REPLY = 100_000
_BATCH = "https://news.google.com/_/DotsSplashUi/data/batchexecute?rpcids=Fbv4je"


class _ArticleMetadata(HTMLParser):
    def __init__(self, article_id: str):
        super().__init__(convert_charrefs=True)
        self.article_id = article_id
        self.matches: set[tuple[str, str]] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "div":
            return
        values = dict(attrs)
        if values.get("data-n-a-id") == self.article_id:
            ts, sg = values.get("data-n-a-ts"), values.get("data-n-a-sg")
            if ts and sg and _TS.fullmatch(ts) and _SIGNATURE.fullmatch(sg):
                self.matches.add((ts, sg))


def _safe_publisher(value: object) -> str | None:
    if not isinstance(value, str) or len(value) > 8192 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or not parts.hostname or parts.username is not None or parts.password is not None:
            return None
        if parts.port not in (None, 443):
            return None
        host = parts.hostname.encode("idna").decode("ascii").lower()
        if host == "news.google.com" or "." not in host or host.endswith((".local", ".internal", ".localhost", ".test", ".invalid")):
            return None
        try:
            ipaddress.ip_address(host)
            return None
        except ValueError:
            pass
        if not all(label and len(label) <= 63 and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", label) for label in host.split(".")):
            return None
        return urlunsplit(("https", parts.netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def _legacy_url(article_id: str) -> str | None:
    """Decode the older length-prefixed, literal-URL Google News envelope."""
    try:
        raw = base64.b64decode(article_id + "=" * (-len(article_id) % 4), altchars=b"-_", validate=True)
        if not raw.startswith(b"\x08\x13\x22"):
            return None
        raw = raw[3:]
        length = 0
        for index, byte in enumerate(raw[:3]):
            length |= (byte & 0x7f) << (index * 7)
            if byte < 0x80:
                start = index + 1
                if not 0 < length <= 8192 or start + length > len(raw):
                    return None
                tail = raw[start + length:]
                if tail not in (b"", b"\xd2\x01\x00"):
                    return None
                return _safe_publisher(raw[start:start + length].decode("utf-8"))
        return None
    except (ValueError, UnicodeError):
        return None


def _batch_url(response: bytes) -> str | None:
    if len(response) > _MAX_REPLY:
        return None
    try:
        text = response.decode("utf-8")
        if not text.startswith(")]}'\n"):
            return None
        # Google's XSSI guard is followed by JSON frames. Ignore length lines,
        # but parse the entire frame rather than searching for a URL substring.
        frames = []
        for line in text[5:].splitlines():
            if line.startswith("[["):
                frames.append(json.loads(line))
        if len(frames) != 1 or not isinstance(frames[0], list):
            return None
        matches = []
        for frame in frames[0]:
            if isinstance(frame, list) and len(frame) >= 3 and frame[:2] == ["wrb.fr", "Fbv4je"] and isinstance(frame[2], str):
                inner = json.loads(frame[2])
                if isinstance(inner, list) and len(inner) >= 2 and inner[0] == "garturlres":
                    matches.append(_safe_publisher(inner[1]))
        return matches[0] if len(matches) == 1 else None
    except (ValueError, UnicodeError, TypeError, RecursionError):
        return None


def resolve_publisher_url(
    url: str,
    page_html: bytes,
    *,
    request: Callable,
    deadline: float,
) -> str | None:
    """Return a publisher URL or None; never follow links from untrusted HTML.

    request(url, *, method='GET', body=None, deadline=deadline) must enforce
    public DNS, pinned TLS, body limits, and the common total deadline.
    """
    if not isinstance(url, str) or len(url) > 8192 or any(ord(c) < 32 for c in url):
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.netloc != "news.google.com" or parsed.fragment:
            return None
        segments = parsed.path.split("/")
        if len(segments) == 3 and segments[1] in ("articles", "read"):
            article_id = segments[2]
        elif len(segments) == 4 and segments[1:3] == ["rss", "articles"]:
            article_id = segments[3]
        else:
            return None
        if not _ID.fullmatch(article_id):
            return None
        old = _legacy_url(article_id)
        if old:
            return old
        if time.monotonic() >= deadline:
            return None
        parser = _ArticleMetadata(article_id)
        if page_html and len(page_html) <= _MAX_PAGE:
            parser.feed(page_html.decode("utf-8", errors="replace"))
        if len(parser.matches) != 1:
            fixed_url = "https://news.google.com/articles/" + quote(article_id, safe="") + "?hl=en-US&gl=US&ceid=US:en"
            status, _headers, content = request(fixed_url, method="GET", body=None, deadline=deadline)
            if status != 200 or not isinstance(content, bytes) or len(content) > _MAX_PAGE:
                return None
            parser = _ArticleMetadata(article_id)
            parser.feed(content.decode("utf-8", errors="replace"))
        if len(parser.matches) != 1 or time.monotonic() >= deadline:
            return None
        timestamp, signature = next(iter(parser.matches))
        payload = ["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], article_id, int(timestamp), signature]
        body = urlencode({"f.req": json.dumps([[["Fbv4je", json.dumps(payload, separators=(",", ":")), None, "generic"]]], separators=(",", ":"))}).encode("ascii")
        status, _headers, content = request(_BATCH, method="POST", body=body, deadline=deadline)
        if status != 200 or not isinstance(content, bytes):
            return None
        return _batch_url(content)
    except (ValueError, UnicodeError, TypeError, OSError, TimeoutError, RecursionError):
        return None
