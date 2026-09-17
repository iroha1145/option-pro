"""Continuous-date 24-theme A/B/C/D on the approved window. Cache replay only.

Does not unseal 2024-07-01. Does not overwrite the 17-day closeout metrics.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pickle
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1 import FEATURE_VERSION  # noqa: E402
from app.services.research_eod_v1.calendar_asof import (  # noqa: E402
    HISTORICAL_RECONSTRUCTION,
    VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
    eod_evaluation_as_of,
    next_session,
    shift_sessions,
)
from app.services.research_eod_v1.config_load import load_registry  # noqa: E402
from app.services.research_eod_v1.constants import ALGORITHMS  # noqa: E402
from app.services.research_eod_v1.data.to_series import bars_to_series  # noqa: E402
from app.services.research_eod_v1.factors import extract_raw  # noqa: E402
from app.services.research_eod_v1.measurement import (  # noqa: E402
    attach_forward_label,
    pairing_diff,
    reference_panel,
)
from app.services.research_eod_v1.series import SecuritySeries  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.stats import (  # noqa: E402
    STATISTICS_VERSION,
    aggregate_ic_by_date,
    factor_ic_universe,
    grouped_ic,
    register_independent_events,
    snapshot_identity_key,
    spearman,
)
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_yahoo_abcd" / "bars.pkl"
PACK = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack"
OUT = PACK / "measurement_continuous_abcd.json"
LOG = PACK / "measurement_continuous_execution_log.jsonl"
ROWS = PACK / "measurement_factor_rows.jsonl"
IC_OUT = PACK / "measurement_ic_summary.json"
IC_GROUPS = PACK / "measurement_ic_groups.jsonl"
PAIR = PACK / "measurement_runner_pairing.json"
SUPERSEDED = PACK / "closeout_historical_abcd.SUPERSEDED.json"
REGISTRY = ROOT / "research" / "option_pro_us_eod_v1" / "config" / "registry.json"
CHECKPOINT = PACK / "measurement_continuous.checkpoint.json"
HOLDOUT_START = date(2024, 7, 1)
ALLOWED_END = date(2024, 6, 28)
FEATURE_READY_BARS = 420
LABELS = (5, 20, 63)
OLD_SESSIONS = (
    date(2023, 6, 12),
    date(2023, 6, 13),
    date(2023, 6, 14),
    date(2023, 6, 15),
    date(2023, 6, 16),
    date(2023, 6, 20),
    date(2023, 6, 21),
    date(2023, 6, 22),
    date(2023, 6, 23),
    date(2018, 12, 31),
    date(2019, 12, 31),
    date(2020, 12, 31),
    date(2021, 12, 31),
    date(2022, 12, 30),
    date(2023, 12, 29),
    date(2024, 3, 28),
    date(2024, 6, 28),
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_head() -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()


def _venue(track: str) -> dict:
    return {
        "listing_country": "US",
        "exchange": "NASDAQ",
        "mic": "XNAS",
        "security_type": "ETF" if track == "etf" else "CS",
        "identity_confidence": "unverified_default_not_checked",
        "industry_source": "unverified_economic_industry_missing",
    }


def _sessions_between(start: date, end: date) -> list[date]:
    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        try:
            cursor = next_session(cursor)
        except RuntimeError:
            break
    return days


def _clip(series: SecuritySeries, session: date) -> SecuritySeries | None:
    return series.slice_through(session)


def _build_panel(batched: dict) -> tuple[dict[str, SecuritySeries], list[dict]]:
    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(ticker, []).append(theme_id)
    panel: dict[str, SecuritySeries] = {}
    coverage = []
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = [bar for bar in (batched.get(ticker) or []) if bar.session_date <= ALLOWED_END]
        coverage.append(
            {
                "ticker": ticker,
                "themes": themes,
                "bars_clipped": len(bars),
                "first": bars[0].session_date.isoformat() if bars else None,
                "last": bars[-1].session_date.isoformat() if bars else None,
                "raw_history_years": None
                if not bars
                else round((bars[-1].session_date - bars[0].session_date).days / 365.25, 2),
                "status": "ok" if bars else "empty",
            }
        )
        if not bars:
            continue
        series = bars_to_series(
            bars,
            security_id=ticker,
            asset_track=track,
            theme_ids=tuple(themes),
            industry_id=None,
            parent_industry_id=None,
            venue_metadata=_venue(track),
            reconstruction_mode=HISTORICAL_RECONSTRUCTION,
        )
        if series is not None:
            panel[ticker] = series
    for extra in ("SPY", "QQQ"):
        if extra in panel or extra not in batched:
            continue
        bars = [bar for bar in batched[extra] if bar.session_date <= ALLOWED_END]
        series = bars_to_series(
            bars,
            security_id=extra,
            asset_track="etf",
            theme_ids=("etfs",),
            venue_metadata=_venue("etf"),
            reconstruction_mode=HISTORICAL_RECONSTRUCTION,
        )
        if series is not None:
            panel[extra] = series
    return panel, coverage


def _extract_raws(session_panel: dict[str, SecuritySeries], registry: dict, horizon: str) -> dict:
    blend = tuple(registry["horizons"][horizon]["momentum_blend"])
    gates = registry["sectors"]["semiconductors"]["gates"]
    market = session_panel.get("SPY")
    def _one(sid: str):
        return sid, extract_raw(
            session_panel[sid],
            market=market,
            panel=session_panel,
            horizon=horizon,
            momentum_blend=blend,
            sector_gates=gates,
        )

    raws = {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        for sid, raw in pool.map(_one, session_panel):
            raws[sid] = raw
    return raws


def _empty_theme_stats() -> dict[str, dict]:
    return {
        theme_id: {
            "theme_id": theme_id,
            "members_in_panel": 0,
            "valid_dates": 0,
            "zero_result_dates": 0,
            "missing_data_dates": 0,
            "warmup_dates": 0,
            "family": {algo: {"eligible": 0, "rejected": 0, "reasons": Counter()} for algo in ALGORITHMS},
            "funnel": Counter(),
            "eligible_name_sessions": 0,
        }
        for theme_id in SECTORS
    }


def _rebuild_theme_stats_from_artifacts(panel: dict[str, SecuritySeries]) -> tuple[dict[str, dict], int]:
    """Resume-safe: log + label_horizon=5 rows are the source of truth."""

    stats = _empty_theme_stats()
    for theme_id, sector in SECTORS.items():
        stats[theme_id]["members_in_panel"] = len([sid for sid in sector["tickers"] if sid in panel])
    executed = 0
    seen_theme_dates: dict[str, set[str]] = {theme_id: set() for theme_id in SECTORS}
    eligible_dates: dict[str, set[str]] = {theme_id: set() for theme_id in SECTORS}
    if LOG.exists():
        with LOG.open(encoding="utf-8") as handle:
            for line in handle:
                rec = json.loads(line)
                theme_id = rec["theme_id"]
                algo = rec["algorithm"]
                executed += 1
                fam = stats[theme_id]["family"][algo]
                elig = int(rec.get("eligible") or 0)
                cands = int(rec.get("candidates") or 0)
                fam["eligible"] += elig
                fam["rejected"] += max(0, cands - elig)
                stats[theme_id]["eligible_name_sessions"] += elig
                seen_theme_dates[theme_id].add(rec["session"])
                if elig:
                    eligible_dates[theme_id].add(rec["session"])
    if ROWS.exists():
        with ROWS.open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                if row.get("label_horizon") != 5:
                    continue
                theme_id = row["theme_id"]
                algo = row["algorithm"]
                stats[theme_id]["funnel"].update(_funnel_from_rows([row]))
                for reason in row.get("rejection_reasons") or ():
                    stats[theme_id]["family"][algo]["reasons"][str(reason)] += 1
    for theme_id, dates in seen_theme_dates.items():
        stats[theme_id]["valid_dates"] = len(dates)
        stats[theme_id]["zero_result_dates"] = len(dates - eligible_dates[theme_id])
    return stats, executed


def _relabel_factor_rows(panel: dict[str, SecuritySeries]) -> int:
    """Repair labels written from session-clipped series (no forward bars)."""

    if not ROWS.exists():
        return 0
    tmp = ROWS.with_suffix(".relabel.jsonl")
    updated = 0
    with ROWS.open(encoding="utf-8") as src, tmp.open("w", encoding="utf-8") as dst:
        for line in src:
            row = json.loads(line)
            series = panel.get(row.get("security_id"))
            horizon = int(row.get("label_horizon") or 0)
            session = date.fromisoformat(str(row.get("signal_session")))
            if series is None or not horizon:
                label = {"label": None, "label_matured_at": None, "reason": "NO_SERIES"}
            else:
                label = attach_forward_label(
                    series,
                    session,
                    horizon,
                    last_allowed=ALLOWED_END,
                    holdout_start=HOLDOUT_START,
                )
            if row.get("label") != label.get("label") or row.get("label_reason") != label.get("reason"):
                updated += 1
            row["label"] = label.get("label")
            row["label_matured_at"] = label.get("label_matured_at")
            row["label_reason"] = label.get("reason")
            dst.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    tmp.replace(ROWS)
    return updated


def _stream_ic_and_events() -> tuple[list[dict], dict, int]:
    outcomes = 0
    ic_pairs: list[dict] = []
    event_rows: list[dict] = []
    if not ROWS.exists():
        return [], register_independent_events([], continuous_calendar=True), 0
    with ROWS.open(encoding="utf-8") as handle:
        for line in handle:
            row = json.loads(line)
            outcomes += 1
            if factor_ic_universe(row) and row.get("label") is not None:
                ic_pairs.append(
                    {
                        "signal_session": row.get("signal_session"),
                        "theme_id": row.get("theme_id"),
                        "algorithm": row.get("algorithm"),
                        "profile": row.get("profile"),
                        "horizon": row.get("horizon"),
                        "label_horizon": row.get("label_horizon"),
                        "score": row.get("score"),
                        "label": row.get("label"),
                    }
                )
            if row.get("final_eligible") and row.get("label_horizon") == 5:
                event_rows.append(
                    {
                        "security_id": row.get("security_id"),
                        "algorithm": row.get("algorithm"),
                        "profile": row.get("profile"),
                        "horizon": row.get("horizon"),
                        "signal_session": row.get("signal_session"),
                        "final_eligible": True,
                        "status": "eligible",
                    }
                )
    groups = grouped_ic(ic_pairs)
    events = register_independent_events(event_rows, continuous_calendar=True)
    return groups, events, outcomes


def _yearly_from_groups(groups: list[dict]) -> dict[str, dict]:
    years: dict[str, dict] = defaultdict(lambda: {"defined_groups": 0, "undefined_groups": 0, "n_sum": 0, "ic_sum": 0.0})
    for row in groups:
        year = str(row.get("signal_session") or "")[:4]
        if not year:
            continue
        bucket = years[year]
        bucket["n_sum"] += int(row.get("n") or 0)
        if row.get("ic") is None:
            bucket["undefined_groups"] += 1
        else:
            bucket["defined_groups"] += 1
            bucket["ic_sum"] += float(row["ic"])
    out = {}
    for year, bucket in sorted(years.items()):
        defined = bucket["defined_groups"]
        out[year] = {
            "defined_groups": defined,
            "undefined_groups": bucket["undefined_groups"],
            "n_sum": bucket["n_sum"],
            "mean_grouped_ic": None if not defined else bucket["ic_sum"] / defined,
            "note": "mean of within-group ICs; pairs were not pooled",
        }
    return out


def _write_continuous_reports(
    *,
    panel: dict[str, SecuritySeries],
    coverage: list[dict],
    all_sessions: list[date],
    done_sessions: set[str],
    label_cut: dict,
    recomputes: int,
) -> dict:
    relabeled = _relabel_factor_rows(panel)
    theme_stats, executed = _rebuild_theme_stats_from_artifacts(panel)
    groups, events, outcomes = _stream_ic_and_events()
    years = [item["raw_history_years"] for item in coverage if item.get("raw_history_years")]
    firsts = [date.fromisoformat(item["first"]) for item in coverage if item.get("first")]
    lasts = [date.fromisoformat(item["last"]) for item in coverage if item.get("last")]
    raw_span = None if not (firsts and lasts) else round((max(lasts) - min(firsts)).days / 365.25, 2)
    empty_dates = 0
    valid_dates = 0
    for stats in theme_stats.values():
        valid_dates += stats["valid_dates"]
        empty_dates += stats["zero_result_dates"]
        stats["family"] = {
            algo: {
                "eligible": fam["eligible"],
                "rejected": fam["rejected"],
                "top_rejections": Counter(fam["reasons"]).most_common(8),
            }
            for algo, fam in stats["family"].items()
        }
        stats["funnel"] = dict(stats["funnel"])
        stats["sampled_eligible_set_changes"] = None
        stats["independent_events"] = None
        stats["event_definition"] = events
        stats["empty_result_ratio"] = None if not stats["valid_dates"] else stats["zero_result_dates"] / stats["valid_dates"]
    rows_hash = _sha256(ROWS) if ROWS.exists() else ""
    undefined_reasons = Counter(row.get("undefined_reason") or "defined" for row in groups)
    with IC_GROUPS.open("w", encoding="utf-8") as handle:
        for row in groups:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    summary = {
        "statistics_version": STATISTICS_VERSION,
        "groups_path": str(IC_GROUPS.relative_to(ROOT)),
        "groups_sha256": _sha256(IC_GROUPS) if IC_GROUPS.exists() else "",
        "group_count": len(groups),
        "defined_groups": sum(1 for row in groups if row["ic"] is not None),
        "undefined_groups": sum(1 for row in groups if row["ic"] is None),
        "undefined_reason_counts": dict(undefined_reasons),
        "valid_group_dates": sorted({row["signal_session"] for row in groups if row["ic"] is not None}),
        "valid_group_date_count": len({row["signal_session"] for row in groups if row["ic"] is not None}),
        "by_date_sample": dict(list(aggregate_ic_by_date(groups).items())[:8]),
        "by_year": _yearly_from_groups(groups),
        "note": "Full per-group IC is measurement_ic_groups.jsonl; this file is the review summary.",
    }
    IC_OUT.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    report = {
        "head_note": "continuous balanced/mid A/B/C/D; grouped average-rank IC; holdout sealed",
        "capture_mode": HISTORICAL_RECONSTRUCTION,
        "vendor_finalization_policy": VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END.isoformat(),
        "session_first": all_sessions[0].isoformat() if all_sessions else None,
        "session_last": all_sessions[-1].isoformat() if all_sessions else None,
        "session_count": len(all_sessions),
        "completed_sessions": len(done_sessions),
        "profile": "balanced",
        "horizon": "mid",
        "matrix_claimed": "24xABCD_balanced_mid_only_not_864",
        "label_horizons": list(LABELS),
        "label_maturity_cuts": {str(k): v.isoformat() for k, v in label_cut.items()},
        "feature_ready_bars": FEATURE_READY_BARS,
        "raw_history_years_span": raw_span,
        "raw_history_years_median": None if not years else float(np.median(years)),
        "raw_history_years_note": "2018-2024 clipped tape is not a decade",
        "evaluable_continuous_years": round(len(all_sessions) / 252.0, 2),
        "coverage": coverage,
        "themes": theme_stats,
        "empty_result_ratio_theme_dates": None if not valid_dates else empty_dates / valid_dates,
        "registered": 1920,
        "unique_registered_configs": 1920,
        "unique_snapshots": executed,
        "executed_snapshots": executed,
        "actual_function_calls": executed,
        "recomputes": recomputes,
        "outcomes_inspected": outcomes,
        "executed_backtests": 0,
        "engineering_fixtures": 0,
        "market_backtest_run": False,
        "winners": [],
        "event_ledger": events,
        "capability_flags": [
            "CURRENT_UNIVERSE_SURVIVORS",
            "PIT_CLASSIFICATION_MISSING",
            "CORPORATE_ACTIONS_INCOMPLETE",
            "EXECUTION_DATA_UNVERIFIED",
            "download_time_not_pit",
            "IC_IS_SIGNAL_DIAGNOSTIC_NOT_PNL",
            "INDUSTRY_UNVERIFIED",
        ],
        "param_hash": _sha256(REGISTRY),
        "code_sha": _git_head(),
        "data_hash": _sha256(CACHE),
        "statistics_version": STATISTICS_VERSION,
        "feature_version": FEATURE_VERSION,
        "rows_path": str(ROWS.relative_to(ROOT)),
        "rows_sha256": rows_hash,
        "execution_log": str(LOG.relative_to(ROOT)),
        "ic_groups_path": str(IC_GROUPS.relative_to(ROOT)),
        "ic_summary_path": str(IC_OUT.relative_to(ROOT)),
        "superseded_17day": str(SUPERSEDED.relative_to(ROOT)),
        "relabeled_rows": relabeled,
        "notes": [
            "Cache replay only; no Massive; no network purchase.",
            "Old 17-day IC/independent_events are SUPERSEDED_METRIC.",
            "Portfolio PnL not run; executed_backtests stays 0.",
            "Forward labels use the unclipped series through allowed_end.",
            "Stop after this fixed-parameter continuous pass.",
        ],
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    return report


def _funnel_from_rows(rows: list[dict]) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        reasons = [str(item) for item in (row.get("rejection_reasons") or ())]
        if row.get("status") == "eligible":
            counts["eligible"] += 1
        elif any(reason in {"MISSING_T_BAR", "LATE_SOURCE", "SOURCE_UNAVAILABLE", "INCOMPLETE_COMMON_INPUTS"} for reason in reasons):
            counts["data_missing"] += 1
        elif any("LOW_SCORE" in reason or reason == "LOW_SCORE" for reason in reasons):
            counts["score_floor"] += 1
        elif any(reason in {"ADV_TOO_LOW", "HIGH_ATR", "EXTENDED", "NOT_TRADABLE", "HALTED_SESSION"} for reason in reasons):
            counts["risk_or_tradability"] += 1
        elif reasons:
            counts["setup_not_met"] += 1
        else:
            counts["other_reject"] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-sessions", type=int, default=0)
    parser.add_argument("--pairing-only", action="store_true")
    parser.add_argument("--include-pairing", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    if not CACHE.exists():
        raise SystemExit(f"offline Yahoo cache missing: {CACHE}")
    batched = pickle.loads(CACHE.read_bytes())
    panel, coverage = _build_panel(batched)
    all_sessions = [day for day in _sessions_between(date(2018, 1, 2), ALLOWED_END) if day < HOLDOUT_START]
    if args.max_sessions:
        all_sessions = all_sessions[: args.max_sessions]
    registry = load_registry()
    label_cut = {horizon: shift_sessions(ALLOWED_END, -horizon) for horizon in LABELS}

    if args.pairing_only or args.include_pairing:
        pairing = []
        for session in [day for day in OLD_SESSIONS if day in set(all_sessions) or day <= ALLOWED_END][:3]:
            theme_id = "semiconductors"
            members = set(SECTORS[theme_id]["tickers"]) | {"SPY", "QQQ"}
            old_panel = {sid: series for sid, series in panel.items() if sid in members}
            old_win = {sid: clipped for sid, series in old_panel.items() if (clipped := _clip(series, session))}
            new_win = reference_panel({sid: clipped for sid, series in panel.items() if (clipped := _clip(series, session))}, theme_id, session)
            old_payload = compute_snapshot(
                eod_evaluation_as_of(session),
                old_win,
                "u_old_runner",
                registry,
                sector_id=theme_id,
                algorithm="A_trend_quality",
                source_finalized_through=session,
            )
            new_payload = compute_snapshot(
                eod_evaluation_as_of(session),
                new_win,
                "u_new_runner",
                registry,
                sector_id=theme_id,
                algorithm="A_trend_quality",
                source_finalized_through=session,
            )
            pairing.append({"session": session.isoformat(), **pairing_diff(old_payload, new_payload)})
        PAIR.write_text(json.dumps({"note": "old theme+SPY/QQQ vs full same-track pool; not a tune", "pairs": pairing}, indent=2, default=str) + "\n", encoding="utf-8")
        if args.pairing_only:
            print(json.dumps({"pairing": str(PAIR), "n": len(pairing)}))
            return 0

    SUPERSEDED.write_text(
        json.dumps(
            {
                "status": "SUPERSEDED_METRIC",
                "artifact": "closeout_historical_abcd.json",
                "superseded_fields": ["ic_forward_labels", "independent_events", "ic_pair_counts"],
                "replacement": [
                    "measurement_ic_summary.json",
                    "measurement_ic_groups.jsonl",
                    "measurement_continuous_abcd.json",
                    "measurement_factor_rows.jsonl",
                ],
                "reason": "pooled-across-days ordinal Spearman and disjoint-set event increment",
                "old_retrieved_at_unchanged": True,
                "old_invalid_eod_retrieved_at": "2026-09-16T17:24:41.368841+00:00",
                "old_code_sha": "0c1d419416f057fba2c8184abb54a2f029edbff7",
                "statistics_version_new": STATISTICS_VERSION,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    done_sessions: set[str] = set()
    if CHECKPOINT.exists():
        done_sessions = set(json.loads(CHECKPOINT.read_text(encoding="utf-8")).get("done_sessions", []))
    theme_stats = _empty_theme_stats()
    for theme_id in theme_stats:
        theme_stats[theme_id]["members_in_panel"] = len([sid for sid in SECTORS[theme_id]["tickers"] if sid in panel])
    executed = 0
    recomputes = 0
    if args.finalize_only:
        if CHECKPOINT.exists():
            done_sessions = set(json.loads(CHECKPOINT.read_text(encoding="utf-8")).get("done_sessions", []))
        report = _write_continuous_reports(
            panel=panel,
            coverage=coverage,
            all_sessions=all_sessions,
            done_sessions=done_sessions,
            label_cut=label_cut,
            recomputes=0,
        )
        print(json.dumps({"finalize_only": True, "executed_snapshots": report["executed_snapshots"], "sessions": report["completed_sessions"], "out": str(OUT)}))
        return 0
    if LOG.exists() and not done_sessions:
        LOG.unlink()
    if ROWS.exists() and not done_sessions:
        ROWS.unlink()
    log = LOG.open("a", encoding="utf-8")
    rows_handle = ROWS.open("a", encoding="utf-8")
    try:
        for session in all_sessions:
            if session.isoformat() in done_sessions:
                continue
            if session >= HOLDOUT_START:
                raise SystemExit(f"holdout leaked: {session}")
            as_of = eod_evaluation_as_of(session)
            clipped = {sid: item for sid, series in panel.items() if (item := _clip(series, session))}
            raw_cache: dict[str, dict] = {}
            any_usable = False
            for theme_id in SECTORS:
                refs = reference_panel(clipped, theme_id, session)
                if not refs:
                    theme_stats[theme_id]["missing_data_dates"] += 1
                    continue
                warmup = sum(1 for series in refs.values() if series.security_id not in {"SPY", "QQQ"} and len(series.dates) < FEATURE_READY_BARS)
                members = [series for sid, series in refs.items() if sid not in {"SPY", "QQQ"}]
                if members and all(len(series.dates) < FEATURE_READY_BARS for series in members):
                    theme_stats[theme_id]["warmup_dates"] += 1
                theme_stats[theme_id]["valid_dates"] += 1
                any_usable = True
                track = "etf" if theme_id == "etfs" else "stock"
                if track not in raw_cache:
                    # Shared residual/pivots once per track. Theme gates reapplied
                    # on candidates inside compute_snapshot.
                    raw_cache[track] = _extract_raws(refs, registry, "mid")
                raws = raw_cache[track]
                any_eligible = False
                for algorithm in ALGORITHMS:
                    payload = compute_snapshot(
                        as_of,
                        refs,
                        "u_measurement_continuous",
                        registry,
                        sector_id=theme_id,
                        algorithm=algorithm,
                        profile="balanced",
                        horizon="mid",
                        source_finalized_through=session,
                        precomputed_raws=raws,
                    )
                    executed += 1
                    fam = theme_stats[theme_id]["family"][algorithm]
                    elig = 0
                    for row in payload["rows"]:
                        for reason in row.get("rejection_reasons") or ():
                            fam["reasons"][str(reason)] += 1
                        if row.get("status") == "eligible":
                            elig += 1
                            any_eligible = True
                        for label_horizon in LABELS:
                            label = attach_forward_label(
                                panel[row["security_id"]],
                                session,
                                label_horizon,
                                last_allowed=ALLOWED_END,
                                holdout_start=HOLDOUT_START,
                            ) if row["security_id"] in panel else {"label": None, "label_matured_at": None, "reason": "NO_SERIES"}
                            record = {
                                "security_id": row["security_id"],
                                "signal_session": session.isoformat(),
                                "theme_id": theme_id,
                                "algorithm": algorithm,
                                "profile": "balanced",
                                "horizon": "mid",
                                "label_horizon": label_horizon,
                                "setup_id": row.get("setup_state"),
                                "source": "historical_reconstruction",
                                "feature_version": FEATURE_VERSION,
                                "score_version": "us-eod-research-score-v1",
                                "statistics_version": STATISTICS_VERSION,
                                "label_matured_at": label.get("label_matured_at"),
                                "factors": row.get("factors"),
                                "score": row.get("score"),
                                "setup_gate": row.get("setup_state"),
                                "final_eligible": row.get("status") == "eligible",
                                "status": row.get("status"),
                                "label": label.get("label"),
                                "label_reason": label.get("reason"),
                                "rejection_reasons": list(row.get("rejection_reasons") or ()),
                                "industry_source": row.get("industry_source"),
                                "snapshot_key": snapshot_identity_key(
                                    session=session.isoformat(),
                                    theme_id=theme_id,
                                    algorithm=algorithm,
                                    profile="balanced",
                                    horizon="mid",
                                    security_id=row["security_id"],
                                ),
                            }
                            rows_handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
                    fam["eligible"] += elig
                    fam["rejected"] += max(0, len(payload["rows"]) - elig)
                    theme_stats[theme_id]["eligible_name_sessions"] += elig
                    theme_stats[theme_id]["funnel"].update(_funnel_from_rows(payload["rows"]))
                    log.write(
                        json.dumps(
                            {
                                "session": session.isoformat(),
                                "theme_id": theme_id,
                                "algorithm": algorithm,
                                "candidates": len(payload["candidate_ids"]),
                                "references": len(payload["reference_ids"]),
                                "eligible": elig,
                                "eligible_ids": [
                                    row["security_id"] for row in payload["rows"] if row.get("status") == "eligible"
                                ],
                            }
                        )
                        + "\n"
                    )
                if not any_eligible:
                    theme_stats[theme_id]["zero_result_dates"] += 1
            if not any_usable:
                pass
            rows_handle.flush()
            log.flush()
            done_sessions.add(session.isoformat())
            CHECKPOINT.write_text(json.dumps({"done_sessions": sorted(done_sessions)}), encoding="utf-8")
            print(json.dumps({"session": session.isoformat(), "executed": executed, "done": len(done_sessions)}), flush=True)
    finally:
        rows_handle.close()
        log.close()

    report = _write_continuous_reports(
        panel=panel,
        coverage=coverage,
        all_sessions=all_sessions,
        done_sessions=done_sessions,
        label_cut=label_cut,
        recomputes=recomputes,
    )
    print(json.dumps({"executed_snapshots": report["executed_snapshots"], "sessions": report["completed_sessions"], "out": str(OUT), "rows": str(ROWS)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
