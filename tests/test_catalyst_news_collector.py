from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.access import request_owner_access_context
from app.services.catalysts import news_collector as collector_module
from app.services.catalysts.calendar_source import CalendarBatch, normalize_events
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.services.catalysts.news_collector import (
    INGEST_SCHEMA_VERSION,
    NewsCollector,
    local_ingest_has_written,
    source_health,
)
from app.services.catalysts.news_dedup import compute_content_hash, normalize_url
from app.services.catalysts.news_sources import (
    NEWS_SOURCES,
    NewsBatch,
    SourceError,
    SourceItem,
)
from test_catalyst_calendar_source import _raw_events
from test_catalyst_local_intelligence import _apply_news, _news_change, _stack


T0 = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)
PUBLISHED = "2026-10-09T11:30:00Z"


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **delta: float) -> None:
        self.now += timedelta(**delta)


def _sources(**overrides: tuple[bool, int]) -> SimpleNamespace:
    defaults = {
        "massive": (True, 300),
        "finnhub": (True, 300),
        "globenewswire": (True, 300),
        "google_news": (True, 900),
        "seekingalpha_breaking": (True, 300),
        "seekingalpha_daily": (True, 21_600),
        "calendar": (True, 600),
    }
    defaults.update(overrides)
    return SimpleNamespace(
        **{
            name: SimpleNamespace(enabled=enabled, interval_seconds=seconds)
            for name, (enabled, seconds) in defaults.items()
        }
    )


def _item(
    item_id: str,
    title: str,
    *,
    source: str = "massive/Zacks Investment Research",
    url: str | None = None,
    published_at: str | None = PUBLISHED,
    tickers: tuple[str, ...] = ("NVDA",),
    summary: str | None = "English summary",
) -> SourceItem:
    return SourceItem(
        source_item_id=item_id,
        source=source,
        title=title,
        url=url or f"https://news.example/{item_id}?utm_source=feed",
        summary=summary,
        published_at=published_at,
        tickers=tickers,
    )


class Feeds:
    """Programmable fetchers: one entry per source key."""

    def __init__(self) -> None:
        self.batches: dict[str, NewsBatch | CalendarBatch | SourceError] = {}
        self.requests: list[tuple[str, object]] = []

    def fetcher(self, key: str):
        async def fetch(request):
            self.requests.append((key, request))
            value = self.batches.get(key, NewsBatch())
            if isinstance(value, SourceError):
                raise value
            return value

        return fetch

    def mapping(self, *keys: str) -> dict:
        return {key: self.fetcher(key) for key in keys}


def _collector(tmp_path, feeds: Feeds, clock: Clock, *keys: str, sources=None) -> NewsCollector:
    repository = CatalystEtlRepository(tmp_path / "catalyst-cache.db")
    collector = NewsCollector(
        repository,
        sources=sources or _sources(),
        fetchers=feeds.mapping(*(keys or ("massive",))),
        clock=clock,
    )
    collector.initialize()
    return collector


def _round(collector: NewsCollector, stream: str = "news", *, force: bool = False):
    plan = collector.plan(stream, force=force)
    outcomes = asyncio.run(collector.fetch(plan))
    return collector.commit(plan, outcomes)


def _changes(repository: CatalystEtlRepository) -> list[dict]:
    with sqlite3.connect(repository.path) as connection:
        rows = connection.execute(
            "SELECT raw_json FROM macrolens_etl_news_changes ORDER BY change_sequence"
        ).fetchall()
    return [json.loads(row[0]) for row in rows]


def _query(repository: CatalystEtlRepository, statement: str, *values) -> list[tuple]:
    with sqlite3.connect(repository.path) as connection:
        return connection.execute(statement, values).fetchall()


