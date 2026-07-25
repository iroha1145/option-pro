"""Percentile scoring, module aggregation and the composite score.

Discipline enforced here:

* the percentile window is the trailing five years ending at the snapshot date —
  never a single day of data from after it;
* the empirical percentile uses a deterministic mid-rank, so ties are stable and
  two runs over the same inputs agree exactly;
* a missing factor is dropped and the remaining weights are renormalised. It is
  never replaced with 50, and a previous score never stands in for a current one;
* below its registered minimum history a factor scores ``None``;
* a module below its factor floor, and a composite below five valid modules,
  publish no official score at all.

v1 weights every factor inside a module equally and every valid module equally.
There is no correlation de-duplication and no learned weighting.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right, insort
from collections import deque
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Mapping, Optional, Sequence

from .models import FactorPoint, finite
from .registry import (
    COMPOSITE_MINIMUM_VALID_MODULES,
    FACTORS,
    FACTORS_BY_ID,
    FACTOR_IDS_BY_MODULE,
    MODULES,
    MODULES_BY_ID,
    regime_for,
)


CHANGE_LOOKBACK_DAYS = 7


def mid_rank_percentile(history: Sequence[float], value: float) -> Optional[float]:
    """``100 × (count(<x) + 0.5 × count(==x)) / count`` over a sorted window.

    ``history`` must be sorted ascending. The mid-rank convention keeps ties
    stable: identical values always receive an identical percentile.
    """

    total = len(history)
    if total == 0:
        return None
    below = bisect_left(history, value)
    equal = bisect_right(history, value) - below
    percentile = 100.0 * (below + 0.5 * equal) / total
    return finite(min(100.0, max(0.0, percentile)))


def rolling_percentiles(
    dates: Sequence[date],
    values: Sequence[Optional[float]],
    *,
    window_years: int,
    minimum_history: int,
) -> list[Optional[float]]:
    """Trailing-window percentile of each value within its own history.

    The current observation participates in its own window exactly once. Values
    outside the window and every ``None`` are excluded. Implemented with a sorted
    window plus a FIFO of pending expiries so the whole grid costs one pass.
    """

    if len(dates) != len(values):
        raise ValueError("percentile inputs must be the same length")
    window: list[float] = []
    pending: deque[tuple[date, float]] = deque()
    output: list[Optional[float]] = []
    for index, when in enumerate(dates):
        cutoff = _subtract_years(when, window_years)
        # Drop everything that has aged out of the trailing window.
        while pending and pending[0][0] <= cutoff:
            _expired_date, expired_value = pending.popleft()
            position = bisect_left(window, expired_value)
            if position < len(window) and window[position] == expired_value:
                window.pop(position)
        value = finite(values[index])
        if value is None:
            output.append(None)
            continue
        insort(window, value)
        pending.append((when, value))
        if len(window) < max(1, minimum_history):
            output.append(None)
            continue
        output.append(mid_rank_percentile(window, value))
    return output


def _subtract_years(when: date, years: int) -> date:
    try:
        return when.replace(year=when.year - years)
    except ValueError:
        # 29 February in a window that lands on a non-leap year.
        return when.replace(year=when.year - years, day=28)


def apply_direction(percentile: Optional[float], score_method: str) -> Optional[float]:
    if percentile is None:
        return None
    if score_method == "supportive_high_percentile":
        return percentile
    if score_method in {"supportive_low_percentile", "target_distance"}:
        return finite(100.0 - percentile)
    raise ValueError(f"unsupported percentile method {score_method}")


@dataclass(frozen=True, slots=True)
class ScoredFactor:
    factor_id: str
    module_id: str
    snapshot_date: date
    raw_value: Optional[float]
    signed_value: Optional[float]
    score: Optional[float]
    score_method: str
    status: str
    valid_observations: int
    confidence: Optional[float]
    data_through: Optional[date]
    available_at: Optional[str]
    history_basis: Optional[str]
    missing_inputs: tuple[str, ...]
    stale_inputs: tuple[str, ...]
    raw_change_7d: Optional[float] = None
    score_change_7d: Optional[float] = None


def score_factor_series(
    factor_id: str,
    points: Sequence[FactorPoint],
    *,
    window_years: int,
) -> list[ScoredFactor]:
    """Score one factor across the grid, then attach its seven-day changes."""

    spec = FACTORS_BY_ID[factor_id]
    dates = [point.snapshot_date for point in points]
    if spec.score_method == "direct_score":
        # The registry formula already produces a bounded 0–100 value; running a
        # percentile over it would score the score.
        scores: list[Optional[float]] = [
            None if point.score_value is None else finite(point.score_value)
            for point in points
        ]
        valid_counts = [1 if score is not None else 0 for score in scores]
    else:
        percentiles = rolling_percentiles(
            dates,
            [point.score_value for point in points],
            window_years=window_years,
            minimum_history=spec.minimum_history,
        )
        scores = [
            apply_direction(percentile, spec.score_method) for percentile in percentiles
        ]
        valid_counts = _window_counts(
            dates,
            [point.score_value for point in points],
            window_years=window_years,
        )

    scored: list[ScoredFactor] = []
    for index, point in enumerate(points):
        score = scores[index]
        if point.raw_value is None:
            status = point.status
        elif score is None:
            status = "insufficient_history"
        else:
            status = "ok"
        scored.append(
            ScoredFactor(
                factor_id=factor_id,
                module_id=spec.module_id,
                snapshot_date=point.snapshot_date,
                raw_value=point.raw_value,
                signed_value=point.signed_value,
                score=score,
                score_method=spec.score_method,
                status=status,
                valid_observations=valid_counts[index],
                confidence=None,
                data_through=point.data_through,
                available_at=point.available_at,
                history_basis=point.history_basis,
                missing_inputs=point.missing_inputs,
                stale_inputs=point.stale_inputs,
            )
        )

    raw_changes = seven_day_changes(dates, [item.raw_value for item in scored])
    score_changes = seven_day_changes(dates, [item.score for item in scored])
    return [
        ScoredFactor(
            factor_id=item.factor_id,
            module_id=item.module_id,
            snapshot_date=item.snapshot_date,
            raw_value=item.raw_value,
            signed_value=item.signed_value,
            score=item.score,
            score_method=item.score_method,
            status=item.status,
            valid_observations=item.valid_observations,
            confidence=item.confidence,
            data_through=item.data_through,
            available_at=item.available_at,
            history_basis=item.history_basis,
            missing_inputs=item.missing_inputs,
            stale_inputs=item.stale_inputs,
            raw_change_7d=raw_changes[index],
            score_change_7d=score_changes[index],
        )
        for index, item in enumerate(scored)
    ]


def _window_counts(
    dates: Sequence[date],
    values: Sequence[Optional[float]],
    *,
    window_years: int,
) -> list[int]:
    """How many valid observations each trailing window actually contained."""

    pending: deque[date] = deque()
    counts: list[int] = []
    for index, when in enumerate(dates):
        cutoff = _subtract_years(when, window_years)
        while pending and pending[0] <= cutoff:
            pending.popleft()
        if finite(values[index]) is not None:
            pending.append(when)
        counts.append(len(pending))
    return counts


def seven_day_changes(
    dates: Sequence[date],
    values: Sequence[Optional[float]],
) -> list[Optional[float]]:
    """Change against the newest valid value at least seven calendar days back.

    When no such earlier value exists the change is ``None``. Zero is never used
    to stand in for an unavailable comparison.
    """

    output: list[Optional[float]] = []
    valid_dates: list[date] = []
    valid_values: list[float] = []
    for index, when in enumerate(dates):
        current = finite(values[index])
        target = when - timedelta(days=CHANGE_LOOKBACK_DAYS)
        position = bisect_right(valid_dates, target) - 1
        if current is None or position < 0:
            output.append(None)
        else:
            output.append(finite(current - valid_values[position]))
        if current is not None:
            valid_dates.append(when)
            valid_values.append(current)
    return output


@dataclass(frozen=True, slots=True)
class ScoredModule:
    module_id: str
    snapshot_date: date
    score: Optional[float]
    score_change_7d: Optional[float]
    confidence: Optional[float]
    valid_factor_count: int
    total_factor_count: int
    data_through: Optional[date]
    available_at: Optional[str]
    status: str


def _exponential_moving_average(
    values: Sequence[Optional[float]],
    *,
    days: int,
) -> list[Optional[float]]:
    """Standard EMA with ``alpha = 2 / (days + 1)``.

    A date without a raw module score stays ``None`` and leaves the smoothing
    state untouched, so a gap is never filled with an invented value.
    """

    alpha = 2.0 / (days + 1)
    state: Optional[float] = None
    output: list[Optional[float]] = []
    for value in values:
        current = finite(value)
        if current is None:
            output.append(None)
            continue
        state = current if state is None else alpha * current + (1 - alpha) * state
        output.append(finite(state))
    return output


def aggregate_modules(
    grid: Sequence[date],
    scored_factors: Mapping[str, Sequence[ScoredFactor]],
) -> dict[str, list[ScoredModule]]:
    """Equal-weight the valid factors inside each module, then smooth Funding."""

    output: dict[str, list[ScoredModule]] = {}
    for module in MODULES:
        members = FACTOR_IDS_BY_MODULE[module.module_id]
        total = len(members)
        raw_scores: list[Optional[float]] = []
        rows: list[dict] = []
        for index in range(len(grid)):
            valid: list[float] = []
            through: list[date] = []
            available: list[str] = []
            for factor_id in members:
                item = scored_factors[factor_id][index]
                if item.score is None:
                    continue
                valid.append(item.score)
                if item.data_through is not None:
                    through.append(item.data_through)
                if item.available_at:
                    available.append(item.available_at)
            if len(valid) < module.minimum_valid_factors:
                raw_scores.append(None)
                rows.append(
                    {
                        "valid": len(valid),
                        "through": min(through) if through else None,
                        "available": max(available) if available else None,
                    }
                )
                continue
            raw_scores.append(finite(math.fsum(valid) / len(valid)))
            rows.append(
                {
                    "valid": len(valid),
                    "through": min(through) if through else None,
                    "available": max(available) if available else None,
                }
            )
        smoothed = (
            _exponential_moving_average(raw_scores, days=module.ema_days)
            if module.ema_days
            else list(raw_scores)
        )
        changes = seven_day_changes(grid, smoothed)
        output[module.module_id] = [
            ScoredModule(
                module_id=module.module_id,
                snapshot_date=grid[index],
                score=smoothed[index],
                score_change_7d=changes[index],
                confidence=(
                    finite(rows[index]["valid"] / total) if total else None
                ),
                valid_factor_count=int(rows[index]["valid"]),
                total_factor_count=total,
                data_through=rows[index]["through"],
                available_at=rows[index]["available"],
                status="ok" if smoothed[index] is not None else "insufficient_factors",
            )
            for index in range(len(grid))
        ]
    return output


@dataclass(frozen=True, slots=True)
class ScoredComposite:
    snapshot_date: date
    score: Optional[float]
    score_change_7d: Optional[float]
    confidence: Optional[float]
    regime: Optional[str]
    valid_module_count: int
    data_through: Optional[date]
    available_at: Optional[str]
    status: str


def aggregate_composite(
    grid: Sequence[date],
    scored_modules: Mapping[str, Sequence[ScoredModule]],
) -> list[ScoredComposite]:
    """Equal-weight the valid modules; publish nothing below five of seven."""

    total_modules = len(MODULES)
    raw: list[Optional[float]] = []
    rows: list[dict] = []
    for index in range(len(grid)):
        valid_scores: list[float] = []
        coverage: list[float] = []
        through: list[date] = []
        available: list[str] = []
        for module in MODULES:
            item = scored_modules[module.module_id][index]
            if item.score is None:
                continue
            valid_scores.append(item.score)
            if item.confidence is not None:
                coverage.append(item.confidence)
            if item.data_through is not None:
                through.append(item.data_through)
            if item.available_at:
                available.append(item.available_at)
        valid_count = len(valid_scores)
        if valid_count < COMPOSITE_MINIMUM_VALID_MODULES:
            raw.append(None)
            rows.append(
                {
                    "valid": valid_count,
                    "confidence": None,
                    "through": min(through) if through else None,
                    "available": max(available) if available else None,
                }
            )
            continue
        # Confidence = share of modules that published × the mean in-module
        # factor coverage among those modules.
        module_weight = valid_count / total_modules
        mean_coverage = math.fsum(coverage) / len(coverage) if coverage else 0.0
        raw.append(finite(math.fsum(valid_scores) / valid_count))
        rows.append(
            {
                "valid": valid_count,
                "confidence": finite(module_weight * mean_coverage),
                "through": min(through) if through else None,
                "available": max(available) if available else None,
            }
        )
    changes = seven_day_changes(grid, raw)
    return [
        ScoredComposite(
            snapshot_date=grid[index],
            score=raw[index],
            score_change_7d=changes[index],
            confidence=rows[index]["confidence"],
            regime=regime_for(raw[index]),
            valid_module_count=int(rows[index]["valid"]),
            data_through=rows[index]["through"],
            available_at=rows[index]["available"],
            status=(
                "ok" if raw[index] is not None else "insufficient_modules"
            ),
        )
        for index in range(len(grid))
    ]


def factor_confidence(point: FactorPoint, *, required_inputs: int) -> Optional[float]:
    """Share of a factor's declared inputs that were present and fresh."""

    if required_inputs <= 0:
        return None
    unavailable = len(set(point.missing_inputs) | set(point.stale_inputs))
    return finite(max(0.0, (required_inputs - unavailable) / required_inputs))


__all__ = [
    "CHANGE_LOOKBACK_DAYS",
    "ScoredComposite",
    "ScoredFactor",
    "ScoredModule",
    "aggregate_composite",
    "aggregate_modules",
    "apply_direction",
    "factor_confidence",
    "mid_rank_percentile",
    "rolling_percentiles",
    "score_factor_series",
    "seven_day_changes",
]
