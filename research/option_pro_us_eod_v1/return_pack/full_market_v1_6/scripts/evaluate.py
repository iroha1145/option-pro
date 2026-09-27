"""Forward returns and pre-registered metrics for replayed screener lists.

Entry at the open of the session after T, exit at the close h sessions after T, both
raw prices from the replay cache, split-adjusted with the same frozen split table. A
missing bar between entry and exit ends the holding at the last close before the gap;
gaps are never bridged, so a reused ticker cannot be stitched to another company. A
name with no bar on the entry session counts as an unfilled slot. Excess is against
SPY over the identical window. The primary metric fills empty slots with SPY (excess 0),
so a variant cannot win by listing fewer names. See PREREGISTRATION.md.

    python evaluate.py --db replay.sqlite --replay /content/replay/stage1 --out results/stage1
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

HOLDINGS = (5, 20, 63)
TOPS = (10, 20)
P1_END = "2024-12-31"
VIEWS = ("short", "mid", "long")


def newey_west_t(values: list[float], lag: int) -> float | None:
    n = len(values)
    if n < 3:
        return None
    mean = sum(values) / n
    centered = [value - mean for value in values]
    variance = sum(value * value for value in centered) / n
    for step in range(1, min(lag, n - 1) + 1):
        weight = 1 - step / (lag + 1)
        variance += 2 * weight * sum(centered[i] * centered[i - step] for i in range(step, n)) / n
    return mean / math.sqrt(variance / n) if variance > 0 else None


class Prices:
    def __init__(self, connection: sqlite3.Connection, start: str) -> None:
        self.connection = connection
        self.start = start
        self.sessions = [row[0] for row in connection.execute(
            "SELECT session_date FROM market_sessions ORDER BY session_date")]
        self.index = {day: i for i, day in enumerate(self.sessions)}
        self.splits: dict[str, list[tuple[str, float, float]]] = defaultdict(list)
        for ticker, execution, split_from, split_to in connection.execute(
                "SELECT ticker, execution_date, split_from, split_to FROM splits"):
            self.splits[ticker].append((execution, split_from, split_to))
        self.bars: dict[str, dict[str, tuple[float, float]]] = {}

    def series(self, ticker: str) -> dict[str, tuple[float, float]]:
        if ticker not in self.bars:
            self.bars[ticker] = {day: (open_, close) for day, open_, close in self.connection.execute(
                "SELECT session_date, open, close FROM raw_daily_bars WHERE ticker = ? AND session_date >= ?",
                (ticker, self.start))}
        return self.bars[ticker]

    def forward(self, ticker: str, signal_day: str, holding: int) -> tuple[float | None, str, str | None]:
        """Return (split-adjusted return, status, exit day) for entry at T+1 open, exit at T+h close."""
        i = self.index[signal_day]
        entry_i, exit_i = i + 1, i + holding
        if exit_i >= len(self.sessions):
            return None, "no_label", None
        bars = self.series(ticker)
        entry_day = self.sessions[entry_i]
        if entry_day not in bars or not bars[entry_day][0]:
            return None, "no_entry_bar", None
        last = entry_i
        for j in range(entry_i, exit_i + 1):
            if self.sessions[j] in bars and bars[self.sessions[j]][1]:
                last = j
            else:
                break
        exit_day = self.sessions[last]
        entry_price = bars[entry_day][0]
        for execution, split_from, split_to in self.splits.get(ticker, ()):
            if entry_day < execution <= exit_day:
                entry_price *= split_from / split_to
        status = "ok" if last == exit_i else "gap_exit"
        return bars[exit_day][1] / entry_price - 1, status, exit_day

    def forward_to(self, ticker: str, signal_day: str, exit_day: str) -> float:
        """Return for the benchmark over a holding that already ended on ``exit_day``."""
        entry_day = self.sessions[self.index[signal_day] + 1]
        bars = self.series(ticker)
        entry_price = bars[entry_day][0]
        for execution, split_from, split_to in self.splits.get(ticker, ()):
            if entry_day < execution <= exit_day:
                entry_price *= split_from / split_to
        return bars[exit_day][1] / entry_price - 1


def resolve(row: dict) -> tuple[str | None, str]:
    sources = row.get("source_tickers") or []
    if len(sources) == 1:
        return sources[0], "ok"
    if len(sources) > 1:
        return None, "case_collision"
    return None, "unresolved"


def evaluate(records: list[dict], prices: Prices) -> tuple[dict, dict]:
    """Per list key, list type, top N and holding: dated rows of (slot excess, unfilled excess, hit, listed)."""
    series: dict = defaultdict(list)
    counts: dict = defaultdict(lambda: defaultdict(int))
    spy_cache: dict = {}
    for record in records:
        day = record["session"]
        for key, block in record["lists"].items():
            ranked = block["rows"]
            variants = {"mixed": ranked, "stock": [row for row in ranked if row.get("stock_or_etf_track") == "stock"]}
            for list_type, rows in variants.items():
                for top in TOPS:
                    names = rows[:top]
                    for holding in HOLDINGS:
                        excesses, hits = [], 0
                        labelled = True
                        for row in names:
                            ticker, why = resolve(row)
                            counts[(key, list_type, top, holding)][f"resolve_{why}"] += 1
                            if ticker is None:
                                continue
                            result, status, exit_day = prices.forward(ticker, day, holding)
                            counts[(key, list_type, top, holding)][f"label_{status}"] += 1
                            if status == "no_label":
                                labelled = False
                                break
                            if result is None:
                                continue
                            spy_key = (day, exit_day)
                            if spy_key not in spy_cache:
                                spy_cache[spy_key] = prices.forward_to("SPY", day, exit_day)
                            excess = result - spy_cache[spy_key]
                            excesses.append(excess)
                            hits += excess > 0
                        if not labelled or (not names and prices.index[day] + holding >= len(prices.sessions)):
                            continue
                        series[(key, list_type, top, holding)].append({
                            "day": day,
                            "slot": sum(excesses) / top,
                            "unfilled": sum(excesses) / len(excesses) if excesses else None,
                            "hit": hits / len(excesses) if excesses else None,
                            "listed": len(names),
                            "tickers": [row["ticker"] for row in names],
                        })
    return series, counts


def summarize(points: list[dict], holding: int) -> dict:
    slot = [point["slot"] for point in points]
    unfilled = [point["unfilled"] for point in points if point["unfilled"] is not None]
    hits = [point["hit"] for point in points if point["hit"] is not None]
    listed = [point["listed"] for point in points]
    return {
        "days": len(points),
        "mean_slot_pct": round(100 * statistics.fmean(slot), 3) if slot else None,
        "t_slot": (lambda t: round(t, 2) if t is not None else None)(newey_west_t(slot, max(0, holding // 5 - 1))),
        "mean_unfilled_pct": round(100 * statistics.fmean(unfilled), 3) if unfilled else None,
        "hit_rate": round(statistics.fmean(hits), 3) if hits else None,
        "median_listed": statistics.median(listed) if listed else None,
    }


def turnover(points: list[dict], top: int) -> float | None:
    changes = []
    for before, after in zip(points, points[1:]):
        a, b = set(before["tickers"][:top]), set(after["tickers"][:top])
        if a or b:
            changes.append(1 - len(a & b) / max(len(a), len(b)))
    return round(statistics.fmean(changes), 3) if changes else None


def periods(points: list[dict]) -> dict[str, list[dict]]:
    out = {"ALL": points, "P1": [p for p in points if p["day"] <= P1_END],
           "P2": [p for p in points if p["day"] > P1_END]}
    for year in sorted({p["day"][:4] for p in points}):
        out[year] = [p for p in points if p["day"][:4] == year]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--dates-from", help="only use replay dates on or after this day")
    args = parser.parse_args()
    records = []
    skipped = defaultdict(int)
    for path in sorted(args.replay.glob("20*.json.gz")):
        record = json.load(gzip.open(path))
        if args.dates_from and record["session"] < args.dates_from:
            continue
        if record.get("status") != "scored" or not record.get("gate_ok"):
            skipped[record.get("status") if record.get("status") != "scored" else "gate_failed"] += 1
            continue
        records.append(record)
    if not records:
        raise SystemExit("no scored replay dates")
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    prices = Prices(connection, records[0]["session"])
    series, counts = evaluate(records, prices)

    args.out.mkdir(parents=True, exist_ok=True)
    rows = []
    for (key, list_type, top, holding), points in sorted(series.items()):
        variant, profile, view = key.split("/")
        for period, subset in periods(points).items():
            if not subset:
                continue
            summary = summarize(subset, holding)
            summary["days_short"] = sum(1 for point in subset if point["listed"] < top)
            rows.append({"variant": variant, "profile": profile, "view": view, "list_type": list_type,
                         "top": top, "holding": holding, "period": period, **summary,
                         "turnover": turnover(subset, top)})
    with (args.out / "metrics.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    with (args.out / "label_status.json").open("w") as handle:
        json.dump({"|".join(map(str, key)): dict(value) for key, value in counts.items()}, handle, indent=1)
    print(f"dates used {len(records)}, skipped {dict(skipped)}, first {records[0]['session']}, "
          f"last {records[-1]['session']}; metrics rows {len(rows)}")

    def primary(variant: str, profile: str, period: str, list_type: str, holding: int = 63) -> float | None:
        values = [row["mean_slot_pct"] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == period
                  and row["holding"] == holding and row["top"] == 20 and row["list_type"] == list_type]
        return round(statistics.fmean(values), 3) if len(values) == len(VIEWS) else None

    def median_listed(variant: str, profile: str, list_type: str) -> float | None:
        values = [row["median_listed"] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == "ALL"
                  and row["holding"] == 20 and row["top"] == 20 and row["list_type"] == list_type]
        return statistics.fmean(values) if values else None

    variants = sorted({row["variant"] for row in rows}, key=lambda name: (name != "v15", name))
    years = sorted({row["period"] for row in rows if row["period"].isdigit()})
    table = []
    for list_type in ("mixed", "stock"):
        for profile in ("conservative", "balanced", "aggressive"):
            for variant in variants:
                if primary(variant, profile, "ALL", list_type) is None:
                    continue
                entry = {"list_type": list_type, "profile": profile, "variant": variant}
                for period in ("ALL", "P1", "P2", *years):
                    entry[f"h63_{period}"] = primary(variant, profile, period, list_type)
                entry["h20_ALL"] = primary(variant, profile, "ALL", list_type, holding=20)
                entry["h5_ALL"] = primary(variant, profile, "ALL", list_type, holding=5)
                entry["median_listed"] = median_listed(variant, profile, list_type)
                table.append(entry)
    with (args.out / "primary.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    print("\nprimary metric: top-20 slot-filled excess vs SPY, % per signal, mean of the three views")
    for entry in table:
        print("  " + ", ".join(f"{k} {v}" for k, v in entry.items()))

    def lookup(list_type: str, profile: str, variant: str) -> dict | None:
        return next((e for e in table if (e["list_type"], e["profile"], e["variant"]) == (list_type, profile, variant)),
                    None)

    def decide(cand: dict, ref: dict) -> dict:
        def up(key: str) -> bool:
            return cand.get(key) is not None and ref.get(key) is not None and cand[key] > ref[key]
        compared = [y for y in years if cand.get(f"h63_{y}") is not None and ref.get(f"h63_{y}") is not None]
        return {
            "P1_up": up("h63_P1"),
            "P2_up": up("h63_P2"),
            "years_better": sum(1 for y in compared if cand[f"h63_{y}"] > ref[f"h63_{y}"]),
            "years_compared": len(compared),
            "h20_within_1pp": cand["h20_ALL"] is not None and ref["h20_ALL"] is not None
            and cand["h20_ALL"] >= ref["h20_ALL"] - 1.0,
            "length_ok": (cand["median_listed"] or 0) >= 0.5 * (ref["median_listed"] or 0),
            "smaller_period_gain": round(min((cand["h63_P1"] or 0) - (ref["h63_P1"] or 0),
                                             (cand["h63_P2"] or 0) - (ref["h63_P2"] or 0)), 3),
        }

    verdicts: dict = {"variant_vs_v15_mixed": {}, "variant_vs_v15_stock": {}, "stock_vs_mixed": {}}
    for list_type in ("mixed", "stock"):
        print(f"\npre-registered decision checks, variant vs v15 on the {list_type} list (balanced, aggressive):")
        for variant in variants:
            if variant == "v15":
                continue
            for profile in ("balanced", "aggressive"):
                cand, ref = lookup(list_type, profile, variant), lookup(list_type, profile, "v15")
                if cand and ref:
                    verdict = decide(cand, ref)
                    verdicts[f"variant_vs_v15_{list_type}"][f"{variant}/{profile}"] = verdict
                    print(f"  {variant} {profile}: {verdict}")
    print("\nstock-only top 20 vs the mixed list of the same variant:")
    for variant in variants:
        for profile in ("conservative", "balanced", "aggressive"):
            cand, ref = lookup("stock", profile, variant), lookup("mixed", profile, variant)
            if cand and ref:
                verdict = decide(cand, ref)
                verdicts["stock_vs_mixed"][f"{variant}/{profile}"] = verdict
                print(f"  {variant} {profile}: {verdict}")
    with (args.out / "decision.json").open("w") as handle:
        json.dump(verdicts, handle, indent=1)
    print("DONE evaluate")


if __name__ == "__main__":
    main()
