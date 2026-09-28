"""Paired, date-matched differences between replayed lists, with Newey-West t-statistics.

Reporting only; it does not change the pre-registered decision rule. Each comparison
subtracts two slot-filled top-20 excess series on the same signal dates (for example the
stock-only list minus the mixed list of the same variant) and reports the mean difference
and its Newey-West t (lag h/5 - 1), overall and per period.

Since the 2026-09-28 review (v1.7 PREREGISTRATION.md 修订 3) every comparison is run on
four per-date metrics: ``slot`` (the primary: observable names only, empty slots at SPY),
and the three bounds ``slot_legacy``, ``slot_zero``, ``slot_loss``. A date enters a paired
comparison only when both sides have the metric (``days``); dates that one side cannot
observe are counted in ``days_removed``. ``level`` rows give each variant's own excess
over SPY (variant minus zero) on the same three-view daily mean, so a candidate's own
significance is reported and not only its difference from the baseline.

    python paired.py --db replay.sqlite --replay /content/replay/stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --out results/stage1_reeval
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import sqlite3
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("v16_evaluate", HERE / "evaluate.py")
evaluate = sys.modules.get(_spec.name) or importlib.util.module_from_spec(_spec)
if _spec.name not in sys.modules:
    sys.modules[_spec.name] = evaluate
    _spec.loader.exec_module(evaluate)

PROFILES = ("conservative", "balanced", "aggressive")
METRICS = ("slot", "slot_legacy", "slot_zero", "slot_loss")
FIELDNAMES = ["comparison", "metric", "variant", "profile", "holding", "period", "days", "days_removed",
              "mean_diff_pct", "nw_t", "share_days_positive"]


def paired_rows(series: dict, baseline: str, *, profiles=PROFILES) -> list[dict]:
    def points(variant: str, profile: str, view: str, list_type: str, holding: int) -> dict[str, dict]:
        return {point["day"]: point
                for point in series.get((f"{variant}/{profile}/{view}", list_type, 20, holding), [])}

    variants = sorted({key[0].split("/")[0] for key in series}, key=lambda name: (name != baseline, name))
    scored_profiles = {variant: {key[0].split("/")[1] for key in series if key[0].split("/")[0] == variant}
                       for variant in variants}
    comparisons = []
    for variant in variants:
        for profile in profiles:
            if profile not in scored_profiles[variant]:
                continue
            for list_type in ("mixed", "stock"):
                comparisons.append((f"level_{list_type}", variant, profile, None, (variant, list_type)))
            comparisons.append(("stock_minus_mixed", variant, profile, (variant, "mixed"), (variant, "stock")))
            if variant != baseline and profile in scored_profiles.get(baseline, set()):
                for list_type in ("mixed", "stock"):
                    comparisons.append((f"variant_minus_baseline_{list_type}", variant, profile,
                                        (baseline, list_type), (variant, list_type)))
    rows = []
    for label, variant, profile, ref_spec, (cand_variant, cand_type) in comparisons:
        for holding in evaluate.HOLDINGS:
            for metric in METRICS:
                per_day: dict[str, list[float]] = {}
                removed: set[str] = set()
                for view in evaluate.VIEWS:
                    cand = points(cand_variant, profile, view, cand_type, holding)
                    ref = {} if ref_spec is None else points(ref_spec[0], profile, view, ref_spec[1], holding)
                    days = set(cand) if ref_spec is None else set(cand) & set(ref)
                    for day in sorted(days):
                        cand_value = cand[day].get(metric)
                        ref_value = 0.0 if ref_spec is None else ref[day].get(metric)
                        if cand_value is None or ref_value is None:
                            removed.add(day)
                            continue
                        per_day.setdefault(day, []).append(cand_value - ref_value)
                days = sorted(day for day, values in per_day.items() if len(values) == len(evaluate.VIEWS))
                removed |= {day for day, values in per_day.items() if len(values) != len(evaluate.VIEWS)}
                if not days:
                    continue
                for period, subset in (("ALL", days), ("P1", [d for d in days if d <= evaluate.P1_END]),
                                       ("P2", [d for d in days if d > evaluate.P1_END])):
                    diffs = [statistics.fmean(per_day[day]) for day in subset]
                    if len(diffs) < 3:
                        continue
                    t = evaluate.newey_west_t(diffs, max(0, holding // 5 - 1))
                    rows.append({"comparison": label, "metric": metric, "variant": variant, "profile": profile,
                                 "holding": holding, "period": period, "days": len(diffs),
                                 "days_removed": len([d for d in removed if period == "ALL" or
                                                      (period == "P1") == (d <= evaluate.P1_END)]),
                                 "mean_diff_pct": round(100 * statistics.fmean(diffs), 3),
                                 "nw_t": round(t, 2) if t is not None else None,
                                 "share_days_positive": round(sum(d > 0 for d in diffs) / len(diffs), 3)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", default="v15")
    parser.add_argument("--dates-from")
    args = parser.parse_args()
    records, _ = evaluate.load_records(args.replay, dates_from=args.dates_from)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = evaluate.Directory(args.directory) if args.directory else None
    prices = evaluate.Prices(connection, records[0]["session"], directory)
    series, _ = evaluate.evaluate(records, prices)
    rows = paired_rows(series, args.baseline)
    args.out.mkdir(parents=True, exist_ok=True)
    evaluate.write_csv(args.out / "paired.csv", rows) if rows else (args.out / "paired.csv").write_text(
        ",".join(FIELDNAMES) + "\n")
    for row in rows:
        if row["holding"] == 63 and row["period"] == "ALL" and row["metric"] == "slot":
            print("  " + ", ".join(f"{k} {v}" for k, v in row.items()))
    print("DONE paired")


if __name__ == "__main__":
    main()
