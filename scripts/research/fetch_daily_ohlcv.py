#!/usr/bin/env python3
"""Download a bounded daily OHLCV cache for research. Replay never calls this."""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yfinance as yf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.dataset import research_universe, write_dataset_dir
from app.services.yfinance_batch import download_in_bounded_batches


def _records_from_frame(frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    records: list[dict] = []
    if isinstance(frame.columns, pd.MultiIndex):
        tickers = list(dict.fromkeys(frame.columns.get_level_values(0)))
        for ticker in tickers:
            if ticker not in frame.columns.get_level_values(0):
                continue
            sub = frame[ticker].copy()
            records.extend(_records_from_single(str(ticker), sub))
        return records
    return _records_from_single("UNKNOWN", frame)


def _records_from_single(ticker: str, frame: pd.DataFrame) -> list[dict]:
    if frame.empty:
        return []
    columns = {str(name).lower(): name for name in frame.columns}
    close_col = columns.get("close")
    adj_col = columns.get("adj close") or columns.get("adj_close")
    if close_col is None:
        return []
    out: list[dict] = []
    for index, row in frame.iterrows():
        timestamp = pd.Timestamp(index)
        session = timestamp.tz_convert("America/New_York").date() if timestamp.tzinfo else timestamp.date()
        close = row.get(close_col)
        adj_close = row.get(adj_col) if adj_col is not None else close
        try:
            close_n = float(close)
            adj_n = float(adj_close)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(close_n) or close_n <= 0:
            continue
        if not math.isfinite(adj_n) or adj_n <= 0:
            continue
        item = {
            "ticker": ticker,
            "date": session.isoformat(),
            "open": _float(row.get(columns.get("open"), close_n)),
            "high": _float(row.get(columns.get("high"), close_n)),
            "low": _float(row.get(columns.get("low"), close_n)),
            "close": close_n,
            "adj_close": adj_n,
            "volume": _float(row.get(columns.get("volume"), 0.0), allow_zero=True),
        }
        out.append(item)
    return out


def _float(value, *, allow_zero: bool = False) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0 if allow_zero else None
    if number < 0 or (number == 0 and not allow_zero):
        return None
    return number


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    tickers = research_universe()
    frame = download_in_bounded_batches(
        yf.download,
        tickers=tickers,
        start=args.start,
        end=args.end,
        interval="1d",
        group_by="ticker",
        progress=False,
        auto_adjust=False,
        actions=False,
    )
    records = _records_from_frame(frame)
    path = write_dataset_dir(
        args.out,
        records,
        dataset_id=f"yahoo-unadjusted-ohlcv-{args.start}-{(args.end or 'latest')}",
        source="Yahoo/yfinance",
        as_of=datetime.now(timezone.utc),
    )
    print(f"wrote {len(records)} bars for {len({row['ticker'] for row in records})} tickers to {path}")
    missing = sorted(set(tickers) - {row["ticker"] for row in records})
    if missing:
        print("missing:", ",".join(missing))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
