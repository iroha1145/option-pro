"""Pure price-action (K线行为) analysis — OHLC only, no volume.

Classic price-action toolkit as the 7th scoring dimension:
- Swing point detection (fractal pivots, confirmed only — no lookahead)
- Market structure: HH/HL vs LH/LL trend state
- Candlestick patterns: engulfing, pin bar (hammer / shooting star), inside bar
- Structural traps: spring (false breakdown) / upthrust (false breakout)
- Support / resistance from the latest confirmed swings

Everything here derives from Open/High/Low/Close alone, which keeps it
complementary to vol_price_match.py (Wyckoff-style effort-vs-result, which
mixes in volume).
"""
from __future__ import annotations

import math
from typing import Any

import pandas as pd

# Score anchors per structure state (0-100, 50 = neutral).
_STRUCTURE_SCORES = {
    "uptrend": 82.0,          # HH + HL
    "uptrend_weak": 66.0,     # HH only
    "hl_base": 62.0,          # HL only (higher lows building a base)
    "range": 50.0,
    "lh_pressure": 38.0,      # LH only
    "downtrend": 24.0,        # LH + LL
}

_STRUCTURE_LABELS = {
    "uptrend": "HH+HL 上升结构",
    "uptrend_weak": "高点抬升待确认",
    "hl_base": "低点抬升筑底",
    "range": "区间震荡",
    "lh_pressure": "高点压低",
    "downtrend": "LH+LL 下降结构",
}

_PATTERN_LABELS = {
    "bullish_engulfing": "看涨吞没",
    "bearish_engulfing": "看跌吞没",
    "hammer": "锤子线",
    "shooting_star": "射击之星",
    "inside_bar": "内包线",
}

_PATTERN_ADJUST = {
    "bullish_engulfing": 6.0,
    "bearish_engulfing": -6.0,
    "hammer": 6.0,
    "shooting_star": -6.0,
    "inside_bar": 0.0,
}


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _empty(status: str, label: str) -> dict[str, Any]:
    return {
        "status": status,
        "score": 50.0,
        "structure": status,
        "structure_label": label,
        "swing_high": None,
        "swing_low": None,
        "resistance": None,
        "support": None,
        "resistance_dist_pct": None,
        "support_dist_pct": None,
        "patterns": [],
        "pattern_labels": [],
        "spring": False,
        "upthrust": False,
        "tags": [label],
    }


def _find_swings(high: list[float], low: list[float], span: int) -> tuple[list[tuple[int, float]], list[tuple[int, float]]]:
    """Fractal pivots: bar i is a swing high if its high strictly exceeds the
    highs of `span` bars on BOTH sides. The trailing `span` bars can never be
    confirmed (their right side hasn't printed) — standard no-lookahead rule.
    Returns ([(index, price)...] highs, [...] lows) in chronological order.
    """
    swing_highs: list[tuple[int, float]] = []
    swing_lows: list[tuple[int, float]] = []
    n = len(high)
    for i in range(span, n - span):
        # Strict on the LEFT, ties allowed on the RIGHT (Wilder-style fractal):
        # equal highs happen at round numbers / gap opens, and a fully strict
        # test silently drops those pivots. Left-strict keeps plateaus from
        # producing duplicate swings (only the first bar wins).
        if high[i] > max(high[i - span:i]) and high[i] >= max(high[i + 1:i + span + 1]):
            swing_highs.append((i, high[i]))
        if low[i] < min(low[i - span:i]) and low[i] <= min(low[i + 1:i + span + 1]):
            swing_lows.append((i, low[i]))
    return swing_highs, swing_lows


def _structure_state(swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]]) -> str:
    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "range"
    hh = swing_highs[-1][1] > swing_highs[-2][1]
    hl = swing_lows[-1][1] > swing_lows[-2][1]
    lh = swing_highs[-1][1] < swing_highs[-2][1]
    ll = swing_lows[-1][1] < swing_lows[-2][1]
    if hh and hl:
        return "uptrend"
    if lh and ll:
        return "downtrend"
    if hh:
        return "uptrend_weak"
    if hl:
        return "hl_base"
    if lh:
        return "lh_pressure"
    return "range"


