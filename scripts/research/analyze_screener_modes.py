#!/usr/bin/env python3
"""Report the six pre-registered screener modes from one compact dump."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.metrics import date_clustered_mean, spearman_rank_ic, summarize_daily_ics, top_k_mean
from app.services.research.protocol import SCREENER_MODES, split_for_date
from app.services.research.screener import apply_disable_market_fit, apply_price_filter, apply_screener_mode


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["signal_date"])].append(row)
    return grouped


def _mode_summary(grouped: dict[str, list[dict]], rows_for_day) -> dict:
    ranking_ics = []
    tops = {"5": [], "10": [], "20": []}
    for session, items in grouped.items():
        ranked = rows_for_day(items)
        ranking_ics.append(
            {
                "signal_date": session,
                **spearman_rank_ic(
                    [row.get("ranking_score") for row in ranked],
                    [
                        ((row.get("excess") or {}).get("20") or {}).get("excess_vs_universe")
                        for row in ranked
                    ],
                ),
            }
        )
        for k in (5, 10, 20):
            tops[str(k)].append(
                top_k_mean(
                    ranked,
                    score_key="ranking_score",
                    outcome_key=("excess", "20", "excess_vs_universe"),
                    k=k,
                )
            )

    def _mean_excess(items: list[dict]) -> dict:
        values = [
            (f"d{index}", float(item["excess"]))
            for index, item in enumerate(items)
            if item.get("status") == "active" and item.get("excess") is not None
        ]
        return date_clustered_mean(values)

    return {
        "ranking_ic": summarize_daily_ics(ranking_ics),
        "top_excess": {key: _mean_excess(value) for key, value in tops.items()},
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    if any(split_for_date(row["signal_date"]) == "sealed" for row in rows):
        raise SystemExit("refusing sealed rows")
    grouped = _group(rows)
    modes = {}
    for spec in SCREENER_MODES:
        key = f"{spec['timeframe']}/{spec['profile']}"
        modes[key] = _mode_summary(
            grouped,
            lambda items, timeframe=spec["timeframe"], profile=spec["profile"]: apply_screener_mode(
                items, timeframe=timeframe, profile=profile
            ),
        )
    payload = {
        "row_count": len(rows),
        "day_count": len(grouped),
        "modes": modes,
        "candidate_disable_market_fit": _mode_summary(grouped, apply_disable_market_fit),
        "candidate_unadjusted_min_price": _mode_summary(
            grouped,
            lambda items: apply_screener_mode(
                apply_price_filter(items, min_price=5.0, price_key="unadjusted_close"),
                timeframe="all",
                profile="balanced",
            ),
        ),
        "notes": [
            "short/mid/long 使用生产 _sort_scored（期限分 0.94 + ranking 0.06）。",
            "conservative/aggressive 使用生产 score_profile_fit + score_ranking。",
            "candidate-unadjusted-min-price 只能从已通过复权 min_price 的视图再过滤，不能找回被复权价踢掉的股票。",
        ],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({key: value["ranking_ic"] for key, value in modes.items()}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
