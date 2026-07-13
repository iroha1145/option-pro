from __future__ import annotations

import hashlib
import sqlite3

import pytest

from app.services.catalysts import repository as repository_module
from app.services.catalysts.repository import CatalystRepository
from catalyst_support import catalyst_item, utc


_V1_SCHEMA = """
CREATE TABLE catalyst_schema_metadata (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version TEXT NOT NULL,
    schema_checksum TEXT NOT NULL,
    installed_at TEXT NOT NULL
);

CREATE TABLE catalyst_sync_runs (
    run_id TEXT PRIMARY KEY,
    stream TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT,
    snapshot_token TEXT,
    from_sequence INTEGER,
    through_sequence INTEGER,
    data_through TEXT,
    item_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT
);

CREATE TABLE catalyst_sync_state (
    stream TEXT PRIMARY KEY,
    last_attempt_at TEXT,
    last_success_at TEXT,
    data_through TEXT,
    watermark_sequence INTEGER,
    updated_after TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    circuit_open_until TEXT,
    last_error_code TEXT,
    remote_status TEXT,
    snapshot_generation INTEGER NOT NULL DEFAULT 0,
    current_snapshot_id TEXT
);
"""


def _seed_v1(path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(_V1_SCHEMA)
        connection.execute(
            "INSERT INTO catalyst_schema_metadata VALUES(1,?,?,?)",
            (
                repository_module._V1_DATABASE_VERSION,
                repository_module._V1_SCHEMA_CHECKSUM,
                "2026-07-10T09:00:00Z",
            ),
        )
        connection.execute(
            "INSERT INTO catalyst_sync_state(stream,current_snapshot_id) VALUES('feed','old-snapshot')"
        )
        connection.execute("PRAGMA user_version=1")


def _seed_v5(path) -> None:
    repository = CatalystRepository(path)
    repository.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE focus_daily_strength_snapshots")
        connection.executescript(
            """
            CREATE TABLE focus_daily_strength_snapshots (
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
            CREATE INDEX idx_focus_daily_strength_retention
                ON focus_daily_strength_snapshots(cached_at,trading_day);
            """
        )
        connection.execute(
            """
            INSERT INTO focus_daily_strength_snapshots(
                trading_day,cache_version,universe_version,status,data_through,
                payload_json,cached_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                "2026-07-10",
                "legacy-v5-cache",
                "themes-v5",
                "active",
                "2026-07-10T20:00:00Z",
                '{"_focus_rows":[{"ticker":"AAPL"}]}',
                "2026-07-10T20:05:00Z",
                None,
            ),
        )
        connection.execute(
            "UPDATE catalyst_schema_metadata "
            "SET schema_version=?,schema_checksum=? WHERE singleton=1",
            (
                repository_module._V5_DATABASE_VERSION,
                repository_module._V5_SCHEMA_CHECKSUM,
            ),
        )
        connection.execute("PRAGMA user_version=5")


def _seed_v6(path) -> tuple[str, int]:
    canonical = catalyst_item(sequence=1, updated_at=utc(10, 6), analysis=True)
    missing = catalyst_item(
        sequence=2,
        updated_at=utc(10, 7),
        analysis=True,
        news_id=102,
        ticker="MISS",
    )
    assert missing.analysis is not None
    missing = missing.model_copy(
        update={
            "source_tickers": [],
            "analysis": missing.analysis.model_copy(
                update={"analysis_id": 9002, "stock_validations": []}
            ),
        }
    )
    v6_schema = repository_module._SCHEMA_SQL.removesuffix(
        "\n\n" + repository_module._TRUSTED_PROJECTION_SCHEMA_SQL
    )
    assert hashlib.sha256(v6_schema.encode("utf-8")).hexdigest() == (
        repository_module._V6_SCHEMA_CHECKSUM
    )
    snapshot = "v6-seed-snapshot"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.executescript(v6_schema)
        connection.execute(
            "INSERT INTO catalyst_schema_metadata VALUES(1,?,?,?)",
            (
                repository_module._V6_DATABASE_VERSION,
                repository_module._V6_SCHEMA_CHECKSUM,
                repository_module._iso(utc(9)),
            ),
        )
        connection.execute(
            """INSERT INTO catalyst_sync_state(
                   stream,last_success_at,data_through,watermark_sequence,
                   updated_after,remote_status,snapshot_generation,current_snapshot_id
               ) VALUES('feed',?,?,?,?,?,?,?)""",
            (
                repository_module._iso(utc(10, 7)),
                repository_module._iso(utc(10, 7)),
                2,
                repository_module._iso(utc(10, 7)),
                "active",
                1,
                snapshot,
            ),
        )
        for item in (canonical, missing):
            _insert_v6_item_revision(
                connection,
                item,
                cached_at=repository_module._iso(utc(10, 7)),
            )
        connection.execute("PRAGMA user_version=6")
    return snapshot, 2


def _insert_v6_item_revision(
    connection: sqlite3.Connection,
    item,
    *,
    cached_at: str,
) -> None:
    connection.execute(
        """INSERT INTO catalyst_item_revisions(
               news_id,change_sequence,content_hash,source,title,summary,url,
               published_at,fetched_at,updated_at,cached_at,source_tickers_json,
               analysis_status,is_stale,raw_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            item.news_id,
            item.change_sequence,
            item.content_hash,
            item.source,
            item.title,
            item.summary,
            item.url,
            repository_module._iso(item.published_at),
            repository_module._iso(item.fetched_at),
            repository_module._iso(item.updated_at),
            cached_at,
            repository_module._json(item.source_tickers),
            item.analysis_status.value,
            int(item.is_stale),
            repository_module._json(item.model_dump(mode="json")),
        ),
    )
    for ticker in item.source_tickers:
        connection.execute(
            "INSERT INTO catalyst_item_tickers VALUES(?,?,?)",
            (item.news_id, item.change_sequence, ticker),
        )
    analysis = item.analysis
    if analysis is None or item.analyzed_at is None or item.available_at is None:
        return
    revision_id = f"{analysis.analysis_id}:{analysis.revision}"
    connection.execute(
        """INSERT INTO catalyst_analysis_revisions(
               analysis_revision_id,news_id,content_hash,item_change_sequence,
               analyzed_at,available_at,model,reasoning,prompt_version,
               analysis_schema_version,classification,confidence,
               overall_sentiment,market_relevance,insufficient_context,cached_at,
               raw_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            revision_id,
            item.news_id,
            item.content_hash,
            item.change_sequence,
            repository_module._iso(item.analyzed_at),
            repository_module._iso(item.available_at),
            analysis.model,
            analysis.reasoning,
            analysis.prompt_version,
            analysis.schema_version,
            analysis.classification.value,
            analysis.confidence,
            analysis.overall_sentiment,
            analysis.market_relevance,
            int(analysis.insufficient_context),
            cached_at,
            repository_module._json(analysis.model_dump(mode="json")),
        ),
    )
    for impact in analysis.affected_stocks:
        connection.execute(
            """INSERT INTO catalyst_stock_impacts(
                   analysis_revision_id,news_id,item_change_sequence,ticker,company,
                   impact_score,confidence,horizon,mechanism,reason,content_hash,
                   published_at,fetched_at,analyzed_at,available_at,model,reasoning,
                   prompt_version,analysis_schema_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                revision_id,
                item.news_id,
                item.change_sequence,
                impact.ticker,
                impact.company,
                impact.impact_score,
                impact.confidence,
                impact.horizon.value,
                impact.mechanism.value,
                impact.reason,
                item.content_hash,
                repository_module._iso(item.published_at),
                repository_module._iso(item.fetched_at),
                repository_module._iso(item.analyzed_at),
                repository_module._iso(item.available_at),
                analysis.model,
                analysis.reasoning,
                analysis.prompt_version,
                analysis.schema_version,
            ),
        )


def test_v1_migration_reaches_current_schema_and_preserves_state(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    _seed_v1(path)

    repository = CatalystRepository(path)
    repository.initialize()

    assert repository.check_schema()["schema_version"] == repository_module.DATABASE_VERSION
    with repository.open_read_connection() as connection:
        state = connection.execute(
            "SELECT current_snapshot_id,resync_required,resync_generation "
            "FROM catalyst_sync_state WHERE stream='feed'"
        ).fetchone()
        assert tuple(state) == ("old-snapshot", 0, 0)
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == repository_module.SQLITE_USER_VERSION
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='catalyst_market_focus_cycles'"
        ).fetchone()


def test_v1_migration_failure_rolls_back_schema_and_metadata(tmp_path, monkeypatch) -> None:
    path = tmp_path / "catalysts.db"
    _seed_v1(path)
    repository = CatalystRepository(path)
    execute_atomic = repository._execute_script_atomic

    def fail_during_v3(connection: sqlite3.Connection, script: str) -> None:
        if script == repository_module._MARKET_FOCUS_SCHEMA_SQL:
            connection.execute("CREATE TABLE migration_partial(id INTEGER PRIMARY KEY)")
            raise RuntimeError("injected migration failure")
        execute_atomic(connection, script)

    monkeypatch.setattr(repository, "_execute_script_atomic", fail_during_v3)

    with pytest.raises(RuntimeError, match="injected migration failure"):
        repository.initialize()

    with sqlite3.connect(path) as connection:
        metadata = connection.execute(
            "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata WHERE singleton=1"
        ).fetchone()
        assert metadata == (
            repository_module._V1_DATABASE_VERSION,
            repository_module._V1_SCHEMA_CHECKSUM,
        )
        columns = {
            row[1]
            for row in connection.execute("PRAGMA table_info(catalyst_sync_state)")
        }
        assert "resync_required" not in columns
        assert "resync_generation" not in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='migration_partial'"
        ).fetchone() is None


