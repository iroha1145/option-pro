#!/usr/bin/env python3
"""Turn radar first-trigger events into next-open portfolio signals."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--exclude-extended", action="store_true")
    args = parser.parse_args()
    events = json.loads(Path(args.events).read_text(encoding="utf-8"))
    selected = []
    for event in events:
        if args.exclude_extended and event.get("extended"):
            continue
        selected.append(
            {
                "ticker": event.get("ticker"),
                "signal_date": event.get("trading_date"),
                "selected_view_rank": 1,
                "rank": 1,
                "score": event.get("breakout_distance_atr"),
                "event_id": event.get("event_id"),
                "extended": event.get("extended"),
            }
        )
    Path(args.out).write_text(json.dumps(selected, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({"signals": len(selected), "wrote": args.out}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
