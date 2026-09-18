"""Round 1b: capability-aligned ablations, common-member pairs, overlap events."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from app.services.research_eod_v1.bootstrap import paired_diff_intervals
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    FULL_EIGHT_FACTOR,
    PRICE_ONLY_DIAGNOSTIC,
    ablation_weights,
    build_coverage_matrix,
    diagnostic_weights,
    family_required,
    g_availability,
    realloc_plus_pp,
    rescore_row,
)
from app.services.research_eod_v1.event_groups import EventLedger, count_groups
from app.services.research_eod_v1.measurement import classify_funnel_row, setup_identity
from app.services.research_eod_v1.pairing import common_member_pair
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.stats import factor_ic_universe

ensure_reference_on_path()
from registry import FACTORS, resolve_weights  # type: ignore

ABLATION_FACTORS = ("T", "M", "S", "B", "P", "V", "R")
PRICE_BASELINE_ID = "BASELINE_PRICE_ONLY"
FULL_BASELINE_ID = "BASELINE_FULL_EIGHT"
SCORE_FLOOR_ID = "N_SCORE_FLOOR_PLUS_10PCT"
REALLOC_NEIGHBORS: tuple[dict[str, Any], ...] = (
    {
        "variant_id": "N_M_PLUS_5PP_REALLOC_V1",
        "kind": "weight",
        "factor": "M",
        "delta": 0.05,
        "note": "true +5pp reallocation; G stays frozen at zero",
    },
    {
        "variant_id": "N_S_PLUS_5PP_REALLOC_V1",
        "kind": "weight",
        "factor": "S",
        "delta": 0.05,
        "note": "true +5pp reallocation; G stays frozen at zero",
    },
)
LEGACY_NEIGHBORS = (
    {
        "variant_id": "N_M_PLUS_5PP",
        "status": "legacy_raw_weight_bump",
        "note": "R1 added 0.05 then renormalized by 1.05. Historical only. Not a true +5pp.",
    },
    {
        "variant_id": "N_S_PLUS_5PP",
        "status": "legacy_raw_weight_bump",
        "note": "R1 added 0.05 then renormalized by 1.05. Historical only. Not a true +5pp.",
    },
)
LABEL_HORIZONS = (5, 20, 63)
PAIRING_RULE = "COMMON_MEMBER_RERANK_THEN_IC_DIFF"
EVENT_RULE = "FRAGMENT_AND_LABEL_HORIZON_OVERLAP"
STATISTICS_RULE = "CIRCULAR_DATE_BLOCK_BOOTSTRAP_SEED174_2000"
THIN_DEFINED_IC_DAYS = 10
HISTORICAL_G_ABLATION_COUNT = 82


@dataclass(frozen=True)
class Variant:
    variant_id: str
    track: str
    kind: str
    dropped: str | None
    weights: dict[str, float]
    score_floor: float
    coverage_min: float
    note: str
    aliases: tuple[str, ...] = ()


def preregistered_realloc_neighbors() -> list[dict[str, Any]]:
    return [dict(item) for item in REALLOC_NEIGHBORS]


def iter_variants_r1b(
    theme: str,
    family: str,
    registry: Mapping[str, Any],
    coverage: Mapping[str, Any],
    *,
    profile: str = "balanced",
    horizon: str = "mid",
) -> list[Variant]:
    base = resolve_weights(registry, theme, family, profile, horizon)
    floor = float(registry["profiles"][profile]["score_floor"])
    coverage_min = float(registry["profiles"][profile]["coverage_min"])
    flags = g_availability(coverage)
    price = diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family=family)
    aliases = (D_MARKET_RESIDUAL_DIAGNOSTIC,) if family == "D_residual_momentum" else ()
    out = [
        Variant(
            FULL_BASELINE_ID,
            FULL_EIGHT_FACTOR,
            "baseline",
            None,
            dict(base),
            floor,
            coverage_min,
            "full eight-factor baseline kept for coverage contrast; not a cross-track champion",
        ),
        Variant(
            PRICE_BASELINE_ID,
            PRICE_ONLY_DIAGNOSTIC,
            "baseline",
            "G",
            dict(price),
            floor,
            coverage_min,
            "PRICE_ONLY_DIAGNOSTIC baseline; D uses the same weights as an alias, not a second experiment",
            aliases,
        ),
    ]
    for factor in ABLATION_FACTORS:
        out.append(
            Variant(
                f"PRICE_DROP_{factor}",
                PRICE_ONLY_DIAGNOSTIC,
                "ablation",
                factor,
                ablation_weights(price, factor),
                floor,
                coverage_min,
                "drop one scoring weight on the price track and renormalize; hard gates unchanged",
            )
        )
    if flags["actual_G_observed"]:
        out.append(
            Variant(
                "ABLATION_DROP_G",
                FULL_EIGHT_FACTOR,
                "ablation",
                "G",
                ablation_weights(base, "G"),
                floor,
                coverage_min,
                "only when G is actually observed",
            )
        )
    for spec in REALLOC_NEIGHBORS:
        out.append(
            Variant(
                spec["variant_id"],
                PRICE_ONLY_DIAGNOSTIC,
                "neighbor",
                None,
                realloc_plus_pp(price, spec["factor"], float(spec["delta"])),
                floor,
                coverage_min,
                spec["note"],
            )
        )
    out.append(
        Variant(
            SCORE_FLOOR_ID,
            PRICE_ONLY_DIAGNOSTIC,
            "neighbor",
            None,
            dict(price),
            floor * 1.10,
            coverage_min,
            "score_floor 74*1.1=81.4; scorer IC must match PRICE_ONLY baseline",
        )
    )
    return out


def _empty_cell() -> dict[str, Any]:
    return {
        "rows_label5": 0,
        "scored_label5": 0,
        "eligible_label5": 0,
        "data_insufficient_label5": 0,
        "hard_reject_label5": 0,
        "low_score_label5": 0,
        "labels_inspected": 0,
        "labels_matured": 0,
        "own_ic_days": 0,
        "pair_days": 0,
        "thin_pair_days": 0,
        "undefined_pair_days": 0,
        "daily_own_ics": [],
        "daily_pair_diffs": [],
        "pair_by_session": {},
        "yearly_diffs": defaultdict(list),
        "common_n_sum": 0,
        "baseline_only_n_sum": 0,
        "variant_only_n_sum": 0,
        "raw_sessions": set(),
        "feature_ready_sessions": set(),
        "label_matured_sessions": set(),
        "pair_defined_sessions": set(),
        "eligible_forward": {5: [], 20: [], 63: []},
    }


def analyze_rows_r1b(
    rows: Iterable[Mapping[str, Any]],
    registry: Mapping[str, Any],
    *,
    profile: str = "balanced",
    horizon: str = "mid",
    timeline: list[str] | None = None,
) -> dict[str, Any]:
    coverage_rows = build_coverage_matrix(registry=registry, profile=profile, horizon=horizon)
    coverage_map = {(item["theme"], item["family"]): item for item in coverage_rows}
    themes = list(registry["sectors"])
    families = list(registry["base_algorithm_weights"])
    catalog: dict[tuple[str, str], list[Variant]] = {}
    for theme in themes:
        for family in families:
            catalog[(theme, family)] = iter_variants_r1b(
                theme, family, registry, coverage_map[(theme, family)], profile=profile, horizon=horizon
            )
    registered = [
        {
            "theme": theme,
            "family": family,
            "variant_id": variant.variant_id,
            "track": variant.track,
            "kind": variant.kind,
            "dropped": variant.dropped,
            "score_floor": variant.score_floor,
            "weights": variant.weights,
            "note": variant.note,
            "aliases": list(variant.aliases),
        }
        for (theme, family), variants in catalog.items()
        for variant in variants
    ]
    cells: dict[tuple[str, str, str, int], dict[str, Any]] = defaultdict(_empty_cell)
    theme_dates: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"rows": 0, "eligible": 0, "warmup": 0, "data_missing": 0, "scored": 0, "finite_factor": 0}
    )
    unique_snapshots: set[str] = set()
    inspected = 0
    rescore_calls = 0
    cache_hits = 0
    current_session: str | None = None
    session_order: list[str] = []
    buckets: dict[tuple[str, str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    theme_events = EventLedger()
    global_events = EventLedger()
    required_map = {family: family_required(family) for family in families}
    last_snapshot = None
    last_scored: dict[str, dict[str, Any]] = {}
    setup_unrecoverable = 0

    def flush() -> None:
        if not buckets:
            return
        session = current_session or ""
        year = session[:4]
        for (theme, family, track, variant_id, label_h), members in buckets.items():
            baseline_members = buckets.get((theme, family, track, PRICE_BASELINE_ID if track == PRICE_ONLY_DIAGNOSTIC else FULL_BASELINE_ID, label_h), [])
            cell = cells[(theme, family, variant_id, label_h)]
            own = common_member_pair(members, members)
            if own["baseline_ic_own_set"] is not None:
                cell["own_ic_days"] += 1
                cell["daily_own_ics"].append(own["baseline_ic_own_set"])
            if variant_id in {PRICE_BASELINE_ID, FULL_BASELINE_ID}:
                cell["pair_by_session"][session] = None
                continue
            pair = common_member_pair(baseline_members, members)
            cell["common_n_sum"] += pair["common_n"]
            cell["baseline_only_n_sum"] += pair["baseline_only_n"]
            cell["variant_only_n_sum"] += pair["variant_only_n"]
            if pair["paired_diff"] is None:
                cell["undefined_pair_days"] += 1
                if pair["undefined_reason"] == "CROSS_SECTION_BELOW_N":
                    cell["thin_pair_days"] += 1
                cell["pair_by_session"][session] = None
            else:
                cell["pair_days"] += 1
                cell["pair_defined_sessions"].add(session)
                cell["daily_pair_diffs"].append(pair["paired_diff"])
                cell["yearly_diffs"][year].append(pair["paired_diff"])
                cell["pair_by_session"][session] = pair["paired_diff"]
        buckets.clear()

    for row in rows:
        inspected += 1
        session = str(row.get("signal_session"))
        if session != current_session:
            flush()
            current_session = session
            session_order.append(session)
        theme = str(row.get("theme_id"))
        family = str(row.get("algorithm"))
        label_h = int(row.get("label_horizon") or 5)
        sid = str(row.get("security_id"))
        snap = str(row.get("snapshot_key") or f"{session}|{theme}|{family}|{profile}|{horizon}|{sid}")
        unique_snapshots.add(snap)
        funnel = classify_funnel_row(row)
        factors = row.get("factors") or {}
        finite_factor = any(value is not None for value in factors.values()) if isinstance(factors, Mapping) else False
        date_key = (theme, session)
        theme_dates[date_key]["rows"] += 1
        if row.get("final_eligible") is True:
            theme_dates[date_key]["eligible"] += 1
        if funnel == "warmup":
            theme_dates[date_key]["warmup"] += 1
        if funnel == "data_missing":
            theme_dates[date_key]["data_missing"] += 1
        if factor_ic_universe(row):
            theme_dates[date_key]["scored"] += 1
        if finite_factor:
            theme_dates[date_key]["finite_factor"] += 1
        identity = setup_identity(row, theme, family, profile, horizon)
        if identity["setup_id_status"] == "SOURCE_SETUP_ID_UNRECOVERABLE":
            setup_unrecoverable += 1
        if snap != last_snapshot:
            last_snapshot = snap
            last_scored = {}
            for variant in catalog.get((theme, family), ()):
                last_scored[variant.variant_id] = rescore_row(
                    row,
                    variant.weights,
                    coverage_min=variant.coverage_min,
                    required=required_map[family],
                    score_floor=variant.score_floor,
                )
                rescore_calls += 1
        else:
            cache_hits += 1
        day = date.fromisoformat(session[:10])
        for variant in catalog.get((theme, family), ()):
            scored = last_scored[variant.variant_id]
            cell = cells[(theme, family, variant.variant_id, label_h)]
            cell["labels_inspected"] += 1
            cell["raw_sessions"].add(session)
            if scored["score"] is not None:
                cell["feature_ready_sessions"].add(session)
            if row.get("label") is not None:
                cell["labels_matured"] += 1
                cell["label_matured_sessions"].add(session)
            if label_h == 5:
                cell["rows_label5"] += 1
                if scored["score"] is not None:
                    cell["scored_label5"] += 1
                if scored["final_eligible"]:
                    cell["eligible_label5"] += 1
                if scored["status"] == "DATA_INSUFFICIENT":
                    cell["data_insufficient_label5"] += 1
                if "LOW_SCORE" in scored["rejection_reasons"]:
                    cell["low_score_label5"] += 1
                if any(reason not in {"LOW_SCORE", "DATA_INSUFFICIENT"} for reason in scored["rejection_reasons"]):
                    cell["hard_reject_label5"] += 1
            if scored["score"] is not None and row.get("label") is not None:
                buckets[(theme, family, variant.track, variant.variant_id, label_h)].append(
                    {
                        "security_id": sid,
                        "score": float(scored["score"]),
                        "label": float(row["label"]),
                        "status": scored["status"],
                        "rejection_reasons": scored["rejection_reasons"],
                    }
                )
            if scored["final_eligible"]:
                if row.get("label") is not None and label_h in cell["eligible_forward"]:
                    cell["eligible_forward"][label_h].append(float(row["label"]))
                theme_events.add(
                    (variant.variant_id, theme, family, profile, horizon, label_h, sid),
                    day,
                )
                global_events.add(
                    (variant.variant_id, family, profile, horizon, label_h, sid),
                    day,
                )
    flush()

    pairing: list[dict[str, Any]] = []
    for (theme, family, variant_id, label_h), cell in sorted(cells.items()):
        variant = next(item for item in catalog[(theme, family)] if item.variant_id == variant_id)
        gate_n = cell["rows_label5"]
        aligned = [cell["pair_by_session"].get(session) for session in session_order]
        intervals = (
            paired_diff_intervals(aligned, label_horizon=label_h)
            if variant_id not in {PRICE_BASELINE_ID, FULL_BASELINE_ID}
            else None
        )
        yearly = {year: _sign_year(values) for year, values in sorted(cell["yearly_diffs"].items())}
        eligible_labels = cell["eligible_forward"].get(label_h) or []
        pairing.append(
            {
                "theme": theme,
                "family": family,
                "variant_id": variant_id,
                "track": variant.track,
                "kind": variant.kind,
                "dropped": variant.dropped,
                "aliases": list(variant.aliases),
                "label_horizon": label_h,
                "statistically_thin": cell["pair_days"] < THIN_DEFINED_IC_DAYS,
                "scorer_rows_label5": gate_n,
                "scorer_pass_rate": None if not gate_n else cell["scored_label5"] / gate_n,
                "eligible_rate": None if not gate_n else cell["eligible_label5"] / gate_n,
                "data_insufficient_rate": None if not gate_n else cell["data_insufficient_label5"] / gate_n,
                "hard_reject_rate": None if not gate_n else cell["hard_reject_label5"] / gate_n,
                "low_score_rate": None if not gate_n else cell["low_score_label5"] / gate_n,
                "labels_inspected": cell["labels_inspected"],
                "labels_matured": cell["labels_matured"],
                "own_ic_days": cell["own_ic_days"],
                "pair_days": cell["pair_days"],
                "thin_pair_days": cell["thin_pair_days"],
                "undefined_pair_days": cell["undefined_pair_days"],
                "mean_common_n": None if not cell["pair_days"] else cell["common_n_sum"] / cell["pair_days"],
                "mean_baseline_only_n": None if not (cell["pair_days"] + cell["undefined_pair_days"]) else cell["baseline_only_n_sum"] / max(1, cell["pair_days"] + cell["undefined_pair_days"]),
                "mean_variant_only_n": None if not (cell["pair_days"] + cell["undefined_pair_days"]) else cell["variant_only_n_sum"] / max(1, cell["pair_days"] + cell["undefined_pair_days"]),
                "paired_diff_block_bootstrap": intervals,
                "yearly_direction": yearly,
                "eligible_signal_label_mean": None if not eligible_labels else sum(eligible_labels) / len(eligible_labels),
                "eligible_signal_n": len(eligible_labels),
                "notes": [
                    "paired_diff is common-member rerank IC difference, not own-set IC subtraction",
                    "Family B IC is not breakout ledger PnL",
                    "Tracks are not cross-compared for a champion",
                    "N_SCORE_FLOOR_PLUS_10PCT scorer IC must match PRICE_ONLY; judge it on eligible signals",
                    "executed_backtests stays 0; close-to-close labels are descriptive signal diagnostics",
                ],
            }
        )

    def _event_payload(ledger: EventLedger, predicate) -> dict[str, int]:
        fragments = 0
        overlaps = 0
        for key, days in ledger.items():
            if not predicate(key):
                continue
            label_h = int(key[-2])
            counted = count_groups(days, label_horizon=label_h)
            fragments += counted["consecutive_trigger_fragments"]
            overlaps += counted["label_horizon_overlap_groups"]
        return {
            "consecutive_trigger_fragments": fragments,
            "label_horizon_overlap_groups": overlaps,
            "independent_events": None,
            "usable_for_independent_sample": False,
            "old_count_4514": "SUPERSEDED_EVENT_COUNT",
        }

    event_by_variant: dict[str, dict[str, Any]] = {}
    for variant_id in {item["variant_id"] for item in registered}:
        event_by_variant[variant_id] = {
            "theme_report": _event_payload(theme_events, lambda key, vid=variant_id: key[0] == vid),
            "global_book": _event_payload(global_events, lambda key, vid=variant_id: key[0] == vid),
        }

    sessions = session_order
    theme_empty: dict[str, dict[str, Any]] = {}
    for theme in themes:
        theme_can_score = any(coverage_map[(theme, family)].get("can_score_without_G") for family in families)
        all_n = 0
        empty_all = 0
        post_n = 0
        empty_post = 0
        capable_n = 0
        empty_capable = 0
        warmup_dates = 0
        for session in sessions:
            flags = theme_dates.get((theme, session))
            all_n += 1
            empty = not flags or flags["eligible"] == 0
            if empty:
                empty_all += 1
            warmup_only = bool(flags) and flags["warmup"] > 0 and flags["eligible"] == 0 and flags["scored"] == 0
            if warmup_only:
                warmup_dates += 1
            else:
                post_n += 1
                if empty:
                    empty_post += 1
                data_ok = theme_can_score and bool(flags) and (flags["finite_factor"] > 0 or flags["scored"] > 0)
                if data_ok:
                    capable_n += 1
                    if empty:
                        empty_capable += 1
        theme_empty[theme] = {
            "empty_all_window": None if not all_n else empty_all / all_n,
            "empty_post_warmup": None if not post_n else empty_post / post_n,
            "empty_data_capable": None if not capable_n else empty_capable / capable_n,
            "denominators": {"all_window": all_n, "post_warmup": post_n, "data_capable": capable_n},
            "warmup_dates": warmup_dates,
            "note": "G-missing coverage failures are data-capability, not strategy loss",
        }

    cards = [_theme_card(theme, families, pairing, coverage_map, theme_empty[theme]) for theme in themes]
    g_confounded = [
        {"theme": row["theme"], "family": row["family"], "status": "capability-confounded"}
        for row in coverage_rows
        if row.get("can_score_without_G") and not row.get("actual_G_observed")
    ]
    return {
        "profile": profile,
        "horizon": horizon,
        "registered_configurations": len(registered),
        "registered": registered,
        "actual_rescore_calls": rescore_calls,
        "feature_reuse_hits": cache_hits,
        "unique_snapshots": len(unique_snapshots),
        "inspected_outcomes": inspected,
        "transformed_label_rows": inspected,
        "coverage_matrix": coverage_rows,
        "pairing": pairing,
        "historical_g_ablations": {
            "count": HISTORICAL_G_ABLATION_COUNT,
            "status": "capability-confounded",
            "note": "R1 ABLATION_DROP_G on cells that can_score_without_G but have no actual G. Not re-run.",
            "cells": g_confounded,
        },
        "legacy_neighbors": list(LEGACY_NEIGHBORS),
        "events": {
            "definition": EVENT_RULE,
            "pairing_rule": PAIRING_RULE,
            "statistics_rule": STATISTICS_RULE,
            "by_variant": event_by_variant,
            "setup_id_unrecoverable_rows": setup_unrecoverable,
            "independent_events": None,
            "old_count_4514": "SUPERSEDED_EVENT_COUNT",
        },
        "empty_ratios": theme_empty,
        "theme_cards": cards,
        "neighbors_preregistered": preregistered_realloc_neighbors()
        + [{"variant_id": SCORE_FLOOR_ID, "kind": "threshold", "field": "score_floor", "scale": 1.1}],
        "factors": list(FACTORS),
        "label_horizons": list(LABEL_HORIZONS),
        "executed_backtests": 0,
        "holdout_unsealed": False,
    }


def _sign_year(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0, "mean": None, "direction": None}
    mean = float(sum(values) / len(values))
    if mean > 0:
        direction = "positive"
    elif mean < 0:
        direction = "negative"
    else:
        direction = "zero"
    return {"n": len(values), "mean": mean, "direction": direction}


def _theme_card(
    theme: str,
    families: list[str],
    pairing: list[dict[str, Any]],
    coverage_map: Mapping[tuple[str, str], Mapping[str, Any]],
    empty_ratios: Mapping[str, Any],
) -> dict[str, Any]:
    family_cards = []
    for family in families:
        coverage = coverage_map[(theme, family)]
        rows = [item for item in pairing if item["theme"] == theme and item["family"] == family]
        price_base = [item for item in rows if item["variant_id"] == PRICE_BASELINE_ID]
        drops = [item for item in rows if item["kind"] == "ablation" and item["track"] == PRICE_ONLY_DIAGNOSTIC]
        neighbors = [item for item in rows if item["kind"] == "neighbor"]
        robust = []
        for item in drops + neighbors:
            interval = ((item.get("paired_diff_block_bootstrap") or {}).get("H") or {})
            ci = interval.get("ci95")
            pair_days = item.get("pair_days") or 0
            if item.get("statistically_thin") or pair_days < THIN_DEFINED_IC_DAYS:
                continue
            if ci and (ci[0] > 0 or ci[1] < 0):
                robust.append(item)
        if not coverage.get("can_score_without_G") and coverage.get("score_status") == "DATA_INSUFFICIENT":
            decision = "DATA_CAPABILITY_BLOCKED"
            action = "evaluate_price_track_only"
            conclusion = "EIGHT_FACTOR_NOT_SCOREABLE"
        elif not price_base or all(item.get("statistically_thin") for item in drops):
            decision = "CROSS_SECTION_THIN"
            action = "inherit_shared_prior"
            conclusion = "NO_CONCLUSION"
        elif not robust:
            decision = "NO_ROBUST_INCREMENT"
            action = "keep_price_baseline_no_champion"
            conclusion = "NO_ROBUST_INCREMENT"
        else:
            decision = "CONTINUE_CANDIDATE"
            action = "inspect_named_neighbors_only"
            conclusion = "NO_CHAMPION"
        next_neighbors = []
        for item in sorted(drops + neighbors, key=lambda row: abs(((row.get("paired_diff_block_bootstrap") or {}).get("H") or {}).get("mean") or 0), reverse=True):
            if item["variant_id"] == SCORE_FLOOR_ID:
                continue
            if item["variant_id"] not in next_neighbors:
                next_neighbors.append(item["variant_id"])
            if len(next_neighbors) == 3:
                break
        if not next_neighbors:
            next_neighbors = [spec["variant_id"] for spec in REALLOC_NEIGHBORS] + [SCORE_FLOOR_ID]
        family_cards.append(
            {
                "family": family,
                "track": PRICE_ONLY_DIAGNOSTIC,
                "eight_factor_status": coverage.get("score_status"),
                "actual_G_observed": coverage.get("actual_G_observed"),
                "can_score_without_G": coverage.get("can_score_without_G"),
                "decision": decision,
                "action": action,
                "conclusion": conclusion,
                "pair_days_price_ablations": sum(item.get("pair_days") or 0 for item in drops),
                "mean_common_n": None if not price_base else price_base[0].get("mean_common_n"),
                "robust_variants": [item["variant_id"] for item in robust[:6]],
                "yearly_direction_sample": {item["variant_id"]: item.get("yearly_direction") for item in drops[:3]},
                "next_neighbors": next_neighbors[:3],
                "neighbor_reason": "unified realloc/floor neighbors already executed; not a theme-specific discovery",
                "do_not": [
                    "do not invert or delete the family because software IC is negative",
                    "do not cross-track champion PRICE_ONLY vs FULL_EIGHT",
                    "do not treat B IC as breakout ledger PnL",
                    "do not treat N_SCORE_FLOOR_PLUS_10PCT unchanged IC as proof the floor is useless",
                ],
            }
        )
    return {"theme": theme, "families": family_cards, "empty_ratios": empty_ratios}