def test_a_new_item_mints_an_id_and_one_complete_change(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    feeds.batches["massive"] = NewsBatch(
        (_item("m1", "Nvidia stock jumps after earnings beat"),),
        cursor="2026-10-09T11:30:00Z",
    )

    outcome = _round(collector)

    assert outcome.error_code is None
    assert outcome.metrics["new"] == 1
    [change] = _changes(collector.repository)
    news = change["news"]
    assert change["sequence"] == 1 and change["news_id"] == 1 and news["id"] == 1
    assert news["source"] == "massive/Zacks Investment Research"
    assert news["sources"] == ["massive/Zacks Investment Research"]
    assert news["source_count"] == 1
    assert news["url"] == "https://news.example/m1"
    assert news["content_hash"] == compute_content_hash(
        "Nvidia stock jumps after earnings beat", "https://news.example/m1", PUBLISHED
    )
    assert len(news["published_at"]) == 20
    assert len(news["fetched_at"]) == 27
    assert len(change["available_at"]) == 27
    assert change["available_at"] == "2026-10-09T12:00:00.000000Z"
    state = collector.repository.state("news")
    assert state.cursor is None
    assert state.completed_watermark_sequence == 1
    assert state.completed_as_of == change["available_at"]
    assert state.updated_after == change["available_at"]
    assert _query(
        collector.repository,
        """SELECT cursor,consecutive_failures,last_error_code FROM catalyst_ingest_sources
           WHERE source_key='massive'""",
    ) == [("2026-10-09T11:30:00Z", 0, None)]


def test_the_same_fixture_twice_produces_no_new_change(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    batch = NewsBatch(
        (
            _item("m1", "Fed holds rates steady"),
            _item("m2", "Treasury yields edge higher ahead of inflation data"),
        )
    )
    feeds.batches["massive"] = batch
    _round(collector)
    clock.advance(minutes=10)

    second = _round(collector)

    assert second.metrics["new"] == 0
    assert second.metrics["observed"] == 0
    assert len(_changes(collector.repository)) == 2
    assert collector.repository.state("news").completed_as_of == "2026-10-09T12:10:00.000000Z"


def test_sources_seen_in_one_round_share_one_change(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "globenewswire")
    title = "McEwen Signs US$55 Million Agreement to Sell Fuller and Paymaster"
    feeds.batches["massive"] = NewsBatch(
        (_item("m1", title, source="massive/GlobeNewswire Inc.", tickers=("MUX",)),)
    )
    feeds.batches["globenewswire"] = NewsBatch(
        (
            _item(
                "3378099",
                title,
                source="globenewswire/public_companies",
                url="https://www.globenewswire.com/news-release/2026/10/09/3378099/0/en/x.html",
                tickers=("MUX", "MUXF"),
            ),
        )
    )

    outcome = _round(collector)

    [change] = _changes(collector.repository)
    assert outcome.metrics["new"] == 1
    assert outcome.metrics["observed"] == 2
    assert change["news"]["sources"] == [
        "massive/GlobeNewswire Inc.",
        "globenewswire/public_companies",
    ]
    assert change["news"]["source_tickers"] == ["MUX", "MUXF"]


def test_corroboration_appends_once_within_six_hours_and_keeps_the_text(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "finnhub_general", "google_news_stocks")
    title = "Wall Street ends lower as Treasury yields climb"
    feeds.batches["massive"] = NewsBatch((_item("m1", title, tickers=()),))
    _round(collector)

    clock.advance(hours=1)
    feeds.batches["massive"] = NewsBatch()
    feeds.batches["finnhub_general"] = NewsBatch(
        (
            _item(
                "f1",
                title,
                source="finnhub/Reuters",
                url="https://www.reuters.com/markets/us/wall-street",
                tickers=("SPY",),
                summary="A different summary",
            ),
        )
    )
    first = _round(collector)

    clock.advance(minutes=5)
    feeds.batches["finnhub_general"] = NewsBatch()
    feeds.batches["google_news_stocks"] = NewsBatch(
        (_item("g1", f"{title} - Reuters", source="google/Reuters", tickers=()),)
    )
    second = _round(collector, force=True)

    changes = _changes(collector.repository)
    assert first.metrics["corroborated"] == 1
    assert second.metrics["corroborated"] == 0
    assert second.metrics["observed"] == 1
    assert [change["news_id"] for change in changes] == [1, 1]
    original, appended = (change["news"] for change in changes)
    assert appended["sources"] == ["massive/Zacks Investment Research", "finnhub/Reuters"]
    assert appended["source_tickers"] == ["SPY"]
    for field_name in ("title", "summary", "url", "content_hash", "published_at", "fetched_at"):
        assert appended[field_name] == original[field_name]
    assert changes[1]["available_at"] > changes[0]["available_at"]
    assert _query(
        collector.repository,
        "SELECT source_key,news_id FROM catalyst_ingest_observations ORDER BY source_key",
    ) == [("finnhub_general", 1), ("google_news_stocks", 1), ("massive", 1)]


def test_corroboration_after_six_hours_only_records_an_observation(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "finnhub_general")
    feeds.batches["massive"] = NewsBatch((_item("m1", "Oil climbs on supply worries"),))
    _round(collector)
    clock.advance(hours=6, minutes=1)
    feeds.batches["massive"] = NewsBatch()
    feeds.batches["finnhub_general"] = NewsBatch(
        (_item("f1", "Oil climbs on supply worries", source="finnhub/CNBC"),)
    )

    outcome = _round(collector)

    assert outcome.metrics["corroborated"] == 0
    assert len(_changes(collector.repository)) == 1


def test_corroboration_never_appends_once_an_analysis_link_exists(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    with request_owner_access_context(True):
        _stack(tmp_path)
    collector = _collector(tmp_path, feeds, clock, "massive", "finnhub_general")
    feeds.batches["massive"] = NewsBatch((_item("m1", "Chipmakers slide on export curbs"),))
    _round(collector)
    with request_owner_access_context(True):
        _etl, _ai, intelligence = _stack(tmp_path)
        intelligence.reconcile(allow_scheduled_jobs=False)
    with sqlite3.connect(collector.repository.path) as connection:
        revision = connection.execute(
            "SELECT news_id,change_sequence,content_hash FROM catalyst_local_news_revisions"
        ).fetchone()
        connection.execute(
            """INSERT INTO catalyst_local_analysis_links(
                   news_id,change_sequence,content_hash,job_id,created_at
               ) VALUES(?,?,?,?,?)""",
            (*revision, "job-1", "2026-10-09T12:01:00Z"),
        )
    clock.advance(minutes=30)
    feeds.batches["massive"] = NewsBatch()
    feeds.batches["finnhub_general"] = NewsBatch(
        (_item("f1", "Chipmakers slide on export curbs", source="finnhub/MarketWatch"),)
    )

    outcome = _round(collector)

    assert outcome.metrics["corroborated"] == 0
    assert len(_changes(collector.repository)) == 1


def test_news_stored_before_the_switch_is_matched_and_never_extended(tmp_path):
    repository = CatalystEtlRepository(tmp_path / "catalyst-cache.db")
    repository.initialize()
    remote_title = "Fed holds rates steady"
    remote = _news_change(
        41,
        900,
        available_at=T0 - timedelta(hours=1),
        title=remote_title,
        content_hash=compute_content_hash(remote_title, "https://remote.example/a", PUBLISHED),
        source="massive/Zacks Investment Research",
    )
    remote["news"]["url"] = "https://remote.example/a"
    remote["news"]["published_at"] = PUBLISHED
    _apply_news(repository, [remote], as_of=T0 - timedelta(minutes=50))
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "finnhub_general")
    feeds.batches["massive"] = NewsBatch(
        (_item("m1", remote_title, url="https://remote.example/a"),)
    )
    feeds.batches["finnhub_general"] = NewsBatch(
        (_item("f1", "Fed Holds Rates Steady!", source="finnhub/Reuters", tickers=("TLT",)),)
    )

    outcome = _round(collector)

    assert outcome.metrics["new"] == 0
    assert outcome.metrics["corroborated"] == 0
    assert outcome.metrics["observed"] == 2
    assert [change["sequence"] for change in _changes(repository)] == [41]
    assert _query(repository, "SELECT preexisting FROM catalyst_ingest_items") == [(1,)]
    assert repository.state("news").completed_as_of == "2026-10-09T12:00:00.000000Z"


def test_counters_continue_above_every_table_that_still_references_ids(tmp_path):
    with request_owner_access_context(True):
        etl, _ai, intelligence = _stack(tmp_path)
    _apply_news(
        etl,
        [_news_change(7, 30, available_at=T0 - timedelta(days=2))],
        as_of=T0 - timedelta(days=2),
    )
    with request_owner_access_context(True):
        intelligence.reconcile(allow_scheduled_jobs=False)
    with sqlite3.connect(etl.path) as connection:
        # 被修剪掉的旧付费分析仍引用更大的序号。
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            """INSERT INTO catalyst_local_analysis_links(
                   news_id,change_sequence,content_hash,job_id,created_at
               ) VALUES(55,812,'hash-old','job-old','2026-09-01T00:00:00Z')"""
        )
        connection.execute(
            """INSERT INTO macrolens_etl_news_tombstones(
                   news_id,change_sequence,deleted_at,source_updated_at,raw_json,applied_at
               ) VALUES(77,300,'2026-09-01T00:00:00Z','2026-09-01T00:00:00Z','{}','2026-09-01T00:00:00Z')"""
        )
    feeds = Feeds()
    collector = _collector(tmp_path, feeds, Clock(T0))
    feeds.batches["massive"] = NewsBatch((_item("m1", "Fresh headline about chips"),))

    _round(collector)

    [change] = [item for item in _changes(etl) if item["sequence"] > 7]
    assert change["news_id"] == 78
    assert change["sequence"] == 813
    assert _query(
        etl, "SELECT name,value FROM catalyst_ingest_counters ORDER BY name"
    ) == [("change_sequence", 813), ("local_writes", 1), ("news_id", 78)]


def test_a_failed_commit_leaves_nothing_and_the_rerun_mints_the_same_ids(tmp_path, monkeypatch):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    feeds.batches["massive"] = NewsBatch(
        (_item("m1", "First headline here"), _item("m2", "Second headline here"))
    )
    plan = collector.plan("news")
    outcomes = asyncio.run(collector.fetch(plan))
    real_write_pages = NewsCollector._write_pages

    def crash(self, *args, **kwargs):
        raise RuntimeError("process died before commit")

    monkeypatch.setattr(NewsCollector, "_write_pages", crash)
    with pytest.raises(RuntimeError):
        collector.commit(plan, outcomes)
    assert _changes(collector.repository) == []
    assert _query(collector.repository, "SELECT COUNT(*) FROM catalyst_ingest_observations") == [(0,)]
    assert _query(collector.repository, "SELECT COUNT(*) FROM catalyst_ingest_sources") == [(0,)]

    monkeypatch.setattr(NewsCollector, "_write_pages", real_write_pages)
    collector.commit(plan, outcomes)
    assert [(change["sequence"], change["news_id"]) for change in _changes(collector.repository)] == [
        (1, 1),
        (2, 2),
    ]


def test_a_clock_moving_backwards_never_moves_available_at_or_the_checkpoint_back(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "finnhub_general")
    feeds.batches["massive"] = NewsBatch((_item("m1", "Dollar steadies after jobs data"),))
    _round(collector)
    clock.now = T0 - timedelta(minutes=30)
    feeds.batches["massive"] = NewsBatch()
    feeds.batches["finnhub_general"] = NewsBatch(
        (_item("f1", "Dollar steadies after jobs data", source="finnhub/Forbes"),)
    )

    _round(collector, force=True)

    first, appended = _changes(collector.repository)
    assert appended["available_at"] == "2026-10-09T12:00:00.000001Z"
    state = collector.repository.state("news")
    assert state.updated_after == "2026-10-09T12:00:00.000001Z"
    assert state.completed_as_of == state.updated_after


def test_more_changes_than_one_page_are_stored_in_one_transaction(tmp_path):
    feeds = Feeds()
    collector = _collector(tmp_path, feeds, Clock(T0))
    feeds.batches["massive"] = NewsBatch(
        tuple(
            _item(f"m{index}", f"Company {index:04d} reports quarterly revenue of {index} million")
            for index in range(1, 1201)
        )
    )

    outcome = _round(collector)

    state = collector.repository.state("news")
    assert outcome.metrics["new"] == 1200
    assert state.cursor is None
    assert state.completed_watermark_sequence == 1200
    assert len(_changes(collector.repository)) == 1200


def test_only_a_round_where_every_due_source_failed_fails_the_stream(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "globenewswire")
    feeds.batches["massive"] = SourceError("http_503")
    feeds.batches["globenewswire"] = NewsBatch((_item("g1", "Contoso Energy declares dividend"),))

    partial = _round(collector)

    assert partial.error_code is None
    assert partial.source_errors == {"source:massive": "http_503"}
    assert collector.repository.state("news").last_error_code is None

    clock.advance(minutes=10)
    feeds.batches["globenewswire"] = SourceError("http_403")
    failed = _round(collector, force=True)

    assert failed.error_code == "http_503"
    assert failed.source_errors == {
        "source:massive": "http_503",
        "source:globenewswire": "http_403",
    }
    assert collector.repository.state("news").last_error_code == "http_503"
    assert _query(
        collector.repository,
        """SELECT source_key,consecutive_failures FROM catalyst_ingest_sources
           WHERE source_key IN ('massive','globenewswire') ORDER BY source_key""",
    ) == [("globenewswire", 1), ("massive", 2)]


def test_failures_back_off_exponentially_and_honor_retry_after(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    feeds.batches["massive"] = SourceError("timeout")
    due_query = "SELECT next_due_at FROM catalyst_ingest_sources WHERE source_key='massive'"
    _round(collector)
    assert _query(collector.repository, due_query) == [("2026-10-09T12:05:00.000000Z",)]
    clock.advance(minutes=5)
    _round(collector)
    assert _query(collector.repository, due_query) == [("2026-10-09T12:15:00.000000Z",)]

    clock.now = datetime(2026, 10, 9, 12, 15, tzinfo=timezone.utc)
    feeds.batches["massive"] = SourceError("rate_limited", retry_after=7200)
    _round(collector)
    assert _query(collector.repository, due_query) == [("2026-10-09T14:15:00.000000Z",)]
    clock.advance(seconds=61)
    assert collector.plan("news").due == ()
    assert [planned.spec.key for planned in collector.plan("news", force=True).due] == ["massive"]


def test_hosts_are_spaced_by_a_minute_even_when_forced(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    keys = ("google_news_stocks", "google_news_commodities", "google_news_macro")
    collector = _collector(tmp_path, feeds, clock, *keys)

    rounds = []
    for _ in range(3):
        rounds.append([planned.spec.key for planned in collector.plan("news", force=True).due])
        _round(collector, force=True)
        clock.advance(seconds=30)
        rounds.append([planned.spec.key for planned in collector.plan("news", force=True).due])
        clock.advance(seconds=90)

    assert rounds == [
        ["google_news_stocks"],
        [],
        ["google_news_stocks"],
        [],
        ["google_news_stocks"],
        [],
    ]
    # Without force, each search waits for its own interval and the host spacing
    # spreads the three searches over consecutive sync slots.
    collector = _collector(tmp_path / "unforced", feeds, Clock(T0), *keys)
    clock = collector._clock
    seen = []
    for _ in range(3):
        seen.append([planned.spec.key for planned in collector.plan("news").due])
        _round(collector)
        clock.advance(seconds=120)
    assert seen == [["google_news_stocks"], ["google_news_commodities"], ["google_news_macro"]]


def test_budget_deferred_and_unconfigured_sources_change_no_checkpoint(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "finnhub_general")
    feeds.batches["finnhub_general"] = NewsBatch(deferred=True)

    outcome = _round(collector)

    assert outcome.metrics["sources_deferred"] == 1
    assert outcome.error_code is None
    assert collector.repository.state("news").last_success_at is None
    rows = dict(
        _query(collector.repository, "SELECT source_key,last_error_code FROM catalyst_ingest_sources")
    )
    assert rows["massive"] == "not_configured"
    assert "finnhub_general" not in rows
    assert collector.plan("news").unconfigured == ()


def test_disabled_sources_are_never_planned(tmp_path):
    feeds = Feeds()
    collector = _collector(
        tmp_path,
        feeds,
        Clock(T0),
        "massive",
        "globenewswire",
        sources=_sources(massive=(False, 300)),
    )

    assert [planned.spec.key for planned in collector.plan("news").due] == ["globenewswire"]


def test_malformed_items_are_skipped_and_the_rest_of_the_round_lands(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "globenewswire")
    poisoned = (
        _item("bad-title", "Chip stocks rally \ud83d after earnings"),
        _item("bad-source", "Fed minutes due today", source="massive/Zacks \udc00Research"),
        _item("bad-summary", "Oil climbs on supply worries", summary="cut \ud83d"),
        _item("bad-port", "Dollar steadies after jobs data", url="https://www.zacks.com:99999/a"),
        _item("good", "Treasury yields edge higher ahead of inflation data"),
    )
    feeds.batches["massive"] = NewsBatch(poisoned, cursor="2026-10-09T11:30:00Z")
    feeds.batches["globenewswire"] = NewsBatch(
        (
            _item("g1", "Contoso Energy declares dividend", source="globenewswire/public_companies"),
            _item("g2", "Fabrikam to acquire Litware", source="globenewswire/public_companies"),
        )
    )

    first = _round(collector)
    clock.advance(minutes=10)
    second = _round(collector, force=True)

    assert first.error_code is None
    assert first.metrics["new"] == 3
    assert first.metrics["invalid_items"] == 4
    assert first.source_errors == {"source:massive": "invalid_items"}
    assert second.metrics["new"] == 0
    assert second.metrics["invalid_items"] == 4
    titles = sorted(change["news"]["title"] for change in _changes(collector.repository))
    assert titles == [
        "Contoso Energy declares dividend",
        "Fabrikam to acquire Litware",
        "Treasury yields edge higher ahead of inflation data",
    ]
    assert [change["news_id"] for change in _changes(collector.repository)] == [1, 2, 3]
    assert _query(
        collector.repository,
        "SELECT source_key,source_item_id FROM catalyst_ingest_observations ORDER BY news_id",
    ) == [("massive", "good"), ("globenewswire", "g1"), ("globenewswire", "g2")]
    assert _query(
        collector.repository,
        """SELECT cursor,consecutive_failures,last_error_code FROM catalyst_ingest_sources
           WHERE source_key='massive'""",
    ) == [("2026-10-09T11:30:00Z", 0, None)]


def test_initialize_is_idempotent_and_versions_its_own_schema(tmp_path):
    feeds = Feeds()
    collector = _collector(tmp_path, feeds, Clock(T0))
    collector.initialize()

    assert _query(collector.repository, "SELECT version FROM catalyst_ingest_schema") == [
        (INGEST_SCHEMA_VERSION,)
    ]
    with sqlite3.connect(collector.repository.path) as connection:
        connection.execute("UPDATE catalyst_ingest_schema SET checksum='tampered'")
    with pytest.raises(collector_module.IngestError, match="checksum_mismatch"):
        collector.initialize()


def test_orphaned_private_rows_follow_the_journal_prune(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    feeds.batches["massive"] = NewsBatch(
        (_item("m1", "Old headline to prune"), _item("m2", "Recent headline to keep"))
    )
    _round(collector)
    with sqlite3.connect(collector.repository.path) as connection:
        connection.execute("DELETE FROM macrolens_etl_news WHERE news_id=1")

    # 两个判重键、模糊池与条目各一行；观察记录留到保留期满。
    assert collector.prune_orphans(retention_days=30) == 4
    assert _query(collector.repository, "SELECT news_id FROM catalyst_ingest_items") == [(2,)]
    observations = "SELECT news_id FROM catalyst_ingest_observations ORDER BY news_id"
    assert _query(collector.repository, observations) == [(1,), (2,)]

    # The feed still lists the pruned story: it is recognised, not minted again.
    clock.advance(minutes=10)
    assert _round(collector, force=True).metrics["new"] == 0

    clock.advance(days=31)
    assert collector.prune_orphans(retention_days=30) == 1
    assert _query(collector.repository, observations) == [(2,)]


def test_collected_news_is_ingested_as_revisions_by_local_intelligence(tmp_path):
    with request_owner_access_context(True):
        _etl, _ai, intelligence = _stack(tmp_path)
    feeds = Feeds()
    collector = _collector(tmp_path, feeds, Clock(T0 + timedelta(microseconds=123_456)))
    feeds.batches["massive"] = NewsBatch(
        (
            _item(
                "m1",
                "Nvidia launches Blackwell platform update",
                tickers=("NVDA", "AMD", "ZZZZ"),
            ),
        )
    )
    _round(collector)

    with request_owner_access_context(True):
        projection = intelligence.reconcile(allow_scheduled_jobs=False)
    rows = _query(
        collector.repository,
        """SELECT news_id,change_sequence,source,raw_title,url,published_at,fetched_at,
                  source_available_at,canonical_tickers_json,source_count
           FROM catalyst_local_news_revisions""",
    )

    assert projection["ingested"] == 1
    [row] = rows
    assert row[:5] == (
        1,
        1,
        "massive/Zacks Investment Research",
        "Nvidia launches Blackwell platform update",
        normalize_url("https://news.example/m1?utm_source=feed"),
    )
    assert row[5] == PUBLISHED
    assert len(row[6]) == 27 and len(row[7]) == 27
    assert json.loads(row[8]) == ["NVDA", "AMD"]
    assert row[9] == 1


def test_source_health_reports_every_enabled_source_that_has_run(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "massive", "globenewswire")
    feeds.batches["massive"] = NewsBatch((_item("m1", "Health check headline"),))
    feeds.batches["globenewswire"] = SourceError("http_403")
    _round(collector)
    clock.advance(minutes=1)

    cards = source_health(
        collector.repository.path,
        sources=_sources(),
        now=clock.now,
        calendar_items_last_24h=4,
    )

    by_key = {card["key"]: card for card in cards}
    assert set(by_key) == {spec.key for spec in NEWS_SOURCES}
    assert by_key["massive"] == {
        "key": "massive",
        "source": "Massive",
        "status": "active",
        "lag_ms": 60_000,
        "last_success_at": "2026-10-09T12:00:00.000000Z",
        "items_last_24h": 1,
        "note": "",
    }
    assert by_key["globenewswire"]["status"] == "degraded"
    assert by_key["globenewswire"]["note"] == "http_403"
    assert by_key["globenewswire"]["lag_ms"] is None
    # Sources without a fetcher (no API key) stay visible instead of silently missing.
    assert by_key["finnhub_general"]["status"] == "degraded"
    assert by_key["finnhub_general"]["note"] == "not_configured"
    disabled = source_health(
        collector.repository.path,
        sources=_sources(google_news=(False, 900)),
        now=clock.now,
        calendar_items_last_24h=None,
    )
    assert not any(card["key"].startswith("google_news") for card in disabled)
    assert (
        source_health(
            tmp_path / "missing.db", sources=_sources(), now=T0, calendar_items_last_24h=None
        )
        == []
    )


def test_local_writes_are_counted_for_the_rollback_guard(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock)
    assert local_ingest_has_written(collector.repository.path) is False

    feeds.batches["massive"] = SourceError("timeout")
    _round(collector)
    assert local_ingest_has_written(collector.repository.path) is False

    clock.advance(minutes=10)
    feeds.batches["massive"] = NewsBatch()
    _round(collector)
    assert local_ingest_has_written(collector.repository.path) is True
    assert local_ingest_has_written(tmp_path / "missing.db") is False


def test_calendar_rounds_write_confirm_and_degrade_only_when_a_day_old(tmp_path):
    feeds = Feeds()
    clock = Clock(T0)
    collector = _collector(tmp_path, feeds, clock, "forexfactory")
    events = normalize_events(_raw_events())
    feeds.batches["forexfactory"] = CalendarBatch(events, cursor='{"etag":"W/\\"v1\\"","last_modified":null}')

    first = _round(collector, "calendar")
    assert first.error_code is None
    assert first.metrics["snapshot_written"] is True
    [(cursor,)] = _query(collector.repository, "SELECT cursor FROM catalyst_ingest_sources")
    assert json.loads(cursor)["token"].startswith("ff-")

    clock.advance(minutes=11)
    plan = collector.plan("calendar")
    assert plan.due[0].request.cursor == cursor
    feeds.batches["forexfactory"] = CalendarBatch(cursor=cursor, not_modified=True)
    second = collector.commit(plan, asyncio.run(collector.fetch(plan)))
    assert second.metrics["snapshot_written"] is False
    assert collector.repository.state("calendar").completed_as_of == "2026-10-09T12:11:00.000000Z"

    clock.advance(minutes=11)
    feeds.batches["forexfactory"] = SourceError("http_429")
    third = _round(collector, "calendar")
    assert third.error_code is None
    assert third.source_errors == {"source:forexfactory": "http_429"}

    clock.advance(hours=25)
    stale = _round(collector, "calendar", force=True)
    assert stale.error_code == "http_429"
    assert collector.repository.state("calendar").last_error_code == "http_429"
