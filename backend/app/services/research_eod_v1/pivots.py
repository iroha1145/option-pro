"""Confirmed fractal pivots with explicit pivot_at / confirmed_at."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

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
    if last < 2 * span or span < 1:
        return highs, lows
    h = np.asarray(high[: last + 1], dtype=float)
    l = np.asarray(low[: last + 1], dtype=float)
    windows_h = sliding_window_view(h, span)
    windows_l = sliding_window_view(l, span)
    idxs = np.arange(span, last - span + 1)
    left_max_h = windows_h[idxs - span].max(axis=1)
    right_max_h = windows_h[idxs + 1].max(axis=1)
    left_min_l = windows_l[idxs - span].min(axis=1)
    right_min_l = windows_l[idxs + 1].min(axis=1)
    high_hits = (h[idxs] > left_max_h) & (h[idxs] >= right_max_h)
    low_hits = (l[idxs] < left_min_l) & (l[idxs] <= right_min_l)
    for i in idxs[high_hits]:
        i = int(i)
        highs.append(Pivot(i, float(h[i]), "high", dates[i], dates[i + span]))
    for i in idxs[low_hits]:
        i = int(i)
        lows.append(Pivot(i, float(l[i]), "low", dates[i], dates[i + span]))
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
