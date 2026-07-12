from __future__ import annotations

import math


_MIN_ACTIVE_WEIGHT = 0.25

MARKET_SCORE_SIGNAL_KEYS = (
    "sma20_distance",
    "sma50_distance",
    "sma200_distance",
    "rsi14",
    "return_20d",
    "rsp_spy_5d",
    "iwm_spy_5d",
    "sectors_above_50dma",
    "vix_percentile",
    "vix",
    "vix_5d_change",
    "yield_10y",
    "yield_10y_20d_change",
    "credit_risk",
)

STOCK_SCORE_SIGNAL_KEYS = (
    "sma20_dist",
    "sma50_dist",
    "sma200_dist",
    "rsi14",
    "return_20d",
    "atr_percentile",
    "volume_zscore",
    "obv_divergence",
    "close_position",
    "macd_hist",
    "relative_strength_spy",
)


def _finite_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_scores(signals: dict) -> list[dict]:
    # Metadata entries such as ``_volume_today`` are dictionaries too, but they
    # are not scored signals and must not inflate the reported data quality.
    return [
        signal
        for signal in signals.values()
        if (
            isinstance(signal, dict)
            and _finite_number(signal.get("value")) is not None
            and (
                _finite_number(signal.get("top_score")) is not None
                or _finite_number(signal.get("bottom_score")) is not None
            )
        )
    ]


def _avg(signals: dict, keys: list[str], side: str) -> float | None:
    vals = []
    score_key = "top_score" if side == "top" else "bottom_score"
    for key in keys:
        sig = signals.get(key)
        if not isinstance(sig, dict) or _finite_number(sig.get("value")) is None:
            continue
        score = _finite_number(sig.get(score_key))
        if score is not None:
            vals.append(score)
    return sum(vals) / len(vals) if vals else None


def _aggregate_parts(
    parts: dict[str, float | None],
    weights: dict[str, float],
    *,
    min_active_weight: float = _MIN_ACTIVE_WEIGHT,
) -> dict:
    configured_weight = sum(max(0.0, float(weight)) for weight in weights.values())
    active = {}
    for name, value in parts.items():
        number = _finite_number(value)
        if number is not None and weights.get(name, 0.0) > 0:
            active[name] = number
    active_weight = sum(weights[name] for name in active)
    coverage = active_weight / configured_weight if configured_weight > 0 else 0.0
    missing = [name for name in weights if _finite_number(parts.get(name)) is None]
    score = None
    status = "insufficient_data"
    if active_weight >= min_active_weight:
        score = sum(active[name] * weights[name] for name in active) / active_weight
        status = "active"
    return {
        "score": score,
        "status": status,
        "active_weight": round(active_weight, 4),
        "coverage": round(max(0.0, min(1.0, coverage)), 4),
        "missing_components": missing,
    }


def _quality(signals: dict, expected: int) -> int:
    valid = len(_valid_scores(signals))
    return round(max(0, min(100, valid / expected * 100)))


def _quality_for_keys(signals: dict, keys: tuple[str, ...]) -> tuple[int, int, int]:
    available = 0
    for key in keys:
        signal = signals.get(key)
        if (
            isinstance(signal, dict)
            and _finite_number(signal.get("value")) is not None
            and (
                _finite_number(signal.get("top_score")) is not None
                or _finite_number(signal.get("bottom_score")) is not None
            )
        ):
            available += 1
    expected = len(keys)
    quality = round(available / expected * 100) if expected else 0
    return quality, available, expected


def _level(score: int | None, top: bool = True) -> str:
    if score is None:
        return "数据不足"
    if score < 30:
        return "顶部风险低" if top else "没有底部迹象"
    if score < 50:
        return "正常震荡风险" if top else "可能只是超跌"
    if score < 65:
        return "需要停止追高" if top else "开始出现底部条件"
    if score < 80:
        return "阶段性顶部风险高" if top else "阶段性底部概率较高"
    return "极端过热，等待反转确认" if top else "恐慌释放充分，仍需价格确认"



def _sig_value(signals: dict, key: str) -> float | None:
    sig = signals.get(key)
    if not isinstance(sig, dict):
        return None
    return _finite_number(sig.get("value"))


