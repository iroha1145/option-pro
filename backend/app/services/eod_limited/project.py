"""Project an EOD limited score payload onto the existing strength scan DTO."""

from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from app.services.sectors import SECTORS, primary_sector_id

from . import (
    ALGORITHM_VERSION,
    LIST_KIND_COMPOSITE,
    LIST_KIND_OBSERVATION,
    MODE_ID,
    PURPOSE_HISTORICAL,
    PURPOSE_LIVE,
    PURPOSE_SYNTHETIC,
    RESEARCH_SEALED_SESSION,
    SCORE_BASIS,
)
from .price_only import DOLLAR_LIQUIDITY_UNVERIFIED, VOLUME_SESSION_UNVERIFIED

FAMILY_LABELS = {
    "A_trend_quality": "A 趋势质量",
    "B_confirmed_base_breakout": "B 确认突破",
    "C_trend_pullback": "C 趋势回踩",
    "D_residual_momentum": "D 残差动量",
}

_HARD_BLOCKS = {
    "SETUP_NOT_MET",
    "HIGH_ATR",
    "EXTENDED",
    "HALTED_SESSION",
    "NOT_TRADABLE",
    "SHORT_HISTORY",
    "ADV_TOO_LOW",
}


def _name_for(ticker: str) -> str:
    sector_id = primary_sector_id(ticker)
    sector = SECTORS.get(sector_id or "") or {}
    names = sector.get("names") or {}
    if isinstance(names, Mapping) and ticker in names:
        return str(names[ticker])
    return ticker


def _sector_name(theme_id: str | None) -> str:
    if not theme_id:
        return ""
    sector = SECTORS.get(theme_id) or {}
    return str(sector.get("name") or theme_id)


def _close_price(row: Mapping[str, Any]) -> float | None:
    for key in ("price", "raw_close", "close"):
        value = row.get(key)
        if isinstance(value, (int, float)) and float(value) > 0:
            return float(value)
    return None


