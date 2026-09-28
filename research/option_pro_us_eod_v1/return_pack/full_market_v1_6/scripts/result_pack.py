"""One machine-readable JSON for a return pack: metrics, coverage, legacy-versus-fixed deltas, ledger end values.

Reads the result directories evaluate.py / paired.py / export_backtest.py wrote, so every
number in the pack is traceable to a CSV in the repository. ``--stage`` may be repeated:
``name=reeval_dir[:legacy_dir]``; when a legacy directory (the pre-fix evaluation) is given,
the pack lists per variant, profile and list type the primary metric under the old rule,
the fixed rule and the three bounds, so the movement of every prior conclusion is a number.
``--compare`` rows (``label=baseline:candidate[:profile][@stage]``) pull the paired differences the
comparisons rest on. ``unverifiable_execution_data`` is written on every pack.

    python result_pack.py --stage stage1=results/stage1_reeval:results/stage1 \\
        --stage stage2b=results/stage2b_reeval:results/stage2b \\
        --compare tilt_b=v15:tilt_b --backtest results/stage1_reeval/backtest --out results/result_pack.json
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

UNVERIFIABLE_EXECUTION_DATA = [
    "fills: entry at the grouped daily open and exit at the grouped daily close; no intraday prices, "
    "spreads or partial fills are available",
    "volume: Massive grouped daily volume, session hours unverified; no capacity or participation check",
    "delisting proceeds: a censored name has no verifiable exit price; the three bounds bracket it, none is the "
    "realised value (cash-merger consideration, bankruptcy recovery and OTC prices are not in the data)",
    "dividends and cash distributions: price returns only, for the lists and for SPY",
    "corporate actions other than splits (spin-offs, rights, exchange offers) are not applied",
    "renames are followed only when the directory identity and a continuous price verify them",
    "costs: a flat per-side basis-point assumption, not observed execution costs",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open() as handle:
        return list(csv.DictReader(handle))


def number(value: Any) -> float | None:
    if value in (None, "", "None"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def primary_by_key(rows: list[dict[str, str]]) -> dict[tuple[str, str, str], dict[str, str]]:
    return {(row["list_type"], row["profile"], row["variant"]): row for row in rows}


def stage_pack(name: str, reeval: Path, legacy: Path | None) -> dict[str, Any]:
    fixed = primary_by_key(read_csv(reeval / "primary.csv"))
    old = primary_by_key(read_csv(legacy / "primary.csv")) if legacy else {}
    variants = []
    for key in sorted(fixed):
        row = fixed[key]
        legacy_row = old.get(key)
        entry = {
            "list_type": key[0], "profile": key[1], "variant": key[2],
            "h63_primary": number(row.get("h63_ALL")), "h63_P1": number(row.get("h63_P1")), "h63_P2": number(row.get("h63_P2")),
            "h63_bound_legacy": number(row.get("h63_ALL_legacy")), "h63_bound_zero": number(row.get("h63_ALL_zero")),
            "h63_bound_loss": number(row.get("h63_ALL_loss")),
            "h20_primary": number(row.get("h20_ALL")), "h5_primary": number(row.get("h5_ALL")),
            "median_listed": number(row.get("median_listed")), "observable_share_h63": number(row.get("observable_share_h63")),
            "h63_pre_fix": None if legacy_row is None else number(legacy_row.get("h63_ALL")),
        }
        if entry["h63_pre_fix"] is not None and entry["h63_primary"] is not None:
            entry["h63_fix_delta"] = round(entry["h63_primary"] - entry["h63_pre_fix"], 3)
        variants.append(entry)
    coverage = read_csv(reeval / "coverage.csv")
    paired = read_csv(reeval / "paired.csv")
    decision_path = reeval / "decision.json"
    identity_path = reeval / "identity.json"
    rules_path = reeval / "rules.json"
    decision = json.loads(decision_path.read_text()) if decision_path.exists() else None
    return {
        "stage": name,
        "results_dir": str(reeval), "legacy_results_dir": None if legacy is None else str(legacy),
        "baseline": None if decision is None else decision.get("baseline"),
        "rules": json.loads(rules_path.read_text()) if rules_path.exists() else None,
        "variants": variants,
        "coverage": [{**row, **{k: number(v) for k, v in row.items() if k not in {"variant", "profile", "list_type"}}}
                     for row in coverage],
        # Pre-fix paired.csv files have no metric or days_removed column: they are the legacy metric.
        "paired_h63": [{**row, "metric": row.get("metric") or "slot_legacy", "days": number(row.get("days")),
                        "days_removed": number(row.get("days_removed")), "mean_diff_pct": number(row.get("mean_diff_pct")),
                        "nw_t": number(row.get("nw_t")), "share_days_positive": number(row.get("share_days_positive")),
                        "holding": number(row.get("holding"))}
                       for row in paired if row.get("holding") == "63"],
        "decision": decision,
        "identity": json.loads(identity_path.read_text()) if identity_path.exists() else None,
    }


def comparison(pack_stage: dict[str, Any], baseline: str, candidate: str, profile: str | None) -> list[dict[str, Any]]:
    """The candidate's paired difference against the baseline and both levels, per metric, stock list, 63 sessions.

    Empty unless the stage was evaluated against ``baseline`` (its decision.json says so)
    and holds the candidate's difference rows; a baseline's own level rows never satisfy
    a comparison. ``baseline == candidate`` asks for that variant's stock-minus-mixed rows.
    """
    rows = [row for row in pack_stage["paired_h63"] if row["period"] == "ALL" and (not profile or row["profile"] == profile)]
    if baseline == candidate:
        return [{"what": f"{candidate} stock list minus mixed list", **row}
                for row in rows if row["comparison"] == "stock_minus_mixed" and row["variant"] == candidate]
    if pack_stage.get("baseline") not in (None, baseline):
        return []
    differences = [row for row in rows if row["comparison"] == "variant_minus_baseline_stock" and row["variant"] == candidate]
    if not differences:
        return []
    out = [{"what": f"{candidate} minus {baseline}, stock list", **row} for row in differences]
    for row in rows:
        if row["comparison"] == "level_stock" and row["variant"] in {baseline, candidate}:
            out.append({"what": f"{row['variant']} own excess over SPY, stock list", **row})
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--stage", action="append", required=True, help="name=reeval_dir[:legacy_dir]")
    parser.add_argument("--compare", action="append", default=[],
                        help="label=baseline:candidate[:profile][@stage]; resolved in the first stage evaluated against "
                             "that baseline that holds the candidate, or only in @stage when several qualify "
                             "(baseline == candidate: stock minus mixed)")
    parser.add_argument("--backtest", type=Path, action="append", default=[], help="backtest directory with ledger_end_values.json")
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--note", action="append", default=[])
    args = parser.parse_args()
    stages = []
    for item in args.stage:
        name, _, dirs = item.partition("=")
        reeval, _, legacy = dirs.partition(":")
        stages.append(stage_pack(name, Path(reeval), Path(legacy) if legacy else None))
    comparisons = {}
    for item in args.compare:
        label, _, spec = item.partition("=")
        spec, _, only_stage = spec.partition("@")
        parts = spec.split(":")
        baseline, candidate = parts[0], parts[1]
        profile = parts[2] if len(parts) > 2 else None
        for stage in stages:
            if only_stage and stage["stage"] != only_stage:
                continue
            rows = comparison(stage, baseline, candidate, profile)
            if rows:
                comparisons[label] = {"stage": stage["stage"], "baseline": baseline, "candidate": candidate,
                                      "profile": profile, "rows": rows,
                                      # False when the stage's decision.json is missing: the baseline is then assumed.
                                      "baseline_verified": stage.get("baseline") == baseline or baseline == candidate}
                break
    ledgers = []
    for folder in args.backtest:
        path = folder / "ledger_end_values.json"
        if path.exists():
            payload = json.loads(path.read_text())
            ledgers.append({"backtest_dir": str(folder), "cost_bps": payload.get("cost_bps"), "rules": payload.get("rules"),
                            "identity_verification": payload.get("identity_verification"),
                            "end_values": payload.get("end_values")})
    pack = {
        "generated_for": "option-pro EOD screener research packs; every value traces to a CSV/JSON in results/",
        "stages": stages,
        "comparisons": comparisons,
        "ledgers": ledgers,
        "notes": args.note,
        "unverifiable_execution_data": UNVERIFIABLE_EXECUTION_DATA,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(pack, indent=1, default=str))
    print(f"result pack -> {args.out}: {len(stages)} stages, {len(comparisons)} comparisons, {len(ledgers)} ledgers")


if __name__ == "__main__":
    main()
