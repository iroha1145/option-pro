"""The catalyst worker task collecting news locally (``catalyst.news_source = "local"``).

Fetchers are injected through ``source_fetchers``, the source-key-to-fetch-function
map that replaces the MacroLens transport hook for this path.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from app.personal_config import CatalystConfig, FeatureConfig, PersonalConfig
from app.services.catalysts.calendar_source import CalendarBatch, normalize_events
from app.services.catalysts.news_sources import NEWS_SOURCES, NewsBatch, SourceError, SourceItem
from app.worker.tasks import CatalystSyncTask, FocusTask
from test_catalyst_calendar_source import _raw_events
from test_personal_worker import _empty_etl_page, _runtime_settings, _worker_config


PUBLISHED = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _runtime(monkeypatch):
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(),
    )


def _config(news_source: str = "local") -> PersonalConfig:
    return PersonalConfig(
        features=FeatureConfig(catalyst_mode="read"),
        catalyst=CatalystConfig(news_source=news_source, journal_retention_days=9),
    )


def _items(prefix: str, count: int, *, source: str) -> tuple[SourceItem, ...]:
    return tuple(
        SourceItem(
            source_item_id=f"{prefix}{index}",
            source=source,
            title=f"{prefix} headline number {index} about chip demand {index * 7}",
            url=f"https://news.example/{prefix}/{index}",
            summary="English summary",
            published_at=PUBLISHED,
            tickers=("NVDA",),
        )
        for index in range(count)
    )


class Feeds:
    def __init__(self, **results) -> None:
        self.results = dict(results)
        self.calls: list[str] = []

    def __call__(self, key: str):
        async def fetch(_request):
            self.calls.append(key)
            result = self.results.get(key, NewsBatch())
            if isinstance(result, SourceError):
                raise result
            return result

        return fetch

    def mapping(self, *keys: str) -> dict:
        return {key: self(key) for key in keys}


def _task(tmp_path: Path, fetchers: dict, **options) -> CatalystSyncTask:
    return CatalystSyncTask(
        "local-worker",
        settings=_worker_config(tmp_path),
        personal_config=options.pop("personal_config", _config()),
        source_fetchers=fetchers,
        **options,
    )


async def _run(task: CatalystSyncTask, rounds: int = 1):
    results = []
    try:
        for _ in range(rounds):
            results.append(await task())
    finally:
        await task.aclose()
    return results


def test_local_round_collects_reconciles_and_prunes_without_macrolens(tmp_path):
    feeds = Feeds(
        massive=NewsBatch(_items("massive", 3, source="massive/Zacks Investment Research")),
        forexfactory=CalendarBatch(normalize_events(_raw_events())),
    )
    task = _task(tmp_path, feeds.mapping("massive", "forexfactory"))

    [result] = asyncio.run(_run(task))

    assert result.status == "idle"
    assert result.details["processed"] == [
        "news",
        "calendar",
        "local_intelligence",
        "journal_prune",
    ]
    assert result.details["streams"]["news"]["new"] == 3
    assert result.details["streams"]["calendar"]["snapshot_written"] is True
    assert result.details["local_intelligence"]["ingested"] == 3
    assert result.details["journal_prune"]["pruned_ingest_rows"] == 0
    assert "errors" not in result.details
    assert sorted(feeds.calls) == ["forexfactory", "massive"]
    with sqlite3.connect(tmp_path / "catalyst-cache.db") as connection:
        states = dict(
            connection.execute(
                "SELECT stream,completed_watermark_sequence FROM macrolens_etl_state"
            ).fetchall()
        )
        revisions = connection.execute(
            "SELECT COUNT(*) FROM catalyst_local_news_revisions"
        ).fetchone()[0]
    assert states == {"news": 3, "calendar": 1}
    assert revisions == 3


def test_one_failing_source_is_reported_without_degrading_the_task(tmp_path):
    feeds = Feeds(
        massive=SourceError("http_503"),
        globenewswire=NewsBatch(_items("gnw", 1, source="globenewswire/public_companies")),
        forexfactory=CalendarBatch(normalize_events(_raw_events())),
    )
    task = _task(tmp_path, feeds.mapping("massive", "globenewswire", "forexfactory"))

    [result] = asyncio.run(_run(task))

    assert result.status == "idle"
    assert result.error_code is None
    assert result.details["processed"][:3] == ["news", "calendar", "local_intelligence"]
    assert result.details["errors"] == {"source:massive": "http_503"}


def test_one_malformed_item_never_blocks_the_healthy_sources(tmp_path):
    bad = SourceItem(
        source_item_id="m-bad",
        source="massive/Zacks Investment Research",
        title="Chip stocks rally \ud83d after earnings",
        url="https://www.zacks.com/a/bad",
        published_at=PUBLISHED,
    )
    feeds = Feeds(
        massive=NewsBatch((bad,)),
        globenewswire=NewsBatch(_items("gnw", 3, source="globenewswire/public_companies")),
        forexfactory=CalendarBatch(normalize_events(_raw_events())),
    )
    task = _task(tmp_path, feeds.mapping("massive", "globenewswire", "forexfactory"))

    async def scenario():
        first = await task()
        # Next sync slot, with Massive due again and the bad item still listed.
        task._last_personal_sync_monotonic = None
        task._collector._clock = lambda: datetime.now(timezone.utc) + timedelta(minutes=10)
        second = await task()
        await task.aclose()
        return [first, second]

    results = asyncio.run(scenario())

    assert [result.status for result in results] == ["idle", "idle"]
    assert all(
        result.details["errors"] == {"source:massive": "invalid_items"} for result in results
    )
    assert results[0].details["streams"]["news"]["new"] == 3
    assert feeds.calls.count("massive") == 2
    with sqlite3.connect(tmp_path / "catalyst-cache.db") as connection:
        stored = connection.execute("SELECT COUNT(*) FROM macrolens_etl_news").fetchone()[0]
    assert stored == 3


def test_a_failing_calendar_without_any_snapshot_degrades_the_task(tmp_path):
    feeds = Feeds(
        massive=NewsBatch(_items("massive", 1, source="massive/Benzinga")),
        forexfactory=SourceError("timeout"),
    )
    task = _task(tmp_path, feeds.mapping("massive", "forexfactory"))

    [result] = asyncio.run(_run(task))

    assert result.status == "degraded"
    assert result.details["errors"] == {
        "calendar": "calendar_stale",
        "source:forexfactory": "timeout",
    }
    assert result.details["processed"][:2] == ["news", "local_intelligence"]


def test_a_news_outage_stays_degraded_on_every_slot_until_a_source_recovers(tmp_path):
    feeds = Feeds(
        massive=SourceError("http_503"),
        globenewswire=SourceError("http_403"),
        forexfactory=CalendarBatch(normalize_events(_raw_events())),
    )
    task = _task(tmp_path, feeds.mapping("massive", "globenewswire", "forexfactory"))

    async def slot(offset: timedelta):
        task._last_personal_sync_monotonic = None
        task._collector._clock = lambda: datetime.now(timezone.utc) + offset
        return await task()

    async def scenario():
        results = [await task()]
        # Every failed source is backing off; nothing is due on the next slot.
        results.append(await slot(timedelta(minutes=2)))
        feeds.results["massive"] = NewsBatch(_items("massive", 1, source="massive/Benzinga"))
        results.append(await slot(timedelta(hours=2)))
        await task.aclose()
        return results

    first, second, recovered = asyncio.run(scenario())

    for result in (first, second):
        assert result.status == "degraded"
        assert result.error_code == "catalyst_sync_degraded"
        assert result.details["errors"]["news"] == "news_sources_stale"
        assert "news" not in result.details["processed"]
        assert "calendar" in result.details["processed"]
        # The collector waits for its next slot instead of the supervisor's backoff.
        assert 100 < result.next_delay_seconds <= 120
    assert first.details["errors"] == {
        "news": "news_sources_stale",
        "source:massive": "http_503",
        "source:globenewswire": "http_403",
    }
    assert second.details["streams"]["news"]["sources_due"] == 0
    assert recovered.status == "idle"


def test_manual_news_refresh_forces_news_sources_and_leaves_the_calendar(tmp_path):
    completed: list[tuple[str, str | None]] = []
    requests = [None, {"request_id": "manual-1", "operation_type": "news"}]

    class Intelligence:
        def __init__(self, *_args, **_options) -> None:
            pass

        def initialize(self) -> None:
            return None

        def consume_refresh_requested(self):
            return requests.pop(0)

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            return {"ingested": 0}

        def complete_refresh_request(self, request_id: str, *, error_code=None) -> None:
            completed.append((request_id, error_code))

    feeds = Feeds(
        massive=NewsBatch(_items("massive", 1, source="massive/Benzinga")),
        forexfactory=CalendarBatch(normalize_events(_raw_events())),
    )
    task = _task(
        tmp_path,
        feeds.mapping("massive", "forexfactory"),
        intelligence_factory=Intelligence,
    )

    async def scenario():
        first = await task()
        # Ninety seconds later the next sync slot is still away and Massive is
        # not due for minutes; the owner's request runs the news stream anyway.
        task._collector._clock = lambda: datetime.now(timezone.utc) + timedelta(seconds=90)
        second = await task()
        await task.aclose()
        return first, second

    first, second = asyncio.run(scenario())

    assert first.details["processed"][:2] == ["news", "calendar"]
    assert feeds.calls == ["massive", "forexfactory", "massive"]
    # The calendar stream still runs to report its health; nothing of it is due.
    assert second.details["processed"] == ["news", "calendar", "local_intelligence"]
    assert second.details["streams"]["calendar"]["sources_due"] == 0
    assert second.details["refresh_operation_type"] == "news"
    assert second.details["streams"]["news"]["sources_due"] == 1
    assert completed == [("manual-1", None)]


def test_rollback_to_macrolens_is_refused_after_local_writes(tmp_path, monkeypatch):
    feeds = Feeds(massive=NewsBatch(_items("massive", 1, source="massive/Benzinga")))
    asyncio.run(_run(_task(tmp_path, feeds.mapping("massive"))))
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    remote = CatalystSyncTask(
        "rollback",
        settings=_worker_config(tmp_path, token="owner-token", url="https://macrolens.example"),
        personal_config=_config("macrolens"),
        etl_transport=httpx.MockTransport(handler),
    )

    [result] = asyncio.run(_run(remote))

    assert result.status == "degraded"
    assert result.error_code == "catalyst_rollback_requires_restore"
    assert requested == []


def test_rollback_before_any_local_write_resumes_the_remote_sync(tmp_path, monkeypatch):
    feeds = Feeds(**{spec.key: SourceError("http_503") for spec in NEWS_SOURCES})
    feeds.results["forexfactory"] = SourceError("timeout")
    asyncio.run(
        _run(_task(tmp_path, feeds.mapping(*(spec.key for spec in NEWS_SOURCES), "forexfactory")))
    )
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    remote = CatalystSyncTask(
        "rollback",
        settings=_worker_config(tmp_path, token="owner-token", url="https://macrolens.example"),
        personal_config=_config("macrolens"),
        etl_transport=httpx.MockTransport(handler),
    )

    [result] = asyncio.run(_run(remote))

    assert result.status == "idle"
    assert requested == ["/internal/v1/news/changes", "/internal/v1/calendar"]


def test_default_fetchers_cover_keyless_sources_and_close_their_client(tmp_path):
    task = CatalystSyncTask(
        "default-fetchers",
        settings=_worker_config(tmp_path),
        personal_config=_config(),
    )

    async def scenario():
        assert await task._prepare() == "local"
        keys = set(task._collector._fetchers)
        client = task._client
        await task.aclose()
        return keys, client

    keys, client = asyncio.run(scenario())

    assert keys == {
        "globenewswire",
        "google_news_stocks",
        "google_news_commodities",
        "google_news_macro",
        "seekingalpha_breaking",
        "seekingalpha_daily",
        "forexfactory",
    }
    assert client.is_closed


def test_focus_runs_in_local_mode_without_macrolens_settings(tmp_path):
    calls: list[str] = []

    class Intelligence:
        def __init__(self, *_args, **_options) -> None:
            pass

        def initialize(self) -> None:
            calls.append("initialize")

    focus = FocusTask(
        "local-focus",
        enabled=True,
        settings=_worker_config(tmp_path),
        personal_config=_config(),
        intelligence_factory=Intelligence,
    )
    remote_focus = FocusTask(
        "remote-focus",
        enabled=True,
        settings=_worker_config(tmp_path),
        personal_config=_config("macrolens"),
        intelligence_factory=Intelligence,
    )

    result = asyncio.run(focus())
    remote = asyncio.run(remote_focus())

    assert result.status == "idle"
    assert result.details["result"] == "not_scheduled"
    assert remote.status == "disabled"
    assert calls == ["initialize"]


def test_source_metrics_and_errors_fit_the_status_row(tmp_path):
    feeds = Feeds(**{spec.key: SourceError("http_503") for spec in NEWS_SOURCES})
    task = _task(tmp_path, feeds.mapping(*(spec.key for spec in NEWS_SOURCES), "forexfactory"))

    [result] = asyncio.run(_run(task))

    encoded = json.dumps(result.details, ensure_ascii=False, separators=(",", ":"))
    assert len(encoded.encode()) < 16 * 1024
    assert all(isinstance(value, (int, bool)) for value in result.details["streams"]["news"].values())
