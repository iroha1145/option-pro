"""Data-capability tracks and signed weight transforms. Not a champion picker."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.research_eod_v1.registry_scoring import FACTORS, score_features

PRICE_ONLY_DIAGNOSTIC = "PRICE_ONLY_DIAGNOSTIC"
# Price data plus an SIC-based industry classification: the eight-factor score
# with G computed from that classification (v1.7 candidates). Not a claim that
# the classification is point-in-time verified.
PRICE_SIC_INDUSTRY_DIAGNOSTIC = "PRICE_SIC_INDUSTRY_DIAGNOSTIC"
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


def diagnostic_weights(
    weights: Mapping[str, float],
    *,
    track: str,
    family: str,
) -> dict[str, float]:
    if track in {FULL_EIGHT_FACTOR, PRICE_SIC_INDUSTRY_DIAGNOSTIC}:
        return dict(weights)
    if track == PRICE_ONLY_DIAGNOSTIC:
        return renormalize(weights, {"G"})
    if track == D_MARKET_RESIDUAL_DIAGNOSTIC:
        if family != "D_residual_momentum":
            raise ValueError("D_MARKET_RESIDUAL_DIAGNOSTIC is only named for family D")
        return renormalize(weights, {"G"})
    raise ValueError(f"unknown track {track}")


def can_score_without_g(coverage: Mapping[str, Any]) -> bool:
    return coverage.get("score_status") == "SCORED_NOT_SETUP_VALIDATED"


def actual_g_observed(coverage: Mapping[str, Any], *, factors: Mapping[str, Any] | None = None) -> bool:
    """G is observed only when a finite G value is present. Missing G is not 'available'."""

    if factors is not None:
        value = factors.get("G")
        try:
            return value is not None and float(value) == float(value)
        except (TypeError, ValueError):
            return False
    if "actual_G_observed" in coverage:
        return bool(coverage.get("actual_G_observed"))
    missing = coverage.get("missing") or ()
    if "G" in set(missing):
        return False
    return False


def g_availability(coverage: Mapping[str, Any], *, factors: Mapping[str, Any] | None = None) -> dict[str, bool]:
    observed = actual_g_observed(coverage, factors=factors)
    scoreable = can_score_without_g(coverage)
    return {
        "actual_G_observed": observed,
        "can_score_without_G": scoreable,
        "g_is_available": observed,
    }


def g_is_available(coverage: Mapping[str, Any]) -> bool:
    """True only when G is actually observed. Scoring without G is a different flag."""

    return g_availability(coverage)["g_is_available"]


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
        "effective_weights": scored.effective_weights,
        "score_components": scored.contributions,
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
