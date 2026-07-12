"""Classify coarse movers against point-in-time structure."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.models import (
    BreakoutCandidate,
    BreakoutLifecycleState,
    BreakoutSetupType,
    BreakoutStructure,
    MarketSession,
    TemporalCutoff,
)


def _number(features: Mapping[str, Any], name: str) -> float | None:
    try:
        value = float(features.get(name))
    except (TypeError, ValueError):
        return None
    return value


def detect_breakout(
    candidate: BreakoutCandidate,
    structure: BreakoutStructure | None,
    features: Mapping[str, Any],
    cutoff: TemporalCutoff,
    settings: BreakoutSettings | None = None,
) -> dict[str, Any]:
    config = settings or get_breakout_settings()
    price = _number(features, "event_price") or candidate.price
    atr = _number(features, "atr20")
    clv = _number(features, "close_location_value")
    rvol = _number(features, "rvol_time_of_day")
    upper_wick = _number(features, "upper_wick_ratio")
    hold_bars = int(features.get("hold_bars_above_pivot") or 0)
    warnings: list[str] = []

    if cutoff.session is MarketSession.PREMARKET:
        return {
            "setup_type": BreakoutSetupType.PREMARKET_GAP,
            "lifecycle_state": BreakoutLifecycleState.WATCHING,
            "triggered": False,
            "confirmed": False,
            "transition_reason": "premarket_gap_requires_regular_session_confirmation",
            "breakout_distance_atr": None,
            "warnings": ["premarket_not_confirmed"],
        }

    opening_high = _number(features, "opening_range_high")
    opening_complete = bool(features.get("opening_range_complete"))
    opening_buffer = max(
        (price or 0) * config.break_buffer_pct,
        (atr or 0) * config.break_buffer_atr,
    )
    if opening_complete and opening_high is not None and price is not None:
        if price > opening_high + opening_buffer:
            return {
                "setup_type": BreakoutSetupType.OPENING_RANGE_BREAKOUT,
                "lifecycle_state": BreakoutLifecycleState.TRIGGERED,
                "triggered": True,
                "confirmed": False,
                "transition_reason": "complete_bar_above_opening_range",
                "opening_range_distance_atr": (
                    (price - opening_high) / atr if atr else None
                ),
                "warnings": warnings,
            }

    if structure is None:
        return {
            "setup_type": BreakoutSetupType.MOMENTUM_SPIKE,
            "lifecycle_state": BreakoutLifecycleState.WATCHING,
            "triggered": False,
            "confirmed": False,
            "transition_reason": "mover_without_preexisting_resistance",
            "breakout_distance_atr": None,
            "warnings": ["no_valid_base"],
        }

    resistance = structure.resistance_zone.high
    buffer = max(
        (price or resistance) * config.break_buffer_pct,
        (atr or 0) * config.break_buffer_atr,
    )
    distance = (
        (price - resistance) / atr
        if price is not None and atr is not None and atr > 0
        else None
    )
    if price is None or price <= resistance + buffer:
        return {
            "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "lifecycle_state": BreakoutLifecycleState.WATCHING,
            "triggered": False,
            "confirmed": False,
            "transition_reason": "below_breakout_trigger",
            "breakout_distance_atr": distance,
            "warnings": warnings,
        }
    strong_single = (
        clv is not None
        and clv >= 0.70
        and rvol is not None
        and rvol >= 1.5
        and upper_wick is not None
        and upper_wick <= 0.15
        and distance is not None
        and distance >= 0.20
    )
    confirmed = hold_bars >= config.confirmation_bars or strong_single
    state = (
        BreakoutLifecycleState.CONFIRMED
        if confirmed
        else BreakoutLifecycleState.TRIGGERED
    )
    if distance is not None and distance >= config.max_chase_distance_atr:
        warnings.append("extended_from_pivot")
    return {
        "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
        "lifecycle_state": state,
        "triggered": True,
        "confirmed": confirmed,
        "strong_single_bar_confirmation": strong_single,
        "transition_reason": (
            "confirmation_evidence_satisfied"
            if confirmed
            else "first_complete_bar_above_resistance"
        ),
        "breakout_distance_atr": distance,
        "extended": bool(
            distance is not None and distance >= config.max_chase_distance_atr
        ),
        "warnings": warnings,
    }
