from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.breakouts.lifecycle import event_identity, transition_state
from app.services.breakouts.models import BreakoutLifecycleState, BreakoutSetupType


def _move(state, **observation):
    return transition_state(state, observation)


def test_full_lifecycle_and_idempotent_noop() -> None:
    state = BreakoutLifecycleState.DISCOVERED
    sequence = [
        ({}, BreakoutLifecycleState.WATCHING),
        ({"triggered": True}, BreakoutLifecycleState.TRIGGERED),
        ({"confirmed": True}, BreakoutLifecycleState.CONFIRMED),
        ({"holding": True}, BreakoutLifecycleState.HOLDING),
        ({"retesting": True}, BreakoutLifecycleState.RETESTING),
        ({"retest_held": True}, BreakoutLifecycleState.RETEST_HELD),
        ({"reaccelerating": True}, BreakoutLifecycleState.REACCELERATING),
    ]
    for observation, expected in sequence:
        result = transition_state(state, observation)
        assert result.changed is True
        state = result.state
        assert state is expected
    repeated = transition_state(state, {"reaccelerating": True})
    assert repeated.changed is False
    assert repeated.state is state


def test_failure_extended_and_terminal_behavior() -> None:
    extended = _move(BreakoutLifecycleState.TRIGGERED, extended=True)
    assert extended.state is BreakoutLifecycleState.EXTENDED
    failed = _move(extended.state, failed=True, failure_reason="below_invalidation")
    assert failed.state is BreakoutLifecycleState.FAILED
    terminal = _move(failed.state, triggered=True)
    assert terminal.changed is False
    assert terminal.state is BreakoutLifecycleState.FAILED


def test_event_identity_is_stable_and_new_pivot_creates_new_event() -> None:
    values = {
        "trading_date": date(2026, 7, 10),
        "ticker": "test",
        "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
        "pivot_id": "p1",
    }
    first = event_identity(**values)
    assert first == event_identity(**values)
    assert first != event_identity(**{**values, "pivot_id": "p2"})
