"""News sources polled by the local catalyst collector.

Every source is an async fetch function registered under a source key. A
fetch makes a few HTTPS requests through one shared client that ignores proxy
settings from the environment, follows a redirect only within the same host
and reads every body under a byte cap. The parsers keep the field mapping of
the News-feed clients they replace; channel/publisher source strings such as
``massive/Zacks Investment Research`` are unchanged.
"""

from __future__ import annotations

import asyncio
import html
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from functools import partial
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from app.services.finnhub_budget import (
    async_reserve_finnhub_request,
    mark_finnhub_rate_limited,
)
from app.services.massive import MassiveError, _page_cursor, _retry_after_seconds


USER_AGENT = "OptionPro-NewsReader/1.0"
REQUEST_TIMEOUT_SECONDS = 15.0
CONNECT_TIMEOUT_SECONDS = 5.0
FEED_MAX_BYTES = 1_000_000
JSON_MAX_BYTES = 5 * 1024 * 1024
MAX_TITLE_CHARS = 4_000
MAX_SUMMARY_CHARS = 50_000
MAX_URL_CHARS = 8_000
MAX_TICKERS = 100

MASSIVE_NEWS_PATH = "/v2/reference/news"
MASSIVE_PAGE_LIMIT = 1_000
MASSIVE_MAX_PAGES = 5
MASSIVE_OVERLAP = timedelta(hours=6)
MASSIVE_BACKFILL = timedelta(hours=24)
FINNHUB_CATEGORIES = ("general", "forex", "merger")
GLOBENEWSWIRE_FEED_URL = (
    "https://www.globenewswire.com/RssFeed/orgclass/1/feedTitle/"
    "GlobeNewswire%20-%20News%20about%20Public%20Companies"
)
GOOGLE_NEWS_FEEDS = {
    "google_news_stocks": (
        "https://news.google.com/rss/search?q=stock+market+OR+S%26P+500+OR+nasdaq"
        "+OR+fed+rate&hl=en-US&gl=US&ceid=US:en"
    ),
    "google_news_commodities": (
        "https://news.google.com/rss/search?q=gold+price+OR+silver+OR+precious+metals"
        "+OR+commodities&hl=en-US&gl=US&ceid=US:en"
    ),
    "google_news_macro": (
        "https://news.google.com/rss/search?q=economy+OR+inflation+OR+GDP+OR+unemployment"
        "+OR+trade+war&hl=en-US&gl=US&ceid=US:en"
    ),
}
GOOGLE_NEWS_ITEMS_PER_FEED = 30
SEEKING_ALPHA_FEEDS = {
    "seekingalpha_breaking": ("https://seekingalpha.com/market_currents.xml", "breaking"),
    "seekingalpha_daily": ("https://seekingalpha.com/tag/wall-st-breakfast.xml", "daily"),
}

_WHITESPACE_RE = re.compile(r"\s+")
_ZERO_WIDTH_RE = re.compile("[\u200b\u200c\u200d\ufeff]")
# JSON escapes can carry half of a UTF-16 pair (a summary cut mid-emoji); such
# text cannot be encoded for hashing, SQLite or the page models.
_SURROGATES_RE = re.compile("[\ud800-\udfff]")
_EARLIEST_PUBLICATION_YEAR = 1970
_TICKER_RE = re.compile(r"[A-Z0-9][A-Z0-9.^/_-]{0,19}")
_DOCTYPE_RE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_GLOBENEWSWIRE_STOCK_DOMAIN = "https://www.globenewswire.com/rss/stock"
_DC_LANGUAGE = "{http://dublincore.org/documents/dcmi-namespace/}language"
_US_STOCK = re.compile(r"(?:NASDAQ|NYSE|NYSE American|AMEX):([A-Z][A-Z0-9.\-]{0,9})", re.I)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_UNSAFE_URL_CHAR = re.compile(r"[\x00-\x20\x7f\\]")
_GLOBENEWSWIRE_SUMMARY_CHARS = 500
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}


@dataclass(frozen=True)
class SourceSpec:
    key: str
    label: str
    host: str
    config: str


