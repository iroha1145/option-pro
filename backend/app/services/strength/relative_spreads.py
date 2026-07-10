from __future__ import annotations

import math
from typing import Any

import pandas as pd


SPREAD_DEFINITIONS = (
    {
        "key": "qqq_spy",
        "numerator": "QQQ",
        "denominator": "SPY",
        "name": "QQQ/SPY",
        "label_positive": "成长股领先",
        "label_negative": "成长弱于大盘",
        "weight": 0.18,
    },
    {
        "key": "xlk_spy",
        "numerator": "XLK",
        "denominator": "SPY",
        "name": "XLK/SPY",
        "label_positive": "科技强于大盘",
        "label_negative": "科技弱于大盘",
        "weight": 0.12,
    },
    {
        "key": "iwm_spy",
        "numerator": "IWM",
        "denominator": "SPY",
        "name": "IWM/SPY",
        "label_positive": "小盘风险扩散",
        "label_negative": "小盘未参与",
        "weight": 0.14,
    },
    {
        "key": "rsp_spy",
        "numerator": "RSP",
        "denominator": "SPY",
        "name": "RSP/SPY",
        "label_positive": "等权宽度改善",
        "label_negative": "大权重托盘",
        "weight": 0.12,
    },
    {
        "key": "xly_xlp",
        "numerator": "XLY",
        "denominator": "XLP",
        "name": "XLY/XLP",
        "label_positive": "进攻消费偏强",
        "label_negative": "防御消费占优",
        "weight": 0.14,
    },
    {
        "key": "hyg_ief",
        "numerator": "HYG",
        "denominator": "IEF",
        "name": "HYG/IEF",
        "label_positive": "信用风险偏好改善",
        "label_negative": "信用风险偏弱",
        "weight": 0.14,
    },
    {
        "key": "spy_gld",
        "numerator": "SPY",
        "denominator": "GLD",
        "name": "SPY/GLD",
        "label_positive": "股票强于避险资产",
        "label_negative": "避险资产占优",
        "weight": 0.08,
    },
    {
        "key": "soxx_xlk",
        "numerator": "SOXX",
        "denominator": "XLK",
        "name": "SOXX/XLK",
        "label_positive": "半导体强于科技",
        "label_negative": "半导体弱于科技",
        "weight": 0.06,
    },
    {
        "key": "soxx_spy",
        "numerator": "SOXX",
        "denominator": "SPY",
        "name": "SOXX/SPY",
        "label_positive": "半导体强于大盘",
        "label_negative": "半导体弱于大盘",
        "weight": 0.06,
    },
    {
        "key": "xlu_spy",
        "numerator": "XLU",
        "denominator": "SPY",
        "name": "XLU/SPY",
        "label_positive": "防御资金增强",
        "label_negative": "防御资金回落",
        "weight": 0.04,
        "defensive": True,
    },
)


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


def _score_signed(value: float | None, scale: float, neutral: float = 50.0) -> float:
    if value is None:
        return neutral
    return _clamp(neutral + value * scale)


def _close(frame: pd.DataFrame) -> pd.Series:
    return frame["Close"].dropna() if not frame.empty and "Close" in frame.columns else pd.Series(dtype=float)


def _ret(close: pd.Series, days: int) -> float | None:
    if len(close) <= days:
        return None
    base = close.iloc[-(days + 1)]
    if not base or base <= 0:
        return None
    return _safe_float(close.iloc[-1] / base - 1, 5)


def _slope(series: pd.Series) -> float | None:
    values = [float(value) for value in series.dropna()]
    size = len(values)
    if size < 3:
        return None
    x_mean = (size - 1) / 2
    y_mean = sum(values) / size
    denom = sum((idx - x_mean) ** 2 for idx in range(size))
    if denom <= 0:
        return None
    numer = sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values))
    return numer / denom


def compute_log_ratio(numerator_close: pd.Series, denominator_close: pd.Series) -> pd.Series:
    aligned = pd.concat([numerator_close, denominator_close], axis=1).dropna()
    if aligned.empty:
        return pd.Series(dtype=float)
    num = aligned.iloc[:, 0].astype(float)
    den = aligned.iloc[:, 1].astype(float)
    den = den.where(den > 0)
    num = num.where(num > 0)
    ratio = num.apply(math.log) - den.apply(math.log)
    return ratio.dropna()