def _detect_patterns(
    open_: list[float], high: list[float], low: list[float], close: list[float],
    check_last: int = 3, extreme_window: int = 10,
) -> list[str]:
    """Candlestick patterns on the most recent `check_last` bars.

    Pin bars only count at price extremes (hammer near a local low, shooting
    star near a local high) — a hammer mid-range is noise, not signal.
    """
    n = len(close)
    found: list[str] = []
    for i in range(max(1, n - check_last), n):
        o, h, l, c = open_[i], high[i], low[i], close[i]
        po, pc = open_[i - 1], close[i - 1]
        rng = h - l
        if rng <= 0:
            continue
        body = abs(c - o)
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        prev_body = abs(pc - po)

        # Engulfing: current real body wraps the previous real body, opposite colors.
        if pc < po and c > o and c >= po and o <= pc and body > prev_body * 1.05:
            found.append("bullish_engulfing")
        elif pc > po and c < o and c <= po and o >= pc and body > prev_body * 1.05:
            found.append("bearish_engulfing")

        # Pin bars (require a meaningful body so dojis don't trigger).
        window_lo = min(low[max(0, i - extreme_window):i + 1])
        window_hi = max(high[max(0, i - extreme_window):i + 1])
        if body > 0 and lower_wick >= body * 2 and upper_wick <= body * 0.6 and l <= window_lo * 1.01:
            found.append("hammer")
        elif body > 0 and upper_wick >= body * 2 and lower_wick <= body * 0.6 and h >= window_hi * 0.99:
            found.append("shooting_star")

        # Inside bar: contraction, direction-neutral.
        if h < high[i - 1] and l > low[i - 1]:
            found.append("inside_bar")
    return list(dict.fromkeys(found))


def _detect_traps(
    high: list[float], low: list[float], close: list[float],
    swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]],
    recent: int = 8,
) -> tuple[bool, bool]:
    """Spring / upthrust against the latest CONFIRMED swing levels.

    spring   = a recent bar pierced the prior swing low intraday but closed
               back above it (failed breakdown → bullish).
    upthrust = pierced the prior swing high but closed back below (failed
               breakout → bearish).
    Only swings confirmed BEFORE the probed bar are used — no lookahead.
    """
    n = len(close)
    start = max(0, n - recent)
    spring = False
    upthrust = False
    for i in range(start, n):
        prior_lows = [price for idx, price in swing_lows if idx < i - 1]
        if prior_lows and not spring:
            level = prior_lows[-1]
            if low[i] < level * 0.998 and close[i] > level:
                spring = True
        prior_highs = [price for idx, price in swing_highs if idx < i - 1]
        if prior_highs and not upthrust:
            level = prior_highs[-1]
            if high[i] > level * 1.002 and close[i] < level:
                upthrust = True
    return spring, upthrust


def compute_price_action(
    hist: pd.DataFrame,
    *,
    swing_span: int = 3,
    lookback: int = 120,
) -> dict[str, Any]:
    required = {"Open", "High", "Low", "Close"}
    if hist is None or hist.empty or not required.issubset(hist.columns):
        return _empty("missing_data", "K线数据不足")

    data = hist.tail(lookback)
    frame = data[["Open", "High", "Low", "Close"]].astype(float).dropna()
    if len(frame) < 40:
        return _empty("not_enough_data", "K线样本不足")

    open_ = frame["Open"].tolist()
    high = frame["High"].tolist()
    low = frame["Low"].tolist()
    close = frame["Close"].tolist()
    last_close = close[-1]
    if not last_close or last_close <= 0:
        return _empty("invalid_price", "价格异常")

    swing_highs, swing_lows = _find_swings(high, low, swing_span)
    structure = _structure_state(swing_highs, swing_lows)
    structure_label = _STRUCTURE_LABELS[structure]
    score = _STRUCTURE_SCORES[structure]
    tags: list[str] = [structure_label] if structure != "range" else []

    patterns = _detect_patterns(open_, high, low, close)
    pattern_adjust = 0.0
    for pattern in patterns:
        pattern_adjust += _PATTERN_ADJUST.get(pattern, 0.0)
        label = _PATTERN_LABELS.get(pattern)
        if label and pattern != "inside_bar":
            tags.append(label)
    if "inside_bar" in patterns:
        tags.append("内包线收缩")
    # Cap combined candle influence — patterns refine structure, never dominate it.
    pattern_adjust = max(-10.0, min(10.0, pattern_adjust))

    spring, upthrust = _detect_traps(high, low, close, swing_highs, swing_lows)
    trap_adjust = 0.0
    if spring:
        trap_adjust += 8.0
        tags.append("Spring 假跌破回收")
    if upthrust:
        trap_adjust -= 8.0
        tags.append("Upthrust 假突破")

    score = _clamp(score + pattern_adjust + trap_adjust)

    resistance = swing_highs[-1][1] if swing_highs else None
    support = swing_lows[-1][1] if swing_lows else None

    return {
        "status": "active",
        "score": round(score, 1),
        "structure": structure,
        "structure_label": structure_label,
        "swing_high": _safe_float(resistance, 4),
        "swing_low": _safe_float(support, 4),
        "resistance": _safe_float(resistance, 4),
        "support": _safe_float(support, 4),
        "resistance_dist_pct": _safe_float((resistance / last_close - 1) * 100, 2) if resistance else None,
        "support_dist_pct": _safe_float((support / last_close - 1) * 100, 2) if support else None,
        "patterns": patterns,
        "pattern_labels": [_PATTERN_LABELS[p] for p in patterns if p in _PATTERN_LABELS],
        "spring": spring,
        "upthrust": upthrust,
        "tags": list(dict.fromkeys(tags))[:4],
    }
