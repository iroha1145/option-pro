"""Data-capability tracks and signed weight transforms. Not a champion picker."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.paths import ensure_reference_on_path

ensure_reference_on_path()
from registry import FACTORS, resolve_weights, score_features  # type: ignore

PRICE_ONLY_DIAGNOSTIC = "PRICE_ONLY_DIAGNOSTIC"
D_MARKET_RESIDUAL_DIAGNOSTIC = "D_MARKET_RESIDUAL_DIAGNOSTIC"
FULL_EIGHT_FACTOR = "FULL_EIGHT_FACTOR"
HARD_REJECTIONS = {
    "MISSING_T_BAR",
    "LATE_SOURCE",
    "SOURCE_UNAVAILABLE",
    "INCOMPLETE_COMMON_INPUTS",
    "SHORT_HISTORY",
    "ADV_TOO_LOW",
    "HIGH_ATR",
    "EXTENDED",
    "NOT_TRADABLE",
    "HALTED_SESSION",
    "SETUP_NOT_MET",
}
SCORE_DERIVED_REASONS = {
    "LOW_SCORE",
    "LOW_COVERAGE",
    "MISSING_SCORE",
    "DATA_INSUFFICIENT",
}


def renormalize(weights: Mapping[str, float], drop: set[str] | None = None) -> dict[str, float]:
    """Keep all eight keys. Dropped or zero mass becomes 0 so score_features can run."""

    dropped = drop or set()
    kept = {
        key: float(weights.get(key) or 0.0)
        for key in FACTORS
        if key not in dropped and float(weights.get(key) or 0.0) > 0
    }
    total = sum(kept.values())
    if total <= 0:
        raise ValueError("no remaining weight mass")
    return {key: (kept[key] / total if key in kept else 0.0) for key in FACTORS}


def coverage_without_factor(weights: Mapping[str, float], factor: str) -> dict[str, Any]:
    weight = float(weights.get(factor) or 0.0)
    return {
        f"{factor}_weight": weight,
        f"maximum_coverage_without_{factor}": 1.0 - weight,
    }


def coverage_row(
    theme: str,
    family: str,
    *,
    registry: Mapping[str, Any] | None = None,
    profile: str = "balanced",
    horizon: str = "mid",
) -> dict[str, Any]:
    data = registry or load_registry()
    weights = resolve_weights(data, theme, family, profile, horizon)
    coverage_min = float(data["profiles"][profile]["coverage_min"])
    g_weight = float(weights.get("G") or 0.0)
    max_without_g = 1.0 - g_weight
    features = {factor: 100.0 for factor in FACTORS if factor != "G"}
    features["G"] = None
    required = ("T", "M", "S", "R")
    if family == "B_confirmed_base_breakout":
        required = ("T", "M", "S", "R", "B", "V")
    elif family == "C_trend_pullback":
        required = ("T", "M", "S", "R", "P", "V")
    scored = score_features(features, weights, coverage_min=coverage_min, required=required)
    return {
        "theme": theme,
        "family": family,
        "profile": profile,
        "horizon": horizon,
        "G_weight": g_weight,
        "maximum_coverage_without_G": max_without_g,
        "minimum_coverage": coverage_min,
        "score_with_other_seven_factors_at_100": scored.score,
        "score_status": scored.status,
        "missing": list(scored.missing),
        "track": FULL_EIGHT_FACTOR,
        "note": "G missing is a data-capability failure, not a strategy loss",
    }


def build_coverage_matrix(
    *,
    registry: Mapping[str, Any] | None = None,
    profile: str = "balanced",
    horizon: str = "mid",
) -> list[dict[str, Any]]:
    data = registry or load_registry()
    themes = list(data["sectors"])
    families = list(data["base_algorithm_weights"])
    return [coverage_row(theme, family, registry=data, profile=profile, horizon=horizon) for theme in themes for family in families]


def diagnostic_weights(
    weights: Mapping[str, float],
    *,
    track: str,
    family: str,
) -> dict[str, float]:
    if track == FULL_EIGHT_FACTOR:
        return dict(weights)
    if track == PRICE_ONLY_DIAGNOSTIC:
        return renormalize(weights, {"G"})
    if track == D_MARKET_RESIDUAL_DIAGNOSTIC:
        if family != "D_residual_momentum":
            raise ValueError("D_MARKET_RESIDUAL_DIAGNOSTIC is only named for family D")
        return renormalize(weights, {"G"})
    raise ValueError(f"unknown track {track}")


def ablation_weights(weights: Mapping[str, float], dropped: str) -> dict[str, float]:
    return renormalize(weights, {dropped})


def neighbor_weights(weights: Mapping[str, float], factor: str, delta: float) -> dict[str, float]:
    updated = {key: float(weights.get(key) or 0.0) for key in FACTORS}
    updated[factor] = updated[factor] + delta
    if updated[factor] <= 0:
        raise ValueError("neighbor would drop the factor entirely")
    return renormalize(updated)


def g_is_available(coverage: Mapping[str, Any]) -> bool:
    """G is available only when the eight-factor model can still score without it."""

    return coverage.get("score_status") == "SCORED_NOT_SETUP_VALIDATED"


def rescore_row(
    row: Mapping[str, Any],
    weights: Mapping[str, float],
    *,
    coverage_min: float,
    required: tuple[str, ...],
    score_floor: float,
) -> dict[str, Any]:
    factors = row.get("factors") or {}
    scored = score_features(factors, weights, coverage_min=coverage_min, required=required)
    reasons = [str(item) for item in (row.get("rejection_reasons") or ())]
    hard = [reason for reason in reasons if reason not in SCORE_DERIVED_REASONS]
    new_reasons = list(hard)
    if scored.score is None:
        new_reasons.append(scored.status)
        eligible = False
    elif scored.score < score_floor:
        new_reasons.append("LOW_SCORE")
        eligible = False
    else:
        eligible = not hard
    return {
        "score": scored.score,
        "coverage": scored.coverage,
        "status": scored.status,
        "final_eligible": bool(eligible),
        "rejection_reasons": new_reasons,
    }


def family_required(family: str) -> tuple[str, ...]:
    if family == "B_confirmed_base_breakout":
        return ("T", "M", "S", "R", "B", "V")
    if family == "C_trend_pullback":
        return ("T", "M", "S", "R", "P", "V")
    return ("T", "M", "S", "R")
