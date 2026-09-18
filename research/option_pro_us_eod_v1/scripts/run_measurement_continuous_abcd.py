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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from multiprocessing import get_context
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
from app.services.research_eod_v1.factors import apply_sector_gates, extract_raw  # noqa: E402
from app.services.research_eod_v1.membership import is_theme_candidate  # noqa: E402
from app.services.research_eod_v1.measurement import (  # noqa: E402
    attach_forward_label,
    classify_funnel_row,
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
RESULTS = PACK / "measurement_results.json"
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
                "feature_ready": len(bars) >= FEATURE_READY_BARS,
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
            include_setup=False,
        )

    raws = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for sid, raw in pool.map(_one, session_panel):
            raws[sid] = raw
    return raws


_WORKER: dict = {}


def _init_session_worker(panel: dict[str, SecuritySeries], registry: dict) -> None:
    _WORKER["panel"] = panel
    _WORKER["registry"] = registry


def _process_one_session(session: date, panel: dict[str, SecuritySeries], registry: dict) -> tuple[list[str], list[str], int]:
    as_of = eod_evaluation_as_of(session)
    clipped = {sid: item for sid, series in panel.items() if (item := _clip(series, session))}
    raw_cache: dict[str, dict] = {}
    log_lines: list[str] = []
    row_lines: list[str] = []
    executed = 0
    for theme_id in SECTORS:
        refs = reference_panel(clipped, theme_id, session)
        if not refs:
            continue
        track = "etf" if theme_id == "etfs" else "stock"
        if track not in raw_cache:
            raw_cache[track] = _extract_raws(refs, registry, "mid")
        raws = raw_cache[track]
        gates = registry["sectors"][theme_id]["gates"]
        gate_key = (
            int(gates.get("base_min_sessions", 20)),
            int(gates.get("base_max_sessions", 80)),
            int(gates.get("base_min_distinct_touches", 2)),
            float(gates.get("breakout_buffer_atr", 0.15)),
            float(gates.get("breakout_buffer_price_fraction", 0.0025)),
        )
        gated = {}
        setup_memo = raw_cache.setdefault("_setup_memo", {})
        for sid, series in refs.items():
            raw = raws.get(sid)
            if raw is None:
                continue
            ok, _reason = is_theme_candidate(
                series,
                sector_id=theme_id,
                session=session,
                target_track=track,
            )
            if not ok:
                gated[sid] = raw
                continue
            memo_key = (sid, gate_key)
            if memo_key not in setup_memo:
                setup_memo[memo_key] = apply_sector_gates(raw, series, gates)
            gated[sid] = setup_memo[memo_key]
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
                precomputed_raws=gated,
                reapply_theme_gates=False,
                already_session_clipped=True,
            )
            executed += 1
            elig = 0
            for row in payload["rows"]:
                if row.get("status") == "eligible":
                    elig += 1
                for label_horizon in LABELS:
                    label = (
                        attach_forward_label(
                            panel[row["security_id"]],
                            session,
                            label_horizon,
                            last_allowed=ALLOWED_END,
                            holdout_start=HOLDOUT_START,
                        )
                        if row["security_id"] in panel
                        else {"label": None, "label_matured_at": None, "reason": "NO_SERIES"}
                    )
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
                    row_lines.append(json.dumps(record, sort_keys=True, default=str))
            log_lines.append(
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
            )
    return log_lines, row_lines, executed


def _mp_session(session_iso: str) -> tuple[str, list[str], list[str], int]:
    logs, rows, executed = _process_one_session(
        date.fromisoformat(session_iso),
        _WORKER["panel"],
        _WORKER["registry"],
    )
    return session_iso, logs, rows, executed


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
    date_funnel: dict[str, dict[str, Counter]] = {theme_id: defaultdict(Counter) for theme_id in SECTORS}
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
                funnel = _funnel_from_rows([row])
                stats[theme_id]["funnel"].update(funnel)
                date_funnel[theme_id][str(row.get("signal_session"))].update(funnel)
                for reason in row.get("rejection_reasons") or ():
                    stats[theme_id]["family"][algo]["reasons"][str(reason)] += 1
    for theme_id, dates in seen_theme_dates.items():
        warmup = 0
        missing = 0
        for session in dates:
            flags = date_funnel[theme_id][session]
            if session in eligible_dates[theme_id]:
                continue
            warmup_n = int(flags.get("warmup") or 0)
            missing_n = int(flags.get("data_missing") or 0)
            later = sum(int(flags.get(name) or 0) for name in ("setup_not_met", "score_floor", "risk_or_tradability", "other_reject"))
            if warmup_n and later == 0 and warmup_n >= missing_n:
                warmup += 1
            elif missing_n and later == 0:
                missing += 1
        stats[theme_id]["valid_dates"] = len(dates)
        stats[theme_id]["zero_result_dates"] = len(dates - eligible_dates[theme_id])
        stats[theme_id]["warmup_dates"] = warmup
        stats[theme_id]["missing_data_dates"] = missing
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


