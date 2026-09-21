"""Mechanical adapter: full post-price-only rows → frozen gate candidates.

Does not change weights, production status/score/reasons, or public
projection. ETF rows keep the production HIGH_ATR object; only stocks
use the G1 stock reference.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any, Mapping

from screener_gate_candidates_v1 import (
    B0_CURRENT,
    G1_STOCK_REFERENCE,
    G2_EXTENSION_DISCOVERY,
    G3_RISK_DISCOVERY,
    VARIANTS,
    Decision,
    Facts,
    InsufficientReference,
    ReferenceError,
    ReferenceInput,
    ReferencePool,
    attach_research,
    build_reference,
    evaluate,
    stock_rank,
    upper_median,
)

from app.services.eod_limited.price_only import apply_price_only_track
from app.services.research_eod_v1.membership import has_complete_session_bar
from app.services.research_eod_v1.snapshot import compute_snapshot
from app.services.research_eod_v1.venue import classify_venue


def facts_from_raw_and_series(raw: Any, series: Any, session: date) -> Facts:
    venue = classify_venue(getattr(raw, "venue_metadata", None))
    return Facts(
        security_id=str(raw.security_id),
        session_date=session.isoformat(),
        asset_track=str(raw.asset_track),
        raw_close=raw.raw_close,
        adv20=raw.adv20,
        atr_pct=raw.atr_pct,
        history_sessions=int(raw.history_sessions),
        currently_tradable=bool(raw.currently_tradable),
        halted=bool(raw.halted),
        zero_volume=bool(raw.zero_volume),
        complete_bar=has_complete_session_bar(series, session),
        venue_eligible=bool(venue.eligible),
    )


def reference_input_from_raw_and_series(raw: Any, series: Any, session: date) -> ReferenceInput:
    facts = facts_from_raw_and_series(raw, series, session)
    return ReferenceInput(
        security_id=facts.security_id,
        session_date=facts.session_date,
        asset_track=facts.asset_track,
        raw_close=facts.raw_close,
        adv20=facts.adv20,
        atr_pct=facts.atr_pct,
        history_sessions=facts.history_sessions,
        currently_tradable=facts.currently_tradable,
        halted=facts.halted,
        zero_volume=facts.zero_volume,
        complete_bar=facts.complete_bar,
        venue_eligible=facts.venue_eligible,
    )


def production_old_median(raws: Mapping[str, Any]) -> float | None:
    """Reproduce snapshot.py: upper median of ATR% grouped by industry_id."""

    groups: dict[Any, list[float]] = defaultdict(list)
    for raw in raws.values():
        if getattr(raw, "atr_pct", None) is not None:
            groups[getattr(raw, "industry_id", None)].append(float(raw.atr_pct))
    if not groups:
        return None
    if None in groups:
        return upper_median(groups[None])
    merged = [value for values in groups.values() for value in values]
    return None if not merged else upper_median(merged)


def build_session_reference(
    raws: Mapping[str, Any],
    clipped: Mapping[str, Any],
    session: date,
) -> tuple[ReferencePool | None, str | None]:
    inputs = []
    for sid, raw in raws.items():
        series = clipped.get(sid)
        if series is None:
            continue
        inputs.append(reference_input_from_raw_and_series(raw, series, session))
    try:
        return build_reference(inputs), None
    except InsufficientReference as exc:
        return None, str(exc)
    except ReferenceError as exc:
        return None, str(exc)


def evaluate_row(
    row: Mapping[str, Any],
    *,
    facts: Facts,
    reference: ReferencePool | None,
    old_reference_median: float | None,
    profile_cfg: Mapping[str, Any],
    variant: str,
    dollar_liquidity_verified: bool,
    volume_session_verified: bool,
) -> Decision:
    return evaluate(
        variant=variant,
        row=row,
        facts=facts,
        reference=reference,
        old_reference_median=old_reference_median,
        absolute_atr_cap=float(profile_cfg["atr_absolute_cap_pct"]),
        reference_multiplier=float(profile_cfg["atr_sector_median_multiplier"]),
        dollar_liquidity_verified=dollar_liquidity_verified,
        volume_session_verified=volume_session_verified,
    )


def score_contexts(
    *,
    registry: Mapping[str, Any],
    session: date,
    as_of,
    clipped: Mapping[str, Any],
    raws: Mapping[str, Any],
    themed: Mapping[str, Mapping[str, Any]],
    themes: list[str],
    families: list[str],
    profile: str,
    horizon: str,
    universe_version: str,
    dollar_liquidity_verified: bool = False,
    volume_session_verified: bool = False,
    snapshot_cache: dict | None = None,
) -> dict[str, Any]:
    """One T-day: shared features/reference, then B0/G1/G2/G3 on every full row."""

    cache = {} if snapshot_cache is None else snapshot_cache
    reference, reference_error = build_session_reference(raws, clipped, session)
    old_median = production_old_median(raws)
    profile_cfg = registry["profiles"][profile]
    records: list[dict[str, Any]] = []
    family_pre_merge: dict[str, dict[str, int]] = {}
    for theme_id in themes:
        theme_raws = themed.get(theme_id) or raws
        for family in families:
            snap = compute_snapshot(
                as_of,
                clipped,
                universe_version,
                registry,
                sector_id=theme_id,
                algorithm=family,
                profile=profile,
                horizon=horizon,
                source_finalized_through=session,
                precomputed_raws=theme_raws,
                reapply_theme_gates=False,
                already_session_clipped=True,
                snapshot_cache=cache,
            )
            scored = apply_price_only_track(
                snap,
                registry=registry,
                theme_id=theme_id,
                family=family,
                profile=profile,
                horizon=horizon,
                volume_verified=False,
                dollar_liquidity_verified=dollar_liquidity_verified,
                volume_session_verified=volume_session_verified,
            )
            pre = family_pre_merge.setdefault(family, {"rows": 0, "eligible": 0, "watch": 0, "rejected": 0})
            for src in scored.get("rows") or []:
                row = dict(src)
                sid = str(row.get("security_id") or "")
                raw = theme_raws.get(sid) or raws.get(sid)
                series = clipped.get(sid)
                pre["rows"] += 1
                pre[str(row.get("status") or "rejected")] = pre.get(str(row.get("status") or "rejected"), 0) + 1
                if raw is None or series is None:
                    continue
                facts = facts_from_raw_and_series(raw, series, session)
                decisions = {}
                attached = {}
                for variant in VARIANTS:
                    use_variant = B0_CURRENT if facts.asset_track == "etf" else variant
                    decision = evaluate_row(
                        row,
                        facts=facts,
                        reference=reference,
                        old_reference_median=old_median,
                        profile_cfg=profile_cfg,
                        variant=use_variant,
                        dollar_liquidity_verified=dollar_liquidity_verified,
                        volume_session_verified=volume_session_verified,
                    )
                    decisions[variant] = decision
                    attached[variant] = attach_research(row, decision)
                records.append(
                    {
                        "row": row,
                        "facts": facts,
                        "decisions": decisions,
                        "attached": attached,
                    }
                )
    return {
        "session_date": session.isoformat(),
        "profile": profile,
        "horizon": horizon,
        "old_reference_median": old_median,
        "new_reference_median": None if reference is None else reference.median_atr_pct,
        "reference_n": None if reference is None else reference.n,
        "reference_member_ids": None if reference is None else list(reference.member_ids),
        "reference_error": reference_error,
        "actual_cap": None
        if reference is None
        else reference.actual_cap(
            float(profile_cfg["atr_absolute_cap_pct"]),
            float(profile_cfg["atr_sector_median_multiplier"]),
        ),
        "absolute_atr_cap": float(profile_cfg["atr_absolute_cap_pct"]),
        "reference_multiplier": float(profile_cfg["atr_sector_median_multiplier"]),
        "family_pre_merge": family_pre_merge,
        "records": records,
    }


def variant_rows(session_result: Mapping[str, Any], variant: str, *, layer: str) -> list[dict[str, Any]]:
    out = []
    for item in session_result.get("records") or []:
        decision: Decision = item["decisions"][variant]
        row = dict(item["row"])
        if layer == "discovery" and not decision.discovery_passed:
            continue
        if layer == "technical_entry" and not decision.technical_entry_passed:
            continue
        if layer == "qualified_entry" and not decision.qualified_entry_passed:
            continue
        row["research_gate"] = attach_research(row, decision)["research_gate"]
        out.append(row)
    return out


def ranked_stocks(session_result: Mapping[str, Any], variant: str, *, layer: str, top_k: int | None = None) -> list[dict[str, Any]]:
    return stock_rank(variant_rows(session_result, variant, layer=layer), track="stock", top_k=top_k)


__all__ = [
    "B0_CURRENT",
    "G1_STOCK_REFERENCE",
    "G2_EXTENSION_DISCOVERY",
    "G3_RISK_DISCOVERY",
    "VARIANTS",
    "build_session_reference",
    "evaluate_row",
    "facts_from_raw_and_series",
    "production_old_median",
    "ranked_stocks",
    "reference_input_from_raw_and_series",
    "score_contexts",
    "variant_rows",
]
