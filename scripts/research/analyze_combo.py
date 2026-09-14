#!/usr/bin/env python3
"""Radar events filtered by a strictly earlier screener snapshot."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.metrics import date_clustered_mean
from app.services.research.protocol import split_for_date
from app.services.research.radar import prior_screener_overlap


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _pairs(events: list[dict]) -> list[tuple[str, float]]:
    out = []
    for event in events:
        value = _finite(event.get("excess_vs_spy_20d"))
        if value is None:
            continue
        out.append((str(event.get("trading_date")), value))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--screener-rows", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--top", type=int, default=20)
    args = parser.parse_args()
    events = json.loads(Path(args.events).read_text(encoding="utf-8"))
    rows = json.loads(Path(args.screener_rows).read_text(encoding="utf-8"))
    if any(split_for_date(event["trading_date"]) == "sealed" for event in events):
        raise SystemExit("refusing sealed events")
    if any(split_for_date(row["signal_date"]) == "sealed" for row in rows):
        raise SystemExit("refusing sealed screener rows")
    marked = prior_screener_overlap(events, rows, top=args.top)
    overlap = [event for event in marked if event.get("in_prior_screener_top")]
    same_day_keys = {
        (str(row.get("signal_date")), str(row.get("ticker")))
        for row in rows
        if row.get("selected_view_rank") is not None and int(row["selected_view_rank"]) <= args.top
    }
    same_day = [
        event
        for event in events
        if (str(event.get("trading_date")), str(event.get("ticker"))) in same_day_keys
    ]
    payload = {
        "event_count": len(marked),
        "overlap_count": len(overlap),
        "same_day_top_count": len(same_day),
        "all_events": date_clustered_mean(_pairs(marked)),
        "prior_screener_overlap": date_clustered_mean(_pairs(overlap)),
        "notes": [
            "联用只使用触发日之前的选股快照，不用当日收盘名单给当日突破背书。",
            "same_day_top_count 仅作泄漏对照，不计入主结论。",
        ],
        "overlap_events": overlap,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(
        json.dumps(
            {
                "overlap_count": payload["overlap_count"],
                "prior_screener_overlap": payload["prior_screener_overlap"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
