"""Explicit, idempotent and replayable breakout lifecycle."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Mapping

from app.services.breakouts.models import (
    BreakoutLifecycleState,
    BreakoutSetupType,
    normalize_ticker,
)


TERMINAL_STATES = {
    BreakoutLifecycleState.FAILED,
    BreakoutLifecycleState.EXPIRED,
}

ALLOWED_TRANSITIONS = {
    BreakoutLifecycleState.DISCOVERED: {
        BreakoutLifecycleState.WATCHING,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.WATCHING: {
        BreakoutLifecycleState.TRIGGERED,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.TRIGGERED: {
        BreakoutLifecycleState.CONFIRMED,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXTENDED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.CONFIRMED: {
        BreakoutLifecycleState.HOLDING,
        BreakoutLifecycleState.RETESTING,
        BreakoutLifecycleState.EXTENDED,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.HOLDING: {
        BreakoutLifecycleState.RETESTING,
        BreakoutLifecycleState.REACCELERATING,
        BreakoutLifecycleState.EXTENDED,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.RETESTING: {
        BreakoutLifecycleState.RETEST_HELD,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.RETEST_HELD: {
        BreakoutLifecycleState.REACCELERATING,
        BreakoutLifecycleState.RETESTING,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.REACCELERATING: {
        BreakoutLifecycleState.HOLDING,
        BreakoutLifecycleState.EXTENDED,
        BreakoutLifecycleState.RETESTING,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
    BreakoutLifecycleState.EXTENDED: {
        BreakoutLifecycleState.RETESTING,
        BreakoutLifecycleState.FAILED,
        BreakoutLifecycleState.EXPIRED,
    },
}


_GAP_SETUPS = {
    BreakoutSetupType.PREMARKET_GAP,
    BreakoutSetupType.GAP_AND_GO,
    BreakoutSetupType.GAP_HOLD,
    BreakoutSetupType.GAP_FADE,
}


def classify_continuation_setup(
    current_setup: BreakoutSetupType | str,
    previous_state: BreakoutLifecycleState | str,
    observation: Mapping[str, Any],
    *,
    detection_setup: BreakoutSetupType | str | None = None,
    market_state: str | None = None,
) -> tuple[BreakoutSetupType, str]:
    """Classify an existing event without changing its stable identity.

    Lifecycle state and setup classification answer different questions.  The
    former records progress; the latter records the shape currently being
    observed.  In particular, a premarket gap keeps one ``event_id`` while its
    setup evolves to hold, continuation, or fade after the open.
    """

    setup = (
        current_setup
        if isinstance(current_setup, BreakoutSetupType)
        else BreakoutSetupType(str(current_setup))
    )
    state = (
        previous_state
        if isinstance(previous_state, BreakoutLifecycleState)
        else BreakoutLifecycleState(str(previous_state))
    )
    detected = (
        detection_setup
        if isinstance(detection_setup, BreakoutSetupType)
        else BreakoutSetupType(str(detection_setup))
        if detection_setup is not None
        else None
    )
    origin_raw = observation.get("origin_setup_type")
    try:
        origin = BreakoutSetupType(str(origin_raw)) if origin_raw is not None else setup
    except ValueError:
        origin = setup

    if origin in _GAP_SETUPS or setup in _GAP_SETUPS:
        if observation.get("gap_faded") or observation.get("failed"):
            return BreakoutSetupType.GAP_FADE, "gap_reference_or_invalidation_broken"
        if observation.get("gap_and_go"):
            return BreakoutSetupType.GAP_AND_GO, "opening_range_breakout_confirmed"
        if observation.get("gap_holding"):
            return BreakoutSetupType.GAP_HOLD, "gap_held_through_opening_range"
        return setup, "premarket_gap_waiting_for_regular_confirmation"

    if state is BreakoutLifecycleState.RETESTING and observation.get("retest_held"):
        return BreakoutSetupType.RETEST_BREAKOUT, "retest_reclaimed_breakout_zone"

    if (
        state is BreakoutLifecycleState.RETEST_HELD
        and observation.get("reaccelerating")
    ):
        return BreakoutSetupType.RECOVERY_BREAKOUT, "new_event_high_after_retest"

    if (
        str(market_state or "").upper() == "CAPITULATION_RECOVERY"
        and observation.get("recovery_reclaimed")
        and (observation.get("triggered") or observation.get("confirmed"))
    ):
        return BreakoutSetupType.RECOVERY_BREAKOUT, "capitulation_recovery_level_reclaimed"

    if detected is not None and setup is BreakoutSetupType.MOMENTUM_SPIKE:
        return detected, "current_structure_classification"
    return setup, "setup_unchanged"


@dataclass(frozen=True)
class TransitionResult:
    previous_state: BreakoutLifecycleState
    state: BreakoutLifecycleState
    changed: bool
    reason: str


def _target(
    current: BreakoutLifecycleState,
    observation: Mapping[str, Any],
) -> tuple[BreakoutLifecycleState, str]:
    if observation.get("expired"):
        return BreakoutLifecycleState.EXPIRED, "event_ttl_expired"
    if observation.get("failed"):
        return BreakoutLifecycleState.FAILED, str(
            observation.get("failure_reason") or "invalidation_broken"
        )
    if observation.get("extended"):
        return BreakoutLifecycleState.EXTENDED, "distance_threshold_exceeded"
    if current is BreakoutLifecycleState.DISCOVERED:
        return BreakoutLifecycleState.WATCHING, "daily_enrichment_completed"
    if current is BreakoutLifecycleState.WATCHING and observation.get("triggered"):
        return BreakoutLifecycleState.TRIGGERED, "breakout_trigger_crossed"
    if current is BreakoutLifecycleState.TRIGGERED and observation.get("confirmed"):
        return BreakoutLifecycleState.CONFIRMED, "confirmation_evidence_satisfied"
    if current is BreakoutLifecycleState.CONFIRMED and observation.get("holding"):
        return BreakoutLifecycleState.HOLDING, "bars_holding_above_pivot"
    if current in {
        BreakoutLifecycleState.CONFIRMED,
        BreakoutLifecycleState.HOLDING,
        BreakoutLifecycleState.RETEST_HELD,
        BreakoutLifecycleState.REACCELERATING,
        BreakoutLifecycleState.EXTENDED,
    } and observation.get("retesting"):
        return BreakoutLifecycleState.RETESTING, "price_reentered_breakout_zone"
    if current is BreakoutLifecycleState.RETESTING and observation.get("retest_held"):
        return BreakoutLifecycleState.RETEST_HELD, "retest_reclaimed_breakout_zone"
    if current in {
        BreakoutLifecycleState.HOLDING,
        BreakoutLifecycleState.RETEST_HELD,
    } and observation.get("reaccelerating"):
        return BreakoutLifecycleState.REACCELERATING, "new_event_high_after_hold"
    if current is BreakoutLifecycleState.REACCELERATING and observation.get("holding"):
        return BreakoutLifecycleState.HOLDING, "reacceleration_holding"
    return current, "no_meaningful_transition"


def transition_state(
    current_state: BreakoutLifecycleState | str,
    observation: Mapping[str, Any],
    settings: Any = None,
) -> TransitionResult:
    del settings
    current = (
        current_state
        if isinstance(current_state, BreakoutLifecycleState)
        else BreakoutLifecycleState(str(current_state))
    )
    if current in TERMINAL_STATES:
        return TransitionResult(current, current, False, "terminal_state")
    target, reason = _target(current, observation)
    if target is current:
        return TransitionResult(current, current, False, reason)
    if target not in ALLOWED_TRANSITIONS.get(current, set()):
        return TransitionResult(current, current, False, "transition_not_allowed")
    return TransitionResult(current, target, True, reason)


def event_identity(
    *,
    trading_date: date,
    ticker: str,
    setup_type: BreakoutSetupType | str,
    pivot_id: str,
) -> str:
    setup = (
        setup_type.value
        if isinstance(setup_type, BreakoutSetupType)
        else BreakoutSetupType(str(setup_type)).value
    )
    raw = "|".join(
        [trading_date.isoformat(), normalize_ticker(ticker), setup, str(pivot_id)]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def transition_identity(
    *,
    event_id: str,
    from_state: BreakoutLifecycleState,
    to_state: BreakoutLifecycleState,
    reason: str,
    transitioned_at: datetime,
) -> str:
    raw = "|".join(
        [
            event_id,
            from_state.value,
            to_state.value,
            reason,
            transitioned_at.isoformat(),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]
