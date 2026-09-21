"""Frozen cross-section comparisons. No orders, forecasts, network or publication.

The first three variants retain production feature windows, including D63skip5.
Only the fourth adds explicitly windowed, non-risk-adjusted branches. A proxy
liquidity filter never certifies dollar volume or regular-session volume.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import fields
from datetime import date
from functools import lru_cache
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np

from app.services.research_eod_v1.calendar_asof import eod_evaluation_as_of, shift_sessions
from app.services.research_eod_v1.constants import ALGORITHMS, HORIZONS, PROFILES
from app.services.research_eod_v1.cross_section import q_star
from app.services.research_eod_v1.membership import has_complete_session_bar, source_is_available
from app.services.research_eod_v1.series import session_date_is_halted
from app.services.research_eod_v1.snapshot import ATR_REFERENCE_POLICIES, compute_snapshot
from app.services.research_eod_v1.venue import classify_venue
from .inference import precompute_all_horizon_inputs
from .price_only import apply_price_only_track

VARIANTS = ("baseline", "track_atr", "entry_state", "raw_momentum")
SHADOW_VERSION = "eod-shadow-v2"
WINDOWS = {
    "short": {"relative_sessions": 20, "acceleration_sessions": 5},
    "mid": {"relative_sessions": 63, "acceleration_sessions": 20},
    "long": {"relative_sessions": 126, "acceleration_sessions": 63},
}
QUALIFICATION_REASONS = {"DOLLAR_LIQUIDITY_UNVERIFIED", "VOLUME_SESSION_UNVERIFIED"}
ETF_SUBTYPES = {"fixed_income", "leverage", "inverse", "options_income"}
REFERENCE_MIN_HISTORY = 252


def _json_value(value):
    if isinstance(value, np.ndarray):
        return [_json_value(v) for v in value.tolist()]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, (date, Path)):
        return value.isoformat() if isinstance(value, date) else str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def _digest(value) -> str:
    return hashlib.sha256(json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def panel_hash(panel: Mapping[str, Any]) -> str:
    """Hash every used series field, not only a count or a symbol list."""
    digest = hashlib.sha256()
    for sid, series in sorted(panel.items()):
        payload = {field.name: getattr(series, field.name) for field in fields(series)}
        digest.update(_digest([sid, payload]).encode())
    return digest.hexdigest()


def etf_classification(series) -> dict:
    metadata = dict(series.venue_metadata)
    subtype = metadata.get("etf_subtype")
    evidence = metadata.get("etf_subtype_source")
    verified = metadata.get("etf_subtype_verified") is True
    if series.asset_track != "etf":
        return {"subtype": "not_applicable", "source": None}
    if subtype in ETF_SUBTYPES and evidence and verified:
        return {"subtype": subtype, "source": str(evidence)}
    return {"subtype": "unknown", "source": None}


@lru_cache(maxsize=64)
def _session_grid(session: date, sessions: int, offset: int = 0) -> tuple[date, ...]:
    end = shift_sessions(session, -offset)
    reverse = [end]
    for _ in range(sessions):
        reverse.append(shift_sessions(reverse[-1], -1))
    return tuple(reversed(reverse))


def _return(prices: Mapping[date, float], grid: Sequence[date]) -> tuple[float | None, list[str]]:
    """Never extend a window to replace a missing session with an older bar."""
    missing = [day.isoformat() for day in grid if day not in prices or not math.isfinite(prices[day]) or prices[day] <= 0]
    if missing:
        return None, missing
    return float(prices[grid[-1]] / prices[grid[0]] - 1), []


def _reference_rejections(series, session: date) -> list[str]:
    """Snapshot's source/T checks plus the E/F preregistered history floor.

    Snapshot first removes unavailable sources and incomplete T bars, then uses
    SPY/QQQ or same-track peers. E/F retain track-separated q_star pools and add
    the common 252-session history floor; no ADV evidence is manufactured.
    """
    reasons = []
    if not source_is_available(series, eod_evaluation_as_of(session)):
        reasons.append("SOURCE_UNAVAILABLE")
    if not has_complete_session_bar(series, session):
        reasons.append("MISSING_T_BAR")
    if sum(day <= session for day in series.dates) < REFERENCE_MIN_HISTORY:
        reasons.append("SHORT_HISTORY")
    if not classify_venue(dict(series.venue_metadata)).eligible:
        reasons.append("OUT_OF_SCOPE")
    index = series.index_on_or_before(session)
    if index is not None:
        volume = float(series.volume[index])
        if session_date_is_halted(series, session) or (math.isfinite(volume) and volume <= 0):
            reasons.append("NOT_TRADABLE")
        if not math.isfinite(float(series.raw_close[index])) or series.raw_close[index] <= 0:
            reasons.append("INVALID_RAW_PRICE")
    return reasons


def _benchmark(series) -> tuple[str | None, str | None]:
    if series.asset_track == "stock":
        return "SPY", "shadow-v2-stock-relative-to-SPY"
    metadata = dict(series.venue_metadata)
    ticker = metadata.get("etf_relative_benchmark_ticker")
    evidence = metadata.get("etf_relative_benchmark_source")
    if isinstance(ticker, str) and ticker.strip() and evidence and metadata.get("etf_relative_benchmark_verified") is True:
        return ticker.strip(), str(evidence)
    return None, None


def momentum_features(panel, horizon: str, *, session: date) -> dict:
    """Calendar-aligned close returns; F is absolute, not benchmark-relative."""
    window = WINDOWS[horizon]
    relative_n, acceleration_n = window["relative_sessions"], window["acceleration_sessions"]
    relative_grid = _session_grid(session, relative_n)
    recent_grid = _session_grid(session, acceleration_n)
    previous_grid = _session_grid(session, acceleration_n, acceleration_n)
    price_maps = {sid: {day: float(series.close[i]) for i, day in enumerate(series.dates)
                       if day <= session and not (series.bar_partial is not None and series.bar_partial[i])}
                  for sid, series in panel.items()}
    as_of = eod_evaluation_as_of(session)
    result = {}
    for sid, series in panel.items():
        reasons = _reference_rejections(series, session)
        total, relative_missing = _return(price_maps[sid], relative_grid)
        recent, recent_missing = _return(price_maps[sid], recent_grid)
        previous, previous_missing = _return(price_maps[sid], previous_grid)
        benchmark_ticker, benchmark_source = _benchmark(series)
        benchmark_series = panel.get(benchmark_ticker)
        benchmark_return, benchmark_missing = None, []
        benchmark_status = "missing_benchmark"
        if benchmark_series is not None:
            if not source_is_available(benchmark_series, as_of) or not has_complete_session_bar(benchmark_series, session):
                benchmark_status = "benchmark_unavailable"
            else:
                benchmark_return, benchmark_missing = _return(price_maps[benchmark_ticker], relative_grid)
                benchmark_status = "ok" if benchmark_return is not None else "missing_benchmark_prices"
        result[sid] = {
            "relative_return": None if reasons or total is None or benchmark_return is None else total - benchmark_return,
            "price_return": None if reasons else total,
            "benchmark_return": benchmark_return,
            "benchmark_ticker": benchmark_ticker,
            "benchmark_source": benchmark_source,
            "benchmark_status": benchmark_status,
            "recent_return": None if reasons else recent,
            "previous_return": None if reasons else previous,
            "acceleration": None if reasons or recent is None or previous is None else recent - previous,
            "acceleration_basis": "absolute_close_price_return_difference_adjacent_equal_windows",
            "reference_eligible": not reasons,
            "reference_rejection_reasons": reasons,
            "relative_return_reason": "REFERENCE_INELIGIBLE" if reasons else "MISSING_BENCHMARK" if benchmark_status != "ok" else "MISSING_WINDOW_PRICE" if relative_missing else None,
            "acceleration_reason": "REFERENCE_INELIGIBLE" if reasons else "MISSING_WINDOW_PRICE" if recent_missing or previous_missing else None,
            "window_dates": {"relative_start": relative_grid[0].isoformat(), "relative_end": session.isoformat(),
                             "recent_start": recent_grid[0].isoformat(), "recent_end": session.isoformat(),
                             "previous_start": previous_grid[0].isoformat(), "previous_end": previous_grid[-1].isoformat()},
            "missing_sessions": {"relative": relative_missing, "recent": recent_missing, "previous": previous_missing, "benchmark": benchmark_missing},
            **window,
        }
    return result


def _entry_view(strict: Mapping, relaxed: Mapping | None = None) -> dict:
    """Keep strict eligibility untouched; relax only observation's public gate."""
    out = dict(strict)
    observed = strict if relaxed is None else relaxed
    out["strict_status"] = strict.get("status")
    out["strict_eligible"] = strict.get("status") == "eligible"
    out["observation_status"] = observed.get("status")
    out["observation_included"] = observed.get("status") in {"eligible", "watch"}
    out["observation_rejection_reasons"] = list(observed.get("rejection_reasons") or [])
    reasons = set(strict.get("rejection_reasons") or [])
    out["entry_state"] = ("extended" if "EXTENDED" in reasons else "ready" if out["strict_eligible"] else "data_unverified" if reasons and reasons <= QUALIFICATION_REASONS else "blocked")
    out["new_entry_allowed"] = out["strict_eligible"]
    return out


