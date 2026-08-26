"""Classic per-stock technical indicators for the detail page.

Pure-Python, dependency-free implementations shared with the JP sister
project (JP-option-pro radar/technicals.py — itself derived from this
repo's strength scanner/scoring math). Missing inputs stay ``None``; the
functions never impute.
"""

from __future__ import annotations

import math
from typing import Any, Sequence

TECHNICALS_VERSION = "us-technicals-v2"


def _safe(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, ndigits)


def rsi14(closes: Sequence[float], period: int = 14) -> float | None:
    """Wilder RSI over the full series (seed SMA then recursive smoothing)."""

    if len(closes) < period + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    for i in range(period + 1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gain = max(delta, 0.0)
        loss = max(-delta, 0.0)
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period
    if avg_loss <= 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def rsi_score(value: float | None) -> float | None:
    """RSI → 0-100 的评分映射。

    节点必须与 strength/scoring.rsi_score **完全一致**（那是正式评分用的
    唯一口径）：本文件为 JP 姊妹仓可携带而保持零依赖，所以是复制而不是
    import——一致性由 tests 里的等价测试钉死，改任何一边都会被测试打回。
    """

    if value is None:
        return None
    value = max(0.0, min(100.0, value))
    knots = (
        (0.0, 0.0),
        (35.0, 42.0),
        (50.0, 58.0),
        (68.0, 88.0),
        (78.0, 66.0),
        (100.0, 33.0),
    )
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
        if value <= x1:
            if x1 == x0:
                return y0
            ratio = (value - x0) / (x1 - x0)
            return y0 + (y1 - y0) * ratio
    return 33.0


def _ema_series(values: Sequence[float], period: int) -> list[float]:
    alpha = 2.0 / (period + 1.0)
    result: list[float] = []
    ema = values[0]
    for value in values:
        ema = value * alpha + ema * (1.0 - alpha)
        result.append(ema)
    return result


def macd_direction(closes: Sequence[float]) -> dict[str, float | None]:
    """MACD(12/26/9) histogram and its 3-bar change as a % of last close."""

    if len(closes) < 40:
        return {"histogram": None, "direction_pct": None}
    fast = _ema_series(closes, 12)
    slow = _ema_series(closes, 26)
    macd_line = [f - s for f, s in zip(fast, slow)]
    signal = _ema_series(macd_line, 9)
    histogram = [m - s for m, s in zip(macd_line, signal)]
    last_close = closes[-1]
    if last_close <= 0 or len(histogram) < 4:
        return {"histogram": _safe(histogram[-1]), "histogram_pct": None, "direction_pct": None}
    delta = histogram[-1] - histogram[-4]
    return {
        "histogram": _safe(histogram[-1]),
        # 柱体自身位置（零轴上/下）与柱体变化是两回事：柱体在零轴下方时
        # 「变化为正」只是空头动能衰减，不是多头增强——两个读数都下发，
        # 前端才能把话说对。都按收盘价折算成 %，跨价位可比。
        "histogram_pct": _safe(histogram[-1] / last_close * 100.0),
        "direction_pct": _safe(delta / last_close * 100.0),
    }


def trend_efficiency(closes: Sequence[float], window: int = 63) -> float | None:
    """Kaufman efficiency ratio: |net move| / path length over the window."""

    if len(closes) < window + 1:
        return None
    tail = closes[-window - 1:]
    net = abs(tail[-1] - tail[0])
    path = sum(abs(tail[i] - tail[i - 1]) for i in range(1, len(tail)))
    if path <= 0:
        return None
    return net / path


def ma_slope(closes: Sequence[float], ma_window: int = 50, change_bars: int = 21) -> float | None:
    """% change of the 50-day SMA over the last 21 bars."""

    need = ma_window + change_bars
    if len(closes) < need:
        return None

    def sma_at(end_index: int) -> float:
        return sum(closes[end_index - ma_window:end_index]) / ma_window

    now = sma_at(len(closes))
    before = sma_at(len(closes) - change_bars)
    if before <= 0:
        return None
    return (now / before - 1.0) * 100.0


def trend_stability(closes: Sequence[float], window: int = 20) -> float | None:
    """Population stdev of daily returns (smaller = steadier trend)."""

    if len(closes) < window + 1:
        return None
    returns = [
        closes[i] / closes[i - 1] - 1.0
        for i in range(len(closes) - window, len(closes))
        if closes[i - 1] > 0
    ]
    if len(returns) < 3:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return math.sqrt(variance)


def range_position(
    closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], window: int = 60
) -> dict[str, float | None]:
    """Close position inside the N-day range (0-1) plus 5/20-day EMAs of it."""

    n = len(closes)
    if n < window + 5:
        return {"position": None, "persistence_fast": None, "persistence_slow": None}
    positions: list[float] = []
    for i in range(window, n):
        window_high = max(highs[i - window + 1:i + 1])
        window_low = min(lows[i - window + 1:i + 1])
        span = window_high - window_low
        positions.append((closes[i] - window_low) / span if span > 0 else 0.5)
    # EMA 平滑线要有不少于自身周期的观测才有意义：5 期线至少 5 个点、
    # 20 期线至少 20 个点，否则「慢线」只是首值的影子，如实返回 None。
    fast = _ema_series(positions, 5)[-1] if len(positions) >= 5 else None
    slow = _ema_series(positions, 20)[-1] if len(positions) >= 20 else None
    return {
        "position": _safe(positions[-1]) if positions else None,
        "persistence_fast": _safe(fast),
        "persistence_slow": _safe(slow),
    }


