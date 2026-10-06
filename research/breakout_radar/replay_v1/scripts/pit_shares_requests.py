"""Which (ticker, date) point-in-time share counts to fetch, and in which order.

    python pit_shares_requests.py --db replay.sqlite --directory massive_directory \
        --start 2021-10-04 --end 2026-09-25 --out round1.csv
    python pit_shares_requests.py ... --samples samples.jsonl --out round2.csv

Round 1 (no samples yet) asks for one sample per eligible ticker at its first needed
day. Later rounds read the samples fetched so far (JSON lines, one Massive
``/v3/reference/tickers/{T}?date=D`` result per line with ``ticker`` and ``date``) and
ask for the next sample wherever coverage lapses under the tier cadence: market cap
below $500M every 31 days, $500M to $2B every 92 days, above $2B every 366 days
(DATA_SPEC 11.4). Re-run until the output is empty.

Eligible tickers are the daily superset (A1 relaxed union A3n, the same rules as the
census) restricted to share-bearing types (CS, OS, ADRC, ADRP, GDR) in the weekly
point-in-time directory. Funds have no TradingView market cap and are not requested.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import sqlite3
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

SHARE_TYPES = {"CS", "OS", "ADRC", "ADRP", "GDR"}
TIERS = ((5e8, 31), (2e9, 92), (float("inf"), 366))

SUPERSET_SQL = """
WITH bars AS (
  SELECT ticker, session_date, open, high, close, volume,
         LAG(close) OVER (PARTITION BY ticker ORDER BY session_date) AS prev_close,
         AVG(volume) OVER (PARTITION BY ticker ORDER BY session_date
                           ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS avg_vol_10,
         COUNT(volume) OVER (PARTITION BY ticker ORDER BY session_date
                             ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING) AS n_prior,
         AVG(close * volume) OVER (PARTITION BY ticker ORDER BY session_date
                                   ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING) AS adv_20
  FROM raw_daily_bars
  WHERE session_date BETWEEN :window_start AND :end
),
adjusted AS (
  SELECT b.*,
         b.prev_close * COALESCE(MIN(s.split_from / s.split_to), 1.0) AS prev_close_adj,
         b.avg_vol_10 * COALESCE(MIN(s.split_to / s.split_from), 1.0) AS avg_vol_10_adj
  FROM bars b
  LEFT JOIN splits s ON s.ticker = b.ticker AND s.execution_date = b.session_date
  GROUP BY b.ticker, b.session_date
)
SELECT ticker, session_date
FROM adjusted
WHERE session_date >= :start AND n_prior = 10 AND prev_close_adj > 0 AND high >= 2.0
  AND (
    (high >= 1.025 * prev_close_adj AND volume >= 1.4 * avg_vol_10_adj)
    OR (
      (open >= 1.02 * prev_close_adj OR high >= 1.05 * prev_close_adj)
      AND (adv_20 >= 10000000 OR open >= 1.05 * prev_close_adj)
    )
  )
"""


class Directory:
    def __init__(self, folder: Path) -> None:
        self.labels = sorted(path.name[:-8] for path in folder.glob("20*.json.gz"))
        if not self.labels:
            raise SystemExit(f"no directory snapshots in {folder}")
        self.folder = folder
        self._types: dict[str, dict[str, str]] = {}

    def types(self, label: str) -> dict[str, str]:
        if label not in self._types:
            with gzip.open(self.folder / f"{label}.json.gz", "rt") as handle:
                rows = json.load(handle)["results"]
            self._types[label] = {str(r.get("ticker") or "").upper(): str(r.get("type") or "").upper() for r in rows}
        return self._types[label]

    def type_on(self, ticker: str, day: date) -> str | None:
        position = bisect_right(self.labels, day.isoformat()) - 1
        if position < 0:
            return None
        return self.types(self.labels[position]).get(ticker)


def needed_days(db: Path, start: date, end: date, directory: Directory) -> dict[str, list[date]]:
    connection = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    window_start = (start - timedelta(days=60)).isoformat()
    out: dict[str, set[date]] = defaultdict(set)
    for ticker, session in connection.execute(
        SUPERSET_SQL, {"window_start": window_start, "start": start.isoformat(), "end": end.isoformat()}
    ):
        day = date.fromisoformat(session)
        if directory.type_on(str(ticker).upper(), day) in SHARE_TYPES:
            out[str(ticker).upper()].add(day)
    connection.close()
    return {ticker: sorted(days) for ticker, days in out.items()}


def load_samples(path: Path | None) -> dict[str, list[tuple[date, float | None]]]:
    samples: dict[str, list[tuple[date, float | None]]] = defaultdict(list)
    if path is None or not path.exists():
        return samples
    with open(path) as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            body = row.get("results") if isinstance(row.get("results"), dict) else row
            ticker = str(row.get("ticker") or body.get("ticker") or "").upper()
            stamp = row.get("date") or row.get("as_of")
            if not ticker or not stamp:
                continue
            cap = body.get("market_cap")
            if cap is None and body.get("weighted_shares_outstanding") and row.get("close"):
                cap = float(body["weighted_shares_outstanding"]) * float(row["close"])
            samples[ticker].append((date.fromisoformat(str(stamp)[:10]), float(cap) if cap is not None else None))
    for ticker in samples:
        samples[ticker].sort()
    return samples


def cadence_days(cap: float | None) -> int:
    if cap is None:
        return TIERS[0][1]
    for ceiling, days in TIERS:
        if cap < ceiling:
            return days
    return TIERS[-1][1]


def requests_for(days: list[date], samples: list[tuple[date, float | None]]) -> list[tuple[date, str]]:
    """Walk the needed days; ask for a sample where the latest one is missing or too old."""

    out: list[tuple[date, str]] = []
    if not samples:
        return [(days[0], "initial")]
    dates = [item[0] for item in samples]
    pending: date | None = None
    for day in days:
        if pending is not None and day <= pending:
            continue  # covered by a sample we are about to request
        position = bisect_right(dates, day) - 1
        if position < 0:
            out.append((day, "before_first_sample"))
            pending = day + timedelta(days=cadence_days(None))
            continue
        sample_day, cap = samples[position]
        limit = cadence_days(cap)
        if (day - sample_day).days > limit:
            out.append((day, f"refresh_{limit}d"))
            pending = day + timedelta(days=limit)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--samples", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    directory = Directory(args.directory)
    needed = needed_days(args.db, args.start, args.end, directory)
    samples = load_samples(args.samples)
    rows: list[tuple[str, str, str]] = []
    reasons: dict[str, int] = defaultdict(int)
    for ticker in sorted(needed):
        for day, reason in requests_for(needed[ticker], samples.get(ticker, [])):
            rows.append((ticker, day.isoformat(), reason))
            reasons[reason.split("_")[0]] += 1
    with open(args.out, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "date", "reason"])
        writer.writerows(rows)
    print(json.dumps({"eligible_tickers": len(needed), "requests": len(rows), "by_reason": dict(reasons)}))
    print("DONE pit_shares_requests")


if __name__ == "__main__":
    main()
