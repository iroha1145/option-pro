#!/usr/bin/env python3
"""Build portfolio signals from a screener row dump. Rank filter only."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-rank", type=int, default=10)
    parser.add_argument("--score-key", default="ranking_score")
    args = parser.parse_args()
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    selected = []
    for row in rows:
        rank = row.get("selected_view_rank")
        if rank is None or int(rank) > args.max_rank:
            continue
        selected.append(
            {
                "ticker": row.get("ticker"),
                "signal_date": row.get("signal_date"),
                "selected_view_rank": rank,
                "rank": rank,
                "score": row.get(args.score_key),
            }
        )
    Path(args.out).write_text(json.dumps(selected, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({"signals": len(selected), "wrote": args.out}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
