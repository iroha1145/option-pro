"""Deterministic daily-bar base (pivot-cluster) detection for the detail page.

Same algorithm family as breakouts/base_detector.py, repackaged as a pure
dict-in/dict-out module so the stock-detail endpoint can serve a chart-ready
structure without the radar's cutoff/settings machinery. Constants (window
grid, tolerance, touch counts, quality weights) mirror the radar detector;
participation is measured in dollar volume (close × volume) because raw share
counts are not comparable across price levels.

No lookahead: callers pass bars **excluding the evaluation day**, so the
current bar never participates in its own resistance band.
"""

from __future__ import annotations

import hashlib
import json
from statistics import mean, median, pstdev
from typing import Any, Sequence

DETECTOR_VERSION = "us-base-structure-v1"

WINDOW_GRID = (10, 15, 20, 30, 40, 60, 80)
BASE_MIN_DAYS = 10
BASE_MAX_DAYS = 80
PIVOT_TOLERANCE_ATR = 0.50
BREAK_BUFFER_PCT = 0.0025
BREAK_BUFFER_ATR = 0.10


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], window: int) -> float | None:
    n = len(closes)
    if n < 2 or window < 1:
        return None
    window = min(window, n - 1)
    trs = []
    for i in range(n - window, n):
        tr = max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def _pivots(values: Sequence[float], *, high: bool, span: int = 2) -> list[tuple[int, float]]:
    found: list[tuple[int, float]] = []
    for index in range(span, len(values) - span):
        left = values[index - span:index]
        right = values[index + 1:index + span + 1]
        value = values[index]
        if high and value > max(left) and value >= max(right):
            found.append((index, float(value)))
        if not high and value < min(left) and value <= min(right):
            found.append((index, float(value)))
    return found


def _best_cluster(
    pivots: list[tuple[int, float]], tolerance: float, *, prefer_high: bool
) -> list[tuple[int, float]]:
    candidates: list[list[tuple[int, float]]] = []
    for pivot in pivots:
        cluster = [item for item in pivots if abs(item[1] - pivot[1]) <= tolerance]
        unique = list(dict.fromkeys(cluster))
        if len(unique) >= 2:
            candidates.append(unique)
    if not candidates:
        return []
    return max(
        candidates,
        key=lambda items: (
            len(items),
            -(pstdev([item[1] for item in items]) if len(items) > 1 else 0.0),
            max(item[0] for item in items),
            (1 if prefer_high else -1) * mean(item[1] for item in items),
        ),
    )


