#!/usr/bin/env python3
"""Development-only radar event summary. Refuses sealed dates."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.metrics import date_clustered_mean
from app.services.research.protocol import split_for_date


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _pairs(events: list[dict], key: str = "excess_vs_spy_20d") -> list[tuple[str, float]]:
    out = []
    for event in events:
        value = _finite(event.get(key))
        if value is None:
            continue
        out.append((str(event.get("trading_date")), value))
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--events", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    events = json.loads(Path(args.events).read_text(encoding="utf-8"))
    if any(split_for_date(event["trading_date"]) == "sealed" for event in events):
        raise SystemExit("refusing sealed events")
    by_year: dict[str, list[dict]] = defaultdict(list)
    for event in events:
        by_year[str(event["trading_date"])[:4]].append(event)
    no_extended = [event for event in events if not event.get("extended")]
    payload = {
        "event_count": len(events),
        "extended_count": sum(1 for event in events if event.get("extended")),
        "confirmed_count": sum(1 for event in events if event.get("confirmed")),
        "all_triggered_20d_vs_spy": date_clustered_mean(_pairs(events)),
        "exclude_extended_20d_vs_spy": date_clustered_mean(_pairs(no_extended)),
        "by_year": {
            year: {
                "event_count": len(items),
                "clustered_excess_vs_spy": date_clustered_mean(_pairs(items)),
            }
            for year, items in sorted(by_year.items())
        },
        "notes": [
            "同一 ticker+pivot_id 应已在 replay 去重。",
            "exclude_extended 是预登记消融，不是事后挑选赢家。",
            "无独立机会集合，召回率不可评估。",
        ],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps(payload["all_triggered_20d_vs_spy"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
