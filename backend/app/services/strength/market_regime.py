from __future__ import annotations

import math
from typing import Any

import pandas as pd

from app.services.strength.relative_spreads import compute_spread_matrix

MARKET_BENCHMARKS = (
    "SPY", "QQQ", "IWM", "RSP", "^VIX", "HYG", "IEF", "TLT", "^TNX", "GLD",
    "XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB",
    "SOXX", "SMH",
)

SECTOR_ETFS = ("XLK", "XLF", "XLV", "XLE", "XLI", "XLC", "XLY", "XLP", "XLU", "XLRE", "XLB")

# A regime score is only meaningful when its core trend and breadth inputs are
# present. Optional spreads/sectors may degrade to neutral, but these may not.
MINIMUM_HISTORY = {"SPY": 220, "QQQ": 200, "IWM": 50, "RSP": 50}


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


def _sma_slope_up(close: pd.Series, period: int = 200, lookback: int = 20) -> bool:
    if len(close) < period + lookback:
        return False
    sma = close.rolling(period).mean()
    current = sma.iloc[-1]
    previous = sma.iloc[-lookback]
    return bool(current and previous and current > previous)


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


def _score_signed_pct(value: float | None, scale: float, neutral: float = 50.0) -> float:
    if value is None:
        return neutral
    return _clamp(neutral + (value * 100.0 * scale))


def _compute_trend_score(closes: dict[str, pd.Series]) -> tuple[float, dict[str, Any]]:
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    iwm = closes.get("IWM", pd.Series(dtype=float))
    rsp = closes.get("RSP", pd.Series(dtype=float))
    components = {
        "spy_above_sma50": _above_sma(spy, 50),
        "spy_above_sma200": _above_sma(spy, 200),
        "qqq_above_sma50": _above_sma(qqq, 50),
        "qqq_above_sma200": _above_sma(qqq, 200),
        "iwm_above_sma50": _above_sma(iwm, 50),
        "rsp_above_sma50": _above_sma(rsp, 50),
        "spy_sma200_slope_up": _sma_slope_up(spy, 200, 20),
    }
    score = (
        (20 if components["spy_above_sma50"] else 0) +
        (20 if components["spy_above_sma200"] else 0) +
        (15 if components["qqq_above_sma50"] else 0) +
        (15 if components["qqq_above_sma200"] else 0) +
        (10 if components["iwm_above_sma50"] else 0) +
        (10 if components["rsp_above_sma50"] else 0) +
        (10 if components["spy_sma200_slope_up"] else 0)
    )
    return round(_clamp(score), 1), components


def _compute_momentum_score(closes: dict[str, pd.Series]) -> tuple[float, dict[str, Any]]:
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
    score = (
        _score_signed_pct(spy_20d, 2.8) * .35 +
        _score_signed_pct(qqq_20d, 2.5) * .30 +
        _score_signed_pct(iwm_20d, 2.0) * .15 +
        _score_signed_pct(qqq_spy_20d, 5.0) * .10 +
        _score_signed_pct(rsp_spy_20d, 5.0) * .10
    )
    components = {
        "spy_20d": _safe_float((spy_20d or 0) * 100, 2) if spy_20d is not None else None,
        "qqq_20d": _safe_float((qqq_20d or 0) * 100, 2) if qqq_20d is not None else None,
        "iwm_20d": _safe_float((iwm_20d or 0) * 100, 2) if iwm_20d is not None else None,
        "qqq_spy_20d": _safe_float((qqq_spy_20d or 0) * 100, 2) if qqq_spy_20d is not None else None,
        "iwm_spy_20d": _safe_float((iwm_spy_20d or 0) * 100, 2) if iwm_spy_20d is not None else None,
        "rsp_spy_20d": _safe_float((rsp_spy_20d or 0) * 100, 2) if rsp_spy_20d is not None else None,
    }
    return round(_clamp(score), 1), components


def _compute_volume_score(index_data: dict[str, pd.DataFrame], closes: dict[str, pd.Series]) -> tuple[float, dict[str, Any]]:
    spy_ret5 = _ret(closes.get("SPY", pd.Series(dtype=float)), 5)
    qqq_ret5 = _ret(closes.get("QQQ", pd.Series(dtype=float)), 5)
    spy_rvol = _rvol(index_data.get("SPY", pd.DataFrame()))
    qqq_rvol = _rvol(index_data.get("QQQ", pd.DataFrame()))
    score = 50.0
    if spy_ret5 is not None and spy_rvol is not None:
        if spy_ret5 > 0 and spy_rvol > 1.1:
            score += 15
        elif spy_ret5 < 0 and spy_rvol > 1.2:
            score -= 20
        elif spy_ret5 > 0:
            score += 6
    if qqq_ret5 is not None and qqq_rvol is not None:
        if qqq_ret5 > 0 and qqq_rvol > 1.1:
            score += 10
        elif qqq_ret5 < 0 and qqq_rvol > 1.2:
            score -= 15
        elif qqq_ret5 > 0:
            score += 4
    return round(_clamp(score), 1), {
        "spy_5d": _safe_float((spy_ret5 or 0) * 100, 2) if spy_ret5 is not None else None,
        "qqq_5d": _safe_float((qqq_ret5 or 0) * 100, 2) if qqq_ret5 is not None else None,
        "spy_rvol": spy_rvol,
        "qqq_rvol": qqq_rvol,
    }


