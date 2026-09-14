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
from app.services.research.protocol import PRIMARY_HORIZON, split_for_date
from app.services.research.radar import platform_evolution_groups


def _finite(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _pairs(events: list[dict], key: str) -> list[tuple[str, float]]:
    out = []
    for event in events:
        value = _finite(event.get(key))
        if value is None:
            continue
        out.append((str(event.get("trading_date")), value))
    return out


def _metric(events: list[dict], key: str) -> dict:
    return date_clustered_mean(_pairs(events, key), horizon_days=PRIMARY_HORIZON)


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
    primary_key = (
        "excess_vs_universe_20d"
        if any(event.get("excess_vs_universe_20d") is not None for event in events)
        else "excess_vs_spy_20d"
    )
    payload = {
        "event_count": len(events),
        "extended_count": sum(1 for event in events if event.get("extended")),
        "confirmed_count": sum(1 for event in events if event.get("confirmed")),
        "primary_metric_key": primary_key,
        "all_triggered_20d_vs_universe": _metric(events, "excess_vs_universe_20d"),
        "all_triggered_20d_vs_spy": _metric(events, "excess_vs_spy_20d"),
        "exclude_extended_20d_vs_universe": _metric(no_extended, "excess_vs_universe_20d"),
        "exclude_extended_20d_vs_spy": _metric(no_extended, "excess_vs_spy_20d"),
        "by_year": {
            year: {
                "event_count": len(items),
                "triggered_20d_vs_universe": _metric(items, "excess_vs_universe_20d"),
                "triggered_20d_vs_spy": _metric(items, "excess_vs_spy_20d"),
            }
            for year, items in sorted(by_year.items())
        },
        "platform_evolution": platform_evolution_groups(events),
        "review_status": "repaired_round2",
        "notes": [
            "冻结主指标是 triggered_20d_excess_vs_universe；SPY 只作补充。",
            "同一 ticker+pivot_id 应已在 replay 去重，但不等于首次经济事件。",
            "exclude_extended 是预登记消融，不是事后挑选赢家。",
            "无独立机会集合，召回率不可评估。",
            "CI 使用真实交易日上的 20 日块 bootstrap，不是普通日期分块 SE。",
            "证据等级保持 Grade C：无 Discovery、无分钟、无真实发布时间。",
        ],
    }
    if primary_key == "excess_vs_spy_20d":
        payload["notes"].append(
            "本文件事件缺少 excess_vs_universe_20d 时，主指标不可用，只能报告 SPY 补充项。"
        )
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps(payload["all_triggered_20d_vs_universe"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
