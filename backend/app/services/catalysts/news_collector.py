"""Local catalyst collector: polls news and calendar sources into the ETL store.

The collector replaces the MacroLens remote sync as the writer of the
``macrolens_etl_*`` tables. It mints ``news_id`` values and change sequences
locally, continuing from the highest value any local table references, and
stores each round as complete pages through ``CatalystEtlRepository`` inside a
single transaction. Its own bookkeeping lives in the ``catalyst_ingest_*``
tables, versioned separately so the ETL schema checksum never changes.

Duplicates are found by content hash or canonical URL first, then by fuzzy
title within the item's UTC publication day. A corroborating source records
an observation. It appends a change (new ``sources`` or tickers; title,
summary and URL unchanged) only while the item has no analysis link, was
first seen less than six hours ago and was never extended before. Items that
already existed when the collector took over are never extended, so the
switch does not detach paid analyses from their revisions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal, Mapping
from urllib.parse import quote

import httpx

from app.failure_diagnostics import record_fallback_failure

from .calendar_source import (
    CALENDAR_SOURCE,
    CalendarBatch,
    build_calendar_fetchers,
    calendar_stale,
    prune_snapshots,
    store_calendar,
)
from .etl_repository import CatalystEtlRepository
from .ingest_models import NEWS_PAGE_LIMIT, NewsChangesPage, RawNewsItem
from .news_dedup import (
    compute_content_hash,
    fuzzy_title,
    normalize_url,
    publication_bucket,
    similar_titles,
)
from .news_sources import (
    MAX_URL_CHARS,
    NEWS_SOURCES,
    NewsBatch,
    SourceError,
    SourceItem,
    SourceRequest,
    SourceSpec,
    build_news_fetchers,
    parse_utc,
    utc_micros,
    web_url,
)


Stream = Literal["news", "calendar"]
SourceFetch = Callable[[SourceRequest], Awaitable[NewsBatch | CalendarBatch]]

INGEST_SCHEMA_VERSION = "catalyst-ingest-v1"
_INGEST_SCHEMA = """
CREATE TABLE IF NOT EXISTS catalyst_ingest_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_ingest_counters (
    name TEXT PRIMARY KEY,
    value INTEGER NOT NULL CHECK(value >= 0)
);

