#!/usr/bin/env python3
"""Extract first TRIGGERED hits with the same detect_breakout path.

Uses reconstruct_ticker_dates(..., max_lookback=120). Structure windows are
at most 80 bars, so trigger identity matches a full-history walk. hold_bars
may truncate and is not used by T1/T2.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.dataset import load_dataset
from app.services.research.labels import last_usable_signal_date
from app.services.research.protocol import FROZEN_SPLITS, PRIMARY_HORIZON, iter_split_dates
from app.services.research.radar import DEFAULT_STRUCTURE_LOOKBACK, first_trigger_by_pivot, reconstruct_ticker_dates
from app.services.strength.scanner import _theme_universe

_JOB: dict = {}


def _init(state: dict) -> None:
    _JOB.clear()
    _JOB.update(state)


def _one(ticker: str) -> tuple[str, dict]:
    payload = reconstruct_ticker_dates(
        _JOB["dataset"],
        ticker,
        _JOB["dates"],
        allow_sealed=False,
        frame=_JOB["frames"].get(ticker),
        max_lookback=_JOB["max_lookback"],
    )
    return ticker, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--split", default="development")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-lookback", type=int, default=DEFAULT_STRUCTURE_LOOKBACK)
    parser.add_argument("--limit-tickers", type=int, default=0)
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    dates = [
        day
        for day in iter_split_dates(args.split, allow_sealed=False)
        if day <= last_usable_signal_date(args.split, PRIMARY_HORIZON, embargo_sessions=1)
    ]
    symbols, _meta = _theme_universe()
    if args.limit_tickers:
        symbols = symbols[: args.limit_tickers]
    panel_through = FROZEN_SPLITS["validation"]["end"]
    frames = {
        ticker: dataset.frame(ticker, through=panel_through, allow_sealed=False)
        for ticker in symbols
    }
    state = {
        "dataset": dataset,
        "dates": dates,
        "frames": frames,
        "max_lookback": args.max_lookback,
    }
    events: list[dict] = []
    skipped = {}
    workers = max(1, int(args.workers))
    print(
        f"extract-first-triggers tickers={len(symbols)} sessions={len(dates)} "
        f"lookback={args.max_lookback} workers={workers}",
        flush=True,
    )
    if workers == 1:
        _init(state)
        jobs = [_one(ticker) for ticker in symbols]
    else:
        with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(state,)) as pool:
            futures = {pool.submit(_one, ticker): ticker for ticker in symbols}
            jobs = []
            done = 0
            for future in as_completed(futures):
                jobs.append(future.result())
                done += 1
                print(f"extract {done}/{len(symbols)} {jobs[-1][0]} events={len(jobs[-1][1]['events'])}", flush=True)
    if workers == 1:
        for index, (ticker, payload) in enumerate(jobs, start=1):
            print(f"extract {index}/{len(symbols)} {ticker} events={len(payload['events'])}", flush=True)
    for _ticker, payload in jobs:
        events.extend(payload.get("events") or [])
        for key, value in (payload.get("skipped") or {}).items():
            skipped[key] = skipped.get(key, 0) + int(value)
    pivot = first_trigger_by_pivot(events)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "split": args.split,
        "max_lookback": args.max_lookback,
        "ticker_count": len(symbols),
        "session_count": len(dates),
        "raw_triggered": len(events),
        "first_triggers": len(pivot["events"]),
        "duplicate_triggers_dropped": pivot["duplicate_triggers_dropped"],
        "skipped": skipped,
        "events": pivot["events"],
        "note": (
            "Same detect_base/detect_breakout as radar-replay. "
            "max_lookback only drops unused pre-window bars."
        ),
    }
    tmp = out.with_suffix(out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(out)
    print(json.dumps({"wrote": str(out), "first_triggers": len(pivot["events"])}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
