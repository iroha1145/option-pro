"""Deterministic six-state market-shape classification and breakout fit rules.

The existing market-regime service already measures trend, momentum, breadth,
volume and risk appetite.  This module turns those independent measurements
into the six-state contract used by Breakout Radar without feeding market
context back into a stock's intrinsic-strength score.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import pstdev
from typing import Any, Mapping, Sequence


MARKET_SHAPE_VERSION = (
    os.environ.get("MARKET_SHAPE_VERSION", "market-shape-v3").strip()
    or "market-shape-v3"
)
HYSTERESIS_VERSION = "market-shape-hysteresis-v1"


def _env_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class MarketShapeHysteresisConfig:
    """Engineering bootstrap values for deterministic market-state replay."""

    enter_confirm_days: int = 2
    exit_confirm_days: int = 2
    min_dwell_days: int = 3
    history_days: int = 20
    hysteresis_version: str = HYSTERESIS_VERSION

    @classmethod
    def from_env(cls) -> "MarketShapeHysteresisConfig":
        return cls(
            enter_confirm_days=_env_int(
                "MARKET_SHAPE_ENTER_CONFIRM_DAYS", 2, minimum=1, maximum=10
            ),
            exit_confirm_days=_env_int(
                "MARKET_SHAPE_EXIT_CONFIRM_DAYS", 2, minimum=1, maximum=10
            ),
            min_dwell_days=_env_int(
                "MARKET_SHAPE_MIN_DWELL_DAYS", 3, minimum=1, maximum=20
            ),
            history_days=_env_int(
                "MARKET_SHAPE_HISTORY_DAYS", 20, minimum=5, maximum=120
            ),
        )


# Centralized, versioned emergency override. These are conservative engineering
# defaults, not statistically optimized trading parameters.
EMERGENCY_BEAR_CONFIG: dict[str, Any] = {
    "version": "market-shape-emergency-v1",
    "overall_max": 30.0,
    "trend_max": 30.0,
    "momentum_max": 35.0,
    "risk_support_max": 38.0,
}

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


def _score(regime: Mapping[str, Any], key: str) -> float | None:
    value = _finite(regime.get(key))
    return None if value is None else _clamp(value, 0.0, 100.0)


def _hard_missing(regime: Mapping[str, Any]) -> list[str]:
    explicit = regime.get("hard_missing")
    missing: list[str] = []
    if isinstance(explicit, Sequence) and not isinstance(explicit, (str, bytes)):
        missing.extend(str(item) for item in explicit if str(item))
    missing.extend(
        key
        for key in (
            "score",
            "index_trend_score",
            "market_momentum_score",
            "market_breadth_score",
        )
        if _finite(regime.get(key)) is None
    )
    trend = regime.get("trend") if isinstance(regime.get("trend"), Mapping) else {}
    momentum = (
        regime.get("momentum")
        if isinstance(regime.get("momentum"), Mapping)
        else {}
    )
    for name, value in {
        "spy_above_sma200": trend.get("spy_above_sma200"),
        "spy_sma200_slope_up": trend.get("spy_sma200_slope_up"),
        "spy_20d": momentum.get("spy_20d"),
    }.items():
        if value is None:
            missing.append(name)
    return list(dict.fromkeys(missing))


def _optional_missing(regime: Mapping[str, Any]) -> list[str]:
    value = regime.get("optional_missing")
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(dict.fromkeys(str(item) for item in value if str(item)))
    inferred = [
        name
        for name, key in (
            ("market_volume", "market_volume_score"),
            ("risk_on_spreads", "risk_on_spread_score"),
            ("risk_appetite", "risk_appetite_score"),
        )
        if _finite(regime.get(key)) is None
    ]
    return inferred


def _coverage_ratio(regime: Mapping[str, Any]) -> float:
    coverage = regime.get("input_coverage")
    if isinstance(coverage, Mapping):
        value = _finite(coverage.get("ratio"))
    else:
        value = _finite(coverage)
    if value is not None:
        return _clamp(value)
    keys = (
        "index_trend_score",
        "market_momentum_score",
        "market_breadth_score",
        "market_volume_score",
        "risk_on_spread_score",
        "risk_appetite_score",
    )
    return sum(_finite(regime.get(key)) is not None for key in keys) / len(keys)


def _classify_state(regime: Mapping[str, Any]) -> str:
    overall = _score(regime, "score")
    trend = _score(regime, "index_trend_score")
    momentum = _score(regime, "market_momentum_score")
    breadth = _score(regime, "market_breadth_score")
    risk_on = _score(regime, "risk_on_spread_score")
    if overall is None or trend is None or momentum is None or breadth is None:
        raise ValueError("core market-shape inputs are unavailable")
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
        and (
            (risk_on is not None and risk_on >= 52.0)
            or (vix is not None and vix >= 20.0)
        )
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
    if (
        overall >= 50.0
        and breadth >= 52.0
        and momentum >= 48.0
        and (risk_on is None or risk_on >= 50.0)
    ):
        return "RANGE_ACCUMULATION"
    return "RANGE_DISTRIBUTION"


def _state_instability(regime: Mapping[str, Any]) -> float:
    values = [
        value
        for value in (
            _score(regime, "index_trend_score"),
            _score(regime, "market_momentum_score"),
            _score(regime, "market_breadth_score"),
            _score(regime, "market_volume_score"),
            _score(regime, "risk_on_spread_score"),
            _score(regime, "risk_appetite_score"),
        )
        if value is not None
    ]
    if len(values) < 2:
        return 1.0
    return round(_clamp(pstdev(values) / 35.0), 4)


def _confidence(regime: Mapping[str, Any], state: str) -> float:
    overall = _score(regime, "score")
    if overall is None:
        return 0.0
    distance = _clamp(abs(overall - 50.0) / 50.0)
    agreement = 1.0 - _state_instability(regime)
    coverage = _coverage_ratio(regime)
    structural_bonus = 0.08 if state in {"BULL_TREND", "BEAR_TREND"} else 0.03
    value = _clamp(
        0.24 + 0.22 * distance + 0.27 * agreement + 0.19 * coverage + structural_bonus,
        0.20,
        0.95,
    )
    if str(regime.get("status") or "") == "degraded":
        value *= 0.84
    return round(_clamp(value, 0.15, 0.95), 4)


def _parse_as_of(value: Any, fallback: datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = fallback
    else:
        parsed = fallback
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _is_emergency_bear(regime: Mapping[str, Any], raw_state: str) -> bool:
    if raw_state != "BEAR_TREND":
        return False
    trend_evidence = regime.get("trend") if isinstance(regime.get("trend"), Mapping) else {}
    risk_on = _score(regime, "risk_on_spread_score")
    risk_appetite = _score(regime, "risk_appetite_score")
    # Emergency entry is deliberately stricter than the ordinary state
    # machine: both independent risk groups must exist and deteriorate
    # together.  One weak optional source cannot bypass confirmation/dwell.
    risk_support = (
        max(risk_on, risk_appetite)
        if risk_on is not None and risk_appetite is not None
        else None
    )
    overall = _score(regime, "score")
    trend = _score(regime, "index_trend_score")
    momentum = _score(regime, "market_momentum_score")
    return bool(
        trend_evidence.get("spy_above_sma200") is False
        and trend_evidence.get("spy_sma200_slope_up") is False
        and overall is not None
        and overall <= float(EMERGENCY_BEAR_CONFIG["overall_max"])
        and trend is not None
        and trend <= float(EMERGENCY_BEAR_CONFIG["trend_max"])
        and momentum is not None
        and momentum <= float(EMERGENCY_BEAR_CONFIG["momentum_max"])
        and risk_support is not None
        and risk_support <= float(EMERGENCY_BEAR_CONFIG["risk_support_max"])
    )


def replay_market_shape(
    snapshots: Sequence[Mapping[str, Any]],
    *,
    config: MarketShapeHysteresisConfig | None = None,
    fallback_as_of: datetime | None = None,
) -> dict[str, Any]:
    """Replay raw daily states without process memory or future observations."""

    cfg = config or MarketShapeHysteresisConfig.from_env()
    fallback = fallback_as_of or datetime.now(timezone.utc)
    if fallback.tzinfo is None or fallback.utcoffset() is None:
        raise ValueError("fallback_as_of must include a timezone")

    stable_state: str | None = None
    previous_state: str | None = None
    entered_at: datetime | None = None
    pending_state: str | None = None
    pending_days = 0
    days_in_state = 0
    raw_state: str | None = None
    emergency_override = False
    processed = 0

    for item in snapshots[-cfg.history_days :]:
        regime = item.get("regime") if isinstance(item.get("regime"), Mapping) else item
        if not isinstance(regime, Mapping) or _hard_missing(regime):
            continue
        observed_at = _parse_as_of(item.get("as_of") or regime.get("as_of"), fallback)
        raw_state = _classify_state(regime)
        processed += 1
        if stable_state is None:
            stable_state = raw_state
            entered_at = observed_at
            days_in_state = 1
            continue

        days_in_state += 1
        if raw_state == stable_state:
            pending_state = None
            pending_days = 0
            continue

        if _is_emergency_bear(regime, raw_state):
            previous_state = stable_state
            stable_state = raw_state
            entered_at = observed_at
            days_in_state = 1
            pending_state = None
            pending_days = 0
            emergency_override = True
            continue

        if pending_state != raw_state:
            pending_state = raw_state
            pending_days = 1
        else:
            pending_days += 1
        confirmation_days = max(cfg.enter_confirm_days, cfg.exit_confirm_days)
        if pending_days >= confirmation_days and days_in_state > cfg.min_dwell_days:
            previous_state = stable_state
            stable_state = raw_state
            entered_at = observed_at
            days_in_state = 1
            pending_state = None
            pending_days = 0
            emergency_override = False

    return {
        "raw_state": raw_state,
        "state": stable_state,
        "previous_state": previous_state,
        "entered_at": entered_at.isoformat() if entered_at is not None else None,
        "days_in_state": days_in_state if stable_state is not None else 0,
        "pending_state": pending_state,
        "pending_days": pending_days,
        "emergency_override": emergency_override,
        "processed_days": processed,
        "hysteresis_version": cfg.hysteresis_version,
        "enter_confirm_days": cfg.enter_confirm_days,
        "exit_confirm_days": cfg.exit_confirm_days,
        "min_dwell_days": cfg.min_dwell_days,
        "history_days": cfg.history_days,
        "emergency_version": EMERGENCY_BEAR_CONFIG["version"],
    }


def _transition_risk(
    regime: Mapping[str, Any],
    replay: Mapping[str, Any],
    *,
    config: MarketShapeHysteresisConfig,
) -> float:
    overall = _score(regime, "score")
    boundary_distance = (
        min(abs(overall - boundary) for boundary in (40.0, 45.0, 50.0, 60.0, 75.0))
        if overall is not None
        else 0.0
    )
    boundary_proximity = 1.0 - _clamp(boundary_distance / 15.0)
    raw_differs = float(
        replay.get("raw_state") is not None
        and replay.get("raw_state") != replay.get("state")
    )
    confirm_days = max(config.enter_confirm_days, config.exit_confirm_days)
    pending_progress = _clamp(float(replay.get("pending_days") or 0) / confirm_days)
    instability = _state_instability(regime)
    days_in_state = max(0, int(replay.get("days_in_state") or 0))
    dwell_maturity = _clamp(days_in_state / max(1, config.min_dwell_days))
    mixed_state = 1.0 if replay.get("raw_state") in {
        "BULL_PULLBACK",
        "CAPITULATION_RECOVERY",
    } else 0.0
    value = (
        0.30 * raw_differs
        + 0.25 * pending_progress
        + 0.18 * boundary_proximity
        + 0.17 * instability
        + 0.06 * dwell_maturity
        + 0.04 * mixed_state
    )
    return round(_clamp(value, 0.02, 0.98), 4)


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


def build_market_shape(
    regime: Mapping[str, Any],
    *,
    as_of: datetime,
    history: Sequence[Mapping[str, Any]] | None = None,
    config: MarketShapeHysteresisConfig | None = None,
) -> dict[str, Any]:
    """Build a six-state payload from core inputs and point-in-time history.

    ``history`` contains prior daily snapshots only. The current ``regime`` is
    appended here, preventing callers from accidentally applying a future row.
    """

    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    cfg = config or MarketShapeHysteresisConfig.from_env()
    hard_missing = _hard_missing(regime)
    optional_missing = _optional_missing(regime)
    input_coverage = regime.get("input_coverage") or {
        "ratio": round(_coverage_ratio(regime), 4)
    }
    active_groups = [str(item) for item in list(regime.get("active_groups") or [])]
    degraded_reasons = [
        str(item) for item in list(regime.get("degraded_reasons") or [])
    ]
    if hard_missing:
        warnings = [str(item) for item in list(regime.get("warnings") or [])]
        return {
            "status": "unavailable",
            "state": None,
            "raw_state": None,
            "previous_state": None,
            "state_label": "数据不足",
            "confidence": 0.0,
            "transition_risk": None,
            "transition_risk_semantics": "state_change_pressure_not_probability",
            "state_instability_score": None,
            "entered_at": None,
            "days_in_state": 0,
            "pending_state": None,
            "pending_days": 0,
            "as_of": as_of.isoformat(),
            "rules": {},
            "evidence": {},
            "input_coverage": input_coverage,
            "hard_missing": hard_missing,
            "optional_missing": optional_missing,
            "active_groups": active_groups,
            "degraded_reasons": degraded_reasons,
            "warnings": list(
                dict.fromkeys(
                    [
                        *warnings,
                        "大盘形态缺少核心维度：" + ",".join(hard_missing),
                    ]
                )
            )[:8],
            "version": MARKET_SHAPE_VERSION,
            "hysteresis_version": cfg.hysteresis_version,
        }

    prior_history = [
        item
        for item in list(history or [])
        if _parse_as_of(
            item.get("as_of")
            or (
                item.get("regime", {}).get("as_of")
                if isinstance(item.get("regime"), Mapping)
                else None
            ),
            as_of,
        )
        < as_of
    ]
    replay = replay_market_shape(
        [
            *prior_history,
            {"as_of": as_of, "regime": regime},
        ],
        config=cfg,
        fallback_as_of=as_of,
    )
    state = str(replay.get("state") or _classify_state(regime))
    raw_state = str(replay.get("raw_state") or state)
    confidence = _confidence(regime, state)
    transition_risk = _transition_risk(regime, replay, config=cfg)
    instability = _state_instability(regime)
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
    status = (
        "active"
        if str(regime.get("status") or "") == "active" and not optional_missing
        else "degraded"
    )
    warnings = list(
        dict.fromkeys(
            [
                *[str(item) for item in list(regime.get("warnings") or [])],
                *(
                    ["可选风险数据不完整，六态形态按较低置信度输出"]
                    if status == "degraded"
                    else []
                ),
                *_state_warnings(state),
            ]
        )
    )[:8]
    return {
        "status": status,
        "state": state,
        "raw_state": raw_state,
        "previous_state": replay.get("previous_state"),
        "state_label": STATE_LABELS[state],
        "confidence": confidence,
        "transition_risk": transition_risk,
        "transition_risk_semantics": "state_change_pressure_not_probability",
        "state_instability_score": instability,
        "entered_at": replay.get("entered_at"),
        "days_in_state": replay.get("days_in_state", 0),
        "pending_state": replay.get("pending_state"),
        "pending_days": replay.get("pending_days", 0),
        "as_of": as_of.isoformat(),
        "rules": rules,
        "evidence": evidence,
        "input_coverage": input_coverage,
        "hard_missing": hard_missing,
        "optional_missing": optional_missing,
        "active_groups": active_groups,
        "degraded_reasons": degraded_reasons,
        "emergency_override": bool(replay.get("emergency_override")),
        "emergency_version": replay.get("emergency_version"),
        "warnings": warnings,
        "version": MARKET_SHAPE_VERSION,
        "hysteresis_version": cfg.hysteresis_version,
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
