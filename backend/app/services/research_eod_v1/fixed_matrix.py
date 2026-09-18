"""Fixed registered-config coverage and signal diagnostics.

Not a new weight grid. Does not overwrite B0 / R1 / R1b / R2 / freeze / readiness packs.
"""

from __future__ import annotations

import json
import math
import time
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from app.services.market_calendar import (
    ADHOC_FULL_CLOSURES,
    CALENDAR_VERSION,
    is_trading_day,
    trading_sessions,
)
from app.services.research_eod_v1.calendar_asof import shift_sessions
from app.services.research_eod_v1.capability import (
    D_MARKET_RESIDUAL_DIAGNOSTIC,
    PRICE_ONLY_DIAGNOSTIC,
    diagnostic_weights,
    family_required,
    rescore_row,
)
from app.services.research_eod_v1.config_load import load_experiment_manifest, load_registry
from app.services.research_eod_v1.constants import RESIDUAL_HISTORY_MIN
from app.services.research_eod_v1.data.contract import ResearchBar, validate_research_bars
from app.services.research_eod_v1.data_readiness import (
    ALLOWED_END,
    AUTOMOTIVE_MEMBERS,
    HOLDOUT_START,
    LAYER_A,
    LAYER_B,
    LAYER_C,
    LAYER_D,
    TRUSTED_RELATIVE_PICKLES,
    _complete_t_day,
    inspect_symbol_bars,
    load_trusted_research_bars,
)
from app.services.research_eod_v1.freeze import FROZEN_CANDIDATE_ID, FROZEN_FAMILY, FROZEN_THEME, load_frozen_candidate
from app.services.research_eod_v1.measurement import earliest_entry_session, next_day_confirm_available_at
from app.services.research_eod_v1.paths import REPO_ROOT, RETURN_PACK_DIR, ensure_reference_on_path
from app.services.research_eod_v1.runs import run_signature, scorer_cache_key, write_signed_checkpoint
from app.services.research_eod_v1.source_bind import EXPECTED_B0_SHA256, sha256_file
from app.services.sectors import SECTORS

ensure_reference_on_path()
from registry import resolve_weights  # type: ignore

PROTOCOL = "us-eod-research-fixed-matrix-v1"
DATASET_VERSION = "us-eod-fixed-matrix-yahoo-v1"
MISSING_ENDPOINT = "LABEL_ENDPOINT_MISSING"
STATUS_VALID = "VALID"
STATUS_INVALID = "INVALID"
STATUS_INSUFFICIENT = "INSUFFICIENT"
STATUS_UNVERIFIED = "UNVERIFIED"
B0_ROWS = RETURN_PACK_DIR / "measurement_factor_rows.jsonl"
CANDIDATES_PATH = RETURN_PACK_DIR / "algorithm_round2_candidates.json"
PILOT_SESSIONS = 8


def official_allowed_sessions() -> list[date]:
    return trading_sessions(date(2018, 1, 2), ALLOWED_END)


def registered_warmup(family: str, registry: Mapping[str, Any] | None = None) -> int:
    data = registry or load_registry()
    values = [
        int(sector["candidates"][family]["min_history_sessions"])
        for sector in data["sectors"].values()
        if family in sector.get("candidates", {})
    ]
    floor = max(330, RESIDUAL_HISTORY_MIN) if family == "D_residual_momentum" else 252
    return max([floor, *values]) if values else floor


def feature_cache_key(*, session: date, security_id: str, score_horizon: str, feature_version: str) -> str:
    return "|".join([session.isoformat(), security_id, score_horizon, feature_version])