def _compute_breadth_score(closes: dict[str, pd.Series]) -> tuple[float, dict[str, Any]]:
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
    score = (
        _clamp(above_50_pct, default=50) * .40 +
        _clamp(above_200_pct, default=50) * .25 +
        _score_signed_pct(rsp_spy_20d, 5.0) * .20 +
        _score_signed_pct(iwm_spy_20d, 5.0) * .15
    )
    return round(_clamp(score), 1), {
        "sectors_above_50dma": _safe_float(above_50_pct, 1),
        "sectors_above_200dma": _safe_float(above_200_pct, 1),
        "rsp_spy_20d": _safe_float((rsp_spy_20d or 0) * 100, 2) if rsp_spy_20d is not None else None,
        "iwm_spy_20d": _safe_float((iwm_spy_20d or 0) * 100, 2) if iwm_spy_20d is not None else None,
    }


def _compute_risk_appetite_score(closes: dict[str, pd.Series]) -> tuple[float, float, dict[str, Any]]:
    spy = closes.get("SPY", pd.Series(dtype=float))
    qqq = closes.get("QQQ", pd.Series(dtype=float))
    vix = closes.get("^VIX", pd.Series(dtype=float))
    hyg = closes.get("HYG", pd.Series(dtype=float))
    tlt = closes.get("TLT", pd.Series(dtype=float))
    tnx = closes.get("^TNX", pd.Series(dtype=float))

    vix_last = _safe_float(vix.iloc[-1], 2) if len(vix) else None
    vix_percentile = _percentile(vix.tail(252), vix_last) if len(vix) else None
    credit_20d = _relative_return(hyg, tlt, 20)
    rate_20d_change = _safe_float(tnx.iloc[-1] - tnx.iloc[-21], 4) if len(tnx) > 20 else None
    spy_dd50 = _drawdown_from_high(spy, 50)
    qqq_dd50 = _drawdown_from_high(qqq, 50)

    score = 50.0
    if vix_last is not None:
        score += 14 if vix_last < 16 else (-16 if vix_last > 25 else 0)
    if vix_percentile is not None:
        score += _clamp(50 - vix_percentile, -16, 14, 0) * .35
    if credit_20d is not None:
        score += _clamp(credit_20d * 100 * 4.0, -12, 12, 0)
    if rate_20d_change is not None:
        score -= _clamp(rate_20d_change * 18.0, -8, 12, 0)
    if spy_dd50 is not None:
        score -= _clamp(abs(min(spy_dd50, 0)) * 100 * 2.2, 0, 14, 0)
    if qqq_dd50 is not None:
        score -= _clamp(abs(min(qqq_dd50, 0)) * 100 * 1.4, 0, 12, 0)

    penalty = 0.0
    if vix_last is not None and vix_last > 25:
        penalty += min(10, (vix_last - 25) * .8)
    if vix_percentile is not None and vix_percentile >= 80:
        penalty += min(8, (vix_percentile - 80) * .25)
    if credit_20d is not None and credit_20d < -0.025:
        penalty += min(8, abs(credit_20d) * 160)
    if rate_20d_change is not None and rate_20d_change > .25:
        penalty += min(5, rate_20d_change * 4)
    if spy_dd50 is not None and spy_dd50 < -0.08:
        penalty += min(8, abs(spy_dd50) * 70)
    if qqq_dd50 is not None and qqq_dd50 < -0.10:
        penalty += min(6, abs(qqq_dd50) * 50)

    return round(_clamp(score), 1), round(_clamp(penalty, 0, 30, 0), 1), {
        "vix": vix_last,
        "vix_percentile": vix_percentile,
        "hyg_tlt_20d": _safe_float((credit_20d or 0) * 100, 2) if credit_20d is not None else None,
        "yield_10y": _safe_float(tnx.iloc[-1], 2) if len(tnx) else None,
        "yield_10y_20d_change": rate_20d_change,
        "spy_drawdown_50d": _safe_float((spy_dd50 or 0) * 100, 2) if spy_dd50 is not None else None,
        "qqq_drawdown_50d": _safe_float((qqq_dd50 or 0) * 100, 2) if qqq_dd50 is not None else None,
    }