NEWS_SOURCES: tuple[SourceSpec, ...] = (
    SourceSpec("massive", "Massive", "api.massive.com", "massive"),
    SourceSpec("finnhub_general", "Finnhub · general", "finnhub.io", "finnhub"),
    SourceSpec("finnhub_forex", "Finnhub · forex", "finnhub.io", "finnhub"),
    SourceSpec("finnhub_merger", "Finnhub · merger", "finnhub.io", "finnhub"),
    SourceSpec("globenewswire", "GlobeNewswire", "www.globenewswire.com", "globenewswire"),
    SourceSpec("google_news_stocks", "Google News · stocks", "news.google.com", "google_news"),
    SourceSpec(
        "google_news_commodities",
        "Google News · commodities",
        "news.google.com",
        "google_news",
    ),
    SourceSpec("google_news_macro", "Google News · macro", "news.google.com", "google_news"),
    SourceSpec(
        "seekingalpha_breaking",
        "Seeking Alpha · market currents",
        "seekingalpha.com",
        "seekingalpha_breaking",
    ),
    SourceSpec(
        "seekingalpha_daily",
        "Seeking Alpha · Wall St. Breakfast",
        "seekingalpha.com",
        "seekingalpha_daily",
    ),
)


@dataclass(frozen=True)
class SourceRequest:
    now: datetime
    cursor: str | None = None


@dataclass(frozen=True)
class SourceItem:
    source_item_id: str
    source: str
    title: str
    url: str
    summary: str | None = None
    image_url: str | None = None
    published_at: str | None = None
    tickers: tuple[str, ...] = ()


@dataclass(frozen=True)
class NewsBatch:
    items: tuple[SourceItem, ...] = ()
    cursor: str | None = None
    not_modified: bool = False
    # No request was made, e.g. the shared Finnhub budget had no slot left.
    deferred: bool = False


NewsFetch = Callable[[SourceRequest], Awaitable[NewsBatch]]


