from __future__ import annotations

import math

from app.services.breakouts.scoring import score_breakout, weighted_score


def _features(value: float = 80.0) -> dict[str, float]:
    names = [
        "tightness_quality",
        "duration_quality",
        "resistance_touch_quality",
        "volume_contraction_quality",
        "atr_contraction_quality",
        "support_integrity",
        "relative_strength_structure",
        "close_above_zone_quality",
        "rvol_time_of_day_quality",
        "close_location_quality",
        "hold_quality",
        "candle_body_quality",
        "upper_wick_quality",
        "relative_strength_confirmation",
        "average_dollar_volume_quality",
        "current_dollar_volume_quality",
        "dollar_volume_percentile",
        "spread_quality",
        "intraday_completeness_quality",
        "distance_from_pivot_risk",
        "distance_from_vwap_risk",
        "gap_atr_risk",
        "upper_wick_risk",
        "short_term_acceleration_risk",
        "liquidity_risk",
        "event_freshness_score",
    ]
    return {name: value for name in names}


def test_missing_component_is_not_replaced_with_fifty() -> None:
    result = weighted_score(
        {"known": 100.0, "missing": None},
        {"known": 0.5, "missing": 0.5},
    )
    assert result.score == 100.0
    assert result.effective_weights == {"known": 1.0}
    assert "missing" in result.missing_components


def test_active_weight_below_threshold_returns_null() -> None:
    result = weighted_score(
        {"known": 80.0, "missing": None},
        {"known": 0.1, "missing": 0.9},
        min_active_weight=0.25,
    )
    assert result.score is None
    assert result.status == "insufficient_data"


def test_chase_risk_does_not_reduce_breakout_quality() -> None:
    low = _features(80)
    high = dict(low)
    for name in (
        "distance_from_pivot_risk",
        "distance_from_vwap_risk",
        "gap_atr_risk",
        "upper_wick_risk",
        "short_term_acceleration_risk",
        "liquidity_risk",
    ):
        low[name] = 10
        high[name] = 100
    low_scores = score_breakout(low, intrinsic_strength=75)
    high_scores = score_breakout(high, intrinsic_strength=75)
    assert low_scores.breakout_quality_score == high_scores.breakout_quality_score
    assert high_scores.chase_risk_score > low_scores.chase_risk_score
    assert high_scores.alert_priority_score < low_scores.alert_priority_score


def test_market_shape_only_changes_market_fit_and_priority() -> None:
    features = _features()
    missing = score_breakout(features, intrinsic_strength=75, market_fit=None)
    strong = score_breakout(features, intrinsic_strength=75, market_fit=90)
    assert missing.base_quality_score == strong.base_quality_score
    assert missing.breakout_confirmation_score == strong.breakout_confirmation_score
    assert missing.intrinsic_strength_score == strong.intrinsic_strength_score
    assert missing.market_fit_score is None
    assert strong.alert_priority_score != missing.alert_priority_score


def test_priority_contributions_and_penalty_reconcile() -> None:
    scores = score_breakout(_features(90), intrinsic_strength=80, market_fit=70)
    detail = scores.details["alert_priority"]
    total = sum(detail.contribution_breakdown.values()) - sum(detail.penalties.values())
    assert abs(total - scores.alert_priority_score) <= 0.1
    for value in scores.model_dump().values():
        if isinstance(value, float):
            assert math.isfinite(value)


def test_range_adjustment_is_capped_and_not_double_counted() -> None:
    features = _features(70)
    adjusted = score_breakout(
        features,
        intrinsic_strength=75,
        range_persistence_adjustment=99,
    )
    blocked = score_breakout(
        features,
        intrinsic_strength=75,
        strength_included_features=["range_persistence"],
        range_persistence_adjustment=4,
    )
    base = score_breakout(features, intrinsic_strength=75)
    assert adjusted.breakout_confirmation_score - base.breakout_confirmation_score <= 4
    assert blocked.breakout_confirmation_score == base.breakout_confirmation_score
