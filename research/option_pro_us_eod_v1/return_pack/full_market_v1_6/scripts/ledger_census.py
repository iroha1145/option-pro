"""Census of the three ledger defects fixed after the second review (2026-09-28), on real replay records.

For every exported ledger (list key x list type x holding, top 20) it counts the cases the old
``ledger_curve`` handled wrongly, so the effect on the historical results can be stated rather
than guessed. Zero is a valid answer.

* B  ``tail_entries_skipped``: names of signals whose holding runs past the data end (the
  evaluator's ``no_label``) that have a bar at T+1. The old ledger never bought them, so it
  ended fully in cash; the new ledger enters them and reports ``open_at_end``.
* C1 ``rename_gap_splits``: renamed positions with a split filed under the successor ticker
  strictly inside the rename gap (old last bar, first new bar). The old ledger missed it, so
  the position was carried at 1/f of its value from the switch to the exit.
* C2 ``missing_bar_splits``: held positions with a split executing on a session without a bar
  for the held ticker. ``held_gap``: the old ledger multiplied the shares but kept the pre-split
  mark until the next bar (a transient spike, no end effect). ``old_leg_after_last_bar``: a split
  filed under the old ticker after its last bar, inside a rename gap; the old ledger applied it
  and never reverted it (an end effect), the evaluator ignores it.

``--before`` / ``--after`` take two ``ledger_end_values.json`` files (the committed one and the
regenerated one) and list the end-value changes per ledger; those numbers are the actual effect,
the per-case estimates are approximate (they ignore costs and the sleeve's cash path).

    python ledger_census.py --db replay.sqlite --replay /content/replay/stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --variants v15,tilt_b \\
        --before results/stage1_reeval/backtest/ledger_end_values.before.json \\
        --after results/stage1_reeval/backtest/ledger_end_values.json --out results/review2/census_v16_stage1
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"v16_{name}", HERE / f"{name}.py")
    module = sys.modules.get(spec.name) or importlib.util.module_from_spec(spec)
    if spec.name not in sys.modules:
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


evaluate = _load("evaluate")
backtest = _load("export_backtest")

TOP = 20
END_VALUE_KEY = ("variant", "profile", "view", "list_type", "cost_bps", "bound", "holding")


def census(records: list[dict], prices: "evaluate.Prices", *, holdings=tuple(backtest.LEDGER_HOLDINGS.values()),
           top: int = TOP, spacing: int = backtest.SIGNAL_SPACING) -> tuple[dict, list[dict]]:
    """Per (list key, list type, holding): the counts, and one row per case."""
    sessions, index = prices.sessions, prices.index
    last_i = len(sessions) - 1
    groups: dict = {}
    cases: list[dict] = []
    keys = sorted({key for record in records for key in record["lists"]})
    for key in keys:
        variant, profile, view = key.split("/")
        for list_type in ("mixed", "stock"):
            for holding in holdings:
                n_sleeves = math.ceil(holding / spacing)
                weight = 1.0 / (n_sleeves * top)  # a slot's share of the initial capital, the approximate scale of a case
                group = {"variant": variant, "profile": profile, "view": view, "list_type": list_type, "holding": holding,
                         "signals": 0, "tail_signals": 0, "first_tail_signal": None, "tail_entries_skipped": 0,
                         "tail_spy_slots_skipped": 0, "rename_gap_splits": 0, "missing_bar_splits_held_gap": 0,
                         "missing_bar_splits_old_leg_after_last_bar": 0, "renamed_positions": 0,
                         "approx_nav_effect_rename_gap_splits": 0.0, "approx_nav_effect_old_leg_splits": 0.0}
                base = {"variant": variant, "profile": profile, "view": view, "list_type": list_type, "holding": holding}
                for record in records:
                    if key not in record["lists"]:
                        continue
                    signal = record["session"]
                    signal_i = index[signal]
                    group["signals"] += 1
                    tail = signal_i + holding > last_i
                    if tail:
                        group["tail_signals"] += 1
                        group["first_tail_signal"] = group["first_tail_signal"] or signal
                    if signal_i + 1 > last_i:
                        continue
                    entry_day = sessions[signal_i + 1]
                    for name in backtest.ranked_names(record["lists"][key], list_type, top):
                        ticker = "SPY" if name == "" else name
                        if ticker is None:
                            continue
                        if tail:
                            bar = prices.series(ticker).get(entry_day)
                            if bar and bar[0]:
                                group["tail_spy_slots_skipped" if name == "" else "tail_entries_skipped"] += 1
                                if name != "":
                                    cases.append({**base, "case": "B_tail_entry_skipped", "signal": signal, "ticker": ticker,
                                                  "successor": "", "execution": "", "split_factor": "", "kind": "",
                                                  "label_return": "", "approx_nav_effect": ""})
                            continue
                        outcome = prices.observe(ticker, signal, holding)
                        if not outcome.observable:
                            continue
                        successor = outcome.followed_ticker
                        first_new = outcome.detail.get("first_new_day") if successor else None
                        if successor:
                            group["renamed_positions"] += 1
                            for execution, split_from, split_to in prices.splits.get(successor, ()):
                                if outcome.last_day < execution < first_new:
                                    factor = split_to / split_from
                                    effect = weight * (1 + outcome.ret) * (1 - 1 / factor)  # old ledger understated by this
                                    group["rename_gap_splits"] += 1
                                    group["approx_nav_effect_rename_gap_splits"] += effect
                                    cases.append({**base, "case": "C1_rename_gap_split", "signal": signal, "ticker": ticker,
                                                  "successor": successor, "execution": execution, "split_factor": factor,
                                                  "kind": "successor_split_in_gap", "label_return": round(outcome.ret, 6),
                                                  "approx_nav_effect": round(effect, 8)})
                        exit_i = index[outcome.exit_day]
                        for day_i in range(signal_i + 2, exit_i + 1):
                            day = sessions[day_i]
                            held = successor if successor and day >= first_new else ticker
                            bar = prices.series(held).get(day)
                            if bar and bar[1]:
                                continue
                            for execution, split_from, split_to in prices.splits.get(held, ()):
                                if execution != day:
                                    continue
                                factor = split_to / split_from
                                if held == ticker and successor and day > outcome.last_day:
                                    kind = "old_leg_after_last_bar"
                                    effect = weight * (1 + outcome.ret) * (factor - 1)  # old ledger overstated by this
                                    group["missing_bar_splits_old_leg_after_last_bar"] += 1
                                    group["approx_nav_effect_old_leg_splits"] += effect
                                else:
                                    kind = "held_gap"
                                    effect = 0.0  # transient: the next bar's raw quote restored the value
                                    group["missing_bar_splits_held_gap"] += 1
                                cases.append({**base, "case": "C2_missing_bar_split", "signal": signal, "ticker": ticker,
                                              "successor": successor or "", "execution": execution, "split_factor": factor,
                                              "kind": kind, "label_return": round(outcome.ret, 6),
                                              "approx_nav_effect": round(effect, 8)})
                for name in ("approx_nav_effect_rename_gap_splits", "approx_nav_effect_old_leg_splits"):
                    group[name] = round(group[name], 8)
                groups[(key, list_type, holding)] = group
    return groups, cases


def end_value_changes(before: dict | None, after: dict | None) -> list[dict]:
    """One row per ledger in ``after``: the before and after end values and their differences."""
    if not after:
        return []
    old = {tuple(row.get(k) for k in END_VALUE_KEY): row for row in (before or {}).get("end_values", [])}
    rows = []
    for row in after["end_values"]:
        prior = old.get(tuple(row.get(k) for k in END_VALUE_KEY), {})
        out: dict[str, Any] = {k: row.get(k) for k in END_VALUE_KEY}
        for field in ("portfolio", "spy", "relative", "relative_passive", "end_date"):
            out[f"before_{field}"] = prior.get(field)
            out[f"after_{field}"] = row.get(field)
            if isinstance(prior.get(field), (int, float)) and isinstance(row.get(field), (int, float)):
                out[f"delta_{field}"] = round(row[field] - prior[field], 6)
            else:
                out[f"delta_{field}"] = None
        end = row.get("open_at_end") or {}
        out["after_open_positions"] = end.get("positions")
        out["after_unlabelled_positions"] = end.get("unlabelled_positions")
        out["after_positions_without_final_bar"] = end.get("positions_without_final_bar")
        out["after_invested_sleeves"] = end.get("invested_sleeves")
        out["after_cash_share"] = end.get("cash_share")
        rows.append(out)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--variants", required=True)
    parser.add_argument("--before", type=Path, help="ledger_end_values.json written by the old export")
    parser.add_argument("--after", type=Path, help="ledger_end_values.json written by the fixed export")
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    wanted = {name.strip() for name in args.variants.split(",") if name.strip()}
    records = backtest.load_records(args.replay, wanted)
    if not records:
        raise SystemExit("no scored replay dates for the requested variants")
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = evaluate.Directory(args.directory) if args.directory else None
    prices = evaluate.Prices(connection, records[0]["session"], directory)
    groups, cases = census(records, prices)
    before = json.loads(args.before.read_text()) if args.before else None
    after = json.loads(args.after.read_text()) if args.after else None
    changes = end_value_changes(before, after)
    args.out.mkdir(parents=True, exist_ok=True)
    totals = {name: sum(group[name] for group in groups.values())
              for name in ("tail_entries_skipped", "tail_spy_slots_skipped", "rename_gap_splits",
                           "missing_bar_splits_held_gap", "missing_bar_splits_old_leg_after_last_bar", "renamed_positions")}
    summary = {
        "sessions": len(prices.sessions), "data_end": prices.sessions[-1], "dates": len(records),
        "first_signal": records[0]["session"], "last_signal": records[-1]["session"],
        "identity_verification": directory is not None, "variants": sorted(wanted),
        "totals": totals,
        "old_ledger_end_state": "no open positions and 100% cash by construction: unlabelled signals never entered and "
                                "every labelled position exits inside the data",
        "approximate_effects": "per-case estimates use a slot's share of the initial capital and the label return; "
                               "the end_value_changes rows are the actual before/after difference",
        "groups": list(groups.values()),
    }
    (args.out / "census.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False))
    evaluate.write_csv(args.out / "census_cases.csv", cases)
    evaluate.write_csv(args.out / "end_value_changes.csv", changes)
    print(f"census: {len(groups)} ledgers, totals {totals}")
    mid = [row for row in changes if row["view"] == "mid" and row["list_type"] == "stock" and row["bound"] == "legacy"]
    for row in mid:
        print(f"  {row['variant']:22s} {row['profile']:12s} {row['holding']:14s} cost {row['cost_bps']}: relative "
              f"{row['before_relative']} -> {row['after_relative']} (delta {row['delta_relative']}), open at end "
              f"{row['after_open_positions']}, cash share {row['after_cash_share']}")
    print("DONE ledger_census")


if __name__ == "__main__":
    main()
