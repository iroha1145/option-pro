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
    # 摆动点不足以构成完整序列时不打分：中性 50 是「观察到的均衡」，
    # 不能拿来掩盖「没有足够证据」。score=None → 评分侧该维度自动脱落。
    "unconfirmed": "结构未确认",
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
    # 数据不足时 score 必须是 None：这个字典会原样进入 API 行与
    # breakdown.price_action_detail，50.0 会被快照/AI 上下文当成
    # 真实的「中性价格行为分」。评分侧本就按 status 把非 active 挡在外面。
    return {
        "status": status,
        "score": None,
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
        "pattern_events": [],
        "spring": False,
        "upthrust": False,
        "spring_bars_ago": None,
        "spring_level": None,
        "upthrust_bars_ago": None,
        "upthrust_level": None,
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
        # 单边平滑行情、平台或样本太短都会走到这里：连两个已确认高点/低点
        # 都凑不齐，HH/HL 比较无从谈起——如实报未确认，而不是判成区间震荡。
        return "unconfirmed"
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
) -> list[tuple[str, int]]:
    """Candlestick patterns on the most recent `check_last` bars, with the bar
    index each fired on (so callers can report the date / bars-ago).

    Pin bars only count at price extremes (hammer near a local low, shooting
    star near a local high) — a hammer mid-range is noise, not signal.
    """
    n = len(close)
    found: list[tuple[str, int]] = []
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
            found.append(("bullish_engulfing", i))
        elif pc > po and c < o and c <= po and o >= pc and body > prev_body * 1.05:
            found.append(("bearish_engulfing", i))

        # Pin bars (require a meaningful body so dojis don't trigger).
        window_lo = min(low[max(0, i - extreme_window):i + 1])
        window_hi = max(high[max(0, i - extreme_window):i + 1])
        if body > 0 and lower_wick >= body * 2 and upper_wick <= body * 0.6 and l <= window_lo * 1.01:
            found.append(("hammer", i))
        elif body > 0 and upper_wick >= body * 2 and lower_wick <= body * 0.6 and h >= window_hi * 0.99:
            found.append(("shooting_star", i))

        # Inside bar: contraction, direction-neutral.
        if h < high[i - 1] and l > low[i - 1]:
            found.append(("inside_bar", i))
    # 每种形态只留最近一次出现（bars_ago 才是最新发生位置）；顺序按首次出现。
    latest: dict[str, int] = {}
    order: list[str] = []
    for name, index in found:
        if name not in latest:
            order.append(name)
        latest[name] = max(latest.get(name, index), index)
    return [(name, latest[name]) for name in order]


def _atr(high: list[float], low: list[float], close: list[float], window: int = 14) -> float | None:
    n = len(close)
    if n < 2:
        return None
    window = min(window, n - 1)
    trs = [
        max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
        for i in range(n - window, n)
    ]
    return sum(trs) / len(trs) if trs else None


def _detect_traps(
    high: list[float], low: list[float], close: list[float],
    swing_highs: list[tuple[int, float]], swing_lows: list[tuple[int, float]],
    atr: float | None,
    recent: int = 8,
) -> dict[str, Any]:
    """Spring / upthrust against the latest CONFIRMED swing levels.

    spring   = a recent bar pierced the prior swing low intraday but closed
               back above it (failed breakdown → bullish).
    upthrust = pierced the prior swing high but closed back below (failed
               breakout → bearish).
    Only swings confirmed BEFORE the probed bar are used — no lookahead.
    穿越幅度阈值按 max(0.2%×位, 0.1×ATR)：高价股 0.2% 是天量、低波动股
    0.2% 又太宽，纯百分比在两端都失真。
    Returns the latest occurrence's bar index and reference level, so callers
    can report when it happened instead of a bare boolean.
    """
    n = len(close)
    start = max(0, n - recent)
    result: dict[str, Any] = {
        "spring": False, "upthrust": False,
        "spring_index": None, "spring_level": None,
        "upthrust_index": None, "upthrust_level": None,
    }
    for i in range(start, n):
        prior_lows = [price for idx, price in swing_lows if idx < i - 1]
        if prior_lows:
            level = prior_lows[-1]
            pierce = max(level * 0.002, (atr or 0.0) * 0.1)
            if low[i] < level - pierce and close[i] > level:
                result["spring"] = True
                result["spring_index"] = i
                result["spring_level"] = level
        prior_highs = [price for idx, price in swing_highs if idx < i - 1]
        if prior_highs:
            level = prior_highs[-1]
            pierce = max(level * 0.002, (atr or 0.0) * 0.1)
            if high[i] > level + pierce and close[i] < level:
                result["upthrust"] = True
                result["upthrust_index"] = i
                result["upthrust_level"] = level
    return result


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
    tags: list[str] = [structure_label] if structure != "range" else []

    pattern_hits = _detect_patterns(open_, high, low, close)
    patterns = [name for name, _ in pattern_hits]
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

    n = len(close)
    atr = _atr(high, low, close)
    traps = _detect_traps(high, low, close, swing_highs, swing_lows, atr)
    spring = bool(traps["spring"])
    upthrust = bool(traps["upthrust"])
    trap_adjust = 0.0
    if spring:
        trap_adjust += 8.0
        tags.append("Spring 假跌破回收")
    if upthrust:
        trap_adjust -= 8.0
        tags.append("Upthrust 假突破")

    # 结构未确认 → 不打分。形态/陷阱仍如实报告（它们不依赖摆动序列完整），
    # 但没有基准分就没有可修正的对象；评分侧按 None 让该维度权重重分。
    if structure == "unconfirmed":
        score: float | None = None
    else:
        score = round(_clamp(_STRUCTURE_SCORES[structure] + pattern_adjust + trap_adjust), 1)

    resistance = swing_highs[-1][1] if swing_highs else None
    support = swing_lows[-1][1] if swing_lows else None

    bars_ago = (lambda index: None if index is None else n - 1 - int(index))

    return {
        "status": "active",
        "score": score,
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
        # 形态是「最近 3 根里的历史事件」，不是当前状态——带上距今根数让
        # 前端能写明发生时点，而不是永远像刚刚出现。
        "pattern_events": [
            {
                "pattern": name,
                "label": _PATTERN_LABELS.get(name, name),
                "bars_ago": bars_ago(index),
            }
            for name, index in pattern_hits
        ],
        "spring": spring,
        "upthrust": upthrust,
        "spring_bars_ago": bars_ago(traps["spring_index"]),
        "spring_level": _safe_float(traps["spring_level"], 4),
        "upthrust_bars_ago": bars_ago(traps["upthrust_index"]),
        "upthrust_level": _safe_float(traps["upthrust_level"], 4),
        "tags": list(dict.fromkeys(tags))[:4],
    }
