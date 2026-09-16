from __future__ import annotations

import math
from typing import Any, Sequence

import numpy as np


def finite(value: Any, *, name: str = "value") -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def clip100(value: float | None) -> float | None:
    number = finite(value)
    if number is None:
        return None
    return min(100.0, max(0.0, number))


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    prev = np.roll(close, 1)
    prev[0] = close[0]
    return np.maximum.reduce([
        high - low,
        np.abs(high - prev),
        np.abs(low - prev),
    ])


def sma_at(values: np.ndarray, index: int, window: int) -> float | None:
    if window <= 0 or index < window - 1 or index >= len(values):
        return None
    sl = values[index - window + 1 : index + 1]
    if sl.size < window or not np.isfinite(sl).all():
        return None
    return float(sl.mean())


def atr_sma_at(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    index: int,
    window: int = 14,
) -> float | None:
    if index < window or index >= len(close):
        return None
    tr = true_range(high[: index + 1], low[: index + 1], close[: index + 1])
    sl = tr[index - window + 1 : index + 1]
    if sl.size < window or not np.isfinite(sl).all():
        return None
    return float(sl.mean())


def clv_at(high: np.ndarray, low: np.ndarray, close: np.ndarray, index: int) -> float | None:
    if index < 0 or index >= len(close):
        return None
    span = high[index] - low[index]
    if not np.isfinite(span) or span == 0:
        return None
    return float((close[index] - low[index]) / span)


def midrank_percentiles(values: Sequence[float]) -> list[float | None]:
    """0-100 midrank on the supplied finite observations. One row is not a rank."""

    finite_vals = [finite(v) for v in values]
    usable = sorted(v for v in finite_vals if v is not None)
    if len(usable) < 2:
        return [None for _ in values]
    denom = len(usable) - 1
    ranks: list[float | None] = []
    for value in finite_vals:
        if value is None:
            ranks.append(None)
            continue
        below = sum(1 for item in usable if item < value)
        tied = sum(1 for item in usable if item == value)
        mid = below + (tied - 1) / 2
        ranks.append(round(100.0 * mid / denom, 10))
    return ranks


def shrink_q(q_industry: float | None, q_parent: float | None, n_industry: int) -> float | None:
    if q_parent is None:
        return None
    if n_industry < 3 or q_industry is None:
        return q_parent
    lam = n_industry / (n_industry + 30.0)
    return lam * q_industry + (1.0 - lam) * q_parent


def max_drawdown_magnitude(tri: np.ndarray) -> float | None:
    """Non-negative peak-to-trough decline. Negative library values are flipped."""

    if tri.size < 2 or not np.isfinite(tri).all() or np.any(tri <= 0):
        return None
    peak = np.maximum.accumulate(tri)
    dd = (peak - tri) / peak
    return float(np.max(dd))


def ordinary_least_squares(y: np.ndarray, x: np.ndarray) -> np.ndarray | None:
    """y = Xb with a leading intercept column already in x, or added here."""

    if y.size < 3 or x.size == 0:
        return None
    if x.ndim == 1:
        x = x.reshape(-1, 1)
    design = np.column_stack([np.ones(len(y)), x])
    if not np.isfinite(design).all() or not np.isfinite(y).all():
        return None
    try:
        beta, _residuals, rank, _s = np.linalg.lstsq(design, y, rcond=None)
    except np.linalg.LinAlgError:
        return None
    if rank < design.shape[1]:
        return None
    return beta