def _score_to_reason(sig: dict, key: str, side: str) -> str:
    label = sig.get("label") or key
    value = sig.get("value")
    if isinstance(value, float):
        value = round(value, 1)
    return f"{label} {value}"


def _reasons(signals: dict, side: str) -> dict:
    score_key = "top_score" if side == "top" else "bottom_score"
    scored = []
    for key, sig in signals.items():
        if not isinstance(sig, dict) or _finite_number(sig.get("value")) is None:
            continue
        score = _finite_number(sig.get(score_key))
        if score is not None:
            scored.append((score, key, sig))
    raisers = [_score_to_reason(sig, key, side) for _, key, sig in sorted(scored, reverse=True)[:3] if _ > 0]
    suppressors = [_score_to_reason(sig, key, side) for score, key, sig in sorted(scored, key=lambda x: x[0])[:3] if score <= 15]
    return {"raising": raisers, "suppressing": suppressors}


def _dip_level(score: int | None) -> str:
    if score is None: return "数据不足"
    if score < 30: return "回调买点弱"
    if score < 50: return "回调承接一般"
    if score < 70: return "趋势回调买点中等"
    return "高质量趋势回调"


def _compute_dip_buy_quality(signals: dict) -> dict:
    s20 = _sig_value(signals, "sma20_dist")
    s50 = _sig_value(signals, "sma50_dist")
    s200 = _sig_value(signals, "sma200_dist")
    volz = _sig_value(signals, "volume_zscore")
    close_pos = _sig_value(signals, "close_position")
    rs = _sig_value(signals, "relative_strength_spy")

    trend_still_up = (
        None
        if s50 is None
        else (
            100
            if s50 > 0 and s200 is not None and s200 > 0
            else (60 if s50 > 0 else 30)
        )
    )
    distances = [abs(value) for value in (s20, s50) if value is not None]
    pullback_to_key_level = (
        max(0.0, 100.0 - min(distances) * 25.0)
        if distances
        else None
    )
    decline_on_low_volume = (
        None
        if volz is None
        else (80 if volz < 0 else (45 if volz <= 1 else 20))
    )
    intraday_recovery = close_pos if close_pos is not None else None
    relative_strength_intact = (
        None
        if rs is None
        else (80 if rs > 0 else (50 if rs > -1 else 25))
    )
    # The stock endpoint does not yet carry a market-shape snapshot.  Missing
    # context stays missing and its weight is redistributed over real inputs.
    market_environment_stable = None

    parts = {
        "trend_still_up": trend_still_up,
        "pullback_to_key_level": pullback_to_key_level,
        "decline_on_low_volume": decline_on_low_volume,
        "intraday_recovery": intraday_recovery,
        "relative_strength_intact": relative_strength_intact,
        "market_environment_stable": market_environment_stable,
    }
    weights = {
        "trend_still_up": 0.25,
        "pullback_to_key_level": 0.20,
        "decline_on_low_volume": 0.20,
        "intraday_recovery": 0.15,
        "relative_strength_intact": 0.10,
        "market_environment_stable": 0.10,
    }
    aggregate = _aggregate_parts(parts, weights)
    score = (
        round(float(aggregate["score"]))
        if aggregate["score"] is not None
        else None
    )
    evidence = []
    if s50 is not None: evidence.append(f"价格相对50日线 {s50:.1f}%")
    if s20 is not None: evidence.append(f"价格相对20日线 {s20:.1f}%")
    if volz is not None: evidence.append(f"成交量Z分数 {volz:.1f}")
    if close_pos is not None: evidence.append(f"收盘位于日内区间 {close_pos:.1f}%")
    if rs is not None: evidence.append(f"相对SPY {rs:.1f}%")
    return {
        "score": score,
        "label": _dip_level(score),
        "breakdown": {
            key: round(value, 1) if value is not None else None
            for key, value in parts.items()
        },
        "reasons": evidence[:4],
        "status": aggregate["status"],
        "active_weight": aggregate["active_weight"],
        "coverage": aggregate["coverage"],
        "missing_components": aggregate["missing_components"],
    }


