"""Missing-aware breakout scoring with auditable contributions."""

from __future__ import annotations

import math
from typing import Any, Mapping

from app.services.breakouts.models import BreakoutScores, ScoreResult


SCORE_VERSION = "breakout-score-v1"


def _finite_score(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return max(0.0, min(100.0, number))


def weighted_score(
    components: Mapping[str, Any],
    weights: Mapping[str, float],
    qualities: Mapping[str, float] | None = None,
    min_active_weight: float = 0.25,
    *,
    score_version: str = SCORE_VERSION,
) -> ScoreResult:
    quality_map = qualities or {}
    configured = {name: max(0.0, float(weight)) for name, weight in weights.items()}
    active: dict[str, float] = {}
    values: dict[str, float] = {}
    missing: list[str] = []
    for name, weight in configured.items():
        raw = components.get(name)
        if isinstance(raw, Mapping):
            value = _finite_score(raw.get("value"))
            quality = raw.get("quality", quality_map.get(name, 1.0))
        else:
            value = _finite_score(raw)
            quality = quality_map.get(name, 1.0)
        try:
            quality_value = max(0.0, min(1.0, float(quality)))
        except (TypeError, ValueError):
            quality_value = 0.0
        if value is None or quality_value <= 0 or weight <= 0:
            missing.append(name)
            continue
        values[name] = value
        active[name] = weight * quality_value
    active_total = sum(active.values())
    configured_total = sum(configured.values()) or 1.0
    confidence = max(0.0, min(1.0, active_total / configured_total))
    if active_total < min_active_weight:
        return ScoreResult(
            score=None,
            status="insufficient_data",
            confidence=confidence,
            configured_weights=configured,
            effective_weights={},
            contribution_breakdown={},
            penalties={},
            missing_components=missing,
            score_version=score_version,
        )
    effective = {name: weight / active_total for name, weight in active.items()}
    contributions = {
        name: values[name] * effective[name] for name in effective
    }
    score = max(0.0, min(100.0, sum(contributions.values())))
    return ScoreResult(
        score=round(score, 4),
        status="active",
        confidence=round(confidence, 6),
        configured_weights={name: round(value, 6) for name, value in configured.items()},
        effective_weights={name: round(value, 6) for name, value in effective.items()},
        contribution_breakdown={
            name: round(value, 6) for name, value in contributions.items()
        },
        penalties={},
        missing_components=missing,
        score_version=score_version,
    )


def _feature_components(features: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {name: features.get(name) for name in names}


def score_breakout(
    features: Mapping[str, Any],
    *,
    intrinsic_strength: float | None = None,
    market_fit: float | None = None,
    sector_fit: float | None = None,
    strength_included_features: list[str] | None = None,
    range_persistence_adjustment: float = 0.0,
    score_version: str = SCORE_VERSION,
) -> BreakoutScores:
    base_weights = {
        "tightness_quality": 0.25,
        "duration_quality": 0.15,
        "resistance_touch_quality": 0.15,
        "volume_contraction_quality": 0.15,
        "atr_contraction_quality": 0.10,
        "support_integrity": 0.10,
        "relative_strength_structure": 0.10,
    }
    confirmation_weights = {
        "close_above_zone_quality": 0.20,
        "rvol_time_of_day_quality": 0.20,
        "close_location_quality": 0.15,
        "hold_quality": 0.15,
        "candle_body_quality": 0.10,
        "upper_wick_quality": 0.10,
        "relative_strength_confirmation": 0.10,
    }
    liquidity_weights = {
        "average_dollar_volume_quality": 0.35,
        "current_dollar_volume_quality": 0.25,
        "dollar_volume_percentile": 0.20,
        "spread_quality": 0.10,
        "intraday_completeness_quality": 0.10,
    }
    chase_weights = {
        "distance_from_pivot_risk": 0.30,
        "distance_from_vwap_risk": 0.20,
        "gap_atr_risk": 0.15,
        "upper_wick_risk": 0.15,
        "short_term_acceleration_risk": 0.10,
        "liquidity_risk": 0.10,
    }
    base = weighted_score(
        _feature_components(features, tuple(base_weights)),
        base_weights,
        score_version=score_version,
    )
    confirmation = weighted_score(
        _feature_components(features, tuple(confirmation_weights)),
        confirmation_weights,
        score_version=score_version,
    )
    adjustment = max(-4.0, min(4.0, float(range_persistence_adjustment)))
    included = set(strength_included_features or ())
    if adjustment > 0 and "range_persistence" in included:
        adjustment = 0.0
    if confirmation.score is not None and adjustment:
        adjusted = max(0.0, min(100.0, confirmation.score + adjustment))
        confirmation.score = round(adjusted, 4)
        confirmation.penalties["range_persistence_adjustment"] = round(-adjustment, 4)
    liquidity = weighted_score(
        _feature_components(features, tuple(liquidity_weights)),
        liquidity_weights,
        score_version=score_version,
    )
    chase = weighted_score(
        _feature_components(features, tuple(chase_weights)),
        chase_weights,
        score_version=score_version,
    )
    breakout_quality = weighted_score(
        {
            "base_quality_score": base.score,
            "breakout_confirmation_score": confirmation.score,
            "liquidity_quality_score": liquidity.score,
        },
        {
            "base_quality_score": 0.45,
            "breakout_confirmation_score": 0.45,
            "liquidity_quality_score": 0.10,
        },
        score_version=score_version,
    )
    data_confidence = max(
        0.0,
        min(
            100.0,
            (base.confidence + confirmation.confidence + liquidity.confidence) / 3.0 * 100.0,
        ),
    )
    priority = weighted_score(
        {
            "breakout_quality_score": breakout_quality.score,
            "intrinsic_strength_score": intrinsic_strength,
            "market_fit_score": market_fit,
            "sector_fit_score": sector_fit,
            "data_confidence_score": data_confidence,
            "event_freshness_score": features.get("event_freshness_score"),
        },
        {
            "breakout_quality_score": 0.35,
            "intrinsic_strength_score": 0.25,
            "market_fit_score": 0.15,
            "sector_fit_score": 0.10,
            "data_confidence_score": 0.10,
            "event_freshness_score": 0.05,
        },
        score_version=score_version,
    )
    if priority.score is not None and chase.score is not None:
        penalty = 0.25 * max(chase.score - 50.0, 0.0)
        priority.penalties["chase_risk"] = round(penalty, 6)
        priority.score = round(max(0.0, min(100.0, priority.score - penalty)), 4)
    return BreakoutScores(
        intrinsic_strength_score=_finite_score(intrinsic_strength),
        base_quality_score=base.score,
        breakout_confirmation_score=confirmation.score,
        liquidity_quality_score=liquidity.score,
        chase_risk_score=chase.score,
        sector_fit_score=_finite_score(sector_fit),
        market_fit_score=_finite_score(market_fit),
        breakout_quality_score=breakout_quality.score,
        alert_priority_score=priority.score,
        data_confidence_score=round(data_confidence, 4),
        details={
            "base_quality": base,
            "breakout_confirmation": confirmation,
            "liquidity_quality": liquidity,
            "chase_risk": chase,
            "breakout_quality": breakout_quality,
            "alert_priority": priority,
        },
    )
