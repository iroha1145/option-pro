"""Bounded historical 24-theme A/B/C/D diagnostics. Offline cache first.

Uses registered balanced/mid parameters. Does not unseal 2024-07-01 holdout.
Signal diagnostics only; Yahoo Close is not verified raw execution.
"""

from __future__ import annotations

import hashlib
import json
import pickle
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

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
from app.services.research_eod_v1.series import SecuritySeries  # noqa: E402
from app.services.research_eod_v1.snapshot import compute_snapshot  # noqa: E402
from app.services.research_eod_v1.universe_audit import ETF_SUBASSET_HINTS  # noqa: E402
from app.services.sectors import SECTORS  # noqa: E402

CACHE = ROOT / "research" / "option_pro_us_eod_v1" / "data" / "cache" / "round3_yahoo_abcd" / "bars.pkl"
OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "closeout_historical_abcd.json"
LOG = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "closeout_historical_execution_log.jsonl"
REGISTRY = ROOT / "research" / "option_pro_us_eod_v1" / "config" / "registry.json"
HOLDOUT_START = date(2024, 7, 1)
ALLOWED_END = date(2024, 6, 28)
DENSE_START = date(2023, 6, 12)
DENSE_END = date(2023, 6, 23)
MONTH_ENDS = (
    date(2018, 12, 31),
    date(2019, 12, 31),
    date(2020, 12, 31),
    date(2021, 12, 31),
    date(2022, 12, 30),
    date(2023, 12, 29),
    date(2024, 3, 28),
    date(2024, 6, 28),
)
HISTORY_BARS = 420
LABELS = (5, 20, 63)


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
        "industry_source": "theme_tag_diagnostic_not_economic_parent",
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


def _month_ends(sessions: list[date]) -> list[date]:
    wanted = set()
    for day in MONTH_ENDS:
        if day <= ALLOWED_END:
            wanted.add(day)
    have = set(sessions)
    out = []
    for day in sessions:
        if day in wanted:
            out.append(day)
    # If a listed calendar date is not a session, keep the last session before it.
    for day in MONTH_ENDS:
        if day > ALLOWED_END:
            continue
        if day in have:
            continue
        prior = [item for item in sessions if item <= day]
        if prior and prior[-1] not in out:
            out.append(prior[-1])
    return sorted(set(out))


def _tail_history(series: SecuritySeries, session: date, max_bars: int = HISTORY_BARS) -> SecuritySeries | None:
    clipped = series.slice_through(session)
    if clipped is None:
        return None
    if len(clipped.dates) <= max_bars:
        return clipped
    start = len(clipped.dates) - max_bars
    end_session = clipped.dates[-1]
    return SecuritySeries(
        security_id=clipped.security_id,
        ticker_at_signal=clipped.ticker_at_signal,
        dates=list(clipped.dates[start:]),
        open=clipped.open[start:].copy(),
        high=clipped.high[start:].copy(),
        low=clipped.low[start:].copy(),
        close=clipped.close[start:].copy(),
        raw_close=clipped.raw_close[start:].copy(),
        volume=clipped.volume[start:].copy(),
        dollar_volume=clipped.dollar_volume[start:].copy(),
        tri=clipped.tri[start:].copy(),
        turnover_is_proxy=clipped.turnover_is_proxy,
        volume_session_scope=clipped.volume_session_scope,
        asset_track=clipped.asset_track,
        industry_id=clipped.industry_id,
        parent_industry_id=clipped.parent_industry_id,
        theme_ids=clipped.theme_ids,
        venue_metadata=clipped.venue_metadata,
        source_available_at=None,
        halted=clipped.halted,
        raw_open=None if clipped.raw_open is None else clipped.raw_open[start:].copy(),
        dividends=tuple(item for item in clipped.dividends if item[0] <= end_session),
        splits=tuple(item for item in clipped.splits if item[0] <= end_session),
        economic_known_at=None if clipped.economic_known_at is None else list(clipped.economic_known_at[start:]),
        source_published_at=None if clipped.source_published_at is None else list(clipped.source_published_at[start:]),
        retrieved_at=None if clipped.retrieved_at is None else list(clipped.retrieved_at[start:]),
        finalized_at=None if clipped.finalized_at is None else list(clipped.finalized_at[start:]),
        bar_partial=None if clipped.bar_partial is None else clipped.bar_partial[start:].copy(),
        bar_halted=None if clipped.bar_halted is None else clipped.bar_halted[start:].copy(),
        vintage_status=clipped.vintage_status[start:] if clipped.vintage_status else (),
        price_adjustment=clipped.price_adjustment[start:] if clipped.price_adjustment else (),
        volume_adjustment=clipped.volume_adjustment[start:] if clipped.volume_adjustment else (),
        tri_verified=clipped.tri_verified,
        reconstruction_mode=clipped.reconstruction_mode,
        dividend_events=tuple(item for item in clipped.dividend_events if item),
    )


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 10:
        return None
    left = np.asarray(xs, dtype=float)
    right = np.asarray(ys, dtype=float)
    ok = np.isfinite(left) & np.isfinite(right)
    if int(ok.sum()) < 10:
        return None
    rx = np.argsort(np.argsort(left[ok]))
    ry = np.argsort(np.argsort(right[ok]))
    corr = np.corrcoef(rx, ry)[0, 1]
    return None if not np.isfinite(corr) else float(corr)


