"""ATR% distribution of the listed stocks at the signal date, with the production definition.

The replay rows do not carry ``atr_pct``, so it is recomputed from the replay cache the way
``research_eod_v1.factors.extract_raw`` does: ``atr_sma_at`` (simple mean of the 14-session
true range on split-adjusted high/low/close) divided by the split-adjusted close on the
signal session, times 100. Splits are applied as the production loader does (prices before
an execution date are scaled by split_from/split_to). The conservative HIGH_ATR gate compares
this number with min(registry absolute cap, multiplier x liquid-stock median), so the
distribution shows what the 2.0 (cons17) versus 1.25 (cons17_atr125) multiple let through.

    python atr_distribution.py --db replay.sqlite --replay /content/replay/v17_stage3 \\
        --variants "v16,cons17+nofund,cons17_atr125+nofund" --profile conservative --out results/stage1_v2_reeval
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[4]
sys.path.insert(0, str(REPO / "backend"))

from app.services.research_eod_v1.constants import ATR_PERIOD  # noqa: E402
from app.services.research_eod_v1.mathutil import atr_sma_at  # noqa: E402

_spec = importlib.util.spec_from_file_location("v17_evaluate", HERE / "evaluate.py")
evaluate = sys.modules.get(_spec.name) or importlib.util.module_from_spec(_spec)
if _spec.name not in sys.modules:
    sys.modules[_spec.name] = evaluate
    _spec.loader.exec_module(evaluate)

LOOKBACK = 40  # sessions of bars loaded before the signal; the ATR needs ATR_PERIOD + 1
QUANTILES = (0.1, 0.25, 0.5, 0.75, 0.9)


def atr_pct_at(connection: sqlite3.Connection, splits: dict, sessions: list[str], index: dict, ticker: str,
               signal_day: str) -> float | None:
    """Production ATR% on the signal session: split-adjusted 14-session true range mean over the close."""
    end_i = index[signal_day]
    start = sessions[max(0, end_i - LOOKBACK)]
    rows = connection.execute(
        "SELECT session_date, high, low, close FROM raw_daily_bars WHERE ticker = ? AND session_date BETWEEN ? AND ? "
        "ORDER BY session_date", (ticker, start, signal_day)).fetchall()
    if not rows or rows[-1][0] != signal_day:
        return None
    days = [row[0] for row in rows]
    high = np.asarray([row[1] for row in rows], dtype=float)
    low = np.asarray([row[2] for row in rows], dtype=float)
    close = np.asarray([row[3] for row in rows], dtype=float)
    factor = np.ones(len(rows))
    for execution, split_from, split_to in splits.get(ticker, ()):
        before = np.asarray([day < execution for day in days])
        factor[before] *= split_from / split_to
    high, low, close = high * factor, low * factor, close * factor
    t = len(rows) - 1
    atr = atr_sma_at(high, low, close, t, ATR_PERIOD)
    if atr is None or not np.isfinite(close[t]) or close[t] <= 0:
        return None
    return 100.0 * atr / close[t]


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    position = q * (len(ordered) - 1)
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append")
    parser.add_argument("--variants", required=True)
    parser.add_argument("--profile", default="conservative")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    wanted = {name.strip() for name in args.variants.split(",") if name.strip()}
    records, _ = evaluate.load_records(args.replay)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    sessions = [row[0] for row in connection.execute("SELECT session_date FROM market_sessions ORDER BY session_date")]
    index = {day: i for i, day in enumerate(sessions)}
    splits: dict = defaultdict(list)
    for ticker, execution, split_from, split_to in connection.execute(
            "SELECT ticker, execution_date, split_from, split_to FROM splits"):
        splits[ticker].append((execution, split_from, split_to))
    values: dict[tuple[str, str], list[float]] = defaultdict(list)
    missing: dict[tuple[str, str], int] = defaultdict(int)
    cache: dict[tuple[str, str], float | None] = {}
    for record in records:
        for key, block in record["lists"].items():
            variant, profile, view = key.split("/")
            if variant not in wanted or profile != args.profile:
                continue
            stocks = [row for row in block["rows"] if row.get("stock_or_etf_track") == "stock"][:args.top]
            for row in stocks:
                ticker, why = evaluate.resolve(row)
                if ticker is None:
                    missing[(variant, view)] += 1
                    continue
                pair = (ticker, record["session"])
                if pair not in cache:
                    cache[pair] = atr_pct_at(connection, splits, sessions, index, ticker, record["session"])
                value = cache[pair]
                if value is None:
                    missing[(variant, view)] += 1
                else:
                    values[(variant, view)].append(value)
    rows = []
    for (variant, view), sample in sorted(values.items()):
        rows.append({"variant": variant, "profile": args.profile, "view": view, "names": len(sample),
                     "missing": missing.get((variant, view), 0),
                     **{f"p{int(q * 100)}": round(quantile(sample, q), 3) for q in QUANTILES},
                     "max": round(max(sample), 3), "mean": round(statistics.fmean(sample), 3),
                     "share_above_5pct": round(sum(1 for v in sample if v > 5.0) / len(sample), 4),
                     "share_above_3pct": round(sum(1 for v in sample if v > 3.0) / len(sample), 4)})
    pooled = defaultdict(list)
    for (variant, view), sample in values.items():
        pooled[variant].extend(sample)
    for variant, sample in sorted(pooled.items()):
        rows.append({"variant": variant, "profile": args.profile, "view": "all", "names": len(sample),
                     "missing": sum(count for (name, _view), count in missing.items() if name == variant),
                     **{f"p{int(q * 100)}": round(quantile(sample, q), 3) for q in QUANTILES},
                     "max": round(max(sample), 3), "mean": round(statistics.fmean(sample), 3),
                     "share_above_5pct": round(sum(1 for v in sample if v > 5.0) / len(sample), 4),
                     "share_above_3pct": round(sum(1 for v in sample if v > 3.0) / len(sample), 4)})
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "atr_distribution.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    with (args.out / "atr_distribution.json").open("w") as handle:
        json.dump({"definition": "100 x mean 14-session true range (split-adjusted high/low/close) / split-adjusted "
                                 "close on the signal session; research_eod_v1.factors.extract_raw",
                   "top": args.top, "profile": args.profile, "rows": rows}, handle, indent=1)
    for row in rows:
        if row["view"] == "all":
            print("  " + ", ".join(f"{k} {v}" for k, v in row.items()))
    print("DONE atr_distribution")


if __name__ == "__main__":
    main()
