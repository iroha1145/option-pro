from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.strength.relative_spreads import compute_spread_matrix
from app.services.strength.market_shape import (
    MarketShapeHysteresisConfig,
    build_market_shape,
)

MARKET_BENCHMARKS = (
    "SPY", "QQQ", "IWM", "RSP", "^VIX", "HYG", "IEF", "TLT", "^TNX", "GLD",
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
    "SOXX", "SMH",
)

SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB")

# Core requirements are expressed by evidence, not by requiring every proxy.
# In particular, either RSP or IWM can satisfy the breadth proxy requirement.
MINIMUM_HISTORY = {"SPY": 220, "QQQ": 21}
_NY = ZoneInfo("America/New_York")


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _clamp(value: float | int | None, lo: float = 0.0, hi: float = 100.0, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _close(df: pd.DataFrame) -> pd.Series:
    return df["Close"].dropna() if not df.empty and "Close" in df.columns else pd.Series(dtype=float)


def _volume(df: pd.DataFrame) -> pd.Series:
    return df["Volume"].dropna() if not df.empty and "Volume" in df.columns else pd.Series(dtype=float)


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    base = close.iloc[-(days + 1)]
    if not base or base <= 0:
        return None
    return _safe_float(close.iloc[-1] / base - 1, 5)


def _above_sma(close: pd.Series, period: int) -> bool:
    if len(close) < period:
        return False
    sma = close.rolling(period).mean().iloc[-1]
    return bool(sma and close.iloc[-1] > sma)


def _above_sma_value(close: pd.Series, period: int) -> bool | None:
    if len(close) < period:
        return None
    sma = close.rolling(period).mean().iloc[-1]
    if not math.isfinite(float(sma)):
        return None
    return bool(close.iloc[-1] > sma)


def _sma_slope_up(close: pd.Series, period: int = 200, lookback: int = 20) -> bool:
    if len(close) < period + lookback:
        return False
    sma = close.rolling(period).mean()
    current = sma.iloc[-1]
    previous = sma.iloc[-lookback]
    return bool(current and previous and current > previous)


def _sma_slope_up_value(
    close: pd.Series,
    period: int = 200,
    lookback: int = 20,
) -> bool | None:
    if len(close) < period + lookback:
        return None
    sma = close.rolling(period).mean()
    current = _safe_float(sma.iloc[-1], 8)
    previous = _safe_float(sma.iloc[-lookback], 8)
    if current is None or previous is None:
        return None
    return current > previous


def _relative_return(left: pd.Series, right: pd.Series, days: int) -> float | None:
    left_ret = _ret(left, days)
    right_ret = _ret(right, days)
    if left_ret is None or right_ret is None:
        return None
    return _safe_float(left_ret - right_ret, 5)


def _rvol(df: pd.DataFrame, period: int = 20) -> float | None:
    volume = _volume(df)
    if len(volume) < period + 1:
        return None
    avg = volume.iloc[-period - 1:-1].mean()
    if not avg or avg <= 0:
        return None
    return _safe_float(volume.iloc[-1] / avg, 3)


def _percentile(series: pd.Series, value: float | None) -> float | None:
    if value is None:
        return None
    clean = series.dropna()
    if clean.empty:
        return None
    return _safe_float((clean <= value).mean() * 100, 1)


def _drawdown_from_high(close: pd.Series, days: int) -> float | None:
    if len(close) < 2:
        return None
    recent = close.tail(days)
    if recent.empty:
        return None
    high = recent.max()
    if not high or high <= 0:
        return None
    return _safe_float(close.iloc[-1] / high - 1, 5)


def _score_signed_pct(
    value: float | None,
    scale: float,
    neutral: float = 50.0,
) -> float | None:
    if value is None:
        return None
    return _clamp(neutral + (value * 100.0 * scale))


def _weighted_available(
    values: dict[str, float | None],
    weights: dict[str, float],
) -> float | None:
    active = {
        name: float(value)
        for name, value in values.items()
        if value is not None and weights.get(name, 0.0) > 0
    }
    denominator = sum(weights[name] for name in active)
    if denominator <= 0:
        return None
    return sum(value * weights[name] for name, value in active.items()) / denominator


def _compute_trend_score(
    closes: dict[str, pd.Series],
) -> tuple[float | None, dict[str, Any]]:
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    iwm = closes.get("IWM", pd.Series(dtype=float))
    rsp = closes.get("RSP", pd.Series(dtype=float))
    components = {
        "spy_above_sma50": _above_sma_value(spy, 50),
        "spy_above_sma200": _above_sma_value(spy, 200),
        "qqq_above_sma50": _above_sma_value(qqq, 50),
        "qqq_above_sma200": _above_sma_value(qqq, 200),
        "iwm_above_sma50": _above_sma_value(iwm, 50),
        "rsp_above_sma50": _above_sma_value(rsp, 50),
        "spy_sma200_slope_up": _sma_slope_up_value(spy, 200, 20),
    }
    weights = {
        "spy_above_sma50": .20,
        "spy_above_sma200": .20,
        "qqq_above_sma50": .15,
        "qqq_above_sma200": .15,
        "iwm_above_sma50": .10,
        "rsp_above_sma50": .10,
        "spy_sma200_slope_up": .10,
    }
    score = _weighted_available(
        {
            key: (100.0 if value else 0.0) if value is not None else None
            for key, value in components.items()
        },
        weights,
    )
    return (round(_clamp(score), 1) if score is not None else None), components


def _compute_momentum_score(closes: dict[str, pd.Series]) -> tuple[float | None, dict[str, Any]]:
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    iwm = closes.get("IWM", pd.Series(dtype=float))
    rsp = closes.get("RSP", pd.Series(dtype=float))
    spy_20d = _ret(spy, 20)
    qqq_20d = _ret(qqq, 20)
    iwm_20d = _ret(iwm, 20)
    qqq_spy_20d = _relative_return(qqq, spy, 20)
    iwm_spy_20d = _relative_return(iwm, spy, 20)
    rsp_spy_20d = _relative_return(rsp, spy, 20)
    score = _weighted_available(
        {
            "spy": _score_signed_pct(spy_20d, 2.8),
            "qqq": _score_signed_pct(qqq_20d, 2.5),
            "iwm": _score_signed_pct(iwm_20d, 2.0),
            "qqq_spy": _score_signed_pct(qqq_spy_20d, 5.0),
            "rsp_spy": _score_signed_pct(rsp_spy_20d, 5.0),
        },
        {"spy": .35, "qqq": .30, "iwm": .15, "qqq_spy": .10, "rsp_spy": .10},
    )
    components = {
        "spy_20d": _safe_float((spy_20d or 0) * 100, 2) if spy_20d is not None else None,
        "qqq_20d": _safe_float((qqq_20d or 0) * 100, 2) if qqq_20d is not None else None,
        "iwm_20d": _safe_float((iwm_20d or 0) * 100, 2) if iwm_20d is not None else None,
        "qqq_spy_20d": _safe_float((qqq_spy_20d or 0) * 100, 2) if qqq_spy_20d is not None else None,
        "iwm_spy_20d": _safe_float((iwm_spy_20d or 0) * 100, 2) if iwm_spy_20d is not None else None,
        "rsp_spy_20d": _safe_float((rsp_spy_20d or 0) * 100, 2) if rsp_spy_20d is not None else None,
    }
    return round(_clamp(score), 1) if score is not None else None, components


def _compute_volume_score(index_data: dict[str, pd.DataFrame], closes: dict[str, pd.Series]) -> tuple[float | None, dict[str, Any]]:
    spy_ret5 = _ret(closes.get("SPY", pd.Series(dtype=float)), 5)
    qqq_ret5 = _ret(closes.get("QQQ", pd.Series(dtype=float)), 5)
    spy_rvol = _rvol(index_data.get("SPY", pd.DataFrame()))
    qqq_rvol = _rvol(index_data.get("QQQ", pd.DataFrame()))
    score = 50.0
    evidence_count = 0
    if spy_ret5 is not None and spy_rvol is not None:
        evidence_count += 1
        if spy_ret5 > 0 and spy_rvol > 1.1:
            score += 15
        elif spy_ret5 < 0 and spy_rvol > 1.2:
            score -= 20
        elif spy_ret5 > 0:
            score += 6
    if qqq_ret5 is not None and qqq_rvol is not None:
        evidence_count += 1
        if qqq_ret5 > 0 and qqq_rvol > 1.1:
            score += 10
        elif qqq_ret5 < 0 and qqq_rvol > 1.2:
            score -= 15
        elif qqq_ret5 > 0:
            score += 4
    return (round(_clamp(score), 1) if evidence_count else None), {
        "spy_5d": _safe_float((spy_ret5 or 0) * 100, 2) if spy_ret5 is not None else None,
        "qqq_5d": _safe_float((qqq_ret5 or 0) * 100, 2) if qqq_ret5 is not None else None,
        "spy_rvol": spy_rvol,
        "qqq_rvol": qqq_rvol,
    }


def _compute_breadth_score(closes: dict[str, pd.Series]) -> tuple[float | None, dict[str, Any]]:
    sector_closes = [closes.get(symbol, pd.Series(dtype=float)) for symbol in SECTOR_ETFS]
    above_50 = [close for close in sector_closes if _above_sma(close, 50)]
    above_200 = [close for close in sector_closes if _above_sma(close, 200)]
    valid_count = sum(1 for close in sector_closes if len(close) >= 50)
    valid_200_count = sum(1 for close in sector_closes if len(close) >= 200)
    above_50_pct = len(above_50) / valid_count * 100 if valid_count else None
    above_200_pct = len(above_200) / valid_200_count * 100 if valid_200_count else None

    spy = closes.get("SPY", pd.Series(dtype=float))
    rsp = closes.get("RSP", pd.Series(dtype=float))
    iwm = closes.get("IWM", pd.Series(dtype=float))
    rsp_spy_20d = _relative_return(rsp, spy, 20)
    iwm_spy_20d = _relative_return(iwm, spy, 20)
    score = _weighted_available(
        {
            "sector_50": above_50_pct,
            "sector_200": above_200_pct,
            "rsp_spy": _score_signed_pct(rsp_spy_20d, 5.0),
            "iwm_spy": _score_signed_pct(iwm_spy_20d, 5.0),
        },
        {"sector_50": .40, "sector_200": .25, "rsp_spy": .20, "iwm_spy": .15},
    )
    return (round(_clamp(score), 1) if score is not None else None), {
        "sectors_above_50dma": _safe_float(above_50_pct, 1),
        "sectors_above_200dma": _safe_float(above_200_pct, 1),
        "sector_50dma_coverage": valid_count,
        "sector_200dma_coverage": valid_200_count,
        "rsp_spy_20d": _safe_float((rsp_spy_20d or 0) * 100, 2) if rsp_spy_20d is not None else None,
        "iwm_spy_20d": _safe_float((iwm_spy_20d or 0) * 100, 2) if iwm_spy_20d is not None else None,
    }


def _compute_risk_appetite_score(
    closes: dict[str, pd.Series],
) -> tuple[float | None, float, dict[str, Any], dict[str, bool]]:
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    vix = closes.get("^VIX", pd.Series(dtype=float))
    hyg = closes.get("HYG", pd.Series(dtype=float))
    ief = closes.get("IEF", pd.Series(dtype=float))
    tlt = closes.get("TLT", pd.Series(dtype=float))
    tnx = closes.get("^TNX", pd.Series(dtype=float))

    vix_last = _safe_float(vix.iloc[-1], 2) if len(vix) else None
    vix_percentile = _percentile(vix.tail(252), vix_last) if len(vix) else None
    credit_tlt_20d = _relative_return(hyg, tlt, 20)
    credit_ief_20d = _relative_return(hyg, ief, 20)
    rate_20d_change = _safe_float(tnx.iloc[-1] - tnx.iloc[-21], 4) if len(tnx) > 20 else None
    duration_20d = _relative_return(ief, tlt, 20)
    spy_dd50 = _drawdown_from_high(spy, 50)
    qqq_dd50 = _drawdown_from_high(qqq, 50)

    components = {
        "vix_level": (
            _clamp(76.0 - (vix_last - 12.0) * 2.4)
            if vix_last is not None
            else None
        ),
        "vix_percentile": (
            _clamp(100.0 - vix_percentile)
            if vix_percentile is not None
            else None
        ),
        "credit_hyg_tlt": _score_signed_pct(credit_tlt_20d, 4.0),
        "credit_hyg_ief": _score_signed_pct(credit_ief_20d, 4.0),
        "rates_tnx": (
            _clamp(50.0 - rate_20d_change * 20.0)
            if rate_20d_change is not None
            else None
        ),
        "rates_ief_tlt": (
            _clamp(50.0 - duration_20d * 100.0 * 5.0)
            if duration_20d is not None
            else None
        ),
        "spy_drawdown": (
            _clamp(100.0 + min(spy_dd50, 0.0) * 100.0 * 4.0)
            if spy_dd50 is not None
            else None
        ),
        "qqq_drawdown": (
            _clamp(100.0 + min(qqq_dd50, 0.0) * 100.0 * 3.2)
            if qqq_dd50 is not None
            else None
        ),
    }
    score = _weighted_available(
        components,
        {
            "vix_level": .18,
            "vix_percentile": .07,
            "credit_hyg_tlt": .15,
            "credit_hyg_ief": .10,
            "rates_tnx": .10,
            "rates_ief_tlt": .10,
            "spy_drawdown": .18,
            "qqq_drawdown": .12,
        },
    )

    penalty = 0.0
    if vix_last is not None and vix_last > 25:
        penalty += min(10, (vix_last - 25) * .8)
    if vix_percentile is not None and vix_percentile >= 80:
        penalty += min(8, (vix_percentile - 80) * .25)
    weakest_credit = min(
        (value for value in (credit_tlt_20d, credit_ief_20d) if value is not None),
        default=None,
    )
    if weakest_credit is not None and weakest_credit < -0.025:
        penalty += min(8, abs(weakest_credit) * 160)
    if rate_20d_change is not None and rate_20d_change > .25:
        penalty += min(5, rate_20d_change * 4)
    if spy_dd50 is not None and spy_dd50 < -0.08:
        penalty += min(8, abs(spy_dd50) * 70)
    if qqq_dd50 is not None and qqq_dd50 < -0.10:
        penalty += min(6, abs(qqq_dd50) * 50)

    evidence = {
        "vix": vix_last,
        "vix_percentile": vix_percentile,
        "hyg_tlt_20d": _safe_float(credit_tlt_20d * 100, 2) if credit_tlt_20d is not None else None,
        "hyg_ief_20d": _safe_float(credit_ief_20d * 100, 2) if credit_ief_20d is not None else None,
        "yield_10y": _safe_float(tnx.iloc[-1], 2) if len(tnx) else None,
        "yield_10y_20d_change": rate_20d_change,
        "ief_tlt_20d": _safe_float(duration_20d * 100, 2) if duration_20d is not None else None,
        "spy_drawdown_50d": _safe_float((spy_dd50 or 0) * 100, 2) if spy_dd50 is not None else None,
        "qqq_drawdown_50d": _safe_float((qqq_dd50 or 0) * 100, 2) if qqq_dd50 is not None else None,
        "component_scores": {
            key: _safe_float(value, 2) for key, value in components.items()
        },
    }
    groups = {
        "volatility": vix_last is not None or vix_percentile is not None,
        "credit": credit_tlt_20d is not None or credit_ief_20d is not None,
        "rates": rate_20d_change is not None or duration_20d is not None,
    }
    if not any(groups.values()):
        score = None
    return (
        round(_clamp(score), 1) if score is not None else None,
        round(_clamp(penalty, 0, 30, 0), 1),
        evidence,
        groups,
    )


def _rules_for_score(
    score: float,
    breadth_score: float,
    risk_penalty: float,
    risk_on_score: float | None,
) -> tuple[dict[str, float], list[str]]:
    warnings: list[str] = []
    if score >= 75:
        rules = {
            "momentum_weight_multiplier": 1.10,
            "relative_strength_weight_multiplier": 1.00,
            "long_trend_weight_multiplier": 1.00,
            "breakout_weight_multiplier": 1.15,
            "sector_strength_weight_multiplier": 1.10,
            "option_heat_weight_multiplier": 1.00,
            "risk_penalty_multiplier": 1.00,
        }
    elif score >= 60:
        rules = {
            "momentum_weight_multiplier": 1.00,
            "relative_strength_weight_multiplier": 1.05,
            "long_trend_weight_multiplier": 1.05,
            "breakout_weight_multiplier": .95,
            "sector_strength_weight_multiplier": 1.03,
            "option_heat_weight_multiplier": .90,
            "risk_penalty_multiplier": 1.10,
        }
    elif score >= 40:
        rules = {
            "momentum_weight_multiplier": .90,
            "relative_strength_weight_multiplier": 1.12,
            "long_trend_weight_multiplier": 1.12,
            "breakout_weight_multiplier": .75,
            "sector_strength_weight_multiplier": 1.00,
            "option_heat_weight_multiplier": .80,
            "risk_penalty_multiplier": 1.20,
        }
    else:
        rules = {
            "momentum_weight_multiplier": .72,
            "relative_strength_weight_multiplier": 1.18,
            "long_trend_weight_multiplier": 1.25,
            "breakout_weight_multiplier": .50,
            "sector_strength_weight_multiplier": .92,
            "option_heat_weight_multiplier": .60,
            "risk_penalty_multiplier": 1.50,
        }
    if breadth_score < 45:
        rules["breakout_weight_multiplier"] *= .85
        warnings.append("市场宽度偏弱，突破型信号已降权")
    if risk_penalty >= 10:
        rules["option_heat_weight_multiplier"] *= .85
        warnings.append("波动或信用压力偏高，期权热度已降权")
    if risk_on_score is not None and risk_on_score < 45:
        rules["breakout_weight_multiplier"] *= .85
        rules["option_heat_weight_multiplier"] *= .85
        warnings.append("风险偏好价差偏弱，突破与期权信号已降权")
    elif score >= 65 and risk_on_score is not None and risk_on_score >= 70:
        rules["breakout_weight_multiplier"] *= 1.08
        rules["sector_strength_weight_multiplier"] *= 1.08
        warnings.append("风险偏好价差支持进攻型强势股")
    return {key: round(value, 3) for key, value in rules.items()}, warnings


def _core_missing(
    closes: dict[str, pd.Series],
    *,
    trend: dict[str, Any],
    momentum: dict[str, Any],
    breadth: dict[str, Any],
) -> list[str]:
    missing: list[str] = []
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    if len(spy) < MINIMUM_HISTORY["SPY"]:
        missing.append("spy_long_trend")
    if trend.get("spy_above_sma200") is None or trend.get("spy_sma200_slope_up") is None:
        missing.append("spy_200d_structure")
    if momentum.get("spy_20d") is None:
        missing.append("spy_momentum")
    qqq_available = (
        momentum.get("qqq_20d") is not None
        or trend.get("qqq_above_sma50") is not None
        or trend.get("qqq_above_sma200") is not None
    )
    if len(qqq) < MINIMUM_HISTORY["QQQ"] or not qqq_available:
        missing.append("qqq_trend_or_momentum")
    if breadth.get("rsp_spy_20d") is None and breadth.get("iwm_spy_20d") is None:
        missing.append("rsp_or_iwm_breadth")
    return list(dict.fromkeys(missing))


def _input_coverage(
    *,
    hard_missing: list[str],
    volume_score: float | None,
    risk_groups: dict[str, bool],
    risk_on_spread_score: float | None,
) -> dict[str, Any]:
    groups = {
        "core_trend": not any(item.startswith("spy_") for item in hard_missing),
        "core_momentum": not any(
            item in {"spy_momentum", "qqq_trend_or_momentum"}
            for item in hard_missing
        ),
        "core_breadth": "rsp_or_iwm_breadth" not in hard_missing,
        "market_volume": volume_score is not None,
        "volatility": bool(risk_groups.get("volatility")),
        "credit": bool(risk_groups.get("credit")),
        "rates": bool(risk_groups.get("rates")),
        "risk_on_spreads": risk_on_spread_score is not None,
    }
    weights = {
        "core_trend": .25,
        "core_momentum": .20,
        "core_breadth": .20,
        "market_volume": .10,
        "volatility": .08,
        "credit": .07,
        "rates": .05,
        "risk_on_spreads": .05,
    }
    available_weight = sum(weight for key, weight in weights.items() if groups[key])
    return {
        "ratio": round(available_weight / sum(weights.values()), 4),
        "available_weight": round(available_weight, 4),
        "configured_weight": round(sum(weights.values()), 4),
        "groups": groups,
    }


def _compute_market_regime_snapshot(
    index_data: dict[str, pd.DataFrame],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    closes = {symbol: _close(frame) for symbol, frame in index_data.items()}
    trend_score, trend = _compute_trend_score(closes)
    momentum_score, momentum = _compute_momentum_score(closes)
    volume_score, volume = _compute_volume_score(index_data, closes)
    breadth_score, breadth = _compute_breadth_score(closes)
    risk_appetite_score, risk_penalty, risk, risk_groups = _compute_risk_appetite_score(closes)
    spread_matrix = compute_spread_matrix(index_data)
    risk_on_spread_score = _safe_float(spread_matrix.get("score"), 1)

    hard_missing = _core_missing(
        closes,
        trend=trend,
        momentum=momentum,
        breadth=breadth,
    )
    if trend_score is None:
        hard_missing.append("market_trend")
    if momentum_score is None:
        hard_missing.append("market_momentum")
    if breadth_score is None:
        hard_missing.append("market_breadth")
    hard_missing = list(dict.fromkeys(hard_missing))

    optional_missing: list[str] = []
    if volume_score is None:
        optional_missing.append("market_volume")
    for group in ("volatility", "credit", "rates"):
        if not risk_groups.get(group):
            optional_missing.append(group)
    if spread_matrix.get("status") != "active" or risk_on_spread_score is None:
        optional_missing.append("risk_on_spreads")
    if int(breadth.get("sector_50dma_coverage") or 0) < 6:
        optional_missing.append("sector_breadth_50dma")
    if int(breadth.get("sector_200dma_coverage") or 0) < 6:
        optional_missing.append("sector_breadth_200dma")
    optional_missing = list(dict.fromkeys(optional_missing))

    coverage = _input_coverage(
        hard_missing=hard_missing,
        volume_score=volume_score,
        risk_groups=risk_groups,
        risk_on_spread_score=(
            risk_on_spread_score
            if spread_matrix.get("status") == "active"
            else None
        ),
    )
    active_groups = [
        name for name, active in coverage["groups"].items() if active
    ]
    degraded_reasons = [f"missing_optional:{name}" for name in optional_missing]
    if hard_missing:
        degraded_reasons = [f"missing_core:{name}" for name in hard_missing]

    aggregate = _weighted_available(
        {
            "trend": trend_score,
            "momentum": momentum_score,
            "breadth": breadth_score,
            "volume": volume_score,
            "risk_on": risk_on_spread_score,
            "risk_appetite": risk_appetite_score,
        },
        {
            "trend": .30,
            "momentum": .20,
            "breadth": .20,
            "volume": .10,
            "risk_on": .10,
            "risk_appetite": .10,
        },
    )
    partial_score = (
        round(_clamp(aggregate - risk_penalty * .35), 1)
        if aggregate is not None
        else None
    )
    score = None if hard_missing else partial_score
    if score is None:
        label = "数据不足"
    elif score >= 75:
        label = "强风险偏好"
    elif score >= 60:
        label = "温和偏强"
    elif score >= 40:
        label = "中性震荡"
    else:
        label = "弱势高风险"

    if score is not None and breadth_score is not None:
        rules, warnings = _rules_for_score(
            score,
            float(breadth_score),
            risk_penalty,
            risk_on_spread_score,
        )
    else:
        rules = {}
        warnings = ["市场核心行情不足，未生成正式环境分数"]
    if optional_missing and not hard_missing:
        warnings.append("可选市场数据不完整：" + ",".join(optional_missing))
    warnings.extend(str(item) for item in list(spread_matrix.get("warnings") or []))

    status = (
        "insufficient_data"
        if hard_missing
        else "degraded"
        if optional_missing
        else "active"
    )
    soxx_xlk = spread_matrix.get("spreads", {}).get("soxx_xlk", {})
    sector_flow_score = (
        _safe_float(soxx_xlk.get("score"), 1)
        if soxx_xlk.get("status") == "active"
        else None
    )
    trend_momentum_score = _weighted_available(
        {"trend": trend_score, "momentum": momentum_score},
        {"trend": .58, "momentum": .42},
    )
    payload = {
        "as_of": observed_at.astimezone(timezone.utc).isoformat(),
        "status": status,
        "score": score,
        "partial_score": partial_score,
        "label": label,
        "index_trend_score": trend_score,
        "market_momentum_score": momentum_score,
        "market_breadth_score": breadth_score,
        "market_volume_score": volume_score,
        "risk_appetite_score": risk_appetite_score,
        "risk_on_spread_score": risk_on_spread_score,
        "risk_on_spread_label": spread_matrix.get("label"),
        "market_risk_penalty": risk_penalty,
        "rules": rules,
        "warnings": list(dict.fromkeys(warnings))[:8],
        "missing_requirements": [*hard_missing, *optional_missing],
        "hard_missing": hard_missing,
        "optional_missing": optional_missing,
        "input_coverage": coverage,
        "active_groups": active_groups,
        "degraded_reasons": degraded_reasons,
        "trend": trend,
        "momentum": momentum,
        "volume": volume,
        "breadth": breadth,
        "risk": risk,
        "spread_matrix": spread_matrix.get("spreads", {}),
        "market_context": {
            "status": status,
            "score": score,
            "partial_score": partial_score,
            "label": label,
            "trend_momentum_score": (
                round(trend_momentum_score, 1)
                if trend_momentum_score is not None
                else None
            ),
            "breadth_score": breadth_score,
            "risk_on_spread_score": risk_on_spread_score,
            "liquidity_credit_score": risk_appetite_score,
            "sentiment_score": risk_appetite_score,
            "sector_flow_score": sector_flow_score,
            "valuation_status": "not_available",
            "valuation_risk_penalty": 0.0,
        },
        "spy_20d": momentum.get("spy_20d"),
        "qqq_20d": momentum.get("qqq_20d"),
        "iwm_20d": momentum.get("iwm_20d"),
        "spy_above_sma200": trend.get("spy_above_sma200"),
        "vix": risk.get("vix"),
    }
    return payload


def _daily_replay_as_of(index_value: Any, fallback: datetime, offset: int) -> datetime:
    if isinstance(index_value, (pd.Timestamp, datetime)):
        timestamp = pd.Timestamp(index_value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(_NY)
        else:
            timestamp = timestamp.tz_convert(_NY)
        from app.services.market_calendar import early_close_minutes as _early_close_minutes

        close_minutes = _early_close_minutes(timestamp.date()) or 16 * 60
        timestamp = timestamp.normalize() + pd.Timedelta(minutes=close_minutes)
        return timestamp.tz_convert(timezone.utc).to_pydatetime()
    return fallback - timedelta(days=offset)


def _slice_replay_frame(
    frame: pd.DataFrame,
    *,
    cutoff: Any,
    remaining: int,
) -> pd.DataFrame:
    if frame.empty:
        return frame.copy()
    if isinstance(frame.index, pd.DatetimeIndex) and isinstance(
        cutoff, (pd.Timestamp, datetime)
    ):
        timestamp = pd.Timestamp(cutoff)
        if frame.index.tz is None and timestamp.tzinfo is not None:
            timestamp = timestamp.tz_localize(None)
        elif frame.index.tz is not None and timestamp.tzinfo is None:
            timestamp = timestamp.tz_localize(frame.index.tz)
        elif frame.index.tz is not None:
            timestamp = timestamp.tz_convert(frame.index.tz)
        return frame.loc[frame.index <= timestamp].copy()
    end = max(0, len(frame) - remaining)
    return frame.iloc[:end].copy()


def _market_shape_history(
    index_data: dict[str, pd.DataFrame],
    *,
    observed_at: datetime,
    config: MarketShapeHysteresisConfig,
) -> list[dict[str, Any]]:
    spy_frame = index_data.get("SPY", pd.DataFrame())
    if spy_frame.empty:
        return []
    prior_count = min(config.history_days - 1, max(0, len(spy_frame) - 1))
    history: list[dict[str, Any]] = []
    for remaining in range(prior_count, 0, -1):
        position = len(spy_frame) - remaining - 1
        cutoff = spy_frame.index[position]
        replay_at = _daily_replay_as_of(cutoff, observed_at, remaining)
        bounded = {
            symbol: _slice_replay_frame(
                frame,
                cutoff=cutoff,
                remaining=remaining,
            )
            for symbol, frame in index_data.items()
        }
        snapshot = _compute_market_regime_snapshot(
            bounded,
            observed_at=replay_at,
        )
        history.append({"as_of": replay_at, "regime": snapshot})
    return history


def _bound_index_data_as_of(
    index_data: dict[str, pd.DataFrame],
    observed_at: datetime,
) -> dict[str, pd.DataFrame]:
    from app.services.market_calendar import (
        early_close_minutes as _early_close_minutes,
        is_trading_day as _is_trading_day,
    )

    local_observed = observed_at.astimezone(_NY)
    completed_date = local_observed.date()
    close_minutes = _early_close_minutes(completed_date) or 16 * 60
    before_close = (
        local_observed.hour * 60 + local_observed.minute < close_minutes
    )
    if before_close or not _is_trading_day(completed_date):
        completed_date -= timedelta(days=1)
        while not _is_trading_day(completed_date):
            completed_date -= timedelta(days=1)

    bounded: dict[str, pd.DataFrame] = {}
    for symbol, frame in index_data.items():
        if frame.empty or not isinstance(frame.index, pd.DatetimeIndex):
            bounded[symbol] = frame.copy()
            continue
        dates = (
            pd.Index(frame.index.date)
            if frame.index.tz is None
            else pd.Index(frame.index.tz_convert(_NY).date)
        )
        bounded[symbol] = frame.loc[dates <= completed_date].copy()
    return bounded


def compute_market_regime(
    index_data: dict[str, pd.DataFrame],
    *,
    as_of: datetime | None = None,
) -> dict[str, Any]:
    observed_at = as_of or datetime.now(timezone.utc)
    if observed_at.tzinfo is None or observed_at.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    config = MarketShapeHysteresisConfig.from_env()
    bounded_data = _bound_index_data_as_of(index_data, observed_at)
    spy_frame = bounded_data.get("SPY", pd.DataFrame())
    shape_as_of = observed_at
    if not spy_frame.empty and isinstance(spy_frame.index, pd.DatetimeIndex):
        # Shape transitions belong to the last completed daily session, not
        # the time an API request happens on a weekend or before the open.
        shape_as_of = _daily_replay_as_of(spy_frame.index[-1], observed_at, 0)
    payload = _compute_market_regime_snapshot(
        bounded_data,
        observed_at=shape_as_of,
    )
    payload["requested_at"] = observed_at.astimezone(timezone.utc).isoformat()
    history = _market_shape_history(
        bounded_data,
        observed_at=shape_as_of,
        config=config,
    )
    shape = build_market_shape(
        payload,
        as_of=shape_as_of,
        history=history,
        config=config,
    )
    history_truncated = len(spy_frame) > config.history_days
    first_replay_at = history[0]["as_of"] if history else shape_as_of
    first_replay_iso = (
        first_replay_at.isoformat()
        if isinstance(first_replay_at, datetime)
        else str(first_replay_at)
    )
    state_age_is_lower_bound = bool(
        history_truncated
        and shape.get("entered_at")
        and str(shape["entered_at"]) == first_replay_iso
    )
    shape["history_truncated"] = history_truncated
    shape["days_in_state_is_lower_bound"] = state_age_is_lower_bound
    shape["state_age_semantics"] = (
        "lower_bound_from_replay_window"
        if state_age_is_lower_bound
        else "exact_within_replay_window"
    )
    payload["market_shape"] = shape
    return payload
