"""Canonical, missing-aware scoring for the Strength Radar.

The module deliberately keeps four concepts separate:

* ``intrinsic`` uses only the stock's own price/volume history and SPY-relative
  price history;
* ``market_fit`` describes the current market backdrop;
* ``profile_fit`` describes suitability for the requested risk profile;
* ``ranking`` combines the three for the selected view.

Cross-sectional ranks, options data and view filters never enter the intrinsic
score.  This makes the same stock reproducible across API entry points.
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

from app.services.technical.range_persistence import (
    RANGE_PERSISTENCE_VERSION,
    build_range_persistence_shadow,
)


SCORE_VERSION = "strength-v2"
FEATURE_VERSION = "strength-features-v2"
NORMALIZATION_VERSION = "strength-normalization-v1"


def finite_number(value: Any, *, lo: float | None = None, hi: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    if lo is not None:
        number = max(lo, number)
    if hi is not None:
        number = min(hi, number)
    return number


def score_value(value: Any) -> float | None:
    return finite_number(value, lo=0.0, hi=100.0)


def weighted_available(
    components: Mapping[str, Any],
    weights: Mapping[str, float],
    qualities: Mapping[str, float] | None = None,
    *,
    min_active_weight: float = 0.25,
    score_version: str = SCORE_VERSION,
) -> dict[str, Any]:
    """Aggregate only finite inputs and expose the complete weighting audit.

    ``confidence`` is the fraction of configured weight backed by usable data,
    after optional quality discounts.  Unknown inputs have zero active weight;
    they are never converted to a neutral score.
    """

    configured = {
        str(name): max(0.0, finite_number(weight) or 0.0)
        for name, weight in weights.items()
    }
    quality_map = qualities or {}
    values: dict[str, float] = {}
    active_weights: dict[str, float] = {}
    missing: list[str] = []

    for name, configured_weight in configured.items():
        raw = components.get(name)
        if isinstance(raw, Mapping):
            value = score_value(raw.get("value"))
            raw_quality = raw.get("quality", quality_map.get(name, 1.0))
        else:
            value = score_value(raw)
            raw_quality = quality_map.get(name, 1.0)
        quality = finite_number(raw_quality, lo=0.0, hi=1.0)
        if value is None or quality is None or quality <= 0 or configured_weight <= 0:
            missing.append(name)
            continue
        values[name] = value
        active_weights[name] = configured_weight * quality

    configured_total = sum(configured.values())
    active_total = sum(active_weights.values())
    confidence = active_total / configured_total if configured_total > 0 else 0.0
    result = {
        "score": None,
        "status": "insufficient_data",
        "confidence": round(max(0.0, min(1.0, confidence)), 6),
        "configured_weights": {
            name: round(weight, 6) for name, weight in configured.items()
        },
        "effective_weights": {},
        "contributions": {},
        "contribution_breakdown": {},
        "missing_components": missing,
        "active_weight": round(active_total, 6),
        "score_version": score_version,
    }
    if active_total <= 0 or active_total < max(0.0, float(min_active_weight)):
        return result

    effective = {
        name: weight / active_total for name, weight in active_weights.items()
    }
    contributions = {
        name: values[name] * effective[name] for name in effective
    }
    final = max(0.0, min(100.0, sum(contributions.values())))
    rounded_contributions = {
        name: round(value, 6) for name, value in contributions.items()
    }
    return {
        **result,
        "score": round(final, 4),
        "status": "active",
        "effective_weights": {
            name: round(weight, 6) for name, weight in effective.items()
        },
        "contributions": rounded_contributions,
        "contribution_breakdown": rounded_contributions,
    }


def absolute_return_score(value: Any, scale: float) -> float | None:
    number = finite_number(value)
    if number is None:
        return None
    return round(max(0.0, min(100.0, 50.0 + number * scale)), 4)


def rsi_score(value: Any) -> float | None:
    number = finite_number(value, lo=0.0, hi=100.0)
    if number is None:
        return None
    if 50 <= number <= 68:
        return round(min(100.0, 58.0 + (number - 50.0) * 1.7), 4)
    if 68 < number <= 78:
        return round(max(0.0, 88.0 - (number - 68.0) * 2.2), 4)
    if number < 50:
        return round(max(0.0, min(100.0, 42.0 + (number - 35.0) * 1.1)), 4)
    return round(max(0.0, 50.0 - (number - 78.0) * 1.5), 4)


def relative_volume_score(value: Any) -> float | None:
    number = finite_number(value, lo=0.0)
    if number is None:
        return None
    return round(max(0.0, min(100.0, 50.0 + (number - 1.0) * 35.0)), 4)


def _close_series(hist: pd.DataFrame | None) -> pd.Series:
    if hist is None or hist.empty or "Close" not in hist.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(hist["Close"], errors="coerce").dropna()


def _trend_efficiency(close: pd.Series, days: int = 63) -> float | None:
    if len(close) < days + 1:
        return None
    window = close.tail(days + 1)
    path = finite_number(window.diff().abs().sum(), lo=0.0)
    if path is None or path <= 0:
        return None
    signed = float(window.iloc[-1] - window.iloc[0]) / path
    return round(max(0.0, min(100.0, 50.0 + signed * 50.0)), 4)


def _moving_average_slope(close: pd.Series) -> float | None:
    if len(close) < 70:
        return None
    average = close.rolling(50).mean().dropna()
    if len(average) < 21 or average.iloc[-21] <= 0:
        return None
    return absolute_return_score(float(average.iloc[-1] / average.iloc[-21] - 1.0), 500.0)


def _trend_stability(close: pd.Series) -> float | None:
    if len(close) < 21:
        return None
    volatility = finite_number(close.pct_change().tail(20).std(ddof=0), lo=0.0)
    if volatility is None:
        return None
    return round(max(0.0, min(100.0, 100.0 - volatility * 2200.0)), 4)


def _active_price_action(row: Mapping[str, Any]) -> float | None:
    payload = row.get("price_action")
    if not isinstance(payload, Mapping) or payload.get("status") != "active":
        return None
    return score_value(payload.get("score"))


def _range_component(range_feature: Mapping[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    active = range_feature.get("status") == "active"
    level = (
        score_value(
            range_feature.get("range_persistence_normalized_score")
            if range_feature.get("range_persistence_normalized_score") is not None
            else range_feature.get("range_persistence")
        )
        if active
        else None
    )
    slope = (
        absolute_return_score(range_feature.get("range_persistence_slope_5d"), 5.0)
        if active
        else None
    )
    ratio = score_value(range_feature.get("range_persistence_ratio_10d")) if active else None
    components = {
        "persistence_level": level,
        "slope_5d": slope,
        "ratio_10d": ratio,
    }
    result = weighted_available(
        components,
        {"persistence_level": 0.50, "slope_5d": 0.30, "ratio_10d": 0.20},
        min_active_weight=0.50,
        score_version=SCORE_VERSION,
    )
    return result, components


def _factor_result(
    components: Mapping[str, Any],
    weights: Mapping[str, float],
    *,
    min_active_weight: float,
) -> dict[str, Any]:
    return weighted_available(
        components,
        weights,
        min_active_weight=min_active_weight,
        score_version=SCORE_VERSION,
    )


def score_intrinsic(
    row: Mapping[str, Any],
    hist: pd.DataFrame | None,
    *,
    range_feature: Mapping[str, Any] | None = None,
    range_mode: str = "shadow",
    range_trend_weight: float = 0.15,
    range_final_cap: float = 0.04,
) -> dict[str, Any]:
    """Score stock-intrinsic evidence without market, options or peer ranks."""

    range_payload = dict(range_feature or {"status": "disabled", "version": RANGE_PERSISTENCE_VERSION})
    close = _close_series(hist)
    price_action = _active_price_action(row)

    short_components = {
        "return_5d": absolute_return_score(row.get("return_5d"), 500.0),
        "return_20d": absolute_return_score(row.get("return_20d"), 300.0),
        "relative_volume": relative_volume_score(row.get("rel_volume")),
        "distance_sma20": absolute_return_score(row.get("dist_sma20"), 400.0),
        "rsi14": rsi_score(row.get("rsi14")),
    }
    short = _factor_result(
        short_components,
        {
            "return_5d": 0.20,
            "return_20d": 0.25,
            "relative_volume": 0.15,
            "distance_sma20": 0.20,
            "rsi14": 0.20,
        },
        min_active_weight=0.45,
    )

    mid_components = {
        "return_63d": absolute_return_score(row.get("return_63d"), 180.0),
        "relative_strength_spy_63d": absolute_return_score(row.get("rs_spy_63d"), 220.0),
        "ma_alignment": score_value(row.get("ma_alignment")),
        # scanner stores MACD change in percentage points, not a ratio.
        "macd_direction": absolute_return_score(row.get("macd_direction"), 5.0),
        "distance_sma50": absolute_return_score(row.get("dist_sma50"), 320.0),
    }
    mid = _factor_result(
        mid_components,
        {
            "return_63d": 0.28,
            "relative_strength_spy_63d": 0.27,
            "ma_alignment": 0.20,
            "macd_direction": 0.10,
            "distance_sma50": 0.15,
        },
        min_active_weight=0.45,
    )

    long_components = {
        "return_126d": absolute_return_score(row.get("return_126d"), 130.0),
        "return_252d": absolute_return_score(row.get("return_252d"), 90.0),
        "distance_sma200": absolute_return_score(row.get("dist_sma200"), 260.0),
        "ath_proximity": score_value(row.get("ath_proximity")),
        "ma_alignment": score_value(row.get("ma_alignment")),
    }
    long = _factor_result(
        long_components,
        {
            "return_126d": 0.26,
            "return_252d": 0.22,
            "distance_sma200": 0.24,
            "ath_proximity": 0.18,
            "ma_alignment": 0.10,
        },
        min_active_weight=0.45,
    )

    base_breakout = weighted_available(
        {
            "ath_proximity": score_value(row.get("ath_proximity")),
            "return_20d": absolute_return_score(row.get("return_20d"), 300.0),
            "price_action": price_action,
        },
        {"ath_proximity": 0.40, "return_20d": 0.40, "price_action": 0.20},
        min_active_weight=0.40,
        score_version=SCORE_VERSION,
    )
    breakout_score = base_breakout["score"]
    volume_truth = row.get("vol_price_match") if isinstance(row.get("vol_price_match"), Mapping) else {}
    adjustment = finite_number(volume_truth.get("breakout_quality_adjustment"))
    false_breakout = finite_number(volume_truth.get("false_breakout_risk"), lo=0.0)
    if breakout_score is not None:
        breakout_score = max(
            0.0,
            min(100.0, breakout_score + (adjustment or 0.0) - (false_breakout or 0.0)),
        )
        if volume_truth.get("setup_type") == "vacuum" and not row.get("follow_through"):
            breakout_score = min(breakout_score, 65.0)
        if volume_truth.get("setup_type") == "absorption_bearish" and not row.get("breakout_confirmed"):
            breakout_score = min(breakout_score, 55.0)
        breakout_score = round(breakout_score, 4)

    range_result, range_components = _range_component(range_payload)
    configured_range_weight = max(0.0, min(0.15, finite_number(range_trend_weight) or 0.0))
    production_trend_weights = {
        "medium_term_momentum": 0.50,
        "trend_efficiency": 0.25,
        "moving_average_slope": 0.20,
        "trend_stability": 0.05,
    }
    hypothetical_trend_weights = {
        "medium_term_momentum": 0.35 + (0.15 - configured_range_weight),
        "trend_efficiency": 0.25,
        "moving_average_slope": 0.20,
        "range_persistence_component": configured_range_weight,
        "trend_stability": 0.05,
    }
    trend_components = {
        "medium_term_momentum": absolute_return_score(row.get("return_63d"), 180.0),
        "trend_efficiency": _trend_efficiency(close),
        "moving_average_slope": _moving_average_slope(close),
        "range_persistence_component": range_result.get("score"),
        "trend_stability": _trend_stability(close),
    }
    production_trend_components = {**trend_components, "range_persistence_component": None}
    production_trend = _factor_result(
        production_trend_components,
        production_trend_weights,
        min_active_weight=0.45,
    )
    hypothetical_trend = _factor_result(
        trend_components,
        hypothetical_trend_weights,
        min_active_weight=0.45,
    )

    family_weights = {
        "short": 0.16,
        "mid": 0.24,
        "long": 0.14,
        "trend": 0.16,
        "breakout": 0.15,
        "price_action": 0.15,
    }
    production_families = {
        "short": short.get("score"),
        "mid": mid.get("score"),
        "long": long.get("score"),
        "trend": production_trend.get("score"),
        "breakout": breakout_score,
        "price_action": price_action,
    }
    family_qualities = {
        "short": short.get("confidence", 0.0),
        "mid": mid.get("confidence", 0.0),
        "long": long.get("confidence", 0.0),
        "trend": production_trend.get("confidence", 0.0),
        "breakout": base_breakout.get("confidence", 0.0),
        "price_action": 1.0 if price_action is not None else 0.0,
    }
    production = weighted_available(
        production_families,
        family_weights,
        family_qualities,
        min_active_weight=0.25,
        score_version=SCORE_VERSION,
    )

    hypothetical_families = {
        **production_families,
        "trend": hypothetical_trend.get("score"),
    }
    hypothetical_qualities = {
        **family_qualities,
        "trend": hypothetical_trend.get("confidence", 0.0),
    }
    hypothetical = weighted_available(
        hypothetical_families,
        family_weights,
        hypothetical_qualities,
        min_active_weight=0.25,
        score_version=SCORE_VERSION,
    )
    unbounded_weight = (
        float(hypothetical.get("effective_weights", {}).get("trend", 0.0))
        * float(hypothetical_trend.get("effective_weights", {}).get("range_persistence_component", 0.0))
    )
    final_cap = max(0.0, min(0.04, finite_number(range_final_cap) or 0.0))
    applied_range_weight = min(unbounded_weight, final_cap)
    production_score = production.get("score")
    raw_hypothetical_score = hypothetical.get("score")
    hypothetical_score = production_score
    if production_score is not None and raw_hypothetical_score is not None and unbounded_weight > 0:
        scale = min(1.0, final_cap / unbounded_weight) if final_cap > 0 else 0.0
        hypothetical_score = round(
            production_score + (raw_hypothetical_score - production_score) * scale,
            4,
        )
    elif raw_hypothetical_score is None:
        hypothetical_score = None

    selected_score = hypothetical_score if range_mode == "enabled" else production_score
    shadow = build_range_persistence_shadow(
        mode=range_mode if range_mode in {"disabled", "shadow", "enabled"} else "shadow",
        production_score=production_score,
        hypothetical_score=hypothetical_score,
        effective_weight=applied_range_weight,
        final_weight_cap=final_cap,
        version=str(range_payload.get("version") or RANGE_PERSISTENCE_VERSION),
    )

    selected_contributions = dict(production.get("contributions") or {})
    selected_effective = dict(production.get("effective_weights") or {})
    if range_mode == "enabled" and selected_score is not None:
        selected_contributions = dict(hypothetical.get("contributions") or {})
        selected_effective = dict(hypothetical.get("effective_weights") or {})
        other_total = sum(
            value for name, value in selected_contributions.items() if name != "trend"
        )
        if "trend" in selected_effective:
            selected_contributions["trend"] = round(selected_score - other_total, 6)

    raw_missing = [
        name
        for name, value in {
            "return_5d": row.get("return_5d"),
            "return_20d": row.get("return_20d"),
            "return_63d": row.get("return_63d"),
            "return_126d": row.get("return_126d"),
            "return_252d": row.get("return_252d"),
            "rs_spy_63d": row.get("rs_spy_63d"),
            "dist_sma20": row.get("dist_sma20"),
            "dist_sma50": row.get("dist_sma50"),
            "dist_sma200": row.get("dist_sma200"),
            "rsi14": row.get("rsi14"),
            "macd_direction": row.get("macd_direction"),
            "rel_volume": row.get("rel_volume"),
            "ath_proximity": row.get("ath_proximity"),
            "price_action": price_action,
            "trend_efficiency": trend_components.get("trend_efficiency"),
            "moving_average_slope": trend_components.get("moving_average_slope"),
            "trend_stability": trend_components.get("trend_stability"),
        }.items()
        if finite_number(value) is None
    ]
    missing_components = list(dict.fromkeys(raw_missing))
    included_features = [name for name in (
        "return_5d",
        "return_20d",
        "return_63d",
        "return_126d",
        "return_252d",
        "rs_spy_63d",
        "dist_sma20",
        "dist_sma50",
        "dist_sma200",
        "rsi14",
        "macd_direction",
        "rel_volume",
        "ath_proximity",
    ) if finite_number(row.get(name)) is not None]
    if finite_number(row.get("return_20d")) is not None:
        included_features.append("momentum_20d")
    if finite_number(row.get("return_63d")) is not None:
        included_features.append("medium_term_momentum_63d")
    included_features.extend(
        name
        for name, value in {
            "trend_efficiency": trend_components.get("trend_efficiency"),
            "moving_average_slope": trend_components.get("moving_average_slope"),
            "trend_stability": trend_components.get("trend_stability"),
            "price_action": price_action,
        }.items()
        if value is not None
    )
    if range_mode == "enabled" and range_result.get("score") is not None:
        included_features.append("range_persistence")

    status = "active" if selected_score is not None else "insufficient_data"
    confidence = float(production.get("confidence") or 0.0)
    return {
        "score": round(selected_score, 1) if selected_score is not None else None,
        "status": status,
        "confidence": round(confidence, 6),
        "score_version": SCORE_VERSION,
        "feature_version": FEATURE_VERSION,
        "normalization_version": NORMALIZATION_VERSION,
        "configured_weights": dict(production.get("configured_weights") or {}),
        "effective_weights": selected_effective,
        "contributions": selected_contributions,
        "missing_components": missing_components,
        "included_features": list(dict.fromkeys(included_features)),
        "coverage": {
            "status": status,
            "active_weight": production.get("active_weight"),
            "ratio": round(confidence, 6),
            "active_families": len(production.get("effective_weights") or {}),
            "expected_families": len(family_weights),
        },
        "factor_breakdown": {
            "factor_families": production_families,
            "hypothetical_factor_families": hypothetical_families,
            "family_details": {
                "short": short,
                "mid": mid,
                "long": long,
                "trend": production_trend,
                "breakout": base_breakout,
            },
            "trend_family": {
                "components": trend_components,
                "range_persistence_subcomponents": {
                    "components": range_components,
                    "effective_weights": range_result.get("effective_weights") or {},
                    "contributions": range_result.get("contributions") or {},
                    "missing": range_result.get("missing_components") or [],
                },
                "production_effective_weights": production_trend.get("effective_weights") or {},
                "hypothetical_effective_weights": hypothetical_trend.get("effective_weights") or {},
                "applied_effective_weights": (
                    hypothetical_trend.get("effective_weights") or {}
                    if range_mode == "enabled"
                    else production_trend.get("effective_weights") or {}
                ),
                "production_missing": production_trend.get("missing_components") or [],
                "hypothetical_missing": hypothetical_trend.get("missing_components") or [],
            },
            "effective_weights": selected_effective,
            "contributions": selected_contributions,
            "production_effective_weights": production.get("effective_weights") or {},
            "production_contributions": production.get("contributions") or {},
            "hypothetical_effective_weights": hypothetical.get("effective_weights") or {},
            "hypothetical_contributions": hypothetical.get("contributions") or {},
            "missing_families": production.get("missing_components") or [],
            "missing_components": missing_components,
            "range_persistence": range_payload,
            "range_persistence_shadow": shadow,
        },
        "score_short": short.get("score"),
        "score_mid": mid.get("score"),
        "score_long": long.get("score"),
        "breakout_quality_score": breakout_score,
        "price_action_score": price_action,
        "range_persistence": range_payload,
        "range_persistence_shadow": shadow,
    }


def score_profile_fit(row: Mapping[str, Any], profile: str) -> dict[str, Any]:
    intrinsic = score_value(row.get("intrinsic_score"))
    atr = finite_number(row.get("atr_pct"), lo=0.0)
    if atr is None:
        volatility_fit = None
    elif profile == "conservative":
        volatility_fit = max(0.0, min(100.0, 100.0 - atr * 11.0))
    elif profile == "aggressive":
        volatility_fit = max(0.0, min(100.0, 45.0 + atr * 7.0))
    else:
        volatility_fit = max(0.0, min(100.0, 100.0 - abs(atr - 3.0) * 12.0))

    avg_dollar = finite_number(row.get("avg_dollar_volume_20d"), lo=0.0)
    liquidity_fit = None
    if avg_dollar is not None and avg_dollar > 0:
        # 1m maps near 20, 10m near 50, 100m near 80.
        liquidity_fit = max(0.0, min(100.0, 20.0 + math.log10(avg_dollar / 1_000_000.0) * 30.0))
    trend_fit = score_value(row.get("ma_alignment"))
    weights = {
        "intrinsic": 0.55,
        "volatility_fit": 0.20,
        "liquidity_fit": 0.15,
        "trend_fit": 0.10,
    }
    if profile == "conservative":
        weights = {"intrinsic": 0.45, "volatility_fit": 0.25, "liquidity_fit": 0.20, "trend_fit": 0.10}
    elif profile == "aggressive":
        weights = {"intrinsic": 0.60, "volatility_fit": 0.20, "liquidity_fit": 0.10, "trend_fit": 0.10}
    return weighted_available(
        {
            "intrinsic": intrinsic,
            "volatility_fit": volatility_fit,
            "liquidity_fit": liquidity_fit,
            "trend_fit": trend_fit,
        },
        weights,
        min_active_weight=0.45,
        score_version=SCORE_VERSION,
    )


def score_market_fit(market: Mapping[str, Any]) -> dict[str, Any]:
    shape = market.get("market_shape")
    if not isinstance(shape, Mapping) or str(shape.get("status") or "") not in {
        "active",
        "degraded",
    }:
        result = weighted_available(
            {"market_shape": None},
            {"market_shape": 1.0},
            min_active_weight=0.25,
            score_version=SCORE_VERSION,
        )
        return {
            **result,
            "raw_score": None,
            "shape_status": (
                str(shape.get("status")) if isinstance(shape, Mapping) else "unavailable"
            ),
            "shape_state": None,
        }

    raw_score = score_value(market.get("score"))
    shape_confidence = finite_number(shape.get("confidence"), lo=0.0, hi=1.0)
    if raw_score is None or shape_confidence is None or shape_confidence <= 0:
        fitted_score = None
    else:
        # A degraded shape expresses weaker evidence by shrinking its market
        # fit toward neutral.  Missing shape never becomes a synthetic 50.
        fitted_score = 50.0 + (raw_score - 50.0) * shape_confidence
    result = weighted_available(
        {"market_shape": fitted_score},
        {"market_shape": 1.0},
        {"market_shape": shape_confidence or 0.0},
        min_active_weight=0.01,
        score_version=SCORE_VERSION,
    )
    return {
        **result,
        "raw_score": raw_score,
        "shape_status": str(shape.get("status")),
        "shape_state": shape.get("state"),
        "shape_confidence": shape_confidence,
    }


def score_ranking(
    intrinsic: Mapping[str, Any],
    market_fit: Mapping[str, Any],
    profile_fit: Mapping[str, Any],
) -> dict[str, Any]:
    return weighted_available(
        {
            "intrinsic": intrinsic.get("score"),
            "market_fit": market_fit.get("score"),
            "profile_fit": profile_fit.get("score"),
        },
        {"intrinsic": 0.78, "market_fit": 0.08, "profile_fit": 0.14},
        {
            "intrinsic": intrinsic.get("confidence", 0.0),
            "market_fit": market_fit.get("confidence", 0.0),
            "profile_fit": profile_fit.get("confidence", 0.0),
        },
        min_active_weight=0.45,
        score_version=SCORE_VERSION,
    )