def _ratio_features(
    *,
    key: str,
    name: str,
    numerator: str,
    denominator: str,
    numerator_close: pd.Series,
    denominator_close: pd.Series,
    label_positive: str,
    label_negative: str,
    defensive: bool = False,
) -> dict[str, Any]:
    log_ratio = compute_log_ratio(numerator_close, denominator_close)
    if len(log_ratio) < 65:
        return {
            "key": key,
            "name": name,
            "numerator": numerator,
            "denominator": denominator,
            "score": 50.0,
            "label": "数据不足",
            "status": "not_enough_data",
            "constructive": False,
            "defensive_relative_strength": False,
        }

    momentum_20d = log_ratio.iloc[-1] - log_ratio.iloc[-21]
    momentum_60d = log_ratio.iloc[-1] - log_ratio.iloc[-61]
    slope_20d = _slope(log_ratio.tail(20))
    slope_60d = _slope(log_ratio.tail(60))
    acceleration = slope_20d - slope_60d if slope_20d is not None and slope_60d is not None else None
    sma20 = log_ratio.rolling(20).mean().iloc[-1]
    sma60 = log_ratio.rolling(60).mean().iloc[-1]
    above_sma20 = bool(log_ratio.iloc[-1] > sma20) if math.isfinite(sma20) else False
    trend_up = bool(above_sma20 and math.isfinite(sma20) and math.isfinite(sma60) and sma20 > sma60)
    breakout_60d = bool(log_ratio.iloc[-1] >= log_ratio.tail(60).max())
    numerator_20d = _ret(numerator_close, 20)
    denominator_20d = _ret(denominator_close, 20)

    ratio_up = momentum_20d > 0
    numerator_up = numerator_20d is not None and numerator_20d > 0
    denominator_not_weak = denominator_20d is None or denominator_20d > -0.03
    constructive = bool(ratio_up and numerator_up and denominator_not_weak)
    defensive_relative_strength = bool(ratio_up and not numerator_up)

    momentum_score_20 = _score_signed(momentum_20d, 680)
    momentum_score_60 = _score_signed(momentum_60d, 420)
    trend_score = (
        (38 if above_sma20 else 18) +
        (34 if trend_up else 16) +
        (28 if slope_20d is not None and slope_20d > 0 else 12)
    )
    breakout_score = 82 if breakout_60d else 48
    absolute_confirm_score = 76 if constructive else (42 if defensive_relative_strength else 50)
    score = (
        momentum_score_20 * .35 +
        momentum_score_60 * .25 +
        trend_score * .20 +
        breakout_score * .10 +
        absolute_confirm_score * .10
    )
    if defensive:
        # Defensive leadership is useful context, but it should reduce risk-on score.
        score = 100 - score

    if constructive:
        label = label_positive
    elif defensive_relative_strength:
        label = "相对抗跌，非进攻领先"
    elif defensive and score < 45:
        label = "防御资金增强"
    else:
        label = label_negative if score < 48 else "中性"

    return {
        "key": key,
        "name": name,
        "numerator": numerator,
        "denominator": denominator,
        "score": round(_clamp(score), 1),
        "label": label,
        "status": "active",
        "ratio_return_20d": _safe_float((math.exp(momentum_20d) - 1) * 100, 2),
        "ratio_return_60d": _safe_float((math.exp(momentum_60d) - 1) * 100, 2),
        "slope_20d": _safe_float(slope_20d, 6),
        "slope_60d": _safe_float(slope_60d, 6),
        "acceleration": _safe_float(acceleration, 6),
        "above_sma20": above_sma20,
        "trend_up": trend_up,
        "breakout_60d": breakout_60d,
        "numerator_20d": _safe_float((numerator_20d or 0) * 100, 2) if numerator_20d is not None else None,
        "denominator_20d": _safe_float((denominator_20d or 0) * 100, 2) if denominator_20d is not None else None,
        "constructive": constructive,
        "defensive_relative_strength": defensive_relative_strength,
        "defensive": defensive,
    }


def compute_spread_matrix(index_data: dict[str, pd.DataFrame]) -> dict[str, Any]:
    closes = {symbol: _close(frame) for symbol, frame in index_data.items()}
    spreads: dict[str, dict[str, Any]] = {}
    weighted_score = 0.0
    total_weight = 0.0
    warnings: list[str] = []

    for definition in SPREAD_DEFINITIONS:
        numerator = definition["numerator"]
        denominator = definition["denominator"]
        result = _ratio_features(
            key=definition["key"],
            name=definition["name"],
            numerator=numerator,
            denominator=denominator,
            numerator_close=closes.get(numerator, pd.Series(dtype=float)),
            denominator_close=closes.get(denominator, pd.Series(dtype=float)),
            label_positive=definition["label_positive"],
            label_negative=definition["label_negative"],
            defensive=bool(definition.get("defensive")),
        )
        spreads[str(definition["key"])] = result
        if result.get("status") == "active":
            weight = float(definition.get("weight") or 0)
            weighted_score += float(result.get("score") or 50) * weight
            total_weight += weight

    score = round(_clamp(weighted_score / total_weight if total_weight else 50), 1)
    if score >= 72:
        label = "风险偏好扩散"
    elif score >= 58:
        label = "风险偏好温和"
    elif score >= 45:
        label = "风险偏好中性"
    else:
        label = "风险偏好偏弱"

    for key in ("iwm_spy", "rsp_spy"):
        item = spreads.get(key, {})
        if item.get("status") == "active" and (item.get("score") or 50) < 45:
            warnings.append(f"{item.get('name')}偏弱，强势未充分扩散")
    soxx = spreads.get("soxx_xlk", {})
    if soxx.get("status") == "active" and (soxx.get("score") or 50) >= 70:
        warnings.append("SOXX/XLK走强，半导体相对科技板块领先")
    hyg = spreads.get("hyg_ief", {})
    if hyg.get("status") == "active" and (hyg.get("score") or 50) < 42:
        warnings.append("HYG/IEF偏弱，信用风险偏好不足")

    return {
        "score": score,
        "label": label,
        "spreads": spreads,
        "warnings": warnings[:4],
    }
