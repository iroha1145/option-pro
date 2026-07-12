from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts import repository as repository_module
from app.services.breakouts.repository import (
    BreakoutRepository,
    LEGACY_SCHEMA_CHECKSUM,
    LEGACY_SCHEMA_VERSION,
    MAX_EVENT_JSON_BYTES,
    SCHEMA_VERSION,
    SchemaVersionError,
    V2_SCHEMA_CHECKSUM,
    V2_SCHEMA_VERSION,
)


START = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _json_at_size(body: dict, target_bytes: int, *, field: str = "padding") -> str:
    candidate = dict(body)
    candidate[field] = ""
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    remaining = target_bytes - len(encoded.encode("utf-8"))
    assert remaining >= 0
    candidate[field] = "x" * remaining
    encoded = json.dumps(candidate, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    assert len(encoded.encode("utf-8")) == target_bytes
    return encoded


def _event_body(*, event_id: str = "event-legacy") -> dict:
    return {
        "event_id": event_id,
        "trading_date": START.date().isoformat(),
        "ticker": "AAPL",
        "session": "regular",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "CONFIRMED",
        "event_at": _stamp(START + timedelta(minutes=5)),
        "first_seen_at": _stamp(START),
        "last_seen_at": _stamp(START + timedelta(minutes=10)),
        "pivot_id": "pivot-AAPL",
        "source_snapshot_id": "provider-cache-bucket",
        "scores": {
            "alert_priority_score": 72.0,
            "data_confidence_score": 88.0,
        },
    }


def _create_versioned_fixture(
    path,
    *,
    version: str,
    event_json: str | None = None,
    snapshot_json: str | None = None,
) -> None:
    if version == LEGACY_SCHEMA_VERSION:
        schema = repository_module._LEGACY_SCHEMA
        checksum = LEGACY_SCHEMA_CHECKSUM
    elif version == V2_SCHEMA_VERSION:
        schema = repository_module._V2_SCHEMA
        checksum = V2_SCHEMA_CHECKSUM
    else:  # pragma: no cover - fixture misuse guard
        raise AssertionError(f"unsupported fixture version: {version}")

    body = _event_body()
    event_json = event_json if event_json is not None else json.dumps(
        body, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    snapshot_json = snapshot_json if snapshot_json is not None else event_json
    connection = sqlite3.connect(path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        for statement in schema:
            connection.execute(statement)
        connection.execute("PRAGMA ignore_check_constraints=ON")
        connection.execute(
            "INSERT INTO breakout_schema_version(version,checksum,applied_at) VALUES(?,?,?)",
            (version, checksum, _stamp(START)),
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
                "scan-legacy",
                "idem-legacy",
                "fixture",
                "regular",
                _stamp(START),
                _stamp(START),
                _stamp(START + timedelta(minutes=15)),
                _stamp(START + timedelta(minutes=15)),
                "completed",
                0,
                1,
                "config-v1",
                "versions-v1",
                "{}",
                "provider-cache-bucket",
                _stamp(START),
                _stamp(START + timedelta(minutes=15)),
            ),
        )
        provider_payload = json.dumps(
            {
                "provider": "fixture",
                "status": "active",
                "as_of": _stamp(START),
                "session": "regular",
                "schema_version": "fixture-v1",
                "cache_key": "provider-cache-bucket",
                "candidates": [],
            },
            separators=(",", ":"),
            sort_keys=True,
        )
        connection.execute(
            """
            INSERT INTO breakout_provider_snapshots(
                snapshot_id,scan_run_id,provider,status,as_of,session,schema_version,
                candidate_count,is_stale,warnings_json,payload_json,created_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "provider-cache-bucket",
                "scan-legacy",
                "fixture",
                "active",
                _stamp(START),
                "regular",
                "fixture-v1",
                0,
                0,
                "[]",
                provider_payload,
                _stamp(START),
                None,
            ),
        )
        if version == LEGACY_SCHEMA_VERSION:
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
                    "event-legacy",
                    START.date().isoformat(),
                    "AAPL",
                    "DAILY_BASE_BREAKOUT",
                    "pivot-AAPL",
                    "CONFIRMED",
                    _stamp(START + timedelta(minutes=5)),
                    _stamp(START),
                    _stamp(START + timedelta(minutes=10)),
                    "provider-cache-bucket",
                    72.0,
                    88.0,
                    "scan-legacy",
                    event_json,
                    _stamp(START),
                    _stamp(START + timedelta(minutes=10)),
                ),
            )
        else:
            connection.execute(
                """
                INSERT INTO breakout_events(
                    event_id,trading_date,ticker,setup_type,pivot_id,lifecycle_state,
                    event_at,first_seen_at,triggered_at,state_changed_at,last_seen_at,
                    source_snapshot_id,alert_priority_score,data_confidence_score,
                    current_scan_run_id,event_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    "event-legacy",
                    START.date().isoformat(),
                    "AAPL",
                    "DAILY_BASE_BREAKOUT",
                    "pivot-AAPL",
                    "CONFIRMED",
                    _stamp(START + timedelta(minutes=5)),
                    _stamp(START),
                    _stamp(START + timedelta(minutes=5)),
                    _stamp(START + timedelta(minutes=7)),
                    _stamp(START + timedelta(minutes=10)),
                    "provider-cache-bucket",
                    72.0,
                    88.0,
                    "scan-legacy",
                    event_json,
                    _stamp(START),
                    _stamp(START + timedelta(minutes=10)),
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
                "scan-legacy",
                "event-legacy",
                1,
                "AAPL",
                "regular",
                "DAILY_BASE_BREAKOUT",
                "CONFIRMED",
                _stamp(START + timedelta(minutes=5)),
                72.0,
                72.0,
                snapshot_json,
                _stamp(START + timedelta(minutes=10)),
            ),
        )
        transition = {
            "transition_id": "transition-legacy",
            "event_id": "event-legacy",
            "from_state": "TRIGGERED",
            "to_state": "CONFIRMED",
            "reason": "fixture",
            "evidence_at": _stamp(START + timedelta(minutes=7)),
        }
        connection.execute(
            """
            INSERT INTO breakout_transitions(
                transition_id,event_id,from_state,to_state,reason,evidence_at,
                scan_run_id,transition_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (
                "transition-legacy",
                "event-legacy",
                "TRIGGERED",
                "CONFIRMED",
                "fixture",
                _stamp(START + timedelta(minutes=7)),
                "scan-legacy",
                json.dumps(transition, separators=(",", ":"), sort_keys=True),
                _stamp(START + timedelta(minutes=7)),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _event(event_id: str, ticker: str, first_seen: datetime, observed: datetime) -> dict:
    return {
        "event_id": event_id,
        "trading_date": first_seen.date(),
        "ticker": ticker,
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "previous_state": "WATCHING",
        "transition_reason": "pivot_crossed",
        "event_at": first_seen,
        "first_seen_at": first_seen,
        "triggered_at": first_seen,
        "state_changed_at": first_seen,
        "last_seen_at": observed,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": "provider-cache-bucket",
        "scores": {"alert_priority_score": 80.0, "data_confidence_score": 90.0},
    }


def _publish(
    repo: BreakoutRepository,
    at: datetime,
    events: list[dict],
    *,
    cache_key: str = "provider-cache-bucket",
    transitions: list[dict] | None = None,
) -> str:
    scan_id = repo.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"database": SCHEMA_VERSION},
        now=at,
    )
    repo.publish_scan(
        scan_id,
        {
            "provider_snapshot": {
                "snapshot_id": cache_key,
                "cache_key": cache_key,
                "provider": "fixture",
                "status": "active",
                "as_of": at,
                "session": "regular",
                "schema_version": "fixture-v1",
                "warnings": [],
                "candidates": [],
            },
            "events": events,
            "transitions": transitions or [],
        },
        now=at,
    )
    return scan_id


def test_published_v1_and_v2_checksums_remain_stable() -> None:
    assert LEGACY_SCHEMA_VERSION == "breakout-db-v1"
    assert LEGACY_SCHEMA_CHECKSUM == (
        "126122e7ff28a629ab4dfe129e1b87ac1446a52ac8d2b3c358cdf510e8b49af2"
    )
    assert V2_SCHEMA_VERSION == "breakout-db-v2"
    assert V2_SCHEMA_CHECKSUM == (
        "a8c354a34d9782a740a943938a4bb28a58c8ddfab3cf0a9e70c9b5bd3945a5af"
    )
    assert SCHEMA_VERSION == "breakout-db-v3"


@pytest.mark.parametrize("source_version", [LEGACY_SCHEMA_VERSION, V2_SCHEMA_VERSION])
def test_v1_and_v2_migrate_to_v3_and_v3_initialization_is_idempotent(
    tmp_path,
    source_version: str,
) -> None:
    path = tmp_path / f"{source_version}.db"
    _create_versioned_fixture(path, version=source_version)

    repo = BreakoutRepository(path)
    repo.initialize()
    repo.initialize()

    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        schema = connection.execute(
            "SELECT version,checksum FROM breakout_schema_version"
        ).fetchone()
        assert schema["version"] == SCHEMA_VERSION
        assert schema["checksum"] == repository_module.SCHEMA_CHECKSUM
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        provider = connection.execute(
            "SELECT snapshot_id,provider_cache_key FROM breakout_provider_snapshots"
        ).fetchone()
        assert provider["provider_cache_key"] == "provider-cache-bucket"
        assert provider["snapshot_id"] != provider["provider_cache_key"]
        assert connection.execute(
            "SELECT count(*) FROM breakout_migration_quarantine"
        ).fetchone()[0] == 0
        transition = json.loads(
            connection.execute(
                "SELECT transition_json FROM breakout_transitions"
            ).fetchone()[0]
        )
        assert transition["evidence"]["provider_cache_key"] == "provider-cache-bucket"
        assert transition["evidence"]["source_snapshot_id"] == provider["snapshot_id"]


def test_read_only_v2_reports_upgrade_required_without_mutating_schema(tmp_path) -> None:
    path = tmp_path / "read-only-v2.db"
    _create_versioned_fixture(path, version=V2_SCHEMA_VERSION)

    with pytest.raises(SchemaVersionError) as error:
        BreakoutRepository(path, read_only=True).status()

    assert error.value.status_payload()["schema_version"] == V2_SCHEMA_VERSION
    assert error.value.status_payload()["required_schema_version"] == SCHEMA_VERSION
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM breakout_schema_version"
        ).fetchone()[0] == V2_SCHEMA_VERSION
        assert connection.execute(
            """
            SELECT count(*) FROM sqlite_master
            WHERE type='table' AND name='breakout_migration_quarantine'
            """
        ).fetchone()[0] == 0


def test_near_v1_limit_json_grows_safely_during_migration(tmp_path) -> None:
    path = tmp_path / "near-limit-v1.db"
    raw = _json_at_size(_event_body(), 262_144)
    _create_versioned_fixture(
        path,
        version=LEGACY_SCHEMA_VERSION,
        event_json=raw,
        snapshot_json=raw,
    )

    repo = BreakoutRepository(path)
    repo.initialize()

    with sqlite3.connect(path) as connection:
        stored_event, stored_snapshot = connection.execute(
            """
            SELECT event_json,
                   (SELECT event_snapshot_json FROM breakout_scan_events LIMIT 1)
            FROM breakout_events LIMIT 1
            """
        ).fetchone()
        assert len(stored_event.encode("utf-8")) > 262_144
        assert len(stored_event.encode("utf-8")) <= MAX_EVENT_JSON_BYTES
        assert len(stored_snapshot.encode("utf-8")) <= MAX_EVENT_JSON_BYTES
        assert json.loads(stored_event)["event_id"] == "event-legacy"


def test_corrupt_event_and_snapshot_are_rebuilt_and_quarantined(tmp_path) -> None:
    path = tmp_path / "corrupt-v2.db"
    _create_versioned_fixture(
        path,
        version=V2_SCHEMA_VERSION,
        event_json="{broken-event-json",
        snapshot_json="[broken-snapshot-json",
    )

    repo = BreakoutRepository(path)
    repo.initialize()
    repo.initialize()

    event = repo.get_event("event-legacy")
    assert event is not None
    assert event["event_id"] == "event-legacy"
    assert event["ticker"] == "AAPL"
    assert event["lifecycle_state"] == "CONFIRMED"
    assert event["migration_warning"] == "invalid_event_json"
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT table_name,record_key,error_code,raw_sha256,raw_preview,recovered_json
            FROM breakout_migration_quarantine ORDER BY table_name
            """
        ).fetchall()
        assert {(row["table_name"], row["error_code"]) for row in rows} == {
            ("breakout_events", "invalid_event_json"),
            ("breakout_scan_events", "invalid_event_snapshot_json"),
        }
        for row in rows:
            assert len(row["raw_sha256"]) == 64
            assert row["raw_preview"]
            recovered = json.loads(row["recovered_json"])
            assert recovered["event_id"] == "event-legacy"
            assert recovered["trading_date"] == START.date().isoformat()
            assert recovered["pivot_id"] == "pivot-AAPL"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_oversized_valid_json_is_compacted_deterministically(tmp_path) -> None:
    path = tmp_path / "oversized-v2.db"
    oversized = _json_at_size(
        _event_body() | {"debug": "placeholder"},
        MAX_EVENT_JSON_BYTES + 32_000,
        field="debug",
    )
    _create_versioned_fixture(
        path,
        version=V2_SCHEMA_VERSION,
        event_json=oversized,
        snapshot_json=oversized,
    )

    BreakoutRepository(path).initialize()

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT event_json FROM breakout_events"
        ).fetchone()[0]
        snapshot = connection.execute(
            "SELECT event_snapshot_json FROM breakout_scan_events"
        ).fetchone()[0]
    event = json.loads(stored)
    snapshot_event = json.loads(snapshot)
    assert "debug" not in event
    assert "debug" not in snapshot_event
    assert event["migration_compacted_fields"] == ["debug"]
    assert snapshot_event["migration_compacted_fields"] == ["debug"]
    assert len(stored.encode("utf-8")) <= MAX_EVENT_JSON_BYTES
    assert len(snapshot.encode("utf-8")) <= MAX_EVENT_JSON_BYTES


def test_uncompactable_oversized_json_falls_back_to_minimal_event(tmp_path) -> None:
    path = tmp_path / "oversized-minimal-v2.db"
    oversized = _json_at_size(
        _event_body(),
        MAX_EVENT_JSON_BYTES + 32_000,
        field="essential_blob",
    )
    normal_snapshot = json.dumps(
        _event_body(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )
    _create_versioned_fixture(
        path,
        version=V2_SCHEMA_VERSION,
        event_json=oversized,
        snapshot_json=normal_snapshot,
    )

    BreakoutRepository(path).initialize()

    with sqlite3.connect(path) as connection:
        stored = json.loads(
            connection.execute("SELECT event_json FROM breakout_events").fetchone()[0]
        )
        quarantine = connection.execute(
            """
            SELECT error_code,recovered_json FROM breakout_migration_quarantine
            WHERE table_name='breakout_events'
            """
        ).fetchone()
    assert stored["event_id"] == "event-legacy"
    assert stored["migration_warning"] == "oversized_event_json"
    assert "essential_blob" not in stored
    assert quarantine[0] == "oversized_event_json"
    assert json.loads(quarantine[1]) == stored


def test_failed_v2_migration_rolls_back_all_schema_changes(tmp_path) -> None:
    path = tmp_path / "rollback-v2.db"
    _create_versioned_fixture(path, version=V2_SCHEMA_VERSION)
    repo = BreakoutRepository(path)

    def fail_after_rebuild(phase, _connection):
        if phase == "tables_rebuilt":
            raise RuntimeError("injected migration failure")

    repo._migration_hook = fail_after_rebuild
    with pytest.raises(RuntimeError, match="injected migration failure"):
        repo.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT version FROM breakout_schema_version"
        ).fetchone()[0] == V2_SCHEMA_VERSION
        assert {
            row[1] for row in connection.execute("PRAGMA table_info(breakout_provider_snapshots)")
        }.isdisjoint({"provider_cache_key"})
        assert connection.execute(
            "SELECT count(*) FROM breakout_events"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='breakout_migration_quarantine'"
        ).fetchone()[0] == 0


def test_database_snapshot_id_is_scoped_to_scan_run_and_keeps_provider_cache_key(
    tmp_path,
) -> None:
    repo = BreakoutRepository(tmp_path / "snapshot-identity.db")
    repo.initialize()
    first_at = START
    second_at = START + timedelta(seconds=15)
    first = _publish(
        repo,
        first_at,
        [_event("event-first", "AAPL", first_at, first_at)],
    )
    second = _publish(
        repo,
        second_at,
        [_event("event-second", "MSFT", second_at, second_at)],
    )

    with sqlite3.connect(repo.path) as connection:
        rows = connection.execute(
            """
            SELECT scan_run_id,snapshot_id,provider_cache_key
            FROM breakout_provider_snapshots ORDER BY scan_run_id
            """
        ).fetchall()
        event_sources = connection.execute(
            """
            SELECT current_scan_run_id,source_snapshot_id,event_json
            FROM breakout_events ORDER BY current_scan_run_id
            """
        ).fetchall()
        transition_sources = connection.execute(
            """
            SELECT scan_run_id,transition_json
            FROM breakout_transitions ORDER BY scan_run_id
            """
        ).fetchall()
    assert {row[0] for row in rows} == {first, second}
    assert len({row[1] for row in rows}) == 2
    assert {row[2] for row in rows} == {"provider-cache-bucket"}
    snapshots_by_scan = {row[0]: row[1] for row in rows}
    for scan_run_id, source_snapshot_id, event_json in event_sources:
        assert source_snapshot_id == snapshots_by_scan[scan_run_id]
        assert json.loads(event_json)["source_snapshot_id"] == source_snapshot_id
    for scan_run_id, transition_json in transition_sources:
        evidence = json.loads(transition_json)["evidence"]
        assert evidence["source_snapshot_id"] == snapshots_by_scan[scan_run_id]
        assert evidence["provider_cache_key"] == "provider-cache-bucket"


def test_retention_keeps_latest_completed_reference_across_multiple_batches(
    tmp_path,
) -> None:
    repo = BreakoutRepository(tmp_path / "retention-v3.db")
    repo.initialize()
    first_seen = START - timedelta(days=70)
    scan_ids = []
    for offset in range(35):
        observed = first_seen + timedelta(days=offset)
        scan_ids.append(
            _publish(
                repo,
                observed,
                [_event("event-long-lived", "AAPL", first_seen, observed)],
                cache_key=f"bucket-{offset}",
            )
        )
    _publish(
        repo,
        START,
        [_event("event-unrelated", "MSFT", START, START)],
        cache_key="bucket-current",
    )
    token = repo.acquire_lock(
        "breakout-worker",
        "retention-v3-worker",
        120,
        START + timedelta(seconds=1),
    )

    for _ in range(20):
        counts = repo.prune_retention(
            owner_id="retention-v3-worker",
            lease_token=token,
            raw_payload_hours=24,
            scan_days=30,
            batch_size=3,
            now=START + timedelta(seconds=2),
        )
        if not any(counts.values()):
            break
    else:  # pragma: no cover - protects against retention making no progress
        pytest.fail("retention did not converge across bounded batches")

    with sqlite3.connect(repo.path) as connection:
        references = connection.execute(
            """
            SELECT event.scan_run_id
            FROM breakout_scan_events AS event
            JOIN breakout_scan_runs AS run USING(scan_run_id)
            WHERE event.event_id='event-long-lived' AND run.status='completed'
            ORDER BY run.published_at,run.scan_run_id
            """
        ).fetchall()
        assert references == [(scan_ids[-1],)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert repo.get_event("event-long-lived") is not None
    assert repo.events_for_ticker("AAPL")[0]["event_id"] == "event-long-lived"
    carryover = repo.load_carryover_events(
        as_of=START + timedelta(seconds=3),
        event_ttl_seconds=365 * 24 * 60 * 60,
        limit=10,
        expired_due_limit=10,
    )
    assert "event-long-lived" in {
        str(event["event_id"]) for event in carryover.events
    }


def test_snapshot_id_formula_is_deterministic() -> None:
    expected = hashlib.sha256(
        (
            "scan-legacy"
            "fixture"
            "provider-cache-bucket"
            f"{_stamp(START)}"
        ).encode("utf-8")
    ).hexdigest()
    assert repository_module._database_snapshot_id(
        "scan-legacy",
        "fixture",
        "provider-cache-bucket",
        _stamp(START),
    ) == expected