def _forward_return(series, session: date, horizon: int, last: date) -> float | None:
    if session not in series.dates:
        return None
    try:
        target = shift_sessions(session, horizon)
    except Exception:
        return None
    if target > last or target not in series.dates:
        return None
    start_i = series.dates.index(session)
    end_i = series.dates.index(target)
    start_px = float(series.tri[start_i])
    end_px = float(series.tri[end_i])
    if not np.isfinite(start_px) or not np.isfinite(end_px) or start_px <= 0:
        return None
    return end_px / start_px - 1.0


def _audit_series(panel: dict) -> dict:
    names = 0
    ohlc_ok = 0
    missing_hl = 0
    missing_vol = 0
    missing_tri = 0
    for series in panel.values():
        names += 1
        for i in range(len(series.dates)):
            if np.isfinite(series.open[i]) and np.isfinite(series.high[i]) and np.isfinite(series.low[i]) and np.isfinite(series.close[i]):
                ohlc_ok += 1
            if not np.isfinite(series.high[i]) or not np.isfinite(series.low[i]):
                missing_hl += 1
            if not np.isfinite(series.volume[i]):
                missing_vol += 1
            if not np.isfinite(series.tri[i]) or float(series.tri[i]) <= 0:
                missing_tri += 1
    return {
        "names": names,
        "complete_ohlc_bars": ohlc_ok,
        "missing_high_or_low_bars": missing_hl,
        "missing_volume_bars": missing_vol,
        "missing_or_nonpositive_tri_bars": missing_tri,
        "field_capability": {
            "yahoo_ohlc": "present_unverified",
            "yahoo_volume": "present_unverified_session_scope_unknown",
            "yahoo_tri": "vendor_adj_close_not_verified_total_return",
            "industry": "theme_tag_not_economic_parent",
            "fundamentals": "unused_not_a_stop",
        },
        "experiment_eligibility": "price_factor_diagnostics_may_run_without_fundamentals",
    }


