"""Row-by-row comparison of replayed lists across runs, code versions or machines.

Compares the top-N rows (ticker order and sort_score at ``--decimals`` decimals) of every
view shared by two replay directories, after mapping variant names (``--map v15=v16`` reads
the left run's ``v15`` lists against the right run's ``v16``). Exit status 1 when any view
differs, so the check can gate a longer run.

    python compare_records.py --left /content/replay/verify_v16 --right /content/replay/v17_verify \\
        --map v15=v16 --top 20
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path


def load(folder: Path) -> dict[str, dict]:
    records = {}
    for path in sorted(folder.glob("20*.json.gz")):
        record = json.load(gzip.open(path))
        if record.get("status") == "scored":
            records[record["session"]] = record
    return records


def signature(rows: list[dict], top: int, decimals: int) -> list[tuple]:
    return [(row["ticker"], round(float(row["sort_score"]), decimals)) for row in rows[:top]]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--left", required=True, type=Path)
    parser.add_argument("--right", required=True, type=Path)
    parser.add_argument("--map", action="append", default=[], help="left_variant=right_variant (repeatable)")
    parser.add_argument("--top", type=int, default=20)
    parser.add_argument("--decimals", type=int, default=9)
    args = parser.parse_args()
    mapping = dict(item.split("=", 1) for item in args.map)
    left, right = load(args.left), load(args.right)
    shared_dates = sorted(set(left) & set(right))
    compared = identical = 0
    differing: list[str] = []
    for day in shared_dates:
        for key, block in left[day]["lists"].items():
            variant, profile, view = key.split("/")
            right_key = f"{mapping.get(variant, variant)}/{profile}/{view}"
            other = right[day]["lists"].get(right_key)
            if other is None:
                continue
            compared += 1
            if signature(block["rows"], args.top, args.decimals) == signature(other["rows"], args.top, args.decimals) \
                    and block["n"] == other["n"]:
                identical += 1
            else:
                differing.append(f"{day} {key} -> {right_key}")
    print(f"dates shared {len(shared_dates)}, views compared {compared}, identical {identical}")
    for item in differing[:50]:
        print("  differs:", item)
    if compared == 0 or differing:
        sys.exit(1)


if __name__ == "__main__":
    main()
