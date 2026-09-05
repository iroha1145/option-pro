"""Per-stock factor row shared by the Strength Radar and the detail chart.

强度雷达（scanner）与个股详情图（technical/chart_analysis）打的是同一套
score_intrinsic 因子，所以特征行只能有一份实现——手抄的第二份漂过一次：
follow_through 口径不同、atr_pct 与 avg_dollar_volume_20d 直接缺失，
同一支票同一天两个界面给出不同分数。

这个模块是中立层：它不认识扫描器的全市场管线（scan_strength / 排名 /
market-fit），所以详情图路径 import 它不违反「个股图不跑 Strength Scanner」。
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

from app.services.daily_returns import aligned_benchmark_return
from app.services.strength.price_action import compute_price_action
from app.services.strength.vol_price_match import compute_vol_price_match
from app.services.zh_names import get_zh_name

# 「52 周高位」的最低样本量：一年约 252 个交易日，放 12 根余量容忍
# 数据源对首尾少量交易日的裁剪；再短就不构成「一年」。
_MIN_52W_HISTORY_BARS = 240


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    avg_gain = _safe_float(gain.dropna().iloc[-1], 8) if not gain.dropna().empty else None
    avg_loss = _safe_float(loss.dropna().iloc[-1], 8) if not loss.dropna().empty else None
    if avg_gain is None or avg_loss is None:
        return None
    if avg_gain == 0 and avg_loss == 0:
        return 50.0
    if avg_loss == 0:
        return 100.0
    if avg_gain == 0:
        return 0.0
    return _safe_float(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def _macd_direction(close: pd.Series) -> float | None:
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    if len(hist.dropna()) < 4 or not close.iloc[-1]:
        return None
    return _safe_float((hist.iloc[-1] - hist.iloc[-4]) / close.iloc[-1] * 100, 4)


def _atr_pct(hist: pd.DataFrame) -> float | None:
    if len(hist) < 15 or not {"High", "Low", "Close"}.issubset(hist.columns):
        return None
    high, low, close = hist["High"], hist["Low"], hist["Close"]
    prev_close = close.shift(1)
    tr = pd.concat([(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().dropna()
    if atr.empty or close.iloc[-1] <= 0:
        return None
    return _safe_float(atr.iloc[-1] / close.iloc[-1] * 100, 2)


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    base = close.iloc[-(days + 1)]
    if not base or base <= 0:
        return None
    return _safe_float(close.iloc[-1] / base - 1, 5)


def _feature_row(ticker: str, hist: pd.DataFrame, spy: pd.DataFrame, sector_meta: Mapping[str, Any]) -> dict[str, Any] | None:
    if hist.empty or len(hist) < 63 or "Close" not in hist.columns:
        return None
    close = pd.to_numeric(hist["Close"], errors="coerce").replace([math.inf, -math.inf], pd.NA).dropna()
    if len(close) < 63:
        return None
    volume = (
        pd.to_numeric(hist["Volume"], errors="coerce").replace([math.inf, -math.inf], pd.NA)
        if "Volume" in hist.columns
        else pd.Series(index=hist.index, dtype=float)
    )
    price = _safe_float(close.iloc[-1], 2)
    if price is None or price <= 0:
        return None

    def sma(period: int) -> float | None:
        if len(close) < period:
            return None
        return _safe_float(close.rolling(period).mean().iloc[-1], 4)

    sma20, sma50, sma200 = sma(20), sma(50), sma(200)
    valid_volume = volume[volume >= 0].dropna()
    avg_vol20 = (
        _safe_float(valid_volume.tail(20).mean(), 2)
        if len(valid_volume) >= 20
        else None
    )
    liquidity = pd.concat(
        [
            pd.to_numeric(hist["Close"], errors="coerce").rename("Close"),
            volume.rename("Volume"),
        ],
        axis=1,
    ).replace([math.inf, -math.inf], pd.NA)
    liquidity = liquidity[
        (liquidity["Close"] > 0) & (liquidity["Volume"] >= 0)
    ].dropna(subset=["Close", "Volume"])
    dollar_volume = liquidity["Close"] * liquidity["Volume"]
    avg_dollar_vol = (
        _safe_float(dollar_volume.tail(20).mean(), 0)
        if len(dollar_volume) >= 20
        else None
    )
    latest_volume = _safe_float(volume.reindex(close.index).iloc[-1], 4) if not close.empty else None
    rel_volume = (
        _safe_float(latest_volume / avg_vol20, 3)
        if latest_volume is not None and avg_vol20 is not None and avg_vol20 > 0
        else None
    )
    # 52 周高位要求接近一整年的真实样本（一年约 252 个交易日，留少量
    # 假日/数据源裁剪余量）。不足一年时宁可缺失，也不用短历史最高价冒充
    # ——那会给上市不久的标的打出字面不成立的「接近52周高位」。
    high_52w = _safe_float(close.tail(252).max(), 4) if len(close) >= _MIN_52W_HISTORY_BARS else None
    high_3m = _safe_float(close.tail(63).max(), 4)
    vol_price_match = compute_vol_price_match(hist)
    price_action = compute_price_action(hist)

    spy_close = spy["Close"] if not spy.empty and "Close" in spy.columns else pd.Series(dtype=float)
    stock_ret_63 = _ret(close, 63)
    spy_ret_63 = _safe_float(aligned_benchmark_return(close, spy_close, 63), 5)

    moving_average_states = [
        price > average
        for average in (sma20, sma50, sma200)
        if average is not None and average > 0
    ]
    ma_alignment = (
        sum(moving_average_states) / len(moving_average_states) * 100
        if moving_average_states
        else None
    )

    return {
        "ticker": ticker,
        "name": get_zh_name(ticker) or ticker,
        "sector_id": sector_meta.get("sector_id"),
        "sector_name": sector_meta.get("sector_name"),
        "primary_sector_id": sector_meta.get("primary_sector_id") or sector_meta.get("sector_id"),
        "primary_sector_name": sector_meta.get("primary_sector_name") or sector_meta.get("sector_name"),
        "theme_ids": list(sector_meta.get("theme_ids") or ([sector_meta.get("sector_id")] if sector_meta.get("sector_id") else [])),
        "theme_names": list(sector_meta.get("theme_names") or ([sector_meta.get("sector_name")] if sector_meta.get("sector_name") else [])),
        "price": price,
        "change_pct": _safe_float((close.iloc[-1] / close.iloc[-2] - 1) * 100, 2) if len(close) > 1 and close.iloc[-2] else None,
        "return_5d": _ret(close, 5),
        "return_20d": _ret(close, 20),
        "return_63d": stock_ret_63,
        "return_126d": _ret(close, 126),
        "return_252d": _ret(close, 252),
        "rs_spy_63d": _safe_float((stock_ret_63 - spy_ret_63), 5) if stock_ret_63 is not None and spy_ret_63 is not None else None,
        "dist_sma20": _safe_float((price / sma20 - 1), 5) if sma20 else None,
        "dist_sma50": _safe_float((price / sma50 - 1), 5) if sma50 else None,
        "dist_sma200": _safe_float((price / sma200 - 1), 5) if sma200 else None,
        "above_sma20": (price > sma20) if sma20 is not None else None,
        "above_sma50": (price > sma50) if sma50 is not None else None,
        "above_sma200": (price > sma200) if sma200 is not None else None,
        "ma_alignment": _safe_float(ma_alignment, 2),
        "rsi14": _rsi(close),
        "macd_direction": _macd_direction(close),
        "atr_pct": _atr_pct(hist),
        "rel_volume": rel_volume,
        "avg_volume_20d": int(avg_vol20) if avg_vol20 is not None else None,
        "avg_dollar_volume_20d": avg_dollar_vol,
        "avg_dollar_volume_20d_calculation_method": (
            "mean_close_times_volume_20d" if avg_dollar_vol is not None else "unavailable"
        ),
        "calculation_method": {
            "average_dollar_volume_20d": (
                "mean_close_times_volume_20d" if avg_dollar_vol is not None else "unavailable"
            )
        },
        "ath_proximity": _safe_float(price / high_52w * 100, 2) if high_52w else None,
        "drawdown_3m": _safe_float((price / high_3m - 1) * 100, 2) if high_3m else None,
        "near_3m_high": bool(high_3m and price >= high_3m * 0.985),
        "breakout_confirmed": bool(high_3m and price >= high_3m * 0.995 and (rel_volume or 0) >= 1.15),
        "follow_through": bool(len(close) >= 5 and close.tail(3).min() >= close.tail(20).mean()),
        "vol_price_match": vol_price_match,
        "volume_truth": vol_price_match,
        "price_action": price_action,
        "history_days": len(close),
    }


__all__ = [
    "_MIN_52W_HISTORY_BARS",
    "_atr_pct",
    "_feature_row",
    "_macd_direction",
    "_ret",
    "_rsi",
    "_safe_float",
]