def test_v3_focus_jobs_migrate_to_snapshot_batch_identity(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP INDEX idx_catalyst_market_focus_jobs_due")
        connection.execute("DROP TABLE catalyst_market_focus_jobs")
        connection.executescript(
            """
            CREATE TABLE catalyst_market_focus_jobs (
                local_cycle_id TEXT PRIMARY KEY,
                expected_prepared_revision INTEGER NOT NULL UNIQUE,
                remote_cycle_id TEXT UNIQUE,
                status TEXT NOT NULL,
                model TEXT,
                reasoning TEXT,
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
            CREATE INDEX idx_catalyst_market_focus_jobs_due
                ON catalyst_market_focus_jobs(status,next_attempt_at,updated_at);
            """
        )
        connection.execute(
            """
            INSERT INTO catalyst_market_focus_jobs(
                local_cycle_id,expected_prepared_revision,status,model,reasoning,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?)
            """,
            (
                "mfc_" + "a" * 32,
                7,
                "pending",
                "gpt-5.6-terra",
                "max",
                "2026-07-12T10:00:00Z",
                "2026-07-12T10:00:00Z",
            ),
        )
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=?",
            (
                repository_module._V3_DATABASE_VERSION,
                repository_module._V3_SCHEMA_CHECKSUM,
            ),
        )
        connection.execute("PRAGMA user_version=3")

    repository.initialize()

    with repository.open_read_connection() as connection:
        row = connection.execute(
            """
            SELECT request_key,last_consumed_revision_at_request,execution_number,
                   expected_prepared_revision,status
            FROM catalyst_market_focus_jobs
            """
        ).fetchone()
        assert tuple(row) == ("batch:7:0", 0, 1, 7, "pending")
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == repository_module.SQLITE_USER_VERSION
        )
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='focus_daily_strength_snapshots'"
        ).fetchone()


