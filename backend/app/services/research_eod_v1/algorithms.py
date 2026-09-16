"""A/B/C/D setup gates. Score cannot override a hard veto."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from app.services.research_eod_v1.factors import RawComponents


@dataclass(frozen=True)
class SetupDecision:
    passed: bool
    reasons: tuple[str, ...]
    state: str


def _blend(raw: RawComponents, horizon: str) -> float | None:
    return raw.momentum_raw_blend.get(horizon)


def setup_a(raw: RawComponents, horizon: str) -> SetupDecision:
    reasons: list[str] = []
    if not raw.t_direction_ok:
        reasons.append("TREND_DIRECTION")
    blend = _blend(raw, horizon)
    if blend is None or blend <= 0:
        reasons.append("NONPOSITIVE_MOMENTUM")
    if raw.lh_ll_unrepaired:
        reasons.append("LH_LL")
    return SetupDecision(not reasons, tuple(reasons), "eligible" if not reasons else "rejected")


def setup_b(
    raw: RawComponents,
    profile: Mapping[str, Any],
    sector_gates: Mapping[str, Any],
) -> SetupDecision:
    reasons: list[str] = []
    if raw.frozen_setup is None or raw.b_status != "observed":
        reasons.append("NO_FROZEN_BASE")
        state = "forming_base" if raw.b_status == "no_base_observed" else "rejected"
        return SetupDecision(False, tuple(reasons), state)
    resistance = float(raw.frozen_setup["resistance_high"])
    atr = raw.atr
    if raw.sma50 is None or raw.sma50_prev20 is None or raw.sma50 <= raw.sma50_prev20:
        reasons.append("SMA50_SLOPE")
    if raw.above_sma50 is not True:
        reasons.append("BELOW_SMA50")
    price = raw.last_close
    buffer = max(
        float(sector_gates.get("breakout_buffer_price_fraction", 0.0025)) * (price or 0.0),
        float(sector_gates.get("breakout_buffer_atr", 0.15)) * (atr or 0.0),
    )
    if price is None:
        reasons.append("MISSING_CLOSE")
    elif price <= resistance + buffer:
        reasons.append("NOT_THROUGH_RESISTANCE")
    rvol_min = float(sector_gates.get("breakout_rvol_min", 1.0)) * float(
        profile.get("rvol_multiplier", 1.0)
    )
    if raw.rvol is None or raw.rvol < rvol_min:
        reasons.append("LOW_EVENT_RVOL")
    if raw.clv is None or raw.clv < 0.65:
        reasons.append("LOW_CLV")
    if raw.extension_atr is not None and raw.extension_atr > 2.0:
        reasons.append("TOO_FAR_FROM_BASE")
    if not reasons:
        state = "eligible"
    elif "NOT_THROUGH_RESISTANCE" in reasons:
        state = "testing_resistance"
    else:
        state = "rejected"
    return SetupDecision(not reasons, tuple(reasons), state)


def setup_c(raw: RawComponents, sector_gates: Mapping[str, Any]) -> SetupDecision:
    reasons: list[str] = []
    if not raw.t_direction_ok:
        reasons.append("TREND_DIRECTION")
    if raw.structure_invalidated:
        reasons.append("SUPPORT_BROKEN")
    depth_max = float(sector_gates.get("pullback_depth_max_atr", 3.0))
    if raw.p_depth is None or raw.p_depth < 0.5 or raw.p_depth > depth_max:
        reasons.append("DEPTH_OUT_OF_RANGE")
    if not raw.rebound:
        reasons.append("NO_REBOUND")
    if raw.clv is None or raw.clv < 0.55:
        reasons.append("LOW_CLV")
    if raw.rvol is None or raw.rvol < 0.8:
        reasons.append("LOW_RVOL")
    return SetupDecision(not reasons, tuple(reasons), "eligible" if not reasons else "rejected")


def setup_d(raw: RawComponents, profile: Mapping[str, Any]) -> SetupDecision:
    reasons: list[str] = []
    if raw.residual.status != "OK" or raw.residual.raw is None:
        reasons.append(raw.residual.status if raw.residual.status != "OK" else "MISSING_RESIDUAL")
    elif raw.residual.raw <= 0:
        reasons.append("NONPOSITIVE_RESIDUAL")
    if raw.above_sma50 is not True:
        reasons.append("BELOW_SMA50")
    if raw.sma50 is None or raw.sma50_prev20 is None or raw.sma50 <= raw.sma50_prev20:
        reasons.append("SMA50_SLOPE")
    floor = float(profile.get("structure_floor", 55.0))
    if raw.structure_score is None or raw.structure_score < floor:
        reasons.append("WEAK_STRUCTURE")
    return SetupDecision(not reasons, tuple(reasons), "eligible" if not reasons else "rejected")


def setup_for(
    algorithm: str,
    raw: RawComponents,
    profile: Mapping[str, Any],
    sector_gates: Mapping[str, Any],
    horizon: str,
) -> SetupDecision:
    if algorithm == "A_trend_quality":
        return setup_a(raw, horizon)
    if algorithm == "B_confirmed_base_breakout":
        return setup_b(raw, profile, sector_gates)
    if algorithm == "C_trend_pullback":
        return setup_c(raw, sector_gates)
    if algorithm == "D_residual_momentum":
        return setup_d(raw, profile)
    return SetupDecision(False, ("UNKNOWN_ALGORITHM",), "rejected")
