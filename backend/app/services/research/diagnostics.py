"""Failure-mode diagnostics that decide the next edit, not a score search."""

from __future__ import annotations

import math
from collections import Counter
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence

from app.services.research.compare import close_excess, close_raw, group_by_date
from app.services.research.protocol import PRIMARY_HORIZON
from app.services.strength.ranking_variants import _sector_bucket


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


NEAR_CAP = 99.5

CONTINUOUS_SCORE_FIELDS = (
    "score_short",
    "score_mid",
    "score_long",
    "ranking_score",
    "rsi14",
    "ath_proximity",
)


def _top_n(rows: Sequence[Mapping[str, Any]], n: int = 20) -> list[Mapping[str, Any]]:
    ordered = [
        row
        for row in rows
        if row.get("ranking_score") is not None
    ]
    ordered.sort(
        key=lambda item: (
            item.get("ranking_score") is not None,
            item.get("ranking_score") if item.get("ranking_score") is not None else -1,
            str(item.get("ticker") or ""),
        ),
        reverse=True,
    )
    return ordered[:n]


def saturation_report(rows: Iterable[Mapping[str, Any]], *, top_n: int = 20) -> dict[str, Any]:
    days = group_by_date(rows)
    field_hits: dict[str, int] = {name: 0 for name in CONTINUOUS_SCORE_FIELDS}
    field_seen: dict[str, int] = {name: 0 for name in CONTINUOUS_SCORE_FIELDS}
    tie_days = 0
    top_count = 0
    for items in days.values():
        leaders = _top_n(items, top_n)
        top_count += len(leaders)
        scores = [row.get("ranking_score") for row in leaders if row.get("ranking_score") is not None]
        if len(scores) >= 2 and len(set(round(float(value), 2) for value in scores)) < len(scores):
            tie_days += 1
        for row in leaders:
            for name in CONTINUOUS_SCORE_FIELDS:
                value = _finite(row.get(name))
                if value is None:
                    continue
                field_seen[name] += 1
                if name == "rsi14":
                    if value >= 78 or value <= 35:
                        field_hits[name] += 1
                elif value >= NEAR_CAP:
                    field_hits[name] += 1
    return {
        "top_n": top_n,
        "day_count": len(days),
        "leader_rows": top_count,
        "near_cap_share": {
            name: None if not field_seen[name] else field_hits[name] / field_seen[name]
            for name in CONTINUOUS_SCORE_FIELDS
        },
        "days_with_top_score_ties": tie_days,
        "note": (
            "Fixed 0-100 scales can saturate without proving they caused the "
            "Top-K result. RSI hits count washed-out or extended knots, not cap=100."
        ),
    }


def concentration_report(rows: Iterable[Mapping[str, Any]], *, top_k: int = 10) -> dict[str, Any]:
    days = group_by_date(rows)
    max_share: list[float] = []
    unique_sectors: list[int] = []
    unclassified_days = 0
    for items in days.values():
        leaders = _top_n(items, top_k)
        buckets = [_sector_bucket(row) for row in leaders]
        counts = Counter(buckets)
        if not leaders:
            continue
        max_share.append(max(counts.values()) / len(leaders))
        unique_sectors.append(len(counts))
        if any(bucket == "__unclassified__" for bucket in buckets):
            unclassified_days += 1
    return {
        "top_k": top_k,
        "day_count": len(days),
        "mean_max_sector_share": None if not max_share else fmean(max_share),
        "mean_unique_sectors": None if not unique_sectors else fmean(unique_sectors),
        "days_max_share_ge_0_4": sum(1 for value in max_share if value >= 0.4),
        "unclassified_days": unclassified_days,
        "sector_map": "static current theme first-listing",
    }


def tail_failures(rows: Iterable[Mapping[str, Any]], *, top_k: int = 10, threshold: float = -0.08) -> dict[str, Any]:
    days = group_by_date(rows)
    examples: list[dict[str, Any]] = []
    for session, items in days.items():
        for row in _top_n(items, top_k):
            raw = close_raw(row)
            if raw is None or raw >= threshold:
                continue
            examples.append(
                {
                    "signal_date": session,
                    "ticker": row.get("ticker"),
                    "ranking_score": row.get("ranking_score"),
                    "score_mid": row.get("score_mid"),
                    "score_long": row.get("score_long"),
                    "return_63d": row.get("return_63d"),
                    "primary_sector_id": row.get("primary_sector_id"),
                    "raw_20d": raw,
                    "excess_20d": close_excess(row),
                }
            )
    examples.sort(key=lambda item: item["raw_20d"] or 0.0)
    return {
        "threshold": threshold,
        "count": len(examples),
        "worst": examples[:25],
        "horizon": PRIMARY_HORIZON,
    }


def radar_repeat_report(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_ticker_pivot: dict[tuple[str, str], int] = Counter()
    by_ticker_resistance: dict[tuple[str, str], int] = Counter()
    for event in events:
        ticker = str(event.get("ticker") or "")
        pivot = str(event.get("pivot_id") or "")
        resistance = event.get("resistance_high")
        by_ticker_pivot[(ticker, pivot)] += 1
        if resistance is not None:
            by_ticker_resistance[(ticker, f"{float(resistance):.2f}")] += 1
    return {
        "events": len(events),
        "unique_ticker_pivot": len(by_ticker_pivot),
        "duplicate_ticker_pivot": sum(1 for count in by_ticker_pivot.values() if count > 1),
        "unique_ticker_resistance": len(by_ticker_resistance),
        "duplicate_ticker_resistance": sum(
            1 for count in by_ticker_resistance.values() if count > 1
        ),
    }
