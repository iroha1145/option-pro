"""Forward returns and pre-registered metrics for replayed v1.7 lists.

The return convention, the observation rules (verified exits, censoring, the three
bounds) and the per-date metric come from ``full_market_v1_6/scripts/evaluate.py``, which
this script imports by path so the two packs cannot drift apart. See that module and
v1.7 PREREGISTRATION.md 修订 3 for the rules. v1.7 adds:

* the baseline is the ``v16`` production code (``--baseline``), the main list is the
  stock-only list, and the mixed list is reported as a secondary view;
* decisions are made per profile a variant scored, so a conservative-only candidate
  is compared with the baseline's conservative view;
* pre-registered robustness pairs (``g3``/``g4``, ``full3``/``full4``) are checked;
* one diagnostic per list: the share of top-20 stock rows without a SIC class;
* ``identity.json`` counts, per view, the dates on which a variant's kept stock rows
  equal the baseline's on their common prefix (ticker and sort_score at nine decimals;
  the variant may keep more stock rows because funds no longer take slots of the
  KEEP_ROWS-limited mixed list) - the acceptance test of the fund-scope candidate
  ``nofund``. Differences in ``n``, which counts fund rows, are reported separately.

    python evaluate.py --db replay.sqlite --replay /content/replay/v17_stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --out results/stage1_reeval
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sqlite3
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_V16 = HERE.parents[1] / "full_market_v1_6" / "scripts" / "evaluate.py"
_spec = importlib.util.spec_from_file_location("v16_evaluate", _V16)
v16 = sys.modules.get(_spec.name) or importlib.util.module_from_spec(_spec)
if _spec.name not in sys.modules:
    sys.modules[_spec.name] = v16
    _spec.loader.exec_module(v16)

# Shared with the v1.6 pack; re-exported so tests and sibling scripts have one name for them.
Directory, Outcome, Prices = v16.Directory, v16.Outcome, v16.Prices
HOLDINGS, TOPS, P1_END, VIEWS, BOUNDS, RULES = v16.HOLDINGS, v16.TOPS, v16.P1_END, v16.VIEWS, v16.BOUNDS, v16.RULES
newey_west_t, resolve, summarize, turnover, periods = v16.newey_west_t, v16.resolve, v16.summarize, v16.turnover, v16.periods
coverage_rows, load_records, write_csv, decide = v16.coverage_rows, v16.load_records, v16.write_csv, v16.decide

PROFILES = ("conservative", "balanced", "aggressive")
PAIRS = (("g3", "g4"), ("full3", "full4"))
IDENTITY_VARIANTS = ("nofund",)


def _unclassified_share(rows: list[dict]) -> dict:
    stocks = [row for row in rows if row.get("stock_or_etf_track") == "stock"]
    share = sum(1 for row in stocks if row.get("industry_id") is None) / len(stocks) if stocks else None
    return {"unclassified": share}


def evaluate(records: list[dict], prices: "Prices") -> tuple[dict, dict]:
    """The v1.6 evaluation plus the unclassified-share diagnostic on every point."""
    return v16.evaluate(records, prices, row_metrics=_unclassified_share)


def stock_identity(records: list[dict], baseline: str, variant: str) -> dict:
    """Per view: dates compared and dates on which the variant's stock rows equal the baseline's.

    The replay keeps the first KEEP_ROWS rows of the mixed list, so a variant that
    scores fewer funds keeps more stock rows than the baseline: the comparison is on
    the common prefix of the kept stock rows (ticker and sort_score at nine decimals),
    and the variant may only have extra rows, never fewer. ``n`` counts fund rows as
    well; its differences are reported separately and do not count against identity.
    """
    out: dict = {}
    for record in records:
        for key, block in record["lists"].items():
            name, profile, view = key.split("/")
            if name != variant:
                continue
            reference = record["lists"].get(f"{baseline}/{profile}/{view}")
            if reference is None:
                continue
            def stock_rows(rows):
                return [(row["ticker"], round(float(row["sort_score"]), 9)) for row in rows
                        if row.get("stock_or_etf_track") == "stock"]
            item = out.setdefault(f"{profile}/{view}", {
                "dates": 0, "identical": 0, "differing_dates": [],
                "compared_rows": 0, "extra_variant_rows": 0, "n_differing_dates": 0,
            })
            item["dates"] += 1
            candidate, base = stock_rows(block["rows"]), stock_rows(reference["rows"])
            prefix = min(len(candidate), len(base))
            item["compared_rows"] += prefix
            if candidate[:prefix] == base[:prefix] and len(candidate) >= len(base):
                item["identical"] += 1
                item["extra_variant_rows"] += len(candidate) - len(base)
            else:
                item["differing_dates"].append(record["session"])
            if block.get("n") != reference.get("n"):
                item["n_differing_dates"] += 1
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append",
                        help="replay directory; repeat to merge the date halves of two machines")
    parser.add_argument("--directory", type=Path,
                        help="weekly point-in-time directory folder; without it gaps cannot be verified and are censored")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", default="v16")
    parser.add_argument("--dates-from", help="only use replay dates on or after this day")
    args = parser.parse_args()
    records, skipped = load_records(args.replay, dates_from=args.dates_from)
    if not records:
        raise SystemExit("no scored replay dates")
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = Directory(args.directory) if args.directory else None
    prices = Prices(connection, records[0]["session"], directory)
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
            unclassified = [point["unclassified"] for point in subset if point.get("unclassified") is not None]
            summary["unclassified_share"] = round(statistics.fmean(unclassified), 3) if unclassified else None
            rows.append({"variant": variant, "profile": profile, "view": view, "list_type": list_type,
                         "top": top, "holding": holding, "period": period, **summary,
                         "turnover": turnover(subset, top)})
    write_csv(args.out / "metrics.csv", rows)
    with (args.out / "label_status.json").open("w") as handle:
        json.dump({"|".join(map(str, key)): dict(value) for key, value in counts.items()}, handle, indent=1)
    write_csv(args.out / "coverage.csv", coverage_rows(series))
    with (args.out / "rules.json").open("w") as handle:
        json.dump({**RULES, "directory": None if directory is None else str(directory.folder),
                   "identity_verification": directory is not None}, handle, indent=1)
    print(f"dates used {len(records)}, skipped {skipped}, first {records[0]['session']}, "
          f"last {records[-1]['session']}; metrics rows {len(rows)}; identity verification "
          f"{'on' if directory else 'OFF (gaps censored as unverified)'}")

    def primary(variant: str, profile: str, period: str, list_type: str, holding: int = 63,
                field_name: str = "mean_slot_pct") -> float | None:
        values = [row[field_name] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == period
                  and row["holding"] == holding and row["top"] == 20 and row["list_type"] == list_type
                  and row[field_name] is not None]
        return round(statistics.fmean(values), 3) if len(values) == len(VIEWS) else None

    def secondary(variant: str, profile: str, list_type: str, field_name: str) -> float | None:
        values = [row[field_name] for row in rows
                  if row["variant"] == variant and row["profile"] == profile and row["period"] == "ALL"
                  and row["holding"] == 20 and row["top"] == 20 and row["list_type"] == list_type
                  and row[field_name] is not None]
        return round(statistics.fmean(values), 3) if values else None

    baseline = args.baseline
    variants = sorted({row["variant"] for row in rows}, key=lambda name: (name != baseline, name))
    years = sorted({row["period"] for row in rows if row["period"].isdigit()})
    table = []
    for list_type in ("stock", "mixed"):
        for profile in PROFILES:
            for variant in variants:
                if primary(variant, profile, "ALL", list_type) is None:
                    continue
                entry = {"list_type": list_type, "profile": profile, "variant": variant}
                for period in ("ALL", "P1", "P2", *years):
                    entry[f"h63_{period}"] = primary(variant, profile, period, list_type)
                for bound in BOUNDS:
                    entry[f"h63_ALL_{bound}"] = primary(variant, profile, "ALL", list_type, field_name=f"mean_slot_{bound}_pct")
                entry["h63_unfilled_ALL"] = primary(variant, profile, "ALL", list_type, field_name="mean_unfilled_pct")
                entry["h63_unfilled_P1"] = primary(variant, profile, "P1", list_type, field_name="mean_unfilled_pct")
                entry["h63_unfilled_P2"] = primary(variant, profile, "P2", list_type, field_name="mean_unfilled_pct")
                entry["h20_ALL"] = primary(variant, profile, "ALL", list_type, holding=20)
                entry["h5_ALL"] = primary(variant, profile, "ALL", list_type, holding=5)
                entry["median_listed"] = secondary(variant, profile, list_type, "median_listed")
                entry["turnover"] = secondary(variant, profile, list_type, "turnover")
                entry["observable_share_h63"] = primary(variant, profile, "ALL", list_type, field_name="observable_share")
                entry["unclassified_share"] = secondary(variant, profile, list_type, "unclassified_share")
                table.append(entry)
    write_csv(args.out / "primary.csv", table)
    print("\nprimary metric: top-20 slot-filled excess vs SPY on observable names, % per signal, mean of the three views")
    for entry in table:
        print("  " + ", ".join(f"{k} {v}" for k, v in entry.items()))

    def lookup(list_type: str, profile: str, variant: str) -> dict | None:
        return next((e for e in table if (e["list_type"], e["profile"], e["variant"]) == (list_type, profile, variant)),
                    None)

    def verdict_for(cand: dict, ref: dict) -> dict:
        verdict = decide(cand, ref, years)
        verdict["rules_1_2_4_5"] = bool(
            verdict["P1_up"] and verdict["P2_up"] and verdict["h20_within_1pp"] and verdict["length_ok"]
            and verdict["years_compared"] and verdict["years_better"] * 4 >= verdict["years_compared"] * 3
        )
        verdict["unfilled_P1_up"] = (cand.get("h63_unfilled_P1") or 0) > (ref.get("h63_unfilled_P1") or 0)
        verdict["unfilled_P2_up"] = (cand.get("h63_unfilled_P2") or 0) > (ref.get("h63_unfilled_P2") or 0)
        verdict["length_ok_80pct"] = (cand["median_listed"] or 0) >= 0.8 * (ref["median_listed"] or 0)
        return verdict

    verdicts: dict = {"baseline": baseline, "rules": RULES, "variant_vs_baseline": {}, "pairs": {}, "stock_vs_mixed": {}}
    for list_type in ("stock", "mixed"):
        print(f"\npre-registered checks, variant vs {baseline} on the {list_type} list:")
        for variant in variants:
            if variant == baseline:
                continue
            for profile in PROFILES:
                cand, ref = lookup(list_type, profile, variant), lookup(list_type, profile, baseline)
                if cand and ref:
                    verdict = verdict_for(cand, ref)
                    verdicts["variant_vs_baseline"][f"{variant}/{profile}/{list_type}"] = verdict
                    print(f"  {variant} {profile}: {verdict}")
    for left, right in PAIRS:
        for profile in PROFILES:
            pair = {}
            for name in (left, right):
                verdict = verdicts["variant_vs_baseline"].get(f"{name}/{profile}/stock")
                pair[name] = None if verdict is None else verdict["smaller_period_gain"]
            if all(value is not None for value in pair.values()):
                verdicts["pairs"][f"{left}|{right}/{profile}"] = {
                    **pair, "same_direction": (pair[left] > 0) == (pair[right] > 0)}
    print("\nstock-only top 20 vs the mixed list of the same variant:")
    for variant in variants:
        for profile in PROFILES:
            cand, ref = lookup("stock", profile, variant), lookup("mixed", profile, variant)
            if cand and ref:
                verdict = verdict_for(cand, ref)
                verdicts["stock_vs_mixed"][f"{variant}/{profile}"] = verdict
                print(f"  {variant} {profile}: {verdict}")
    with (args.out / "decision.json").open("w") as handle:
        json.dump(verdicts, handle, indent=1)
    identity = {variant: stock_identity(records, baseline, variant) for variant in IDENTITY_VARIANTS
                if variant in variants}
    if identity:
        with (args.out / "identity.json").open("w") as handle:
            json.dump(identity, handle, indent=1)
        for variant, views in identity.items():
            for view, item in sorted(views.items()):
                print(f"  identity {variant} {view}: {item['identical']}/{item['dates']} dates identical on the "
                      f"common prefix, {item['extra_variant_rows']} extra kept stock rows, n differs on "
                      f"{item['n_differing_dates']} dates")
    print("DONE evaluate")


if __name__ == "__main__":
    main()