def compute_market_scores(signals: dict) -> dict:
    """Aggregate market signals into top/bottom scores using Section VII weights."""
    top_weights = {
        "price_overheated": 0.20,
        "breadth_divergence": 0.20,
        "options_sentiment": 0.15,
        "volatility_turning": 0.15,
        "rates_pressure": 0.10,
        "credit_risk": 0.10,
        "positioning": 0.10,
    }
    top_parts = {
        "price_overheated": _avg(signals, ["sma20_distance", "sma50_distance", "sma200_distance", "rsi14", "return_20d"], "top"),
        "breadth_divergence": _avg(signals, ["rsp_spy_5d", "iwm_spy_5d", "sectors_above_50dma"], "top"),
        "options_sentiment": _avg(signals, ["vix_percentile", "vix"], "top"),
        "volatility_turning": _avg(signals, ["vix_5d_change"], "top"),
        "rates_pressure": _avg(signals, ["yield_10y", "yield_10y_20d_change"], "top"),
        "credit_risk": _avg(signals, ["credit_risk"], "top"),
        # No reliable positioning series is present.  Keep the component
        # missing and renormalize active weights instead of inventing 50.
        "positioning": None,
    }

    bottom_weights = {
        "panic_release": 0.20,
        "technical_reclaim": 0.20,
        "breadth_repair": 0.20,
        "volatility_falling": 0.15,
        "credit_stable": 0.10,
        "rates_easing": 0.10,
        "sentiment_pessimism": 0.05,
    }
    bottom_parts = {
        "panic_release": _avg(signals, ["vix_percentile", "vix"], "bottom"),
        "technical_reclaim": _avg(signals, ["sma20_distance", "sma50_distance", "sma200_distance", "rsi14", "return_20d"], "bottom"),
        "breadth_repair": _avg(signals, ["rsp_spy_5d", "iwm_spy_5d", "sectors_above_50dma"], "bottom"),
        "volatility_falling": _avg(signals, ["vix_5d_change"], "bottom"),
        "credit_stable": _avg(signals, ["credit_risk"], "bottom"),
        "rates_easing": _avg(signals, ["yield_10y_20d_change", "yield_10y"], "bottom"),
        "sentiment_pessimism": None,
    }
    top_aggregate = _aggregate_parts(top_parts, top_weights)
    bottom_aggregate = _aggregate_parts(bottom_parts, bottom_weights)
    top_score = (
        round(float(top_aggregate["score"]))
        if top_aggregate["score"] is not None
        else None
    )
    bottom_score = (
        round(float(bottom_aggregate["score"]))
        if bottom_aggregate["score"] is not None
        else None
    )
    top_reasons = _reasons(signals, "top")
    bottom_reasons = _reasons(signals, "bottom")
    signal_quality, quality_available, quality_expected = _quality_for_keys(
        signals,
        MARKET_SCORE_SIGNAL_KEYS,
    )
    model_coverage = (
        top_aggregate["coverage"] + bottom_aggregate["coverage"]
    ) / 2.0
    return {
        "top_score": top_score,
        "bottom_score": bottom_score,
        "top_status": top_aggregate["status"],
        "bottom_status": bottom_aggregate["status"],
        "data_quality": round(signal_quality * model_coverage),
        "signal_data_quality": signal_quality,
        "data_quality_available": quality_available,
        "data_quality_expected": quality_expected,
        "coverage": {
            "top_active_weight": top_aggregate["active_weight"],
            "bottom_active_weight": bottom_aggregate["active_weight"],
            "top_ratio": top_aggregate["coverage"],
            "bottom_ratio": bottom_aggregate["coverage"],
            "top_missing_components": top_aggregate["missing_components"],
            "bottom_missing_components": bottom_aggregate["missing_components"],
        },
        "top_breakdown": {k: round(v, 1) if v is not None else None for k, v in top_parts.items()},
        "bottom_breakdown": {k: round(v, 1) if v is not None else None for k, v in bottom_parts.items()},
        "top_label": _level(top_score, True),
        "bottom_label": _level(bottom_score, False),
        "top_reasons": top_reasons,
        "bottom_reasons": bottom_reasons,
    }


