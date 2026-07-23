from __future__ import annotations

import math
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts.repository import (
    BreakoutRepository,
    ReadOnlyRepositoryError,
    _CARRYOVER_LATEST_ROWS_SQL,
)
from app.services.breakouts.research import load_completed_shadows


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


def _publish(
    repo: BreakoutRepository,
    at: datetime,
    events: list[dict],
    *,
    published_at: datetime | None = None,
) -> str:
    published = published_at or at
    scan = repo.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"database": "breakout-db-v1"},
        now=published,
    )
    repo.publish_scan(scan, _snapshot(at, events), now=published)
    return scan


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


def test_shadow_storage_uses_feature_version_and_canonical_rank_delta(tmp_path) -> None:
    path = tmp_path / "shadow-version.db"
    repo = BreakoutRepository(path)
    repo.initialize()
    event = _event("event-shadow-version", "AAPL", NOW)
    scan = _begin(repo, NOW)
    snapshot = _snapshot(NOW, [event])
    snapshot["range_persistence_shadow"] = [
        {
            "event_id": event["event_id"],
            "ticker": event["ticker"],
            "production_score": 70.0,
            "hypothetical_score": 73.0,
            "production_rank": 10,
            "hypothetical_rank": 7,
            "feature_version": "range-persistence-v2",
        }
    ]

    repo.publish_scan(scan, snapshot, now=NOW)

    with sqlite3.connect(path) as connection:
        stored = connection.execute(
            "SELECT version,rank_delta FROM range_persistence_shadow"
        ).fetchone()
    assert stored == ("range-persistence-v2", -3)
    research_rows = load_completed_shadows(path)
    assert research_rows[0]["version"] == "range-persistence-v2"
    assert research_rows[0]["rank_delta"] == -3


def test_shadow_storage_rejects_conflicting_version_fields(tmp_path) -> None:
    path = tmp_path / "shadow-version-conflict.db"
    repo = BreakoutRepository(path)
    repo.initialize()
    event = _event("event-shadow-conflict", "AAPL", NOW)
    scan = _begin(repo, NOW)
    snapshot = _snapshot(NOW, [event])
    snapshot["range_persistence_shadow"] = [
        {
            "event_id": event["event_id"],
            "ticker": event["ticker"],
            "version": "range-persistence-v3",
            "feature_version": "range-persistence-v2",
        }
    ]

    with pytest.raises(ValueError, match="version fields disagree"):
        repo.publish_scan(scan, snapshot, now=NOW)

    with sqlite3.connect(path) as connection:
        assert connection.execute(
            "SELECT count(*) FROM range_persistence_shadow"
        ).fetchone()[0] == 0


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


def test_event_date_filter_uses_market_trading_date_across_utc_midnight(tmp_path):
    repo = BreakoutRepository(tmp_path / "market-date.db")
    repo.initialize()
    event_at = datetime(2026, 7, 11, 0, 30, tzinfo=timezone.utc)
    event = _event("event-market-date", "AAPL", event_at)
    event["trading_date"] = "2026-07-10"
    _publish(repo, event_at, [event])

    market_day = repo.list_events(date="2026-07-10")
    utc_day = repo.list_events(date="2026-07-11")

    assert [item["event_id"] for item in market_day["events"]] == [
        "event-market-date"
    ]
    assert utc_day["events"] == []


