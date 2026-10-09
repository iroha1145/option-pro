"""Source parsing and HTTP behaviour of the local news collector.

Fixtures in ``tests/fixtures/catalyst_sources``:

- ``google_news_*.xml``, ``seekingalpha_*.xml`` and ``forexfactory_thisweek.json``
  were fetched from the public addresses on 2026-10-09 and trimmed (Google
  descriptions dropped, 30 items per search).
- ``massive_news.json`` holds four real ``/v2/reference/news`` records with the
  publisher logo, keyword and insight fields removed; ``next_url`` is real.
- ``globenewswire.xml`` and ``finnhub_*.json`` are hand-built from the fields the
  News-feed parsers read: GlobeNewswire answered 403 to every request from this
  machine and Finnhub needs a key that is not available locally.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import finnhub_budget
from app.services.catalysts import news_sources
from app.services.catalysts.news_sources import (
    NEWS_SOURCES,
    SourceError,
    SourceRequest,
    build_news_fetchers,
    fetch_finnhub,
    fetch_globenewswire,
    fetch_google_news,
    fetch_massive,
    fetch_seekingalpha,
    fetch_url,
    http_client,
    parse_feed,
    parse_finnhub_item,
    parse_globenewswire_item,
    parse_google_news_item,
    parse_massive_item,
    parse_seekingalpha_item,
)


FIXTURES = Path(__file__).parent / "fixtures" / "catalyst_sources"
NOW = datetime(2026, 10, 9, 14, 0, tzinfo=timezone.utc)
MASSIVE_BASE = "https://api.massive.com"
FINNHUB_BASE = "https://finnhub.io/api/v1"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _run(coroutine):
    return asyncio.run(coroutine)


def _client(handler) -> httpx.AsyncClient:
    return http_client(transport=httpx.MockTransport(handler))


async def _with_client(handler, call):
    async with _client(handler) as client:
        return await call(client)


def test_massive_items_keep_publisher_channel_and_full_description():
    payload = json.loads(_fixture("massive_news.json"))
    items = [parse_massive_item(raw) for raw in payload["results"]]

    assert [item.source for item in items] == [
        "massive/GlobeNewswire Inc.",
        "massive/The Motley Fool",
        "massive/The Motley Fool",
        "massive/GlobeNewswire Inc.",
    ]
    first = items[0]
    assert first.source_item_id == payload["results"][0]["id"]
    assert first.title == "McEwen Signs US$55 Million Agreement to Sell Fuller and Paymaster"
    assert first.published_at == "2026-10-09T13:00:00Z"
    assert first.tickers == ("MUX",)
    # News-feed 截到 500 字；新路线保留全文。
    assert first.summary == payload["results"][0]["description"]
    assert items[1].tickers == ("GOOG", "GOOGL", "GOOGM", "GOOGN", "GEV", "BRK.A", "BRK.B")
    assert all(len(item.published_at) == 20 for item in items)


def test_massive_fetch_starts_six_hours_before_newest_and_follows_the_cursor():
    requests: list[httpx.Request] = []
    second_page = {
        "results": [
            {
                "id": "f" * 64,
                "publisher": {"name": "Zacks Investment Research"},
                "title": "Earnings Preview: Woodgrove Bank Q3",
                "published_utc": "2026-10-09T11:00:00Z",
                "article_url": "https://www.zacks.com/stock/news/1/woodgrove-bank",
                "tickers": ["wgbk"],
            }
        ],
        "status": "OK",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.headers["authorization"] == "Bearer massive-key"
        if "cursor" in request.url.params:
            return httpx.Response(200, json=second_page)
        return httpx.Response(200, content=_fixture("massive_news.json"))

    batch = _run(
        _with_client(
            handler,
            lambda client: fetch_massive(
                client,
                SourceRequest(now=NOW, cursor="2026-10-09T12:30:00Z"),
                api_key="massive-key",
                base_url=MASSIVE_BASE,
            ),
        )
    )

    first, second = requests
    assert first.url.path == "/v2/reference/news"
    assert dict(first.url.params) == {
        "published_utc.gte": "2026-10-09T06:30:00Z",
        "limit": "1000",
        "sort": "published_utc",
        "order": "desc",
    }
    # 后续页只带官方 next_url 里的游标，不重放其他参数，也不访问其他地址。
    assert second.url.path == "/v2/reference/news"
    assert list(second.url.params) == ["cursor"]
    assert len(batch.items) == 5
    assert batch.items[-1].tickers == ("WGBK",)
    assert batch.cursor == "2026-10-09T13:00:00Z"


def test_massive_first_fetch_backfills_a_day_and_never_moves_the_cursor_past_now():
    seen: list[httpx.Request] = []
    future = {
        "results": [
            {
                "id": "a" * 64,
                "publisher": {"name": "Zacks Investment Research"},
                "title": "Scheduled column",
                "published_utc": "2026-10-12T00:00:00Z",
                "article_url": "https://www.zacks.com/stock/news/2/column",
            }
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=future)

    batch = _run(
        _with_client(
            handler,
            lambda client: fetch_massive(
                client,
                SourceRequest(now=NOW, cursor="not-a-time"),
                api_key="k",
                base_url=MASSIVE_BASE,
            ),
        )
    )

    assert seen[0].url.params["published_utc.gte"] == "2026-10-08T14:00:00Z"
    assert batch.cursor == "2026-10-09T14:00:00Z"


def test_massive_reads_at_most_five_pages_and_rejects_foreign_next_url():
    calls = 0

    def endless(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            json={
                "results": [],
                "next_url": f"{MASSIVE_BASE}/v2/reference/news?cursor=page{calls}",
            },
        )

    _run(
        _with_client(
            endless,
            lambda client: fetch_massive(
                client, SourceRequest(now=NOW), api_key="k", base_url=MASSIVE_BASE
            ),
        )
    )
    assert calls == 5

    def foreign(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"results": [], "next_url": "https://evil.example/v2/reference/news?cursor=x"},
        )

    with pytest.raises(SourceError) as raised:
        _run(
            _with_client(
                foreign,
                lambda client: fetch_massive(
                    client, SourceRequest(now=NOW), api_key="k", base_url=MASSIVE_BASE
                ),
            )
        )
    assert raised.value.code == "invalid_response"


def test_finnhub_items_map_related_tickers_and_unix_time():
    payload = json.loads(_fixture("finnhub_general.json"))
    items = [parse_finnhub_item(raw) for raw in payload]

    assert items[0].source == "finnhub/Reuters"
    assert items[0].published_at == "2026-10-07T23:20:00Z"
    assert items[0].tickers == ()
    assert items[1].tickers == ("JPM", "WFC", "C")
    assert items[1].source_item_id == str(payload[1]["id"])
    assert items[3].image_url.startswith("https://static.finnhub.io/")
    assert parse_finnhub_item({"headline": "", "url": "https://x.test/a"}) is None


def test_finnhub_uses_the_shared_budget_and_header_token():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, content=_fixture("finnhub_merger.json"))

    batch = _run(
        _with_client(
            handler,
            lambda client: fetch_finnhub(
                client,
                SourceRequest(now=NOW),
                api_key="finnhub-key",
                base_url=FINNHUB_BASE,
                category="merger",
            ),
        )
    )

    assert requests[0].url.path == "/api/v1/news"
    assert requests[0].url.params["category"] == "merger"
    assert requests[0].headers["x-finnhub-token"] == "finnhub-key"
    assert "token" not in requests[0].url.params
    assert len(batch.items) == 6
    assert batch.deferred is False


def test_finnhub_without_budget_defers_without_a_request(monkeypatch):
    async def exhausted(*_args, **_kwargs):
        return False

    monkeypatch.setattr(news_sources, "async_reserve_finnhub_request", exhausted)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request may be made without a budget slot")

    batch = _run(
        _with_client(
            handler,
            lambda client: fetch_finnhub(
                client,
                SourceRequest(now=NOW),
                api_key="k",
                base_url=FINNHUB_BASE,
                category="general",
            ),
        )
    )
    assert batch.deferred is True
    assert batch.items == ()


def test_finnhub_rate_limit_publishes_a_shared_cooldown():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "120"})

    with pytest.raises(SourceError) as raised:
        _run(
            _with_client(
                handler,
                lambda client: fetch_finnhub(
                    client,
                    SourceRequest(now=NOW),
                    api_key="cooldown-key",
                    base_url=FINNHUB_BASE,
                    category="forex",
                ),
            )
        )
    assert raised.value.code == "rate_limited"
    assert raised.value.retry_after == 120
    assert finnhub_budget.reserve_finnhub_request("cooldown-key") is False


def test_globenewswire_keeps_english_releases_and_us_listings():
    items = [
        parsed
        for element in parse_feed(_fixture("globenewswire.xml"))
        if (parsed := parse_globenewswire_item(element))
    ]
    titles = [item.title for item in items]

    assert len(items) == 17
    assert not any("Proseware" in title for title in titles)
    assert "Contoso Energy newsroom" not in titles
    by_title = {item.title: item for item in items}
    mcewen = by_title["McEwen Signs US$55 Million Agreement to Sell Fuller and Paymaster"]
    assert mcewen.source == "globenewswire/public_companies"
    assert mcewen.source_item_id == "3378099"
    assert mcewen.tickers == ("MUX",)
    assert mcewen.published_at == "2026-10-09T13:00:00Z"
    assert mcewen.summary.startswith("TORONTO, Oct. 09, 2026 (GLOBE NEWSWIRE)")
    assert by_title["Adatum Robotics Announces Pricing of $150 Million Public Offering"].tickers == (
        "ADRB",
    )
    assert by_title["Coho Winery Reports Record Harvest Volumes"].tickers == ()
    northwind = by_title[
        "Northwind Biosciences Reports Topline Phase 2 Results for NWB-101 in Moderate Psoriasis"
    ]
    assert "tracker" not in northwind.summary
    assert "Sponsored" not in northwind.summary
    assert all(len(item.summary or "") <= 500 for item in items)


def test_google_news_strips_publisher_suffix_and_has_no_summary():
    elements = parse_feed(_fixture("google_news_stocks.xml"))
    items = [parse_google_news_item(element) for element in elements]

    assert len(elements) == 30
    assert items[0].title == "S&P 500, Nasdaq end lower as crude prices jump, chip stocks weigh"
    assert items[0].source == "google/Reuters"
    assert items[0].summary is None
    assert items[0].published_at == "2026-10-08T23:09:38Z"
    assert items[0].url.startswith("https://news.google.com/rss/articles/")
    assert items[0].source_item_id.startswith("CBMi")


def test_seeking_alpha_reads_symbols_from_both_feed_layouts():
    breaking = [
        parse_seekingalpha_item(element, "breaking")
        for element in parse_feed(_fixture("seekingalpha_breaking.xml"))
    ]
    daily = [
        parse_seekingalpha_item(element, "daily")
        for element in parse_feed(_fixture("seekingalpha_daily.xml"))
    ]

    assert breaking[0].source == "seekingalpha/breaking"
    assert breaking[0].tickers[:3] == ("XLF", "VFH", "IYF")
    assert breaking[0].summary is None
    assert breaking[0].published_at == "2026-10-09T14:05:06Z"
    assert daily[0].tickers == ()
    assert daily[1].tickers == ("SPCX", "META", "AAPL", "TMUS", "VZ", "T")
    assert daily[1].source_item_id == "4953042"
    # 加拿大挂牌（SBUX:CA）不是美股代码，丢弃。
    assert not any(":" in ticker for item in daily for ticker in item.tickers)
    assert "CMG" in daily[2].tickers


def test_feed_conditional_request_reuses_validators_and_keeps_cursor_on_304():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.headers.get("if-none-match") == 'W/"v1"':
            return httpx.Response(304)
        return httpx.Response(
            200,
            headers={"ETag": 'W/"v1"', "Last-Modified": "Fri, 09 Oct 2026 13:42:15 GMT"},
            content=_fixture("seekingalpha_breaking.xml"),
        )

    url, kind = news_sources.SEEKING_ALPHA_FEEDS["seekingalpha_breaking"]

    async def scenario():
        async with _client(handler) as client:
            first = await fetch_seekingalpha(client, SourceRequest(now=NOW), url=url, kind=kind)
            second = await fetch_seekingalpha(
                client, SourceRequest(now=NOW, cursor=first.cursor), url=url, kind=kind
            )
            return first, second

    first, second = _run(scenario())

    assert len(first.items) == 7
    assert json.loads(first.cursor) == {
        "etag": 'W/"v1"',
        "last_modified": "Fri, 09 Oct 2026 13:42:15 GMT",
    }
    assert requests[1].headers["if-modified-since"] == "Fri, 09 Oct 2026 13:42:15 GMT"
    assert second.not_modified is True
    assert second.items == ()
    assert second.cursor == first.cursor


def test_redirects_are_followed_once_and_only_on_the_same_host():
    def same_host(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/RssFeed/moved":
            return httpx.Response(200, content=_fixture("globenewswire.xml"))
        return httpx.Response(301, headers={"Location": "/RssFeed/moved"})

    batch = _run(
        _with_client(same_host, lambda client: fetch_globenewswire(client, SourceRequest(now=NOW)))
    )
    assert len(batch.items) == 17

    def other_host(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "news.google.com"
        return httpx.Response(302, headers={"Location": "https://consent.example/redirect"})

    with pytest.raises(SourceError) as raised:
        _run(
            _with_client(
                other_host,
                lambda client: fetch_google_news(
                    client,
                    SourceRequest(now=NOW),
                    url=news_sources.GOOGLE_NEWS_FEEDS["google_news_stocks"],
                ),
            )
        )
    assert raised.value.code == "redirect_refused"

    def chain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": f"{request.url.path}x"})

    with pytest.raises(SourceError) as raised:
        _run(
            _with_client(
                chain,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )
    assert raised.value.code == "redirect_refused"


def test_bodies_are_capped_and_document_type_declarations_rejected():
    def declared(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Length": "5000"}, content=b"x" * 5000)

    with pytest.raises(SourceError, match="response_too_large"):
        _run(
            _with_client(
                declared,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )

    async def chunks():
        yield b"x" * 600
        yield b"x" * 600

    def streamed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=chunks())

    with pytest.raises(SourceError, match="response_too_large"):
        _run(
            _with_client(
                streamed,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )

    hostile = (
        b'<?xml version="1.0"?><!DOCTYPE rss [<!ENTITY a "aaaa">]>'
        b"<rss><channel><item><title>&a;</title></item></channel></rss>"
    )
    with pytest.raises(SourceError, match="invalid_response"):
        parse_feed(hostile)
    with pytest.raises(SourceError, match="invalid_response"):
        parse_feed(b"<html><body>blocked</body></html>")


def test_status_and_transport_failures_are_classified():
    def unavailable(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="upstream detail must not leak")

    with pytest.raises(SourceError) as raised:
        _run(
            _with_client(
                unavailable,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )
    assert raised.value.code == "http_503"
    assert "leak" not in str(raised.value)

    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(SourceError, match="timeout"):
        _run(
            _with_client(
                slow,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )

    async def not_gzip():
        yield b"not gzip"

    def corrupt(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"Content-Encoding": "gzip"}, content=not_gzip())

    with pytest.raises(SourceError, match="invalid_response"):
        _run(
            _with_client(
                corrupt,
                lambda client: fetch_url(client, "https://seekingalpha.com/a", max_bytes=1_000),
            )
        )


def test_shared_client_ignores_environment_proxies_and_redirects(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.invalid:3128")

    async def inspect():
        async with http_client() as client:
            return client.follow_redirects, client._trust_env, client.headers["user-agent"]

    follow, trust_env, agent = _run(inspect())
    assert follow is False
    assert trust_env is False
    assert agent == news_sources.USER_AGENT


def test_fetchers_exist_only_for_configured_credentials():
    async def build(settings):
        async with http_client() as client:
            return set(build_news_fetchers(settings, client))

    keyless = SimpleNamespace(massive_api_key="", finnhub_api_key="")
    keyed = SimpleNamespace(
        massive_api_key="m",
        massive_base_url=MASSIVE_BASE,
        finnhub_api_key="f",
        finnhub_base_url=FINNHUB_BASE,
    )

    assert _run(build(keyless)) == {
        "globenewswire",
        "google_news_stocks",
        "google_news_commodities",
        "google_news_macro",
        "seekingalpha_breaking",
        "seekingalpha_daily",
    }
    assert _run(build(keyed)) == {spec.key for spec in NEWS_SOURCES}


def test_items_lose_lone_surrogates_and_unusable_addresses():
    raw = json.loads(
        '{"id":"m\\ud83d-9","publisher":{"name":"Zacks \\udc00Research"},'
        '"title":"Chip stocks rally \\ud83d after earnings","description":"cut \\ud83d",'
        '"published_utc":"2026-10-09T12:00:00Z","article_url":"https://www.zacks.com/a/9",'
        '"image_url":"https://img.example:99999/x.png"}'
    )
    item = parse_massive_item(raw)

    assert item.title == "Chip stocks rally  after earnings"
    assert item.summary == "cut"
    assert item.source == "massive/Zacks Research"
    assert item.source_item_id == "m-9"
    assert item.image_url is None
    for address in (
        "https://www.zacks.com:99999/a",
        "https://www.zacks.com:abc/a",
        "https://www.zacks.com:0/a",
        "javascript:alert(1)",
        "ftp://files.example/a",
        "https:///no-host",
    ):
        assert parse_massive_item({**raw, "article_url": address}) is None
    finnhub = parse_finnhub_item(
        json.loads('{"id":2,"headline":"Fed minutes due","url":"https://x.example/2",'
                   '"datetime":1791554700,"source":"Reu\\udc00ters"}')
    )
    assert finnhub.source == "finnhub/Reuters"


def test_publication_dates_before_1970_are_unparseable_and_years_stay_padded():
    raw = json.loads(_fixture("massive_news.json"))["results"][0]

    assert parse_massive_item({**raw, "published_utc": "0999-01-01T00:00:00Z"}).published_at is None
    assert parse_massive_item({**raw, "published_utc": "1969-12-31T23:59:59Z"}).published_at is None
    assert news_sources.utc_seconds(datetime(999, 1, 2, 3, 4, 5, tzinfo=timezone.utc)) == (
        "0999-01-02T03:04:05Z"
    )
    assert news_sources.utc_micros(datetime(999, 1, 2, 3, 4, 5, tzinfo=timezone.utc)) == (
        "0999-01-02T03:04:05.000000Z"
    )
    assert parse_finnhub_item(
        {"id": 3, "headline": "Huge timestamp", "url": "https://x.example/3", "datetime": 10**20}
    ).published_at is None


def test_hostile_documents_are_classified_as_invalid_responses():
    with pytest.raises(SourceError, match="invalid_response"):
        news_sources.parse_json(b"[" * 200_000 + b"]" * 200_000)
    with pytest.raises(SourceError, match="invalid_response"):
        parse_feed(b'<?xml version="1.0" encoding="x-unknown-enc"?><rss><channel></channel></rss>')
