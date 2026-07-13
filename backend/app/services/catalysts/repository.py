from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence
from zoneinfo import ZoneInfo

from .errors import CatalystRepositoryError, InvalidCursorError
from .models import (
    ACTIVE_JOB_STATUSES,
    CalendarEvent,
    CatalystItem,
    ComponentHealth,
    HotspotPreparationItem,
    HotspotPreparationStatus,
    JobStatus,
    RemoteMarketFocusCycle,
    RemoteJobResponse,
    TERMINAL_JOB_STATUSES,
    TICKER_PATTERN,
    utc_iso,
)
from .focus_models import FocusContextDraft, FocusContextResponse


DATABASE_VERSION = "catalyst-cache-v6"
SQLITE_USER_VERSION = 6
FOCUS_PRODUCER_WORKER_PREFIX = "focus-context-producer:"
_V1_DATABASE_VERSION = "catalyst-cache-v1"
_V1_SCHEMA_CHECKSUM = "72f57f049e66986e7f0dd19e71eff0f772c88b13d5be1d99cbb6d0fe9423c951"
_V2_DATABASE_VERSION = "catalyst-cache-v2"
_V2_SCHEMA_CHECKSUM = "f69577b5cc0957010d01627c7c25b4cf894a6920156c43ad2c7a3114bf067f35"
_V3_DATABASE_VERSION = "catalyst-cache-v3"
_V3_SCHEMA_CHECKSUM = "eff9b3d6070fadee9aff5717799d13126bc8d3aed7a1f381cdaeba781dac3c43"
_V4_DATABASE_VERSION = "catalyst-cache-v4"
_V4_SCHEMA_CHECKSUM = "ea1566f2aedabe63057b7f70ffaf20ac2e24a999852f0f8acab068404e405e21"
_V5_DATABASE_VERSION = "catalyst-cache-v5"
_V5_SCHEMA_CHECKSUM = "3dfe7078bdf2daf4564bab33e1be3b3f6e8777f5400dd2960ed14b4e77707781"


