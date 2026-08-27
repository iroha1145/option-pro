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

import math
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence
from zoneinfo import ZoneInfo

import logging

import pandas as pd

from app.services.market_calendar import early_close_minutes, is_trading_day
from app.services.strength.price_action import (
    _find_swings,
    compute_price_action,
)
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.technical.auto_patterns import detect_auto_patterns
from app.services.technical.base_structure import detect_base_structure
from app.services.technical.chart_analysis import assemble_chart_analysis
from app.services.technical.indicators import compute_technicals

logger = logging.getLogger(__name__)

STRUCTURE_VERSION = "us-structure-v2"

_NEW_YORK_TZ = ZoneInfo("America/New_York")
_SWING_SPAN = 3
_SWING_LOOKBACK = 120
_MIN_BARS = 30

_PATTERN_FAIL_COUNTS: dict[str, int] = {}
_PATTERN_FAIL_KEY_CAP = 200
_PATTERN_FAIL_TRACEBACKS = 3
_PATTERN_FAIL_MSG_CHARS = 200
_pattern_fail_suppressed = False


def _log_pattern_failure(ticker: str, exc: Exception) -> None:
    """记录形态检测失败，且三重有界，绝不刷爆日志。

    这条路径每个请求每支票都会走一遍：真出系统性故障时，不设限的
    ``logger.exception`` 会按请求量刷屏，把日志盘吃掉。所以：
      1. 按（票 × 异常类型）去重——同一种故障每支票只说一次；
      2. 只有最先的几次带堆栈，之后是单行摘要（堆栈才是体积大头）；
      3. 去重表的键数封顶，满了就停止收录并只提示一次，避免字典本身无限增长。

    计数保留在内存里，重启清零——它服务的是「线上现在有没有在静默失败」，
    不是长期统计。
    """
    global _pattern_fail_suppressed
    key = f"{ticker or '?'}:{type(exc).__name__}"
    seen = _PATTERN_FAIL_COUNTS.get(key)
    if seen is not None:
        _PATTERN_FAIL_COUNTS[key] = seen + 1
        return
    if len(_PATTERN_FAIL_COUNTS) >= _PATTERN_FAIL_KEY_CAP:
        if not _pattern_fail_suppressed:
            _pattern_fail_suppressed = True
            logger.warning(
                "auto-pattern failures exceeded %d distinct keys; further first-sightings are not logged",
                _PATTERN_FAIL_KEY_CAP,
            )
        return
    _PATTERN_FAIL_COUNTS[key] = 1
    with_traceback = len(_PATTERN_FAIL_COUNTS) <= _PATTERN_FAIL_TRACEBACKS
    logger.warning(
        "auto-pattern detection failed for %s: %s",
        key,
        str(exc)[:_PATTERN_FAIL_MSG_CHARS],
        exc_info=with_traceback,
    )


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
        try:
            volume = max(0.0, float(bar.get("v") or 0))
        except (TypeError, ValueError):
            volume = 0.0
        # NaN/Inf 会穿过下面所有比较（与 NaN 比大小恒为 False），一路活到
        # 指纹与指标里再炸；非有限值一律当坏 bar 丢掉，先于任何筛选。
        if not all(math.isfinite(v) for v in (open_, high, low, close, volume)):
            continue
        if close <= 0 or high < low or close > high * 1.0001 or close < low * 0.9999:
            continue
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
    ticker: str = "",
    spy_closes: Sequence[float | None] | Mapping[str, float] | None = None,
    chart_range: str = "1d",
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

    try:
        auto_patterns = detect_auto_patterns(
            analysis,
            data_through=analysis["dates"][-1],
        )
    except Exception as exc:
        # 装饰性图层不该拖垮整页，但也不能像以前那样一声不吭：这个裸吞曾让
        # 「线上一条形态都画不出来」看起来和「本来就没有形态」完全一样。
        auto_patterns = []
        _log_pattern_failure(ticker, exc)

    aligned_spy = spy_closes
    if isinstance(spy_closes, Mapping):
        aligned_spy = spy_closes
    elif spy_closes is not None:
        by_index = list(spy_closes)
        if len(by_index) == len(series["dates"]) and len(analysis["dates"]) != len(series["dates"]):
            aligned_spy = by_index[: len(analysis["dates"])]
    # 图层包是装饰层：它整包失败也不该带走 base/指标/摆动这些核心字段，
    # 与上面 detect_auto_patterns 同一待遇（客户端拿到 None 就什么都不画）。
    try:
        chart_analysis = assemble_chart_analysis(
            series=analysis,
            data_through=analysis["dates"][-1],
            ticker=ticker,
            chart_range=chart_range,
            adjustment="raw",
            price_action=price_action,
            vol_price=vol_price,
            base=base,
            base_state=base_state,
            technicals=technicals,
            auto_patterns=auto_patterns,
            spy_closes=aligned_spy,
            series_break_at=series_break_at,
            hist=frame,
        )
    except Exception:
        chart_analysis = None

    return {
        "version": STRUCTURE_VERSION,
        "base": base,
        "base_state": base_state,
        "price_action": price_action,
        "vol_price": vol_price,
        "technicals": technicals,
        "chart_overlays": overlays,
        # 形态只以 chart_analysis.overlays 一种形态下发：顶层再发一份没有任何
        # 消费者，只是把同一份 JSON 在响应里重复三次。
        "chart_analysis": chart_analysis,
        "bar_count": len(analysis["closes"]),
        # data_through = 指标/结构实际吃到的最后一根收盘 K；last_bar 描述
        # 图上可见的最后一根（可能未收盘）。两者不同时 UI 应标「暂定」。
        "data_through": analysis["dates"][-1],
        "last_bar": last_bar,
        "series_break_at": series_break_at,
    }


__all__ = ["STRUCTURE_VERSION", "clean_series", "compute_technical_structure", "series_excluding_last"]