def classify_symbol(bars: Sequence[ResearchBar]) -> dict[str, Any]:
    allowed = [bar for bar in bars if bar.session_date <= ALLOWED_END]
    rejection: Counter[str] = Counter()
    try:
        if allowed:
            validate_research_bars(allowed)
        contract = "ok"
    except ValueError as exc:
        contract = str(exc)
        rejection["CONTRACT"] += 1
    summary = inspect_symbol_bars(allowed, allowed_end=ALLOWED_END)
    if summary["ohlc_violations"]:
        rejection["OHLC"] += summary["ohlc_violations"]
    if summary["duplicates"]:
        rejection["DUPLICATE"] += summary["duplicates"]
    if summary["unsorted"]:
        rejection["UNSORTED"] += 1
    complete = summary["complete_t_days"]
    if contract != "ok" or summary["ohlc_violations"] or summary["duplicates"] or summary["unsorted"]:
        status = STATUS_INVALID
    elif complete <= 0:
        status = STATUS_INSUFFICIENT
    else:
        status = STATUS_VALID
    return {
        "status": status,
        "contract": contract,
        "complete_t_days": complete,
        "allowed_n": summary["allowed_n"],
        "after_holdout": summary["after_holdout"],
        "raw_ne_close": summary["raw_ne_close"],
        "tri_ne_close": summary["tri_ne_close"],
        "tri_present_bars": summary["tri_ne_close"] + sum(
            1 for bar in allowed if bar.tri is not None and bar.close is not None and abs(bar.tri - bar.close) <= 1e-6
        ),
        "rejections": dict(rejection),
        **summary,
    }