def _ic_slices(groups: list[dict]) -> dict[str, dict]:
    buckets: dict[str, dict] = defaultdict(
        lambda: {"defined_groups": 0, "undefined_groups": 0, "n_sum": 0, "ic_sum": 0.0, "undefined_reasons": Counter()}
    )
    for row in groups:
        key = "|".join(
            [
                str(row.get("theme_id")),
                str(row.get("algorithm")),
                str(row.get("profile")),
                str(row.get("horizon")),
                str(row.get("label_horizon")),
            ]
        )
        bucket = buckets[key]
        bucket["n_sum"] += int(row.get("n") or 0)
        if row.get("ic") is None:
            bucket["undefined_groups"] += 1
            bucket["undefined_reasons"][str(row.get("undefined_reason") or "undefined")] += 1
        else:
            bucket["defined_groups"] += 1
            bucket["ic_sum"] += float(row["ic"])
    out = {}
    for key, bucket in sorted(buckets.items()):
        defined = bucket["defined_groups"]
        out[key] = {
            "defined_groups": defined,
            "undefined_groups": bucket["undefined_groups"],
            "n_sum": bucket["n_sum"],
            "mean_grouped_ic": None if not defined else bucket["ic_sum"] / defined,
            "undefined_reasons": dict(bucket["undefined_reasons"]),
            "note": "mean of within-group ICs; pairs were not pooled across dates",
        }
    return out


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
        "by_theme_family_label": _ic_slices(groups),
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
        "feature_ready_name_count": sum(1 for item in coverage if item.get("feature_ready") or item.get("bars_clipped", 0) >= FEATURE_READY_BARS),
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
    _write_measurement_results(report, summary, events)
    return report


