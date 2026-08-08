"""Chart-ready technical structure for the stock detail page.

Assembles four existing analyses over the same raw daily bars the chart
endpoint serves, so every overlay lands exactly on the candles the user sees:

- price action (strength/price_action.compute_price_action — canonical)
- effort-vs-result volume/price match (strength/vol_price_match)
- completed-base detection (technical/base_structure — pivot clustering)
- classic indicators (technical/indicators)

Swing points are re-derived here with the same fractal parameters as the
price-action module (span=3, lookback=120) purely to attach bar timestamps
for chart markers; values are bit-identical to the analysis by construction.

Input bars are the chart contract shape: {t: epoch-seconds, o, h, l, c, v}.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.market_calendar import early_close_minutes, is_trading_day
from app.services.strength.price_action import (
    _find_swings,
    compute_price_action,
)
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.technical.base_structure import detect_base_structure
from app.services.technical.indicators import compute_technicals

STRUCTURE_VERSION = "us-structure-v2"

_NEW_YORK_TZ = ZoneInfo("America/New_York")
_SWING_SPAN = 3
_SWING_LOOKBACK = 120
_MIN_BARS = 30
# 相邻收盘比落在这个区间外视为序列断裂（错误的未复权拼接、坏行情源）。
# 真实单日 ±50%+ 的行情极罕见且多伴随停牌；跨过断裂点做摆动/基底分析
# 得到的全是假结构，宁可只用断裂之后的一致段。
_SERIES_CONTINUITY_RATIO = (0.45, 2.2)


def clean_series(bars: Sequence[Mapping[str, Any]]) -> dict[str, list] | None:
    """Chart bars → parallel columns. Malformed bars are dropped, not repaired.

    ``turnover`` is dollar volume (close × volume) — share counts are not
    comparable across price levels, same discipline as vol_price_match.
    """

    times: list[int] = []
    dates: list[str] = []
    opens: list[float] = []
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    turnover: list[float | None] = []
    for bar in bars:
        try:
            t = int(bar["t"])
            close = float(bar["c"])
            open_ = float(bar.get("o") or close)
            high = float(bar.get("h") or close)
            low = float(bar.get("l") or close)
        except (KeyError, TypeError, ValueError):
            continue
        if close <= 0 or high < low or close > high * 1.0001 or close < low * 0.9999:
            continue
        try:
            volume = max(0.0, float(bar.get("v") or 0))
        except (TypeError, ValueError):
            volume = 0.0
        times.append(t)
        # Daily bars stamp the New York session; render dates in that zone so
        # a UTC+8 viewer's overlay matches the axis label, not the next day.
        dates.append(
            datetime.fromtimestamp(t, tz=timezone.utc).astimezone(_NEW_YORK_TZ).date().isoformat()
        )
        opens.append(open_)
        highs.append(high)
        lows.append(low)
        closes.append(close)
        volumes.append(volume)
        turnover.append(close * volume if volume > 0 else None)
    if len(closes) < _MIN_BARS:
        return None
    return {
        "times": times,
        "dates": dates,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "closes": closes,
        "volumes": volumes,
        "turnover": turnover,
    }


def series_excluding_last(series: dict[str, list]) -> dict[str, list] | None:
    if len(series["closes"]) < 2:
        return None
    return {key: values[:-1] for key, values in series.items()}


def _frame(series: dict[str, list]) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [datetime.fromtimestamp(t, tz=timezone.utc) for t in series["times"]]
    )
    return pd.DataFrame(
        {
            "Open": series["opens"],
            "High": series["highs"],
            "Low": series["lows"],
            "Close": series["closes"],
            "Volume": series["volumes"],
        },
        index=index,
    )


def _dated_swings(series: dict[str, list]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Confirmed fractal swings over the price-action window, with the exact
    bar `t` so the frontend can address the matching candle by identity."""

    highs = series["highs"][-_SWING_LOOKBACK:]
    lows = series["lows"][-_SWING_LOOKBACK:]
    times = series["times"][-_SWING_LOOKBACK:]
    dates = series["dates"][-_SWING_LOOKBACK:]
    swing_highs, swing_lows = _find_swings(highs, lows, _SWING_SPAN)

    def pack(points: list[tuple[int, float]]) -> list[dict[str, Any]]:
        return [
            {"t": times[index], "trade_date": dates[index], "price": round(price, 4)}
            for index, price in points[-4:]
        ]

    return pack(swing_highs), pack(swing_lows)


def _truncate_after_series_break(series: dict[str, list]) -> tuple[dict[str, list], str | None]:
    """Keep only the consistent segment after the latest close-to-close break."""

    closes = series["closes"]
    lo, hi = _SERIES_CONTINUITY_RATIO
    break_index = None
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev <= 0:
            continue
        ratio = closes[i] / prev
        if ratio < lo or ratio > hi:
            break_index = i
    if break_index is None:
        return series, None
    truncated = {key: values[break_index:] for key, values in series.items()}
    return truncated, series["dates"][break_index]


