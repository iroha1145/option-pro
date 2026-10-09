"""prune_retention cost must grow linearly with scan history.

Counts SQLite virtual-machine steps (progress handler), never wall time.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.breakouts.repository import DEFAULT_LOCK_NAME, BreakoutRepository

NOW = datetime(2026, 10, 9, 13, 0, tzinfo=timezone.utc)
EVENTS_PER_RUN = 6
LIFETIME = 12


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _build(path: Path, old_runs: int) -> None:
    BreakoutRepository(path).initialize()
    times = [NOW - timedelta(days=60) + timedelta(minutes=5 * index) for index in range(old_runs)]
    times += [NOW - timedelta(minutes=5 * (3 - index)) for index in range(3)]
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN")
    for index, when in enumerate(times):
        stamp = _stamp(when)
        connection.execute(
            """INSERT INTO breakout_scan_runs(scan_run_id,idempotency_key,provider,profile,session,
                   scheduled_at,started_at,completed_at,published_at,status,candidate_count,event_count,
                   config_hash,versions_hash,versions_json,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?,?,'completed',0,?,'cfg','ver','{}',?,?)""",
            (f"scan-{index:05d}", f"idem-{index:05d}", "fixture", "regular", "regular",
             stamp, stamp, stamp, stamp, EVENTS_PER_RUN, stamp, stamp),
        )
    runs = len(times)
    events = max(1, runs * EVENTS_PER_RUN // LIFETIME)
    ranks: dict[int, int] = {}
    for number in range(events):
        start = min(runs - 1, number * runs // events)
        end = min(runs - 1, start + LIFETIME - 1)
        event_id = f"evt-{number:05d}"
        first_seen = _stamp(times[start])
        connection.execute(
            """INSERT INTO breakout_events(event_id,trading_date,ticker,setup_type,pivot_id,lifecycle_state,
                   event_at,first_seen_at,triggered_at,state_changed_at,last_seen_at,source_snapshot_id,
                   alert_priority_score,data_confidence_score,current_scan_run_id,event_json,created_at,updated_at)
               VALUES(?,?,?,?,?,'WATCHING',?,?,NULL,?,?,'snap',50.0,0.8,?,?,?,?)""",
            (event_id, times[start].date().isoformat(), f"T{number}", "breakout", f"pv-{number}",
             first_seen, first_seen, first_seen, _stamp(times[end]), f"scan-{end:05d}",
             json.dumps({"event_id": event_id}), first_seen, _stamp(times[end])),
        )
        for run in range(start, end + 1):
            ranks[run] = ranks.get(run, 0) + 1
            connection.execute(
                """INSERT INTO breakout_scan_events(scan_run_id,event_id,rank,ticker,session,setup_type,
                       lifecycle_state,event_at,alert_priority_score,sort_priority,event_snapshot_json,created_at)
                   VALUES(?,?,?,?,'regular','breakout','WATCHING',?,50.0,50.0,?,?)""",
                (f"scan-{run:05d}", event_id, ranks[run], f"T{number}", first_seen,
                 json.dumps({"event_id": event_id, "triggered_at": None}), _stamp(times[run])),
            )
    connection.execute("COMMIT")
    connection.close()


def _steps_for_idle_prune(path: Path) -> tuple[int, dict]:
    repository = BreakoutRepository(path)
    token = repository.acquire_lock(DEFAULT_LOCK_NAME, "scaling", 3_600, NOW)
    assert token is not None
    for call in range(50):
        result = repository.prune_retention(
            owner_id="scaling", lease_token=token, scan_days=30, batch_size=500,
            now=NOW + timedelta(seconds=call + 1),
        )
        if not any(result.values()):
            break
    steps = 0

    def tick() -> int:
        nonlocal steps
        steps += 1
        return 0

    original = repository._write_connection

    def counted() -> sqlite3.Connection:
        connection = original()
        connection.set_progress_handler(tick, 100)
        return connection

    repository._write_connection = counted  # type: ignore[method-assign]
    result = repository.prune_retention(
        owner_id="scaling", lease_token=token, scan_days=30, batch_size=500,
        now=NOW + timedelta(seconds=100),
    )
    return steps, dict(result)


def test_idle_prune_after_backlog_scales_linearly_with_history(tmp_path: Path) -> None:
    small_steps, small_result = _steps_for_idle_prune(_built(tmp_path / "small.db", 240))
    large_steps, large_result = _steps_for_idle_prune(_built(tmp_path / "large.db", 480))
    assert not any(small_result.values()) and not any(large_result.values())
    # Doubling the history doubles a linear probe and quadruples the old
    # plan (every past event re-scanned every completed run).
    assert large_steps < 2.6 * small_steps


def test_prune_keeps_first_and_latest_snapshot_of_every_event(tmp_path: Path) -> None:
    path = _built(tmp_path / "kept.db", 120)
    _steps_for_idle_prune(path)
    connection = sqlite3.connect(path)
    kept = dict(
        connection.execute(
            "SELECT event_id, COUNT(*) FROM breakout_scan_events GROUP BY event_id"
        ).fetchall()
    )
    events = [row[0] for row in connection.execute("SELECT event_id FROM breakout_events")]
    first_and_latest = connection.execute(
        """SELECT COUNT(*) FROM breakout_events AS event
           WHERE NOT EXISTS(
               SELECT 1 FROM breakout_scan_events AS snapshot
               WHERE snapshot.event_id=event.event_id
                 AND snapshot.scan_run_id=event.current_scan_run_id
           )"""
    ).fetchone()[0]
    connection.close()
    assert set(kept) == set(events)
    assert first_and_latest == 0
    assert all(count >= 1 for count in kept.values())


def _built(path: Path, old_runs: int) -> Path:
    _build(path, old_runs)
    return path