def _relax_public_extension(payload: Mapping) -> dict:
    result = {**payload, "rows": []}
    for row in payload["rows"]:
        updated = dict(row)
        gates = row.get("gate_results") or {}
        if "EXTENDED" in gates.get("common", ()) and "EXTENDED" not in gates.get("setup", ()):
            updated["rejection_reasons"] = [reason for reason in row.get("rejection_reasons", ()) if reason != "EXTENDED"]
        result["rows"].append(updated)
    return result


def _new_branches(raw_a_rows, panel, features, registry, profile, horizon):
    tracks = {sid: s.asset_track for sid, s in panel.items()}
    # No invented industry or G; transparent within-track percentile scores.
    ranks = {key: q_star({sid: f[key] if f["reference_eligible"] else None for sid, f in features.items()}, industry={}, parent={}, tracks=tracks)
             for key in ("relative_return", "acceleration")}
    rows = []
    for source in raw_a_rows:
        sid = source["security_id"]
        f = features.get(sid, {})
        for key, name in (("relative_return", "E_raw_relative_momentum"), ("acceleration", "F_short_acceleration")):
            score = ranks[key].get(sid)
            reasons = [reason for reason in source.get("gate_results", {}).get("common", ())
                       if reason not in {"LOW_SCORE", "MISSING_SCORE", "LOW_COVERAGE"}]
            reasons.extend(f.get("reference_rejection_reasons", ()))
            feature_reason = f.get("relative_return_reason" if key == "relative_return" else "acceleration_reason")
            if feature_reason:
                reasons.append(feature_reason)
            if score is None:
                reasons.append("MISSING_SCORE")
            elif score < registry["profiles"][profile]["score_floor"]:
                reasons.append("LOW_SCORE")
            if f.get(key) is None or f[key] <= 0:
                reasons.append("NONPOSITIVE_RELATIVE_MOMENTUM" if key == "relative_return" else "NONPOSITIVE_ACCELERATION")
            if key == "acceleration" and (f.get("recent_return") is None or f["recent_return"] <= 0):
                reasons.append("NONPOSITIVE_RECENT_RETURN")
            # Unknown evidence always remains false, including when EXTENDED masks it.
            reasons.append("DOLLAR_LIQUIDITY_UNVERIFIED")
            strict = {**source, "algorithm_id": name, "score": score,
                      "factors": {key: score}, "score_components": {} if score is None else {key: score},
                      "configured_weights": {key: 1.0}, "effective_weights": {key: 1.0},
                      "observed_feature_coverage": 0.0 if score is None else 1.0,
                      "signal_features": f, "momentum_basis": "close_price_return_not_risk_adjusted",
                      "rejection_reasons": list(dict.fromkeys(reasons)),
                      "status": "watch" if set(reasons) <= QUALIFICATION_REASONS else "rejected"}
            observed_reasons = [reason for reason in reasons if reason != "EXTENDED"]
            observed = {**strict, "status": "watch" if set(observed_reasons) <= QUALIFICATION_REASONS else "rejected",
                        "rejection_reasons": observed_reasons}
            strict["gate_results"] = {"common": tuple(reasons), "setup": (), "venue": source.get("gate_results", {}).get("venue")}
            rows.append(_entry_view(strict, observed))
    return rows


