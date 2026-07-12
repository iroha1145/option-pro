from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.api import breakouts as breakout_api
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts import repository as repository_module
from app.services.breakouts.repository import (
    BreakoutRepository,
    LEGACY_SCHEMA_CHECKSUM,
    LEGACY_SCHEMA_VERSION,
    SCHEMA_VERSION,
    SchemaVersionError,
)


START = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _create_v1_database(path) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        for statement in repository_module._LEGACY_SCHEMA:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO breakout_schema_version(version,checksum,applied_at) VALUES(?,?,?)",
            (LEGACY_SCHEMA_VERSION, LEGACY_SCHEMA_CHECKSUM, _stamp(START)),
        )
        connection.execute(
            """
            INSERT INTO breakout_scan_runs(
                scan_run_id,idempotency_key,provider,session,scheduled_at,started_at,
                completed_at,published_at,status,candidate_count,event_count,config_hash,
                versions_hash,versions_json,source_snapshot_id,created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "scan-v1",
                "idem-v1",
                "fixture",
                "regular",
                _stamp(START),
                _stamp(START),
                _stamp(START + timedelta(minutes=15)),
                _stamp(START + timedelta(minutes=15)),
                "completed",
                0,
                2,
                "config-v1",
                "versions-v1",
                "{}",
                "snapshot-v1",
                _stamp(START),
                _stamp(START + timedelta(minutes=15)),
            ),
        )

        rows = [
            (
                "event-triggered-v1",
                "AAPL",
                "CONFIRMED",
                START + timedelta(minutes=10),
                START,
                START + timedelta(minutes=10),
                "pivot-AAPL",
            ),
            (
                "event-watching-v1",
                "MSFT",
                "WATCHING",
                START + timedelta(minutes=10),
                START,
                START + timedelta(minutes=10),
                "pivot-MSFT",
            ),
        ]
        for rank, (
            event_id,
            ticker,
            state,
            event_at,
            first_seen_at,
            last_seen_at,
            pivot_id,
        ) in enumerate(rows, 1):
            event = {
                "event_id": event_id,
                "trading_date": START.date().isoformat(),
                "ticker": ticker,
                "session": "regular",
                "setup_type": "DAILY_BASE_BREAKOUT",
                "lifecycle_state": state,
                "event_at": _stamp(event_at),
                "first_seen_at": _stamp(first_seen_at),
                "last_seen_at": _stamp(last_seen_at),
                "pivot_id": pivot_id,
                "source_snapshot_id": "snapshot-v1",
                "scores": {"alert_priority_score": 70.0},
            }
            encoded = json.dumps(event, separators=(",", ":"), sort_keys=True)
            connection.execute(
                """
                INSERT INTO breakout_events(
                    event_id,trading_date,ticker,setup_type,pivot_id,lifecycle_state,
                    event_at,first_seen_at,last_seen_at,source_snapshot_id,
                    alert_priority_score,data_confidence_score,current_scan_run_id,
                    event_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    START.date().isoformat(),
                    ticker,
                    "DAILY_BASE_BREAKOUT",
                    pivot_id,
                    state,
                    _stamp(event_at),
                    _stamp(first_seen_at),
                    _stamp(last_seen_at),
                    "snapshot-v1",
                    70.0,
                    90.0,
                    "scan-v1",
                    encoded,
                    _stamp(START),
                    _stamp(last_seen_at),
                ),
            )
            connection.execute(
                """
                INSERT INTO breakout_scan_events(
                    scan_run_id,event_id,rank,ticker,session,setup_type,lifecycle_state,
                    event_at,alert_priority_score,sort_priority,event_snapshot_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "scan-v1",
                    event_id,
                    rank,
                    ticker,
                    "regular",
                    "DAILY_BASE_BREAKOUT",
                    state,
                    _stamp(event_at),
                    70.0,
                    70.0,
                    encoded,
                    _stamp(last_seen_at),
                ),
            )

        transitions = [
            (
                "transition-trigger-v1",
                "event-triggered-v1",
                "WATCHING",
                "TRIGGERED",
                START + timedelta(minutes=5),
            ),
            (
                "transition-confirm-v1",
                "event-triggered-v1",
                "TRIGGERED",
                "CONFIRMED",
                START + timedelta(minutes=7),
            ),
            (
                "transition-watch-v1",
                "event-watching-v1",
                "DISCOVERED",
                "WATCHING",
                START + timedelta(minutes=2),
            ),
        ]
        for transition_id, event_id, from_state, to_state, evidence_at in transitions:
            payload = {
                "transition_id": transition_id,
                "event_id": event_id,
                "from_state": from_state,
                "to_state": to_state,
                "reason": "fixture",
                "evidence_at": _stamp(evidence_at),
            }
            connection.execute(
                """
                INSERT INTO breakout_transitions(
                    transition_id,event_id,from_state,to_state,reason,evidence_at,
                    scan_run_id,transition_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    transition_id,
                    event_id,
                    from_state,
                    to_state,
                    "fixture",
                    _stamp(evidence_at),
                    "scan-v1",
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    _stamp(evidence_at),
                ),
            )
        connection.commit()
    finally:
        connection.close()


