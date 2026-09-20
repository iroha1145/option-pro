"""PRICE_ONLY_DIAGNOSTIC rescore shared by research and product."""

from __future__ import annotations

from typing import Any, Mapping

from app.services.research_eod_v1.capability import (
    PRICE_ONLY_DIAGNOSTIC,
    SCORE_DERIVED_REASONS,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.constants import SCORE_FLOORS
from app.services.research_eod_v1.registry_scoring import resolve_weights

from . import MOMENTUM_BASIS, RETURN_BASIS, VOLUME_SCOPE

DOLLAR_LIQUIDITY_UNVERIFIED = "DOLLAR_LIQUIDITY_UNVERIFIED"
VOLUME_SESSION_UNVERIFIED = "VOLUME_SESSION_UNVERIFIED"


def resolve_capability_flags(
    *,
    volume_verified: bool = False,
    dollar_liquidity_verified: bool | None = None,
    volume_session_verified: bool | None = None,
) -> dict[str, bool]:
    dollar_ok = volume_verified if dollar_liquidity_verified is None else dollar_liquidity_verified
    session_ok = volume_verified if volume_session_verified is None else volume_session_verified
    return {
        "dollar_liquidity_verified": bool(dollar_ok),
        "volume_session_verified": bool(session_ok),
        "volume_verified": bool(volume_verified),
    }


def apply_price_only_track(
    payload: Mapping[str, Any],
    *,
    registry: Mapping[str, Any],
    theme_id: str,
    family: str,
    profile: str,
    horizon: str,
    volume_verified: bool = False,
    dollar_liquidity_verified: bool | None = None,
    volume_session_verified: bool | None = None,
) -> dict[str, Any]:
    flags = resolve_capability_flags(
        volume_verified=volume_verified,
        dollar_liquidity_verified=dollar_liquidity_verified,
        volume_session_verified=volume_session_verified,
    )
    dollar_ok = flags["dollar_liquidity_verified"]
    session_ok = flags["volume_session_verified"]
    weights = resolve_weights(registry, theme_id, family, profile, horizon)
    track = PRICE_ONLY_DIAGNOSTIC
    diag = diagnostic_weights(weights, track=track, family=family)
    coverage_min = float(registry["profiles"][profile]["coverage_min"])
    required = family_required(family)
    score_floor = float(SCORE_FLOORS[profile])
    rows = []
    for row in payload.get("rows") or []:
        updated = dict(row)
        updated["track"] = track
        updated["volume_scope"] = VOLUME_SCOPE if not session_ok else row.get("volume_scope")
        updated["momentum_basis"] = MOMENTUM_BASIS
        updated["return_basis"] = RETURN_BASIS
        inherited = [str(reason) for reason in (updated.get("rejection_reasons") or ())]
        updated["rejection_reasons"] = [reason for reason in inherited if reason not in SCORE_DERIVED_REASONS]
        if updated.get("gate_results") is not None:
            updated["gate_results_source"] = "upstream_full_model"
        if updated.get("factors"):
            scored = rescore_row(
                updated,
                diag,
                coverage_min=coverage_min,
                required=required,
                score_floor=score_floor,
            )
            updated["score"] = scored["score"]
            updated["observed_feature_coverage"] = scored["coverage"]
            updated["effective_weights"] = diag
            updated["rejection_reasons"] = list(dict.fromkeys(scored["rejection_reasons"]))
            updated["status"] = "eligible" if scored["final_eligible"] else "rejected"
        if updated.get("status") == "eligible" and not dollar_ok:
            updated["status"] = "watch"
            reasons = list(updated.get("rejection_reasons") or [])
            if DOLLAR_LIQUIDITY_UNVERIFIED not in reasons:
                reasons.append(DOLLAR_LIQUIDITY_UNVERIFIED)
            updated["rejection_reasons"] = reasons
        if (
            family in {"B_confirmed_base_breakout", "C_trend_pullback"}
            and not session_ok
            and updated.get("status") in {"eligible", "watch"}
        ):
            reasons = list(updated.get("rejection_reasons") or [])
            if VOLUME_SESSION_UNVERIFIED not in reasons:
                reasons.append(VOLUME_SESSION_UNVERIFIED)
            updated["rejection_reasons"] = reasons
            if updated.get("status") == "eligible":
                updated["status"] = "watch"
        rows.append(updated)
    out = dict(payload)
    out["rows"] = rows
    out["track"] = track
    out["capability_track"] = track
    return out