class SourceError(RuntimeError):
    """A classified source failure; ``code`` never carries a body or a key."""

    def __init__(self, code: str, *, retry_after: float | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.retry_after = retry_after


@dataclass(frozen=True)
class SourceResponse:
    status_code: int
    headers: httpx.Headers
    body: bytes


def http_client(*, transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=False,
        trust_env=False,
        transport=transport,
    )


def _same_origin(left: httpx.URL, right: str) -> bool:
    target = urlsplit(right)
    try:
        port = target.port
    except ValueError:
        return False
    return (
        target.scheme == "https"
        and left.scheme == "https"
        and (target.hostname or "").lower() == left.host.lower()
        and port == left.port
        and target.username is None
        and target.password is None
    )


async def _bounded_body(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise SourceError("response_too_large")
    chunks: list[bytes] = []
    total = 0
    async for chunk in response.aiter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise SourceError("response_too_large")
        chunks.append(chunk)
    return b"".join(chunks)


async def fetch_url(
    client: httpx.AsyncClient,
    url: str,
    *,
    max_bytes: int,
    params: Mapping[str, Any] | None = None,
    headers: Mapping[str, str] | None = None,
) -> SourceResponse:
    """GET one resource; 304 is returned, every other non-2xx status raises."""

    target = url
    query = dict(params) if params else None
    for hop in range(2):
        request = client.build_request("GET", target, params=query, headers=headers)
        try:
            response = await client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise SourceError("timeout") from exc
        except httpx.TransportError as exc:
            raise SourceError("network_error") from exc
        try:
            if response.status_code == 304:
                return SourceResponse(304, response.headers, b"")
            if response.has_redirect_location:
                location = urljoin(str(request.url), response.headers["location"])
                if hop or not _same_origin(request.url, location):
                    raise SourceError("redirect_refused")
                target, query = location, None
                continue
            if response.status_code == 429:
                raise SourceError("rate_limited", retry_after=_retry_after_seconds(response))
            if not 200 <= response.status_code < 300:
                raise SourceError(f"http_{response.status_code}")
            try:
                body = await _bounded_body(response, max_bytes)
            except httpx.TimeoutException as exc:
                raise SourceError("timeout") from exc
            except httpx.TransportError as exc:
                raise SourceError("network_error") from exc
            except httpx.DecodingError as exc:
                raise SourceError("invalid_response") from exc
            return SourceResponse(response.status_code, response.headers, body)
        finally:
            await response.aclose()
    raise SourceError("redirect_refused")


def conditional_headers(cursor: str | None) -> dict[str, str]:
    """Validators saved from the last full response of a feed."""

    if not cursor:
        return {}
    try:
        saved = json.loads(cursor)
    except json.JSONDecodeError:
        return {}
    if not isinstance(saved, dict):
        return {}
    headers: dict[str, str] = {}
    if isinstance(saved.get("etag"), str) and saved["etag"]:
        headers["If-None-Match"] = saved["etag"]
    if isinstance(saved.get("last_modified"), str) and saved["last_modified"]:
        headers["If-Modified-Since"] = saved["last_modified"]
    return headers


def validator_cursor(response: SourceResponse) -> str | None:
    etag = response.headers.get("etag")
    modified = response.headers.get("last-modified")
    if not etag and not modified:
        return None
    return json.dumps(
        {"etag": etag, "last_modified": modified},
        separators=(",", ":"),
        sort_keys=True,
    )


def parse_json(body: bytes) -> Any:
    try:
        return json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise SourceError("invalid_response") from exc


def parse_feed(body: bytes) -> list[ET.Element]:
    """Items of an RSS 2.0 document; a DTD is never accepted."""

    if _DOCTYPE_RE.search(body):
        raise SourceError("invalid_response")
    try:
        root = ET.fromstring(body)
    except (ET.ParseError, LookupError) as exc:
        raise SourceError("invalid_response") from exc
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise SourceError("invalid_response")
    return channel.findall("item")


def clean_news_text(value: object, *, empty: str | None = None) -> str | None:
    """Normalize transport noise while preserving short or low-context text."""
    if value is None:
        return empty
    text = html.unescape(str(value))
    text = _ZERO_WIDTH_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or empty


def source_tickers(values: object) -> tuple[str, ...]:
    tickers: list[str] = []
    for value in values if isinstance(values, (list, tuple)) else ():
        ticker = str(value or "").strip().upper().lstrip("$")
        if _TICKER_RE.fullmatch(ticker) and ticker not in tickers:
            tickers.append(ticker)
    return tuple(tickers[:MAX_TICKERS])


def utc_seconds(value: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ``; a naive value is read as UTC.

    Built field by field: glibc's ``strftime`` does not pad a year before 1000.
    """

    moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    moment = moment.astimezone(timezone.utc)
    return (
        f"{moment.year:04d}-{moment.month:02d}-{moment.day:02d}"
        f"T{moment.hour:02d}:{moment.minute:02d}:{moment.second:02d}Z"
    )


def utc_micros(value: datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SS.ffffffZ`` with all six digits, even at zero."""

    moment = value.astimezone(timezone.utc)
    return f"{utc_seconds(moment)[:-1]}.{moment.microsecond:06d}Z"


def parse_utc(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _publication(moment: datetime) -> str | None:
    """Publication time as UTC seconds; a date before 1970 is not a real one."""

    text = utc_seconds(moment)
    return text if int(text[:4]) >= _EARLIEST_PUBLICATION_YEAR else None


def _iso_seconds(value: object) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _publication(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except (ValueError, OverflowError):
        return None


def _rss_seconds(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return _publication(parsedate_to_datetime(value))
    except (TypeError, ValueError, OverflowError):
        return None


def web_url(value: str | None) -> str | None:
    """An http or https address with a host and a usable port, else ``None``."""

    if not value or len(value) > MAX_URL_CHARS:
        return None
    try:
        parts = urlsplit(value)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"} or not parts.hostname or port == 0:
        return None
    return value


def strip_surrogates(value: str | None) -> str:
    return _SURROGATES_RE.sub("", value or "")


def _item(
    *,
    source_item_id: str,
    source: str,
    title: str,
    url: str,
    summary: str | None = None,
    image_url: str | None = None,
    published_at: str | None = None,
    tickers: tuple[str, ...] = (),
) -> SourceItem | None:
    title = strip_surrogates(title).strip()
    address = web_url(strip_surrogates(url))
    if not title or address is None:
        return None
    summary = strip_surrogates(summary).strip()
    return SourceItem(
        source_item_id=(strip_surrogates(source_item_id) or address)[:1_000],
        source=strip_surrogates(source)[:500],
        title=title[:MAX_TITLE_CHARS],
        url=address,
        summary=summary[:MAX_SUMMARY_CHARS] or None,
        image_url=web_url(strip_surrogates(image_url)),
        published_at=published_at,
        tickers=tickers,
    )


def parse_massive_item(raw: Mapping[str, Any]) -> SourceItem | None:
    publisher = raw.get("publisher")
    name = clean_news_text(
        publisher.get("name") if isinstance(publisher, Mapping) else None,
        empty="Massive",
    )
    return _item(
        source_item_id=str(raw.get("id") or "").strip(),
        source=f"massive/{name}",
        title=clean_news_text(raw.get("title"), empty="") or "",
        url=clean_news_text(raw.get("article_url"), empty="") or "",
        summary=clean_news_text(raw.get("description")),
        image_url=clean_news_text(raw.get("image_url")),
        published_at=_iso_seconds(raw.get("published_utc")),
        tickers=source_tickers(raw.get("tickers")),
    )


def parse_finnhub_item(raw: Mapping[str, Any]) -> SourceItem | None:
    published_at = None
    stamp = raw.get("datetime")
    if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp > 0:
        try:
            published_at = _publication(datetime.fromtimestamp(int(stamp), tz=timezone.utc))
        except (OverflowError, OSError, ValueError):
            published_at = None
    related = str(raw.get("related") or "")
    return _item(
        source_item_id=str(raw.get("id") or "").strip(),
        source=f"finnhub/{clean_news_text(raw.get('source'), empty='unknown')}",
        title=clean_news_text(raw.get("headline"), empty="") or "",
        url=clean_news_text(raw.get("url"), empty="") or "",
        summary=clean_news_text(raw.get("summary")),
        image_url=clean_news_text(raw.get("image")),
        published_at=published_at,
        tickers=source_tickers([value for value in related.split(",") if value.strip()]),
    )


class _SummaryText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipped = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        marker = f"{attributes.get('class') or ''} {attributes.get('id') or ''}"
        blocked = self.skipped or tag in {"script", "style", "noscript", "iframe", "form"} or re.search(
            r"\b(?:advertisement|sponsored|ad-banner)\b", marker, re.I
        )
        if blocked and tag not in _VOID_TAGS:
            self.skipped += 1
        elif not blocked and tag in {"p", "div", "br", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if self.skipped:
            self.skipped -= 1
        elif tag in {"p", "div", "li", "h1", "h2", "h3"}:
            self.parts.append(" ")

    def handle_data(self, data: str) -> None:
        if not self.skipped:
            self.parts.append(data)


def _globenewswire_summary(value: str | None) -> str | None:
    if not value:
        return None
    parser = _SummaryText()
    parser.feed(value)
    text = clean_news_text(_CONTROL.sub("", "".join(parser.parts)))
    return text[:_GLOBENEWSWIRE_SUMMARY_CHARS] if text else None


def parse_globenewswire_item(item: ET.Element) -> SourceItem | None:
    title = clean_news_text(_CONTROL.sub("", item.findtext("title") or ""), empty="") or ""
    url = item.findtext("link") or ""
    if _UNSAFE_URL_CHAR.search(url) or not url or len(url) > 4_096:
        return None
    try:
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.hostname != "www.globenewswire.com"
            or parts.port not in (None, 443)
            or parts.username is not None
            or parts.password is not None
            or not parts.path.startswith("/news-release/")
        ):
            return None
    except ValueError:
        return None
    language = clean_news_text(
        item.findtext(_DC_LANGUAGE) or item.findtext("language"),
        empty="",
    ) or ""
    if language and language.split("-", 1)[0].lower() != "en":
        return None
    tickers: list[str] = []
    for category in item.findall("category"):
        if category.get("domain") != _GLOBENEWSWIRE_STOCK_DOMAIN:
            continue
        match = _US_STOCK.fullmatch((category.text or "").strip())
        if match and match.group(1).upper() not in tickers:
            tickers.append(match.group(1).upper())
    return _item(
        source_item_id=(item.findtext("guid") or "").strip() or url,
        source="globenewswire/public_companies",
        title=title,
        url=url,
        summary=_globenewswire_summary(item.findtext("description")),
        published_at=_rss_seconds(item.findtext("pubDate")),
        tickers=tuple(tickers[:MAX_TICKERS]),
    )


def parse_google_news_item(item: ET.Element) -> SourceItem | None:
    title = clean_news_text(item.findtext("title"), empty="") or ""
    url = clean_news_text(item.findtext("link"), empty="") or ""
    publisher = clean_news_text(item.findtext("source"), empty="Google News") or "Google News"
    suffix = f" - {publisher}"
    if suffix in title:
        title = title.rsplit(suffix, 1)[0].strip()
    return _item(
        source_item_id=(item.findtext("guid") or "").strip() or url,
        source=f"google/{publisher}",
        title=title,
        url=url,
        published_at=_rss_seconds(item.findtext("pubDate")),
    )


def parse_seekingalpha_item(item: ET.Element, kind: str) -> SourceItem | None:
    # Market currents mark symbols by domain; Wall St. Breakfast uses type.
    symbols = [
        category.text
        for category in item.findall("category")
        if category.text
        and ("symbol" in category.get("domain", "") or category.get("type") == "symbol")
    ]
    url = clean_news_text(item.findtext("link"), empty="") or ""
    return _item(
        source_item_id=(item.findtext("guid") or "").strip() or url,
        source=f"seekingalpha/{kind}",
        title=clean_news_text(item.findtext("title"), empty="") or "",
        url=url,
        published_at=_rss_seconds(item.findtext("pubDate")),
        tickers=source_tickers(symbols),
    )


async def fetch_massive(
    client: httpx.AsyncClient,
    request: SourceRequest,
    *,
    api_key: str,
    base_url: str,
) -> NewsBatch:
    """Newest-first pages down to six hours before the newest item seen."""

    newest = _iso_seconds(request.cursor)
    floor = (
        datetime.fromisoformat(newest.replace("Z", "+00:00")) - MASSIVE_OVERLAP
        if newest
        else request.now - MASSIVE_BACKFILL
    )
    first_page = {
        "published_utc.gte": utc_seconds(floor),
        "limit": MASSIVE_PAGE_LIMIT,
        "sort": "published_utc",
        "order": "desc",
    }
    headers = {"Authorization": f"Bearer {api_key}", "Accept": "application/json"}
    items: list[SourceItem] = []
    page_cursor: str | None = None
    for _page in range(MASSIVE_MAX_PAGES):
        response = await fetch_url(
            client,
            f"{base_url}{MASSIVE_NEWS_PATH}",
            params=first_page if page_cursor is None else {"cursor": page_cursor},
            headers=headers,
            max_bytes=JSON_MAX_BYTES,
        )
        payload = parse_json(response.body)
        results = payload.get("results") if isinstance(payload, dict) else None
        if not isinstance(payload, dict) or not isinstance(results, (list, type(None))):
            raise SourceError("invalid_response")
        items.extend(
            parsed
            for raw in results or ()
            if isinstance(raw, Mapping) and (parsed := parse_massive_item(raw))
        )
        next_url = payload.get("next_url")
        if not next_url:
            break
        try:
            page_cursor = _page_cursor(next_url, path=MASSIVE_NEWS_PATH)
        except MassiveError as exc:
            raise SourceError("invalid_response") from exc
    seen = [item.published_at for item in items if item.published_at]
    if newest:
        seen.append(newest)
    cursor = min(max(seen), utc_seconds(request.now)) if seen else None
    return NewsBatch(tuple(items), cursor=cursor)


async def fetch_finnhub(
    client: httpx.AsyncClient,
    request: SourceRequest,
    *,
    api_key: str,
    base_url: str,
    category: str,
) -> NewsBatch:
    if not await async_reserve_finnhub_request(api_key, timeout=0):
        return NewsBatch(deferred=True)
    try:
        response = await fetch_url(
            client,
            f"{base_url}/news",
            params={"category": category},
            headers={"X-Finnhub-Token": api_key, "Accept": "application/json"},
            max_bytes=JSON_MAX_BYTES,
        )
    except SourceError as exc:
        if exc.code == "rate_limited":
            await asyncio.to_thread(
                mark_finnhub_rate_limited,
                api_key,
                retry_after=exc.retry_after if exc.retry_after is not None else 60.0,
            )
        raise
    payload = parse_json(response.body)
    if not isinstance(payload, list):
        raise SourceError("invalid_response")
    return NewsBatch(
        tuple(
            parsed
            for raw in payload
            if isinstance(raw, Mapping) and (parsed := parse_finnhub_item(raw))
        )
    )


async def _fetch_feed(
    client: httpx.AsyncClient,
    request: SourceRequest,
    url: str,
    parse: Callable[[ET.Element], SourceItem | None],
    *,
    limit: int | None = None,
) -> NewsBatch:
    response = await fetch_url(
        client,
        url,
        headers=conditional_headers(request.cursor),
        max_bytes=FEED_MAX_BYTES,
    )
    if response.status_code == 304:
        return NewsBatch(cursor=request.cursor, not_modified=True)
    elements = parse_feed(response.body)
    items = tuple(parsed for element in elements[:limit] if (parsed := parse(element)))
    return NewsBatch(items, cursor=validator_cursor(response))


async def fetch_globenewswire(client: httpx.AsyncClient, request: SourceRequest) -> NewsBatch:
    return await _fetch_feed(client, request, GLOBENEWSWIRE_FEED_URL, parse_globenewswire_item)


async def fetch_google_news(
    client: httpx.AsyncClient,
    request: SourceRequest,
    *,
    url: str,
) -> NewsBatch:
    return await _fetch_feed(
        client,
        request,
        url,
        parse_google_news_item,
        limit=GOOGLE_NEWS_ITEMS_PER_FEED,
    )


async def fetch_seekingalpha(
    client: httpx.AsyncClient,
    request: SourceRequest,
    *,
    url: str,
    kind: str,
) -> NewsBatch:
    return await _fetch_feed(
        client,
        request,
        url,
        partial(parse_seekingalpha_item, kind=kind),
    )


def build_news_fetchers(settings: Any, client: httpx.AsyncClient) -> dict[str, NewsFetch]:
    """Fetchers for every news source whose credentials are configured."""

    fetchers: dict[str, NewsFetch] = {}
    massive_key = str(getattr(settings, "massive_api_key", "") or "").strip()
    if massive_key:
        fetchers["massive"] = partial(
            fetch_massive,
            client,
            api_key=massive_key,
            base_url=str(settings.massive_base_url).rstrip("/"),
        )
    finnhub_key = str(getattr(settings, "finnhub_api_key", "") or "").strip()
    if finnhub_key:
        for category in FINNHUB_CATEGORIES:
            fetchers[f"finnhub_{category}"] = partial(
                fetch_finnhub,
                client,
                api_key=finnhub_key,
                base_url=str(settings.finnhub_base_url).rstrip("/"),
                category=category,
            )
    fetchers["globenewswire"] = partial(fetch_globenewswire, client)
    for key, url in GOOGLE_NEWS_FEEDS.items():
        fetchers[key] = partial(fetch_google_news, client, url=url)
    for key, (url, kind) in SEEKING_ALPHA_FEEDS.items():
        fetchers[key] = partial(fetch_seekingalpha, client, url=url, kind=kind)
    return fetchers