def _best(rows, *, strict=False, track=None):
    candidates = {}
    for row in rows:
        if track is not None and row.get("stock_or_etf_track") != track:
            continue
        if not row.get("strict_eligible" if strict else "observation_included") or row.get("score") is None:
            continue
        sid = row["security_id"]
        # Stable tie-break independent of input ordering.
        order = (-float(row["score"]), str(row["algorithm_id"]), str(row["sector_context"]))
        if sid not in candidates or order < candidates[sid][0]:
            candidates[sid] = (order, row)
    return sorted((row for _, row in candidates.values()), key=lambda row: (-float(row["score"]), row["security_id"]))


def _compact(row):
    keys = ("security_id", "algorithm_id", "sector_context", "stock_or_etf_track", "score", "strict_status", "strict_eligible", "observation_status", "entry_state", "new_entry_allowed", "rejection_reasons", "observation_rejection_reasons", "atr_pct", "sector_median_atr_pct", "atr_reference_n", "atr_reference_source", "atr_threshold_pct", "extension_atr", "extension_limit_atr", "adv20", "signal_features", "etf_classification")
    return {key: row.get(key) for key in keys}


def _summarize(rows, registry, *, combined: bool, top_k: int):
    observed = _best(rows)
    strict = _best(rows, strict=True)
    known_adv = [r for r in observed if r.get("adv20") is not None]
    minimum = ATR_REFERENCE_POLICIES["track_liquid_v1"]["minimum_adv20_proxy"]
    families = {}
    for family in sorted({r["algorithm_id"] for r in rows}):
        subset = [r for r in rows if r["algorithm_id"] == family]
        families[family] = {"rows": len(subset), "strict_securities": len(_best(subset, strict=True)),
                            "observation_securities": len(_best(subset)),
                            "rejections": dict(Counter(reason for r in subset for reason in r.get("rejection_reasons", ())))}
    themes = {}
    for theme in registry["sectors"]:
        if theme == "all_market_stocks":
            continue
        subset = [r for r in rows if r["sector_context"] == theme and r.get("score") is not None]
        themes[theme] = {"scored_securities": len({r["security_id"] for r in subset}),
                         "observation_securities": len(_best(subset)), "strict_securities": len(_best(subset, strict=True))}
    boundary = []
    for metric, value, limit in (("atr", "atr_pct", "atr_threshold_pct"), ("extension", "extension_atr", "extension_limit_atr")):
        valid = [r for r in rows if r.get(value) is not None and r.get(limit) is not None]
        chosen = {}
        for row in sorted(valid, key=lambda r: (abs(r[value] - r[limit]), r["security_id"], r["algorithm_id"])):
            chosen.setdefault(row["security_id"], {**_compact(row), "boundary": metric, "distance_to_limit": row[value] - row[limit]})
            if len(chosen) >= 12:
                break
        boundary.extend(chosen.values())
    return {
        "row_status_counts": dict(Counter(r.get("strict_status") for r in rows)),
        "strict_security_count": len(strict), "observation_security_count": len(observed),
        "families": families, "theme_coverage": themes,
        "covered_theme_count": sum(v["observation_securities"] > 0 for v in themes.values()),
        "theme_count": len(themes),
        "liquidity_proxy": {"floor_usd": minimum, "observation_known_n": len(known_adv),
                            "observation_unknown_n": len(observed) - len(known_adv),
                            "low_n": sum(r["adv20"] < minimum for r in known_adv),
                            "low_fraction_of_known": None if not known_adv else sum(r["adv20"] < minimum for r in known_adv) / len(known_adv),
                            "qualification_verified": False},
        "rankings": {track: [_compact(r) for r in _best(rows, track=track)[:top_k]] for track in ("stock", "etf")},
        "strict_rankings": {track: [_compact(r) for r in _best(rows, strict=True, track=track)[:top_k]] for track in ("stock", "etf")},
        "combined_baseline_ranking": [_compact(r) for r in observed[:top_k]] if combined else None,
        "etf_subtypes": dict(Counter(r["etf_classification"]["subtype"] for r in observed if r.get("stock_or_etf_track") == "etf")),
        "etf_subtype_rankings": {subtype: [_compact(r) for r in _best(
            [row for row in rows if row.get("etf_classification", {}).get("subtype") == subtype], track="etf")[:top_k]]
            for subtype in sorted(ETF_SUBTYPES | {"unknown"})},
        "boundaries": boundary,
    }


