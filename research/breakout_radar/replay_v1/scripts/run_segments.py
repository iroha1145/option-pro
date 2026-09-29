"""Run replay segments in parallel processes, one log per segment, one retry.

    python run_segments.py --segments /content/segments.txt --parallel 40 \
        --out-root /content/replay/full --db-root /content/db/full --log-root /content/logs/full \
        -- --daily-db /content/data/replay.sqlite --minute-store /content/minute_store ... --variants baseline,...

``segments.txt`` holds one ``<start> <end>`` pair per line. Everything after ``--`` is
passed to replay.py unchanged; ``--start``, ``--end``, ``--out``, ``--db-dir`` and
``--label`` are added per segment. Touch ``<out-root>/seg_<start>/STOP`` to make a
segment finish its current day, write run.json and its bundles, and exit.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPLAY = Path(__file__).resolve().with_name("replay.py")


def run_one(start: str, end: str, args: argparse.Namespace, passthrough: list[str]) -> tuple[str, int]:
    out = Path(args.out_root) / f"seg_{start}"
    db_dir = Path(args.db_root) / f"seg_{start}"
    Path(args.log_root).mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(REPLAY), *passthrough, "--start", start, "--end", end,
               "--out", str(out), "--db-dir", str(db_dir), "--label", f"seg_{start}"]
    code = 1
    for attempt in (1, 2):
        with open(Path(args.log_root) / f"seg_{start}.attempt{attempt}.log", "w") as log:
            code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
        if code == 0 or (out / "run.json").exists():
            break
    return start, code


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=40)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--log-root", required=True)
    args, passthrough = parser.parse_known_args()
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    pairs = [line.split() for line in args.segments.read_text().splitlines() if line.strip()]
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda pair: run_one(pair[0], pair[1], args, passthrough), pairs))
    failed = [start for start, code in results if code != 0]
    print(f"segments {len(results)} failed {len(failed)} {failed}")
    print("DONE run_segments")


if __name__ == "__main__":
    main()
