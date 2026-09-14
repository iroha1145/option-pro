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

from app.services.research.metrics import date_clustered_mean, paired_difference_ci
from app.services.research.protocol import PRIMARY_HORIZON, split_for_date
from app.services.research.radar import prior_screener_overlap


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


def _primary_key(events: list[dict]) -> str:
    if any(event.get("excess_vs_universe_20d") is not None for event in events):
        return "excess_vs_universe_20d"
    return "excess_vs_spy_20d"


def _paired_overlap_vs_all(marked: list[dict], key: str) -> dict:
    """Same-event indicator contrast: overlap dummy minus the full-radar value.

    For each event with a finite label, pair the overlap subset indicator
    against the same event's outcome. Also report date-aligned overlap vs
    all-radar daily means where both exist.
    """

    overlap_values = []
    all_values = []
    for event in marked:
        value = _finite(event.get(key))
        if value is None:
            continue
        all_values.append(value)
        if event.get("in_prior_screener_top"):
            overlap_values.append(value)
    date_all: dict[str, list[float]] = {}
    date_overlap: dict[str, list[float]] = {}
    for event in marked:
        value = _finite(event.get(key))
        if value is None:
            continue
        session = str(event.get("trading_date"))
        date_all.setdefault(session, []).append(value)
        if event.get("in_prior_screener_top"):
            date_overlap.setdefault(session, []).append(value)
    shared_dates = sorted(set(date_all) & set(date_overlap))
    left = [sum(date_overlap[day]) / len(date_overlap[day]) for day in shared_dates]
    right = [sum(date_all[day]) / len(date_all[day]) for day in shared_dates]
    return {
        "primary_metric_key": key,
        "overlap_event_count": len(overlap_values),
        "all_event_count": len(all_values),
        "shared_date_count": len(shared_dates),
        "date_aligned_paired_difference": paired_difference_ci(
            left,
            right,
            horizon_days=PRIMARY_HORIZON,
        ),
        "note": (
            "Paired difference is overlap-date mean minus all-radar mean on the "
            "same dates. Overlapping CIs of the two separate series are not a "
            "test of no difference. Nested event dependence is retained."
        ),
    }


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
    key = _primary_key(marked)
    payload = {
        "event_count": len(marked),
        "overlap_count": len(overlap),
        "same_day_top_count": len(same_day),
        "primary_metric_key": key,
        "all_events_vs_universe": date_clustered_mean(
            _pairs(marked, "excess_vs_universe_20d"), horizon_days=PRIMARY_HORIZON
        ),
        "prior_screener_overlap_vs_universe": date_clustered_mean(
            _pairs(overlap, "excess_vs_universe_20d"), horizon_days=PRIMARY_HORIZON
        ),
        "all_events_vs_spy": date_clustered_mean(
            _pairs(marked, "excess_vs_spy_20d"), horizon_days=PRIMARY_HORIZON
        ),
        "prior_screener_overlap_vs_spy": date_clustered_mean(
            _pairs(overlap, "excess_vs_spy_20d"), horizon_days=PRIMARY_HORIZON
        ),
        "paired_difference": _paired_overlap_vs_all(marked, key),
        "review_status": "repaired_round2",
        "notes": [
            "联用只使用触发日之前的选股快照，不用当日收盘名单给当日突破背书。",
            "same_day_top_count 仅作泄漏对照，不计入主结论。",
            "主指标优先 excess_vs_universe_20d；SPY 只作补充。",
            "差值检验保留嵌套日期依赖；两段 CI 重叠不是无差异证明。",
            "回撤下降是否只是更少交易，属于待验证解释，不是已证明机制。",
        ],
        "overlap_events": overlap,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(
        json.dumps(
            {
                "overlap_count": payload["overlap_count"],
                "paired_difference": payload["paired_difference"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