def test_setup_phase_changes_without_changing_event_identity(tmp_path):
    repo = BreakoutRepository(tmp_path / "phase-change.db")
    repo.initialize()
    first_event = _event("event-gap-phase", "GAP", NOW)
    first_event["setup_type"] = "PREMARKET_GAP"
    first = _begin(repo, NOW)
    repo.publish_scan(first, _snapshot(NOW, [first_event]), now=NOW)

    later_at = NOW + timedelta(minutes=35)
    continued = dict(first_event)
    continued.update(
        {
            "setup_type": "GAP_HOLD",
            "lifecycle_state": "HOLDING",
            "previous_state": "WATCHING",
            "event_at": later_at,
            "last_seen_at": later_at,
        }
    )
    second = _begin(repo, later_at)
    repo.publish_scan(second, _snapshot(later_at, [continued]), now=later_at)

    connection = repo.open_read_connection()
    try:
        current = connection.execute(
            """
            SELECT event_id,setup_type,first_seen_at
            FROM breakout_events WHERE ticker='GAP'
            """
        ).fetchall()
        phases = connection.execute(
            """
            SELECT scan_run_id,setup_type FROM breakout_scan_events
            WHERE event_id='event-gap-phase' ORDER BY created_at,scan_run_id
            """
        ).fetchall()
    finally:
        connection.close()
    assert len(current) == 1
    assert tuple(current[0]) == (
        "event-gap-phase",
        "GAP_HOLD",
        NOW.isoformat(timespec="microseconds").replace("+00:00", "Z"),
    )
    assert {tuple(row) for row in phases} == {
        (first, "PREMARKET_GAP"),
        (second, "GAP_HOLD"),
    }


def test_terminal_event_cannot_receive_a_rediscovery_revival_transition(tmp_path):
    repo = BreakoutRepository(tmp_path / "terminal-monotonic.db")
    repo.initialize()
    failed = _event("event-terminal", "TERM", NOW)
    failed["lifecycle_state"] = "FAILED"
    first = _begin(repo, NOW)
    repo.publish_scan(first, _snapshot(NOW, [failed]), now=NOW)

    later = NOW + timedelta(minutes=5)
    rediscovered = dict(failed)
    rediscovered.update(
        {
            "lifecycle_state": "WATCHING",
            "previous_state": "DISCOVERED",
            "event_at": later,
            "last_seen_at": later,
        }
    )
    payload = _snapshot(later, [rediscovered])
    payload["transitions"] = [
        {
            "event_id": "event-terminal",
            "from_state": "DISCOVERED",
            "to_state": "WATCHING",
            "reason": "rediscovered",
            "evidence_at": later,
        }
    ]
    second = _begin(repo, later)
    repo.publish_scan(second, payload, now=later)

    detail = repo.get_event("event-terminal")
    assert detail is not None
    assert detail["lifecycle_state"] == "FAILED"
    assert all(
        transition["to_state"] != "WATCHING"
        for transition in detail["transitions"]
    )


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


def test_retention_is_fenced_redacts_raw_payloads_and_keeps_event_history(tmp_path):
    path = tmp_path / "retention.db"
    repo = BreakoutRepository(path)
    repo.initialize()

    old_at = NOW - timedelta(days=40)
    old_scan = _begin(repo, old_at)
    repo.publish_scan(
        old_scan,
        _snapshot(old_at, [_event("event-old", "OLD", old_at)]),
        now=old_at,
    )

    raw_at = NOW - timedelta(days=2)
    raw_scan = _begin(repo, raw_at)
    raw_payload = _snapshot(raw_at, [_event("event-raw", "RAW", raw_at)])
    raw_payload["provider_snapshot"]["candidates"] = [
        {
            "ticker": "RAW",
            "source": "fixture",
            "provider_timestamp": raw_at,
            "raw_provider_fields": {"debug": "sensitive"},
        }
    ]
    repo.publish_scan(raw_scan, raw_payload, now=raw_at)

    latest = _begin(repo, NOW)
    repo.publish_scan(latest, _snapshot(NOW, [_event("event-new", "NEW", NOW)]), now=NOW)
    token = repo.acquire_lock(
        "breakout-worker",
        "retention-worker",
        90,
        NOW + timedelta(seconds=1),
    )
    counts = repo.prune_retention(
        owner_id="retention-worker",
        lease_token=token,
        now=NOW + timedelta(seconds=1),
        raw_payload_hours=24,
        scan_days=30,
    )
    assert counts["provider_payloads"] >= 1
    assert counts["candidate_raw"] == 1
    assert repo.get_event("event-old") is not None
    assert repo.latest_completed_scan()["scan_run_id"] == latest

    connection = repo.open_read_connection()
    try:
        redacted = connection.execute(
            "SELECT payload_json FROM breakout_provider_snapshots WHERE scan_run_id=?",
            (raw_scan,),
        ).fetchone()[0]
        raw_debug = connection.execute(
            "SELECT raw_provider_fields_json FROM breakout_candidates WHERE scan_run_id=?",
            (raw_scan,),
        ).fetchone()[0]
        archived_provider = connection.execute(
            "SELECT count(*) FROM breakout_provider_snapshots WHERE scan_run_id=?",
            (old_scan,),
        ).fetchone()[0]
    finally:
        connection.close()
    assert redacted == '{"redacted":true}'
    assert raw_debug == "{}"
    assert archived_provider == 0