def test_v4_cache_migrates_through_v6_daily_strength_table(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize()
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE focus_daily_strength_snapshots")
        connection.execute(
            "UPDATE catalyst_schema_metadata SET schema_version=?,schema_checksum=?",
            (
                repository_module._V4_DATABASE_VERSION,
                repository_module._V4_SCHEMA_CHECKSUM,
            ),
        )
        connection.execute("PRAGMA user_version=4")

    repository.initialize()

    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='focus_daily_strength_snapshots'"
        ).fetchone()
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == repository_module.SQLITE_USER_VERSION
        )
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(focus_daily_strength_snapshots)"
            ).fetchall()
        }
        assert {
            "strength_feature_version",
            "strength_score_version",
            "normalization_version",
            "range_persistence_version",
            "payload_hash",
            "coverage",
        }.issubset(columns)


def test_v5_cache_migrates_to_v6_by_invalidating_only_derived_rows(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    _seed_v5(path)
    repository = CatalystRepository(path)

    repository.initialize()
    repository.initialize()

    with repository.open_read_connection() as connection:
        metadata = connection.execute(
            "SELECT schema_version,schema_checksum "
            "FROM catalyst_schema_metadata WHERE singleton=1"
        ).fetchone()
        assert tuple(metadata) == (
            repository_module.DATABASE_VERSION,
            repository_module.SCHEMA_CHECKSUM,
        )
        assert connection.execute(
            "SELECT COUNT(*) FROM focus_daily_strength_snapshots"
        ).fetchone()[0] == 0
        columns = {
            row["name"]
            for row in connection.execute(
                "PRAGMA table_info(focus_daily_strength_snapshots)"
            ).fetchall()
        }
        assert {
            "trading_day",
            "cache_version",
            "universe_version",
            "strength_feature_version",
            "strength_score_version",
            "normalization_version",
            "range_persistence_version",
            "payload_hash",
            "coverage",
            "status",
            "data_through",
            "payload_json",
            "cached_at",
            "expires_at",
        } == columns
        assert (
            connection.execute("PRAGMA user_version").fetchone()[0]
            == repository_module.SQLITE_USER_VERSION
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v5_to_v6_migration_failure_rolls_back_drop_and_metadata(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "catalysts.db"
    _seed_v5(path)
    repository = CatalystRepository(path)
    execute_atomic = repository._execute_script_atomic

    def fail_during_v6(connection: sqlite3.Connection, script: str) -> None:
        if (
            "CREATE TABLE focus_daily_strength_snapshots" in script
            and "payload_hash" in script
        ):
            connection.execute(
                "CREATE TABLE migration_partial_v6(id INTEGER PRIMARY KEY)"
            )
            raise RuntimeError("injected v6 migration failure")
        execute_atomic(connection, script)

    monkeypatch.setattr(repository, "_execute_script_atomic", fail_during_v6)

    with pytest.raises(RuntimeError, match="injected v6 migration failure"):
        repository.initialize()

    with sqlite3.connect(path) as connection:
        metadata = connection.execute(
            "SELECT schema_version,schema_checksum "
            "FROM catalyst_schema_metadata WHERE singleton=1"
        ).fetchone()
        assert metadata == (
            repository_module._V5_DATABASE_VERSION,
            repository_module._V5_SCHEMA_CHECKSUM,
        )
        assert connection.execute(
            "SELECT cache_version FROM focus_daily_strength_snapshots"
        ).fetchone()[0] == "legacy-v5-cache"
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(focus_daily_strength_snapshots)"
            )
        }
        assert "payload_hash" not in columns
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
        assert connection.execute(
            "SELECT 1 FROM sqlite_master "
            "WHERE type='table' AND name='migration_partial_v6'"
        ).fetchone() is None


def test_v6_cache_migrates_to_v7_trusted_projections_idempotently(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    snapshot, watermark = _seed_v6(path)
    repository = CatalystRepository(path)

    repository.initialize(now=utc(11))
    repository.initialize(now=utc(11, 1))

    assert repository.check_schema()["schema_version"] == "catalyst-cache-v7"
    state = repository.sync_state("feed")
    assert state["current_snapshot_id"] == snapshot
    assert state["watermark_sequence"] == watermark
    with repository.open_read_connection() as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute(
            "SELECT count(*) FROM catalyst_item_revisions"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM catalyst_analysis_projections"
        ).fetchone()[0] == 2
        assert connection.execute(
            "SELECT count(*) FROM catalyst_stock_impact_projections"
        ).fetchone()[0] == 1
        stats = connection.execute(
            """
            SELECT scanned_item_revisions,analysis_projections,trusted_stock_impacts,
                   missing_validations,malformed_item_revisions
            FROM catalyst_projection_migration_stats
            WHERE schema_version='catalyst-cache-v7'
            """
        ).fetchone()
        assert tuple(stats) == (2, 2, 1, 1, 0)


def test_v7_reads_remain_available_when_v6_raw_items_are_malformed(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    snapshot, watermark = _seed_v6(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE catalyst_item_revisions SET raw_json='{' WHERE news_id=101"
        )
        connection.execute(
            "UPDATE catalyst_item_revisions SET raw_json='[]' WHERE news_id=102"
        )

    repository = CatalystRepository(path)
    repository.initialize(now=utc(11))

    state = repository.sync_state("feed")
    assert state["current_snapshot_id"] == snapshot
    assert state["watermark_sequence"] == watermark
    feed = repository.list_feed(as_of=utc(11), window_hours=72)
    assert {item["news_id"] for item in feed["items"]} == {101, 102}
    assert all(item["analysis"] is None for item in feed["items"])
    assert repository.get_news(101, as_of=utc(11))["analysis"] is None
    assert repository.get_news(102, as_of=utc(11))["analysis"] is None
    with repository.open_read_connection() as connection:
        stats = connection.execute(
            """SELECT scanned_item_revisions,malformed_item_revisions
               FROM catalyst_projection_migration_stats
               WHERE schema_version='catalyst-cache-v7'"""
        ).fetchone()
        assert tuple(stats) == (2, 2)


def test_v6_to_v7_migration_failure_rolls_back_tables_metadata_and_watermark(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "catalysts.db"
    snapshot, watermark = _seed_v6(path)
    repository = CatalystRepository(path)
    original = repository._insert_analysis_projection
    calls = 0

    def fail_after_first(connection, item, *, cached_at):
        nonlocal calls
        result = original(connection, item, cached_at=cached_at)
        calls += 1
        if calls == 1:
            raise RuntimeError("injected v7 projection migration failure")
        return result

    monkeypatch.setattr(repository, "_insert_analysis_projection", fail_after_first)
    with pytest.raises(RuntimeError, match="injected v7"):
        repository.initialize(now=utc(11))

    with sqlite3.connect(path) as connection:
        metadata = connection.execute(
            "SELECT schema_version,schema_checksum FROM catalyst_schema_metadata"
        ).fetchone()
        assert metadata == (
            repository_module._V6_DATABASE_VERSION,
            repository_module._V6_SCHEMA_CHECKSUM,
        )
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 6
        assert connection.execute(
            "SELECT current_snapshot_id,watermark_sequence FROM catalyst_sync_state "
            "WHERE stream='feed'"
        ).fetchone() == (snapshot, watermark)
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='catalyst_analysis_projections'"
        ).fetchone() is None
