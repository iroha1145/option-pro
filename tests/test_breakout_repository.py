from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts.repository import (
    BreakoutRepository,
    ReadOnlyRepositoryError,
)


NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def _event(event_id: str, ticker: str, at: datetime, priority: float = 80.0) -> dict:
    return {
        "event_id": event_id,
        "trading_date": at.date(),
        "ticker": ticker,
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "previous_state": "WATCHING",
        "transition_reason": "pivot_crossed",
        "event_at": at,
        "first_seen_at": at,
        "last_seen_at": at,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": "source-snapshot",
        "scores": {
            "alert_priority_score": priority,
            "data_confidence_score": 90.0,
        },
    }


def _snapshot(at: datetime, events: list[dict]) -> dict:
    return {
        "provider_snapshot": {
            "provider": "fixture",
            "status": "active",
            "as_of": at,
            "session": "regular",
            "schema_version": "fixture-v1",
            "warnings": [],
            "candidates": [],
        },
        "events": events,
    }


def _begin(repo: BreakoutRepository, at: datetime) -> str:
    return repo.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"database": "breakout-db-v1"},
    )


def test_schema_pragmas_and_read_only_connection(tmp_path):
    path = tmp_path / "breakouts.db"
    repo = BreakoutRepository(path)
    repo.initialize()

    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "breakout_provider_snapshots",
        "breakout_scan_events",
        "breakout_worker_status",
        "breakout_worker_lock",
    }.issubset(tables)

    reader = BreakoutRepository(path, read_only=True)
    connection = reader.open_read_connection()
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("CREATE TABLE forbidden(value INTEGER)")
    finally:
        connection.close()
    with pytest.raises(ReadOnlyRepositoryError):
        reader.initialize()


def test_publish_failure_rolls_back_and_old_snapshot_survives_restart(tmp_path):
    path = tmp_path / "breakouts.db"
    repo = BreakoutRepository(path)
    repo.initialize()
    first = _begin(repo, NOW)
    repo.publish_scan(first, _snapshot(NOW, [_event("event-first", "AAPL", NOW)]))

    second_at = NOW + timedelta(minutes=5)
    second = _begin(repo, second_at)

    def fail_before_commit(phase, _connection):
        if phase == "before_complete":
            raise RuntimeError("injected publish failure")

    repo._publish_hook = fail_before_commit
    with pytest.raises(RuntimeError, match="injected publish failure"):
        repo.publish_scan(
            second,
            _snapshot(second_at, [_event("event-second", "MSFT", second_at)]),
        )

    restarted = BreakoutRepository(path, read_only=True)
    latest = restarted.latest_completed_scan()
    assert latest is not None
    assert latest["scan_run_id"] == first
    assert [event["ticker"] for event in latest["events"]] == ["AAPL"]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT status FROM breakout_scan_runs WHERE scan_run_id=?", (second,)
        ).fetchone()[0] == "running"
        assert connection.execute(
            "SELECT count(*) FROM breakout_events WHERE ticker='MSFT'"
        ).fetchone()[0] == 0


def test_event_transition_idempotency_and_cursor_stays_on_original_scan(tmp_path):
    path = tmp_path / "breakouts.db"
    repo = BreakoutRepository(path)
    repo.initialize()
    events = [
        _event("event-aaaa", "AAPL", NOW, 90),
        _event("event-bbbb", "MSFT", NOW - timedelta(seconds=1), 80),
        _event("event-cccc", "NVDA", NOW - timedelta(seconds=2), 70),
    ]
    first = _begin(repo, NOW)
    repo.publish_scan(first, _snapshot(NOW, events))
    page_one = repo.list_events(limit=2)
    assert page_one["scan_run_id"] == first
    assert page_one["next_cursor"]

    later = NOW + timedelta(minutes=5)
    repeated = dict(events[0])
    repeated["last_seen_at"] = later
    repeated["event_at"] = NOW
    second = _begin(repo, later)
    repo.publish_scan(second, _snapshot(later, [repeated]))

    page_two = repo.list_events(limit=2, cursor=page_one["next_cursor"])
    assert page_two["scan_run_id"] == first
    assert [event["ticker"] for event in page_two["events"]] == ["NVDA"]
    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM breakout_events WHERE event_id='event-aaaa'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM breakout_transitions WHERE event_id='event-aaaa'"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT count(*) FROM breakout_scan_events WHERE event_id='event-aaaa'"
        ).fetchone()[0] == 2


def test_non_finite_or_oversized_json_is_rejected(tmp_path):
    path = tmp_path / "breakouts.db"
    repo = BreakoutRepository(path)
    repo.initialize()
    scan = _begin(repo, NOW)
    event = _event("event-nan1", "AAPL", NOW)
    event["features"] = {"bad": math.nan}
    with pytest.raises(ValueError, match="finite"):
        repo.publish_scan(scan, _snapshot(NOW, [event]))

    scan = _begin(repo, NOW + timedelta(minutes=5))
    candidate = {
        "ticker": "AAPL",
        "source": "fixture",
        "provider_timestamp": NOW,
        "raw_provider_fields": {"debug": "x" * 17_000},
    }
    payload = _snapshot(NOW, [])
    payload["candidates"] = [candidate]
    with pytest.raises(ValueError, match="16384"):
        repo.publish_scan(scan, payload)
