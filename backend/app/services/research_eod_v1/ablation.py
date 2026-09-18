"""Preregistered first-round ablations. Neighbors are fixed before any IC is read."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable, Mapping

from app.services.research_eod_v1.calendar_asof import next_session
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    FULL_EIGHT_FACTOR,
    PRICE_ONLY_DIAGNOSTIC,
    ablation_weights,
    build_coverage_matrix,
    diagnostic_weights,
    family_required,
    g_is_available,
    neighbor_weights,
    rescore_row,
)
from app.services.research_eod_v1.measurement import classify_funnel_row
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.stats import IC_MIN_CROSS_SECTION, factor_ic_universe, spearman

ensure_reference_on_path()
from registry import FACTORS, resolve_weights  # type: ignore

ABLATION_FACTORS = ("T", "M", "S", "B", "P", "V", "R")
NEIGHBOR_SPECS: tuple[dict[str, Any], ...] = (
    {"variant_id": "N_M_PLUS_5PP", "kind": "weight", "factor": "M", "delta": 0.05},
    {"variant_id": "N_S_PLUS_5PP", "kind": "weight", "factor": "S", "delta": 0.05},
    {
        "variant_id": "N_SCORE_FLOOR_PLUS_10PCT",
        "kind": "threshold",
        "field": "score_floor",
        "scale": 1.10,
    },
)
DATE_BLOCKS: tuple[dict[str, str], ...] = (
    {"id": "Y2018", "start": "2018-01-02", "end": "2018-12-31"},
    {"id": "Y2019", "start": "2019-01-01", "end": "2019-12-31"},
    {"id": "Y2020", "start": "2020-01-01", "end": "2020-12-31"},
    {"id": "Y2021", "start": "2021-01-01", "end": "2021-12-31"},
    {"id": "Y2022", "start": "2022-01-01", "end": "2022-12-31"},
    {"id": "Y2023", "start": "2023-01-01", "end": "2023-12-31"},
    {"id": "Y2024H1", "start": "2024-01-01", "end": "2024-06-28"},
    {"id": "B2018_2019", "start": "2018-01-02", "end": "2019-12-31"},
    {"id": "B2020_2021", "start": "2020-01-01", "end": "2021-12-31"},
    {"id": "B2022_2023", "start": "2022-01-01", "end": "2023-12-31"},
)
LABEL_HORIZONS = (5, 20, 63)
BASELINE_ID = "BASELINE_FULL_EIGHT"
THIN_DEFINED_IC_DAYS = 10


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


def preregistered_neighbors() -> list[dict[str, Any]]:
    return [dict(item) for item in NEIGHBOR_SPECS]


def preregistered_date_blocks() -> list[dict[str, str]]:
    return [dict(item) for item in DATE_BLOCKS]


def _block_id(session: str) -> list[str]:
    return [block["id"] for block in DATE_BLOCKS if block["start"] <= session <= block["end"]]


def date_span(days: Iterable[str]) -> dict[str, Any]:
    values = sorted({str(item) for item in days if item})
    if not values:
        return {
            "start": None,
            "end": None,
            "n_sessions": 0,
            "n_calendar_days": 0,
            "evaluable_years_div_252": None,
            "note": "not_len_sessions_over_252",
        }
    start = date.fromisoformat(values[0])
    end = date.fromisoformat(values[-1])
    return {
        "start": values[0],
        "end": values[-1],
        "n_sessions": len(values),
        "n_calendar_days": (end - start).days + 1,
        "evaluable_years_div_252": None,
        "note": "session count is not evaluable years; /252 is SUPERSEDED",
    }


def _mean_ci(values: list[float]) -> dict[str, Any]:
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": None, "ci95": None, "method": "PREREGISTERED_NORMAL_APPROX", "reason": "EMPTY"}
    mean = float(sum(values) / n)
    if n < 5:
        return {
            "n": n,
            "mean": mean,
            "ci95": None,
            "method": "PREREGISTERED_NORMAL_APPROX",
            "reason": "BLOCK_TOO_SHORT",
        }
    var = sum((item - mean) ** 2 for item in values) / (n - 1)
    half = 1.96 * (var ** 0.5) / (n ** 0.5)
    return {
        "n": n,
        "mean": mean,
        "ci95": [mean - half, mean + half],
        "method": "PREREGISTERED_NORMAL_APPROX",
        "reason": None,
    }


def iter_variants(
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
    out = [
        Variant(
            BASELINE_ID,
            FULL_EIGHT_FACTOR,
            "baseline",
            None,
            dict(base),
            floor,
            coverage_min,
            "full eight-factor baseline; DATA_INSUFFICIENT stays real",
        )
    ]
    for factor in ABLATION_FACTORS:
        out.append(
            Variant(
                f"ABLATION_DROP_{factor}",
                FULL_EIGHT_FACTOR,
                "ablation",
                factor,
                ablation_weights(base, factor),
                floor,
                coverage_min,
                "drop one scoring weight and renormalize; hard gates unchanged",
            )
        )
    if g_is_available(coverage):
        out.append(
            Variant(
                "ABLATION_DROP_G",
                FULL_EIGHT_FACTOR,
                "ablation",
                "G",
                ablation_weights(base, "G"),
                floor,
                coverage_min,
                "G is coverage-available on this cell; not a fake on/off for missing industry",
            )
        )
    out.append(
        Variant(
            PRICE_ONLY_DIAGNOSTIC,
            PRICE_ONLY_DIAGNOSTIC,
            "diagnostic",
            "G",
            diagnostic_weights(base, track=PRICE_ONLY_DIAGNOSTIC, family=family),
            floor,
            coverage_min,
            "separate data-capability track; not a same-track champion",
        )
    )
    if family == "D_residual_momentum":
        out.append(
            Variant(
                D_MARKET_RESIDUAL_DIAGNOSTIC,
                D_MARKET_RESIDUAL_DIAGNOSTIC,
                "diagnostic",
                "G",
                diagnostic_weights(base, track=D_MARKET_RESIDUAL_DIAGNOSTIC, family=family),
                floor,
                coverage_min,
                "D without industry is not a tested two-factor industry residual",
            )
        )
    for spec in NEIGHBOR_SPECS:
        if spec["kind"] == "weight":
            out.append(
                Variant(
                    spec["variant_id"],
                    FULL_EIGHT_FACTOR,
                    "neighbor",
                    None,
                    neighbor_weights(base, spec["factor"], float(spec["delta"])),
                    floor,
                    coverage_min,
                    "preregistered neighbor; not fit to this IC",
                )
            )
        else:
            out.append(
                Variant(
                    spec["variant_id"],
                    FULL_EIGHT_FACTOR,
                    "neighbor",
                    None,
                    dict(base),
                    floor * float(spec["scale"]),
                    coverage_min,
                    "preregistered score_floor neighbor; coverage_min unchanged",
                )
            )
    return out


def _update_event(store: dict[tuple, tuple[date, int]], key: tuple, day: date) -> None:
    previous = store.get(key)
    if previous is None:
        store[key] = (day, 1)
        return
    last, count = previous
    if day == last:
        return
    if day < last:
        raise ValueError("event sessions must be nondecreasing")
    if next_session(last) != day:
        count += 1
    store[key] = (day, count)


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
        "ic_pairs": 0,
        "defined_ic_days": 0,
        "undefined_ic_days": 0,
        "thin_ic_days": 0,
        "daily_ics": [],
        "paired_diffs": [],
        "yearly_ics": defaultdict(list),
        "block_ics": defaultdict(list),
        "yearly_diffs": defaultdict(list),
        "block_diffs": defaultdict(list),
        "raw_sessions": set(),
        "feature_ready_sessions": set(),
        "label_matured_sessions": set(),
        "ic_defined_sessions": set(),
    }


def analyze_rows(
    rows: Iterable[Mapping[str, Any]],
    registry: Mapping[str, Any],
    *,
    profile: str = "balanced",
    horizon: str = "mid",
) -> dict[str, Any]:
    coverage_rows = build_coverage_matrix(registry=registry, profile=profile, horizon=horizon)
    coverage_map = {(item["theme"], item["family"]): item for item in coverage_rows}
    themes = list(registry["sectors"])
    families = list(registry["base_algorithm_weights"])
    catalog: dict[tuple[str, str], list[Variant]] = {}
    for theme in themes:
        for family in families:
            catalog[(theme, family)] = iter_variants(
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
    buckets: dict[tuple[str, str, str, int], list[tuple[float, float]]] = defaultdict(list)
    theme_events: dict[tuple, tuple[date, int]] = {}
    global_events: dict[tuple, tuple[date, int]] = {}
    required_map = {family: family_required(family) for family in families}
    last_snapshot = None
    last_scored: dict[str, dict[str, Any]] = {}

    def flush() -> None:
        if not buckets:
            return
        baseline_ics: dict[tuple[str, str, int], float | None] = {}
        flushed: dict[tuple[str, str, str, int], dict[str, Any]] = {}
        for key, pairs in buckets.items():
            variant_id, theme, family, label_h = key
            result = spearman([a for a, _ in pairs], [b for _, b in pairs])
            defined = result.value if result.n >= IC_MIN_CROSS_SECTION and result.reason is None else None
            reason = result.reason
            if result.n < IC_MIN_CROSS_SECTION:
                reason = "CROSS_SECTION_BELOW_N"
            flushed[key] = {"ic": defined, "n": result.n, "reason": None if defined is not None else reason}
            if variant_id == BASELINE_ID:
                baseline_ics[(theme, family, label_h)] = defined
        session = current_session or ""
        year = session[:4]
        blocks = _block_id(session)
        for key, item in flushed.items():
            variant_id, theme, family, label_h = key
            cell = cells[(theme, family, variant_id, label_h)]
            cell["ic_pairs"] += item["n"]
            if item["ic"] is None:
                cell["undefined_ic_days"] += 1
                if item["reason"] == "CROSS_SECTION_BELOW_N":
                    cell["thin_ic_days"] += 1
            else:
                cell["defined_ic_days"] += 1
                cell["ic_defined_sessions"].add(session)
                cell["daily_ics"].append(item["ic"])
                cell["yearly_ics"][year].append(item["ic"])
                for block in blocks:
                    cell["block_ics"][block].append(item["ic"])
                base_ic = baseline_ics.get((theme, family, label_h))
                if variant_id != BASELINE_ID and base_ic is not None:
                    diff = item["ic"] - base_ic
                    cell["paired_diffs"].append(diff)
                    cell["yearly_diffs"][year].append(diff)
                    for block in blocks:
                        cell["block_diffs"][block].append(diff)
        buckets.clear()

    for row in rows:
        inspected += 1
        session = str(row.get("signal_session"))
        if session != current_session:
            flush()
            current_session = session
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
                buckets[(variant.variant_id, theme, family, label_h)].append((float(scored["score"]), float(row["label"])))
            if scored["final_eligible"]:
                _update_event(
                    theme_events,
                    (variant.variant_id, theme, family, profile, horizon, label_h, sid),
                    day,
                )
                _update_event(
                    global_events,
                    (variant.variant_id, family, profile, horizon, label_h, sid),
                    day,
                )
    flush()

    pairing: list[dict[str, Any]] = []
    for (theme, family, variant_id, label_h), cell in sorted(cells.items()):
        variant = next(item for item in catalog[(theme, family)] if item.variant_id == variant_id)
        gate_n = cell["rows_label5"]
        pairing.append(
            {
                "theme": theme,
                "family": family,
                "variant_id": variant_id,
                "track": variant.track,
                "kind": variant.kind,
                "dropped": variant.dropped,
                "label_horizon": label_h,
                "statistically_thin": cell["defined_ic_days"] < THIN_DEFINED_IC_DAYS,
                "scorer_rows_label5": gate_n,
                "scorer_pass_rate": None if not gate_n else cell["scored_label5"] / gate_n,
                "eligible_rate": None if not gate_n else cell["eligible_label5"] / gate_n,
                "data_insufficient_rate": None if not gate_n else cell["data_insufficient_label5"] / gate_n,
                "hard_reject_rate": None if not gate_n else cell["hard_reject_label5"] / gate_n,
                "low_score_rate": None if not gate_n else cell["low_score_label5"] / gate_n,
                "labels_inspected": cell["labels_inspected"],
                "labels_matured": cell["labels_matured"],
                "defined_ic_days": cell["defined_ic_days"],
                "undefined_ic_days": cell["undefined_ic_days"],
                "thin_ic_days": cell["thin_ic_days"],
                "mean_grouped_ic": _mean_ci(cell["daily_ics"]),
                "paired_diff_vs_baseline": _mean_ci(cell["paired_diffs"]),
                "by_year": {year: _mean_ci(values) for year, values in sorted(cell["yearly_ics"].items())},
                "by_year_paired_diff": {year: _mean_ci(values) for year, values in sorted(cell["yearly_diffs"].items())},
                "date_blocks": {block: _mean_ci(values) for block, values in sorted(cell["block_ics"].items())},
                "date_blocks_paired_diff": {block: _mean_ci(values) for block, values in sorted(cell["block_diffs"].items())},
                "date_ranges": {
                    "raw": date_span(cell["raw_sessions"]),
                    "feature_ready": date_span(cell["feature_ready_sessions"]),
                    "label_matured": date_span(cell["label_matured_sessions"]),
                    "ic_defined": date_span(cell["ic_defined_sessions"]),
                },
                "notes": [
                    "IC uses the finite-score universe, not eligible-only rows",
                    "Family B IC is not breakout ledger PnL",
                    "Tracks are not cross-compared for a champion",
                ],
            }
        )

    def _event_total(store: Mapping[tuple, tuple[date, int]], predicate) -> int:
        return sum(count for key, (_last, count) in store.items() if predicate(key))

    event_by_variant: dict[str, dict[str, Any]] = {}
    for variant_id in {item["variant_id"] for item in registered}:
        event_by_variant[variant_id] = {
            "theme_report_deduped_event_groups": _event_total(theme_events, lambda key, vid=variant_id: key[0] == vid),
            "global_book_deduped_event_groups": _event_total(global_events, lambda key, vid=variant_id: key[0] == vid),
            "independent_events": None,
            "usable_for_independent_sample": False,
            "old_count_4514": "SUPERSEDED_EVENT_COUNT",
        }
    event_by_theme_family_label: dict[str, int] = {}
    for key, (_last, count) in theme_events.items():
        variant_id, theme, family, _profile, _horizon, label_h, _sid = key
        if variant_id != BASELINE_ID:
            continue
        label = f"{theme}|{family}|{label_h}"
        event_by_theme_family_label[label] = event_by_theme_family_label.get(label, 0) + count

    sessions = sorted({session for _theme, session in theme_dates})
    theme_empty: dict[str, dict[str, Any]] = {}
    for theme in themes:
        theme_can_score = any(g_is_available(coverage_map[(theme, family)]) for family in families)
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

    cards = []
    for theme in themes:
        family_cards = []
        for family in families:
            base_rows = [item for item in pairing if item["theme"] == theme and item["family"] == family and item["variant_id"] == BASELINE_ID]
            defined = sum(item["defined_ic_days"] for item in base_rows)
            thin = all(item["statistically_thin"] for item in base_rows) if base_rows else True
            if thin or defined < THIN_DEFINED_IC_DAYS:
                decision = "STATISTICALLY_THIN"
                action = "inherit_shared_prior"
                conclusion = "NO_CONCLUSION"
            else:
                decision = "CONTINUE_INSPECT"
                action = "run_preregistered_neighbors_only"
                conclusion = "NO_CHAMPION"
            family_cards.append(
                {
                    "family": family,
                    "decision": decision,
                    "action": action,
                    "conclusion": conclusion,
                    "defined_ic_days_all_labels": defined,
                    "next_neighbors": [spec["variant_id"] for spec in NEIGHBOR_SPECS],
                    "do_not": [
                        "do not invert or delete the family because software IC is negative",
                        "do not cross-track champion PRICE_ONLY vs FULL_EIGHT",
                        "do not treat B IC as breakout PnL",
                    ],
                }
            )
        cards.append({"theme": theme, "families": family_cards, "empty_ratios": theme_empty[theme]})

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
        "events": {
            "definition": "SECURITY_FAMILY_PROFILE_HORIZON_LABEL_TRADING_SESSION_OVERLAP",
            "name": "deduped_event_groups",
            "independent_events": None,
            "usable_for_independent_sample": False,
            "old_count_4514": "SUPERSEDED_EVENT_COUNT",
            "by_variant": event_by_variant,
            "baseline_by_theme_family_label": dict(sorted(event_by_theme_family_label.items())),
        },
        "empty_ratios": theme_empty,
        "date_ranges_window": {
            "raw": date_span(sessions),
            "evaluable": date_span(sessions),
            "note": "evaluable is trading sessions present in the tape; not len(sessions)/252",
        },
        "theme_cards": cards,
        "neighbors_preregistered": preregistered_neighbors(),
        "date_blocks_preregistered": preregistered_date_blocks(),
        "factors": list(FACTORS),
        "label_horizons": list(LABEL_HORIZONS),
    }
