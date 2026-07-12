"""Deterministic daily base and pivot detection."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import numpy as np
import pandas as pd

from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.feature_engine import compute_atr, trim_daily_bars
from app.services.breakouts.models import (
    BreakoutStructure,
    PriceZone,
    TemporalCutoff,
    normalize_ticker,
)


WINDOW_GRID = (10, 15, 20, 30, 40, 60, 80)


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _pivots(values: np.ndarray, *, high: bool, span: int = 2) -> list[tuple[int, float]]:
    found: list[tuple[int, float]] = []
    for index in range(span, len(values) - span):
        left = values[index - span:index]
        right = values[index + 1:index + span + 1]
        value = values[index]
        if high and value > left.max() and value >= right.max():
            found.append((index, float(value)))
        if not high and value < left.min() and value <= right.min():
            found.append((index, float(value)))
    return found


def _best_cluster(
    pivots: list[tuple[int, float]],
    tolerance: float,
    *,
    prefer_high: bool,
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
            -float(np.std([item[1] for item in items])),
            max(item[0] for item in items),
            (1 if prefer_high else -1) * float(np.mean([item[1] for item in items])),
        ),
    )


def _candidate(
    ticker: str,
    window: pd.DataFrame,
    *,
    settings: BreakoutSettings,
) -> BreakoutStructure | None:
    if len(window) < settings.base_min_days:
        return None
    atr = compute_atr(window, min(20, max(5, len(window) // 2)))
    price = float(window["Close"].iloc[-1])
    if atr is None or atr <= 0 or price <= 0:
        return None
    tolerance = max(price * 0.01, atr * settings.pivot_tolerance_atr)
    highs = _pivots(window["High"].to_numpy(dtype=float), high=True)
    lows = _pivots(window["Low"].to_numpy(dtype=float), high=False)
    resistance_cluster = _best_cluster(highs, tolerance, prefer_high=True)
    if len(resistance_cluster) < 2:
        return None
    resistance_prices = [item[1] for item in resistance_cluster]
    resistance_mid = float(np.mean(resistance_prices))
    resistance_low = min(resistance_prices) - tolerance * 0.25
    resistance_high = max(resistance_prices) + tolerance * 0.25
    if float(window["Close"].iloc[-1]) > resistance_high + max(
        price * settings.break_buffer_pct,
        atr * settings.break_buffer_atr,
    ):
        return None

    support_cluster = _best_cluster(lows, tolerance, prefer_high=False)
    if support_cluster:
        support_prices = [item[1] for item in support_cluster]
    else:
        support_prices = list(window["Low"].nsmallest(min(3, len(window))).astype(float))
    support_mid = float(np.mean(support_prices))
    support_low = min(support_prices) - tolerance * 0.25
    support_high = max(support_prices) + tolerance * 0.25

    width = resistance_high - support_low
    width_pct = width / price * 100.0
    width_atr = width / atr
    split = max(3, len(window) // 2)
    early_atr = compute_atr(window.iloc[:split], min(10, max(3, split - 1)))
    late_atr = compute_atr(window.iloc[split:], min(10, max(3, len(window) - split - 1)))
    atr_contraction = (
        1.0 - late_atr / early_atr
        if early_atr and late_atr is not None
        else None
    )
    volume_contraction = None
    if "Volume" in window.columns:
        early_volume = pd.to_numeric(window["Volume"].iloc[:split], errors="coerce").median()
        late_volume = pd.to_numeric(window["Volume"].iloc[split:], errors="coerce").median()
        if math.isfinite(float(early_volume)) and early_volume > 0 and math.isfinite(float(late_volume)):
            volume_contraction = 1.0 - float(late_volume / early_volume)
    touch_quality = _clamp(len(resistance_cluster) / 4.0)
    tightness = _clamp(1.0 - width_atr / 12.0)
    duration = _clamp((len(window) - settings.base_min_days) / 40.0 + 0.4)
    atr_quality = _clamp(
        (((atr_contraction if atr_contraction is not None else 0.0) + 0.2) / 0.6)
    )
    volume_quality = _clamp(
        (
            (
                volume_contraction
                if volume_contraction is not None
                else 0.0
            )
            + 0.2
        )
        / 0.6
    )
    support_quality = _clamp(len(support_cluster) / 3.0) if support_cluster else 0.35
    higher_low = (
        _clamp(0.5 + (lows[-1][1] - lows[-2][1]) / atr * 0.2)
        if len(lows) >= 2
        else 0.5
    )
    quality = (
        tightness * 0.25
        + duration * 0.15
        + touch_quality * 0.15
        + volume_quality * 0.15
        + atr_quality * 0.10
        + support_quality * 0.10
        + higher_low * 0.10
    )
    start = pd.Timestamp(window.index[0]).date()
    end = pd.Timestamp(window.index[-1]).date()
    pivot_source = {
        "ticker": ticker,
        "base_start": start.isoformat(),
        "base_end": end.isoformat(),
        "resistance_low": round(resistance_low, 6),
        "resistance_high": round(resistance_high, 6),
        "version": settings.detector_version,
    }
    pivot_id = hashlib.sha256(
        json.dumps(pivot_source, sort_keys=True).encode()
    ).hexdigest()[:24]
    last_touch_index = max(item[0] for item in resistance_cluster)
    last_touch = pd.Timestamp(window.index[last_touch_index])
    if last_touch.tzinfo is None:
        last_touch = last_touch.tz_localize("UTC")
    return BreakoutStructure(
        ticker=ticker,
        base_start=start,
        base_end=end,
        calculation_cutoff_at=pd.Timestamp(window.index[-1]).tz_localize("UTC")
        if pd.Timestamp(window.index[-1]).tzinfo is None
        else pd.Timestamp(window.index[-1]),
        base_duration_days=len(window),
        support_zone=PriceZone(
            low=round(support_low, 6),
            high=round(support_high, 6),
            strength=round(support_quality, 6),
            touches=len(support_cluster) if support_cluster else len(support_prices),
            age_days=len(window) - 1 - max((item[0] for item in support_cluster), default=0),
        ),
        resistance_zone=PriceZone(
            low=round(resistance_low, 6),
            high=round(resistance_high, 6),
            strength=round(touch_quality, 6),
            touches=len(resistance_cluster),
            age_days=len(window) - 1 - last_touch_index,
        ),
        pivot_price=round(resistance_mid, 6),
        pivot_id=pivot_id,
        pivot_touch_count=len(resistance_cluster),
        pivot_last_touch_at=last_touch,
        base_width_pct=round(width_pct, 6),
        base_width_atr=round(width_atr, 6),
        atr_contraction=round(atr_contraction, 6) if atr_contraction is not None else None,
        volume_contraction=round(volume_contraction, 6)
        if volume_contraction is not None
        else None,
        relative_strength_slope=None,
        invalidation_price=round(support_low - atr * 0.10, 6),
        quality=round(_clamp(quality), 6),
        status="active",
        metrics={
            "tightness_quality": round(tightness * 100, 4),
            "duration_quality": round(duration * 100, 4),
            "resistance_touch_quality": round(touch_quality * 100, 4),
            "volume_contraction_quality": round(volume_quality * 100, 4),
            "atr_contraction_quality": round(atr_quality * 100, 4),
            "support_integrity": round(support_quality * 100, 4),
            "higher_low_quality": round(higher_low * 100, 4),
            "resistance_dispersion_atr": round(
                float(np.std(resistance_prices)) / atr, 6
            ),
        },
    )


def detect_base(
    ticker: str,
    daily: pd.DataFrame,
    cutoff: TemporalCutoff,
    settings: BreakoutSettings | None = None,
) -> BreakoutStructure | None:
    config = settings or get_breakout_settings()
    symbol = normalize_ticker(ticker)
    visible = trim_daily_bars(daily, cutoff)
    if len(visible) < config.base_min_days:
        return None
    candidates: list[BreakoutStructure] = []
    for size in WINDOW_GRID:
        if size < config.base_min_days or size > config.base_max_days or len(visible) < size:
            continue
        structure = _candidate(symbol, visible.tail(size), settings=config)
        if structure is not None:
            candidates.append(structure)
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            item.quality,
            item.pivot_touch_count,
            -float(
                item.metrics["resistance_dispersion_atr"]
                if item.metrics.get("resistance_dispersion_atr") is not None
                else 999
            ),
            item.base_duration_days,
            -item.base_start.toordinal(),
        ),
    )
