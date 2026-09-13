#!/usr/bin/env python3
"""Long-running isolated soak. Default 2 hours. Exploratory, not a pass gate.

Writes a JSONL sample every cycle plus a final summary. Stop with SIGINT.
Do not aim this at production or paid upstreams.
"""

from __future__ import annotations

import argparse
import json
import time
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from measure_load import PATHS, _one, summarize  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:2000")
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--cycle-s", type=float, default=15.0)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--out", type=Path, default=Path("/opt/cursor/artifacts/perf/soak.jsonl"))
    args = parser.parse_args()
    stop_at = time.time() + args.hours * 3600
    args.out.parent.mkdir(parents=True, exist_ok=True)
    all_samples: list[dict] = []
    cycle = 0
    with args.out.open("w", encoding="utf-8") as handle:
        while time.time() < stop_at:
            cycle += 1
            started = time.time()
            batch = [_one(args.base, path, args.timeout) for path in PATHS]
            all_samples.extend(batch)
            row = {
                "cycle": cycle,
                "at": datetime.now(timezone.utc).isoformat(),
                "summary": summarize(batch),
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
            handle.flush()
            print(f"cycle {cycle} ok={row['summary']['ok']}/{row['summary']['n']} p95={row['summary']['p95_ms']}")
            leftover = args.cycle_s - (time.time() - started)
            if leftover > 0 and time.time() + leftover < stop_at:
                time.sleep(leftover)
    final = args.out.with_suffix(".summary.json")
    final.write_text(
        json.dumps(
            {
                "hours": args.hours,
                "cycles": cycle,
                "summary": summarize(all_samples),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"wrote {args.out} and {final}")


if __name__ == "__main__":
    main()