def test_load_carryover_events_is_point_in_time(tmp_path):
    repo = BreakoutRepository(tmp_path / "point-in-time.db")
    repo.initialize()

    visible = _event("event-visible", "AAPL", NOW - timedelta(minutes=30))
    future_observation = _event(
        "event-future-observation",
        "MSFT",
        NOW + timedelta(minutes=1),
    )
    _publish(
        repo,
        NOW - timedelta(minutes=20),
        [visible, future_observation],
        published_at=NOW - timedelta(minutes=20),
    )

    future_publication = _event(
        "event-future-publication",
        "NVDA",
        NOW - timedelta(minutes=15),
    )
    _publish(
        repo,
        NOW + timedelta(minutes=10),
        [future_publication],
        published_at=NOW + timedelta(minutes=10),
    )

    batch = repo.load_carryover_events(
        as_of=NOW,
        event_ttl_seconds=3_600,
        limit=10,
        expired_due_limit=4,
    )

    assert [event["event_id"] for event in batch.events] == ["event-visible"]
    assert batch.expired_due_event_ids == frozenset()
    assert batch.has_more is False


def test_load_carryover_events_reconstructs_event_before_a_future_update(tmp_path):
    repo = BreakoutRepository(tmp_path / "point-in-time-update.db")
    repo.initialize()
    original = _event(
        "event-updated-later",
        "AAPL",
        NOW - timedelta(minutes=30),
    )
    _publish(
        repo,
        NOW - timedelta(minutes=20),
        [original],
        published_at=NOW - timedelta(minutes=20),
    )
    future = dict(original)
    future.update(
        {
            "event_at": NOW + timedelta(minutes=10),
            "last_seen_at": NOW + timedelta(minutes=10),
            "lifecycle_state": "CONFIRMED",
        }
    )
    _publish(
        repo,
        NOW + timedelta(minutes=10),
        [future],
        published_at=NOW + timedelta(minutes=10),
    )

    batch = repo.load_carryover_events(
        as_of=NOW,
        event_ttl_seconds=3_600,
        limit=10,
        expired_due_limit=4,
    )

    assert [event["event_id"] for event in batch.events] == [
        "event-updated-later"
    ]
    assert batch.events[0]["lifecycle_state"] == original["lifecycle_state"]
    assert batch.events[0]["last_seen_at"].startswith("2026-07-13T13:30:00")


@pytest.mark.parametrize("terminal_state", ["FAILED", "EXPIRED"])
def test_load_carryover_events_does_not_resurrect_preterminal_snapshot(
    tmp_path,
    terminal_state,
):
    repo = BreakoutRepository(tmp_path / f"terminal-{terminal_state.lower()}.db")
    repo.initialize()
    active = _event(
        "event-terminal-latest",
        "AAPL",
        NOW - timedelta(minutes=30),
    )
    _publish(
        repo,
        NOW - timedelta(minutes=20),
        [active],
        published_at=NOW - timedelta(minutes=20),
    )
    terminal = dict(active)
    terminal.update(
        {
            "lifecycle_state": terminal_state,
            "previous_state": active["lifecycle_state"],
            "last_seen_at": NOW - timedelta(minutes=5),
        }
    )
    _publish(
        repo,
        NOW - timedelta(minutes=5),
        [terminal],
        published_at=NOW - timedelta(minutes=5),
    )

    before_terminal = repo.load_carryover_events(
        as_of=NOW - timedelta(minutes=10),
        event_ttl_seconds=3_600,
        limit=10,
        expired_due_limit=4,
    )
    after_terminal = repo.load_carryover_events(
        as_of=NOW,
        event_ttl_seconds=3_600,
        limit=10,
        expired_due_limit=4,
    )

    assert [event["event_id"] for event in before_terminal.events] == [
        "event-terminal-latest"
    ]
    assert after_terminal.events == ()
    assert after_terminal.expired_due_event_ids == frozenset()
    assert after_terminal.has_more is False