def test_v1_migration_is_idempotent_and_preserves_event_identity(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    _create_v1_database(path)

    with pytest.raises(SchemaVersionError) as error:
        BreakoutRepository(path, read_only=True).status()
    assert error.value.status_payload() == {
        "status": "schema_upgrade_required",
        "schema_version": LEGACY_SCHEMA_VERSION,
        "required_schema_version": SCHEMA_VERSION,
        "migration_required": True,
        "message": (
            f"breakout database schema {LEGACY_SCHEMA_VERSION} requires migration "
            f"to {SCHEMA_VERSION}"
        ),
    }

    repository = BreakoutRepository(path)
    repository.initialize()
    repository.initialize()

    status = repository.status()
    assert status["database"]["schema_version"] == SCHEMA_VERSION
    triggered = dict(repository.get_event("event-triggered-v1") or {})
    watching = dict(repository.get_event("event-watching-v1") or {})
    assert triggered["event_id"] == "event-triggered-v1"
    assert triggered["triggered_at"] == _stamp(START + timedelta(minutes=10))
    assert triggered["event_at"] == triggered["triggered_at"]
    assert triggered["state_changed_at"] == _stamp(START + timedelta(minutes=7))
    assert watching["event_id"] == "event-watching-v1"
    assert watching["triggered_at"] is None
    assert watching["event_at"] == _stamp(START)
    assert watching["state_changed_at"] == _stamp(START + timedelta(minutes=2))

    connection = repository.open_read_connection()
    try:
        assert connection.execute("SELECT count(*) FROM breakout_events").fetchone()[0] == 2
        columns = {
            row["name"]: row for row in connection.execute("PRAGMA table_info(breakout_events)")
        }
        assert columns["triggered_at"]["notnull"] == 0
        assert columns["state_changed_at"]["notnull"] == 1
        snapshots = connection.execute(
            "SELECT event_snapshot_json FROM breakout_scan_events ORDER BY rank"
        ).fetchall()
        for row in snapshots:
            payload = json.loads(row["event_snapshot_json"])
            assert {
                "first_seen_at",
                "triggered_at",
                "state_changed_at",
                "last_seen_at",
            }.issubset(payload)
    finally:
        connection.close()


def test_read_only_api_reports_legacy_schema_without_mutating_it(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "legacy-read-only.db"
    _create_v1_database(path)
    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        BREAKOUT_DB_PATH=path,
    )
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)

    status = breakout_api.status()
    assert status.status == "unavailable"
    assert status.database["status"] == "schema_upgrade_required"
    assert status.database["schema_version"] == LEGACY_SCHEMA_VERSION
    assert status.database["required_schema_version"] == SCHEMA_VERSION
    assert status.database["migration_required"] is True

    current = breakout_api.current()
    assert current.status == "unavailable"
    assert current.source_status["database"] == "schema_upgrade_required"
    assert current.source_status["schema"]["schema_version"] == LEGACY_SCHEMA_VERSION

    # API readers must not run the migration as a side effect.
    connection = sqlite3.connect(path)
    try:
        versions = [
            row[0]
            for row in connection.execute(
                "SELECT version FROM breakout_schema_version ORDER BY version"
            )
        ]
    finally:
        connection.close()
    assert versions == [LEGACY_SCHEMA_VERSION]