CREATE TABLE IF NOT EXISTS catalyst_ingest_sources (
    source_key TEXT PRIMARY KEY,
    cursor TEXT,
    last_attempt_at TEXT,
    last_success_at TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
    next_due_at TEXT,
    last_item_count INTEGER,
    last_error_code TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_ingest_keys (
    dedup_key TEXT PRIMARY KEY,
    news_id INTEGER NOT NULL CHECK(news_id >= 1)
);

CREATE TABLE IF NOT EXISTS catalyst_ingest_titles (
    news_id INTEGER PRIMARY KEY CHECK(news_id >= 1),
    bucket TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_catalyst_ingest_titles_bucket
    ON catalyst_ingest_titles(bucket,news_id);

CREATE TABLE IF NOT EXISTS catalyst_ingest_items (
    news_id INTEGER PRIMARY KEY CHECK(news_id >= 1),
    first_seen_at TEXT NOT NULL,
    preexisting INTEGER NOT NULL CHECK(preexisting IN (0,1)),
    corroboration_appends INTEGER NOT NULL DEFAULT 0 CHECK(corroboration_appends >= 0)
);

CREATE TABLE IF NOT EXISTS catalyst_ingest_observations (
    source_key TEXT NOT NULL,
    source_item_id TEXT NOT NULL,
    news_id INTEGER NOT NULL CHECK(news_id >= 1),
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    url TEXT NOT NULL,
    source_tickers_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(source_key,source_item_id)
);
CREATE INDEX IF NOT EXISTS idx_catalyst_ingest_observations_recent
    ON catalyst_ingest_observations(source_key,observed_at);
""".strip()
INGEST_SCHEMA_CHECKSUM = hashlib.sha256(_INGEST_SCHEMA.encode("utf-8")).hexdigest()

HOST_SPACING = timedelta(seconds=60)
# A news source counts as fresh for three polling intervals, at least 30
# minutes, after its last success; the news stream is degraded once none is.
FRESH_INTERVALS = 3
FRESH_AT_LEAST = timedelta(minutes=30)
ROUND_BUDGET_SECONDS = 60.0
SOURCE_TIMEOUT_SECONDS = 45.0
CORROBORATION_WINDOW = timedelta(hours=6)
MAX_BACKOFF = timedelta(hours=1)
MAX_SOURCES_PER_ITEM = 500
MAX_TICKERS_PER_ITEM = 100
_ONE_MICROSECOND = timedelta(microseconds=1)
_SOURCE_SPECS = {spec.key: spec for spec in (*NEWS_SOURCES, CALENDAR_SOURCE)}
_PRIVATE_TABLES_BY_NEWS_ID = (
    "catalyst_ingest_keys",
    "catalyst_ingest_titles",
    "catalyst_ingest_items",
)


class IngestError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class PlannedSource:
    spec: SourceSpec
    request: SourceRequest


@dataclass(frozen=True)
class RoundPlan:
    stream: Stream
    now: datetime
    due: tuple[PlannedSource, ...] = ()
    unconfigured: tuple[str, ...] = ()


@dataclass(frozen=True)
class FetchOutcome:
    key: str
    fetched_at: datetime
    batch: NewsBatch | CalendarBatch | None = None
    error: SourceError | None = None


@dataclass(frozen=True)
class StreamOutcome:
    metrics: dict[str, int | bool]
    source_errors: dict[str, str] = field(default_factory=dict)
    # Set when the stream failed as a whole; individual sources failing is not that.
    error_code: str | None = None


@dataclass
class _PendingChange:
    news: dict[str, Any]
    sources: list[str]
    tickers: list[str]
    previous_available_at: str | None = None


@dataclass
class _Taken:
    news_id: int
    minted: bool = False
    corroborated: bool = False
    entry: _PendingChange | None = None
    title: tuple[str, str] | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_fetchers(settings: Any, client: httpx.AsyncClient) -> dict[str, SourceFetch]:
    return {**build_news_fetchers(settings, client), **build_calendar_fetchers(client)}


def _read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(path.resolve().as_posix(), safe='/')}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _tables(connection: sqlite3.Connection, *names: str) -> set[str]:
    marks = ",".join("?" for _ in names)
    return {
        str(row[0])
        for row in connection.execute(
            f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({marks})",
            names,
        )
    }


def local_ingest_has_written(path: str | Path) -> bool:
    """Whether the collector has stored anything the remote sync would collide with."""

    database = Path(path)
    if not database.is_file():
        return False
    with closing(_read_only(database)) as connection:
        if not _tables(connection, "catalyst_ingest_counters"):
            return False
        row = connection.execute(
            "SELECT value FROM catalyst_ingest_counters WHERE name='local_writes'"
        ).fetchone()
    return bool(row and int(row[0]) > 0)


def source_health(
    path: str | Path,
    *,
    sources: Any,
    now: datetime,
    calendar_items_last_24h: int | None,
) -> list[dict[str, Any]]:
    """Per-source status cards for ``status.sources``, read without a write lock."""

    database = Path(path)
    if not database.is_file():
        return []
    with closing(_read_only(database)) as connection:
        if len(_tables(connection, "catalyst_ingest_sources", "catalyst_ingest_observations")) < 2:
            return []
        rows = {
            str(row["source_key"]): row
            for row in connection.execute("SELECT * FROM catalyst_ingest_sources")
        }
        counts = {
            str(row[0]): int(row[1])
            for row in connection.execute(
                """SELECT source_key,COUNT(*) FROM catalyst_ingest_observations
                   WHERE observed_at>=? GROUP BY source_key""",
                (utc_micros(now - timedelta(hours=24)),),
            )
        }
    cards: list[dict[str, Any]] = []
    for spec in (*NEWS_SOURCES, CALENDAR_SOURCE):
        row = rows.get(spec.key)
        if row is None or not getattr(sources, spec.config).enabled:
            continue
        succeeded = parse_utc(row["last_success_at"])
        healthy = (
            succeeded is not None
            and not row["last_error_code"]
            and int(row["consecutive_failures"]) == 0
        )
        cards.append(
            {
                "key": spec.key,
                "source": spec.label,
                "status": "active" if healthy else "degraded",
                "lag_ms": (
                    max(0, int((now - succeeded).total_seconds() * 1000))
                    if succeeded is not None
                    else None
                ),
                "last_success_at": row["last_success_at"],
                "items_last_24h": (
                    calendar_items_last_24h
                    if spec is CALENDAR_SOURCE
                    else counts.get(spec.key, 0)
                ),
                "note": row["last_error_code"] or "",
            }
        )
    return cards


class NewsCollector:
    """Plans, fetches and stores one stream round; the caller owns the schedule."""

    def __init__(
        self,
        repository: CatalystEtlRepository,
        *,
        sources: Any,
        fetchers: Mapping[str, SourceFetch],
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self._sources = sources
        self._fetchers = dict(fetchers)
        self._clock = clock

    def _interval(self, spec: SourceSpec) -> timedelta:
        return timedelta(seconds=int(getattr(self._sources, spec.config).interval_seconds))

    def _enabled(self, spec: SourceSpec) -> bool:
        return bool(getattr(self._sources, spec.config).enabled)

    def initialize(self) -> None:
        """Create the private tables and adopt news the previous writer stored.

        The first run seeds the counters from every table that still holds a
        ``news_id`` or change sequence. Every run registers mirror rows that
        are not yet known, so news synced while the remote writer was active
        is matched instead of minted again, and drops any remote pagination
        window left in the ETL state.
        """

        self.repository.initialize()
        with closing(sqlite3.connect(self.repository.path, timeout=5.0)) as connection:
            connection.execute("PRAGMA busy_timeout=5000")
            connection.executescript(_INGEST_SCHEMA)
        with self.repository.transaction() as connection:
            row = connection.execute(
                "SELECT checksum FROM catalyst_ingest_schema WHERE version=?",
                (INGEST_SCHEMA_VERSION,),
            ).fetchone()
            if row is not None and str(row["checksum"]) != INGEST_SCHEMA_CHECKSUM:
                raise IngestError("catalyst_ingest_schema_checksum_mismatch")
            if row is None:
                self._seed_counters(connection)
                connection.execute(
                    """INSERT INTO catalyst_ingest_schema(version,checksum,applied_at)
                       VALUES(?,?,?)""",
                    (INGEST_SCHEMA_VERSION, INGEST_SCHEMA_CHECKSUM, utc_micros(self._clock())),
                )
            self.repository.clear_pending_checkpoints(connection)
            self._register_existing_news(connection)

    def _seed_counters(self, connection: sqlite3.Connection) -> None:
        local = _tables(
            connection,
            "catalyst_local_news_revisions",
            "catalyst_local_analysis_links",
        )
        news_sources = [
            "SELECT MAX(news_id) FROM macrolens_etl_news",
            "SELECT MAX(news_id) FROM macrolens_etl_news_changes",
            "SELECT MAX(news_id) FROM macrolens_etl_news_tombstones",
        ]
        sequence_sources = [
            "SELECT completed_watermark_sequence FROM macrolens_etl_state WHERE stream='news'",
            "SELECT MAX(change_sequence) FROM macrolens_etl_news_changes",
            "SELECT MAX(change_sequence) FROM macrolens_etl_news_tombstones",
        ]
        for table in sorted(local):
            news_sources.append(f"SELECT MAX(news_id) FROM {table}")
            sequence_sources.append(f"SELECT MAX(change_sequence) FROM {table}")

        def highest(statements: list[str]) -> int:
            values = [connection.execute(statement).fetchone()[0] for statement in statements]
            return max((int(value) for value in values if value is not None), default=0)

        connection.executemany(
            "INSERT OR REPLACE INTO catalyst_ingest_counters(name,value) VALUES(?,?)",
            (
                ("news_id", highest(news_sources)),
                ("change_sequence", highest(sequence_sources)),
                ("local_writes", 0),
            ),
        )

    @staticmethod
    def _register_existing_news(connection: sqlite3.Connection) -> int:
        rows = connection.execute(
            """SELECT news.news_id,news.title,news.url,news.content_hash,
                      news.published_at,news.fetched_at,news.available_at
               FROM macrolens_etl_news AS news
               WHERE news.deleted=0 AND NOT EXISTS (
                   SELECT 1 FROM catalyst_ingest_items AS item
                   WHERE item.news_id=news.news_id
               )
               ORDER BY news.news_id"""
        ).fetchall()
        items = []
        keys = []
        titles = []
        for row in rows:
            news_id = int(row["news_id"])
            items.append((news_id, str(row["available_at"])))
            if row["content_hash"]:
                keys.append((f"hash:{row['content_hash']}", news_id))
            url = normalize_url(str(row["url"] or ""))
            if url:
                keys.append((f"url:{url}", news_id))
            title = fuzzy_title(str(row["title"] or ""))
            if title:
                bucket = publication_bucket(row["published_at"] or row["fetched_at"])
                titles.append((news_id, bucket, title))
        connection.executemany(
            """INSERT INTO catalyst_ingest_items(news_id,first_seen_at,preexisting)
               VALUES(?,?,1)""",
            items,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO catalyst_ingest_keys(dedup_key,news_id) VALUES(?,?)",
            keys,
        )
        connection.executemany(
            "INSERT OR IGNORE INTO catalyst_ingest_titles(news_id,bucket,title) VALUES(?,?,?)",
            titles,
        )
        return len(rows)

    def plan(self, stream: Stream, *, force: bool = False) -> RoundPlan:
        """Sources due now; ``force`` ignores intervals and backoff, never host spacing."""

        now = self._clock()
        specs = NEWS_SOURCES if stream == "news" else (CALENDAR_SOURCE,)
        with closing(_read_only(self.repository.path)) as connection:
            rows = {
                str(row["source_key"]): row
                for row in connection.execute("SELECT * FROM catalyst_ingest_sources")
            }
            latest_calendar = (
                self.repository.latest_calendar_snapshot(connection)
                if stream == "calendar"
                else None
            )
        host_requests: dict[str, datetime] = {}
        for key, row in rows.items():
            spec = _SOURCE_SPECS.get(key)
            attempted = parse_utc(row["last_attempt_at"])
            if spec is not None and attempted is not None:
                host_requests[spec.host] = max(attempted, host_requests.get(spec.host, attempted))
        due: list[PlannedSource] = []
        unconfigured: list[str] = []
        for spec in specs:
            if not self._enabled(spec):
                continue
            row = rows.get(spec.key)
            if spec.key not in self._fetchers:
                if row is None or row["last_error_code"] != "not_configured":
                    unconfigured.append(spec.key)
                continue
            next_due = parse_utc(row["next_due_at"]) if row is not None else None
            if not force and next_due is not None and now < next_due:
                continue
            last_request = host_requests.get(spec.host)
            if last_request is not None and timedelta(0) <= now - last_request < HOST_SPACING:
                continue
            host_requests[spec.host] = now
            cursor = row["cursor"] if row is not None else None
            if stream == "calendar":
                cursor = self._calendar_cursor(cursor, latest_calendar)
            due.append(PlannedSource(spec, SourceRequest(now=now, cursor=cursor)))
        return RoundPlan(stream, now, tuple(due), tuple(unconfigured))

    @staticmethod
    def _calendar_cursor(cursor: str | None, latest: tuple[int, str] | None) -> str | None:
        """Send validators only when they belong to the snapshot readers see."""

        if not cursor or latest is None:
            return None
        try:
            saved = json.loads(cursor)
        except json.JSONDecodeError:
            return None
        return cursor if isinstance(saved, dict) and saved.get("token") == latest[1] else None

    async def fetch(self, plan: RoundPlan) -> tuple[FetchOutcome, ...]:
        """Fetch due sources concurrently; a source past the round budget waits a round."""

        if not plan.due:
            return ()
        tasks = [asyncio.create_task(self._fetch_one(planned)) for planned in plan.due]
        try:
            await asyncio.wait(tasks, timeout=ROUND_BUDGET_SECONDS)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        return tuple(
            task.result() for task in tasks if not task.cancelled() and task.exception() is None
        )

    async def _fetch_one(self, planned: PlannedSource) -> FetchOutcome:
        key = planned.spec.key
        try:
            batch = await asyncio.wait_for(
                self._fetchers[key](planned.request),
                timeout=SOURCE_TIMEOUT_SECONDS,
            )
        except SourceError as error:
            return FetchOutcome(key, self._clock(), error=error)
        except asyncio.TimeoutError:
            return FetchOutcome(key, self._clock(), error=SourceError("timeout"))
        except Exception as error:
            record_fallback_failure(f"catalyst_source_{key}", error)
            return FetchOutcome(key, self._clock(), error=SourceError("source_failed"))
        return FetchOutcome(key, self._clock(), batch=batch)

    def commit(self, plan: RoundPlan, outcomes: tuple[FetchOutcome, ...]) -> StreamOutcome:
        if plan.stream == "news":
            return self._commit_news(plan, outcomes)
        return self._commit_calendar(plan, outcomes)

    def _commit_news(self, plan: RoundPlan, outcomes: tuple[FetchOutcome, ...]) -> StreamOutcome:
        failed = [outcome for outcome in outcomes if outcome.error is not None]
        succeeded = [
            outcome
            for outcome in outcomes
            if outcome.error is None and not getattr(outcome.batch, "deferred", False)
        ]
        stats = {"new": 0, "corroborated": 0, "observed": 0}
        watermark = 0
        invalid: dict[str, int] = {}
        if failed or succeeded or plan.unconfigured:
            with self.repository.transaction() as connection:
                self._mark_unconfigured(connection, plan)
                for outcome in failed:
                    self._record_failure(connection, outcome, plan.now)
                if succeeded:
                    stats, watermark, invalid = self._store_news(connection, plan.now, succeeded)
                    for outcome in succeeded:
                        batch = outcome.batch
                        assert isinstance(batch, NewsBatch)
                        self._record_success(
                            connection,
                            outcome,
                            plan.now,
                            cursor=batch.cursor,
                            items=len(batch.items),
                        )
                elif failed:
                    self.repository.record_error(
                        "news", failed[0].error.code, connection=connection
                    )
        metrics: dict[str, int | bool] = {
            "sources_due": len(plan.due),
            "sources_succeeded": len(succeeded),
            "sources_failed": len(failed),
            "sources_deferred": len(plan.due) - len(succeeded) - len(failed),
            "fetched": sum(len(getattr(outcome.batch, "items", ())) for outcome in succeeded),
            **stats,
            "invalid_items": sum(invalid.values()),
            "watermark_sequence": watermark,
        }
        source_errors = {f"source:{key}": "invalid_items" for key in invalid}
        source_errors.update({f"source:{outcome.key}": outcome.error.code for outcome in failed})
        return StreamOutcome(metrics, source_errors, error_code=self._news_health(plan.now))

    def _commit_calendar(
        self,
        plan: RoundPlan,
        outcomes: tuple[FetchOutcome, ...],
    ) -> StreamOutcome:
        outcome = outcomes[0] if outcomes else None
        stored = None
        if outcome is not None or plan.unconfigured:
            with self.repository.transaction() as connection:
                self._mark_unconfigured(connection, plan)
                if outcome is not None and outcome.error is not None:
                    self._record_failure(connection, outcome, plan.now)
                    self.repository.record_error(
                        "calendar", outcome.error.code, connection=connection
                    )
                elif outcome is not None:
                    batch = outcome.batch
                    assert isinstance(batch, CalendarBatch)
                    stored = store_calendar(
                        self.repository,
                        connection,
                        batch,
                        fetched_at=outcome.fetched_at,
                        now=plan.now,
                    )
                    validators = json.loads(batch.cursor) if batch.cursor else {}
                    self._record_success(
                        connection,
                        outcome,
                        plan.now,
                        cursor=json.dumps(
                            {**validators, "token": stored.token},
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                        items=stored.events,
                    )
                    self._count_local_write(connection)
        pruned = 0
        if stored is not None:
            try:
                pruned = prune_snapshots(self.repository, now=plan.now)
            except sqlite3.Error as error:
                # The next refresh prunes again; the stored snapshot stands.
                record_fallback_failure("catalyst_calendar_prune", error)
        metrics: dict[str, int | bool] = {
            "sources_due": len(plan.due),
            "sources_succeeded": int(stored is not None),
            "sources_failed": int(outcome is not None and outcome.error is not None),
            "sources_deferred": int(bool(plan.due) and outcome is None),
            "events": stored.events if stored is not None else 0,
            "snapshot_written": bool(stored is not None and stored.written),
            "watermark_sequence": stored.sequence if stored is not None else 0,
            "pruned_snapshots": pruned,
        }
        return StreamOutcome(
            metrics,
            (
                {f"source:{outcome.key}": outcome.error.code}
                if outcome is not None and outcome.error is not None
                else {}
            ),
            error_code=self._calendar_health(plan.now),
        )

    def _news_health(self, now: datetime) -> str | None:
        """``news_sources_stale`` once no configured news source is fresh.

        Judged on every round from the source table, so the task stays
        degraded for as long as the outage lasts: a failed source backs off
        and is not retried in the next rounds, and a source waiting for the
        shared Finnhub budget neither fails nor succeeds.
        """

        specs = [spec for spec in NEWS_SOURCES if self._enabled(spec) and spec.key in self._fetchers]
        if not specs:
            return None
        with closing(_read_only(self.repository.path)) as connection:
            successes = {
                str(row[0]): parse_utc(row[1])
                for row in connection.execute(
                    "SELECT source_key,last_success_at FROM catalyst_ingest_sources"
                )
            }
        for spec in specs:
            succeeded = successes.get(spec.key)
            fresh_for = max(self._interval(spec) * FRESH_INTERVALS, FRESH_AT_LEAST)
            if succeeded is not None and now - succeeded <= fresh_for:
                return None
        return "news_sources_stale"

    def _calendar_health(self, now: datetime) -> str | None:
        """``calendar_stale`` while the newest confirmation is more than a day old."""

        if not self._enabled(CALENDAR_SOURCE) or CALENDAR_SOURCE.key not in self._fetchers:
            return None
        state = self.repository.state("calendar")
        return "calendar_stale" if calendar_stale(state.completed_as_of, now=now) else None

    def _store_news(
        self,
        connection: sqlite3.Connection,
        now: datetime,
        outcomes: list[FetchOutcome],
    ) -> tuple[dict[str, int], int, dict[str, int]]:
        state = self.repository.state("news", connection=connection)
        checkpoint = max(now, parse_utc(state.updated_after) or now)
        checkpoint_text = utc_micros(checkpoint)
        next_news_id = self._next_news_id(connection)
        linked = bool(_tables(connection, "catalyst_local_analysis_links"))
        buckets: dict[str, list[tuple[str, int]]] = {}
        pending: dict[int, _PendingChange] = {}
        stats = {"new": 0, "corroborated": 0, "observed": 0}
        invalid: dict[str, int] = {}
        for outcome in outcomes:
            batch = outcome.batch
            assert isinstance(batch, NewsBatch)
            fetched_text = utc_micros(outcome.fetched_at)
            for item in batch.items:
                # One malformed item must not take the round, and every healthy
                # source in it, down with it.
                connection.execute("SAVEPOINT catalyst_ingest_item")
                try:
                    taken = self._take(
                        connection,
                        outcome.key,
                        item,
                        fetched_at=fetched_text,
                        checkpoint=checkpoint,
                        checkpoint_text=checkpoint_text,
                        next_news_id=next_news_id,
                        linked=linked,
                        buckets=buckets,
                        pending=pending,
                    )
                except (ValueError, TypeError, sqlite3.IntegrityError) as error:
                    connection.execute("ROLLBACK TO catalyst_ingest_item")
                    connection.execute("RELEASE catalyst_ingest_item")
                    record_fallback_failure(f"catalyst_item_{outcome.key}", error)
                    invalid[outcome.key] = invalid.get(outcome.key, 0) + 1
                    continue
                connection.execute("RELEASE catalyst_ingest_item")
                if taken is None:
                    continue
                stats["observed"] += 1
                if taken.entry is not None:
                    pending[taken.news_id] = taken.entry
                if taken.title is not None:
                    bucket, title_key = taken.title
                    buckets.setdefault(bucket, []).append((title_key, taken.news_id))
                if taken.minted:
                    stats["new"] += 1
                    next_news_id += 1
                elif taken.corroborated:
                    stats["corroborated"] += 1
        sequence = self._next_sequence(connection, state.completed_watermark_sequence)
        changes: list[dict[str, Any]] = []
        for news_id, entry in pending.items():
            available = checkpoint
            previous = parse_utc(entry.previous_available_at)
            if previous is not None and previous >= available:
                available = previous + _ONE_MICROSECOND
            available_text = utc_micros(available)
            changes.append(
                {
                    "sequence": sequence,
                    "operation": "upsert",
                    "changed_at": available_text,
                    "source_updated_at": available_text,
                    "available_at": available_text,
                    "news_id": news_id,
                    "news": self._payload(news_id, entry, available_text),
                }
            )
            sequence += 1
        watermark = changes[-1]["sequence"] if changes else state.completed_watermark_sequence
        as_of = max([checkpoint_text, *(change["available_at"] for change in changes)])
        self._write_pages(connection, state, changes, watermark=watermark, as_of=as_of)
        if pending:
            connection.executemany(
                "UPDATE catalyst_ingest_counters SET value=MAX(value,?) WHERE name=?",
                ((next_news_id - 1, "news_id"), (watermark, "change_sequence")),
            )
        self._count_local_write(connection)
        return stats, watermark, invalid

    @staticmethod
    def _payload(news_id: int, entry: _PendingChange, updated_at: str) -> dict[str, Any]:
        return {
            **entry.news,
            "id": news_id,
            "updated_at": updated_at,
            "source_tickers": entry.tickers,
            "sources": entry.sources,
            "source_count": len(entry.sources),
        }

    def _take(
        self,
        connection: sqlite3.Connection,
        source_key: str,
        item: SourceItem,
        *,
        fetched_at: str,
        checkpoint: datetime,
        checkpoint_text: str,
        next_news_id: int,
        linked: bool,
        buckets: dict[str, list[tuple[str, int]]],
        pending: dict[int, _PendingChange],
    ) -> _Taken | None:
        """Observe one source item inside the caller's savepoint.

        Only database rows change here; the caller applies the returned effect
        to the round's pending changes once the savepoint is released, so a
        rejected item leaves no trace. ``None`` means the source already
        reported this item.
        """

        seen = connection.execute(
            """SELECT 1 FROM catalyst_ingest_observations
               WHERE source_key=? AND source_item_id=?""",
            (source_key, item.source_item_id),
        ).fetchone()
        if seen is not None:
            return None
        url = normalize_url(item.url)
        if web_url(item.url) is None or len(url) > MAX_URL_CHARS:
            raise ValueError("source item has no usable URL")
        content_hash = compute_content_hash(item.title, url, item.published_at)
        title_key = fuzzy_title(item.title)
        bucket = publication_bucket(item.published_at or fetched_at)
        news_id = self._match(connection, content_hash, url, title_key, bucket, buckets)
        taken = _Taken(news_id=news_id or next_news_id)
        if news_id is None:
            taken.minted = True
            taken.entry = _PendingChange(
                news={
                    "source": item.source,
                    "title": item.title,
                    "summary": item.summary,
                    "url": url,
                    "image_url": item.image_url,
                    "published_at": item.published_at,
                    "fetched_at": fetched_at,
                    "content_hash": content_hash,
                },
                sources=[item.source],
                tickers=list(item.tickers[:MAX_TICKERS_PER_ITEM]),
            )
            self._mint(
                connection,
                taken.news_id,
                url=url,
                content_hash=content_hash,
                title_key=title_key,
                bucket=bucket,
                first_seen_at=checkpoint_text,
            )
            if title_key:
                taken.title = (bucket, title_key)
        elif news_id in pending:
            taken.entry = self._merged(pending[news_id], item)
        else:
            taken.entry = self._extension(
                connection, news_id, item, checkpoint=checkpoint, linked=linked
            )
            taken.corroborated = taken.entry is not None
        if taken.entry is not None:
            RawNewsItem.model_validate(self._payload(taken.news_id, taken.entry, checkpoint_text))
        connection.execute(
            """INSERT INTO catalyst_ingest_observations(
                   source_key,source_item_id,news_id,source,title,url,
                   source_tickers_json,observed_at
               ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                source_key,
                item.source_item_id,
                taken.news_id,
                item.source,
                item.title,
                item.url,
                json.dumps(list(item.tickers), separators=(",", ":")),
                fetched_at,
            ),
        )
        return taken

    @staticmethod
    def _match(
        connection: sqlite3.Connection,
        content_hash: str,
        url: str,
        title_key: str,
        bucket: str,
        buckets: dict[str, list[tuple[str, int]]],
    ) -> int | None:
        for key in (f"hash:{content_hash}", f"url:{url}"):
            row = connection.execute(
                "SELECT news_id FROM catalyst_ingest_keys WHERE dedup_key=?",
                (key,),
            ).fetchone()
            if row is not None:
                return int(row[0])
        if not title_key:
            return None
        if bucket not in buckets:
            buckets[bucket] = [
                (str(row[0]), int(row[1]))
                for row in connection.execute(
                    """SELECT title,news_id FROM catalyst_ingest_titles
                       WHERE bucket=? ORDER BY news_id""",
                    (bucket,),
                )
            ]
        return next(
            (news_id for title, news_id in buckets[bucket] if similar_titles(title_key, title)),
            None,
        )

    @staticmethod
    def _mint(
        connection: sqlite3.Connection,
        news_id: int,
        *,
        url: str,
        content_hash: str,
        title_key: str,
        bucket: str,
        first_seen_at: str,
    ) -> None:
        connection.execute(
            """INSERT INTO catalyst_ingest_items(news_id,first_seen_at,preexisting)
               VALUES(?,?,0)""",
            (news_id, first_seen_at),
        )
        connection.executemany(
            "INSERT OR IGNORE INTO catalyst_ingest_keys(dedup_key,news_id) VALUES(?,?)",
            ((f"hash:{content_hash}", news_id), (f"url:{url}", news_id)),
        )
        if title_key:
            connection.execute(
                "INSERT INTO catalyst_ingest_titles(news_id,bucket,title) VALUES(?,?,?)",
                (news_id, bucket, title_key),
            )

    @staticmethod
    def _merged(entry: _PendingChange, item: SourceItem) -> _PendingChange | None:
        """``entry`` with the item's source and tickers added; ``None`` if nothing is new."""

        sources = list(entry.sources)
        tickers = list(entry.tickers)
        if item.source.casefold() not in {source.casefold() for source in sources}:
            if len(sources) < MAX_SOURCES_PER_ITEM:
                sources.append(item.source)
        for ticker in item.tickers:
            if ticker not in tickers and len(tickers) < MAX_TICKERS_PER_ITEM:
                tickers.append(ticker)
        if sources == entry.sources and tickers == entry.tickers:
            return None
        return replace(entry, sources=sources, tickers=tickers)

    def _extension(
        self,
        connection: sqlite3.Connection,
        news_id: int,
        item: SourceItem,
        *,
        checkpoint: datetime,
        linked: bool,
    ) -> _PendingChange | None:
        """The one corroboration change an item may still receive, if any."""

        record = connection.execute(
            """SELECT first_seen_at,preexisting,corroboration_appends
               FROM catalyst_ingest_items WHERE news_id=?""",
            (news_id,),
        ).fetchone()
        first_seen = parse_utc(record["first_seen_at"]) if record is not None else None
        if (
            record is None
            or first_seen is None
            or int(record["preexisting"])
            or int(record["corroboration_appends"])
            or checkpoint - first_seen > CORROBORATION_WINDOW
        ):
            return None
        if linked and connection.execute(
            "SELECT 1 FROM catalyst_local_analysis_links WHERE news_id=? LIMIT 1",
            (news_id,),
        ).fetchone():
            return None
        mirror = connection.execute(
            "SELECT * FROM macrolens_etl_news WHERE news_id=? AND deleted=0",
            (news_id,),
        ).fetchone()
        if mirror is None:
            return None
        current = _PendingChange(
            news={
                "source": mirror["source"],
                "title": mirror["title"],
                "summary": mirror["summary"],
                "url": mirror["url"],
                "image_url": mirror["image_url"],
                "published_at": mirror["published_at"],
                "fetched_at": mirror["fetched_at"],
                "content_hash": mirror["content_hash"],
            },
            sources=list(json.loads(mirror["sources_json"]) or [mirror["source"]]),
            tickers=list(json.loads(mirror["source_tickers_json"])),
            previous_available_at=str(mirror["available_at"]),
        )
        extended = self._merged(current, item)
        if extended is not None:
            connection.execute(
                """UPDATE catalyst_ingest_items
                   SET corroboration_appends=corroboration_appends+1 WHERE news_id=?""",
                (news_id,),
            )
        return extended

    @staticmethod
    def _next_news_id(connection: sqlite3.Connection) -> int:
        row = connection.execute(
            """SELECT MAX(
                   COALESCE((SELECT value FROM catalyst_ingest_counters WHERE name='news_id'),0),
                   COALESCE((SELECT MAX(news_id) FROM macrolens_etl_news),0),
                   COALESCE((SELECT MAX(news_id) FROM macrolens_etl_news_changes),0)
               )"""
        ).fetchone()
        return int(row[0]) + 1

    @staticmethod
    def _next_sequence(connection: sqlite3.Connection, completed: int) -> int:
        row = connection.execute(
            """SELECT MAX(
                   COALESCE((SELECT value FROM catalyst_ingest_counters
                             WHERE name='change_sequence'),0),
                   COALESCE((SELECT MAX(change_sequence) FROM macrolens_etl_news_changes),0)
               )"""
        ).fetchone()
        return max(int(row[0]), completed) + 1

    def _write_pages(
        self,
        connection: sqlite3.Connection,
        state: Any,
        changes: list[dict[str, Any]],
        *,
        watermark: int,
        as_of: str,
    ) -> None:
        chunks = [
            changes[offset : offset + NEWS_PAGE_LIMIT]
            for offset in range(0, len(changes), NEWS_PAGE_LIMIT)
        ] or [[]]
        cursor = state.cursor
        generation = state.generation
        for index, chunk in enumerate(chunks):
            last = index == len(chunks) - 1
            page = NewsChangesPage.model_validate(
                {
                    "items": chunk,
                    "has_more": not last,
                    "next_cursor": None if last else f"local-news:{watermark}:{index + 1}",
                    "watermark": {"sequence": watermark, "as_of": as_of},
                    "next_updated_after": as_of if last else None,
                    "next_after_sequence": watermark if last else None,
                }
            )
            self.repository.apply_news_page(
                page,
                expected_cursor=cursor,
                expected_generation=generation,
                connection=connection,
            )
            cursor = page.next_cursor
            generation += 1

    @staticmethod
    def _count_local_write(connection: sqlite3.Connection) -> None:
        connection.execute(
            "UPDATE catalyst_ingest_counters SET value=value+1 WHERE name='local_writes'"
        )

    def _mark_unconfigured(self, connection: sqlite3.Connection, plan: RoundPlan) -> None:
        connection.executemany(
            """INSERT INTO catalyst_ingest_sources(
                   source_key,consecutive_failures,last_error_code,updated_at
               ) VALUES(?,0,'not_configured',?)
               ON CONFLICT(source_key) DO UPDATE SET
                   last_error_code='not_configured',updated_at=excluded.updated_at""",
            ((key, utc_micros(plan.now)) for key in plan.unconfigured),
        )

    def _record_success(
        self,
        connection: sqlite3.Connection,
        outcome: FetchOutcome,
        attempted_at: datetime,
        *,
        cursor: str | None,
        items: int,
    ) -> None:
        spec = _SOURCE_SPECS[outcome.key]
        connection.execute(
            """INSERT INTO catalyst_ingest_sources(
                   source_key,cursor,last_attempt_at,last_success_at,consecutive_failures,
                   next_due_at,last_item_count,last_error_code,updated_at
               ) VALUES(?,?,?,?,0,?,?,NULL,?)
               ON CONFLICT(source_key) DO UPDATE SET
                   cursor=excluded.cursor,last_attempt_at=excluded.last_attempt_at,
                   last_success_at=excluded.last_success_at,consecutive_failures=0,
                   next_due_at=excluded.next_due_at,last_item_count=excluded.last_item_count,
                   last_error_code=NULL,updated_at=excluded.updated_at""",
            (
                outcome.key,
                cursor,
                utc_micros(attempted_at),
                utc_micros(outcome.fetched_at),
                utc_micros(attempted_at + self._interval(spec)),
                items,
                utc_micros(outcome.fetched_at),
            ),
        )

    def _record_failure(
        self,
        connection: sqlite3.Connection,
        outcome: FetchOutcome,
        attempted_at: datetime,
    ) -> None:
        assert outcome.error is not None
        spec = _SOURCE_SPECS[outcome.key]
        row = connection.execute(
            "SELECT consecutive_failures FROM catalyst_ingest_sources WHERE source_key=?",
            (outcome.key,),
        ).fetchone()
        failures = (int(row[0]) if row is not None else 0) + 1
        interval = self._interval(spec)
        delay = min(interval * 2 ** min(failures - 1, 16), max(interval, MAX_BACKOFF))
        if outcome.error.retry_after is not None:
            delay = max(delay, timedelta(seconds=min(outcome.error.retry_after, 86_400)))
        connection.execute(
            """INSERT INTO catalyst_ingest_sources(
                   source_key,last_attempt_at,consecutive_failures,next_due_at,
                   last_error_code,updated_at
               ) VALUES(?,?,?,?,?,?)
               ON CONFLICT(source_key) DO UPDATE SET
                   last_attempt_at=excluded.last_attempt_at,
                   consecutive_failures=excluded.consecutive_failures,
                   next_due_at=excluded.next_due_at,
                   last_error_code=excluded.last_error_code,updated_at=excluded.updated_at""",
            (
                outcome.key,
                utc_micros(attempted_at),
                failures,
                utc_micros(attempted_at + delay),
                outcome.error.code[:100],
                utc_micros(outcome.fetched_at),
            ),
        )

    def prune_orphans(self, *, retention_days: int) -> int:
        """Drop private rows of news the journal prune removed from the mirror.

        Observations of such news stay until the retention period has passed
        since they were made: an old story a feed keeps listing is then still
        recognised as seen instead of being minted again after every prune.
        """

        cutoff = utc_micros(self._clock() - timedelta(days=retention_days))
        removed = 0
        with self.repository.transaction() as connection:
            for table in _PRIVATE_TABLES_BY_NEWS_ID:
                removed += connection.execute(
                    f"DELETE FROM {table} WHERE news_id NOT IN "
                    "(SELECT news_id FROM macrolens_etl_news)"
                ).rowcount
            removed += connection.execute(
                """DELETE FROM catalyst_ingest_observations
                   WHERE observed_at<? AND news_id NOT IN (
                       SELECT news_id FROM macrolens_etl_news
                   )""",
                (cutoff,),
            ).rowcount
        return removed
