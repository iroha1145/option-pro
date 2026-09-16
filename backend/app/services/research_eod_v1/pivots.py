"""Confirmed fractal pivots with explicit pivot_at / confirmed_at."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np

from app.services.research_eod_v1.constants import SWING_SPAN


@dataclass(frozen=True)
class Pivot:
    index: int
    price: float
    kind: str
    pivot_at: date
    confirmed_at: date


def find_confirmed_pivots(
    high: np.ndarray,
    low: np.ndarray,
    dates: list[date],
    *,
    span: int = SWING_SPAN,
    as_of_index: int | None = None,
) -> tuple[list[Pivot], list[Pivot]]:
    """Pivots whose right-hand confirmation bar has already printed by as_of."""

    n = len(dates)
    last = n - 1 if as_of_index is None else as_of_index
    highs: list[Pivot] = []
    lows: list[Pivot] = []
    if last < 2 * span:
        return highs, lows
    for i in range(span, last - span + 1):
        confirmed_index = i + span
        if confirmed_index > last:
            continue
        left_high = high[i - span : i]
        right_high = high[i + 1 : i + span + 1]
        left_low = low[i - span : i]
        right_low = low[i + 1 : i + span + 1]
        if high[i] > np.max(left_high) and high[i] >= np.max(right_high):
            highs.append(
                Pivot(i, float(high[i]), "high", dates[i], dates[confirmed_index])
            )
        if low[i] < np.min(left_low) and low[i] <= np.min(right_low):
            lows.append(
                Pivot(i, float(low[i]), "low", dates[i], dates[confirmed_index])
            )
    return highs, lows


def structure_anchor(highs: list[Pivot], lows: list[Pivot]) -> tuple[float | None, str]:
    """Spec S anchors. Insufficient evidence is null, never a fake 50."""

    if len(highs) < 2 or len(lows) < 2:
        return None, "insufficient"
    hh = highs[-1].price > highs[-2].price
    hl = lows[-1].price > lows[-2].price
    lh = highs[-1].price < highs[-2].price
    ll = lows[-1].price < lows[-2].price
    if hh and hl:
        return 82.0, "HH+HL"
    if lh and ll:
        return 24.0, "LH+LL"
    if hh:
        return 66.0, "HH"
    if hl:
        return 62.0, "HL"
    if lh:
        return 38.0, "LH"
    return 50.0, "range"


def known_before(pivots: list[Pivot], session: date) -> list[Pivot]:
    return [pivot for pivot in pivots if pivot.confirmed_at < session]