def main() -> int:
    if not CACHE.exists():
        raise SystemExit(f"offline Yahoo cache missing: {CACHE}")
    batched = pickle.loads(CACHE.read_bytes())
    appearances: dict[str, list[str]] = {}
    for theme_id, sector in SECTORS.items():
        for ticker in sector["tickers"]:
            appearances.setdefault(ticker, []).append(theme_id)
    panel = {}
    coverage = []
    for ticker, themes in appearances.items():
        track = "etf" if ticker in ETF_SUBASSET_HINTS or themes == ["etfs"] else "stock"
        bars = [
            bar
            for bar in (batched.get(ticker) or [])
            if bar.session_date <= ALLOWED_END
        ]
        coverage.append(
            {
                "ticker": ticker,
                "themes": themes,
                "bars_clipped": len(bars),
                "first": bars[0].session_date.isoformat() if bars else None,
                "last": bars[-1].session_date.isoformat() if bars else None,
                "status": "ok" if bars else "empty",
            }
        )
        if not bars:
            continue
        try:
            series = bars_to_series(
                bars,
                security_id=ticker,
                asset_track=track,
                theme_ids=tuple(themes),
                industry_id=themes[0],
                parent_industry_id=themes[0],
                venue_metadata=_venue(track),
                reconstruction_mode=HISTORICAL_RECONSTRUCTION,
            )
        except ValueError as exc:
            coverage[-1]["status"] = f"invalid:{exc}"
            continue
        if series is not None:
            panel[ticker] = series
    if "SPY" not in panel and "SPY" in batched:
        spy_bars = [bar for bar in batched["SPY"] if bar.session_date <= ALLOWED_END]
        panel["SPY"] = bars_to_series(
            spy_bars,
            security_id="SPY",
            asset_track="etf",
            theme_ids=("etfs",),
            venue_metadata=_venue("etf"),
            reconstruction_mode=HISTORICAL_RECONSTRUCTION,
        )
    if "QQQ" not in panel and "QQQ" in batched:
        qqq_bars = [bar for bar in batched["QQQ"] if bar.session_date <= ALLOWED_END]
        panel["QQQ"] = bars_to_series(
            qqq_bars,
            security_id="QQQ",
            asset_track="etf",
            theme_ids=("etfs",),
            venue_metadata=_venue("etf"),
            reconstruction_mode=HISTORICAL_RECONSTRUCTION,
        )

    all_allowed = _sessions_between(date(2018, 1, 2), ALLOWED_END)
    sessions = list(dict.fromkeys(_sessions_between(DENSE_START, DENSE_END) + _month_ends(all_allowed)))
    sessions = [day for day in sessions if day <= ALLOWED_END]
    label_cut = {horizon: shift_sessions(ALLOWED_END, -horizon) for horizon in LABELS}

    registry = load_registry()
    theme_stats: dict[str, dict] = {}
    ic_pairs: dict[str, dict[int, list[tuple[float, float]]]] = defaultdict(lambda: {5: [], 20: [], 63: []})
    executed = 0
    outcomes = 0
    if LOG.exists():
        LOG.unlink()
    with LOG.open("w", encoding="utf-8") as log:
        for theme_id in SECTORS:
            members = set(SECTORS[theme_id]["tickers"]) | {"SPY", "QQQ"}
            theme_panel = {sid: series for sid, series in panel.items() if sid in members}
            stats = {
                "theme_id": theme_id,
                "members_in_panel": len(theme_panel) - sum(1 for sid in theme_panel if sid in {"SPY", "QQQ"}),
                "valid_dates": 0,
                "zero_result_dates": 0,
                "missing_data_dates": 0,
                "independent_events": 0,
                "family": {algo: {"eligible": 0, "rejected": 0, "reasons": Counter()} for algo in ALGORITHMS},
                "eligible_name_sessions": 0,
            }
            prev_eligible: set[str] = set()
            for session in sessions:
                as_of = eod_evaluation_as_of(session)
                day_eligible: set[str] = set()
                session_panel = {}
                for sid, series in theme_panel.items():
                    window = _tail_history(series, session)
                    if window is not None:
                        session_panel[sid] = window
                usable = any(session in series.dates for series in session_panel.values())
                if not usable:
                    stats["missing_data_dates"] += 1
                    continue
                stats["valid_dates"] += 1
                any_eligible = False
                for algorithm in ALGORITHMS:
                    payload = compute_snapshot(
                        as_of,
                        session_panel,
                        "u_closeout_historical",
                        registry,
                        sector_id=theme_id,
                        algorithm=algorithm,
                        profile="balanced",
                        horizon="mid",
                        source_finalized_through=session,
                    )
                    executed += 1
                    outcomes += 1
                    fam = stats["family"][algorithm]
                    elig = 0
                    for row in payload["rows"]:
                        for reason in row.get("rejection_reasons") or ():
                            fam["reasons"][str(reason)] += 1
                        if row.get("status") != "eligible":
                            fam["rejected"] += 1
                            continue
                        elig += 1
                        any_eligible = True
                        day_eligible.add(row["security_id"])
                        series = theme_panel.get(row["security_id"])
                        score = row.get("score")
                        if series is not None and score is not None:
                            for horizon in LABELS:
                                if session > label_cut[horizon]:
                                    continue
                                fwd = _forward_return(series, session, horizon, ALLOWED_END)
                                if fwd is None:
                                    continue
                                ic_pairs[theme_id][horizon].append((float(score), float(fwd)))
                    fam["eligible"] += elig
                    stats["eligible_name_sessions"] += elig
                    log.write(
                        json.dumps(
                            {
                                "session": session.isoformat(),
                                "theme_id": theme_id,
                                "algorithm": algorithm,
                                "candidates": len(payload["candidate_ids"]),
                                "eligible": elig,
                                "eligible_ids": sorted(day_eligible) if algorithm == ALGORITHMS[0] else [
                                    row["security_id"] for row in payload["rows"] if row.get("status") == "eligible"
                                ],
                            }
                        )
                        + "\n"
                    )
                if not any_eligible:
                    stats["zero_result_dates"] += 1
                if day_eligible and not (day_eligible & prev_eligible):
                    stats["independent_events"] += 1
                prev_eligible = day_eligible
            stats["family"] = {
                algo: {
                    "eligible": fam["eligible"],
                    "rejected": fam["rejected"],
                    "top_rejections": Counter(fam["reasons"]).most_common(8),
                }
                for algo, fam in stats["family"].items()
            }
            stats["ic_forward_labels"] = {
                str(horizon): _spearman([a for a, _ in ic_pairs[theme_id][horizon]], [b for _, b in ic_pairs[theme_id][horizon]])
                for horizon in LABELS
            }
            stats["ic_pair_counts"] = {str(horizon): len(ic_pairs[theme_id][horizon]) for horizon in LABELS}
            theme_stats[theme_id] = stats
            print(json.dumps({"theme": theme_id, "valid_dates": stats["valid_dates"], "executed": executed}), flush=True)

    report = {
        "head_note": "closeout historical A/B/C/D on registered balanced/mid; signal diagnostics; holdout sealed",
        "capture_mode": HISTORICAL_RECONSTRUCTION,
        "vendor_finalization_policy": VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
        "holdout_start": HOLDOUT_START.isoformat(),
        "holdout_unsealed": False,
        "allowed_end": ALLOWED_END.isoformat(),
        "sessions": [day.isoformat() for day in sessions],
        "session_count": len(sessions),
        "dense_window": [DENSE_START.isoformat(), DENSE_END.isoformat()],
        "other_years": "selected year/quarter-end sessions through allowed_end",
        "remaining_daily_dates": "REGISTERED_NOT_RUN after this first pass",
        "profile": "balanced",
        "horizon": "mid",
        "label_horizons": list(LABELS),
        "label_maturity_cuts": {str(k): v.isoformat() for k, v in label_cut.items()},
        "yahoo_audit": _audit_series(panel),
        "coverage": coverage,
        "themes": theme_stats,
        "registered": 1920,
        "executed_snapshots": executed,
        "executed_backtests": 0,
        "outcomes_inspected": outcomes,
        "engineering_fixtures": 0,
        "market_backtest_run": False,
        "winners": [],
        "capability_flags": [
            "CURRENT_UNIVERSE_SURVIVORS",
            "PIT_CLASSIFICATION_MISSING",
            "CORPORATE_ACTIONS_INCOMPLETE",
            "EXECUTION_DATA_UNVERIFIED",
            "download_time_not_pit",
            "IC_IS_SIGNAL_DIAGNOSTIC_NOT_PNL",
        ],
        "param_hash": _sha256(REGISTRY),
        "code_sha": _git_head(),
        "data_hash": _sha256(CACHE),
        "feature_version": "us-eod-research-features-v1.5",
        "cache_path": "research/option_pro_us_eod_v1/data/cache/round3_yahoo_abcd/bars.pkl",
        "execution_log": "research/option_pro_us_eod_v1/return_pack/closeout_historical_execution_log.jsonl",
        "notes": [
            "Cache replay only; no Massive; no network purchase.",
            "Bars after 2024-06-28 were dropped before compute_snapshot.",
            "Portfolio win-rate is not reported: Yahoo Close is not a verified raw executable print.",
            "Stop after this fixed-parameter first pass.",
        ],
    }
    OUT.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "executed_snapshots": executed,
                "sessions": len(sessions),
                "themes": len(theme_stats),
                "out": str(OUT),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