_MARKET_FOCUS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS catalyst_hotspot_preparation_sets (
    snapshot_id TEXT NOT NULL,
    prepared_revision INTEGER NOT NULL CHECK (prepared_revision >= 0),
    status_json TEXT NOT NULL,
    item_json TEXT,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (snapshot_id,prepared_revision),
    CHECK (
        (prepared_revision=0 AND item_json IS NULL)
        OR (prepared_revision>0 AND item_json IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_catalyst_hotspots_revision
    ON catalyst_hotspot_preparation_sets(prepared_revision DESC,cached_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_market_focus_cycles (
    remote_cycle_id TEXT PRIMARY KEY,
    public_cycle_id TEXT NOT NULL UNIQUE,
    snapshot_id TEXT,
    prepared_revision INTEGER NOT NULL CHECK (prepared_revision >= 0),
    status TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    cached_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalyst_market_focus_cycles_latest
    ON catalyst_market_focus_cycles(cached_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_market_focus_jobs (
    local_cycle_id TEXT PRIMARY KEY,
    request_key TEXT NOT NULL UNIQUE,
    expected_prepared_revision INTEGER NOT NULL CHECK (expected_prepared_revision >= 0),
    last_consumed_revision_at_request INTEGER NOT NULL
        CHECK (last_consumed_revision_at_request >= 0),
    retry_of_local_cycle_id TEXT,
    retry_remote_cycle_id TEXT,
    execution_number INTEGER NOT NULL DEFAULT 1 CHECK (execution_number >= 1),
    remote_cycle_id TEXT UNIQUE,
    status TEXT NOT NULL,
    model TEXT,
    reasoning TEXT,
    error_code TEXT,
    retry_after_seconds INTEGER CHECK (retry_after_seconds IS NULL OR retry_after_seconds >= 0),
    next_attempt_at TEXT,
    cancel_requested_at TEXT,
    result_json TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalyst_market_focus_jobs_due
    ON catalyst_market_focus_jobs(status,next_attempt_at,updated_at);

CREATE TABLE IF NOT EXISTS focus_reference_generation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    generation INTEGER NOT NULL DEFAULT 0 CHECK (generation >= 0)
);

INSERT OR IGNORE INTO focus_reference_generation(singleton,generation)
VALUES(1,0);

CREATE TRIGGER IF NOT EXISTS trg_focus_cycle_reference_insert
AFTER INSERT ON catalyst_market_focus_cycles
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;

CREATE TRIGGER IF NOT EXISTS trg_focus_cycle_reference_update
AFTER UPDATE OF raw_json ON catalyst_market_focus_cycles
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;

CREATE TRIGGER IF NOT EXISTS trg_focus_cycle_reference_delete
AFTER DELETE ON catalyst_market_focus_cycles
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;

CREATE TRIGGER IF NOT EXISTS trg_focus_job_reference_insert
AFTER INSERT ON catalyst_market_focus_jobs
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;

CREATE TRIGGER IF NOT EXISTS trg_focus_job_reference_update
AFTER UPDATE OF result_json ON catalyst_market_focus_jobs
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;

CREATE TRIGGER IF NOT EXISTS trg_focus_job_reference_delete
AFTER DELETE ON catalyst_market_focus_jobs
BEGIN
    UPDATE focus_reference_generation
    SET generation=generation+1 WHERE singleton=1;
END;
""".strip()


_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS catalyst_schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version TEXT NOT NULL,
    schema_checksum TEXT NOT NULL,
    installed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_sync_runs (
    run_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    started_at TEXT NOT NULL,
    completed_at TEXT,
    snapshot_token TEXT,
    from_sequence INTEGER,
    through_sequence INTEGER,
    data_through TEXT,
    item_count INTEGER NOT NULL DEFAULT 0 CHECK (item_count >= 0),
    error_code TEXT,
    sync_mode TEXT NOT NULL DEFAULT 'incremental'
        CHECK (sync_mode IN ('incremental','resync')),
    resync_generation INTEGER
);

CREATE TABLE IF NOT EXISTS catalyst_sync_state (
    stream TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    data_through TEXT,
    watermark_sequence INTEGER,
    updated_after TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    next_attempt_at TEXT,
    circuit_open_until TEXT,
    last_error_code TEXT,
    remote_status TEXT,
    snapshot_generation INTEGER NOT NULL DEFAULT 0 CHECK (snapshot_generation >= 0),
    current_snapshot_id TEXT,
    resync_required INTEGER NOT NULL DEFAULT 0 CHECK (resync_required IN (0,1)),
    resync_generation INTEGER NOT NULL DEFAULT 0 CHECK (resync_generation >= 0),
    last_resync_at TEXT,
    resync_from TEXT
);

CREATE TABLE IF NOT EXISTS catalyst_staging_items (
    run_id TEXT NOT NULL REFERENCES catalyst_sync_runs(run_id) ON DELETE CASCADE,
    news_id INTEGER NOT NULL,
    change_sequence INTEGER NOT NULL CHECK (change_sequence >= 0),
    updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (run_id, news_id, change_sequence)
);

CREATE TABLE IF NOT EXISTS catalyst_item_revisions (
    news_id INTEGER NOT NULL,
    change_sequence INTEGER NOT NULL CHECK (change_sequence >= 0),
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT,
    url TEXT,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    source_tickers_json TEXT NOT NULL,
    analysis_status TEXT NOT NULL,
    is_stale INTEGER NOT NULL DEFAULT 0 CHECK (is_stale IN (0,1)),
    raw_json TEXT NOT NULL,
    PRIMARY KEY (news_id, change_sequence)
);

CREATE INDEX IF NOT EXISTS idx_catalyst_items_visible
    ON catalyst_item_revisions(fetched_at, published_at, updated_at, cached_at);
CREATE INDEX IF NOT EXISTS idx_catalyst_items_content_hash
    ON catalyst_item_revisions(content_hash);
CREATE INDEX IF NOT EXISTS idx_catalyst_items_source
    ON catalyst_item_revisions(source, updated_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_item_tickers (
    news_id INTEGER NOT NULL,
    change_sequence INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    PRIMARY KEY (news_id, change_sequence, ticker),
    FOREIGN KEY (news_id, change_sequence)
        REFERENCES catalyst_item_revisions(news_id, change_sequence) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_catalyst_item_tickers_lookup
    ON catalyst_item_tickers(ticker, news_id, change_sequence);

CREATE TABLE IF NOT EXISTS catalyst_analysis_revisions (
    analysis_revision_id TEXT PRIMARY KEY,
    news_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    item_change_sequence INTEGER NOT NULL CHECK (item_change_sequence >= 1),
    analyzed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    analysis_schema_version TEXT NOT NULL,
    classification TEXT NOT NULL,
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    overall_sentiment INTEGER NOT NULL CHECK (overall_sentiment BETWEEN -100 AND 100),
    market_relevance INTEGER NOT NULL CHECK (market_relevance BETWEEN 0 AND 100),
    insufficient_context INTEGER NOT NULL CHECK (insufficient_context IN (0,1)),
    cached_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(news_id, analysis_revision_id)
);

CREATE INDEX IF NOT EXISTS idx_catalyst_analysis_visible
    ON catalyst_analysis_revisions(
        news_id,content_hash,item_change_sequence,available_at DESC,cached_at DESC
    );

CREATE TABLE IF NOT EXISTS catalyst_stock_impacts (
    analysis_revision_id TEXT NOT NULL REFERENCES catalyst_analysis_revisions(analysis_revision_id) ON DELETE CASCADE,
    news_id INTEGER NOT NULL,
    item_change_sequence INTEGER NOT NULL CHECK (item_change_sequence >= 1),
    ticker TEXT NOT NULL,
    company TEXT NOT NULL,
    impact_score INTEGER NOT NULL CHECK (impact_score BETWEEN -100 AND 100),
    confidence INTEGER NOT NULL CHECK (confidence BETWEEN 0 AND 100),
    horizon TEXT NOT NULL,
    mechanism TEXT NOT NULL,
    reason TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    analyzed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    analysis_schema_version TEXT NOT NULL,
    PRIMARY KEY (analysis_revision_id, ticker)
);

CREATE INDEX IF NOT EXISTS idx_catalyst_impacts_ticker_time
    ON catalyst_stock_impacts(ticker, available_at DESC);
CREATE INDEX IF NOT EXISTS idx_catalyst_impacts_content
    ON catalyst_stock_impacts(content_hash);

CREATE TABLE IF NOT EXISTS catalyst_staging_calendar (
    run_id TEXT NOT NULL REFERENCES catalyst_sync_runs(run_id) ON DELETE CASCADE,
    event_id TEXT NOT NULL,
    available_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (run_id, event_id, available_at)
);

CREATE TABLE IF NOT EXISTS catalyst_calendar_event_revisions (
    revision_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    currency TEXT NOT NULL,
    title TEXT NOT NULL,
    impact TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    forecast_json TEXT,
    previous_json TEXT,
    actual_json TEXT,
    is_stale INTEGER NOT NULL CHECK (is_stale IN (0,1)),
    source_fetched_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    UNIQUE(event_id, available_at, revision_id)
);

CREATE INDEX IF NOT EXISTS idx_catalyst_calendar_visible
    ON catalyst_calendar_event_revisions(scheduled_at, available_at DESC, cached_at DESC);

CREATE TABLE IF NOT EXISTS catalyst_source_health (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_attempt TEXT,
    last_success TEXT,
    failures INTEGER NOT NULL DEFAULT 0 CHECK (failures >= 0),
    next_attempt TEXT,
    raw_count INTEGER,
    inserted_count INTEGER,
    duplicates_count INTEGER,
    observed_at TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_remote_runtime (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    execution_mode TEXT NOT NULL,
    analysis_trigger_enabled INTEGER NOT NULL CHECK (analysis_trigger_enabled IN (0,1)),
    warnings_json TEXT NOT NULL,
    observed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_analysis_jobs (
    local_job_id TEXT PRIMARY KEY,
    news_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    change_sequence INTEGER NOT NULL CHECK (change_sequence >= 1),
    contract_schema_version TEXT NOT NULL,
    remote_job_id TEXT UNIQUE,
    status TEXT NOT NULL,
    force INTEGER NOT NULL DEFAULT 0 CHECK (force IN (0,1)),
    model TEXT,
    reasoning TEXT,
    actual_model TEXT,
    actual_reasoning TEXT,
    remote_input_hash TEXT,
    submitted_at TEXT,
    completed_at TEXT,
    error_code TEXT,
    retry_after_seconds INTEGER,
    next_attempt_at TEXT,
    cancel_requested_at TEXT,
    result_json TEXT,
    lease_owner TEXT,
    lease_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_catalyst_jobs_poll
    ON catalyst_analysis_jobs(status, next_attempt_at, updated_at);
CREATE INDEX IF NOT EXISTS idx_catalyst_jobs_news
    ON catalyst_analysis_jobs(
        news_id,content_hash,change_sequence,model,reasoning,contract_schema_version,created_at DESC
    );

CREATE TABLE IF NOT EXISTS catalyst_refresh_outbox (
    request_id TEXT PRIMARY KEY,
    streams_json TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','processing','completed','failed')),
    requested_at TEXT NOT NULL,
    claimed_at TEXT,
    completed_at TEXT,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_catalyst_refresh_pending
    ON catalyst_refresh_outbox(status, requested_at);

CREATE TABLE IF NOT EXISTS focus_context_snapshots (
    revision INTEGER PRIMARY KEY CHECK (revision >= 1),
    as_of TEXT NOT NULL,
    data_through TEXT,
    market_session TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS focus_context_symbols (
    revision INTEGER NOT NULL REFERENCES focus_context_snapshots(revision) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    dollar_volume_rank INTEGER,
    reasons_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (revision,ticker)
);

CREATE INDEX IF NOT EXISTS idx_focus_context_symbols_ticker
    ON focus_context_symbols(ticker,revision DESC);

CREATE TABLE IF NOT EXISTS focus_daily_strength_snapshots (
    trading_day TEXT NOT NULL,
    cache_version TEXT NOT NULL,
    universe_version TEXT NOT NULL,
    strength_feature_version TEXT NOT NULL,
    strength_score_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    range_persistence_version TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK (length(payload_hash)=64),
    coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
    status TEXT NOT NULL CHECK (status IN ('active','degraded')),
    data_through TEXT,
    payload_json TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (trading_day,cache_version)
);

CREATE INDEX IF NOT EXISTS idx_focus_daily_strength_retention
    ON focus_daily_strength_snapshots(cached_at,trading_day);

CREATE TABLE IF NOT EXISTS macrolens_focus_nonces (
    key_id TEXT NOT NULL,
    nonce TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    PRIMARY KEY (key_id,nonce)
);

CREATE INDEX IF NOT EXISTS idx_macrolens_focus_nonces_expiry
    ON macrolens_focus_nonces(expires_at);

CREATE TABLE IF NOT EXISTS catalyst_worker_status (
    worker_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    heartbeat_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS catalyst_worker_lock (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    fencing_token INTEGER NOT NULL CHECK (fencing_token > 0),
    lease_until TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
""".strip() + "\n\n" + _MARKET_FOCUS_SCHEMA_SQL


SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()
_STREAMS = ("health", "feed", "calendar", "job", "market_focus")
_FOCUS_MARKET_TIMEZONE = ZoneInfo("America/New_York")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime | str | None) -> str | None:
    parsed = _as_utc(value)
    return utc_iso(parsed)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _loads(value: str | None, default: Any = None) -> Any:
    if value is None:
        return default
    return json.loads(value)


class CatalystRepository:
    def __init__(self, path: str | Path, *, read_only: bool = False) -> None:
        self.path = Path(path)
        self.read_only = read_only
        self._publish_hook: Any | None = None

    def _configure(self, connection: sqlite3.Connection, *, writer: bool) -> sqlite3.Connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        if writer:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
        else:
            connection.execute("PRAGMA query_only=ON")
        return connection

    def open_read_connection(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise CatalystRepositoryError("cache_unavailable", "Catalyst cache has not been created")
        uri = f"file:{self.path.resolve()}?mode=ro"
        return self._configure(sqlite3.connect(uri, uri=True), writer=False)

    def open_write_connection(self) -> sqlite3.Connection:
        if self.read_only:
            raise CatalystRepositoryError("read_only_repository", "Catalyst repository is read only")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return self._configure(sqlite3.connect(self.path), writer=True)

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        connection = self.open_read_connection()
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self.open_write_connection()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, *, now: datetime | None = None) -> None:
        if self.read_only:
            raise CatalystRepositoryError("read_only_repository", "Catalyst repository is read only")
        installed_at = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
            ).fetchone() if self._table_exists(connection, "catalyst_schema_metadata") else None
            if row is not None and (
                row["schema_version"] == _V1_DATABASE_VERSION
                and row["schema_checksum"] == _V1_SCHEMA_CHECKSUM
            ):
                self._migrate_v1_to_v2(connection)
                row = connection.execute(
                    "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
                ).fetchone()
            if row is not None and (
                row["schema_version"] == _V2_DATABASE_VERSION
                and row["schema_checksum"] == _V2_SCHEMA_CHECKSUM
            ):
                self._migrate_v2_to_current(connection)
                row = connection.execute(
                    "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
                ).fetchone()
            if row is not None and (
                row["schema_version"] == _V3_DATABASE_VERSION
                and row["schema_checksum"] == _V3_SCHEMA_CHECKSUM
            ):
                self._migrate_v3_to_v4(connection)
                row = connection.execute(
                    "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
                ).fetchone()
            if row is not None and (
                row["schema_version"] == _V4_DATABASE_VERSION
                and row["schema_checksum"] == _V4_SCHEMA_CHECKSUM
            ):
                self._migrate_v4_to_v5(connection)
                row = connection.execute(
                    "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
                ).fetchone()
            if row is not None and (
                row["schema_version"] == _V5_DATABASE_VERSION
                and row["schema_checksum"] == _V5_SCHEMA_CHECKSUM
            ):
                self._migrate_v5_to_v6(connection)
                row = connection.execute(
                    "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
                ).fetchone()
            if row is not None and (
                row["schema_version"] != DATABASE_VERSION
                or row["schema_checksum"] != SCHEMA_CHECKSUM
            ):
                connection.rollback()
                raise CatalystRepositoryError(
                    "cache_schema_mismatch",
                    "Catalyst cache schema checksum does not match this build",
                )
            self._execute_script_atomic(connection, _SCHEMA_SQL)
            row = connection.execute(
                "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO catalyst_schema_metadata(singleton,schema_version,schema_checksum,installed_at) VALUES(1,?,?,?)",
                    (DATABASE_VERSION, SCHEMA_CHECKSUM, installed_at),
                )
            elif row["schema_version"] != DATABASE_VERSION or row["schema_checksum"] != SCHEMA_CHECKSUM:
                raise CatalystRepositoryError(
                    "cache_schema_mismatch",
                    "Catalyst cache schema checksum does not match this build",
                )
            for stream in _STREAMS:
                connection.execute(
                    "INSERT OR IGNORE INTO catalyst_sync_state(stream) VALUES(?)", (stream,)
                )
            connection.execute(f"PRAGMA user_version={SQLITE_USER_VERSION}")
            connection.commit()

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    @staticmethod
    def _column_names(connection: sqlite3.Connection, table: str) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        }

    @staticmethod
    def _execute_script_atomic(connection: sqlite3.Connection, script: str) -> None:
        """Execute a schema script without sqlite3.executescript's implicit commit.

        ``initialize`` owns the surrounding ``BEGIN IMMEDIATE`` transaction.  The
        stdlib ``executescript`` helper commits that transaction before running
        the script, which would make a chained v1 -> v2 -> v3 migration only
        partially rollback-safe.  ``complete_statement`` keeps statement
        splitting delegated to SQLite while every statement remains inside the
        caller's transaction.
        """

        statement = ""
        for line in script.splitlines(keepends=True):
            statement += line
            if not sqlite3.complete_statement(statement):
                continue
            sql = statement.strip()
            if sql:
                connection.execute(sql)
            statement = ""
        if statement.strip():
            raise CatalystRepositoryError(
                "cache_schema_invalid",
                "Catalyst cache schema contains an incomplete SQL statement",
            )

    def _migrate_v1_to_v2(self, connection: sqlite3.Connection) -> None:
        """Perform the additive cache migration without touching published rows."""

        state_columns = self._column_names(connection, "catalyst_sync_state")
        if "resync_required" not in state_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_state ADD COLUMN resync_required "
                "INTEGER NOT NULL DEFAULT 0 CHECK (resync_required IN (0,1))"
            )
        if "resync_generation" not in state_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_state ADD COLUMN resync_generation "
                "INTEGER NOT NULL DEFAULT 0 CHECK (resync_generation >= 0)"
            )
        if "last_resync_at" not in state_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_state ADD COLUMN last_resync_at TEXT"
            )
        if "resync_from" not in state_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_state ADD COLUMN resync_from TEXT"
            )

        run_columns = self._column_names(connection, "catalyst_sync_runs")
        if "sync_mode" not in run_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_runs ADD COLUMN sync_mode TEXT "
                "NOT NULL DEFAULT 'incremental' "
                "CHECK (sync_mode IN ('incremental','resync'))"
            )
        if "resync_generation" not in run_columns:
            connection.execute(
                "ALTER TABLE catalyst_sync_runs ADD COLUMN resync_generation INTEGER"
            )
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (_V2_DATABASE_VERSION, _V2_SCHEMA_CHECKSUM),
        )

    def _migrate_v2_to_current(self, connection: sqlite3.Connection) -> None:
        """Add the market-focus cache without altering published news rows."""

        self._execute_script_atomic(connection, _MARKET_FOCUS_SCHEMA_SQL)
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (_V4_DATABASE_VERSION, _V4_SCHEMA_CHECKSUM),
        )

    def _migrate_v4_to_v5(self, connection: sqlite3.Connection) -> None:
        """Add the bounded daily strength cache used only by focus production."""

        self._execute_script_atomic(
            connection,
            """
            CREATE TABLE IF NOT EXISTS focus_daily_strength_snapshots (
                trading_day TEXT NOT NULL,
                cache_version TEXT NOT NULL,
                universe_version TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('active','degraded')),
                data_through TEXT,
                payload_json TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT,
                PRIMARY KEY (trading_day,cache_version)
            );

            CREATE INDEX IF NOT EXISTS idx_focus_daily_strength_retention
                ON focus_daily_strength_snapshots(cached_at,trading_day);
            """.strip(),
        )
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (_V5_DATABASE_VERSION, _V5_SCHEMA_CHECKSUM),
        )

    def _migrate_v5_to_v6(self, connection: sqlite3.Connection) -> None:
        """Invalidate derived cache rows that lack independently audited fields."""

        connection.execute("DROP TABLE focus_daily_strength_snapshots")
        self._execute_script_atomic(
            connection,
            """
            CREATE TABLE focus_daily_strength_snapshots (
                trading_day TEXT NOT NULL,
                cache_version TEXT NOT NULL,
                universe_version TEXT NOT NULL,
                strength_feature_version TEXT NOT NULL,
                strength_score_version TEXT NOT NULL,
                normalization_version TEXT NOT NULL,
                range_persistence_version TEXT NOT NULL,
                payload_hash TEXT NOT NULL CHECK (length(payload_hash)=64),
                coverage REAL NOT NULL CHECK (coverage BETWEEN 0 AND 1),
                status TEXT NOT NULL CHECK (status IN ('active','degraded')),
                data_through TEXT,
                payload_json TEXT NOT NULL,
                cached_at TEXT NOT NULL,
                expires_at TEXT,
                PRIMARY KEY (trading_day,cache_version)
            );

            CREATE INDEX idx_focus_daily_strength_retention
                ON focus_daily_strength_snapshots(cached_at,trading_day);
            """.strip(),
        )
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (DATABASE_VERSION, SCHEMA_CHECKSUM),
        )

    def _migrate_v3_to_v4(self, connection: sqlite3.Connection) -> None:
        """Replace revision-only focus-job identity with snapshot-batch identity."""

        connection.execute("DROP INDEX IF EXISTS idx_catalyst_market_focus_jobs_due")
        connection.execute(
            "ALTER TABLE catalyst_market_focus_jobs RENAME TO catalyst_market_focus_jobs_v3"
        )
        self._execute_script_atomic(
            connection,
            """
            CREATE TABLE catalyst_market_focus_jobs (
                local_cycle_id TEXT PRIMARY KEY,
                request_key TEXT NOT NULL UNIQUE,
                expected_prepared_revision INTEGER NOT NULL
                    CHECK (expected_prepared_revision >= 0),
                last_consumed_revision_at_request INTEGER NOT NULL
                    CHECK (last_consumed_revision_at_request >= 0),
                retry_of_local_cycle_id TEXT,
                retry_remote_cycle_id TEXT,
                execution_number INTEGER NOT NULL DEFAULT 1
                    CHECK (execution_number >= 1),
                remote_cycle_id TEXT UNIQUE,
                status TEXT NOT NULL,
                model TEXT,
                reasoning TEXT,
                error_code TEXT,
                retry_after_seconds INTEGER
                    CHECK (retry_after_seconds IS NULL OR retry_after_seconds >= 0),
                next_attempt_at TEXT,
                cancel_requested_at TEXT,
                result_json TEXT,
                lease_owner TEXT,
                lease_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE INDEX idx_catalyst_market_focus_jobs_due
                ON catalyst_market_focus_jobs(status,next_attempt_at,updated_at);
            """.strip(),
        )
        connection.execute(
            """
            INSERT INTO catalyst_market_focus_jobs(
                local_cycle_id,request_key,expected_prepared_revision,
                last_consumed_revision_at_request,retry_of_local_cycle_id,
                retry_remote_cycle_id,execution_number,remote_cycle_id,status,
                model,reasoning,error_code,retry_after_seconds,next_attempt_at,
                cancel_requested_at,result_json,lease_owner,lease_until,
                created_at,updated_at
            )
            SELECT local_cycle_id,
                   'batch:' || expected_prepared_revision || ':0',
                   expected_prepared_revision,0,NULL,NULL,1,remote_cycle_id,status,
                   model,reasoning,error_code,retry_after_seconds,next_attempt_at,
                   cancel_requested_at,result_json,lease_owner,lease_until,
                   created_at,updated_at
            FROM catalyst_market_focus_jobs_v3
            """
        )
        connection.execute("DROP TABLE catalyst_market_focus_jobs_v3")
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (_V4_DATABASE_VERSION, _V4_SCHEMA_CHECKSUM),
        )

    def check_schema(self) -> dict[str, Any]:
        with self._read() as connection:
            row = connection.execute(
                "SELECT schema_version,schema_checksum,installed_at FROM catalyst_schema_metadata WHERE singleton=1"
            ).fetchone()
            if row is None or row["schema_version"] != DATABASE_VERSION or row["schema_checksum"] != SCHEMA_CHECKSUM:
                raise CatalystRepositoryError("cache_schema_mismatch", "Catalyst cache schema is not compatible")
            integrity = connection.execute("PRAGMA quick_check").fetchone()[0]
            return {
                "schema_version": row["schema_version"],
                "schema_checksum": row["schema_checksum"],
                "installed_at": row["installed_at"],
                "quick_check": integrity,
            }

    @staticmethod
    def _assert_worker_fence(
        connection: sqlite3.Connection,
        *,
        worker_id: str | None,
        fencing_token: int | None,
        now: datetime,
    ) -> None:
        if worker_id is None and fencing_token is None:
            return
        if worker_id is None or fencing_token is None:
            raise CatalystRepositoryError("worker_lock_lost", "Incomplete worker fencing identity")
        row = connection.execute(
            "SELECT owner_id,fencing_token,lease_until FROM catalyst_worker_lock WHERE lock_name='catalyst-sync-worker'"
        ).fetchone()
        if (
            row is None
            or row["owner_id"] != worker_id
            or row["fencing_token"] != fencing_token
            or _as_utc(row["lease_until"]) <= now
        ):
            raise CatalystRepositoryError("worker_lock_lost", "Catalyst worker fencing token is stale")

    @staticmethod
    def _assert_focus_producer_fence(
        connection: sqlite3.Connection,
        *,
        lock_name: str | None,
        owner_id: str | None,
        fencing_token: int | None,
        now: datetime,
    ) -> None:
        identity = (lock_name, owner_id, fencing_token)
        if all(value is None for value in identity):
            return
        if any(value is None for value in identity):
            raise ValueError("focus producer fencing identity is incomplete")
        row = connection.execute(
            """
            SELECT owner_id,fencing_token,lease_until
            FROM catalyst_worker_lock WHERE lock_name=?
            """,
            (lock_name,),
        ).fetchone()
        if (
            row is None
            or row["owner_id"] != owner_id
            or int(row["fencing_token"]) != int(fencing_token or 0)
            or (_as_utc(row["lease_until"]) or now) <= now
        ):
            raise CatalystRepositoryError(
                "focus_producer_lease_lost",
                "Focus producer lease was lost before the cache write",
            )

    def sync_state(self, stream: str) -> dict[str, Any]:
        if stream not in _STREAMS:
            raise ValueError("unknown catalyst stream")
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM catalyst_sync_state WHERE stream=?", (stream,)
            ).fetchone()
            return dict(row) if row else {"stream": stream}

    def current_focus_context(self) -> FocusContextResponse | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT raw_json FROM focus_context_snapshots ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            return FocusContextResponse.model_validate_json(row["raw_json"])

    def daily_strength_snapshot(
        self,
        *,
        trading_day: date | str,
        cache_version: str,
        strength_feature_version: str,
        strength_score_version: str,
        normalization_version: str,
        range_persistence_version: str,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Read one unexpired trading-day cache entry without mutating it."""

        day = trading_day.isoformat() if isinstance(trading_day, date) else str(trading_day)
        if not day or not cache_version:
            raise ValueError("daily strength cache identity is required")
        observed = now or _now()
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT trading_day,cache_version,universe_version,
                       strength_feature_version,strength_score_version,
                       normalization_version,range_persistence_version,
                       payload_hash,coverage,status,data_through,payload_json,
                       cached_at,expires_at
                FROM focus_daily_strength_snapshots
                WHERE trading_day=? AND cache_version=?
                """,
                (day, cache_version),
            ).fetchone()
        if row is None:
            return None
        expected_versions = (
            strength_feature_version,
            strength_score_version,
            normalization_version,
            range_persistence_version,
        )
        stored_versions = tuple(
            str(row[column])
            for column in (
                "strength_feature_version",
                "strength_score_version",
                "normalization_version",
                "range_persistence_version",
            )
        )
        if stored_versions != expected_versions:
            return None
        expires_at = _as_utc(row["expires_at"])
        if expires_at is not None and expires_at <= observed:
            return None
        try:
            payload = _loads(row["payload_json"], {})
            encoded = _json(payload)
            coverage = float(row["coverage"])
            payload_coverage = float(payload["coverage"])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None
        if (
            hashlib.sha256(encoded.encode("utf-8")).hexdigest()
            != row["payload_hash"]
            or not 0 <= coverage <= 1
            or abs(payload_coverage - coverage) > 1e-9
            or str(payload.get("universe_version") or "")
            != str(row["universe_version"])
        ):
            return None
        return {
            "trading_day": row["trading_day"],
            "cache_version": row["cache_version"],
            "universe_version": row["universe_version"],
            "status": row["status"],
            "data_through": row["data_through"],
            "strength_feature_version": row["strength_feature_version"],
            "strength_score_version": row["strength_score_version"],
            "normalization_version": row["normalization_version"],
            "range_persistence_version": row["range_persistence_version"],
            "payload_hash": row["payload_hash"],
            "coverage": coverage,
            "payload": payload,
            "cached_at": row["cached_at"],
            "expires_at": row["expires_at"],
        }

    def cache_daily_strength_snapshot(
        self,
        *,
        trading_day: date | str,
        cache_version: str,
        universe_version: str,
        strength_feature_version: str,
        strength_score_version: str,
        normalization_version: str,
        range_persistence_version: str,
        coverage: float,
        status: str,
        payload: dict[str, Any],
        data_through: datetime | str | None,
        degraded_ttl_seconds: int,
        now: datetime | None = None,
        lock_name: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> None:
        """Persist active/degraded daily inputs; unavailable results are rejected."""

        if status not in {"active", "degraded"}:
            raise ValueError("only active or degraded daily strength snapshots are cacheable")
        if degraded_ttl_seconds <= 0:
            raise ValueError("degraded daily strength TTL must be positive")
        day = trading_day.isoformat() if isinstance(trading_day, date) else str(trading_day)
        audit_versions = (
            strength_feature_version,
            strength_score_version,
            normalization_version,
            range_persistence_version,
        )
        if (
            not day
            or not cache_version
            or not universe_version
            or any(not str(value).strip() for value in audit_versions)
        ):
            raise ValueError("daily strength cache identity is required")
        if str(payload.get("universe_version") or "") != universe_version:
            raise ValueError("daily strength payload universe version is invalid")
        try:
            normalized_coverage = float(coverage)
            payload_coverage = float(payload["coverage"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("daily strength coverage is required") from exc
        if (
            not 0 <= normalized_coverage <= 1
            or abs(payload_coverage - normalized_coverage) > 1e-9
        ):
            raise ValueError("daily strength coverage is invalid")
        payload_json = _json(payload)
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        observed = now or _now()
        expires_at = (
            observed + timedelta(seconds=degraded_ttl_seconds)
            if status == "degraded"
            else None
        )
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_focus_producer_fence(
                connection,
                lock_name=lock_name,
                owner_id=owner_id,
                fencing_token=fencing_token,
                now=observed,
            )
            connection.execute(
                """
                INSERT INTO focus_daily_strength_snapshots(
                    trading_day,cache_version,universe_version,
                    strength_feature_version,strength_score_version,
                    normalization_version,range_persistence_version,
                    payload_hash,coverage,status,data_through,payload_json,
                    cached_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(trading_day,cache_version) DO UPDATE SET
                    universe_version=excluded.universe_version,
                    strength_feature_version=excluded.strength_feature_version,
                    strength_score_version=excluded.strength_score_version,
                    normalization_version=excluded.normalization_version,
                    range_persistence_version=excluded.range_persistence_version,
                    payload_hash=excluded.payload_hash,
                    coverage=excluded.coverage,
                    status=excluded.status,
                    data_through=excluded.data_through,
                    payload_json=excluded.payload_json,
                    cached_at=excluded.cached_at,
                    expires_at=excluded.expires_at
                """,
                (
                    day,
                    cache_version,
                    universe_version[:200],
                    strength_feature_version[:200],
                    strength_score_version[:200],
                    normalization_version[:200],
                    range_persistence_version[:200],
                    payload_hash,
                    normalized_coverage,
                    status,
                    _iso(data_through),
                    payload_json,
                    _iso(observed),
                    _iso(expires_at),
                ),
            )
            connection.commit()

    def prune_focus_retention(
        self,
        *,
        snapshot_days: int,
        snapshot_full_resolution_days: int = 30,
        snapshot_daily_rollup_enabled: bool = True,
        daily_strength_days: int,
        batch_size: int = 500,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Compact focus history without removing a live or referenced input.

        Existing snapshots are the daily representatives; no synthetic payload is
        manufactured.  One invocation removes at most ``batch_size`` rows in
        total. Snapshot and daily-cache deletes use separate short writer
        transactions so the producer never holds the database write lock while
        scanning the complete snapshot history.
        """

        if (
            snapshot_days <= 0
            or snapshot_full_resolution_days <= 0
            or daily_strength_days <= 0
        ):
            raise ValueError("focus retention windows must be positive")
        if snapshot_full_resolution_days > snapshot_days:
            raise ValueError("full-resolution retention must not exceed snapshot retention")
        if batch_size <= 0 or batch_size > 5000:
            raise ValueError("focus retention batch size must be between 1 and 5000")
        observed = now or _now()
        full_cutoff = observed - timedelta(days=snapshot_full_resolution_days)
        retention_cutoff = observed - timedelta(days=snapshot_days)

        def referenced_revisions(raw_values: Iterable[str | None]) -> set[int]:
            revisions: set[int] = set()
            for raw in raw_values:
                try:
                    value = _loads(raw, {})
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                stack: list[Any] = [value]
                visited = 0
                while stack and visited < 10_000:
                    current = stack.pop()
                    visited += 1
                    if isinstance(current, dict):
                        revision = current.get("focus_revision")
                        if isinstance(revision, int) and not isinstance(revision, bool) and revision > 0:
                            revisions.add(revision)
                        stack.extend(current.values())
                    elif isinstance(current, list):
                        stack.extend(current)
            return revisions

        # Plan with a read-only connection. This can scan the retained history
        # without blocking publication or market-focus task updates.
        with self._read() as connection:
            # Pin the generation and reference scan to the same WAL snapshot.
            # The writer can then reject this plan with one indexed lookup if
            # any cycle/job reference changed before deletion starts.
            connection.execute("BEGIN")
            generation_row = connection.execute(
                "SELECT generation FROM focus_reference_generation WHERE singleton=1"
            ).fetchone()
            reference_generation = (
                int(generation_row["generation"])
                if generation_row is not None
                else 0
            )
            snapshot_rows = connection.execute(
                """
                SELECT revision,as_of,created_at
                FROM focus_context_snapshots ORDER BY revision
                """
            ).fetchall()
            latest_revision = (
                max(int(row["revision"]) for row in snapshot_rows)
                if snapshot_rows
                else None
            )
            reference_values = [
                row[0]
                for row in connection.execute(
                    "SELECT raw_json FROM catalyst_market_focus_cycles"
                ).fetchall()
            ]
            reference_values.extend(
                row[0]
                for row in connection.execute(
                    "SELECT result_json FROM catalyst_market_focus_jobs "
                    "WHERE result_json IS NOT NULL"
                ).fetchall()
            )
            available_revisions = {int(row["revision"]) for row in snapshot_rows}
            protected = referenced_revisions(reference_values) & available_revisions
            if latest_revision is not None:
                protected.add(latest_revision)
            connection.commit()

        keep: set[int] = set(protected)
        middle_by_day: dict[date, tuple[datetime, int]] = {}
        middle_day_by_revision: dict[int, date] = {}
        for row in snapshot_rows:
            revision = int(row["revision"])
            created_at = _as_utc(row["created_at"])
            as_of = _as_utc(row["as_of"])
            if created_at is None:
                keep.add(revision)
                continue
            if created_at >= full_cutoff:
                keep.add(revision)
                continue
            if created_at < retention_cutoff:
                continue
            if not snapshot_daily_rollup_enabled:
                keep.add(revision)
                continue
            representative_time = as_of or created_at
            trading_day = representative_time.astimezone(
                _FOCUS_MARKET_TIMEZONE
            ).date()
            middle_day_by_revision[revision] = trading_day
            current = middle_by_day.get(trading_day)
            candidate = (representative_time, revision)
            if current is None or candidate > current:
                middle_by_day[trading_day] = candidate
        keep.update(item[1] for item in middle_by_day.values())

        snapshot_candidates = [
            int(row["revision"])
            for row in snapshot_rows
            if int(row["revision"]) not in keep
        ][:batch_size]
        snapshots = 0
        daily = 0
        batches = 0
        rollup_dates: set[date] = set()
        remaining = batch_size

        if snapshot_candidates:
            with self._write() as connection:
                connection.execute("BEGIN IMMEDIATE")
                current_generation_row = connection.execute(
                    "SELECT generation FROM focus_reference_generation WHERE singleton=1"
                ).fetchone()
                current_generation = (
                    int(current_generation_row["generation"])
                    if current_generation_row is not None
                    else -1
                )
                if current_generation != reference_generation:
                    # A cycle/job may now refer to one of the planned rows.
                    # Defer snapshot deletion to the next bounded invocation;
                    # never rescan every JSON document while holding this lock.
                    connection.commit()
                else:
                    connection.execute(
                        "CREATE TEMP TABLE focus_retention_candidates("
                        "revision INTEGER PRIMARY KEY) WITHOUT ROWID"
                    )
                    connection.executemany(
                        "INSERT INTO focus_retention_candidates(revision) VALUES(?)",
                        ((revision,) for revision in snapshot_candidates),
                    )
                    # Snapshot publication is also serialized by this writer
                    # lock, so only the latest row needs one bounded recheck.
                    current_latest = connection.execute(
                        "SELECT revision FROM focus_context_snapshots "
                        "ORDER BY revision DESC LIMIT 1"
                    ).fetchone()
                    if current_latest is not None:
                        protected.add(int(current_latest["revision"]))
                        connection.execute(
                            "DELETE FROM focus_retention_candidates WHERE revision=?",
                            (int(current_latest["revision"]),),
                        )
                    delete_targets = [
                        int(row["revision"])
                        for row in connection.execute(
                            """
                            SELECT snapshot.revision
                            FROM focus_context_snapshots AS snapshot
                            JOIN focus_retention_candidates AS candidate
                              ON candidate.revision=snapshot.revision
                            ORDER BY snapshot.revision
                            LIMIT ?
                            """,
                            (remaining,),
                        ).fetchall()
                    ]
                    cursor = connection.execute(
                        """
                        DELETE FROM focus_context_snapshots
                        WHERE revision IN (
                            SELECT snapshot.revision
                            FROM focus_context_snapshots AS snapshot
                            JOIN focus_retention_candidates AS candidate
                              ON candidate.revision=snapshot.revision
                            ORDER BY snapshot.revision
                            LIMIT ?
                        )
                        """,
                        (remaining,),
                    )
                    connection.commit()
                    snapshots = max(0, cursor.rowcount)
                    if snapshots:
                        batches += 1
                        remaining = max(0, remaining - snapshots)
                        rollup_dates.update(
                            middle_day_by_revision[revision]
                            for revision in delete_targets
                            if revision in middle_day_by_revision
                        )

        if remaining:
            with self._write() as connection:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    DELETE FROM focus_daily_strength_snapshots
                    WHERE rowid IN (
                        SELECT rowid
                        FROM focus_daily_strength_snapshots
                        WHERE cached_at<?
                        ORDER BY cached_at,trading_day,cache_version
                        LIMIT ?
                    )
                    """,
                    (
                        _iso(observed - timedelta(days=daily_strength_days)),
                        remaining,
                    ),
                )
                connection.commit()
                daily = max(0, cursor.rowcount)
                if daily:
                    batches += 1

        with self._read() as connection:
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise CatalystRepositoryError(
                    "focus_retention_foreign_key_violation",
                    "Focus retention failed the foreign-key check",
                )
            retained = int(
                connection.execute(
                    "SELECT COUNT(*) FROM focus_context_snapshots"
                ).fetchone()[0]
            )
            page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
            page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
            free_pages = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        return {
            "deleted": snapshots,
            "retained": retained,
            "rollup_created": len(rollup_dates),
            "protected": len(protected),
            "database_bytes": page_count * page_size,
            "live_bytes": max(0, page_count - free_pages) * page_size,
            "foreign_key_violations": 0,
            "batches": batches,
            # Preserve the established internal names for existing logs.
            "focus_snapshots": snapshots,
            "daily_strength_snapshots": daily,
        }

    def publish_focus_context(
        self,
        draft: FocusContextDraft,
        *,
        now: datetime | None = None,
        lock_name: str | None = None,
        owner_id: str | None = None,
        fencing_token: int | None = None,
    ) -> FocusContextResponse:
        """Publish one immutable focus revision in a single transaction."""

        observed = now or _now()
        lease_values = (lock_name, owner_id, fencing_token)
        if any(value is not None for value in lease_values) and not all(
            value is not None for value in lease_values
        ):
            raise ValueError("focus publication lease fields must be supplied together")
        material = draft.model_dump(mode="json")
        content_hash = hashlib.sha256(_json(material).encode("utf-8")).hexdigest()
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if lock_name is not None:
                lease = connection.execute(
                    """
                    SELECT owner_id,fencing_token,lease_until
                    FROM catalyst_worker_lock WHERE lock_name=?
                    """,
                    (lock_name,),
                ).fetchone()
                if (
                    lease is None
                    or lease["owner_id"] != owner_id
                    or int(lease["fencing_token"]) != int(fencing_token or 0)
                    or (_as_utc(lease["lease_until"]) or observed) <= observed
                ):
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "focus_producer_lease_lost",
                        "Focus producer lease was lost before publication",
                    )
            current = connection.execute(
                """
                SELECT revision,as_of,data_through,content_hash,raw_json
                FROM focus_context_snapshots ORDER BY revision DESC LIMIT 1
                """
            ).fetchone()
            if current is not None:
                current_as_of = _as_utc(current["as_of"])
                current_data_through = _as_utc(current["data_through"])
                draft_as_of = _as_utc(draft.as_of)
                draft_data_through = _as_utc(draft.data_through)
                if (
                    current_as_of is not None
                    and draft_as_of is not None
                    and draft_as_of < current_as_of
                ) or (
                    current_data_through is not None
                    and (
                        draft_data_through is None
                        or draft_data_through < current_data_through
                    )
                ):
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "focus_snapshot_time_regression",
                        "Focus snapshot publication would move its market-data time backwards",
                    )
            if current is not None and current["content_hash"] == content_hash:
                connection.commit()
                return FocusContextResponse.model_validate_json(current["raw_json"])
            revision = int(current["revision"] or 0) + 1 if current is not None else 1
            response = FocusContextResponse(revision=revision, **material)
            raw = _json(response.model_dump(mode="json"))
            connection.execute(
                """
                INSERT INTO focus_context_snapshots(
                    revision,as_of,data_through,market_session,universe_version,
                    content_hash,raw_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?)
                """,
                (
                    revision,
                    _iso(draft.as_of),
                    _iso(draft.data_through),
                    draft.market_session,
                    draft.universe_version,
                    content_hash,
                    raw,
                    _iso(observed),
                ),
            )
            for symbol in response.symbols:
                symbol_raw = symbol.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO focus_context_symbols(
                        revision,ticker,dollar_volume_rank,reasons_json,raw_json
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        revision,
                        symbol.ticker,
                        symbol.dollar_volume_rank,
                        _json(symbol.universe_reasons),
                        _json(symbol_raw),
                    ),
                )
            connection.commit()
            return response

    def consume_focus_nonce(
        self,
        *,
        key_id: str,
        nonce: str,
        expires_at: datetime,
        now: datetime | None = None,
    ) -> None:
        observed = now or _now()
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM macrolens_focus_nonces WHERE expires_at<=?",
                (_iso(observed),),
            )
            try:
                connection.execute(
                    """
                    INSERT INTO macrolens_focus_nonces(key_id,nonce,expires_at)
                    VALUES(?,?,?)
                    """,
                    (key_id, nonce, _iso(expires_at)),
                )
            except sqlite3.IntegrityError as exc:
                connection.rollback()
                raise CatalystRepositoryError(
                    "focus_replay", "MacroLens focus request nonce was already used"
                ) from exc
            connection.commit()

    def begin_sync_run(
        self,
        stream: str,
        *,
        snapshot_token: str | None = None,
        sync_mode: str = "incremental",
        resync_generation: int | None = None,
        now: datetime | None = None,
    ) -> str:
        if stream not in _STREAMS:
            raise ValueError("unknown catalyst stream")
        if sync_mode not in {"incremental", "resync"}:
            raise ValueError("unknown catalyst sync mode")
        if stream != "feed" and sync_mode != "incremental":
            raise ValueError("only the feed stream supports resync")
        run_id = uuid.uuid4().hex
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                "SELECT watermark_sequence,resync_required,resync_generation "
                "FROM catalyst_sync_state WHERE stream=?",
                (stream,),
            ).fetchone()
            if sync_mode == "resync":
                if state is None or not state["resync_required"]:
                    raise CatalystRepositoryError(
                        "resync_not_required", "Feed resync has not been requested"
                    )
                expected_generation = int(state["resync_generation"] or 0) + 1
                if resync_generation is None:
                    resync_generation = expected_generation
                if resync_generation != expected_generation:
                    raise CatalystRepositoryError(
                        "resync_generation_mismatch", "Feed resync generation is stale"
                    )
            connection.execute(
                """
                INSERT INTO catalyst_sync_runs(
                    run_id,stream,status,started_at,snapshot_token,from_sequence,
                    sync_mode,resync_generation
                ) VALUES(?,?,"running",?,?,?,?,?)
                """,
                (
                    run_id,
                    stream,
                    timestamp,
                    snapshot_token,
                    state["watermark_sequence"] if state else None,
                    sync_mode,
                    resync_generation,
                ),
            )
            connection.execute(
                "UPDATE catalyst_sync_state SET last_attempt_at=? WHERE stream=?",
                (timestamp, stream),
            )
            connection.commit()
        return run_id

    def require_feed_resync(
        self,
        *,
        resync_from: datetime | None,
        now: datetime | None = None,
    ) -> None:
        """Latch recovery mode without discarding the last readable snapshot."""

        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET resync_required=1,last_attempt_at=?,last_error_code='updated_after_too_old',
                    remote_status='stale',resync_from=?
                WHERE stream='feed'
                """,
                (timestamp, _iso(resync_from)),
            )
            connection.commit()

    def stage_latest_page(self, run_id: str, items: Sequence[CatalystItem]) -> None:
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status,stream FROM catalyst_sync_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running" or run["stream"] != "feed":
                raise CatalystRepositoryError("invalid_sync_run", "Feed sync run is not active")
            for item in items:
                raw = item.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO catalyst_staging_items(run_id,news_id,change_sequence,updated_at,raw_json)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(run_id,news_id,change_sequence) DO UPDATE SET
                        updated_at=excluded.updated_at,
                        raw_json=excluded.raw_json
                    """,
                    (run_id, item.news_id, item.change_sequence, _iso(item.updated_at), _json(raw)),
                )
            connection.commit()

    def _insert_item_revision(
        self,
        connection: sqlite3.Connection,
        item: CatalystItem,
        *,
        cached_at: str,
    ) -> None:
        raw = item.model_dump(mode="json")
        connection.execute(
            """
            INSERT INTO catalyst_item_revisions(
                news_id,change_sequence,content_hash,source,title,summary,url,published_at,
                fetched_at,updated_at,cached_at,source_tickers_json,analysis_status,is_stale,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(news_id,change_sequence) DO NOTHING
            """,
            (
                item.news_id,
                item.change_sequence,
                item.content_hash,
                item.source,
                item.title,
                item.summary,
                item.url,
                _iso(item.published_at),
                _iso(item.fetched_at),
                _iso(item.updated_at),
                cached_at,
                _json(item.source_tickers),
                item.analysis_status.value,
                int(item.is_stale),
                _json(raw),
            ),
        )
        for ticker in item.source_tickers:
            connection.execute(
                """
                INSERT INTO catalyst_item_tickers(news_id,change_sequence,ticker)
                VALUES(?,?,?)
                ON CONFLICT(news_id,change_sequence,ticker) DO NOTHING
                """,
                (item.news_id, item.change_sequence, ticker),
            )
        if item.analysis is None or item.analyzed_at is None or item.available_at is None:
            return
        analysis_payload = item.analysis.model_dump(mode="json")
        revision_id = f"{item.analysis.analysis_id}:{item.analysis.revision}"
        connection.execute(
            """
            INSERT INTO catalyst_analysis_revisions(
                analysis_revision_id,news_id,content_hash,item_change_sequence,
                analyzed_at,available_at,model,reasoning,
                prompt_version,analysis_schema_version,classification,confidence,
                overall_sentiment,market_relevance,insufficient_context,cached_at,raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(analysis_revision_id) DO NOTHING
            """,
            (
                revision_id,
                item.news_id,
                item.content_hash,
                item.change_sequence,
                _iso(item.analyzed_at),
                _iso(item.available_at),
                item.analysis.model,
                item.analysis.reasoning,
                item.analysis.prompt_version,
                item.analysis.schema_version,
                item.analysis.classification.value,
                item.analysis.confidence,
                item.analysis.overall_sentiment,
                item.analysis.market_relevance,
                int(item.analysis.insufficient_context),
                cached_at,
                _json(analysis_payload),
            ),
        )
        for impact in item.analysis.affected_stocks:
            connection.execute(
                """
                INSERT INTO catalyst_stock_impacts(
                    analysis_revision_id,news_id,item_change_sequence,ticker,company,impact_score,confidence,
                    horizon,mechanism,reason,content_hash,published_at,fetched_at,analyzed_at,
                    available_at,model,reasoning,prompt_version,analysis_schema_version
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(analysis_revision_id,ticker) DO NOTHING
                """,
                (
                    revision_id,
                    item.news_id,
                    item.change_sequence,
                    impact.ticker,
                    impact.company,
                    impact.impact_score,
                    impact.confidence,
                    impact.horizon,
                    impact.mechanism,
                    impact.reason,
                    item.content_hash,
                    _iso(item.published_at),
                    _iso(item.fetched_at),
                    _iso(item.analyzed_at),
                    _iso(item.available_at),
                    item.analysis.model,
                    item.analysis.reasoning,
                    item.analysis.prompt_version,
                    item.analysis.schema_version,
                ),
            )

    def publish_latest(
        self,
        run_id: str,
        *,
        snapshot_token: str,
        data_through: datetime | None,
        next_updated_after: datetime | None,
        watermark_sequence: int | None,
        worker_id: str | None = None,
        fencing_token: int | None = None,
        now: datetime | None = None,
    ) -> str:
        observed = now or _now()
        cached_at = _iso(observed)
        snapshot_id = uuid.uuid4().hex
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_worker_fence(
                connection,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=observed,
            )
            run = connection.execute(
                """
                SELECT status,stream,snapshot_token,from_sequence,sync_mode,resync_generation
                FROM catalyst_sync_runs WHERE run_id=?
                """,
                (run_id,),
            ).fetchone()
            if run is None or run["status"] != "running" or run["stream"] != "feed":
                raise CatalystRepositoryError("invalid_sync_run", "Feed sync run is not active")
            if run["snapshot_token"] and run["snapshot_token"] != snapshot_token:
                raise CatalystRepositoryError("snapshot_changed", "Remote snapshot changed during pagination")
            staged = connection.execute(
                "SELECT raw_json FROM catalyst_staging_items WHERE run_id=? ORDER BY change_sequence,news_id",
                (run_id,),
            ).fetchall()
            current = connection.execute(
                "SELECT watermark_sequence,snapshot_generation FROM catalyst_sync_state WHERE stream='feed'"
            ).fetchone()
            old_watermark = current["watermark_sequence"] if current else None
            is_resync = run["sync_mode"] == "resync"
            if (
                not is_resync
                and old_watermark is not None
                and watermark_sequence is not None
                and watermark_sequence < old_watermark
            ):
                raise CatalystRepositoryError("watermark_regression", "Remote change_sequence moved backwards")
            for row in staged:
                self._insert_item_revision(
                    connection,
                    CatalystItem.model_validate_json(row["raw_json"]),
                    cached_at=cached_at,
                )
            if self._publish_hook:
                self._publish_hook("before_complete", connection)
            connection.execute(
                """
                UPDATE catalyst_sync_runs
                SET status='completed',completed_at=?,snapshot_token=?,through_sequence=?,
                    data_through=?,item_count=?
                WHERE run_id=?
                """,
                (cached_at, snapshot_token, watermark_sequence, _iso(data_through), len(staged), run_id),
            )
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET last_success_at=?,data_through=?,watermark_sequence=?,updated_after=?,
                    consecutive_failures=0,next_attempt_at=NULL,circuit_open_until=NULL,
                    last_error_code=NULL,remote_status='active',
                    snapshot_generation=snapshot_generation+1,current_snapshot_id=?,
                    resync_required=CASE WHEN ? THEN 0 ELSE resync_required END,
                    resync_generation=CASE WHEN ? THEN ? ELSE resync_generation END,
                    last_resync_at=CASE WHEN ? THEN ? ELSE last_resync_at END,
                    resync_from=CASE WHEN ? THEN NULL ELSE resync_from END
                WHERE stream='feed'
                """,
                (
                    cached_at,
                    _iso(data_through),
                    watermark_sequence if watermark_sequence is not None else old_watermark,
                    _iso(next_updated_after),
                    snapshot_id,
                    int(is_resync),
                    int(is_resync),
                    run["resync_generation"],
                    int(is_resync),
                    cached_at,
                    int(is_resync),
                ),
            )
            connection.execute("DELETE FROM catalyst_staging_items WHERE run_id=?", (run_id,))
            connection.commit()
        return snapshot_id

    def abort_sync_run(
        self,
        run_id: str,
        error_code: str,
        *,
        next_attempt_at: datetime | None = None,
        circuit_open_until: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT stream,status FROM catalyst_sync_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None:
                connection.rollback()
                return
            if run["status"] == "running":
                connection.execute(
                    "UPDATE catalyst_sync_runs SET status='failed',completed_at=?,error_code=? WHERE run_id=?",
                    (timestamp, error_code[:100], run_id),
                )
                connection.execute("DELETE FROM catalyst_staging_items WHERE run_id=?", (run_id,))
                connection.execute("DELETE FROM catalyst_staging_calendar WHERE run_id=?", (run_id,))
                connection.execute(
                    """
                    UPDATE catalyst_sync_state
                    SET consecutive_failures=consecutive_failures+1,last_error_code=?,
                        next_attempt_at=?,circuit_open_until=?,remote_status='degraded'
                    WHERE stream=?
                    """,
                    (error_code[:100], _iso(next_attempt_at), _iso(circuit_open_until), run["stream"]),
                )
            connection.commit()

    def stage_calendar(self, run_id: str, events: Sequence[CalendarEvent]) -> None:
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            run = connection.execute(
                "SELECT status,stream FROM catalyst_sync_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running" or run["stream"] != "calendar":
                raise CatalystRepositoryError("invalid_sync_run", "Calendar sync run is not active")
            for event in events:
                connection.execute(
                    """
                    INSERT INTO catalyst_staging_calendar(run_id,event_id,available_at,raw_json)
                    VALUES(?,?,?,?)
                    ON CONFLICT(run_id,event_id,available_at) DO UPDATE SET raw_json=excluded.raw_json
                    """,
                    (
                        run_id,
                        event.event_id,
                        _iso(event.available_at),
                        _json(event.model_dump(mode="json")),
                    ),
                )
            connection.commit()

    def publish_calendar(
        self,
        run_id: str,
        *,
        data_through: datetime | None,
        worker_id: str | None = None,
        fencing_token: int | None = None,
        now: datetime | None = None,
    ) -> str:
        observed = now or _now()
        cached_at = _iso(observed)
        snapshot_id = uuid.uuid4().hex
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_worker_fence(
                connection,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=observed,
            )
            run = connection.execute(
                "SELECT status,stream FROM catalyst_sync_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running" or run["stream"] != "calendar":
                raise CatalystRepositoryError("invalid_sync_run", "Calendar sync run is not active")
            rows = connection.execute(
                "SELECT raw_json FROM catalyst_staging_calendar WHERE run_id=? ORDER BY event_id,available_at",
                (run_id,),
            ).fetchall()
            for row in rows:
                event = CalendarEvent.model_validate_json(row["raw_json"])
                raw = event.model_dump(mode="json")
                revision_id = hashlib.sha256(
                    _json({"event": raw, "available_at": _iso(event.available_at)}).encode()
                ).hexdigest()
                connection.execute(
                    """
                    INSERT INTO catalyst_calendar_event_revisions(
                        revision_id,event_id,currency,title,impact,scheduled_at,forecast_json,
                        previous_json,actual_json,is_stale,source_fetched_at,available_at,cached_at,raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(revision_id) DO NOTHING
                    """,
                    (
                        revision_id,
                        event.event_id,
                        event.currency,
                        event.title,
                        event.impact,
                        _iso(event.scheduled_at),
                        None if event.forecast is None else _json(event.forecast),
                        None if event.previous is None else _json(event.previous),
                        None if event.actual is None else _json(event.actual),
                        int(event.is_stale),
                        _iso(event.source_fetched_at),
                        _iso(event.available_at),
                        cached_at,
                        _json(raw),
                    ),
                )
            if self._publish_hook:
                self._publish_hook("before_calendar_complete", connection)
            connection.execute(
                """
                UPDATE catalyst_sync_runs
                SET status='completed',completed_at=?,data_through=?,item_count=?
                WHERE run_id=?
                """,
                (cached_at, _iso(data_through), len(rows), run_id),
            )
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET last_success_at=?,data_through=?,consecutive_failures=0,
                    next_attempt_at=NULL,circuit_open_until=NULL,last_error_code=NULL,
                    remote_status='active',snapshot_generation=snapshot_generation+1,
                    current_snapshot_id=?
                WHERE stream='calendar'
                """,
                (cached_at, _iso(data_through), snapshot_id),
            )
            connection.execute("DELETE FROM catalyst_staging_calendar WHERE run_id=?", (run_id,))
            connection.commit()
        return snapshot_id

    def publish_health(
        self,
        *,
        status: str,
        data_through: datetime | None,
        sources: dict[str, ComponentHealth],
        model: str | None = None,
        reasoning: str | None = None,
        execution_mode: str | None = None,
        analysis_trigger_enabled: bool = False,
        warnings: Sequence[str] = (),
        worker_id: str | None = None,
        fencing_token: int | None = None,
        observed_at: datetime | None = None,
    ) -> None:
        observed = observed_at or _now()
        timestamp = _iso(observed)
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_worker_fence(
                connection,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=observed,
            )
            for source_name, source in sources.items():
                raw = source.model_dump(mode="json")
                connection.execute(
                    """
                    INSERT INTO catalyst_source_health(
                        source,status,last_attempt,last_success,failures,next_attempt,raw_count,inserted_count,
                        duplicates_count,observed_at,raw_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(source) DO UPDATE SET
                        status=excluded.status,last_attempt=excluded.last_attempt,
                        last_success=excluded.last_success,
                        failures=excluded.failures,next_attempt=excluded.next_attempt,
                        raw_count=excluded.raw_count,inserted_count=excluded.inserted_count,
                        duplicates_count=excluded.duplicates_count,observed_at=excluded.observed_at,
                        raw_json=excluded.raw_json
                    """,
                    (
                        source_name,
                        source.status,
                        _iso(source.last_attempt_at),
                        _iso(source.last_success_at),
                        source.consecutive_failures,
                        _iso(source.next_attempt_at),
                        source.raw_count,
                        source.inserted_count,
                        source.duplicates_count,
                        timestamp,
                        _json(raw),
                    ),
                )
            if model and reasoning and execution_mode:
                connection.execute(
                    """
                    INSERT INTO catalyst_remote_runtime(
                        singleton,model,reasoning,execution_mode,analysis_trigger_enabled,
                        warnings_json,observed_at
                    ) VALUES(1,?,?,?,?,?,?)
                    ON CONFLICT(singleton) DO UPDATE SET
                        model=excluded.model,reasoning=excluded.reasoning,
                        execution_mode=excluded.execution_mode,
                        analysis_trigger_enabled=excluded.analysis_trigger_enabled,
                        warnings_json=excluded.warnings_json,observed_at=excluded.observed_at
                    """,
                    (
                        model,
                        reasoning,
                        execution_mode,
                        int(analysis_trigger_enabled),
                        _json(list(warnings)),
                        timestamp,
                    ),
                )
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET last_attempt_at=?,last_success_at=?,data_through=?,consecutive_failures=0,
                    next_attempt_at=NULL,circuit_open_until=NULL,last_error_code=NULL,
                    remote_status=?
                WHERE stream='health'
                """,
                (timestamp, timestamp, _iso(data_through), status[:40]),
            )
            connection.commit()

    def record_stream_failure(
        self,
        stream: str,
        error_code: str,
        *,
        retry_after_seconds: int | None = None,
        open_circuit: bool = False,
        circuit_seconds: int = 300,
        now: datetime | None = None,
    ) -> None:
        if stream not in _STREAMS:
            raise ValueError("unknown catalyst stream")
        observed = now or _now()
        retry_at = observed + timedelta(seconds=retry_after_seconds or 0) if retry_after_seconds else None
        circuit_until = observed + timedelta(seconds=circuit_seconds) if open_circuit else None
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET last_attempt_at=?,consecutive_failures=consecutive_failures+1,
                    next_attempt_at=?,circuit_open_until=?,last_error_code=?,remote_status='degraded'
                WHERE stream=?
                """,
                (
                    _iso(observed),
                    _iso(retry_at),
                    _iso(circuit_until),
                    error_code[:100],
                    stream,
                ),
            )
            connection.commit()

    def mark_stream_success(
        self,
        stream: str,
        *,
        remote_status: str = "active",
        now: datetime | None = None,
    ) -> None:
        if stream not in _STREAMS:
            raise ValueError("unknown catalyst stream")
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute(
                """
                UPDATE catalyst_sync_state
                SET last_attempt_at=?,last_success_at=?,consecutive_failures=0,
                    next_attempt_at=NULL,circuit_open_until=NULL,last_error_code=NULL,
                    remote_status=?
                WHERE stream=?
                """,
                (timestamp, timestamp, remote_status[:40], stream),
            )
            connection.commit()

    @staticmethod
    def _query_hash(filters: dict[str, Any]) -> str:
        return hashlib.sha256(_json(filters).encode()).hexdigest()[:24]

    @staticmethod
    def _encode_cursor(payload: dict[str, Any]) -> str:
        encoded = base64.urlsafe_b64encode(_json(payload).encode()).decode().rstrip("=")
        return encoded

    @staticmethod
    def _decode_cursor(value: str) -> dict[str, Any]:
        try:
            padded = value + "=" * (-len(value) % 4)
            decoded = base64.b64decode(
                padded.encode("ascii"), altchars=b"-_", validate=True
            )
            payload = json.loads(decoded)
        except (
            ValueError,
            TypeError,
            UnicodeEncodeError,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidCursorError() from exc
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "q",
            "as_of",
            "cutoff",
            "last",
        }:
            raise InvalidCursorError()
        if (
            payload["v"] != 1
            or not isinstance(payload["q"], str)
            or len(payload["q"]) != 24
            or any(character not in "0123456789abcdef" for character in payload["q"])
            or not isinstance(payload["as_of"], str)
            or not isinstance(payload["cutoff"], str)
            or not isinstance(payload["last"], list)
            or len(payload["last"]) != 2
            or not isinstance(payload["last"][0], str)
            or isinstance(payload["last"][1], bool)
            or not isinstance(payload["last"][1], int)
            or payload["last"][1] < 1
        ):
            raise InvalidCursorError()
        try:
            as_of = _as_utc(payload["as_of"])
            cutoff = _as_utc(payload["cutoff"])
            last_at = _as_utc(payload["last"][0])
        except (AttributeError, TypeError, ValueError) as exc:
            raise InvalidCursorError() from exc
        if as_of is None or cutoff is None or last_at is None:
            raise InvalidCursorError()
        payload["as_of"] = _iso(as_of)
        payload["cutoff"] = _iso(cutoff)
        payload["last"][0] = _iso(last_at)
        return payload

    @staticmethod
    def _latest_visible_analysis(
        connection: sqlite3.Connection,
        news_id: int,
        content_hash: str,
        change_sequence: int,
        *,
        as_of: str,
        read_cutoff: str,
    ) -> sqlite3.Row | None:
        return connection.execute(
            """
            SELECT * FROM catalyst_analysis_revisions
            WHERE news_id=? AND content_hash=? AND item_change_sequence=?
                AND available_at<=? AND cached_at<=?
            ORDER BY available_at DESC,cached_at DESC,analysis_revision_id DESC
            LIMIT 1
            """,
            (news_id, content_hash, change_sequence, as_of, read_cutoff),
        ).fetchone()

    @staticmethod
    def _visible_impacts(
        connection: sqlite3.Connection, analysis_revision_id: str | None
    ) -> list[dict[str, Any]]:
        if not analysis_revision_id:
            return []
        return [
            {
                "ticker": row["ticker"],
                "company": row["company"],
                "impact_score": row["impact_score"],
                "confidence": row["confidence"],
                "horizon": row["horizon"],
                "mechanism": row["mechanism"],
                "reason": row["reason"],
            }
            for row in connection.execute(
                "SELECT * FROM catalyst_stock_impacts WHERE analysis_revision_id=? ORDER BY abs(impact_score) DESC,ticker",
                (analysis_revision_id,),
            ).fetchall()
        ]

    def _hydrate_visible_item(
        self,
        connection: sqlite3.Connection,
        row: sqlite3.Row,
        *,
        as_of: str,
        read_cutoff: str,
    ) -> dict[str, Any]:
        raw = _loads(row["raw_json"], {})
        analysis_row = self._latest_visible_analysis(
            connection,
            row["news_id"],
            row["content_hash"],
            row["change_sequence"],
            as_of=as_of,
            read_cutoff=read_cutoff,
        )
        if analysis_row is None:
            analysis = None
            analyzed_at = None
            available_at = None
            impacts: list[dict[str, Any]] = []
            analysis_status = raw.get("analysis_status") or "not_requested"
            if analysis_status in {"completed", "insufficient_context"}:
                analysis_status = "not_requested"
        else:
            analysis = _loads(analysis_row["raw_json"], {})
            analyzed_at = analysis_row["analyzed_at"]
            available_at = analysis_row["available_at"]
            impacts = self._visible_impacts(connection, analysis_row["analysis_revision_id"])
            analysis["affected_stocks"] = impacts
            analysis_status = (
                "insufficient_context" if analysis_row["insufficient_context"] else "completed"
            )
        return {
            "news_id": row["news_id"],
            "content_hash": row["content_hash"],
            "change_sequence": row["change_sequence"],
            "source": row["source"],
            "title": row["title"],
            "summary": row["summary"],
            "url": row["url"],
            "published_at": row["published_at"],
            "fetched_at": row["fetched_at"],
            "updated_at": row["updated_at"],
            "source_tickers": _loads(row["source_tickers_json"], []),
            "analysis_status": analysis_status,
            "analysis": analysis,
            "analyzed_at": analyzed_at,
            "available_at": available_at,
            "classification": analysis.get("classification") if analysis else None,
            "impact_score": analysis.get("overall_sentiment") if analysis else None,
            "confidence": analysis.get("confidence") if analysis else None,
            "is_stale": bool(row["is_stale"]),
        }

    def _visible_items(
        self,
        connection: sqlite3.Connection,
        *,
        as_of: datetime,
        read_cutoff: datetime,
        window_start: datetime,
        source: str | None = None,
        ticker: str | None = None,
    ) -> list[dict[str, Any]]:
        as_of_text = _iso(as_of)
        cutoff_text = _iso(read_cutoff)
        window_text = _iso(window_start)
        rows = connection.execute(
            """
            WITH item_candidates AS (
                SELECT *,ROW_NUMBER() OVER(
                    PARTITION BY news_id ORDER BY change_sequence DESC,updated_at DESC
                ) AS row_number
                FROM catalyst_item_revisions
                WHERE fetched_at<=? AND (published_at IS NULL OR published_at<=?)
                    AND updated_at<=? AND cached_at<=?
            ),
            latest_items AS (
                SELECT * FROM item_candidates WHERE row_number=1
            ),
            visible_items AS (
                SELECT * FROM latest_items
                WHERE COALESCE(published_at,fetched_at)>=?
                    AND (? IS NULL OR source=?)
            ),
            analysis_candidates AS (
                SELECT *,ROW_NUMBER() OVER(
                    PARTITION BY news_id,content_hash,item_change_sequence
                    ORDER BY available_at DESC,cached_at DESC,analysis_revision_id DESC
                ) AS analysis_row_number
                FROM catalyst_analysis_revisions AS ar
                WHERE available_at<=? AND cached_at<=?
                    AND EXISTS (
                        SELECT 1 FROM visible_items AS vi
                        WHERE vi.news_id=ar.news_id
                            AND vi.content_hash=ar.content_hash
                            AND vi.change_sequence=ar.item_change_sequence
                    )
            ),
            visible_analysis AS (
                SELECT * FROM analysis_candidates WHERE analysis_row_number=1
            )
            SELECT
                vi.*,
                va.analysis_revision_id AS visible_analysis_revision_id,
                va.analyzed_at AS visible_analyzed_at,
                va.available_at AS visible_available_at,
                va.insufficient_context AS visible_insufficient_context,
                va.raw_json AS visible_analysis_json
            FROM visible_items AS vi
            LEFT JOIN visible_analysis AS va
                ON va.news_id=vi.news_id
                AND va.content_hash=vi.content_hash
                AND va.item_change_sequence=vi.change_sequence
            WHERE ? IS NULL
                OR EXISTS (
                    SELECT 1 FROM catalyst_item_tickers AS it
                    WHERE it.news_id=vi.news_id
                        AND it.change_sequence=vi.change_sequence
                        AND it.ticker=?
                )
                OR EXISTS (
                    SELECT 1 FROM catalyst_stock_impacts AS si
                    WHERE si.analysis_revision_id=va.analysis_revision_id
                        AND si.ticker=?
                )
            """,
            (
                as_of_text,
                as_of_text,
                as_of_text,
                cutoff_text,
                window_text,
                source,
                source,
                as_of_text,
                cutoff_text,
                ticker,
                ticker,
                ticker,
            ),
        ).fetchall()
        analysis_ids = sorted(
            {
                row["visible_analysis_revision_id"]
                for row in rows
                if row["visible_analysis_revision_id"]
            }
        )
        impact_map: dict[str, list[dict[str, Any]]] = {}
        for offset in range(0, len(analysis_ids), 800):
            chunk = analysis_ids[offset : offset + 800]
            placeholders = ",".join("?" for _ in chunk)
            impact_rows = connection.execute(
                f"""
                SELECT * FROM catalyst_stock_impacts
                WHERE analysis_revision_id IN ({placeholders})
                ORDER BY analysis_revision_id,abs(impact_score) DESC,ticker
                """,
                chunk,
            ).fetchall()
            for impact in impact_rows:
                impact_map.setdefault(impact["analysis_revision_id"], []).append(
                    {
                        "ticker": impact["ticker"],
                        "company": impact["company"],
                        "impact_score": impact["impact_score"],
                        "confidence": impact["confidence"],
                        "horizon": impact["horizon"],
                        "mechanism": impact["mechanism"],
                        "reason": impact["reason"],
                    }
                )
        hydrated: list[dict[str, Any]] = []
        for row in rows:
            raw = _loads(row["raw_json"], {})
            revision_id = row["visible_analysis_revision_id"]
            if revision_id:
                analysis = _loads(row["visible_analysis_json"], {})
                impacts = impact_map.get(revision_id, [])
                analysis["affected_stocks"] = impacts
                analysis_status = (
                    "insufficient_context"
                    if row["visible_insufficient_context"]
                    else "completed"
                )
            else:
                analysis = None
                impacts = []
                analysis_status = raw.get("analysis_status") or "not_requested"
                if analysis_status in {"completed", "insufficient_context"}:
                    analysis_status = "not_requested"
            hydrated.append(
                {
                    "news_id": row["news_id"],
                    "content_hash": row["content_hash"],
                    "change_sequence": row["change_sequence"],
                    "source": row["source"],
                    "title": row["title"],
                    "summary": row["summary"],
                    "url": row["url"],
                    "published_at": row["published_at"],
                    "fetched_at": row["fetched_at"],
                    "updated_at": row["updated_at"],
                    "source_tickers": _loads(row["source_tickers_json"], []),
                    "analysis_status": analysis_status,
                    "analysis": analysis,
                    "analyzed_at": row["visible_analyzed_at"],
                    "available_at": row["visible_available_at"],
                    "classification": analysis.get("classification") if analysis else None,
                    "impact_score": analysis.get("overall_sentiment") if analysis else None,
                    "confidence": analysis.get("confidence") if analysis else None,
                    "is_stale": bool(row["is_stale"]),
                }
            )
        return hydrated

    @staticmethod
    def _item_time(item: dict[str, Any]) -> str:
        return str(item.get("published_at") or item["fetched_at"])

    def list_feed(
        self,
        *,
        as_of: datetime,
        window_hours: int = 72,
        limit: int = 50,
        cursor: str | None = None,
        ticker: str | None = None,
        source: str | None = None,
        classification: str | None = None,
        analysis_status: str | None = None,
        min_confidence: int | None = 0,
        include_unanalyzed: bool = True,
        min_abs_impact: int | None = None,
        include_neutral: bool = True,
        horizon: str | None = None,
        mechanism: str | None = None,
        multi_source_only: bool = False,
    ) -> dict[str, Any]:
        min_confidence = 0 if min_confidence is None else min_confidence
        if not 0 <= min_confidence <= 100:
            raise ValueError("min_confidence must be between 0 and 100")
        decoded_cursor = self._decode_cursor(cursor) if cursor else None
        if decoded_cursor is not None:
            frozen_as_of = _as_utc(decoded_cursor["as_of"])
            if frozen_as_of is None:
                raise InvalidCursorError()
            as_of = frozen_as_of
        filters = {
            "as_of": _iso(as_of),
            "window_hours": window_hours,
            "ticker": ticker,
            "source": source,
            "classification": classification,
            "analysis_status": analysis_status,
            "min_confidence": min_confidence,
            "include_unanalyzed": include_unanalyzed,
            "min_abs_impact": min_abs_impact,
            "include_neutral": include_neutral,
            "horizon": horizon,
            "mechanism": mechanism,
            "multi_source_only": multi_source_only,
        }
        query_hash = self._query_hash(filters)
        last_key: list[Any] | None = None
        if decoded_cursor is not None:
            if decoded_cursor["q"] != query_hash:
                raise InvalidCursorError()
            read_cutoff = _as_utc(decoded_cursor["cutoff"])
            last_key = decoded_cursor["last"]
        else:
            read_cutoff = _now()
        assert read_cutoff is not None
        cutoff = as_of - timedelta(hours=window_hours)
        normalized_ticker = ticker.strip().upper() if ticker else None
        if normalized_ticker and not TICKER_PATTERN.fullmatch(normalized_ticker):
            raise ValueError("invalid ticker")
        with self._read() as connection:
            items = self._visible_items(
                connection,
                as_of=as_of,
                read_cutoff=read_cutoff,
                window_start=cutoff,
                source=source,
                ticker=normalized_ticker,
            )
            content_sources: dict[str, set[str]] = {}
            for candidate in items:
                content_sources.setdefault(candidate["content_hash"], set()).add(candidate["source"])
            filtered: list[dict[str, Any]] = []
            for item in items:
                event_time = _as_utc(item.get("published_at") or item["fetched_at"])
                if event_time is None or event_time < cutoff:
                    continue
                analysis = item.get("analysis")
                impacts = (analysis or {}).get("affected_stocks") or []
                if normalized_ticker:
                    related = normalized_ticker in item["source_tickers"] or any(
                        impact.get("ticker") == normalized_ticker for impact in impacts
                    )
                    if not related:
                        continue
                    item = dict(item)
                    item["ticker_impacts"] = [
                        impact for impact in impacts if impact.get("ticker") == normalized_ticker
                    ]
                if source and item["source"] != source:
                    continue
                if classification and item["classification"] != classification:
                    continue
                if not include_neutral and item["classification"] == "neutral":
                    continue
                if analysis_status and item["analysis_status"] != analysis_status:
                    continue
                if min_confidence > 0 and (
                    item["confidence"] is None or item["confidence"] < min_confidence
                ):
                    continue
                if min_confidence == 0 and not include_unanalyzed and analysis is None:
                    continue
                if min_abs_impact is not None and (
                    item["impact_score"] is None or abs(item["impact_score"]) < min_abs_impact
                ):
                    continue
                if horizon and not any(impact.get("horizon") == horizon for impact in impacts):
                    continue
                if mechanism and not any(impact.get("mechanism") == mechanism for impact in impacts):
                    continue
                if multi_source_only and len(content_sources.get(item["content_hash"], set())) < 2:
                    continue
                filtered.append(item)
            filtered.sort(key=lambda item: (self._item_time(item), item["news_id"]), reverse=True)
            summary_items = list(filtered)
            six_hours_ago = as_of - timedelta(hours=6)
            day_ago = as_of - timedelta(hours=24)
            analyzed_items = [item for item in summary_items if item.get("analysis") is not None]
            summary = {
                "news_6h": sum(
                    (_as_utc(item.get("published_at") or item["fetched_at"]) or as_of) >= six_hours_ago
                    for item in summary_items
                ),
                "analyzed_24h": sum(
                    (_as_utc(item.get("available_at")) or datetime.min.replace(tzinfo=as_of.tzinfo)) >= day_ago
                    for item in analyzed_items
                ),
                "bullish": sum(item.get("classification") == "bullish" for item in analyzed_items),
                "bearish": sum(item.get("classification") == "bearish" for item in analyzed_items),
                "pending": sum(
                    item.get("analysis_status")
                    in {"not_requested", "pending", "queued", "in_progress"}
                    for item in summary_items
                ),
                "high_impact_macro": self._high_impact_macro_count(
                    connection, as_of=as_of, read_cutoff=read_cutoff
                ),
            }
            stock_groups: dict[str, dict[str, Any]] = {}
            seen_impacts: set[tuple[str, str]] = set()
            for item in summary_items:
                for impact in (item.get("analysis") or {}).get("affected_stocks") or []:
                    ticker_key = impact.get("ticker")
                    dedupe_key = (item["content_hash"], str(ticker_key))
                    if not ticker_key or dedupe_key in seen_impacts:
                        continue
                    seen_impacts.add(dedupe_key)
                    group = stock_groups.setdefault(
                        ticker_key,
                        {
                            "ticker": ticker_key,
                            "weighted_impact_total": 0.0,
                            "confidence_weight": 0.0,
                            "positive_count": 0,
                            "negative_count": 0,
                            "sources": set(),
                            "latest_catalyst_at": None,
                            "max_confidence": 0,
                            "catalyst_count": 0,
                        },
                    )
                    confidence = int(impact.get("confidence") or 0)
                    score = int(impact.get("impact_score") or 0)
                    weight = confidence / 100.0
                    group["weighted_impact_total"] += score * weight
                    group["confidence_weight"] += weight
                    group["positive_count"] += int(score > 0)
                    group["negative_count"] += int(score < 0)
                    group["sources"].add(item["source"])
                    event_at = self._item_time(item)
                    if not group["latest_catalyst_at"] or event_at > group["latest_catalyst_at"]:
                        group["latest_catalyst_at"] = event_at
                    group["max_confidence"] = max(group["max_confidence"], confidence)
                    group["catalyst_count"] += 1
            stock_impacts: list[dict[str, Any]] = []
            for group in stock_groups.values():
                weight = group.pop("confidence_weight")
                total = group.pop("weighted_impact_total")
                sources = group.pop("sources")
                group["net_impact"] = round(total / weight, 4) if weight else None
                group["source_diversity"] = len(sources)
                group["display_sort_only"] = True
                stock_impacts.append(group)
            stock_impacts.sort(
                key=lambda item: (abs(item["net_impact"] or 0), item["latest_catalyst_at"] or "", item["ticker"]),
                reverse=True,
            )
            if last_key is not None:
                filtered = [
                    item
                    for item in filtered
                    if [self._item_time(item), item["news_id"]] < last_key
                ]
            page = filtered[: limit + 1]
            has_more = len(page) > limit
            page = page[:limit]
            next_cursor = None
            if has_more and page:
                next_cursor = self._encode_cursor(
                    {
                        "v": 1,
                        "q": query_hash,
                        "as_of": _iso(as_of),
                        "cutoff": _iso(read_cutoff),
                        "last": [self._item_time(page[-1]), page[-1]["news_id"]],
                    }
                )
            state = connection.execute(
                "SELECT data_through,current_snapshot_id FROM catalyst_sync_state WHERE stream='feed'"
            ).fetchone()
            return {
                "as_of": _iso(as_of),
                "read_cutoff_at": _iso(read_cutoff),
                "data_through": state["data_through"] if state else None,
                "snapshot_id": state["current_snapshot_id"] if state else None,
                "items": page,
                "summary": summary,
                "stock_impacts": stock_impacts[:100],
                "next_cursor": next_cursor,
                "has_more": has_more,
            }

    @staticmethod
    def _high_impact_macro_count(
        connection: sqlite3.Connection,
        *,
        as_of: datetime,
        read_cutoff: datetime,
    ) -> int | None:
        calendar_state = connection.execute(
            "SELECT last_success_at FROM catalyst_sync_state WHERE stream='calendar'"
        ).fetchone()
        if calendar_state is None or not calendar_state["last_success_at"]:
            return None
        row = connection.execute(
            """
            WITH visible AS (
                SELECT event_id,impact,ROW_NUMBER() OVER(
                    PARTITION BY event_id ORDER BY available_at DESC,cached_at DESC,revision_id DESC
                ) AS row_number
                FROM catalyst_calendar_event_revisions
                WHERE scheduled_at>=? AND scheduled_at<=? AND available_at<=? AND cached_at<=?
            )
            SELECT count(*) FROM visible WHERE row_number=1 AND lower(impact)='high'
            """,
            (
                _iso(as_of),
                _iso(as_of + timedelta(hours=24)),
                _iso(as_of),
                _iso(read_cutoff),
            ),
        ).fetchone()
        return int(row[0]) if row else 0

    def get_news(self, news_id: int, *, as_of: datetime) -> dict[str, Any] | None:
        read_cutoff = _now()
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT * FROM catalyst_item_revisions
                WHERE news_id=? AND fetched_at<=? AND (published_at IS NULL OR published_at<=?)
                    AND updated_at<=? AND cached_at<=?
                ORDER BY change_sequence DESC,updated_at DESC
                LIMIT 1
                """,
                (news_id, _iso(as_of), _iso(as_of), _iso(as_of), _iso(read_cutoff)),
            ).fetchone()
            if row is None:
                return None
            return self._hydrate_visible_item(
                connection,
                row,
                as_of=_iso(as_of) or "",
                read_cutoff=_iso(read_cutoff) or "",
            )

    def latest_job_for_news(
        self,
        news_id: int,
        *,
        content_hash: str,
        change_sequence: int,
        contract_schema_version: str,
        model: str,
        reasoning: str,
        as_of: datetime,
    ) -> dict[str, Any] | None:
        cutoff = _iso(as_of)
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT * FROM catalyst_analysis_jobs
                WHERE news_id=? AND content_hash=? AND change_sequence=?
                    AND contract_schema_version=? AND model=? AND reasoning=?
                    AND created_at<=? AND updated_at<=?
                    AND (submitted_at IS NULL OR submitted_at<=?)
                    AND (completed_at IS NULL OR completed_at<=?)
                    AND (cancel_requested_at IS NULL OR cancel_requested_at<=?)
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    news_id,
                    content_hash,
                    change_sequence,
                    contract_schema_version,
                    model,
                    reasoning,
                    cutoff,
                    cutoff,
                    cutoff,
                    cutoff,
                    cutoff,
                ),
            ).fetchone()
            return self._public_job(row) if row else None

    def ticker_feed(
        self,
        ticker: str,
        *,
        as_of: datetime,
        window_hours: int = 72,
        limit: int = 20,
        cursor: str | None = None,
        min_confidence: int | None = 0,
        include_unanalyzed: bool = True,
        include_neutral: bool = False,
    ) -> dict[str, Any]:
        result = self.list_feed(
            as_of=as_of,
            window_hours=window_hours,
            limit=limit,
            cursor=cursor,
            ticker=ticker,
            min_confidence=min_confidence,
            include_unanalyzed=include_unanalyzed,
            include_neutral=include_neutral,
        )
        result["ticker"] = ticker.strip().upper()
        result["status"] = "active" if result["items"] else "empty"
        return result

    def batch_tickers(
        self,
        tickers: Sequence[str],
        *,
        as_of: datetime,
        window_hours: int,
        limit: int,
        min_confidence: int | None = 0,
        include_unanalyzed: bool = True,
        include_neutral: bool,
    ) -> dict[str, Any]:
        if not 1 <= len(tickers) <= 50:
            raise ValueError("batch must contain between 1 and 50 tickers")
        min_confidence = 0 if min_confidence is None else min_confidence
        if not 0 <= min_confidence <= 100:
            raise ValueError("min_confidence must be between 0 and 100")
        normalized: list[str] = []
        for raw_ticker in tickers:
            ticker = raw_ticker.strip().upper()
            if not TICKER_PATTERN.fullmatch(ticker):
                raise ValueError("invalid ticker")
            if ticker not in normalized:
                normalized.append(ticker)
        requested = set(normalized)
        read_cutoff = _now()
        window_start = as_of - timedelta(hours=window_hours)
        grouped: dict[str, list[dict[str, Any]]] = {ticker: [] for ticker in normalized}
        with self._read() as connection:
            items = self._visible_items(
                connection,
                as_of=as_of,
                read_cutoff=read_cutoff,
                window_start=window_start,
            )
            for item in items:
                if min_confidence > 0 and (
                    item["confidence"] is None or item["confidence"] < min_confidence
                ):
                    continue
                if min_confidence == 0 and not include_unanalyzed and item.get("analysis") is None:
                    continue
                if not include_neutral and item["classification"] == "neutral":
                    continue
                impacts = (item.get("analysis") or {}).get("affected_stocks") or []
                related = requested.intersection(item["source_tickers"])
                related.update(
                    impact["ticker"]
                    for impact in impacts
                    if impact.get("ticker") in requested
                )
                for ticker in related:
                    ticker_item = dict(item)
                    ticker_item["ticker_impacts"] = [
                        impact for impact in impacts if impact.get("ticker") == ticker
                    ]
                    grouped[ticker].append(ticker_item)
            state = connection.execute(
                "SELECT data_through,current_snapshot_id FROM catalyst_sync_state WHERE stream='feed'"
            ).fetchone()
        results: dict[str, Any] = {}
        for ticker in normalized:
            ticker_items = grouped[ticker]
            ticker_items.sort(
                key=lambda item: (self._item_time(item), item["news_id"]), reverse=True
            )
            has_more = len(ticker_items) > limit
            results[ticker] = {
                "ticker": ticker,
                "status": "active" if ticker_items else "empty",
                "as_of": _iso(as_of),
                "data_through": state["data_through"] if state else None,
                "snapshot_id": state["current_snapshot_id"] if state else None,
                "items": ticker_items[:limit],
                "next_cursor": None,
                "has_more": has_more,
            }
        return {
            "as_of": _iso(as_of),
            "read_cutoff_at": _iso(read_cutoff),
            "data_through": state["data_through"] if state else None,
            "snapshot_id": state["current_snapshot_id"] if state else None,
            "results": results,
        }

    def list_calendar(
        self,
        *,
        date_from: datetime,
        date_to: datetime,
        as_of: datetime,
        currencies: Sequence[str] | None = None,
        min_impact: str | None = None,
    ) -> dict[str, Any]:
        read_cutoff = _now()
        allowed = {value.upper() for value in currencies or []}
        rank = {"low": 1, "medium": 2, "high": 3}
        with self._read() as connection:
            rows = connection.execute(
                """
                WITH visible AS (
                    SELECT *,ROW_NUMBER() OVER(
                        PARTITION BY event_id ORDER BY available_at DESC,cached_at DESC,revision_id DESC
                    ) AS row_number
                    FROM catalyst_calendar_event_revisions
                    WHERE scheduled_at>=? AND scheduled_at<=? AND available_at<=? AND cached_at<=?
                )
                SELECT * FROM visible WHERE row_number=1 ORDER BY scheduled_at,event_id
                """,
                (_iso(date_from), _iso(date_to), _iso(as_of), _iso(read_cutoff)),
            ).fetchall()
            items = []
            for row in rows:
                if allowed and row["currency"].upper() not in allowed:
                    continue
                if min_impact and rank.get(row["impact"].lower(), 0) < rank.get(min_impact.lower(), 0):
                    continue
                items.append(_loads(row["raw_json"], {}))
            state = connection.execute(
                "SELECT data_through,current_snapshot_id FROM catalyst_sync_state WHERE stream='calendar'"
            ).fetchone()
            return {
                "as_of": _iso(as_of),
                "data_through": state["data_through"] if state else None,
                "snapshot_id": state["current_snapshot_id"] if state else None,
                "items": items,
            }

    def status_snapshot(
        self,
        *,
        stale_ttl_seconds: int,
        feed_interval_seconds: int,
        action_enabled: bool,
        model: str,
        reasoning: str,
        schema_version: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or _now()
        with self._read() as connection:
            states = {
                row["stream"]: dict(row)
                for row in connection.execute("SELECT * FROM catalyst_sync_state").fetchall()
            }
            feed = states.get("feed", {})
            last_success = _as_utc(feed.get("last_success_at"))
            age_seconds = (
                max(0.0, (observed - last_success).total_seconds()) if last_success else None
            )
            if feed.get("resync_required") and feed.get("current_snapshot_id"):
                # An expired remote watermark does not invalidate the last
                # atomically published snapshot. Keep it readable regardless
                # of its age until a replacement generation is complete.
                public_status = "stale"
            elif last_success is None:
                public_status = "unavailable"
            elif age_seconds is not None and age_seconds > stale_ttl_seconds:
                public_status = "unavailable"
            elif feed.get("last_error_code") or (
                age_seconds is not None and age_seconds > feed_interval_seconds * 2
            ):
                public_status = "stale"
            elif any(
                state.get("last_error_code")
                for stream, state in states.items()
                if stream != "feed"
            ):
                public_status = "degraded"
            else:
                public_status = "active"
            source_rows = connection.execute(
                "SELECT source,raw_json FROM catalyst_source_health ORDER BY source"
            ).fetchall()
            sources = []
            for row in source_rows:
                source = _loads(row["raw_json"], {})
                source["source"] = row["source"]
                sources.append(source)
            remote_health_status = states.get("health", {}).get("remote_status")
            source_statuses = {str(source.get("status") or "") for source in sources}
            worker = connection.execute(
                """
                SELECT worker_id,status,heartbeat_at,details_json
                FROM catalyst_worker_status
                WHERE worker_id NOT LIKE ?
                ORDER BY heartbeat_at DESC LIMIT 1
                """,
                (f"{FOCUS_PRODUCER_WORKER_PREFIX}%",),
            ).fetchone()
            runtime = connection.execute(
                "SELECT * FROM catalyst_remote_runtime WHERE singleton=1"
            ).fetchone()
            warnings = sorted(
                {
                    state["last_error_code"]
                    for state in states.values()
                    if state.get("last_error_code")
                }
            )
            runtime_drift = False
            if runtime is None:
                warnings.append("remote_runtime_unknown")
                remote_model = None
                remote_reasoning = None
                execution_mode = None
                remote_trigger_enabled = False
                runtime_drift = True
            else:
                remote_model = runtime["model"]
                remote_reasoning = runtime["reasoning"]
                execution_mode = runtime["execution_mode"]
                remote_trigger_enabled = bool(runtime["analysis_trigger_enabled"])
                warnings.extend(_loads(runtime["warnings_json"], []))
                if remote_model != model:
                    warnings.append("remote_model_mismatch")
                    runtime_drift = True
                if remote_reasoning != reasoning:
                    warnings.append("remote_reasoning_mismatch")
                    runtime_drift = True
                if execution_mode not in {"background", "worker_sync"}:
                    warnings.append("remote_execution_mode_unsupported")
                    runtime_drift = True
            if remote_health_status == "unavailable":
                warnings.append("remote_health_unavailable")
                if public_status != "unavailable":
                    public_status = "stale"
            elif remote_health_status not in {None, "ok", "active"}:
                warnings.append("remote_health_degraded")
                if public_status == "active":
                    public_status = "degraded"
            if "unavailable" in source_statuses:
                warnings.append("source_unavailable")
                if public_status == "active":
                    public_status = "degraded"
            if "degraded" in source_statuses:
                warnings.append("source_degraded")
                if public_status == "active":
                    public_status = "degraded"
            warnings = sorted(set(warnings))
            if runtime_drift and public_status in {"active", "empty"}:
                public_status = "degraded"
            return {
                "enabled": True,
                "status": public_status,
                "as_of": _iso(observed),
                "data_through": feed.get("data_through"),
                "last_sync_at": feed.get("last_success_at"),
                "last_attempt_at": feed.get("last_attempt_at"),
                "stale_age_seconds": age_seconds,
                "remote_status": remote_health_status,
                "analysis_trigger_enabled": (
                    action_enabled and remote_trigger_enabled and not runtime_drift
                ),
                "model": remote_model,
                "reasoning": remote_reasoning,
                "execution_mode": execution_mode,
                "expected_model": model,
                "expected_reasoning": reasoning,
                "schema_version": schema_version,
                "snapshot_id": feed.get("current_snapshot_id"),
                "resync_required": bool(feed.get("resync_required")),
                "resync_generation": int(feed.get("resync_generation") or 0),
                "last_resync_at": feed.get("last_resync_at"),
                "sources": sources,
                "worker": (
                    {
                        "status": worker["status"],
                        "heartbeat_at": worker["heartbeat_at"],
                    }
                    if worker
                    else None
                ),
                "streams": {
                    stream: {
                        key: value
                        for key, value in state.items()
                        if key
                        in {
                            "last_success_at",
                            "data_through",
                            "consecutive_failures",
                            "next_attempt_at",
                            "circuit_open_until",
                            "last_error_code",
                            "remote_status",
                            "resync_required",
                            "resync_generation",
                            "last_resync_at",
                        }
                    }
                    for stream, state in states.items()
                },
                "warnings": warnings,
            }

    def enqueue_refresh(
        self,
        streams: Sequence[str] = ("health", "feed", "calendar"),
        *,
        now: datetime | None = None,
    ) -> str:
        normalized = sorted(set(streams))
        if not normalized or any(stream not in {"health", "feed", "calendar"} for stream in normalized):
            raise ValueError("refresh streams are invalid")
        request_id = uuid.uuid4().hex
        with self._write() as connection:
            connection.execute(
                "INSERT INTO catalyst_refresh_outbox(request_id,streams_json,status,requested_at) VALUES(?,?,\"pending\",?)",
                (request_id, _json(normalized), _iso(now or _now())),
            )
            connection.commit()
        return request_id

    def claim_refresh(
        self,
        *,
        now: datetime | None = None,
        recovery_after_seconds: int = 300,
    ) -> dict[str, Any] | None:
        observed = now or _now()
        timestamp = _iso(observed)
        stale_claimed_before = _iso(
            observed - timedelta(seconds=max(1, recovery_after_seconds))
        )
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            # A process may stop after claiming an order. Recover only an old
            # claim, then make it eligible at the current worker timestamp.
            connection.execute(
                """
                UPDATE catalyst_refresh_outbox
                SET status='pending',claimed_at=NULL,requested_at=?,error_code='claim_recovered'
                WHERE status='processing' AND claimed_at IS NOT NULL AND claimed_at<=?
                """,
                (timestamp, stale_claimed_before),
            )
            row = connection.execute(
                """
                SELECT * FROM catalyst_refresh_outbox
                WHERE status='pending' AND requested_at<=?
                ORDER BY requested_at LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            changed = connection.execute(
                "UPDATE catalyst_refresh_outbox SET status='processing',claimed_at=? WHERE request_id=? AND status='pending'",
                (timestamp, row["request_id"]),
            ).rowcount
            connection.commit()
            if not changed:
                return None
            return {"request_id": row["request_id"], "streams": _loads(row["streams_json"], [])}

    def complete_refresh(
        self,
        request_id: str,
        *,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                "UPDATE catalyst_refresh_outbox SET status=?,completed_at=?,error_code=? WHERE request_id=?",
                (
                    "failed" if error_code else "completed",
                    _iso(now or _now()),
                    error_code[:100] if error_code else None,
                    request_id,
                ),
            )
            connection.commit()

    def defer_refresh(
        self,
        request_id: str,
        *,
        not_before: datetime | None = None,
    ) -> None:
        """Return a claimed refresh to the queue without bypassing backoff."""

        with self._write() as connection:
            connection.execute(
                """
                UPDATE catalyst_refresh_outbox
                SET status='pending',claimed_at=NULL,error_code=NULL,
                    requested_at=COALESCE(?,requested_at)
                WHERE request_id=? AND status='processing'
                """,
                (_iso(not_before), request_id),
            )
            connection.commit()

    def enqueue_analysis(
        self,
        news_id: int,
        *,
        content_hash: str,
        change_sequence: int,
        contract_schema_version: str,
        force: bool,
        model: str,
        reasoning: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM catalyst_analysis_jobs
                WHERE news_id=? AND content_hash=? AND change_sequence=?
                    AND model=? AND reasoning=? AND contract_schema_version=?
                ORDER BY created_at DESC LIMIT 1
                """,
                (
                    news_id,
                    content_hash,
                    change_sequence,
                    model,
                    reasoning,
                    contract_schema_version,
                ),
            ).fetchone()
            if existing and not force:
                # Retrying a terminal remote execution is always an explicit
                # action. Returning the prior proxy for force=false prevents a
                # second local row from silently replaying a failed or
                # cancelled paid request.
                connection.commit()
                return self._public_job(existing)
            local_job_id = uuid.uuid4().hex
            connection.execute(
                """
                INSERT INTO catalyst_analysis_jobs(
                    local_job_id,news_id,content_hash,change_sequence,contract_schema_version,
                    status,force,model,reasoning,created_at,updated_at
                ) VALUES(?,?,?,?,?,\"pending\",?,?,?,?,?)
                """,
                (
                    local_job_id,
                    news_id,
                    content_hash,
                    change_sequence,
                    contract_schema_version,
                    int(force),
                    model,
                    reasoning,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?", (local_job_id,)
            ).fetchone()
            connection.commit()
            return self._public_job(row)

    @staticmethod
    def _public_job(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        data = dict(row)
        return {
            "job_id": data["local_job_id"],
            "news_id": data["news_id"],
            "status": data["status"],
            "model": data.get("actual_model") or data.get("model"),
            "reasoning": data.get("actual_reasoning") or data.get("reasoning"),
            "requested_model": data.get("model"),
            "requested_reasoning": data.get("reasoning"),
            "submitted_at": data.get("submitted_at"),
            "updated_at": data.get("updated_at"),
            "completed_at": data.get("completed_at"),
            "error_code": data.get("error_code"),
            "retry_after": data.get("retry_after_seconds"),
            "result": _loads(data.get("result_json"), None),
            "cancel_requested": bool(data.get("cancel_requested_at")),
        }

    def get_analysis_job(self, local_job_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            row = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?", (local_job_id,)
            ).fetchone()
            return self._public_job(row) if row else None

    def request_job_cancel(
        self, local_job_id: str, *, now: datetime | None = None
    ) -> dict[str, Any] | None:
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?", (local_job_id,)
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            if row["status"] not in {status.value for status in ACTIVE_JOB_STATUSES}:
                connection.commit()
                return self._public_job(row)
            if row["status"] == JobStatus.PENDING.value and row["remote_job_id"] is None:
                connection.execute(
                    """
                    UPDATE catalyst_analysis_jobs
                    SET status='cancelled',cancel_requested_at=?,completed_at=?,
                        next_attempt_at=NULL,lease_owner=NULL,lease_until=NULL,updated_at=?
                    WHERE local_job_id=?
                    """,
                    (timestamp, timestamp, timestamp, local_job_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE catalyst_analysis_jobs
                    SET cancel_requested_at=COALESCE(cancel_requested_at,?),
                        next_attempt_at=?,updated_at=?
                    WHERE local_job_id=?
                    """,
                    (timestamp, timestamp, timestamp, local_job_id),
                )
            updated = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?", (local_job_id,)
            ).fetchone()
            connection.commit()
            return self._public_job(updated)

    def due_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 25,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        observed = now or _now()
        lease_until = observed + timedelta(seconds=lease_seconds)
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM catalyst_analysis_jobs
                WHERE status IN ('pending','queued','in_progress')
                    AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                    AND (lease_until IS NULL OR lease_until<=? OR lease_owner=?)
                ORDER BY CASE WHEN cancel_requested_at IS NOT NULL THEN 0 ELSE 1 END,created_at
                LIMIT ?
                """,
                (_iso(observed), _iso(observed), worker_id, limit),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                changed = connection.execute(
                    """
                    UPDATE catalyst_analysis_jobs SET lease_owner=?,lease_until=?
                    WHERE local_job_id=? AND (lease_until IS NULL OR lease_until<=? OR lease_owner=?)
                    """,
                    (worker_id, _iso(lease_until), row["local_job_id"], _iso(observed), worker_id),
                ).rowcount
                if changed:
                    claimed.append(dict(row))
            connection.commit()
            return claimed

    def begin_remote_submission(
        self,
        local_job_id: str,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> str | None:
        """Atomically decide whether a claimed job may contact MacroLens.

        ``new`` means no outbound request has started and cancellation must be
        absent. ``recovery`` means a previous worker marked submission started
        but stopped before persisting the idempotent remote response. A recovery
        is allowed even with a cancellation request so the worker can recover
        the opaque remote id and cancel that already-uncertain submission.
        """

        observed = now or _now()
        timestamp = _iso(observed)
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?",
                (local_job_id,),
            ).fetchone()
            if (
                row is None
                or row["remote_job_id"] is not None
                or row["lease_owner"] != worker_id
                or _as_utc(row["lease_until"]) is None
                or _as_utc(row["lease_until"]) <= observed
            ):
                connection.commit()
                return None
            if row["status"] == JobStatus.PENDING.value:
                if row["cancel_requested_at"] is not None:
                    connection.commit()
                    return None
                changed = connection.execute(
                    """
                    UPDATE catalyst_analysis_jobs
                    SET status='in_progress',submitted_at=COALESCE(submitted_at,?),
                        updated_at=?
                    WHERE local_job_id=? AND lease_owner=?
                        AND status='pending' AND remote_job_id IS NULL
                        AND cancel_requested_at IS NULL AND lease_until>?
                    """,
                    (timestamp, timestamp, local_job_id, worker_id, timestamp),
                ).rowcount
                connection.commit()
                return "new" if changed == 1 else None
            if row["status"] == JobStatus.IN_PROGRESS.value and row["submitted_at"]:
                connection.commit()
                return "recovery"
            connection.commit()
            return None

    def apply_remote_job(
        self,
        local_job_id: str,
        remote: RemoteJobResponse,
        *,
        worker_id: str | None = None,
        next_attempt_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        observed = now or _now()
        timestamp = _iso(observed)
        result = remote.result.model_dump(mode="json") if remote.result else None
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM catalyst_analysis_jobs WHERE local_job_id=?",
                (local_job_id,),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise CatalystRepositoryError(
                    "analysis_job_not_found", "The local analysis job no longer exists"
                )
            if worker_id is not None and (
                current["lease_owner"] != worker_id
                or _as_utc(current["lease_until"]) is None
                or _as_utc(current["lease_until"]) <= observed
            ):
                connection.rollback()
                raise CatalystRepositoryError(
                    "analysis_job_lease_lost",
                    "The local analysis job lease changed before the remote result was saved",
                )
            owner = connection.execute(
                """
                SELECT * FROM catalyst_analysis_jobs
                WHERE remote_job_id=? AND local_job_id<>?
                """,
                (remote.job_id, local_job_id),
            ).fetchone()
            if owner is not None:
                terminal_values = {status.value for status in TERMINAL_JOB_STATUSES}
                same_input = all(
                    owner[key] == current[key]
                    for key in (
                        "news_id",
                        "content_hash",
                        "change_sequence",
                        "contract_schema_version",
                        "model",
                        "reasoning",
                    )
                )
                if (
                    not bool(current["force"])
                    or owner["status"] not in terminal_values
                    or not same_input
                ):
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "remote_job_collision",
                        "The remote analysis job is already bound to another local request",
                    )
                # MacroLens can safely return the same terminal job to an
                # explicit retry. Move the opaque mapping atomically so the
                # UNIQUE constraint cannot interrupt the worker.
                changed = connection.execute(
                    """
                    UPDATE catalyst_analysis_jobs SET remote_job_id=NULL
                    WHERE local_job_id=? AND remote_job_id=? AND status IN (
                        'completed','failed','cancelled','insufficient_context','budget_blocked'
                    )
                    """,
                    (owner["local_job_id"], remote.job_id),
                ).rowcount
                if changed != 1:
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "remote_job_collision",
                        "The prior remote analysis mapping changed concurrently",
                    )
            remote_status = remote.status.value
            cancelled_while_active = (
                bool(current["cancel_requested_at"])
                and remote.status in ACTIVE_JOB_STATUSES
            )
            stored_status = JobStatus.IN_PROGRESS.value if cancelled_while_active else remote_status
            stored_next_attempt = timestamp if cancelled_while_active else _iso(next_attempt_at)
            connection.execute(
                """
                UPDATE catalyst_analysis_jobs
                SET remote_job_id=?,status=?,actual_model=?,actual_reasoning=?,remote_input_hash=?,
                    submitted_at=COALESCE(?,submitted_at),completed_at=?,
                    error_code=?,retry_after_seconds=?,next_attempt_at=?,result_json=?,
                    updated_at=?,lease_owner=NULL,lease_until=NULL
                WHERE local_job_id=?
                """,
                (
                    remote.job_id,
                    stored_status,
                    remote.model,
                    remote.reasoning,
                    remote.input_hash,
                    _iso(remote.submitted_at),
                    _iso(remote.completed_at),
                    remote.error_code,
                    remote.retry_after,
                    stored_next_attempt,
                    _json(result) if result is not None else None,
                    timestamp,
                    local_job_id,
                ),
            )
            connection.commit()

    def fail_local_job(
        self,
        local_job_id: str,
        error_code: str,
        *,
        retry_after_seconds: int | None = None,
        terminal: bool = False,
        now: datetime | None = None,
    ) -> None:
        observed = now or _now()
        next_attempt = (
            observed + timedelta(seconds=retry_after_seconds)
            if retry_after_seconds and not terminal
            else None
        )
        with self._write() as connection:
            connection.execute(
                """
                UPDATE catalyst_analysis_jobs
                SET status=CASE WHEN ? THEN 'failed' ELSE status END,error_code=?,
                    retry_after_seconds=?,next_attempt_at=?,updated_at=?,
                    lease_owner=NULL,lease_until=NULL
                WHERE local_job_id=?
                """,
                (
                    int(terminal),
                    error_code[:100],
                    retry_after_seconds,
                    _iso(next_attempt),
                    _iso(observed),
                    local_job_id,
                ),
            )
            connection.commit()

    @staticmethod
    def _public_market_focus_cycle(
        row: sqlite3.Row | dict[str, Any],
    ) -> dict[str, Any]:
        data = dict(row)
        raw = _loads(data.get("raw_json") or data.get("result_json"), {})
        public_id = data.get("public_cycle_id") or data.get("local_cycle_id")
        if raw:
            raw["cycle_id"] = public_id
            # Remote identifiers stay inside the worker cache.  The same-origin
            # API exposes the local parent id when this row represents a retry.
            raw.pop("retry_of_cycle_id", None)
            if data.get("retry_of_local_cycle_id"):
                raw["retry_of_cycle_id"] = data["retry_of_local_cycle_id"]
            if isinstance(raw.get("result"), dict):
                raw["result"] = {**raw["result"], "cycle_id": public_id}
            return raw
        return {
            "cycle_id": public_id,
            "status": data["status"],
            "prepared_revision": data.get("expected_prepared_revision", 0),
            "retry_of_cycle_id": data.get("retry_of_local_cycle_id"),
            "execution_number": data.get("execution_number", 1),
            "model": data.get("model"),
            "reasoning_effort": data.get("reasoning"),
            "error_code": data.get("error_code"),
            "next_attempt_at": data.get("next_attempt_at"),
            "cancel_requested_at": data.get("cancel_requested_at"),
            "created_at": data.get("created_at"),
            "updated_at": data.get("updated_at"),
            "result": None,
        }

    def publish_market_focus_snapshot(
        self,
        status: HotspotPreparationStatus,
        items: Sequence[HotspotPreparationItem],
        cycle: RemoteMarketFocusCycle | None,
        *,
        worker_id: str | None = None,
        fencing_token: int | None = None,
        now: datetime | None = None,
    ) -> None:
        """Atomically replace the visible remote focus snapshot.

        Remote I/O and validation finish before this transaction begins.  A
        failed pull therefore leaves the prior snapshot readable and stale.
        """

        observed = now or _now()
        timestamp = _iso(observed)
        snapshot_id = f"mfs_{uuid.uuid4().hex}"
        status_json = _json(status.model_dump(mode="json"))
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_worker_fence(
                connection,
                worker_id=worker_id,
                fencing_token=fencing_token,
                now=observed,
            )
            connection.execute(
                """
                INSERT INTO catalyst_hotspot_preparation_sets(
                    snapshot_id,prepared_revision,status_json,item_json,cached_at
                ) VALUES(?,0,?,NULL,?)
                """,
                (snapshot_id, status_json, timestamp),
            )
            seen_revisions: set[int] = set()
            for item in items:
                if item.prepared_revision in seen_revisions:
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "duplicate_prepared_revision",
                        "MacroLens returned a duplicate hotspot revision",
                    )
                if item.prepared_revision > status.prepared_revision:
                    connection.rollback()
                    raise CatalystRepositoryError(
                        "prepared_revision_mismatch",
                        "MacroLens returned a hotspot beyond the published revision",
                    )
                seen_revisions.add(item.prepared_revision)
                connection.execute(
                    """
                    INSERT INTO catalyst_hotspot_preparation_sets(
                        snapshot_id,prepared_revision,status_json,item_json,cached_at
                    ) VALUES(?,?,?,?,?)
                    """,
                    (
                        snapshot_id,
                        item.prepared_revision,
                        status_json,
                        _json(item.model_dump(mode="json")),
                        timestamp,
                    ),
                )
            if cycle is not None:
                existing = connection.execute(
                    "SELECT public_cycle_id FROM catalyst_market_focus_cycles WHERE remote_cycle_id=?",
                    (cycle.cycle_id,),
                ).fetchone()
                job = connection.execute(
                    "SELECT local_cycle_id FROM catalyst_market_focus_jobs WHERE remote_cycle_id=?",
                    (cycle.cycle_id,),
                ).fetchone()
                public_cycle_id = (
                    str(existing["public_cycle_id"])
                    if existing is not None
                    else str(job["local_cycle_id"])
                    if job is not None
                    else f"mfc_{uuid.uuid4().hex}"
                )
                connection.execute(
                    """
                    INSERT INTO catalyst_market_focus_cycles(
                        remote_cycle_id,public_cycle_id,snapshot_id,prepared_revision,
                        status,raw_json,cached_at
                    ) VALUES(?,?,?,?,?,?,?)
                    ON CONFLICT(remote_cycle_id) DO UPDATE SET
                        snapshot_id=excluded.snapshot_id,
                        prepared_revision=excluded.prepared_revision,
                        status=excluded.status,
                        raw_json=excluded.raw_json,
                        cached_at=excluded.cached_at
                    """,
                    (
                        cycle.cycle_id,
                        public_cycle_id,
                        snapshot_id,
                        cycle.prepared_revision,
                        cycle.status,
                        _json(cycle.model_dump(mode="json")),
                        timestamp,
                    ),
                )
            connection.execute(
                """
                UPDATE catalyst_sync_state SET
                    last_attempt_at=?,last_success_at=?,data_through=?,
                    consecutive_failures=0,next_attempt_at=NULL,circuit_open_until=NULL,
                    last_error_code=NULL,remote_status='ok',current_snapshot_id=?
                WHERE stream='market_focus'
                """,
                (timestamp, timestamp, _iso(status.data_through), snapshot_id),
            )
            connection.execute(
                "DELETE FROM catalyst_hotspot_preparation_sets WHERE snapshot_id<>?",
                (snapshot_id,),
            )
            connection.commit()

    def market_focus_snapshot(
        self,
        *,
        stale_ttl_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or _now()
        with self._read() as connection:
            state = connection.execute(
                "SELECT * FROM catalyst_sync_state WHERE stream='market_focus'"
            ).fetchone()
            snapshot_id = state["current_snapshot_id"] if state else None
            if not snapshot_id:
                return {
                    "status": "unavailable",
                    "as_of": _iso(observed),
                    "hotspot_status": None,
                    "items": [],
                    "cycle": None,
                    "warnings": ["market_focus_snapshot_unavailable"],
                }
            status_row = connection.execute(
                """
                SELECT status_json,cached_at FROM catalyst_hotspot_preparation_sets
                WHERE snapshot_id=? AND prepared_revision=0
                """,
                (snapshot_id,),
            ).fetchone()
            if status_row is None:
                raise CatalystRepositoryError(
                    "cache_snapshot_incomplete", "Market focus snapshot is incomplete"
                )
            item_rows = connection.execute(
                """
                SELECT item_json FROM catalyst_hotspot_preparation_sets
                WHERE snapshot_id=? AND prepared_revision>0
                ORDER BY prepared_revision DESC
                """,
                (snapshot_id,),
            ).fetchall()
            cycle_row = connection.execute(
                """
                SELECT * FROM catalyst_market_focus_cycles
                WHERE snapshot_id=? ORDER BY cached_at DESC LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            job_row = connection.execute(
                """
                SELECT * FROM catalyst_market_focus_jobs
                ORDER BY updated_at DESC LIMIT 1
                """
            ).fetchone()
            last_success = _as_utc(state["last_success_at"])
            stale = last_success is None or (
                observed - last_success
            ).total_seconds() > stale_ttl_seconds
            failures = int(state["consecutive_failures"] or 0)
            public_status = "stale" if stale else "degraded" if failures else "active"
            warnings = []
            if stale:
                warnings.append("market_focus_snapshot_stale")
            if state["last_error_code"]:
                warnings.append(str(state["last_error_code"]))
            hotspot_status = _loads(status_row["status_json"], {})
            remote_to_public = {
                str(row["remote_cycle_id"]): str(row["public_cycle_id"])
                for row in connection.execute(
                    "SELECT remote_cycle_id,public_cycle_id FROM catalyst_market_focus_cycles"
                ).fetchall()
            }
            active_id = hotspot_status.get("active_cycle_id")
            hotspot_status["active_cycle_id"] = remote_to_public.get(active_id)
            items = []
            for row in item_rows:
                item = _loads(row["item_json"], {})
                for field in ("leased_cycle_id", "consumed_cycle_id"):
                    item[field] = remote_to_public.get(item.get(field))
                items.append(item)
            visible_cycle = cycle_row
            if job_row is not None and (
                cycle_row is None or job_row["updated_at"] >= cycle_row["cached_at"]
            ):
                visible_cycle = job_row
            return {
                "status": public_status,
                "as_of": _iso(observed),
                "last_sync_at": state["last_success_at"],
                "data_through": state["data_through"],
                "hotspot_status": hotspot_status,
                "items": items,
                "cycle": (
                    self._public_market_focus_cycle(visible_cycle)
                    if visible_cycle
                    else None
                ),
                "warnings": warnings,
            }

    def enqueue_market_focus_cycle(
        self,
        expected_prepared_revision: int,
        *,
        last_consumed_revision: int,
        model: str,
        reasoning: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = _iso(now or _now())
        request_key = (
            f"batch:{expected_prepared_revision}:{last_consumed_revision}"
        )
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM catalyst_market_focus_jobs
                WHERE request_key=?
                """,
                (request_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._public_market_focus_cycle(existing)
            local_cycle_id = f"mfc_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO catalyst_market_focus_jobs(
                    local_cycle_id,request_key,expected_prepared_revision,
                    last_consumed_revision_at_request,execution_number,
                    status,model,reasoning,created_at,updated_at
                ) VALUES(?,?,?,?,1,'pending',?,?,?,?)
                """,
                (
                    local_cycle_id,
                    request_key,
                    expected_prepared_revision,
                    last_consumed_revision,
                    model,
                    reasoning,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (local_cycle_id,),
            ).fetchone()
            connection.commit()
            return self._public_market_focus_cycle(row)

    def market_focus_job_for_batch(
        self,
        expected_prepared_revision: int,
        last_consumed_revision: int,
    ) -> dict[str, Any] | None:
        request_key = (
            f"batch:{expected_prepared_revision}:{last_consumed_revision}"
        )
        with self._read() as connection:
            row = connection.execute(
                """
                SELECT * FROM catalyst_market_focus_jobs
                WHERE request_key=?
                """,
                (request_key,),
            ).fetchone()
            return self._public_market_focus_cycle(row) if row else None

    def enqueue_market_focus_retry(
        self,
        retry_of_local_cycle_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Queue one append-only retry of a remote immutable cycle snapshot."""

        timestamp = _iso(now or _now())
        request_key = f"retry:{retry_of_local_cycle_id}"
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE request_key=?",
                (request_key,),
            ).fetchone()
            if existing is not None:
                connection.commit()
                return self._public_market_focus_cycle(existing)

            parent_job = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (retry_of_local_cycle_id,),
            ).fetchone()
            parent_cycle = None
            if parent_job is None:
                parent_cycle = connection.execute(
                    "SELECT * FROM catalyst_market_focus_cycles WHERE public_cycle_id=?",
                    (retry_of_local_cycle_id,),
                ).fetchone()
            if parent_job is None and parent_cycle is None:
                connection.rollback()
                raise CatalystRepositoryError(
                    "market_focus_cycle_not_found",
                    "The market focus retry parent was not found",
                )

            parent = dict(parent_job or parent_cycle)
            raw = _loads(parent.get("result_json") or parent.get("raw_json"), {})
            parent_status = str(raw.get("status") or parent.get("status") or "")
            if str(raw.get("error_code") or parent.get("error_code") or "") == (
                "submission_outcome_unknown"
            ):
                connection.rollback()
                raise CatalystRepositoryError(
                    "market_focus_retry_outcome_unknown",
                    "The remote submission outcome must be reconciled before retrying",
                )
            if parent_status not in {"failed", "cancelled", "incomplete_output"}:
                connection.rollback()
                raise CatalystRepositoryError(
                    "market_focus_cycle_not_retryable",
                    "The market focus cycle is not in a retryable terminal state",
                )
            remote_parent_id = parent.get("remote_cycle_id")
            if not remote_parent_id:
                connection.rollback()
                raise CatalystRepositoryError(
                    "market_focus_retry_snapshot_unavailable",
                    "The immutable remote cycle snapshot is unavailable",
                )

            expected_revision = int(
                raw.get("prepared_revision")
                if raw.get("prepared_revision") is not None
                else parent.get("expected_prepared_revision")
                or parent.get("prepared_revision")
                or 0
            )
            consumed_revision = int(
                parent.get("last_consumed_revision_at_request")
                or raw.get("last_consumed_revision_at_start")
                or 0
            )
            execution_number = int(
                raw.get("execution_number")
                or parent.get("execution_number")
                or 1
            ) + 1
            model = str(raw.get("model") or parent.get("model") or "")
            reasoning = str(
                raw.get("reasoning_effort") or parent.get("reasoning") or ""
            )
            local_cycle_id = f"mfc_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO catalyst_market_focus_jobs(
                    local_cycle_id,request_key,expected_prepared_revision,
                    last_consumed_revision_at_request,retry_of_local_cycle_id,
                    retry_remote_cycle_id,execution_number,status,model,reasoning,
                    created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,'pending',?,?,?,?)
                """,
                (
                    local_cycle_id,
                    request_key,
                    expected_revision,
                    consumed_revision,
                    retry_of_local_cycle_id,
                    str(remote_parent_id),
                    execution_number,
                    model,
                    reasoning,
                    timestamp,
                    timestamp,
                ),
            )
            row = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (local_cycle_id,),
            ).fetchone()
            connection.commit()
            return self._public_market_focus_cycle(row)

    def get_market_focus_cycle(self, public_cycle_id: str) -> dict[str, Any] | None:
        with self._read() as connection:
            job = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (public_cycle_id,),
            ).fetchone()
            if job is not None:
                return self._public_market_focus_cycle(job)
            cycle = connection.execute(
                "SELECT * FROM catalyst_market_focus_cycles WHERE public_cycle_id=?",
                (public_cycle_id,),
            ).fetchone()
            return self._public_market_focus_cycle(cycle) if cycle else None

    def request_market_focus_cancel(
        self,
        local_cycle_id: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        timestamp = _iso(now or _now())
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (local_cycle_id,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            if row["status"] not in {"pending", "queued", "in_progress"}:
                connection.commit()
                return self._public_market_focus_cycle(row)
            if row["remote_cycle_id"] is None:
                connection.execute(
                    """
                    UPDATE catalyst_market_focus_jobs SET
                        status='cancelled',cancel_requested_at=?,next_attempt_at=NULL,
                        lease_owner=NULL,lease_until=NULL,updated_at=?
                    WHERE local_cycle_id=?
                    """,
                    (timestamp, timestamp, local_cycle_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE catalyst_market_focus_jobs SET
                        cancel_requested_at=COALESCE(cancel_requested_at,?),
                        next_attempt_at=?,updated_at=?
                    WHERE local_cycle_id=?
                    """,
                    (timestamp, timestamp, timestamp, local_cycle_id),
                )
            updated = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (local_cycle_id,),
            ).fetchone()
            connection.commit()
            return self._public_market_focus_cycle(updated)

    def due_market_focus_jobs(
        self,
        worker_id: str,
        *,
        limit: int = 5,
        lease_seconds: int = 30,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        observed = now or _now()
        timestamp = _iso(observed)
        lease_until = _iso(observed + timedelta(seconds=lease_seconds))
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            rows = connection.execute(
                """
                SELECT * FROM catalyst_market_focus_jobs
                WHERE status IN ('pending','queued','in_progress')
                  AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                  AND (lease_until IS NULL OR lease_until<=? OR lease_owner=?)
                ORDER BY CASE WHEN cancel_requested_at IS NOT NULL THEN 0 ELSE 1 END,
                         created_at
                LIMIT ?
                """,
                (timestamp, timestamp, worker_id, limit),
            ).fetchall()
            claimed: list[dict[str, Any]] = []
            for row in rows:
                changed = connection.execute(
                    """
                    UPDATE catalyst_market_focus_jobs SET lease_owner=?,lease_until=?
                    WHERE local_cycle_id=?
                      AND (lease_until IS NULL OR lease_until<=? OR lease_owner=?)
                    """,
                    (worker_id, lease_until, row["local_cycle_id"], timestamp, worker_id),
                ).rowcount
                if changed:
                    claimed.append(dict(row))
            connection.commit()
            return claimed

    def begin_market_focus_submission(
        self,
        local_cycle_id: str,
        worker_id: str,
        *,
        now: datetime | None = None,
    ) -> bool:
        observed = now or _now()
        timestamp = _iso(observed)
        with self._write() as connection:
            changed = connection.execute(
                """
                UPDATE catalyst_market_focus_jobs SET status='in_progress',updated_at=?
                WHERE local_cycle_id=? AND lease_owner=?
                  AND (
                    (status='pending' AND cancel_requested_at IS NULL)
                    OR status='in_progress'
                  )
                  AND remote_cycle_id IS NULL
                  AND lease_until>?
                """,
                (timestamp, local_cycle_id, worker_id, timestamp),
            ).rowcount
            connection.commit()
            return changed == 1

    def apply_remote_market_focus_cycle(
        self,
        local_cycle_id: str,
        remote: RemoteMarketFocusCycle,
        *,
        worker_id: str,
        next_attempt_at: datetime | None = None,
        now: datetime | None = None,
    ) -> None:
        observed = now or _now()
        timestamp = _iso(observed)
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                "SELECT * FROM catalyst_market_focus_jobs WHERE local_cycle_id=?",
                (local_cycle_id,),
            ).fetchone()
            if current is None or current["lease_owner"] != worker_id:
                connection.rollback()
                raise CatalystRepositoryError(
                    "market_focus_job_lease_lost", "Market focus job lease was lost"
                )
            owner = connection.execute(
                """
                SELECT local_cycle_id FROM catalyst_market_focus_jobs
                WHERE remote_cycle_id=? AND local_cycle_id<>?
                """,
                (remote.cycle_id, local_cycle_id),
            ).fetchone()
            if owner is not None:
                connection.rollback()
                raise CatalystRepositoryError(
                    "remote_cycle_collision", "Remote cycle is bound to another local request"
                )
            active = remote.status in {"pending", "queued", "in_progress"}
            cancelling = bool(current["cancel_requested_at"]) and active
            stored_status = "in_progress" if cancelling else remote.status
            retry_at = timestamp if cancelling else _iso(next_attempt_at)
            raw = remote.model_dump(mode="json")
            connection.execute(
                """
                UPDATE catalyst_market_focus_jobs SET
                    remote_cycle_id=?,status=?,model=?,reasoning=?,error_code=?,
                    next_attempt_at=?,result_json=?,updated_at=?,
                    lease_owner=NULL,lease_until=NULL
                WHERE local_cycle_id=?
                """,
                (
                    remote.cycle_id,
                    stored_status,
                    remote.model,
                    remote.reasoning_effort,
                    remote.error_code,
                    retry_at,
                    _json(raw),
                    timestamp,
                    local_cycle_id,
                ),
            )
            connection.execute(
                """
                INSERT INTO catalyst_market_focus_cycles(
                    remote_cycle_id,public_cycle_id,snapshot_id,prepared_revision,
                    status,raw_json,cached_at
                ) VALUES(?,?,NULL,?,?,?,?)
                ON CONFLICT(remote_cycle_id) DO UPDATE SET
                    public_cycle_id=excluded.public_cycle_id,
                    prepared_revision=excluded.prepared_revision,
                    status=excluded.status,raw_json=excluded.raw_json,cached_at=excluded.cached_at
                """,
                (
                    remote.cycle_id,
                    local_cycle_id,
                    remote.prepared_revision,
                    remote.status,
                    _json(raw),
                    timestamp,
                ),
            )
            connection.commit()

    def fail_market_focus_job(
        self,
        local_cycle_id: str,
        error_code: str,
        *,
        retry_after_seconds: int | None = None,
        terminal: bool,
        now: datetime | None = None,
    ) -> None:
        observed = now or _now()
        terminal_status = (
            "budget_blocked"
            if error_code in {
                "budget_configuration_required",
                "daily_job_limit_reached",
                "daily_output_token_limit_reached",
            }
            else "failed"
        )
        next_attempt = (
            observed + timedelta(seconds=retry_after_seconds or 1)
            if not terminal
            else None
        )
        with self._write() as connection:
            connection.execute(
                """
                UPDATE catalyst_market_focus_jobs SET
                    status=CASE WHEN ? THEN ? ELSE status END,
                    error_code=?,retry_after_seconds=?,next_attempt_at=?,updated_at=?,
                    lease_owner=NULL,lease_until=NULL
                WHERE local_cycle_id=?
                """,
                (
                    int(terminal),
                    terminal_status,
                    error_code[:100],
                    retry_after_seconds,
                    _iso(next_attempt),
                    _iso(observed),
                    local_cycle_id,
                ),
            )
            connection.commit()

    def acquire_worker_lock(
        self,
        lock_name: str,
        owner_id: str,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> int | None:
        observed = now or _now()
        lease_until = observed + timedelta(seconds=lease_seconds)
        with self._write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM catalyst_worker_lock WHERE lock_name=?", (lock_name,)
            ).fetchone()
            if row is not None and row["owner_id"] != owner_id and _as_utc(row["lease_until"]) > observed:
                connection.commit()
                return None
            token = (row["fencing_token"] if row else 0) + 1
            connection.execute(
                """
                INSERT INTO catalyst_worker_lock(lock_name,owner_id,fencing_token,lease_until,updated_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(lock_name) DO UPDATE SET owner_id=excluded.owner_id,
                    fencing_token=excluded.fencing_token,lease_until=excluded.lease_until,
                    updated_at=excluded.updated_at
                """,
                (lock_name, owner_id, token, _iso(lease_until), _iso(observed)),
            )
            connection.commit()
            return token

    def renew_worker_lock(
        self,
        lock_name: str,
        owner_id: str,
        fencing_token: int,
        *,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        observed = now or _now()
        with self._write() as connection:
            changed = connection.execute(
                """
                UPDATE catalyst_worker_lock SET lease_until=?,updated_at=?
                WHERE lock_name=? AND owner_id=? AND fencing_token=? AND lease_until>?
                """,
                (
                    _iso(observed + timedelta(seconds=lease_seconds)),
                    _iso(observed),
                    lock_name,
                    owner_id,
                    fencing_token,
                    _iso(observed),
                ),
            ).rowcount
            connection.commit()
            return bool(changed)

    def release_worker_lock(self, lock_name: str, owner_id: str, fencing_token: int) -> bool:
        with self._write() as connection:
            changed = connection.execute(
                "DELETE FROM catalyst_worker_lock WHERE lock_name=? AND owner_id=? AND fencing_token=?",
                (lock_name, owner_id, fencing_token),
            ).rowcount
            connection.commit()
            return bool(changed)

    def heartbeat(
        self,
        worker_id: str,
        status: str,
        details: dict[str, Any],
        *,
        now: datetime | None = None,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT INTO catalyst_worker_status(worker_id,status,heartbeat_at,details_json)
                VALUES(?,?,?,?)
                ON CONFLICT(worker_id) DO UPDATE SET status=excluded.status,
                    heartbeat_at=excluded.heartbeat_at,details_json=excluded.details_json
                """,
                (worker_id, status[:40], _iso(now or _now()), _json(details)),
            )
            connection.commit()

    def worker_health(
        self,
        *,
        heartbeat_ttl_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = now or _now()
        schema = self.check_schema()
        with self._read() as connection:
            worker = connection.execute(
                """
                SELECT status,heartbeat_at FROM catalyst_worker_status
                WHERE worker_id NOT LIKE ?
                ORDER BY heartbeat_at DESC LIMIT 1
                """,
                (f"{FOCUS_PRODUCER_WORKER_PREFIX}%",),
            ).fetchone()
            lock = connection.execute(
                "SELECT lease_until FROM catalyst_worker_lock WHERE lock_name='catalyst-sync-worker'"
            ).fetchone()
        heartbeat_at = _as_utc(worker["heartbeat_at"]) if worker else None
        lease_until = _as_utc(lock["lease_until"]) if lock else None
        heartbeat_age = (
            max(0.0, (observed - heartbeat_at).total_seconds())
            if heartbeat_at is not None
            else None
        )
        heartbeat_ok = heartbeat_age is not None and heartbeat_age <= heartbeat_ttl_seconds
        lock_ok = lease_until is not None and lease_until > observed
        return {
            "healthy": bool(
                schema["quick_check"] == "ok" and heartbeat_ok and lock_ok
            ),
            "status": worker["status"] if worker else "not_started",
            "heartbeat_at": _iso(heartbeat_at),
            "heartbeat_age_seconds": heartbeat_age,
            "lock_live": lock_ok,
            "schema_version": schema["schema_version"],
            "schema_checksum": schema["schema_checksum"],
        }

    def focus_producer_health(
        self,
        *,
        heartbeat_ttl_seconds: int,
        snapshot_ttl_seconds: int | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return health for the focus producer without affecting sync health."""

        observed = now or _now()
        schema = self.check_schema()
        with self._read() as connection:
            worker = connection.execute(
                """
                SELECT status,heartbeat_at,details_json
                FROM catalyst_worker_status
                WHERE worker_id LIKE ?
                ORDER BY heartbeat_at DESC LIMIT 1
                """,
                (f"{FOCUS_PRODUCER_WORKER_PREFIX}%",),
            ).fetchone()
            lock = connection.execute(
                """
                SELECT lease_until FROM catalyst_worker_lock
                WHERE lock_name='focus-context-producer'
                """
            ).fetchone()
            snapshot = connection.execute(
                """
                SELECT revision,as_of,data_through,market_session,created_at
                FROM focus_context_snapshots ORDER BY revision DESC LIMIT 1
                """
            ).fetchone()
            daily = connection.execute(
                """
                SELECT trading_day,cache_version,universe_version,status,data_through,
                       strength_feature_version,strength_score_version,
                       normalization_version,range_persistence_version,
                       payload_hash,coverage,cached_at,expires_at
                FROM focus_daily_strength_snapshots
                ORDER BY cached_at DESC LIMIT 1
                """
            ).fetchone()
        heartbeat_at = _as_utc(worker["heartbeat_at"]) if worker else None
        lease_until = _as_utc(lock["lease_until"]) if lock else None
        heartbeat_age = (
            max(0.0, (observed - heartbeat_at).total_seconds())
            if heartbeat_at is not None
            else None
        )
        heartbeat_ok = (
            heartbeat_age is not None
            and heartbeat_age <= heartbeat_ttl_seconds
        )
        lock_ok = lease_until is not None and lease_until > observed
        snapshot_created_at = _as_utc(snapshot["created_at"]) if snapshot else None
        snapshot_age = (
            max(0.0, (observed - snapshot_created_at).total_seconds())
            if snapshot_created_at is not None
            else None
        )
        snapshot_ttl = (
            snapshot_ttl_seconds
            if snapshot_ttl_seconds is not None
            else heartbeat_ttl_seconds
        )
        if snapshot_ttl <= 0:
            raise ValueError("focus snapshot TTL must be positive")
        snapshot_fresh = (
            snapshot_age is not None and snapshot_age <= snapshot_ttl
        )
        return {
            "healthy": bool(
                schema["quick_check"] == "ok"
                and heartbeat_ok
                and lock_ok
                and snapshot_fresh
            ),
            "status": worker["status"] if worker else "not_started",
            "heartbeat_at": _iso(heartbeat_at),
            "heartbeat_age_seconds": heartbeat_age,
            "heartbeat_fresh": heartbeat_ok,
            "lock_live": lock_ok,
            "snapshot_age_seconds": snapshot_age,
            "snapshot_fresh": snapshot_fresh,
            "snapshot_ttl_seconds": snapshot_ttl,
            "details": _loads(worker["details_json"], {}) if worker else {},
            "latest_snapshot": dict(snapshot) if snapshot is not None else None,
            "daily_strength_cache": dict(daily) if daily is not None else None,
            "schema_version": schema["schema_version"],
            "schema_checksum": schema["schema_checksum"],
        }