def _factor_dims(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    factors = row.get("factors") if isinstance(row.get("factors"), Mapping) else {}
    labels = {
        "T": "趋势",
        "M": "动量",
        "S": "结构",
        "B": "突破",
        "P": "回踩",
        "V": "量能",
        "R": "稳定性",
        "G": "行业",
    }
    dims = []
    for key in ("T", "M", "S", "B", "P", "V", "R", "G"):
        value = factors.get(key)
        try:
            number = None if value is None else float(value)
        except (TypeError, ValueError):
            number = None
        dims.append({"key": f"factor_{key}", "label": labels[key], "value": number})
    return dims


def project_row(row: Mapping[str, Any], *, list_kind: str) -> dict[str, Any]:
    ticker = str(row.get("security_id") or row.get("ticker") or "").upper()
    score = row.get("consensus_z", row.get("score")) if list_kind == LIST_KIND_COMPOSITE else row.get("score")
    try:
        score_n = None if score is None else float(score)
    except (TypeError, ValueError):
        score_n = None
    theme = str(row.get("sector_context") or row.get("theme_id") or "")
    if theme == "all_market_stocks":
        theme = ""
    price = _close_price(row)
    if price is None:
        price = 0.0
        price_unknown = True
    else:
        price_unknown = False
    reasons = [str(item) for item in (row.get("rejection_reasons") or [])]
    status = str(row.get("status") or "rejected")
    adv = row.get("adv20")
    adv = float(adv) if isinstance(adv, (int, float)) and not isinstance(adv, bool) and math.isfinite(adv) and adv >= 0 else None
    flags = row.get("capability_flags") or {}
    return {
        "ticker": ticker,
        "name": str(row.get("name") or _name_for(ticker)),
        "sector_id": theme or primary_sector_id(ticker),
        "sector_name": _sector_name(theme) or theme,
        "price": price,
        "price_unknown": price_unknown,
        "price_as_of": row.get("session_date"),
        "daily_data_through": row.get("session_date"),
        "change_pct": None,
        "score": score_n,
        "final_score": score_n,
        "strength_score": score_n if score_n is not None else 0.0,
        "ranking_score": score_n,
        "sort_score": score_n,
        "sort_algorithm": MODE_ID,
        "sort_basis": SCORE_BASIS,
        "avg_dollar_volume_20d": adv,
        "dollar_volume_unknown": adv is None,
        "dollar_volume_proxy_available": adv is not None,
        "dollar_volume_basis": "mean_20_raw_close_times_raw_volume",
        "dollar_volume_source": "vendor_daily_aggregate",
        "dollar_liquidity_verified": bool(flags.get("dollar_liquidity_verified")),
        "volume_session_verified": bool(flags.get("volume_session_verified")),
        "qualification": row.get("qualification") or status,
        "list_kind": list_kind,
        "status": status,
        "rejection_reasons": reasons,
        "entry_state": row.get("entry_state") or "ok",
        "entry_gate_reasons": list(row.get("entry_gate_reasons") or ()),
        "algorithm_id": row.get("algorithm_id"),
        "family_label": FAMILY_LABELS.get(str(row.get("algorithm_id") or ""), row.get("algorithm_id")),
        "family_votes": list(row.get("family_votes") or ()),
        "stock_or_etf_track": row.get("stock_or_etf_track"),
        "factors": row.get("factors") or {},
        "effective_weights": row.get("effective_weights") or {} if list_kind == LIST_KIND_OBSERVATION else {},
        "score_components": row.get("score_components") or {} if list_kind == LIST_KIND_OBSERVATION else {},
        "weight_provenance_id": row.get("weight_provenance_id"),
        "score_aggregation": "m1_consensus" if list_kind == LIST_KIND_COMPOSITE else "best_family_theme_path",
        "known_support": row.get("known_support"),
        "known_resistance": row.get("known_resistance"),
        "planned_invalidation": row.get("planned_invalidation"),
        "score_short": None,
        "score_mid": None,
        "score_long": None,
        "breakout_quality_score": None,
        "factor_dims": _factor_dims(row),
        "observation_only": list_kind != "composite",
    }


def _dedupe_observation_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Keep one row per security/track after cross-theme family collapse."""

    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        ticker = str(row.get("ticker") or "")
        if not ticker:
            continue
        key = f"{ticker}|{row.get('stock_or_etf_track') or ''}"
        current = dict(row)
        previous = best.get(key)
        if previous is None or (current.get("sort_score") or -1) > (previous.get("sort_score") or -1):
            best[key] = current
    return list(best.values())


def _empty_eligible_reason(scored: Mapping[str, Any]) -> str:
    if int(scored.get("eligible_n") or 0) > 0 and int(scored.get("composite_n") or 0) == 0:
        return "consensus_insufficient"
    if int(scored.get("watch_n") or 0) > 0:
        return "data_qualification_unverified"
    if int(scored.get("rejected_n") or 0) > 0:
        return "technical_threshold"
    return "no_complete_candidates"


def _observation_family_counts(watch: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for row in watch:
        sid = str(row.get("security_id") or "")
        algo = str(row.get("algorithm_id") or "")
        item = grouped.setdefault(sid, {"ticker": sid, "families": {}, "count": 0})
        score = row.get("score")
        previous = item["families"].get(algo)
        if algo and score is not None and (previous is None or score > previous):
            item["families"][algo] = score
            item["count"] = len(item["families"])
    return grouped


def project_strength_payload(
    scored: Mapping[str, Any],
    *,
    parameters: Mapping[str, Any],
    list_kind: str = LIST_KIND_OBSERVATION,
) -> dict[str, Any]:
    session = str(scored.get("served_session") or scored.get("session_date") or "")
    watch = list(scored.get("watch_list") or [])
    eligible = [
        row
        for block in (scored.get("family_results") or [])
        for row in (block.get("rows") or [])
        if row.get("status") == "eligible"
    ]
    observation_rows = [project_row(row, list_kind="observation") for row in [*eligible, *watch] if row.get("score") is not None]
    composite_rows = [
        project_row(row, list_kind="composite")
        for row in (scored.get("composite_results") or [])
    ]
    family_counts = _observation_family_counts([*eligible, *watch])
    for row in observation_rows:
        extra = family_counts.get(row["ticker"])
        if extra:
            row["observation_family_count"] = extra["count"]
            row["observation_family_scores"] = extra["families"]
    observation_rows = _dedupe_observation_rows(observation_rows)
    rows = composite_rows if list_kind == LIST_KIND_COMPOSITE else observation_rows
    rows = [row for row in rows if row.get("ticker")]
    rows.sort(key=lambda item: (-(item.get("sort_score") or -1), item["ticker"]))
    rejected_n = int(scored.get("rejected_n") or 0)
    flags = scored.get("capability_flags") or {}
    purpose = str(scored.get("purpose") or "")
    sealed = session == RESEARCH_SEALED_SESSION.isoformat()
    if sealed and purpose == PURPOSE_LIVE:
        purpose = PURPOSE_HISTORICAL
    historical = bool(scored.get("historical_example")) or purpose != PURPOSE_LIVE or sealed
    synthetic = bool(scored.get("synthetic") or purpose == PURPOSE_SYNTHETIC)
    coverage = scored.get("coverage") or {}
    return {
        "as_of": session,
        "as_of_session": session,
        "attempted_session": scored.get("attempted_session") or session,
        "served_session": session,
        "score_data_through": session,
        "params": dict(parameters),
        "requested_algorithm": MODE_ID,
        "effective_algorithm": MODE_ID,
        "algorithm_version": ALGORITHM_VERSION,
        "score_basis": SCORE_BASIS,
        "score_aggregation": "m1_consensus" if list_kind == LIST_KIND_COMPOSITE else "best_family_theme_path",
        "factor_capabilities": {"R": "stability_risk_quality", "G": "disabled_unverified_industry"},
        "theme_statistics": scored.get("theme_statistics"),
        "family_funnels": scored.get("family_funnels"),
        "fallback_reason": None,
        "purpose": purpose,
        "historical_example": historical,
        "synthetic": synthetic,
        "capability_flags": flags,
        "volume_scope": scored.get("volume_scope"),
        "dollar_liquidity_unverified": DOLLAR_LIQUIDITY_UNVERIFIED if not flags.get("dollar_liquidity_verified") else None,
        "volume_session_unverified": VOLUME_SESSION_UNVERIFIED if not flags.get("volume_session_verified") else None,
        "eligible_n": int(scored.get("eligible_n") or 0),
        "watch_n": int(scored.get("watch_n") or 0),
        "rejected_n": rejected_n,
        "composite_n": int(scored.get("composite_n") or 0),
        "observation_n": len(observation_rows),
        "empty_eligible_reason": _empty_eligible_reason(scored),
        "list_kind": list_kind,
        "observation_rows": observation_rows,
        "composite_rows": composite_rows,
        "rows": rows,
        "results": rows,
        "count": len(rows),
        "universe": scored.get("universe"),
        "universe_count": int(coverage.get("eligible_count", scored.get("panel_n") or 0)),
        "screened_count": int(coverage.get("complete_bar_count", scored.get("complete_bar_n") or 0)),
        "coverage": {key: coverage[key] for key in (
            "status", "directory_count", "eligible_count", "excluded_count",
            "complete_bar_count", "missing_session_count", "short_history_count",
            "residual_short_history_count", "invalid_count", "no_history_count",
            "scored_count", "provider", "source_dates", "source_hash",
        ) if key in coverage},
        "score_version": scored.get("compute_version"),
        "feature_version": scored.get("feature_version"),
        "data_sources": {
            "prices": {
                "status": "snapshot",
                "as_of_session": session,
                "note": "close_snapshot_not_live_quote",
                "provider": coverage.get("provider"),
            }
        },
    }
