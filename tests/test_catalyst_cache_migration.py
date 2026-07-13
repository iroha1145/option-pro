from __future__ import annotations

import sqlite3

import pytest

from app.services.catalysts import repository as repository_module
from app.services.catalysts.repository import CatalystRepository


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
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