def sma_series(closes: Sequence[float], window: int) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if window < 1 or n < window:
        return out
    acc = 0.0
    for i, value in enumerate(closes):
        acc += value
        if i >= window:
            acc -= closes[i - window]
        if i >= window - 1:
            out[i] = _safe(acc / window)
    return out


def rsi_series(closes: Sequence[float], period: int = 14) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < period + 1:
        return out
    gains = 0.0
    losses = 0.0
    for i in range(1, period + 1):
        delta = closes[i] - closes[i - 1]
        if delta > 0:
            gains += delta
        else:
            losses -= delta
    avg_gain = gains / period
    avg_loss = losses / period
    if avg_loss <= 0:
        out[period] = 100.0 if avg_gain > 0 else 50.0
    else:
        out[period] = _safe(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 2)
    for i in range(period + 1, n):
        delta = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(delta, 0.0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-delta, 0.0)) / period
        if avg_loss <= 0:
            out[i] = 100.0 if avg_gain > 0 else 50.0
        else:
            out[i] = _safe(100.0 - 100.0 / (1.0 + avg_gain / avg_loss), 2)
    return out


# 三条 EMA 都以第 0 根为种子：慢线要 26 根才摆脱种子，信号线再叠 9 根。
# 这段头部的值只是种子的影子，和任何看盘软件都对不上，按 sma/rsi 的规矩留 None。
MACD_WARMUP = 26 + 9


def macd_series(closes: Sequence[float]) -> dict[str, list[float | None]]:
    n = len(closes)
    empty = [None] * n
    if n < 40:
        return {"macd": empty, "signal": empty[:], "histogram": empty[:]}
    fast = _ema_series(closes, 12)
    slow = _ema_series(closes, 26)
    macd_line = [f - s for f, s in zip(fast, slow)]
    signal = _ema_series(macd_line, 9)
    histogram = [m - s for m, s in zip(macd_line, signal)]

    def masked(values: Sequence[float]) -> list[float | None]:
        return [None if i < MACD_WARMUP else _safe(value) for i, value in enumerate(values)]

    return {
        "macd": masked(macd_line),
        "signal": masked(signal),
        "histogram": masked(histogram),
    }


def range_position_series(
    closes: Sequence[float], highs: Sequence[float], lows: Sequence[float], window: int = 60
) -> list[float | None]:
    n = len(closes)
    out: list[float | None] = [None] * n
    if n < window:
        return out
    for i in range(window - 1, n):
        window_high = max(highs[i - window + 1 : i + 1])
        window_low = min(lows[i - window + 1 : i + 1])
        span = window_high - window_low
        out[i] = _safe((closes[i] - window_low) / span if span > 0 else 0.5)
    return out


def compute_technicals(series: dict[str, list]) -> dict[str, Any]:
    closes = list(series.get("closes") or [])
    highs = list(series.get("highs") or [])
    lows = list(series.get("lows") or [])
    rsi_value = rsi14(closes)
    rp = range_position(closes, highs, lows)
    return {
        "version": TECHNICALS_VERSION,
        "rsi14": _safe(rsi_value, 2),
        "rsi_score": _safe(rsi_score(rsi_value), 1),
        "macd": macd_direction(closes),
        "trend_efficiency_63d": _safe(trend_efficiency(closes)),
        "ma50_slope_pct_21d": _safe(ma_slope(closes), 2),
        "return_stability_20d": _safe(trend_stability(closes), 5),
        "range_position_60d": rp["position"],
        "range_persistence_fast": rp["persistence_fast"],
        "range_persistence_slow": rp["persistence_slow"],
    }


__all__ = [
    "MACD_WARMUP",
    "TECHNICALS_VERSION",
    "compute_technicals",
    "ma_slope",
    "macd_direction",
    "macd_series",
    "range_position",
    "range_position_series",
    "rsi14",
    "rsi_score",
    "rsi_series",
    "sma_series",
    "trend_efficiency",
    "trend_stability",
]
