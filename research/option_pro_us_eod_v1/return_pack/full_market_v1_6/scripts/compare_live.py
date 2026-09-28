"""Compare a replayed session against the lists production served for the same session.

Pass criteria, fixed before the first comparison: for every view the replayed top 20
shares at least 18 names with the live top 20, and at least 90% of the names common
to both lists differ in sort_score by less than 0.5 points. The replay used a directory
fetched on 2026-09-27 and provider data frozen on that day, so small drift is expected;
anything beyond the tolerance is treated as a harness bug until explained.

    python compare_live.py --replay fidelity/2026-09-25.json.gz --live live-ref/
"""
from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

PROFILES = ("conservative", "balanced", "aggressive")
HORIZONS = ("short", "mid", "long")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay", required=True, type=Path)
    parser.add_argument("--live", required=True, type=Path, help="folder with scan_<profile>_<horizon>.json")
    args = parser.parse_args()
    record = json.load(gzip.open(args.replay))
    print(f"replay {record['session']} directory {record['directory']} eligible {record['eligible']} "
          f"complete {record.get('complete')} gate_ok {record.get('gate_ok')}")
    failures = 0
    for profile in PROFILES:
        for horizon in HORIZONS:
            live = json.loads((args.live / f"scan_{profile}_{horizon}.json").read_text())
            live_rows = live["observation_rows"]
            replay_rows = record["lists"][f"v15/{profile}/{horizon}"]["rows"]
            replay_n = record["lists"][f"v15/{profile}/{horizon}"]["n"]
            live_top = [row["ticker"] for row in live_rows[:20]]
            replay_top = [row["ticker"] for row in replay_rows[:20]]
            overlap = len(set(live_top) & set(replay_top))
            need = min(18, len(live_top), len(replay_top)) if len(live_top) < 20 or len(replay_top) < 20 else 18
            live_score = {row["ticker"]: float(row["sort_score"]) for row in live_rows}
            replay_score = {row["ticker"]: float(row["sort_score"]) for row in replay_rows}
            common = sorted(set(live_score) & set(replay_score))
            close = sum(abs(live_score[t] - replay_score[t]) < 0.5 for t in common)
            close_share = close / len(common) if common else 1.0
            ok = overlap >= need and close_share >= 0.9 and (len(live_top) == len(replay_top) or overlap >= 18)
            failures += not ok
            print(f"{'PASS' if ok else 'FAIL'} {profile}/{horizon}: list n live {len(live_rows)} replay {replay_n}; "
                  f"top20 overlap {overlap}/{min(20, len(live_top))}; score |diff|<0.5 for {close}/{len(common)}")
            if not ok or overlap < len(live_top):
                print(f"   only live: {sorted(set(live_top) - set(replay_top))}")
                print(f"   only replay: {sorted(set(replay_top) - set(live_top))}")
                worst = sorted(common, key=lambda t: -abs(live_score[t] - replay_score[t]))[:5]
                print("   largest diffs: " + ", ".join(
                    f"{t} {live_score[t]:.2f}->{replay_score[t]:.2f}" for t in worst))
    print(f"views failing: {failures}")
    print("DONE compare_live")


if __name__ == "__main__":
    main()