def _write_measurement_results(report: dict, summary: dict, events: dict) -> None:
    pairing = {}
    if PAIR.exists():
        pairing = json.loads(PAIR.read_text(encoding="utf-8"))
    counter = {}
    counter_path = PACK / "measurement_counterexamples" / "two_classes.json"
    if counter_path.exists():
        counter = json.loads(counter_path.read_text(encoding="utf-8"))
    superseded = {}
    if SUPERSEDED.exists():
        superseded = json.loads(SUPERSEDED.read_text(encoding="utf-8"))
    RESULTS.write_text(
        json.dumps(
            {
                "reviewed_head": report.get("code_sha"),
                "scope": "measurement口径 + continuous 24xABCD balanced/mid approved window",
                "holdout_start": report.get("holdout_start"),
                "holdout_unsealed": report.get("holdout_unsealed"),
                "allowed_end": report.get("allowed_end"),
                "matrix_claimed": report.get("matrix_claimed"),
                "executed_backtests": report.get("executed_backtests"),
                "F1_tests": {
                    "old_closeout": "tests/test_pr174_followup.py",
                    "measurement_acceptance": "tests/test_pr174_measurement_acceptance.py",
                    "local_full_suite": "pending_current_head_rerun",
                    "github_ci": "pending_artifact_head",
                },
                "F2_contracts": {
                    "pairing_path": str(PAIR.relative_to(ROOT)),
                    "pairing_note": pairing.get("note"),
                    "pair_count": len(pairing.get("pairs") or []),
                    "candidate_identity": [
                        {
                            "session": item.get("session"),
                            "old_candidates": item.get("old_candidates"),
                            "new_candidates": item.get("new_candidates"),
                            "n_changed": item.get("n_changed"),
                        }
                        for item in (pairing.get("pairs") or [])
                    ],
                },
                "F3_rows": {
                    "path": report.get("rows_path"),
                    "schema": str((PACK / "measurement_factor_row_schema.json").relative_to(ROOT)),
                    "sha256": report.get("rows_sha256"),
                    "command": "PYTHONPATH=/workspace:/workspace/backend python research/option_pro_us_eod_v1/scripts/run_measurement_continuous_abcd.py --workers 2",
                    "finalize_command": "PYTHONPATH=/workspace:/workspace/backend python research/option_pro_us_eod_v1/scripts/run_measurement_continuous_abcd.py --finalize-only",
                },
                "F4_ic": {
                    "groups_path": summary.get("groups_path"),
                    "summary_path": str(IC_OUT.relative_to(ROOT)),
                    "group_count": summary.get("group_count"),
                    "defined_groups": summary.get("defined_groups"),
                    "undefined_groups": summary.get("undefined_groups"),
                    "valid_group_date_count": summary.get("valid_group_date_count"),
                    "undefined_reason_counts": summary.get("undefined_reason_counts"),
                    "by_year": summary.get("by_year"),
                },
                "F5_funnel_events": {
                    "empty_result_ratio_theme_dates": report.get("empty_result_ratio_theme_dates"),
                    "event_ledger": events,
                    "theme_empty_ratios": {
                        theme_id: stats.get("empty_result_ratio")
                        for theme_id, stats in (report.get("themes") or {}).items()
                    },
                    "theme_warmup_dates": {
                        theme_id: stats.get("warmup_dates")
                        for theme_id, stats in (report.get("themes") or {}).items()
                    },
                },
                "F6_counts": {
                    "registered": report.get("registered"),
                    "unique_registered_configs": report.get("unique_registered_configs"),
                    "unique_snapshots": report.get("unique_snapshots"),
                    "actual_function_calls": report.get("actual_function_calls"),
                    "recomputes": report.get("recomputes"),
                    "outcomes_inspected": report.get("outcomes_inspected"),
                    "completed_sessions": report.get("completed_sessions"),
                    "session_count": report.get("session_count"),
                },
                "F7_portfolio_counterexamples": counter,
                "F8_superseded_17day": superseded,
                "notes": report.get("notes"),
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )


def _funnel_from_rows(rows: list[dict]) -> dict[str, int]:
    counts = Counter()
    for row in rows:
        counts[classify_funnel_row(row)] += 1
    return dict(counts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-sessions", type=int, default=0)
    parser.add_argument("--pairing-only", action="store_true")
    parser.add_argument("--include-pairing", action="store_true")
    parser.add_argument("--finalize-only", action="store_true")
    parser.add_argument("--workers", type=int, default=2)
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
    remaining = [session for session in all_sessions if session.isoformat() not in done_sessions]
    if any(session >= HOLDOUT_START for session in remaining):
        raise SystemExit("holdout leaked")
    log = LOG.open("a", encoding="utf-8")
    rows_handle = ROWS.open("a", encoding="utf-8")
    try:
        def _commit_session(session_iso: str, log_lines: list[str], row_lines: list[str], n_exec: int) -> None:
            nonlocal executed
            for line in log_lines:
                log.write(line + "\n")
            for line in row_lines:
                rows_handle.write(line + "\n")
            executed += n_exec
            done_sessions.add(session_iso)
            rows_handle.flush()
            log.flush()
            CHECKPOINT.write_text(json.dumps({"done_sessions": sorted(done_sessions)}), encoding="utf-8")
            print(json.dumps({"session": session_iso, "executed": executed, "done": len(done_sessions)}), flush=True)

        workers = max(1, int(args.workers))
        if workers == 1:
            for session in remaining:
                log_lines, row_lines, n_exec = _process_one_session(session, panel, registry)
                _commit_session(session.isoformat(), log_lines, row_lines, n_exec)
        else:
            ctx = get_context("fork")
            with ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                initializer=_init_session_worker,
                initargs=(panel, registry),
            ) as pool:
                for session_iso, log_lines, row_lines, n_exec in pool.map(
                    _mp_session,
                    [session.isoformat() for session in remaining],
                    chunksize=1,
                ):
                    _commit_session(session_iso, log_lines, row_lines, n_exec)
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
