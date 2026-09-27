"""Export backtest data for replayed screener lists: daily series, top-20 lists, equity curves.

Uses evaluate.py's forward-return logic unchanged. Two equity curves per list, both
equal weight, empty slots in SPY (as in the slot-filled metric), no costs, price only:

- weekly: each signal date T (every 5 sessions) buys the top 20 at T+1 open and sells
  at T+5 close, so consecutive holdings are contiguous (a full weekly turnover);
- overlapping 13 weeks: every signal starts a cohort held for 13 signal periods (about
  63 sessions, the primary metric's horizon); each week the portfolio is the average of
  the last 13 cohorts, the standard overlapping-portfolio construction.

    python export_backtest.py --db replay.sqlite --replay /content/replay/stage1 \
        --out results/stage1/backtest --variants v15,tilt_b
"""
from __future__ import annotations

import argparse
import csv
import gzip
import importlib.util
import json
import sqlite3
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v16_evaluate", HERE / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

PROFILES = ("conservative", "balanced", "aggressive")


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--variants", default="v15")
    args = parser.parse_args()
    wanted = {name.strip() for name in args.variants.split(",") if name.strip()}
    records = []
    for path in sorted(args.replay.glob("20*.json.gz")):
        record = json.load(gzip.open(path))
        if record.get("status") != "scored" or not record.get("gate_ok"):
            continue
        record["lists"] = {key: value for key, value in record["lists"].items() if key.split("/")[0] in wanted}
        records.append(record)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    prices = evaluate.Prices(connection, records[0]["session"])
    series, _ = evaluate.evaluate(records, prices)
    args.out.mkdir(parents=True, exist_ok=True)

    lists = []
    for record in records:
        for key, block in sorted(record["lists"].items()):
            variant, profile, view = key.split("/")
            stock_rank = 0
            for rank, row in enumerate(block["rows"][:40], 1):
                is_stock = row.get("stock_or_etf_track") == "stock"
                stock_rank += is_stock
                if rank > 20 and not (is_stock and stock_rank <= 20):
                    continue
                lists.append({"date": record["session"], "variant": variant, "profile": profile, "view": view,
                              "mixed_rank": rank if rank <= 20 else "", "stock_rank": stock_rank if is_stock and stock_rank <= 20 else "",
                              "ticker": row["ticker"], "track": row.get("stock_or_etf_track"),
                              "sort_score": round(float(row["sort_score"]), 4) if row.get("sort_score") is not None else "",
                              "family": row.get("algorithm_id"), "entry_state": row.get("entry_state"),
                              "price": row.get("price")})
    write_csv(args.out / "top20_lists.csv", lists)

    daily = []
    for (key, list_type, top, holding), points in sorted(series.items()):
        if top != 20:
            continue
        variant, profile, view = key.split("/")
        for point in points:
            daily.append({"date": point["day"], "variant": variant, "profile": profile, "view": view,
                          "list_type": list_type, "holding": holding,
                          "slot_excess_pct": round(100 * point["slot"], 4),
                          "unfilled_excess_pct": "" if point["unfilled"] is None else round(100 * point["unfilled"], 4),
                          "hit_rate": "" if point["hit"] is None else round(point["hit"], 4), "listed": point["listed"]})
    write_csv(args.out / "daily_series.csv", daily)

    spy = {}
    for record in records:
        value, status, _ = prices.forward("SPY", record["session"], 5)
        if status == "ok":
            spy[record["session"]] = value
    curves = []
    for (key, list_type, top, holding), points in sorted(series.items()):
        if top != 20 or holding != 5:
            continue
        variant, profile, view = key.split("/")
        portfolio = benchmark = 1.0
        for point in points:
            if point["day"] not in spy:
                continue
            benchmark *= 1 + spy[point["day"]]
            portfolio *= 1 + spy[point["day"]] + point["slot"]
            curves.append({"date": point["day"], "variant": variant, "profile": profile, "view": view,
                           "list_type": list_type, "holding": "weekly", "portfolio": round(portfolio, 6),
                           "spy": round(benchmark, 6), "relative": round(portfolio / benchmark, 6)})
    ranked = {}
    for record in records:
        for key, block in record["lists"].items():
            for list_type in ("mixed", "stock"):
                rows = [row for row in block["rows"] if list_type == "mixed" or row.get("stock_or_etf_track") == "stock"]
                ranked[(key, list_type, record["session"])] = [evaluate.resolve(row)[0] for row in rows[:20]]
    days = [record["session"] for record in records]
    weekly_return: dict = {}
    for (key, list_type) in sorted({(k, t) for k, t, _ in ranked}):
        portfolio = benchmark = 1.0
        for index, day in enumerate(days):
            if day not in spy:
                continue
            cohorts = []
            for start in days[max(0, index - 12): index + 1]:
                names = ranked.get((key, list_type, start), [])
                total = 0.0
                for ticker in names:
                    if ticker is None:
                        total += spy[day]
                        continue
                    key_ret = (ticker, day)
                    if key_ret not in weekly_return:
                        value, status, _ = prices.forward(ticker, day, 5)
                        weekly_return[key_ret] = value if value is not None else None
                    value = weekly_return[key_ret]
                    total += spy[day] if value is None else value
                cohorts.append((total + (20 - len(names)) * spy[day]) / 20)
            benchmark *= 1 + spy[day]
            portfolio *= 1 + sum(cohorts) / len(cohorts)
            variant, profile, view = key.split("/")
            curves.append({"date": day, "variant": variant, "profile": profile, "view": view,
                           "list_type": list_type, "holding": "overlap13w", "portfolio": round(portfolio, 6),
                           "spy": round(benchmark, 6), "relative": round(portfolio / benchmark, 6)})
    write_csv(args.out / "equity_curves.csv", curves)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib unavailable; charts skipped")
    else:
        for profile in PROFILES:
            figure, axes = plt.subplots(1, 2, figsize=(13, 4.8), sharey=True)
            drawn = False
            for axis, holding, title in ((axes[0], "weekly", "weekly turnover"),
                                         (axes[1], "overlap13w", "13-week overlapping cohorts")):
                for variant in sorted(wanted):
                    for list_type in ("mixed", "stock"):
                        points = [c for c in curves if (c["variant"], c["profile"], c["view"], c["list_type"],
                                                        c["holding"]) == (variant, profile, "mid", list_type, holding)]
                        if points:
                            axis.plot([p["date"] for p in points], [p["relative"] for p in points],
                                      label=f"{variant} {list_type}")
                            drawn = True
                axis.axhline(1.0, color="grey", linewidth=0.8)
                axis.set_title(f"{profile} / mid: {title}")
                axis.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(6))
                axis.tick_params(axis="x", labelrotation=30)
            axes[0].set_ylabel("top-20 portfolio / SPY (no costs)")
            axes[1].legend(fontsize=8)
            if drawn:
                figure.tight_layout()
                figure.savefig(args.out / f"equity_{profile}_mid.png", dpi=110)
            plt.close(figure)
    print(f"lists {len(lists)} rows, daily {len(daily)} rows, curves {len(curves)} rows -> {args.out}")
    print("DONE export_backtest")


if __name__ == "__main__":
    main()