def run_shadow_comparison(panel, session: date, *, registry, profiles: Sequence[str] = PROFILES,
                          horizons: Sequence[str] = HORIZONS, themes: Sequence[str] | None = None,
                          source_manifest: Mapping | None = None, precomputed_horizon_inputs=None,
                          geometry_workers: int = 1, top_k: int = 20, verify_stock_isolation: bool = True,
                          progress_callback=None, stock_isolation_remove_ids: Sequence[str] | None = None):
    if not horizons or not profiles or top_k < 1:
        raise ValueError("at least one horizon/profile and positive top_k are required")
    if source_manifest and source_manifest.get("session") not in {None, session.isoformat()}:
        raise ValueError("source manifest session differs from the frozen comparison session")
    themes = list(themes or registry["sectors"])
    inputs = precomputed_horizon_inputs
    if inputs is None:
        inputs = precompute_all_horizon_inputs(panel, session, registry=registry, horizons=horizons,
                                             themes=themes, geometry_workers=geometry_workers)
    frozen_panel = inputs[horizons[0]][1]
    frozen_hash = panel_hash(frozen_panel)
    for horizon in horizons:
        if inputs[horizon][1] is not frozen_panel and panel_hash(inputs[horizon][1]) != frozen_hash:
            raise ValueError("horizons must share the same frozen panel")
    frozen = {"session": session.isoformat(), "panel_hash": frozen_hash,
              "universe_hash": _digest(sorted((sid, s.asset_track, s.theme_ids) for sid, s in frozen_panel.items())),
              "registry_hash": _digest(registry), "source_manifest": dict(source_manifest or {})}
    frozen["input_hash"] = _digest(frozen)
    results = {variant: {} for variant in VARIANTS}
    isolation_failures = []
    if stock_isolation_remove_ids is None:
        removed_etfs = sorted(sid for sid, s in frozen_panel.items() if s.asset_track == "etf" and sid not in {"SPY", "QQQ"})
    else:
        removed_etfs = sorted(set(stock_isolation_remove_ids))
        if any(sid not in frozen_panel or frozen_panel[sid].asset_track != "etf" or sid in {"SPY", "QQQ"} for sid in removed_etfs):
            raise ValueError("stock isolation removals require present nonbenchmark ETFs")
    removed_etf_ids = set(removed_etfs)
    for horizon in horizons:
        raws, clipped, themed = inputs[horizon]
        features = momentum_features(clipped, horizon, session=session)
        smaller = {sid: s for sid, s in clipped.items() if sid not in removed_etf_ids}
        smaller_features = {sid: value for sid, value in features.items() if sid in smaller}
        # All factor and reference caches stay private to a variant and horizon.
        caches = {name: {} for name in ("baseline", "track_atr", "isolation")}
        for profile in profiles:
            accumulated = {variant: [] for variant in VARIANTS}
            for theme in themes:
                for family in ALGORITHMS:
                    args = dict(sector_id=theme, algorithm=family, profile=profile, horizon=horizon,
                                source_finalized_through=session, precomputed_raws=themed[theme],
                                reapply_theme_gates=False, already_session_clipped=True, config_digest=frozen["registry_hash"])
                    track_payload = None
                    for variant, policy in (("baseline", "legacy"), ("track_atr", "track_liquid_v1")):
                        payload = compute_snapshot(eod_evaluation_as_of(session), clipped, frozen["universe_hash"], registry,
                                                   **args, snapshot_cache=caches[variant], atr_reference_policy=policy)
                        score_args = dict(registry=registry, theme_id=theme, family=family, profile=profile,
                                          horizon=horizon, volume_verified=False, dollar_liquidity_verified=False,
                                          volume_session_verified=False)
                        scored = apply_price_only_track(payload, **score_args)
                        accumulated[variant].extend(_entry_view(row) for row in scored["rows"])
                        if variant == "track_atr":
                            track_payload = payload
                            relaxed = apply_price_only_track(_relax_public_extension(payload), **score_args)
                            entry = [_entry_view(strict, observed) for strict, observed in zip(scored["rows"], relaxed["rows"])]
                            accumulated["entry_state"].extend(entry)
                            accumulated["raw_momentum"].extend(entry)
                    if family == "A_trend_quality":
                        new_rows = _new_branches(track_payload["rows"], clipped, features, registry, profile, horizon)
                        accumulated["raw_momentum"].extend(new_rows)
                        if verify_stock_isolation and removed_etfs:
                            isolated_new = _new_branches([r for r in track_payload["rows"] if r["security_id"] in smaller], smaller, smaller_features, registry, profile, horizon)
                            def branch_stock_facts(rows):
                                return {(r["security_id"], r["algorithm_id"]): (r["score"], r["rejection_reasons"], r["observation_included"])
                                        for r in rows if r.get("stock_or_etf_track") == "stock"}
                            if branch_stock_facts(new_rows) != branch_stock_facts(isolated_new):
                                isolation_failures.append({"profile": profile, "horizon": horizon, "theme": theme, "family": "E/F"})
                    if verify_stock_isolation and removed_etfs and registry["sectors"][theme].get("asset_track") != "etf":
                        smaller_raws = {sid: r for sid, r in themed[theme].items() if sid not in removed_etf_ids}
                        isolated = compute_snapshot(eod_evaluation_as_of(session), smaller, frozen["universe_hash"] + ":stock-isolation", registry,
                                                    **{**args, "precomputed_raws": smaller_raws}, snapshot_cache=caches["isolation"], atr_reference_policy="track_liquid_v1")
                        def stock_facts(payload):
                            return {r["security_id"]: {k: r.get(k) for k in ("score", "factors", "rejection_reasons", "sector_median_atr_pct", "atr_reference_n")}
                                    for r in payload["rows"] if r.get("stock_or_etf_track") == "stock"}
                        if stock_facts(isolated) != stock_facts(track_payload):
                            isolation_failures.append({"profile": profile, "horizon": horizon, "theme": theme, "family": family})
            for variant, rows in accumulated.items():
                for row in rows:
                    series = clipped.get(row["security_id"])
                    row["etf_classification"] = etf_classification(series) if series else {"subtype": "unknown", "source": None}
                results[variant][f"{profile}/{horizon}"] = _summarize(rows, registry, combined=variant == "baseline", top_k=top_k)
            if progress_callback is not None:
                progress_callback({"profile": profile, "horizon": horizon, "completed": True})
    overlap = {}
    for variant in VARIANTS:
        overlap[variant] = {}
        for profile in profiles:
            for track in ("stock", "etf"):
                for i, left in enumerate(horizons):
                    for right in horizons[i + 1:]:
                        a = {r["security_id"] for r in results[variant][f"{profile}/{left}"]["rankings"][track]}
                        b = {r["security_id"] for r in results[variant][f"{profile}/{right}"]["rankings"][track]}
                        overlap[variant][f"{profile}/{track}/{left}:{right}"] = {"intersection_n": len(a & b), "union_n": len(a | b), "jaccard": len(a & b) / len(a | b) if a | b else None}
    return _json_value({"schema_version": SHADOW_VERSION, "frozen_inputs": frozen,
                       "scope": "current_cross_section_only_not_backtest_or_return_improvement_evidence",
                       "universe_scope": (source_manifest or {}).get("directory_scope", (source_manifest or {}).get("universe", "provided_panel")),
                       "qualification_flags": {"volume_verified": False, "dollar_liquidity_verified": False, "volume_session_verified": False, "G_enabled": False},
                       "policies": {"atr": ATR_REFERENCE_POLICIES, "new_branch_windows": WINDOWS,
                                    "new_branch_reference_pool": {"minimum_history_sessions": REFERENCE_MIN_HISTORY,
                                        "filters": ["source_is_available_at_eod", "complete_T_bar", "eligible_venue", "positive_raw_price", "not_halted_or_zero_volume", "complete_required_calendar_price_grid"],
                                        "ranking": "within_asset_track; missing feature values excluded; no liquidity certification"},
                                    "new_branch_benchmarks": {"E_stock": "SPY", "E_etf": "verified matched benchmark metadata required; otherwise missing_benchmark", "F": "absolute acceleration, no benchmark"},
                                    "existing_feature_windows": "unchanged production windows; D remains 63 sessions skipping latest 5",
                                    "etf_subtypes": sorted(ETF_SUBTYPES), "unknown_subtype": "unknown"},
                       "variants": results, "horizon_overlap": overlap,
                       "stock_isolation": {"checked": verify_stock_isolation and bool(removed_etfs), "removed_nonbenchmark_etf_n": len(removed_etfs),
                                           "removed_etf_ids": removed_etfs,
                                           "passed": not isolation_failures if verify_stock_isolation and removed_etfs else None,
                                           "failures": isolation_failures, "method": "same precomputed inputs, remove nonbenchmark ETFs; compare A/B/C/D stock scores/factors/references/rejections and E/F stock scores/rejections/observation status"}})


