"""Ranking and event metrics. These consume labels, never feed scoring."""

from __future__ import annotations

import math
from statistics import fmean
from typing import Any, Iterable, Mapping, Sequence


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def spearman_rank_ic(
    scores: Sequence[Any],
    outcomes: Sequence[Any],
) -> dict[str, Any]:
    pairs = [
        (float(score), float(outcome))
        for score, outcome in zip(scores, outcomes)
        if _finite(score) is not None and _finite(outcome) is not None
    ]
    n = len(pairs)
    if n < 5:
        return {"status": "unavailable", "reason": "insufficient_pairs", "n": n, "ic": None}
    ordered_scores = sorted(score for score, _outcome in pairs)
    ordered_outcomes = sorted(outcome for _score, outcome in pairs)

    def ranks(values: list[float], ordered: list[float]) -> list[float]:
        result = []
        for value in values:
            lo = ordered.index(value)
            hi = len(ordered) - list(reversed(ordered)).index(value) - 1
            result.append((lo + hi) / 2.0)
        return result

    score_ranks = ranks([score for score, _outcome in pairs], ordered_scores)
    outcome_ranks = ranks([outcome for _score, outcome in pairs], ordered_outcomes)
    mean_s = fmean(score_ranks)
    mean_o = fmean(outcome_ranks)
    num = sum((s - mean_s) * (o - mean_o) for s, o in zip(score_ranks, outcome_ranks))
    den_s = math.sqrt(sum((s - mean_s) ** 2 for s in score_ranks))
    den_o = math.sqrt(sum((o - mean_o) ** 2 for o in outcome_ranks))
    if den_s == 0 or den_o == 0:
        return {"status": "unavailable", "reason": "constant_series", "n": n, "ic": None}
    return {"status": "active", "reason": None, "n": n, "ic": num / (den_s * den_o)}


def top_k_mean(
    rows: Iterable[Mapping[str, Any]],
    *,
    score_key: str,
    outcome_key: tuple[str, ...],
    k: int,
) -> dict[str, Any]:
    usable = []
    for row in rows:
        score = _finite(row.get(score_key))
        cursor: Any = row
        for part in outcome_key:
            cursor = cursor.get(part) if isinstance(cursor, Mapping) else None
        outcome = _finite(cursor)
        if score is None or outcome is None:
            continue
        usable.append((score, outcome, str(row.get("ticker") or "")))
    usable.sort(key=lambda item: (item[0], item[2]), reverse=True)
    selected = usable[:k]
    all_outcomes = [item[1] for item in usable]
    top_outcomes = [item[1] for item in selected]
    if not selected or not all_outcomes:
        return {
            "status": "unavailable",
            "k": k,
            "n_top": 0,
            "n_universe": len(usable),
            "top_mean": None,
            "universe_mean": None,
            "excess": None,
        }
    top_mean = fmean(top_outcomes)
    universe_mean = fmean(all_outcomes)
    return {
        "status": "active",
        "k": k,
        "n_top": len(selected),
        "n_universe": len(usable),
        "top_mean": top_mean,
        "universe_mean": universe_mean,
        "excess": top_mean - universe_mean,
        "hit_rate": sum(1 for value in top_outcomes if value > 0) / len(top_outcomes),
        "beat_universe_rate": sum(1 for value in top_outcomes if value > universe_mean)
        / len(top_outcomes),
    }


def summarize_daily_ics(daily: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    values = [_finite(row.get("ic")) for row in daily if row.get("status") == "active"]
    values = [value for value in values if value is not None]
    if not values:
        return {
            "status": "unavailable",
            "days": 0,
            "mean_ic": None,
            "icir": None,
            "positive_share": None,
        }
    mean = fmean(values)
    variance = fmean((value - mean) ** 2 for value in values)
    stdev = math.sqrt(variance)
    return {
        "status": "active",
        "days": len(values),
        "mean_ic": mean,
        "icir": None if stdev == 0 else mean / stdev,
        "positive_share": sum(1 for value in values if value > 0) / len(values),
        "min_ic": min(values),
        "max_ic": max(values),
    }


def date_clustered_mean(values: Sequence[tuple[str, float]]) -> dict[str, Any]:
    """Treat each date as one cluster. Do not pretend events are IID."""

    by_date: dict[str, list[float]] = {}
    for session, value in values:
        if _finite(value) is None:
            continue
        by_date.setdefault(session, []).append(float(value))
    day_means = [fmean(items) for items in by_date.values() if items]
    if len(day_means) < 2:
        return {
            "status": "unavailable",
            "clusters": len(day_means),
            "mean": day_means[0] if day_means else None,
            "se": None,
        }
    mean = fmean(day_means)
    variance = fmean((item - mean) ** 2 for item in day_means)
    se = math.sqrt(variance / len(day_means))
    return {
        "status": "active",
        "clusters": len(day_means),
        "n_events": sum(len(items) for items in by_date.values()),
        "mean": mean,
        "se": se,
        "ci95": [mean - 1.96 * se, mean + 1.96 * se],
    }