def quality_pool(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    per: dict[str, dict[str, Any]] = {}
    counts: Counter[str] = Counter()
    for symbol, rows in bars.items():
        row = classify_symbol(rows)
        per[symbol] = row
        counts[row["status"]] += 1
    spy = per.get("SPY") or {"status": STATUS_UNVERIFIED, "complete_t_days": 0}
    return {
        "per_security": per,
        "counts": dict(counts),
        "spy_status": spy.get("status"),
        "valid_n": counts[STATUS_VALID],
        "invalid_n": counts[STATUS_INVALID],
        "insufficient_n": counts[STATUS_INSUFFICIENT],
        "isolated_invalid_does_not_fail_pool": True,
        "spy_valid_does_not_pass_pool": True,
        "execution_gates": "EXECUTION_GATES_UNVERIFIED",
    }


def calendar_diff_audit(spy_dates: Sequence[date]) -> dict[str, Any]:
    official = official_allowed_sessions()
    official_set = set(official)
    spy_set = {session for session in spy_dates if session <= ALLOWED_END}
    exception = date(2018, 12, 5)
    return {
        "calendar_version": CALENDAR_VERSION,
        "official_n": len(official),
        "spy_complete_n": len(spy_set),
        "legacy_is_trading_day_without_adhoc": 1634,
        "official_minus_spy": sorted((official_set - spy_set)),
        "spy_minus_official": sorted(spy_set - official_set),
        "exception_2018_12_05": {
            "official_closed": not is_trading_day(exception),
            "adhoc_name": ADHOC_FULL_CLOSURES.get(exception),
            "in_official_sessions": exception in official_set,
            "in_spy_tape": exception in spy_set,
            "do_not_invent_missing_bar": True,
        },
        "canonical_t20_2024_04_01": shift_sessions(date(2024, 4, 1), 20).isoformat(),
        "bar_index_must_not_move_endpoint": True,
        "impact": {
            "derived_layers_to_reversion": [
                "official-calendar T+5/20/63 endpoints",
                "first_scoreable / mature_label using registered warmup",
            ],
            "not_automatically_invalidated": [
                "2023/2024 candidate ICs on B0 (different tape/calendar vintage)",
            ],
            "do_not_recrawl_quotes": True,
            "do_not_rewrite_b0": True,
        },
    }


def canonical_label_endpoint(signal: date, holding_sessions: int) -> date:
    return shift_sessions(signal, holding_sessions)


def endpoint_fields(
    bars_by_date: Mapping[date, ResearchBar],
    signal: date,
    holding_sessions: int,
) -> dict[str, Any]:
    endpoint = canonical_label_endpoint(signal, holding_sessions)
    start = bars_by_date.get(signal)
    end = bars_by_date.get(endpoint)
    quote_ok = (
        start is not None
        and end is not None
        and _complete_t_day(start)
        and _complete_t_day(end)
        and start.close
        and end.close
    )
    tri_ok = (
        start is not None
        and end is not None
        and start.tri is not None
        and end.tri is not None
        and start.tri == start.tri
        and end.tri == end.tri
        and start.tri > 0
    )
    return {
        "endpoint": endpoint.isoformat(),
        "quote_available": bool(quote_ok),
        "tri_available": bool(tri_ok),
        "reason": None if quote_ok or tri_ok else MISSING_ENDPOINT,
        "quote_return": (end.close / start.close - 1.0) if quote_ok else None,
        "tri_return": (end.tri / start.tri - 1.0) if tri_ok else None,
        "do_not_move_endpoint": True,
    }


def first_scoreable(sessions: Sequence[date], warmup: int) -> date | None:
    if len(sessions) < warmup:
        return None
    return sessions[warmup - 1]


def capability_plan(
    quality: Mapping[str, Any],
    *,
    registry: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    data = registry or load_registry()
    manifest = load_experiment_manifest()
    official = official_allowed_sessions()
    per = quality.get("per_security") or {}
    rows = []
    for item in manifest["experiments"]:
        family = item["algorithm"]
        theme = item["sector_id"]
        warmup = registered_warmup(family, data)
        members = [str(ticker) for ticker in (SECTORS.get(theme) or {}).get("tickers") or ()]
        valid = [name for name in members if (per.get(name) or {}).get("status") == STATUS_VALID]
        first = first_scoreable(official, warmup)
        holding = int(item["holding_sessions"])
        mature = shift_sessions(first, holding) if first else None
        last_signal = shift_sessions(ALLOWED_END, -holding)
        evaluable = 0
        if first and last_signal >= first:
            evaluable = sum(1 for session in official if first <= session <= last_signal)
        track = D_MARKET_RESIDUAL_DIAGNOSTIC if family == FROZEN_FAMILY else PRICE_ONLY_DIAGNOSTIC
        reasons = []
        if not valid:
            reasons.append("NO_VALID_THEME_MEMBERS")
        if first is None:
            reasons.append("SHORT_OFFICIAL_HISTORY")
        reasons.append("EXECUTION_GATES_UNVERIFIED")
        reasons.append("CURRENT_LIST_ONLY")
        if item["horizon"] != "mid":
            reasons.append("SCORE_HORIZON_FEATURES_NOT_IN_B0")
        rows.append(
            {
                "experiment_id": item["experiment_id"],
                "theme": theme,
                "family": family,
                "profile": item["profile"],
                "score_horizon": item["horizon"],
                "label_holding_sessions": holding,
                "warmup_sessions": warmup,
                "members": members,
                "valid_members": valid,
                "valid_member_n": len(valid),
                "first_scoreable_day": first.isoformat() if first else None,
                "mature_label_day": mature.isoformat() if mature else None,
                "last_signal_day": last_signal.isoformat(),
                "evaluable_official_sessions": evaluable,
                "track": track,
                "not_a_second_experiment": family == FROZEN_FAMILY,
                "eligible_nav": False,
                "execution_status": "SCORE_STRUCTURE_DIAGNOSTIC",
                "unexecuted_reasons": reasons,
                "layers": {
                    LAYER_A: "available" if valid else "blocked",
                    LAYER_B: "partial" if valid else "blocked",
                    LAYER_C: "blocked",
                    LAYER_D: "current_list_only",
                },
            }
        )
    return rows


def index_bars(rows: Sequence[ResearchBar]) -> dict[date, ResearchBar]:
    return {bar.session_date: bar for bar in rows if bar.session_date <= ALLOWED_END}


def theme_structure(
    bars: Mapping[str, Sequence[ResearchBar]],
    *,
    theme: str,
    holding_sessions: int,
    warmup: int,
    quality: Mapping[str, Any],
) -> dict[str, Any]:
    official = official_allowed_sessions()
    first = first_scoreable(official, warmup)
    last_signal = shift_sessions(ALLOWED_END, -holding_sessions)
    members = [str(ticker) for ticker in (SECTORS.get(theme) or {}).get("tickers") or ()]
    per = quality.get("per_security") or {}
    indexed = {name: index_bars(bars.get(name) or []) for name in members}
    days = []
    quote_n = 0
    tri_n = 0
    missing_n = 0
    if first:
        for session in official:
            if session < first or session > last_signal:
                continue
            present = []
            quote_vals = []
            tri_vals = []
            for name in members:
                if (per.get(name) or {}).get("status") != STATUS_VALID:
                    continue
                fields = endpoint_fields(indexed[name], session, holding_sessions)
                if fields["quote_available"] or fields["tri_available"]:
                    present.append(name)
                if fields["quote_available"]:
                    quote_vals.append(fields["quote_return"])
                if fields["tri_available"]:
                    tri_vals.append(fields["tri_return"])
                if fields["reason"] == MISSING_ENDPOINT:
                    missing_n += 1
            if quote_vals:
                quote_n += 1
            if tri_vals:
                tri_n += 1
            days.append(
                {
                    "session": session.isoformat(),
                    "n_quote": len(quote_vals),
                    "n_tri": len(tri_vals),
                    "mean_quote": sum(quote_vals) / len(quote_vals) if quote_vals else None,
                    "mean_tri": sum(tri_vals) / len(tri_vals) if tri_vals else None,
                }
            )
    return {
        "theme": theme,
        "holding_sessions": holding_sessions,
        "warmup_sessions": warmup,
        "first_scoreable_day": first.isoformat() if first else None,
        "days_with_quote_label": quote_n,
        "days_with_tri_label": tri_n,
        "missing_endpoint_events": missing_n,
        "quote_and_tri_are_separate_metrics": True,
        "public_day_limit": 8,
        "public_days": days[:8],
    }


def engineering_pilot(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    registry = load_registry()
    official = official_allowed_sessions()
    last_long = shift_sessions(ALLOWED_END, -63)
    window = [session for session in official if session <= last_long][-PILOT_SESSIONS:]
    blends = {name: tuple(registry["horizons"][name]["momentum_blend"]) for name in ("short", "mid", "long")}
    keys = []
    started = time.perf_counter()
    for horizon, blend in blends.items():
        for profile in ("conservative", "balanced", "aggressive"):
            keys.append(
                {
                    "profile": profile,
                    "score_horizon": horizon,
                    "momentum_blend": list(blend),
                    "feature_cache_key": feature_cache_key(
                        session=window[0],
                        security_id="TSLA",
                        score_horizon=horizon,
                        feature_version="us-eod-research-features-v1.5",
                    ),
                    "score_cache_key": scorer_cache_key(
                        profile=profile,
                        horizon=horizon,
                        registry_version=registry.get("schema_version"),
                        security_id="TSLA",
                    ),
                }
            )
    elapsed = time.perf_counter() - started
    mid_blend = blends["mid"]
    return {
        "kind": "engineering_pilot",
        "not_for_winner_selection": True,
        "sessions": [session.isoformat() for session in window],
        "session_n": len(window),
        "combos": keys,
        "distinct_feature_horizon_keys": len({row["feature_cache_key"] for row in keys}),
        "distinct_score_keys": len({row["score_cache_key"] for row in keys}),
        "mid_blend_not_reused": blends["short"] != mid_blend and blends["long"] != mid_blend,
        "elapsed_s": elapsed,
        "yahoo_bars_present": bool(bars),
    }


def _iter_b0(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            yield json.loads(line)


def rescore_mid_from_b0(
    *,
    path: Path | None = None,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tape = path or B0_ROWS
    if not tape.is_file():
        return {"status": STATUS_UNVERIFIED, "reason": "b0_tape_missing"}
    data = registry or load_registry()
    stats: dict[str, dict[str, Any]] = {}
    for item in load_experiment_manifest()["experiments"]:
        if item["horizon"] != "mid":
            continue
        stats[item["experiment_id"]] = {
            "experiment_id": item["experiment_id"],
            "theme": item["sector_id"],
            "family": item["algorithm"],
            "profile": item["profile"],
            "score_horizon": "mid",
            "label_horizon": 20,
            "sessions": 0,
            "eligible_rows": 0,
            "scored_rows": 0,
            "source": "b0_factors_rescored_not_spliced",
            "completed": True,
        }
    for raw in _iter_b0(tape):
        if raw.get("horizon") != "mid" or int(raw.get("label_horizon") or 0) != 20:
            continue
        theme = raw.get("theme_id")
        family = raw.get("algorithm")
        if not theme or not family:
            continue
        for profile in ("conservative", "balanced", "aggressive"):
            experiment_id = f"{theme}__{family}__{profile}__mid"
            bucket = stats.get(experiment_id)
            if bucket is None:
                continue
            weights = diagnostic_weights(
                resolve_weights(data, theme, family, profile, "mid"),
                track=D_MARKET_RESIDUAL_DIAGNOSTIC if family == FROZEN_FAMILY else PRICE_ONLY_DIAGNOSTIC,
                family=family,
            )
            scored = rescore_row(
                raw,
                weights,
                coverage_min=float(data["profiles"][profile]["coverage_min"]),
                required=family_required(family),
                score_floor=float(data["profiles"][profile]["score_floor"]),
            )
            bucket["scored_rows"] += 1
            if scored["score"] is not None:
                bucket["sessions"] += 1
            if scored["final_eligible"]:
                bucket["eligible_rows"] += 1
    return {
        "status": "COMPLETED_MID_RESCORE",
        "sha256": sha256_file(tape),
        "expected_sha256": EXPECTED_B0_SHA256,
        "configs": list(stats.values()),
        "completed_n": sum(1 for row in stats.values() if row["completed"]),
        "versions_not_spliced": True,
    }


def recover_selection_sets(
    *,
    path: Path | None = None,
    registry: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    tape = path or B0_ROWS
    if not tape.is_file() or not CANDIDATES_PATH.is_file():
        return {"status": STATUS_UNVERIFIED, "reason": "b0_or_candidates_missing"}
    data = registry or load_registry()
    frozen = load_frozen_candidate(CANDIDATES_PATH)
    base = diagnostic_weights(
        resolve_weights(data, FROZEN_THEME, FROZEN_FAMILY, "balanced", "mid"),
        track=PRICE_ONLY_DIAGNOSTIC,
        family=FROZEN_FAMILY,
    )
    cand = {key: float(value) for key, value in frozen["weights"].items()}
    required = family_required(FROZEN_FAMILY)
    floor = float(data["profiles"]["balanced"]["score_floor"])
    coverage_min = float(data["profiles"]["balanced"]["coverage_min"])
    by_session: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {"baseline": [], "candidate": []})
    for raw in _iter_b0(tape):
        if raw.get("theme_id") != FROZEN_THEME or raw.get("algorithm") != FROZEN_FAMILY:
            continue
        if raw.get("horizon") != "mid" or int(raw.get("label_horizon") or 0) != 20:
            continue
        session = str(raw.get("signal_session"))
        for side, weights in (("baseline", base), ("candidate", cand)):
            scored = rescore_row(raw, weights, coverage_min=coverage_min, required=required, score_floor=floor)
            if scored["score"] is None:
                continue
            by_session[session][side].append(
                {
                    "security_id": raw.get("security_id"),
                    "score": scored["score"],
                    "final_eligible": scored["final_eligible"],
                    "label": raw.get("label"),
                }
            )
    days = []
    identical = 0
    for session, sides in sorted(by_session.items()):
        signal = date.fromisoformat(session)
        available = next_day_confirm_available_at(signal)
        entry = earliest_entry_session(signal, available)
        base_ids = sorted(
            str(row["security_id"]) for row in sides["baseline"] if row.get("final_eligible")
        )
        cand_ids = sorted(
            str(row["security_id"]) for row in sides["candidate"] if row.get("final_eligible")
        )
        same = base_ids == cand_ids
        identical += int(same)
        days.append(
            {
                "signal_date": session,
                "signal_available_at": available.isoformat(),
                "earliest_entry_session": entry.isoformat(),
                "baseline_selected": base_ids,
                "candidate_selected": cand_ids,
                "baseline_n": len(base_ids),
                "candidate_n": len(cand_ids),
                "own_sets_identical": same,
                "common_scoreable_n": len(
                    {row["security_id"] for row in sides["baseline"]}
                    & {row["security_id"] for row in sides["candidate"]}
                ),
                "common_scoreable_is_not_selection": True,
            }
        )
    return {
        "status": "RECOVERED",
        "theme": FROZEN_THEME,
        "family": FROZEN_FAMILY,
        "candidate_id": FROZEN_CANDIDATE_ID,
        "weights_not_retuned": True,
        "days": days,
        "day_n": len(days),
        "own_sets_identical_days": identical,
        "do_not_infer_same_selection_from_common_score_set": True,
    }


def background_basket_note() -> dict[str, Any]:
    return {
        "statistic": "equal_weight_close_to_close_current_list_background",
        "universe": list(AUTOMOTIVE_MEMBERS),
        "window": "T_close_to_T_plus_20_close_allowed_development",
        "not_candidate_strategy_return": True,
        "not_baseline_strategy_return": True,
        "source_pack": "algorithm_data_readiness_automotive_descriptive.json",
        "overnight_gap": "T_close_to_T_plus_1_open_is_not_NEXT_DAY_CONFIRM",
        "left_tail_p05": {
            "object": "cross_section_name_return_on_session",
            "algorithm": "order_statistics_index_floor_0.05*(n-1)",
            "n_names": 10,
            "index": 0,
            "meaning": "worst name that day under this lower-order convention, not a time-series portfolio VaR",
        },
    }


def next_day_confirm_example(signal: date = date(2024, 4, 1)) -> dict[str, Any]:
    available = next_day_confirm_available_at(signal)
    entry = earliest_entry_session(signal, available)
    return {
        "signal_date": signal.isoformat(),
        "policy_available_at": available.isoformat(),
        "descriptive_overnight_endpoint": shift_sessions(signal, 1).isoformat(),
        "actual_helper_earliest_entry": entry.isoformat(),
        "interpretation": "T-to-T+1 gap is descriptive, not a tradable NEXT_DAY_CONFIRM return",
    }


def bar_count_split(bars: Mapping[str, Sequence[ResearchBar]]) -> dict[str, Any]:
    total = sum(len(rows) for rows in bars.values())
    allowed = sum(1 for rows in bars.values() for bar in rows if bar.session_date <= ALLOWED_END)
    holdout = sum(1 for rows in bars.values() for bar in rows if bar.session_date >= HOLDOUT_START)
    return {
        "file_rows": total,
        "allowed_development_rows": allowed,
        "holdout_isolated_rows": holdout,
        "file_span_is_not_ten_year_evaluable": True,
    }


def layer_gaps() -> dict[str, Any]:
    return {
        "quote_close_signal": "current_list_yahoo_cache_A_available_when_VALID",
        "total_return": "TRI vs quote reported separately; no licensed action ledger",
        "execution": "raw/unadjusted/ADV/capacity EXECUTION_GATES_UNVERIFIED; C blocked",
        "membership": "current SECTORS list only; not a survivorship-free PIT proof",
        "split_tracks": [
            "current-list multi-profile multi-horizon signal diagnostics can run now",
            "raw/split/dividend/real volume block economic execution",
            "backward history shorter than ten evaluable years blocks decade research",
            "historical master/delist/classification blocks full-market PIT claims",
        ],
        "do_not_assume_vendor_has_custom_24_theme_history": True,
        "do_not_assume_api_rights": True,
    }


def run_fixed_matrix(*, root: Path | None = None, load_yahoo: bool = True, rescore_b0: bool = True) -> dict[str, Any]:
    base = root or REPO_ROOT
    yahoo_path = base / TRUSTED_RELATIVE_PICKLES[0]
    yahoo_bars: dict[str, list[ResearchBar]] = {}
    if load_yahoo and yahoo_path.is_file():
        yahoo_bars = load_trusted_research_bars(yahoo_path, root=root)
    spy_dates = [bar.session_date for bar in (yahoo_bars.get("SPY") or []) if _complete_t_day(bar)]
    quality = quality_pool(yahoo_bars) if yahoo_bars else {"per_security": {}, "counts": {}, "valid_n": 0}
    registry = load_registry()
    plan = capability_plan(quality, registry=registry)
    calendar = calendar_diff_audit(spy_dates)
    official = official_allowed_sessions()
    theme_rows = []
    if yahoo_bars:
        for theme in SECTORS:
            for family, holding in (("A_trend_quality", 5), ("A_trend_quality", 20), ("A_trend_quality", 63)):
                theme_rows.append(
                    theme_structure(
                        yahoo_bars,
                        theme=theme,
                        holding_sessions=holding,
                        warmup=registered_warmup(family, registry),
                        quality=quality,
                    )
                )
    mid = {"status": "SKIPPED", "reason": "rescore_b0_false"}
    selections = {"status": "SKIPPED"}
    if rescore_b0:
        mid = rescore_mid_from_b0(registry=registry)
        selections = recover_selection_sets(registry=registry)
    pilot = engineering_pilot(yahoo_bars)
    completed = [row["experiment_id"] for row in (mid.get("configs") or []) if row.get("completed")]
    pending = [row["experiment_id"] for row in plan if row["experiment_id"] not in set(completed)]
    signature = run_signature(
        registry={"protocol": PROTOCOL, "calendar_version": CALENDAR_VERSION},
        profile="all_three",
        horizon="all_three",
        label_horizons=(5, 20, 63),
        feature_version="us-eod-research-features-v1.5",
        statistics_version="us-eod-research-stats-v1.1",
        data_hash=EXPECTED_B0_SHA256,
        universe_version=DATASET_VERSION,
        member_policy="CURRENT_LIST_ONLY",
        reference_policy="PRICE_ONLY_OR_D_MARKET_RESIDUAL",
        start=official[0],
        end=ALLOWED_END,
        available_factors=("T", "M", "S", "B", "P", "V", "R"),
        timing_policy="NEXT_DAY_CONFIRM",
        capability_mask={"g": False, "execution": "UNVERIFIED"},
    )
    return {
        "protocol": PROTOCOL,
        "review_anchor": "c8cd25931f007ce826c7b1e3cd1a1726d48ffae0",
        "calendar_version": CALENDAR_VERSION,
        "dataset_version": DATASET_VERSION,
        "b0_feature_version": "us-eod-research-features-v1.5",
        "versions_not_spliced": True,
        "calendar": calendar,
        "quality": {
            "counts": quality.get("counts"),
            "valid_n": quality.get("valid_n"),
            "invalid_n": quality.get("invalid_n"),
            "spy_status": quality.get("spy_status"),
            "execution_gates": "EXECUTION_GATES_UNVERIFIED",
        },
        "bar_counts": bar_count_split(yahoo_bars) if yahoo_bars else {},
        "capability_plan": plan,
        "plan_n": len(plan),
        "theme_structure": theme_rows,
        "engineering_pilot": pilot,
        "mid_rescore": mid,
        "selection_sets": selections,
        "background_basket": background_basket_note(),
        "next_day_confirm": next_day_confirm_example(),
        "layer_gaps": layer_gaps(),
        "progress": {
            "signature": signature,
            "completed_experiment_ids": completed,
            "completed_n": len(completed),
            "pending_n": len(pending),
            "pending_sample": pending[:12],
            "resume": "PYTHONPATH=/workspace:/workspace/backend python research/option_pro_us_eod_v1/scripts/run_fixed_matrix.py",
        },
        "stop": {
            "new_weight_search": False,
            "holdout_unsealed": False,
            "executed_backtests": 0,
            "do_not_merge": True,
            "not_a_production_champion": True,
        },
    }


def write_return_pack(result: Mapping[str, Any], *, dest: Path | None = None) -> dict[str, Path]:
    out = dest or RETURN_PACK_DIR
    out.mkdir(parents=True, exist_ok=True)

    def dump(name: str, payload: Any) -> Path:
        path = out / name
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    written = {
        "calendar": dump("algorithm_fixed_matrix_calendar.json", result["calendar"]),
        "capabilities": dump("algorithm_fixed_matrix_capabilities.json", result["capability_plan"]),
        "quality": dump("algorithm_fixed_matrix_quality.json", result["quality"]),
        "theme": dump("algorithm_fixed_matrix_theme_table.json", result["theme_structure"]),
        "pilot": dump("algorithm_fixed_matrix_pilot.json", result["engineering_pilot"]),
        "mid": dump("algorithm_fixed_matrix_mid_rescore.json", result["mid_rescore"]),
        "selection": dump("algorithm_fixed_matrix_selection_sets.json", result["selection_sets"]),
        "gaps": dump("algorithm_fixed_matrix_layer_gaps.json", result["layer_gaps"]),
        "progress": dump("algorithm_fixed_matrix_progress.json", result["progress"]),
        "stop": dump("algorithm_fixed_matrix_stop.json", result["stop"]),
        "sources": dump(
            "algorithm_fixed_matrix_sources.json",
            {
                "protocol": PROTOCOL,
                "dataset_version": DATASET_VERSION,
                "calendar_version": CALENDAR_VERSION,
                "versions_not_spliced": True,
                "freeze_readiness_b0_not_overwritten": True,
                "executed_backtests": 0,
            },
        ),
    }
    checkpoint_dir = out / "fixed_matrix_runs" / f"run_{result['progress']['signature'][:16]}"
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    write_signed_checkpoint(
        checkpoint_dir / "checkpoint.json",
        {
            "completed_n": result["progress"]["completed_n"],
            "pending_n": result["progress"]["pending_n"],
            "protocol": PROTOCOL,
        },
        result["progress"]["signature"],
    )
    written["checkpoint"] = checkpoint_dir / "checkpoint.json"
    return written