def write_shadow_report(report: Mapping, output: str | Path):
    root = Path(output)
    root.mkdir(parents=True, exist_ok=True)
    (root / "shadow-comparison.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    frozen = report["frozen_inputs"]
    lines = ["# 收盘选股影子对照", "", f"交易日：{frozen['session']}。输入摘要：`{frozen['input_hash']}`。", "",
             f"输入范围：{report.get('universe_scope', 'provided_panel')}。", "",
             "这份报告只比较同一日截面。没有历史收益检验，不能据此认定方案提高收益。成交额与量能资格均未验证，行业因子 G 禁用；ETF 未有分类证据时列为 unknown。", "",
             "前三方案保留生产特征窗口，D 保持 63 日并跳过最近 5 日。第四方案新增相对动量窗口为 20/63/126 日；加速窗口为相邻两段 5/20/63 日。所有方案严格入场资格保持约束。", "",
             "新增分支按交易日网格对齐，必要日期缺价则不计算，也不以更早记录补足条数。参照证券须在当日可用、具有完整当日行情及至少 252 条历史，按资产轨道分别排序。股票相对 SPY；ETF 相对动量须有已验证的匹配基准证据，否则禁用。加速分支仅比较绝对价格收益。", "",
             "| 方案 | 风险档/周期 | 严格合格数 | 强度观察数 | 有观察结果主题数 | 低成交额代理占比 |", "|---|---|---:|---:|---:|---:|"]
    for variant, groups in report["variants"].items():
        for group, data in groups.items():
            ratio = data["liquidity_proxy"]["low_fraction_of_known"]
            lines.append(f"| {variant} | {group} | {data['strict_security_count']} | {data['observation_security_count']} | {data['covered_theme_count']}/{data['theme_count']} | {'未知' if ratio is None else f'{ratio:.1%}'} |")
    lines += ["", f"股票隔离检查：{json.dumps(report['stock_isolation'], ensure_ascii=False)}", "",
              "完整家族分布、24 主题覆盖、股票与 ETF 分榜、周期重合及门槛边界个股见同目录机器结果。分数是各分支定义下的观察分，跨资产、跨分支分数未经历史收益校准。", ""]
    (root / "shadow-comparison.md").write_text("\n".join(lines), encoding="utf-8")
