#!/usr/bin/env python3
"""Development-only row analytics: momentum baseline, failures, concentration."""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research.labels import outcome_crosses_split
from app.services.research.metrics import date_clustered_mean, spearman_rank_ic, summarize_daily_ics, top_k_mean
from app.services.research.protocol import split_for_date


def _group(rows: list[dict]) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        grouped[str(row["signal_date"])].append(row)
    return grouped


def _outcome(row: dict, horizon: str = "20") -> float | None:
    if outcome_crosses_split(row, horizon=horizon):
        return None
    value = ((row.get("excess") or {}).get(horizon) or {}).get("excess_vs_universe")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(float(value)) else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rows", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--horizon", default="20")
    args = parser.parse_args()
    rows = json.loads(Path(args.rows).read_text(encoding="utf-8"))
    if any(split_for_date(row["signal_date"]) == "sealed" for row in rows):
        raise SystemExit("refusing sealed rows")
    grouped = _group(rows)
    ranking_ics = []
    momentum_ics = []
    tops = {"5": [], "10": [], "20": []}
    mom_tops = {"5": [], "10": [], "20": []}
    failures = []
    surprises = []
    for session, items in grouped.items():
        ranking_ics.append(
            {
                "signal_date": session,
                **spearman_rank_ic(
                    [row.get("ranking_score") for row in items],
                    [_outcome(row, args.horizon) for row in items],
                ),
            }
        )
        momentum_ics.append(
            {
                "signal_date": session,
                **spearman_rank_ic(
                    [row.get("return_63d") for row in items],
                    [_outcome(row, args.horizon) for row in items],
                ),
            }
        )
        for k in (5, 10, 20):
            tops[str(k)].append(
                (
                    session,
                    top_k_mean(
                        items,
                        score_key="ranking_score",
                        outcome_key=("excess", args.horizon, "excess_vs_universe"),
                        k=k,
                    ),
                )
            )
            mom_tops[str(k)].append(
                (
                    session,
                    top_k_mean(
                        items,
                        score_key="return_63d",
                        outcome_key=("excess", args.horizon, "excess_vs_universe"),
                        k=k,
                    ),
                )
            )
        usable = [
            row
            for row in items
            if row.get("ranking_score") is not None and _outcome(row, args.horizon) is not None
        ]
        if not usable:
            continue
        worst_high = max(
            usable,
            key=lambda row: (row["ranking_score"], -(_outcome(row, args.horizon) or 0)),
        )
        best_low = min(
            usable,
            key=lambda row: (row["ranking_score"], -(_outcome(row, args.horizon) or 0)),
        )
        if _outcome(worst_high, args.horizon) is not None and _outcome(worst_high, args.horizon) < -0.05:
            failures.append(
                {
                    "signal_date": session,
                    "ticker": worst_high.get("ticker"),
                    "ranking_score": worst_high.get("ranking_score"),
                    "prior_return_63d": worst_high.get("return_63d"),
                    "excess": _outcome(worst_high, args.horizon),
                }
            )
        if _outcome(best_low, args.horizon) is not None and _outcome(best_low, args.horizon) > 0.05:
            surprises.append(
                {
                    "signal_date": session,
                    "ticker": best_low.get("ticker"),
                    "ranking_score": best_low.get("ranking_score"),
                    "prior_return_63d": best_low.get("return_63d"),
                    "excess": _outcome(best_low, args.horizon),
                }
            )

    def _mean_excess(items: list[tuple[str, dict]]) -> dict:
        values = [
            (session, float(item["excess"]))
            for session, item in items
            if item.get("status") == "active" and item.get("excess") is not None
        ]
        return date_clustered_mean(values, horizon_days=int(args.horizon))

    payload = {
        "row_count": len(rows),
        "day_count": len(grouped),
        "horizon": args.horizon,
        "ranking_ic": summarize_daily_ics(ranking_ics, horizon_days=int(args.horizon)),
        "momentum63_ic": summarize_daily_ics(momentum_ics, horizon_days=int(args.horizon)),
        "ranking_top_excess": {key: _mean_excess(value) for key, value in tops.items()},
        "momentum63_top_excess": {key: _mean_excess(value) for key, value in mom_tops.items()},
        "high_score_negative_examples": sorted(failures, key=lambda item: item["excess"])[:25],
        "low_score_positive_examples": sorted(surprises, key=lambda item: item["excess"], reverse=True)[:25],
        "review_status": "repaired_round2",
        "notes": [
            "动量对照使用同一日合格池的 return_63d，不是生产分数。",
            "失败/惊喜案例保留，不事后删除。",
            "Top-K 先按事前分数冻结再挂标签；缺标签不换人。",
            "CI 使用真实交易日上的期限长度块 bootstrap，不是普通日期分块 SE。",
            "跨后续 split 的标签在读取时 purge，不进入统计。",
        ],
    }
    Path(args.out).write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n")
    print(json.dumps({"ranking_ic": payload["ranking_ic"], "momentum63_ic": payload["momentum63_ic"]}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
