#!/usr/bin/env python3
"""Summarize a completed screener replay without touching the sealed split."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.metrics import date_clustered_mean, summarize_daily_ics
from app.services.research.protocol import PRIMARY_HORIZON, split_for_date


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    payload = json.loads(Path(args.run).read_text(encoding="utf-8"))
    days = payload.get("days") or []
    if any(split_for_date(item["signal_date"]) == "sealed" for item in days):
        raise SystemExit("refusing to summarize a run that contains sealed dates")
    by_year: dict[str, list] = defaultdict(list)
    for item in days:
        year = str(item["signal_date"])[:4]
        by_year[year].append({"signal_date": item["signal_date"], **(item.get("ic") or {})})
    top_excess = []
    for item in days:
        top10 = ((item.get("top_k") or {}).get("10") or {})
        if top10.get("status") == "active" and top10.get("excess") is not None:
            top_excess.append((item["signal_date"], float(top10["excess"])))
    summary = {
        "source": args.run,
        "split": payload.get("split"),
        "protocol_hash": payload.get("protocol_hash"),
        "run_identity": payload.get("run_identity"),
        "day_count": len(days),
        "ic_summary": summarize_daily_ics(
            {"signal_date": item["signal_date"], **(item.get("ic") or {})} for item in days
        ),
        "ic_by_year": {
            year: summarize_daily_ics(values, horizon_days=PRIMARY_HORIZON)
            for year, values in sorted(by_year.items())
        },
        "top10_excess": date_clustered_mean(top_excess, horizon_days=PRIMARY_HORIZON),
        "review_status": "repaired_round2",
        "notes": [
            *(payload.get("notes") or []),
            "IC 与 Top-K CI 使用 20 日块 bootstrap，不是普通日期分块 SE。",
            "若源 dump 的逐日 Top-K 仍是修复前算法，应先用 analyze_screener_rows 按行重算。",
        ],
    }
    Path(args.out).write_text(json.dumps(summary, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    print(json.dumps(summary["ic_summary"], ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
