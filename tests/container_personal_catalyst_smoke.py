"""Run one catalyst sync round inside the worker image with offline sources.

Every news and calendar address is answered from ``fixtures/catalyst_sources``
through an ``httpx.MockTransport``; the real fetchers and parsers run, nothing
leaves the container.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx

from app.config import Settings
from app.personal_config import get_personal_config
from app.services.catalysts.calendar_source import CALENDAR_URL
from app.services.catalysts.news_collector import default_fetchers
from app.services.catalysts.news_sources import (
    GLOBENEWSWIRE_FEED_URL,
    GOOGLE_NEWS_FEEDS,
    SEEKING_ALPHA_FEEDS,
    http_client,
)
from app.worker.tasks import CatalystSyncTask


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "catalyst_sources"
MASSIVE_KEY = "container-fixture-massive-key"
FINNHUB_KEY = "container-fixture-finnhub-key"
# The first sync round of a fresh store: every source is due once, and the
# one-minute host spacing leaves one Finnhub category, one Google search and
# one Seeking Alpha feed for the following slots.
EXPECTED_SOURCES = [
    "finnhub_general",
    "forexfactory",
    "globenewswire",
    "google_news_stocks",
    "massive",
    "seekingalpha_breaking",
]


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlsplit(url).query)


def _routes() -> dict[str, tuple[str, str]]:
    routes = {
        GLOBENEWSWIRE_FEED_URL: ("globenewswire", "globenewswire.xml"),
        CALENDAR_URL: ("forexfactory", "forexfactory_thisweek.json"),
    }
    for key, (url, _kind) in SEEKING_ALPHA_FEEDS.items():
        routes[url] = (key, f"{key}.xml")
    return routes


def _handler(requested: list[str]):
    routes = _routes()
    google = {_query(url)["q"][0]: key for key, url in GOOGLE_NEWS_FEEDS.items()}

    def fixture(name: str) -> httpx.Response:
        content_type = "application/json" if name.endswith(".json") else "application/xml"
        return httpx.Response(
            200,
            headers={"content-type": content_type},
            content=(FIXTURES / name).read_bytes(),
        )

    def handle(request: httpx.Request) -> httpx.Response:
        url = request.url
        if url.host == "api.massive.com" and url.path == "/v2/reference/news":
            assert request.headers.get_list("authorization") == [f"Bearer {MASSIVE_KEY}"]
            if "cursor" in url.params:
                return httpx.Response(200, json={"results": [], "status": "OK"})
            requested.append("massive")
            return fixture("massive_news.json")
        if url.host == "finnhub.io" and url.path == "/api/v1/news":
            assert request.headers.get_list("x-finnhub-token") == [FINNHUB_KEY]
            category = url.params["category"]
            requested.append(f"finnhub_{category}")
            return fixture(f"finnhub_{category}.json")
        if url.host == "news.google.com" and url.path == "/rss/search":
            key = google[url.params["q"]]
            requested.append(key)
            return fixture(f"{key}.xml")
        route = routes.get(str(url))
        assert route is not None, f"unexpected request to {url.host}{url.path}"
        requested.append(route[0])
        return fixture(route[1])

    return handle


async def _run() -> dict:
    settings = Settings(massive_api_key=MASSIVE_KEY, finnhub_api_key=FINNHUB_KEY)
    requested: list[str] = []
    client = http_client(transport=httpx.MockTransport(_handler(requested)))
    task = CatalystSyncTask(
        "container-catalyst-smoke",
        settings=settings,
        personal_config=get_personal_config(),
        source_fetchers=default_fetchers(settings, client),
    )
    try:
        result = await task()
    finally:
        await task.aclose()
        await client.aclose()
    assert settings.openai_model == "claude-haiku-5-5"
    assert settings.openai_reasoning == "xhigh"
    assert settings.openai_max_concurrency == 4
    assert result.status == "idle", result.details
    assert sorted(requested) == EXPECTED_SOURCES, requested
    news = result.details["streams"]["news"]
    calendar = result.details["streams"]["calendar"]
    assert news["sources_failed"] == 0 and news["new"] > 0
    assert calendar["snapshot_written"] is True and calendar["events"] > 0
    return {
        "status": result.status,
        "processed": result.details["processed"],
        "model": settings.openai_model,
        "reasoning": settings.openai_reasoning,
        "max_concurrency": settings.openai_max_concurrency,
        "sources_requested": sorted(requested),
        "all_due_sources_requested": sorted(requested) == EXPECTED_SOURCES,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            asyncio.run(_run()),
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        )
    )
