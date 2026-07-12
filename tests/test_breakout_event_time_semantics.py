from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts.repository import BreakoutRepository


START = datetime(2026, 7, 13, 13, 30, tzinfo=timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event(
    *,
    state: str,
    observed_at: datetime,
    first_seen_at: datetime = START,
    triggered_at: datetime | None = None,
    state_changed_at: datetime | None = None,
    previous_state: str | None = None,
    event_id: str = "event-time-semantics",
    ticker: str = "AAPL",
) -> dict:
    return {
        "event_id": event_id,
        "trading_date": first_seen_at.date(),
        "ticker": ticker,
        "session": "regular",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": state,
        "previous_state": previous_state,
        "transition_reason": "test_transition",
        # Deliberately supply the observation time here. The repository must
        # replace it with the stable compatibility anchor.
        "event_at": observed_at,
        "first_seen_at": first_seen_at,
        "triggered_at": triggered_at,
        "state_changed_at": state_changed_at or observed_at,
        "last_seen_at": observed_at,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": f"snapshot-{observed_at.timestamp()}",
        "scores": {"alert_priority_score": 70.0},
    }


def _publish(
    repository: BreakoutRepository,
    at: datetime,
    events: list[dict],
    *,
    transitions: list[dict] | None = None,
) -> None:
    scan_id = repository.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        now=at,
    )
    repository.publish_scan(
        scan_id,
        {
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
            "transitions": transitions or [],
        },
        now=at,
    )


def test_event_and_state_clocks_do_not_reset_on_repeated_scans(tmp_path) -> None:
    repository = BreakoutRepository(tmp_path / "breakouts.db")
    repository.initialize()

    _publish(
        repository,
        START,
        [_event(state="WATCHING", observed_at=START, previous_state="DISCOVERED")],
    )
    triggered_at = START + timedelta(minutes=5)
    _publish(
        repository,
        triggered_at,
        [
            _event(
                state="TRIGGERED",
                observed_at=triggered_at,
                triggered_at=triggered_at,
                state_changed_at=triggered_at,
                previous_state="WATCHING",
            )
        ],
    )

    for minutes in (10, 15, 20):
        observed_at = START + timedelta(minutes=minutes)
        _publish(
            repository,
            observed_at,
            [
                _event(
                    state="TRIGGERED",
                    observed_at=observed_at,
                    # These later values simulate the old broken producer.
                    triggered_at=observed_at,
                    state_changed_at=observed_at,
                    previous_state="TRIGGERED",
                )
            ],
        )

    stored = dict(repository.get_event("event-time-semantics") or {})
    assert stored["first_seen_at"] == _stamp(START)
    assert stored["triggered_at"] == _stamp(triggered_at)
    assert stored["event_at"] == stored["triggered_at"]
    assert stored["state_changed_at"] == stored["triggered_at"]
    assert stored["last_seen_at"] == _stamp(START + timedelta(minutes=20))

    confirmed_at = START + timedelta(minutes=25)
    _publish(
        repository,
        confirmed_at,
        [
            _event(
                state="CONFIRMED",
                observed_at=confirmed_at,
                triggered_at=confirmed_at,
                state_changed_at=confirmed_at,
                previous_state="TRIGGERED",
            )
        ],
    )
    changed = dict(repository.get_event("event-time-semantics") or {})
    assert changed["triggered_at"] == stored["triggered_at"]
    assert changed["event_at"] == stored["event_at"]
    assert changed["state_changed_at"] == _stamp(confirmed_at)


def test_explicit_null_trigger_time_uses_first_trigger_transition(tmp_path) -> None:
    repository = BreakoutRepository(tmp_path / "trigger-transition.db")
    repository.initialize()
    triggered_at = START + timedelta(minutes=5)
    event = _event(
        state="TRIGGERED",
        observed_at=triggered_at,
        triggered_at=None,
        previous_state="WATCHING",
    )

    _publish(
        repository,
        triggered_at,
        [event],
        transitions=[
            {
                "event_id": event["event_id"],
                "from_state": "WATCHING",
                "to_state": "TRIGGERED",
                "reason": "breakout_trigger_crossed",
                "evidence_at": triggered_at,
            }
        ],
    )

    stored = dict(repository.get_event(event["event_id"]) or {})
    assert stored["triggered_at"] == _stamp(triggered_at)
    assert stored["event_at"] == stored["triggered_at"]


def test_existing_trigger_anchor_repairs_explicit_null_input(tmp_path) -> None:
    repository = BreakoutRepository(tmp_path / "existing-trigger.db")
    repository.initialize()
    triggered_at = START + timedelta(minutes=5)
    _publish(
        repository,
        triggered_at,
        [
            _event(
                state="TRIGGERED",
                observed_at=triggered_at,
                triggered_at=triggered_at,
                previous_state="WATCHING",
            )
        ],
    )
    later = START + timedelta(minutes=10)
    _publish(
        repository,
        later,
        [
            _event(
                state="CONFIRMED",
                observed_at=later,
                triggered_at=None,
                previous_state="TRIGGERED",
            )
        ],
    )

    stored = dict(repository.get_event("event-time-semantics") or {})
    assert stored["triggered_at"] == _stamp(triggered_at)
    assert stored["event_at"] == stored["triggered_at"]


def test_explicit_null_trigger_time_without_safe_anchor_is_rejected(tmp_path) -> None:
    path = tmp_path / "unsafe-null-trigger.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    event = _event(
        state="CONFIRMED",
        observed_at=START,
        triggered_at=None,
        previous_state=None,
    )

    with pytest.raises(ValueError, match="without first-trigger evidence"):
        _publish(repository, START, [event])

    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM breakout_events"
        ).fetchone()[0] == 0


@pytest.mark.parametrize("terminal_state", ["FAILED", "EXPIRED"])
def test_terminal_events_cannot_be_revived_by_upsert(tmp_path, terminal_state: str) -> None:
    repository = BreakoutRepository(tmp_path / f"{terminal_state}.db")
    repository.initialize()
    triggered_at = START if terminal_state == "FAILED" else None
    _publish(
        repository,
        START,
        [
            _event(
                state=terminal_state,
                observed_at=START,
                triggered_at=triggered_at,
                previous_state="TRIGGERED" if triggered_at else "WATCHING",
            )
        ],
    )
    later = START + timedelta(minutes=5)
    _publish(
        repository,
        later,
        [_event(state="WATCHING", observed_at=later, previous_state=terminal_state)],
    )

    stored = dict(repository.get_event("event-time-semantics") or {})
    assert stored["lifecycle_state"] == terminal_state
    if terminal_state == "EXPIRED":
        assert stored["triggered_at"] is None
