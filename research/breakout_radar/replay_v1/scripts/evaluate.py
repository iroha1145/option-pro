"""Evaluate replayed breakout-radar events against the pre-registered rules.

    python evaluate.py --db /content/data/replay.sqlite --replay /content/replay/full/seg_* \
        --variants baseline,confirm3,chase15,orb15,orb60,disc5,adv25,basemin15 \
        --directory /content/data/massive_directory_2026-09-27 --out /content/eval/full \
        [--stage2 S1=confirm3+chase15] [--minute-store /content/minute_store] [--no-events]

Every ``--replay`` directory holds ``<variant>/ledger``, ``<variant>/research_bundle.json.gz``
and ``run.json`` (one per segment; pass them all). Outputs: metrics.csv (every variant,
view, entry, holding and period), events_h20.csv (per trigger), result_pack.json,
README_tables.md, decision.json. See harness/evaluation.py for the rules.
"""

from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

PACK = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(PACK))

from harness.evaluation import run  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, required=True, help="frozen daily database (replay.sqlite)")
    parser.add_argument("--replay", type=str, action="append", required=True,
                        help="segment output directory; repeat or use a glob such as /content/replay/full/seg_*")
    parser.add_argument("--variants", default="baseline")
    parser.add_argument("--baseline", default="baseline")
    parser.add_argument("--directory", type=Path, help="weekly point-in-time directory snapshots for identity checks")
    parser.add_argument("--minute-store", type=Path, help="only for ledgers written before the runner recorded next_bar_open")
    parser.add_argument("--stage2", action="append", default=[], help="combination name=part+part (repeatable)")
    parser.add_argument("--no-events", action="store_true", help="skip the per-trigger CSV")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    replay_dirs = []
    for pattern in args.replay:
        matches = sorted(glob.glob(pattern)) or [pattern]
        replay_dirs.extend(Path(m) for m in matches)
    stage2 = dict(item.split("=", 1) for item in args.stage2)
    pack = run(
        replay_dirs, [v.strip() for v in args.variants.split(",") if v.strip()], args.db, args.out,
        baseline=args.baseline, directory=args.directory, minute_store=args.minute_store, stage2=stage2,
        write_events=not args.no_events,
    )
    (args.out / "decision.json").write_text(json.dumps(pack["decisions"], indent=1, default=str))
    for name, coverage in pack["coverage"].items():
        print(f"{name}: days {coverage.get('days')} triggers {coverage.get('triggers')} "
              f"without next_bar_open {coverage.get('triggers_without_next_bar_open')} "
              f"without SPY bar {coverage.get('triggers_without_benchmark_open')}")
    for name, verdict in pack["decisions"].get("variants", {}).items():
        print(f"  {name}: adopt {verdict['adopt']} h20 diff {verdict['h20_diff_pp']}")
    print("DONE evaluate")


if __name__ == "__main__":
    main()
