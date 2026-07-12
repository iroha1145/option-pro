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
