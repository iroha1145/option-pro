"""Paired, date-matched differences between replayed v1.7 lists, with Newey-West t-statistics.

Reporting only; it does not change the pre-registered decision rule. The comparison
logic is ``full_market_v1_6/scripts/paired.py`` (imported by path): every comparison on
the primary metric and the three censoring bounds, dates one side cannot observe counted
in ``days_removed``, and ``level`` rows giving each variant's own excess over SPY. Every
profile a variant scored is compared, so the conservative candidate is paired with the
baseline's conservative view; ``--replay`` may be repeated to merge two machines' dates.

    python paired.py --db replay.sqlite --replay /content/replay/v17_stage1 \\
        --directory /content/data/massive_directory_2026-09-27 --out results/stage1_reeval
"""
from __future__ import annotations

import argparse
import importlib.util
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = sys.modules.get(spec.name) or importlib.util.module_from_spec(spec)
    if spec.name not in sys.modules:
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return module


evaluate = _load("v17_evaluate", HERE / "evaluate.py")
v16_paired = _load("v16_paired", HERE.parents[1] / "full_market_v1_6" / "scripts" / "paired.py")

PROFILES = ("conservative", "balanced", "aggressive")
METRICS = v16_paired.METRICS
FIELDNAMES = v16_paired.FIELDNAMES
paired_rows = v16_paired.paired_rows


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", required=True)
    parser.add_argument("--replay", required=True, type=Path, action="append")
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--baseline", default="v16")
    parser.add_argument("--dates-from")
    args = parser.parse_args()
    records, _ = evaluate.load_records(args.replay, dates_from=args.dates_from)
    connection = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    directory = evaluate.Directory(args.directory) if args.directory else None
    prices = evaluate.Prices(connection, records[0]["session"], directory)
    series, _ = evaluate.evaluate(records, prices)
    rows = paired_rows(series, args.baseline)
    args.out.mkdir(parents=True, exist_ok=True)
    if rows:
        evaluate.write_csv(args.out / "paired.csv", rows)
    else:
        (args.out / "paired.csv").write_text(",".join(FIELDNAMES) + "\n")
    for row in rows:
        if row["holding"] == 63 and row["period"] == "ALL" and row["metric"] == "slot":
            print("  " + ", ".join(f"{k} {v}" for k, v in row.items()))
    print("DONE paired")


if __name__ == "__main__":
    main()
