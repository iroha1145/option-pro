from __future__ import annotations

from datetime import date, datetime, timezone

from app.services.breakouts.lifecycle import (
    classify_continuation_setup,
    event_identity,
    transition_state,
)
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


def test_watching_gap_can_fail_without_being_revived() -> None:
    failed = _move(
        BreakoutLifecycleState.WATCHING,
        failed=True,
        failure_reason="gap_filled_on_complete_bar",
    )
    assert failed.state is BreakoutLifecycleState.FAILED
    assert failed.reason == "gap_filled_on_complete_bar"
    assert _move(failed.state, triggered=True).changed is False


def test_continuation_setup_labels_keep_event_identity_separate() -> None:
    gap_hold, _ = classify_continuation_setup(
        BreakoutSetupType.PREMARKET_GAP,
        BreakoutLifecycleState.WATCHING,
        {"origin_setup_type": "PREMARKET_GAP", "gap_holding": True},
    )
    gap_go, _ = classify_continuation_setup(
        gap_hold,
        BreakoutLifecycleState.HOLDING,
        {"origin_setup_type": "PREMARKET_GAP", "gap_and_go": True},
    )
    gap_fade, _ = classify_continuation_setup(
        gap_go,
        BreakoutLifecycleState.CONFIRMED,
        {"origin_setup_type": "PREMARKET_GAP", "gap_faded": True},
    )
    retest, _ = classify_continuation_setup(
        BreakoutSetupType.DAILY_BASE_BREAKOUT,
        BreakoutLifecycleState.RETESTING,
        {"retest_held": True},
    )
    recovery, _ = classify_continuation_setup(
        retest,
        BreakoutLifecycleState.RETEST_HELD,
        {"reaccelerating": True},
    )
    assert (gap_hold, gap_go, gap_fade) == (
        BreakoutSetupType.GAP_HOLD,
        BreakoutSetupType.GAP_AND_GO,
        BreakoutSetupType.GAP_FADE,
    )
    assert retest is BreakoutSetupType.RETEST_BREAKOUT
    assert recovery is BreakoutSetupType.RECOVERY_BREAKOUT


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
