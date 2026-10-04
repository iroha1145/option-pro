"""Run replay segments in parallel processes, one log per segment, one retry on a real failure.

    python run_segments.py --segments /content/segments.txt --parallel 40 \
        --out-root /content/replay/full --db-root /content/db/full --log-root /content/logs/full \
        --delete-db-after -- --daily-db /content/data/replay.sqlite --minute-store /content/minute_store ...

``segments.txt`` holds one ``<start> <end>`` pair per line. Everything after ``--`` is
passed to replay.py unchanged; ``--start``, ``--end``, ``--out``, ``--db-dir`` and
``--label`` are added per segment. A segment is retried once only when its process
exited with a positive code and did not finish (no run.json); a process killed by a
signal (negative code) or stopped through ``<out>/STOP`` is left alone. With
``--delete-db-after`` the segment's SQLite directory is removed once run.json and every
variant's research bundle exist (RUN_SPEC section 7: disk is the binding constraint).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

REPLAY = Path(__file__).resolve().with_name("replay.py")
STOP_FILE = "STOP"


def finished(out: Path, variants: list[str]) -> bool:
    if not (out / "run.json").exists():
        return False
    return all((out / name.replace("+", "_") / "research_bundle.json.gz").exists() for name in variants)


def should_retry(code: int, out: Path, attempt: int) -> bool:
    """Retry once after a positive exit code without a run.json; never after a signal or a STOP."""

    if attempt >= 2 or code == 0 or code < 0:
        return False
    if (out / STOP_FILE).exists() or (out / "run.json").exists():
        return False
    return True


def variants_of(passthrough: list[str]) -> list[str]:
    for index, item in enumerate(passthrough):
        if item == "--variants" and index + 1 < len(passthrough):
            return [part.strip() for part in passthrough[index + 1].split(",") if part.strip()]
        if item.startswith("--variants="):
            return [part.strip() for part in item.split("=", 1)[1].split(",") if part.strip()]
    return ["baseline"]


def run_one(start: str, end: str, args: argparse.Namespace, passthrough: list[str]) -> tuple[str, int, str]:
    out = Path(args.out_root) / f"seg_{start}"
    db_dir = Path(args.db_root) / f"seg_{start}"
    Path(args.log_root).mkdir(parents=True, exist_ok=True)
    command = [sys.executable, str(REPLAY), *passthrough, "--start", start, "--end", end,
               "--out", str(out), "--db-dir", str(db_dir), "--label", f"seg_{start}"]
    code = 1
    attempt = 0
    while True:
        attempt += 1
        with open(Path(args.log_root) / f"seg_{start}.attempt{attempt}.log", "w") as log:
            code = subprocess.call(command, stdout=log, stderr=subprocess.STDOUT)
        if not should_retry(code, out, attempt):
            break
    outcome = "finished" if finished(out, variants_of(passthrough)) else ("stopped" if (out / STOP_FILE).exists() else "incomplete")
    if args.delete_db_after and outcome == "finished":
        shutil.rmtree(db_dir, ignore_errors=True)
    return start, code, outcome


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--segments", type=Path, required=True)
    parser.add_argument("--parallel", type=int, default=40)
    parser.add_argument("--out-root", required=True)
    parser.add_argument("--db-root", required=True)
    parser.add_argument("--log-root", required=True)
    parser.add_argument("--delete-db-after", action="store_true",
                        help="remove a segment's SQLite directory once run.json and every bundle exist")
    args, passthrough = parser.parse_known_args()
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    pairs = [line.split() for line in args.segments.read_text().splitlines() if line.strip() and not line.startswith("#")]
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda pair: run_one(pair[0], pair[1], args, passthrough), pairs))
    by_outcome = {}
    for start, code, outcome in results:
        by_outcome.setdefault(outcome, []).append(f"{start}:{code}")
    print(f"segments {len(results)} " + " ".join(f"{k} {len(v)}" for k, v in sorted(by_outcome.items())))
    for outcome, items in sorted(by_outcome.items()):
        if outcome != "finished":
            print(f"  {outcome}: {items}")
    print("DONE run_segments")


if __name__ == "__main__":
    main()
