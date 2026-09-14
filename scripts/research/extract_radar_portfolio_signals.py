#!/usr/bin/env python3
"""Turn radar first-trigger events into next-open research portfolio signals.

This is not production radar ranking. All events share rank 1; capacity is
assigned by ascending ticker. Do not call the resulting ledger a production
radar return.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


RESEARCH_PROTOCOL = "event_plus_alphabetical_research"


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
                "capacity_priority": "alphabetical_ticker_ascending",
                "protocol": RESEARCH_PROTOCOL,
                "not_production_radar_rank": True,
            }
        )
    selected.sort(
        key=lambda item: (
            str(item.get("signal_date") or ""),
            str(item.get("ticker") or ""),
        )
    )
    Path(args.out).write_text(json.dumps(selected, indent=2, ensure_ascii=True) + "\n")
    print(
        json.dumps(
            {
                "signals": len(selected),
                "wrote": args.out,
                "protocol": RESEARCH_PROTOCOL,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