def compute_stock_scores(signals: dict) -> dict:
    """Aggregate stock signals into top/bottom scores using Section VII stock categories."""
    top_weights = {
        "price_overheated": 0.20,
        "distribution": 0.20,
        "options_crowding": 0.15,
        "earnings_reaction": 0.15,
        "relative_strength_turning": 0.10,
        "valuation_expectations": 0.10,
        "event_risk": 0.10,
    }
    top_parts = {
        "price_overheated": _avg(signals, ["sma20_dist", "sma50_dist", "sma200_dist", "rsi14", "return_20d"], "top"),
        "distribution": _avg(signals, ["volume_zscore", "obv_divergence", "close_position", "macd_hist"], "top"),
        # Current ATM IV is not a historical IV rank.  Keep this category
        # unavailable until a real historical series exists.
        "options_crowding": None,
        "earnings_reaction": None,
        "relative_strength_turning": _avg(signals, ["relative_strength_spy"], "top"),
        "valuation_expectations": None,
        "event_risk": None,
    }
    bottom_weights = {
        "panic_release": 0.20,
        "false_break_reclaim": 0.20,
        "short_covering": 0.15,
        "fundamental_stability": 0.15,
        "industry_stabilizing": 0.10,
        "options_panic_falling": 0.10,
        "market_environment": 0.10,
    }
    bottom_parts = {
        "panic_release": _avg(signals, ["rsi14", "return_20d", "atr_percentile", "volume_zscore"], "bottom"),
        "false_break_reclaim": _avg(signals, ["sma20_dist", "sma50_dist", "close_position", "macd_hist"], "bottom"),
        "short_covering": None,
        "fundamental_stability": None,
        "industry_stabilizing": _avg(signals, ["relative_strength_spy"], "bottom"),
        "options_panic_falling": None,
        "market_environment": None,
    }
    top_aggregate = _aggregate_parts(top_parts, top_weights)
    bottom_aggregate = _aggregate_parts(bottom_parts, bottom_weights)
    top_score = (
        round(float(top_aggregate["score"]))
        if top_aggregate["score"] is not None
        else None
    )
    bottom_score = (
        round(float(bottom_aggregate["score"]))
        if bottom_aggregate["score"] is not None
        else None
    )
    dip = _compute_dip_buy_quality(signals)
    top_reasons = _reasons(signals, "top")
    bottom_reasons = _reasons(signals, "bottom")
    data_quality, quality_available, quality_expected = _quality_for_keys(
        signals,
        STOCK_SCORE_SIGNAL_KEYS,
    )
    return {
        "top_score": top_score,
        "bottom_score": bottom_score,
        "dip_buy_quality": dip["score"],
        "dip_buy_label": dip["label"],
        "dip_buy_breakdown": dip["breakdown"],
        "dip_buy_reasons": dip["reasons"],
        "dip_buy_status": dip["status"],
        "dip_buy_active_weight": dip["active_weight"],
        "dip_buy_coverage": dip["coverage"],
        "dip_buy_missing_components": dip["missing_components"],
        "data_quality": data_quality,
        "data_quality_available": quality_available,
        "data_quality_expected": quality_expected,
        "top_status": top_aggregate["status"],
        "bottom_status": bottom_aggregate["status"],
        "top_active_weight": top_aggregate["active_weight"],
        "bottom_active_weight": bottom_aggregate["active_weight"],
        "coverage": {
            "top_active_weight": top_aggregate["active_weight"],
            "bottom_active_weight": bottom_aggregate["active_weight"],
            "top_ratio": top_aggregate["coverage"],
            "bottom_ratio": bottom_aggregate["coverage"],
            "top_missing_components": top_aggregate["missing_components"],
            "bottom_missing_components": bottom_aggregate["missing_components"],
        },
        "top_breakdown": {k: round(v, 1) if v is not None else None for k, v in top_parts.items()},
        "bottom_breakdown": {k: round(v, 1) if v is not None else None for k, v in bottom_parts.items()},
        "top_label": _level(top_score, True),
        "bottom_label": _level(bottom_score, False),
        "top_reasons": top_reasons,
        "bottom_reasons": bottom_reasons,
    }
