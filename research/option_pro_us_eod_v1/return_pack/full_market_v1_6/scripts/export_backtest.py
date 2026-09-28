"""Export backtest data for replayed screener lists: daily series, top-20 lists, equity curves.

Signal metrics come from evaluate.py. The equity curves are a daily share-and-cash ledger
(``ledger_*``), rebuilt after the 2026-09-28 review; the two pre-fix curves are kept as
labelled legacy diagnostics (``legacy_*``) and are not evidence of an executable holding.

Ledger rules (v1.7 PREREGISTRATION.md 修订 3):

* Capital is split into ``ceil(holding / spacing)`` equal sleeves (13 for a 63-session
  holding and 5-session signals). Signal date T uses sleeve ``((T - first signal) / 5) mod 13``,
  so a date without a scored record leaves its sleeve idle; a sleeve reinvests only its own
  cash, so the first weeks ramp up with idle sleeves at 0%.
* A cohort buys at T+1 open and sells exactly at the T+h close; the same sleeve's next
  cohort buys at (T+65)+1 open, so its cash idles for the sessions in between. A signal
  date without a scored record leaves its sleeve idle for that cycle.
* Twenty equal slots per cohort. A pre-decided empty slot buys SPY (as in the slot-filled
  metric); a name with no bar at T+1 or an uncertain identity stays in cash.
* Daily marks at the close, so overnight moves and the entry day's open-to-close move are
  in the curve. Shares multiply by split_to/split_from once per split execution date after
  the entry session (the entry open already sits on the post-split scale).
* Exit follows evaluate.py's observation rules: a missing bar at T+h sells at the first
  close within EXIT_LAG_SESSIONS; a renamed security is followed; a censored position is
  valued under the stated bound (``legacy``: flat at its last close, ``zero``: last close
  moving with SPY, ``loss``: worthless) and settles to cash at the planned exit.
* A per-side cost in basis points is charged on the traded notional at entry and exit;
  curves are produced at 0 and at the stated cost. SPY is the benchmark under the very
  same sleeve, slot, exit and cost rules (``spy``, ``relative``); ``spy_passive`` is SPY
  bought at the first entry open and held with no costs and no idle cash, the investor's
  alternative (``relative_passive``).
* ``ledger_weekly``: one sleeve, buy T+1 open, sell T+5 close, rebuy at T+6 open, flat
  overnight in between, costs on every round trip. This is what the old "weekly" curve
  actually traded.

    python export_backtest.py --db replay.sqlite --replay /content/replay/stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --out results/stage1_reeval/backtest \\
        --variants v15,tilt_b --cost-bps 10
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v16_evaluate", HERE / "evaluate.py")
evaluate = sys.modules.get(_spec.name) or importlib.util.module_from_spec(_spec)
if _spec.name not in sys.modules:
    sys.modules[_spec.name] = evaluate
    _spec.loader.exec_module(evaluate)

PROFILES = ("conservative", "balanced", "aggressive")
SIGNAL_SPACING = 5
DEFAULT_COST_BPS = 10.0
LEDGER_HOLDINGS = {"ledger_h63": 63, "ledger_weekly": 5}
LEDGER_RULES = {
    "sleeves": "ceil(holding / spacing) equal sleeves; signal date T uses sleeve ((T - first signal) / spacing) mod n; "
               "sleeves do not rebalance each other",
    "entry": "T+1 open", "exit": "T+holding close; missing bar -> first close within evaluate.EXIT_LAG_SESSIONS",
    "late_exit_cash": "a sale after the sleeve's next entry leaves its proceeds idle until the following entry",
    "empty_slot": "SPY", "no_entry_or_identity_uncertain": "cash at 0%", "idle_cash": "0%",
    "missing_signal_record": "sleeve idle for that cycle",
    "splits": "shares x split_to/split_from once per execution date after the entry session (the entry open is "
              "already post-split); a censored position keeps its shares from its first missing bar on; a successor "
              "ticker's splits between the old last bar and its first bar are applied at the switch",
    "passive_spy": "SPY bought at the first entry open and held to the ledger's end, no costs, no idle cash: "
                   "the investor's alternative, reported next to the same-rule SPY sleeves",
    "censored": {"legacy": "flat at the last observed close, settled at the planned exit",
                 "zero": "last observed close moving with SPY, settled at the planned exit",
                 "loss": "worthless from the first missing bar"},
    "costs": "per side, basis points of traded notional, at entry and exit",
    "benchmark": "SPY bought in every slot under the same sleeve, exit and cost rules",
    "ledger_weekly": "one sleeve, buy T+1 open, sell T+5 close, rebuy T+6 open, flat overnight",
}


def write_csv(path: Path, rows: list[dict]) -> None:
    evaluate.write_csv(path, rows)


def load_records(folder: Path, wanted: set[str]) -> list[dict]:
    records, _ = evaluate.load_records([folder])
    for record in records:
        record["lists"] = {key: value for key, value in record["lists"].items() if key.split("/")[0] in wanted}
    return [record for record in records if record["lists"]]


def ranked_names(block: dict, list_type: str, top: int) -> list[str | None]:
    """Top names as resolved tickers: None for an identity collision; padded with "" for empty slots."""
    rows = [row for row in block["rows"] if list_type == "mixed" or row.get("stock_or_etf_track") == "stock"]
    names: list[str | None] = [evaluate.resolve(row)[0] for row in rows[:top]]
    return names + [""] * (top - len(names))


def ledger_curve(records: list[dict], prices: "evaluate.Prices", key: str, list_type: str, *,
                 holding: int, spacing: int = SIGNAL_SPACING, cost_bps: float = 0.0, bound: str = "legacy",
                 top: int = 20, benchmark: bool = False) -> list[dict[str, Any]]:
    """Daily marks of the equal-sleeve ledger; ``benchmark`` buys SPY in every slot instead."""
    if bound not in evaluate.BOUNDS:
        raise ValueError(f"bound must be one of {evaluate.BOUNDS}")
    sessions, index = prices.sessions, prices.index
    cost = cost_bps / 10_000.0
    n_sleeves = math.ceil(holding / spacing)
    signals = [record for record in records if key in record["lists"]]
    if not signals:
        return []
    sleeves = [{"cash": 1.0 / n_sleeves, "positions": []} for _ in range(n_sleeves)]
    # A sleeve is a calendar slot: signal date T uses sleeve ((T - first signal) / spacing) mod n, so a
    # signal date without a scored record leaves its sleeve idle for that cycle instead of shifting the rest.
    origin_i = index[signals[0]["session"]]
    entries: dict[int, list[tuple[int, str, list[str | None]]]] = {}
    for record in signals:
        signal_i = index[record["session"]]
        names = ["SPY"] * top if benchmark else ranked_names(record["lists"][key], list_type, top)
        entries.setdefault(signal_i + 1, []).append((((signal_i - origin_i) // spacing) % n_sleeves, record["session"], names))
    first_i = min(entries)
    end_i = min(len(sessions) - 1, index[signals[-1]["session"]] + holding + evaluate.EXIT_LAG_SESSIONS)
    spy = prices.series("SPY")
    passive_base = spy[sessions[first_i]][0]  # SPY bought at the first entry open and held: the investor's alternative
    first_full: str | None = None
    curve = []
    for day_i in range(first_i, end_i + 1):
        day = sessions[day_i]
        for sleeve_no, signal_day, names in entries.get(day_i, ()):
            sleeve = sleeves[sleeve_no]
            slot_cash = sleeve["cash"] / top
            for name in names:
                ticker = "SPY" if name == "" else name
                if ticker is None:  # identity uncertain: stays in cash
                    continue
                outcome = prices.observe(ticker, signal_day, holding)
                if outcome.status in {"no_entry_bar", "no_label"}:
                    continue
                open_ = prices.series(ticker)[day][0]
                sleeve["cash"] -= slot_cash
                sleeve["positions"].append({
                    "ticker": ticker, "shares": slot_cash * (1 - cost) / open_, "outcome": outcome,
                    "exit_i": index[outcome.exit_day] if outcome.observable else index[signal_day] + holding,
                    "last_close": open_, "last_close_i": day_i, "spy_at_last": spy[day][1],
                    # Splits are applied once per execution date strictly after this day: the entry open
                    # is already on the post-split scale of any split executing on the entry session.
                    "splits_through": day,
                })
        total_positions = 0.0
        for sleeve in sleeves:
            kept = []
            for position in sleeve["positions"]:
                outcome = position["outcome"]
                ticker = position["ticker"]
                if outcome.followed_ticker and day >= outcome.detail["first_new_day"]:
                    ticker = outcome.followed_ticker
                # A censored position keeps the shares it had at its first missing bar: later splits under
                # the same ticker may belong to whoever holds the symbol next.
                frozen = not outcome.observable and outcome.first_gap_day is not None and day >= outcome.first_gap_day
                if not frozen:
                    for execution, split_from, split_to in prices.splits.get(ticker, ()):
                        # Covers a split filed under a successor ticker between the old last bar and its first bar.
                        if position["splits_through"] < execution <= day:
                            position["shares"] *= split_to / split_from
                    position["splits_through"] = day
                bar = prices.series(ticker).get(day)
                if not outcome.observable and outcome.first_gap_day and day >= outcome.first_gap_day:
                    bar = None  # a censored position is not marked with bars after its first missing session
                if bar and bar[1]:
                    position["last_close"], position["last_close_i"] = bar[1], day_i
                    position["spy_at_last"] = spy[day][1]
                    value = position["shares"] * bar[1]
                elif outcome.observable:
                    value = position["shares"] * position["last_close"]  # held through an interior gap
                elif bound == "legacy":
                    value = position["shares"] * position["last_close"]
                elif bound == "zero":
                    value = position["shares"] * position["last_close"] * spy[day][1] / position["spy_at_last"]
                else:
                    value = 0.0
                if day_i >= position["exit_i"]:
                    sleeve["cash"] += value * (1 - cost) if value > 0 else 0.0
                    continue
                kept.append(position)
                total_positions += value
            sleeve["positions"] = kept
        invested = sum(1 for sleeve in sleeves if sleeve["positions"])
        if first_full is None and invested == n_sleeves:
            first_full = day
        curve.append({"date": day, "value": round(sum(sleeve["cash"] for sleeve in sleeves) + total_positions, 8),
                      "invested_sleeves": invested, "first_fully_invested": first_full,
                      "spy_passive": round(spy[day][1] / passive_base, 8) if day in spy else None})
    return curve


def legacy_weekly_curve(points: list[dict], spy_week: dict[str, float]) -> list[dict]:
    """Pre-fix curve: chained (SPY + legacy slot excess) per signal week. Diagnostic only."""
    portfolio = benchmark = 1.0
    out = []
    for point in points:
        if point["day"] not in spy_week:
            continue
        benchmark *= 1 + spy_week[point["day"]]
        portfolio *= 1 + spy_week[point["day"]] + point["slot_legacy"]
        out.append({"date": point["day"], "portfolio": portfolio, "spy": benchmark})
    return out


def legacy_overlap_curve(days: list[str], ranked: dict, key: str, list_type: str, prices: "evaluate.Prices",
                         spy_week: dict[str, float], n_cohorts: int = 13, top: int = 20) -> list[dict]:
    """Pre-fix "13 overlapping weeks": every old cohort re-priced from each new week's T+1 open.

    Kept only as a legacy diagnostic: it drops the previous close to next open move and
    keeps no shares, cash or expiry (see the review of 2026-09-28).
    """
    weekly_return: dict = {}
    portfolio = benchmark = 1.0
    out = []
    for position, day in enumerate(days):
        if day not in spy_week:
            continue
        cohorts = []
        for start in days[max(0, position - (n_cohorts - 1)): position + 1]:
            names = ranked.get((key, list_type, start), [])
            total = 0.0
            for ticker in names:
                if ticker is None:
                    total += spy_week[day]
                    continue
                if (ticker, day) not in weekly_return:
                    value, _status, _exit = prices.forward_legacy(ticker, day, 5)
                    weekly_return[(ticker, day)] = value
                value = weekly_return[(ticker, day)]
                total += spy_week[day] if value is None else value
            cohorts.append((total + (top - len(names)) * spy_week[day]) / top)
        benchmark *= 1 + spy_week[day]
        portfolio *= 1 + sum(cohorts) / len(cohorts)
        out.append({"date": day, "portfolio": portfolio, "spy": benchmark})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--variants", default="v15")
    parser.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    args = parser.parse_args()
    wanted = {name.strip() for name in args.variants.split(",") if name.strip()}
    records = load_records(args.replay, wanted)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = evaluate.Directory(args.directory) if args.directory else None
    prices = evaluate.Prices(connection, records[0]["session"], directory)
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
                          "slot_excess_pct": "" if point["slot"] is None else round(100 * point["slot"], 4),
                          "slot_legacy_pct": round(100 * point["slot_legacy"], 4),
                          "slot_zero_pct": "" if point["slot_zero"] is None else round(100 * point["slot_zero"], 4),
                          "slot_loss_pct": "" if point["slot_loss"] is None else round(100 * point["slot_loss"], 4),
                          "unfilled_excess_pct": "" if point["unfilled"] is None else round(100 * point["unfilled"], 4),
                          "hit_rate": "" if point["hit"] is None else round(point["hit"], 4), "listed": point["listed"],
                          "observable": point["observable"], "unobservable": point["unobservable"],
                          "empty_slots": point["empty_slots"]})
    write_csv(args.out / "daily_series.csv", daily)

    # Legacy diagnostics, computed exactly as before the review.
    spy_week = {}
    for record in records:
        value, status, _ = prices.forward_legacy("SPY", record["session"], 5)
        if status == "ok":
            spy_week[record["session"]] = value
    curves = []
    ranked = {}
    for record in records:
        for key, block in record["lists"].items():
            for list_type in ("mixed", "stock"):
                rows = [row for row in block["rows"] if list_type == "mixed" or row.get("stock_or_etf_track") == "stock"]
                ranked[(key, list_type, record["session"])] = [evaluate.resolve(row)[0] for row in rows[:20]]
    days = [record["session"] for record in records]
    for (key, list_type, top, holding), points in sorted(series.items()):
        if top != 20 or holding != 5:
            continue
        variant, profile, view = key.split("/")
        base = {"variant": variant, "profile": profile, "view": view, "list_type": list_type, "cost_bps": 0, "bound": "legacy"}
        for point in legacy_weekly_curve(points, spy_week):
            curves.append({"date": point["date"], **base, "holding": "legacy_weekly", "portfolio": round(point["portfolio"], 6),
                           "spy": round(point["spy"], 6), "relative": round(point["portfolio"] / point["spy"], 6),
                           "spy_passive": "", "relative_passive": ""})
        for point in legacy_overlap_curve(days, ranked, key, list_type, prices, spy_week):
            curves.append({"date": point["date"], **base, "holding": "legacy_overlap13w", "portfolio": round(point["portfolio"], 6),
                           "spy": round(point["spy"], 6), "relative": round(point["portfolio"] / point["spy"], 6),
                           "spy_passive": "", "relative_passive": ""})

    # The ledger, at zero cost and at the stated cost, under the three censoring bounds.
    end_values = []
    keys = sorted({key for record in records for key in record["lists"]})
    for key in keys:
        variant, profile, view = key.split("/")
        for list_type in ("mixed", "stock"):
            for holding_name, holding in LEDGER_HOLDINGS.items():
                for cost_bps in sorted({0.0, float(args.cost_bps)}):
                    benchmark = ledger_curve(records, prices, key, list_type, holding=holding, cost_bps=cost_bps, benchmark=True)
                    spy_by_day = {point["date"]: point["value"] for point in benchmark}
                    for bound in evaluate.BOUNDS:
                        # Every bound for every ledger: a five-session holding can end in a missing bar too.
                        curve = ledger_curve(records, prices, key, list_type, holding=holding, cost_bps=cost_bps, bound=bound)
                        base = {"variant": variant, "profile": profile, "view": view, "list_type": list_type,
                                "cost_bps": cost_bps, "bound": bound}
                        for point in curve:
                            passive = point["spy_passive"]
                            curves.append({"date": point["date"], **base, "holding": holding_name,
                                           "portfolio": round(point["value"], 6), "spy": round(spy_by_day[point["date"]], 6),
                                           "relative": round(point["value"] / spy_by_day[point["date"]], 6),
                                           "spy_passive": "" if passive is None else round(passive, 6),
                                           "relative_passive": "" if passive is None else round(point["value"] / passive, 6)})
                        if curve:
                            last = curve[-1]
                            end_values.append({**base, "holding": holding_name, "sessions": len(curve),
                                               "first_fully_invested": last["first_fully_invested"],
                                               "portfolio": round(last["value"], 6), "spy": round(spy_by_day[last["date"]], 6),
                                               "relative": round(last["value"] / spy_by_day[last["date"]], 6),
                                               "spy_passive": None if last["spy_passive"] is None else round(last["spy_passive"], 6),
                                               "relative_passive": None if last["spy_passive"] is None else round(last["value"] / last["spy_passive"], 6),
                                               "start_date": curve[0]["date"], "end_date": last["date"]})
    write_csv(args.out / "equity_curves.csv", curves)
    with (args.out / "ledger_end_values.json").open("w") as handle:
        json.dump({"rules": LEDGER_RULES, "observation_rules": evaluate.RULES, "cost_bps": args.cost_bps,
                   "identity_verification": directory is not None, "end_values": end_values}, handle, indent=1)

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
            for axis, holding_name, title in ((axes[0], "ledger_weekly", "weekly: sell T+5 close, rebuy T+6 open"),
                                              (axes[1], "ledger_h63", "63-session sleeves, censored at last close")):
                for variant in sorted(wanted):
                    for list_type in ("mixed", "stock"):
                        for cost_bps, style in ((0.0, "-"), (float(args.cost_bps), "--")):
                            points = [c for c in curves if (c["variant"], c["profile"], c["view"], c["list_type"],
                                                            c["holding"], c["cost_bps"], c["bound"]) ==
                                      (variant, profile, "mid", list_type, holding_name, cost_bps, "legacy")]
                            if points:
                                axis.plot([p["date"] for p in points], [p["relative"] for p in points], style,
                                          label=f"{variant} {list_type} {cost_bps:g} bps")
                                drawn = True
                axis.axhline(1.0, color="grey", linewidth=0.8)
                axis.set_title(f"{profile} / mid: {title}")
                axis.xaxis.set_major_locator(matplotlib.ticker.MaxNLocator(6))
                axis.tick_params(axis="x", labelrotation=30)
            axes[0].set_ylabel("ledger / SPY ledger")
            axes[1].legend(fontsize=7)
            if drawn:
                figure.tight_layout()
                figure.savefig(args.out / f"equity_{profile}_mid.png", dpi=110)
            plt.close(figure)
    print(f"lists {len(lists)} rows, daily {len(daily)} rows, curves {len(curves)} rows, "
          f"end values {len(end_values)} -> {args.out}")
    print("DONE export_backtest")


if __name__ == "__main__":
    main()
