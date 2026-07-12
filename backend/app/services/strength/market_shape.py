"""Deterministic six-state market-shape classification and breakout fit rules.

The existing market-regime service already measures trend, momentum, breadth,
volume and risk appetite.  This module turns those independent measurements
into the six-state contract used by Breakout Radar without feeding market
context back into a stock's intrinsic-strength score.
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import pstdev
from typing import Any, Mapping


MARKET_SHAPE_VERSION = "market-shape-v2"

STATE_LABELS = {
    "BULL_TREND": "多头趋势",
    "BULL_PULLBACK": "多头回调",
    "RANGE_ACCUMULATION": "区间蓄势",
    "RANGE_DISTRIBUTION": "区间派发",
    "BEAR_TREND": "空头趋势",
    "CAPITULATION_RECOVERY": "恐慌修复",
}

_STATE_RULES: dict[str, dict[str, Any]] = {
    "BULL_TREND": {
        "ordinary_breakout_fit": 88.0,
        "recovery_breakout_fit": 84.0,
        "preferred_setups": [
            "DAILY_BASE_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
            "RETEST_BREAKOUT",
            "RECOVERY_BREAKOUT",
        ],
        "caution_setups": ["MOMENTUM_SPIKE"],
        "confirmation_bar_delta": 0,
        "allow_single_bar_confirmation": True,
        "eligibility": "normal",
    },
    "BULL_PULLBACK": {
        "ordinary_breakout_fit": 62.0,
        "recovery_breakout_fit": 78.0,
        "preferred_setups": ["RETEST_BREAKOUT", "RECOVERY_BREAKOUT"],
        "caution_setups": [
            "DAILY_BASE_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
            "MOMENTUM_SPIKE",
        ],
        "confirmation_bar_delta": 0,
        "allow_single_bar_confirmation": True,
        "eligibility": "selective",
    },
    "RANGE_ACCUMULATION": {
        "ordinary_breakout_fit": 72.0,
        "recovery_breakout_fit": 70.0,
        "preferred_setups": ["DAILY_BASE_BREAKOUT", "RETEST_BREAKOUT"],
        "caution_setups": ["MOMENTUM_SPIKE", "GAP_AND_GO"],
        "confirmation_bar_delta": 0,
        "allow_single_bar_confirmation": True,
        "eligibility": "normal",
    },
    "RANGE_DISTRIBUTION": {
        "ordinary_breakout_fit": 36.0,
        "recovery_breakout_fit": 48.0,
        "preferred_setups": ["RETEST_BREAKOUT"],
        "caution_setups": [
            "DAILY_BASE_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
            "PREMARKET_GAP",
            "GAP_AND_GO",
            "MOMENTUM_SPIKE",
        ],
        "confirmation_bar_delta": 1,
        "allow_single_bar_confirmation": False,
        "eligibility": "caution",
    },
    "BEAR_TREND": {
        "ordinary_breakout_fit": 20.0,
        "recovery_breakout_fit": 44.0,
        "preferred_setups": ["RETEST_BREAKOUT", "RECOVERY_BREAKOUT"],
        "caution_setups": [
            "DAILY_BASE_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
            "PREMARKET_GAP",
            "GAP_AND_GO",
            "MOMENTUM_SPIKE",
        ],
        "confirmation_bar_delta": 1,
        "allow_single_bar_confirmation": False,
        "eligibility": "restricted",
    },
    "CAPITULATION_RECOVERY": {
        "ordinary_breakout_fit": 46.0,
        "recovery_breakout_fit": 82.0,
        "preferred_setups": ["RECOVERY_BREAKOUT", "RETEST_BREAKOUT"],
        "caution_setups": [
            "DAILY_BASE_BREAKOUT",
            "OPENING_RANGE_BREAKOUT",
            "PREMARKET_GAP",
            "GAP_AND_GO",
            "MOMENTUM_SPIKE",
        ],
        "confirmation_bar_delta": 1,
        "allow_single_bar_confirmation": False,
        "eligibility": "recovery_only",
    },
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _score(regime: Mapping[str, Any], key: str, default: float = 50.0) -> float:
    value = _finite(regime.get(key))
    return default if value is None else _clamp(value, 0.0, 100.0)


def _classify_state(regime: Mapping[str, Any]) -> str:
    overall = _score(regime, "score")
    trend = _score(regime, "index_trend_score")
    momentum = _score(regime, "market_momentum_score")
    breadth = _score(regime, "market_breadth_score")
    risk_on = _score(regime, "risk_on_spread_score")
    trend_evidence = regime.get("trend") if isinstance(regime.get("trend"), Mapping) else {}
    momentum_evidence = regime.get("momentum") if isinstance(regime.get("momentum"), Mapping) else {}
    risk_evidence = regime.get("risk") if isinstance(regime.get("risk"), Mapping) else {}

    spy_above_50 = bool(trend_evidence.get("spy_above_sma50"))
    spy_above_200 = bool(trend_evidence.get("spy_above_sma200"))
    long_slope_up = bool(trend_evidence.get("spy_sma200_slope_up"))
    spy_20d = _finite(momentum_evidence.get("spy_20d"))
    spy_drawdown = _finite(risk_evidence.get("spy_drawdown_50d"))
    vix = _finite(risk_evidence.get("vix"))

    recovery = (
        spy_drawdown is not None
        and spy_drawdown <= -5.0
        and spy_20d is not None
        and spy_20d > 0.0
        and momentum >= 55.0
        and (risk_on >= 52.0 or (vix is not None and vix >= 20.0))
        and trend < 70.0
    )
    if recovery:
        return "CAPITULATION_RECOVERY"
    if (
        not spy_above_200
        and not long_slope_up
        and trend <= 40.0
        and momentum <= 45.0
        and overall < 45.0
    ):
        return "BEAR_TREND"
    if (
        spy_above_50
        and spy_above_200
        and long_slope_up
        and trend >= 70.0
        and momentum >= 50.0
        and overall >= 60.0
    ):
        return "BULL_TREND"
    if (
        spy_above_200
        and long_slope_up
        and trend >= 55.0
        and (momentum < 50.0 or (spy_20d is not None and spy_20d < 0.0))
    ):
        return "BULL_PULLBACK"
    if overall >= 50.0 and breadth >= 52.0 and risk_on >= 50.0 and momentum >= 48.0:
        return "RANGE_ACCUMULATION"
    return "RANGE_DISTRIBUTION"


def _confidence_and_transition(regime: Mapping[str, Any], state: str) -> tuple[float, float]:
    values = [
        _score(regime, "index_trend_score"),
        _score(regime, "market_momentum_score"),
        _score(regime, "market_breadth_score"),
        _score(regime, "market_volume_score"),
        _score(regime, "risk_on_spread_score"),
        _score(regime, "risk_appetite_score"),
    ]
    overall = _score(regime, "score")
    distance = _clamp(abs(overall - 50.0) / 50.0)
    agreement = 1.0 - _clamp(pstdev(values) / 35.0)
    structural_bonus = 0.08 if state in {"BULL_TREND", "BEAR_TREND"} else 0.03
    confidence = _clamp(0.45 + 0.24 * distance + 0.23 * agreement + structural_bonus, 0.35, 0.95)
    mixed_state = 0.12 if state in {"BULL_PULLBACK", "CAPITULATION_RECOVERY"} else 0.0
    transition = _clamp(
        0.48 * (1.0 - distance)
        + 0.24 * (1.0 - agreement)
        + 0.18 * (1.0 - confidence)
        + mixed_state,
        0.05,
        0.95,
    )
    return round(confidence, 4), round(transition, 4)


def _state_warnings(state: str) -> list[str]:
    if state == "BULL_PULLBACK":
        return ["长期趋势仍向上，但短期动量回落；未回踩的直接追涨会降低优先级"]
    if state == "RANGE_DISTRIBUTION":
        return ["市场广度或风险偏好偏弱；突破需要额外完整K线确认"]
    if state == "BEAR_TREND":
        return ["空头环境不会隐藏全部事件，但普通新突破的市场适配度较低"]
    if state == "CAPITULATION_RECOVERY":
        return ["恐慌修复阶段只偏好收复型结构；首次反弹不按普通趋势突破处理"]
    return []


def build_market_shape(regime: Mapping[str, Any], *, as_of: datetime) -> dict[str, Any]:
    """Build the frozen MarketShapePort payload from a market-regime snapshot."""

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    required_scores = (
        "score",
        "index_trend_score",
        "market_momentum_score",
        "market_breadth_score",
        "market_volume_score",
        "risk_on_spread_score",
        "risk_appetite_score",
    )
    missing_scores = [
        name for name in required_scores if _finite(regime.get(name)) is None
    ]
    trend_evidence = (
        regime.get("trend") if isinstance(regime.get("trend"), Mapping) else {}
    )
    momentum_evidence = (
        regime.get("momentum")
        if isinstance(regime.get("momentum"), Mapping)
        else {}
    )
    risk_evidence = (
        regime.get("risk") if isinstance(regime.get("risk"), Mapping) else {}
    )
    missing_evidence = [
        name
        for name, value in {
            "spy_above_sma50": trend_evidence.get("spy_above_sma50"),
            "spy_above_sma200": trend_evidence.get("spy_above_sma200"),
            "spy_sma200_slope_up": trend_evidence.get("spy_sma200_slope_up"),
            "spy_20d": momentum_evidence.get("spy_20d"),
            "spy_drawdown_50d": risk_evidence.get("spy_drawdown_50d"),
            "vix": risk_evidence.get("vix"),
        }.items()
        if value is None
    ]
    if (
        str(regime.get("status") or "") != "active"
        or missing_scores
        or missing_evidence
    ):
        warnings = [str(item) for item in list(regime.get("warnings") or [])]
        return {
            "status": "unavailable",
            "state": None,
            "state_label": "数据不足",
            "confidence": 0.0,
            "transition_risk": None,
            "as_of": as_of.isoformat(),
            "rules": {},
            "evidence": {},
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        *(
                            ["市场核心行情不足，未生成六态形态"]
                            if not missing_scores and not missing_evidence
                            else [
                                "大盘形态缺少必要维度："
                                + ",".join([*missing_scores, *missing_evidence])
                            ]
                        ),
                    ]
                )
            ),
            "version": MARKET_SHAPE_VERSION,
        }

    state = _classify_state(regime)
    confidence, transition_risk = _confidence_and_transition(regime, state)
    rules = {**_STATE_RULES[state]}
    rules["state_label"] = STATE_LABELS[state]
    evidence = {
        "market_score": _finite(regime.get("score")),
        "trend_score": _finite(regime.get("index_trend_score")),
        "momentum_score": _finite(regime.get("market_momentum_score")),
        "breadth_score": _finite(regime.get("market_breadth_score")),
        "volume_score": _finite(regime.get("market_volume_score")),
        "risk_on_spread_score": _finite(regime.get("risk_on_spread_score")),
        "risk_appetite_score": _finite(regime.get("risk_appetite_score")),
    }
    warnings = list(
        dict.fromkeys(
            [
                *[str(item) for item in list(regime.get("warnings") or [])],
                *_state_warnings(state),
            ]
        )
    )[:6]
    return {
        "status": "active",
        "state": state,
        "state_label": STATE_LABELS[state],
        "confidence": confidence,
        "transition_risk": transition_risk,
        "as_of": as_of.isoformat(),
        "rules": rules,
        "evidence": evidence,
        "warnings": warnings,
        "version": MARKET_SHAPE_VERSION,
    }


def market_fit_for_setup(shape: Mapping[str, Any], setup_type: Any) -> float | None:
    """Return setup-specific market fit; unavailable shape remains ``None``."""

    if str(shape.get("status") or "") not in {"active", "degraded"}:
        return None
    state = str(shape.get("state") or "")
    if state not in _STATE_RULES:
        return None
    setup = str(getattr(setup_type, "value", setup_type) or "").upper()
    rules = shape.get("rules") if isinstance(shape.get("rules"), Mapping) else _STATE_RULES[state]
    recovery_setups = {"RETEST_BREAKOUT", "RECOVERY_BREAKOUT"}
    base_key = "recovery_breakout_fit" if setup in recovery_setups else "ordinary_breakout_fit"
    base = _finite(rules.get(base_key))
    if base is None:
        return None
    preferred = {str(item).upper() for item in list(rules.get("preferred_setups") or [])}
    caution = {str(item).upper() for item in list(rules.get("caution_setups") or [])}
    if setup in preferred:
        base += 6.0
    if setup in caution:
        base -= 8.0
    if setup == "MOMENTUM_SPIKE":
        base -= 8.0
    if setup == "GAP_FADE":
        base = min(base, 15.0)
    confidence = _clamp(_finite(shape.get("confidence")) or 0.0)
    fit = 50.0 + (_clamp(base, 0.0, 100.0) - 50.0) * confidence
    return round(_clamp(fit, 0.0, 100.0), 1)


def eligibility_for_setup(shape: Mapping[str, Any], setup_type: Any) -> str:
    if str(shape.get("status") or "") not in {"active", "degraded"}:
        return "unknown"
    setup = str(getattr(setup_type, "value", setup_type) or "").upper()
    rules = shape.get("rules") if isinstance(shape.get("rules"), Mapping) else {}
    if setup in {str(item).upper() for item in list(rules.get("preferred_setups") or [])}:
        return "preferred"
    if setup in {str(item).upper() for item in list(rules.get("caution_setups") or [])}:
        return "caution"
    return "allowed"


def apply_confirmation_rules(
    detection: Mapping[str, Any],
    features: Mapping[str, Any],
    shape: Mapping[str, Any],
    *,
    base_confirmation_bars: int,
) -> dict[str, Any]:
    """Apply market-state confirmation requirements without creating signals."""

    result = dict(detection)
    rules = shape.get("rules") if isinstance(shape.get("rules"), Mapping) else {}
    if str(shape.get("status") or "") not in {"active", "degraded"} or not rules:
        return result
    delta = max(0, int(rules.get("confirmation_bar_delta") or 0))
    required = max(1, int(base_confirmation_bars) + delta)
    setup = str(getattr(result.get("setup_type"), "value", result.get("setup_type")) or "")
    hold_key = "hold_bars_above_opening_range" if setup == "OPENING_RANGE_BREAKOUT" else "hold_bars_above_pivot"
    hold_bars = max(0, int(features.get(hold_key) or 0))
    allow_single = bool(rules.get("allow_single_bar_confirmation", True))
    result["market_confirmation_bars_required"] = required
    result["market_single_bar_confirmation_allowed"] = allow_single
    confirmed_by_hold = hold_bars >= required
    confirmed_by_single = bool(result.get("strong_single_bar_confirmation")) and allow_single
    if result.get("confirmed") and not (confirmed_by_hold or confirmed_by_single):
        from app.services.breakouts.models import BreakoutLifecycleState

        result["confirmed"] = False
        result["lifecycle_state"] = BreakoutLifecycleState.TRIGGERED
        result["transition_reason"] = "market_shape_requires_additional_confirmation"
        result["warnings"] = list(
            dict.fromkeys([*list(result.get("warnings") or []), "market_confirmation_tightened"])
        )
    return result
