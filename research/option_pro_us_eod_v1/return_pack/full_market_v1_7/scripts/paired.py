"""Paired, date-matched differences between replayed v1.7 lists, with Newey-West t-statistics.

Reporting only; it does not change the pre-registered decision rule. Each comparison
subtracts two slot-filled top-20 excess series on the same signal dates (a variant minus
the ``v16`` baseline on the stock-only and the mixed list, and stock-only minus mixed of
the same variant) and reports the mean difference and its Newey-West t (lag h/5 - 1),
overall and per period. Every profile a variant scored is compared, so the conservative
candidate is paired with the baseline's conservative view.

    python paired.py --db replay.sqlite --replay /content/replay/v17_stage1 --out results/stage1
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import sqlite3
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v17_evaluate", HERE / "evaluate.py")
evaluate = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(evaluate)

PROFILES = ("conservative", "balanced", "aggressive")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", default="v16")
    args = parser.parse_args()
    records, _ = evaluate.load_records(args.replay)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    prices = evaluate.Prices(connection, records[0]["session"])
    series, _ = evaluate.evaluate(records, prices)

    def slots(variant: str, profile: str, view: str, list_type: str, holding: int) -> dict[str, float]:
        return {point["day"]: point["slot"] for point in series.get((f"{variant}/{profile}/{view}", list_type, 20, holding), [])}

    variants = sorted({key[0].split("/")[0] for key in series}, key=lambda name: (name != args.baseline, name))
    scored_profiles = {variant: {key[0].split("/")[1] for key in series if key[0].split("/")[0] == variant}
                       for variant in variants}
    comparisons = []
    for variant in variants:
        for profile in PROFILES:
            if profile not in scored_profiles[variant]:
                continue
            comparisons.append(("stock_minus_mixed", variant, profile, (variant, "mixed"), (variant, "stock")))
            if variant != args.baseline and profile in scored_profiles.get(args.baseline, set()):
                for list_type in ("stock", "mixed"):
                    comparisons.append((f"variant_minus_baseline_{list_type}", variant, profile,
                                        (args.baseline, list_type), (variant, list_type)))
    rows = []
    for label, variant, profile, (ref_variant, ref_type), (cand_variant, cand_type) in comparisons:
        for holding in evaluate.HOLDINGS:
            per_day: dict[str, list[float]] = {}
            for view in evaluate.VIEWS:
                ref = slots(ref_variant, profile, view, ref_type, holding)
                cand = slots(cand_variant, profile, view, cand_type, holding)
                for day in sorted(set(ref) & set(cand)):
                    per_day.setdefault(day, []).append(cand[day] - ref[day])
            days = sorted(day for day, values in per_day.items() if len(values) == len(evaluate.VIEWS))
            if not days:
                continue
            for period, subset in (("ALL", days), ("P1", [d for d in days if d <= evaluate.P1_END]),
                                   ("P2", [d for d in days if d > evaluate.P1_END])):
                diffs = [statistics.fmean(per_day[day]) for day in subset]
                if len(diffs) < 3:
                    continue
                t = evaluate.newey_west_t(diffs, max(0, holding // 5 - 1))
                rows.append({"comparison": label, "variant": variant, "profile": profile, "holding": holding,
                             "period": period, "days": len(diffs),
                             "mean_diff_pct": round(100 * statistics.fmean(diffs), 3),
                             "nw_t": round(t, 2) if t is not None else None,
                             "share_days_positive": round(sum(d > 0 for d in diffs) / len(diffs), 3)})
    args.out.mkdir(parents=True, exist_ok=True)
    fieldnames = ["comparison", "variant", "profile", "holding", "period", "days", "mean_diff_pct", "nw_t",
                  "share_days_positive"]
    with (args.out / "paired.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    for row in rows:
        if row["holding"] == 63 and row["period"] == "ALL":
            print("  " + ", ".join(f"{k} {v}" for k, v in row.items()))
    print("DONE paired")


if __name__ == "__main__":
    main()