def test_carryover_latest_query_seeks_by_event_without_history_window(tmp_path):
    repo = BreakoutRepository(tmp_path / "carryover-plan.db")
    repo.initialize()
    connection = repo.open_read_connection()
    try:
        as_of = NOW.isoformat(timespec="microseconds").replace("+00:00", "Z")
        plan = connection.execute(
            f"EXPLAIN QUERY PLAN {_CARRYOVER_LATEST_ROWS_SQL}",
            (as_of,) * 4,
        ).fetchall()
    finally:
        connection.close()

    details = [str(row["detail"]) for row in plan]
    assert any(
        "idx_breakout_scan_events_event" in detail
        and "event_id=?" in detail
        for detail in details
    ), details
    normalized_sql = " ".join(_CARRYOVER_LATEST_ROWS_SQL.upper().split())
    assert "ROW_NUMBER" not in normalized_sql
    assert " OVER " not in normalized_sql


def test_load_carryover_events_ttl_boundary_due_and_terminal_exclusion(tmp_path):
    repo = BreakoutRepository(tmp_path / "ttl-boundary.db")
    repo.initialize()
    cutoff = NOW - timedelta(hours=1)

    due = _event(
        "event-due",
        "DUE",
        cutoff - timedelta(microseconds=1),
    )
    due["last_seen_at"] = NOW - timedelta(minutes=5)
    boundary = _event("event-boundary", "EDGE", cutoff)
    failed = _event("event-failed", "FAIL", NOW - timedelta(minutes=20))
    failed["lifecycle_state"] = "FAILED"
    expired = _event("event-expired", "OLD", cutoff - timedelta(minutes=10))
    expired["lifecycle_state"] = "EXPIRED"
    _publish(
        repo,
        NOW - timedelta(minutes=5),
        [due, boundary, failed, expired],
        published_at=NOW - timedelta(minutes=5),
    )

    batch = repo.load_carryover_events(
        as_of=NOW,
        event_ttl_seconds=3_600,
        limit=10,
        expired_due_limit=4,
    )

    assert [event["event_id"] for event in batch.events] == [
        "event-due",
        "event-boundary",
    ]
    assert batch.expired_due_event_ids == frozenset({"event-due"})
    assert batch.has_more is False


def test_load_carryover_events_is_bounded_and_reports_more_work(tmp_path):
    repo = BreakoutRepository(tmp_path / "bounded.db")
    repo.initialize()

    due_oldest = _event("event-due-oldest", "DUE1", NOW - timedelta(hours=3))
    due_newer = _event("event-due-newer", "DUE2", NOW - timedelta(hours=2))
    live_oldest = _event("event-live-oldest", "LIVE1", NOW - timedelta(minutes=50))
    live_middle = _event("event-live-middle", "LIVE2", NOW - timedelta(minutes=40))
    live_newest = _event("event-live-newest", "LIVE3", NOW - timedelta(minutes=30))
    _publish(
        repo,
        NOW - timedelta(minutes=5),
        [due_oldest, due_newer, live_oldest, live_middle, live_newest],
        published_at=NOW - timedelta(minutes=5),
    )

    batch = repo.load_carryover_events(
        as_of=NOW,
        event_ttl_seconds=3_600,
        limit=3,
        expired_due_limit=1,
    )

    assert [event["event_id"] for event in batch.events] == [
        "event-due-oldest",
        "event-live-oldest",
        "event-live-middle",
    ]
    assert batch.expired_due_event_ids == frozenset({"event-due-oldest"})
    assert batch.has_more is True