def _rules_for_score(score: float, breadth_score: float, risk_penalty: float, risk_on_score: float) -> tuple[dict[str, float], list[str]]:
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
    if risk_on_score < 45:
        rules["breakout_weight_multiplier"] *= .85
        rules["option_heat_weight_multiplier"] *= .85
        warnings.append("风险偏好价差偏弱，突破与期权信号已降权")
    elif score >= 65 and risk_on_score >= 70:
        rules["breakout_weight_multiplier"] *= 1.08
        rules["sector_strength_weight_multiplier"] *= 1.08
        warnings.append("风险偏好价差支持进攻型强势股")
    return {key: round(value, 3) for key, value in rules.items()}, warnings


def compute_market_regime(index_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    closes = {symbol: _close(frame) for symbol, frame in index_data.items()}
    missing = [
        {"symbol": symbol, "required": required, "available": len(closes.get(symbol, pd.Series(dtype=float)))}
        for symbol, required in MINIMUM_HISTORY.items()
        if len(closes.get(symbol, pd.Series(dtype=float))) < required
    ]
    if missing:
        missing_text = "、".join(f"{item['symbol']}({item['available']}/{item['required']})" for item in missing)
        warning = f"核心市场行情不足：{missing_text}，不生成市场强弱分数"
        return {
            "status": "insufficient_data",
            "score": None,
            "label": "数据不足",
            "index_trend_score": None,
            "market_momentum_score": None,
            "market_breadth_score": None,
            "market_volume_score": None,
            "risk_appetite_score": None,
            "risk_on_spread_score": None,
            "risk_on_spread_label": "数据不足",
            "market_risk_penalty": None,
            "rules": {},
            "warnings": [warning],
            "missing_requirements": missing,
            "trend": {},
            "momentum": {},
            "volume": {},
            "breadth": {},
            "risk": {},
            "spread_matrix": {},
            "market_context": {
                "status": "insufficient_data",
                "score": None,
                "label": "数据不足",
                "trend_momentum_score": None,
                "breadth_score": None,
                "risk_on_spread_score": None,
                "liquidity_credit_score": None,
                "sentiment_score": None,
                "sector_flow_score": None,
                "valuation_status": "not_available",
                "valuation_risk_penalty": 0.0,
            },
            "spy_20d": None,
            "qqq_20d": None,
            "iwm_20d": None,
            "spy_above_sma200": None,
            "vix": None,
        }
    trend_score, trend = _compute_trend_score(closes)
    momentum_score, momentum = _compute_momentum_score(closes)
    volume_score, volume = _compute_volume_score(index_data, closes)
    breadth_score, breadth = _compute_breadth_score(closes)
    risk_appetite_score, risk_penalty, risk = _compute_risk_appetite_score(closes)
    spread_matrix = compute_spread_matrix(index_data)
    risk_on_spread_score = float(spread_matrix.get("score") or 50.0)
    raw_score = (
        trend_score * .25 +
        risk_on_spread_score * .25 +
        breadth_score * .20 +
        momentum_score * .10 +
        volume_score * .10 +
        risk_appetite_score * .10 -
        risk_penalty * .35
    )
    score = round(_clamp(raw_score), 1)
    if score >= 75:
        label = "强风险偏好"
    elif score >= 60:
        label = "温和偏强"
    elif score >= 40:
        label = "中性震荡"
    else:
        label = "弱势高风险"
    rules, warnings = _rules_for_score(score, breadth_score, risk_penalty, risk_on_spread_score)
    warnings = [*warnings, *spread_matrix.get("warnings", [])]
    return {
        "status": "active",
        "score": score,
        "label": label,
        "index_trend_score": trend_score,
        "market_momentum_score": momentum_score,
        "market_breadth_score": breadth_score,
        "market_volume_score": volume_score,
        "risk_appetite_score": risk_appetite_score,
        "risk_on_spread_score": round(risk_on_spread_score, 1),
        "risk_on_spread_label": spread_matrix.get("label"),
        "market_risk_penalty": risk_penalty,
        "rules": rules,
        "warnings": list(dict.fromkeys(warnings))[:6],
        "trend": trend,
        "momentum": momentum,
        "volume": volume,
        "breadth": breadth,
        "risk": risk,
        "spread_matrix": spread_matrix.get("spreads", {}),
        "market_context": {
            "score": score,
            "label": label,
            "trend_momentum_score": round(trend_score * .58 + momentum_score * .42, 1),
            "breadth_score": breadth_score,
            "risk_on_spread_score": round(risk_on_spread_score, 1),
            "liquidity_credit_score": risk_appetite_score,
            "sentiment_score": risk_appetite_score,
            "sector_flow_score": round((spread_matrix.get("spreads", {}).get("soxx_xlk", {}).get("score") or 50), 1),
            "valuation_status": "not_available",
            "valuation_risk_penalty": 0.0,
        },
        "spy_20d": momentum.get("spy_20d"),
        "qqq_20d": momentum.get("qqq_20d"),
        "iwm_20d": momentum.get("iwm_20d"),
        "spy_above_sma200": trend.get("spy_above_sma200", False),
        "vix": risk.get("vix"),
    }