def _candidate(window: dict[str, list], dates: Sequence[str]) -> dict[str, Any] | None:
    closes = window["closes"]
    highs = window["highs"]
    lows = window["lows"]
    turnover = window["turnover"]
    size = len(closes)
    if size < BASE_MIN_DAYS:
        return None
    atr = _atr(highs, lows, closes, min(20, max(5, size // 2)))
    price = closes[-1]
    if atr is None or atr <= 0 or price <= 0:
        return None
    tolerance = max(price * 0.01, atr * PIVOT_TOLERANCE_ATR)
    high_pivots = _pivots(highs, high=True)
    low_pivots = _pivots(lows, high=False)
    resistance_cluster = _best_cluster(high_pivots, tolerance, prefer_high=True)
    if len(resistance_cluster) < 2:
        return None
    resistance_prices = [item[1] for item in resistance_cluster]
    resistance_mid = mean(resistance_prices)
    resistance_low = min(resistance_prices) - tolerance * 0.25
    resistance_high = max(resistance_prices) + tolerance * 0.25
    break_buffer = max(price * BREAK_BUFFER_PCT, atr * BREAK_BUFFER_ATR)
    if closes[-1] > resistance_high + break_buffer:
        return None  # a window that already broke out is not a completed base

    support_cluster = _best_cluster(low_pivots, tolerance, prefer_high=False)
    if support_cluster:
        support_prices = [item[1] for item in support_cluster]
    else:
        support_prices = sorted(lows)[: min(3, size)]
    support_low = min(support_prices) - tolerance * 0.25
    support_high = max(support_prices) + tolerance * 0.25

    width = resistance_high - support_low
    width_atr = width / atr
    split = max(3, size // 2)
    early_atr = _atr(highs[:split], lows[:split], closes[:split], min(10, max(3, split - 1)))
    late_atr = _atr(highs[split:], lows[split:], closes[split:], min(10, max(3, size - split - 1)))
    atr_contraction = 1.0 - late_atr / early_atr if early_atr and late_atr is not None else None
    turnover_values_early = [value for value in turnover[:split] if value is not None and value > 0]
    turnover_values_late = [value for value in turnover[split:] if value is not None and value > 0]
    turnover_contraction = None
    if turnover_values_early and turnover_values_late:
        early_median = median(turnover_values_early)
        if early_median > 0:
            turnover_contraction = 1.0 - median(turnover_values_late) / early_median

    touch_quality = _clamp01(len(resistance_cluster) / 4.0)
    tightness = _clamp01(1.0 - width_atr / 12.0)
    duration = _clamp01((size - BASE_MIN_DAYS) / 40.0 + 0.4)
    atr_quality = _clamp01(((atr_contraction if atr_contraction is not None else 0.0) + 0.2) / 0.6)
    turnover_quality = _clamp01(((turnover_contraction if turnover_contraction is not None else 0.0) + 0.2) / 0.6)
    support_quality = _clamp01(len(support_cluster) / 3.0) if support_cluster else 0.35
    higher_low = (
        _clamp01(0.5 + (low_pivots[-1][1] - low_pivots[-2][1]) / atr * 0.2)
        if len(low_pivots) >= 2
        else 0.5
    )
    quality = (
        tightness * 0.25 + duration * 0.15 + touch_quality * 0.15
        + turnover_quality * 0.15 + atr_quality * 0.10
        + support_quality * 0.10 + higher_low * 0.10
    )
    pivot_source = {
        "base_start": dates[0], "base_end": dates[-1],
        "resistance_low": round(resistance_low, 6),
        "resistance_high": round(resistance_high, 6),
        "version": DETECTOR_VERSION,
    }
    pivot_id = hashlib.sha256(json.dumps(pivot_source, sort_keys=True).encode()).hexdigest()[:24]
    last_touch_index = max(item[0] for item in resistance_cluster)
    return {
        "detector_version": DETECTOR_VERSION,
        "pivot_id": pivot_id,
        "base_start": dates[0],
        "base_end": dates[-1],
        "base_duration_days": size,
        "pivot_price": round(resistance_mid, 4),
        "resistance_low": round(resistance_low, 4),
        "resistance_high": round(resistance_high, 4),
        "resistance_touches": len(resistance_cluster),
        "resistance_last_touch_age": size - 1 - last_touch_index,
        "support_low": round(support_low, 4),
        "support_high": round(support_high, 4),
        "support_touches": len(support_cluster) if support_cluster else len(support_prices),
        "invalidation_price": round(support_low - atr * 0.10, 4),
        "base_width_atr": round(width_atr, 4),
        "atr_contraction": round(atr_contraction, 4) if atr_contraction is not None else None,
        "turnover_contraction": round(turnover_contraction, 4) if turnover_contraction is not None else None,
        "quality": round(_clamp01(quality), 4),
        "metrics": {
            "tightness_quality": round(tightness * 100, 2),
            "duration_quality": round(duration * 100, 2),
            "resistance_touch_quality": round(touch_quality * 100, 2),
            "turnover_contraction_quality": round(turnover_quality * 100, 2),
            "atr_contraction_quality": round(atr_quality * 100, 2),
            "support_integrity": round(support_quality * 100, 2),
            "higher_low_quality": round(higher_low * 100, 2),
        },
        "break_buffer": round(break_buffer, 4),
    }


def detect_base_structure(series: dict[str, list]) -> dict[str, Any] | None:
    """``series`` is structure.clean_series shape, **excluding** the last bar.

    Scans the window grid and keeps the highest-quality completed base, or
    ``None`` when no window qualifies.
    """

    closes = series.get("closes") or []
    if len(closes) < BASE_MIN_DAYS:
        return None
    candidates: list[dict[str, Any]] = []
    for size in WINDOW_GRID:
        if size < BASE_MIN_DAYS or size > BASE_MAX_DAYS or len(closes) < size:
            continue
        window = {key: list(series[key][-size:]) for key in ("closes", "highs", "lows", "turnover")}
        dates = list(series["dates"][-size:])
        candidate = _candidate(window, dates)
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item["quality"],
            item["resistance_touches"],
            item["base_duration_days"],
        ),
    )


__all__ = [
    "BASE_MIN_DAYS",
    "BREAK_BUFFER_ATR",
    "BREAK_BUFFER_PCT",
    "DETECTOR_VERSION",
    "WINDOW_GRID",
    "detect_base_structure",
]