def _last_bar_closed(last_epoch: int, now: datetime) -> bool:
    """Whether the last daily bar's New York session has ended.

    Providers include today's in-progress daily bar during the session with
    no marker; treating it as closed lets an hour of trading masquerade as a
    finished candle inside every indicator. Evaluated at build time — the
    flag describes this payload's data, so caching it stays truthful.
    """

    bar_day = datetime.fromtimestamp(last_epoch, tz=timezone.utc).astimezone(_NEW_YORK_TZ).date()
    now_ny = now.astimezone(_NEW_YORK_TZ)
    if bar_day < now_ny.date():
        return True
    if bar_day > now_ny.date():
        return False  # 未来时间戳只可能是坏数据，按未收盘保守处理
    if not is_trading_day(bar_day):
        return True
    close_minutes = early_close_minutes(bar_day) or 16 * 60
    return (now_ny.hour * 60 + now_ny.minute) >= close_minutes


def _base_state(
    base: Mapping[str, Any] | None,
    *,
    close: float,
    trade_date: str,
    provisional: bool,
) -> dict[str, Any] | None:
    """Where the LATEST price sits relative to the detected (historical) base.

    The detector only says "a completed base existed as of yesterday"; without
    this the UI presents every detection as a live, intact base even after the
    newest bar broke out of it or fell through the invalidation level.
    """

    if not base:
        return None
    resistance_high = base.get("resistance_high")
    resistance_low = base.get("resistance_low")
    support_low = base.get("support_low")
    invalidation = base.get("invalidation_price")
    buffer = base.get("break_buffer") or 0.0
    if not all(
        isinstance(v, (int, float)) for v in (resistance_high, resistance_low, support_low, invalidation)
    ):
        return None
    if close > resistance_high + buffer:
        status = "breakout"
    elif close < invalidation:
        status = "failed"
    elif close < support_low:
        status = "below_support"
    elif close >= resistance_low - buffer:
        status = "at_resistance"
    else:
        status = "in_base"
    return {
        "status": status,
        "reference_close": round(close, 4),
        "reference_date": trade_date,
        # 参考收盘来自未收盘 bar 时状态是暂定的：突破可能在尾盘收回去。
        "provisional": provisional,
    }


def compute_technical_structure(
    bars: Sequence[Mapping[str, Any]],
    *,
    last_bar_closed: bool | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Full detail-page structure payload, or ``None`` when bars are unusable.

    ``last_bar_closed`` lets callers that already know the session state (the
    chart endpoint) override the calendar inference; ``now`` exists for tests.
    """

    series = clean_series(bars)
    if series is None:
        return None
    series, series_break_at = _truncate_after_series_break(series)
    if len(series["closes"]) < _MIN_BARS:
        return None

    now = now or datetime.now(timezone.utc)
    if last_bar_closed is None:
        last_bar_closed = _last_bar_closed(series["times"][-1], now)
    last_bar = {
        "t": series["times"][-1],
        "trade_date": series["dates"][-1],
        "closed": bool(last_bar_closed),
    }

    # 全部指标与结构只吃收盘完结的 K 线：盘中未收盘的末根另行作为
    # 「最新价」参与基底状态判定，但不进 RSI/MACD/摆动/量价的样本。
    analysis = series if last_bar_closed else series_excluding_last(series)
    if analysis is None or len(analysis["closes"]) < _MIN_BARS:
        return None
    frame = _frame(analysis)

    price_action = compute_price_action(frame, swing_span=_SWING_SPAN, lookback=_SWING_LOOKBACK)
    swing_highs, swing_lows = _dated_swings(analysis)
    analysis_dates = analysis["dates"]

    def date_of(bars_ago: Any) -> str | None:
        if not isinstance(bars_ago, int) or bars_ago < 0 or bars_ago >= len(analysis_dates):
            return None
        return analysis_dates[len(analysis_dates) - 1 - bars_ago]

    price_action = {
        **price_action,
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
        "pattern_events": [
            {**event, "trade_date": date_of(event.get("bars_ago"))}
            for event in price_action.get("pattern_events", [])
        ],
        "spring_trade_date": date_of(price_action.get("spring_bars_ago")),
        "upthrust_trade_date": date_of(price_action.get("upthrust_bars_ago")),
    }

    vol_price = compute_vol_price_match(frame)

    prior = series_excluding_last(analysis)
    base = detect_base_structure(prior) if prior else None
    base_state = _base_state(
        base,
        close=series["closes"][-1],
        trade_date=series["dates"][-1],
        provisional=not last_bar_closed,
    )

    technicals = compute_technicals(analysis)

    overlays: dict[str, Any] = {
        "swing_highs": swing_highs,
        "swing_lows": swing_lows,
    }
    if base:
        overlays["resistance_high"] = base.get("resistance_high")
        overlays["resistance_low"] = base.get("resistance_low")
        overlays["support_low"] = base.get("support_low")
        overlays["invalidation_price"] = base.get("invalidation_price")
        overlays["pivot_price"] = base.get("pivot_price")
        overlays["base_start"] = base.get("base_start")
        overlays["base_end"] = base.get("base_end")
    if base_state:
        overlays["base_status"] = base_state["status"]

    return {
        "version": STRUCTURE_VERSION,
        "base": base,
        "base_state": base_state,
        "price_action": price_action,
        "vol_price": vol_price,
        "technicals": technicals,
        "chart_overlays": overlays,
        "bar_count": len(analysis["closes"]),
        # data_through = 指标/结构实际吃到的最后一根收盘 K；last_bar 描述
        # 图上可见的最后一根（可能未收盘）。两者不同时 UI 应标「暂定」。
        "data_through": analysis["dates"][-1],
        "last_bar": last_bar,
        "series_break_at": series_break_at,
    }


__all__ = ["STRUCTURE_VERSION", "clean_series", "compute_technical_structure", "series_excluding_last"]
