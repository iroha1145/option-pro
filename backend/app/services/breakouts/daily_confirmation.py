"""Daily T1 condition helpers. Production detect_breakout stays unchanged.

These functions are the frozen T1 math from the researched candidate. They
live in the breakout package so production and research can share them.
Research adapters may import this module; production must not import
``app.services.research``.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence

from app.services.algorithm_modes import T1_ALGORITHM, T1_VERSION


T1_SETTINGS = {
    "variant": T1_ALGORITHM,
    "version": T1_VERSION,
    "clv_min": 0.70,
    "rvol_min": 1.5,
    "upper_shadow_max": 0.15,
    "distance_atr_min": 0.20,
    "rvol_lookback": 20,
    "atr_period": 20,
    "clv_definition": "range_position_0_1",
    "clv_formula": "(close - low) / (high - low); None when high <= low",
    "clv_note": (
        "Range-position CLV in [0, 1]. This is not feature_engine.compute_clv, "
        "which is signed [-1, 1]."
    ),
    "rvol_formula": "V(T) / median(V(T-20), ..., V(T-1))",
    "rvol_note": (
        "Denominator uses only the previous 20 completed sessions. "
        "This is not production rvol_time_of_day."
    ),
    "atr": "compute_atr(daily_through_T, 20)",
    "zero_range": "CLV and upper-shadow unavailable; T1 cannot confirm",
    "information_ready": "T regular close including early closes",
}


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def valid_session_volume(value: Any) -> float | None:
    number = _finite(value)
    if number is None or number < 0:
        return None
    return number


def valid_daily_ohlc(open_: Any, high: Any, low: Any, close: Any) -> dict[str, float] | None:
    """Positive finite prices with a real range and uncrossed high/low."""

    open_v, high_v, low_v, close_v = map(_finite, (open_, high, low, close))
    if None in (open_v, high_v, low_v, close_v):
        return None
    if min(open_v, high_v, low_v, close_v) <= 0:
        return None
    if high_v < max(open_v, close_v) or low_v > min(open_v, close_v) or high_v <= low_v:
        return None
    return {"open": open_v, "high": high_v, "low": low_v, "close": close_v}


def daily_bar_location(open_: Any, high: Any, low: Any, close: Any) -> dict[str, Any]:
    ohlc = valid_daily_ohlc(open_, high, low, close)
    if ohlc is None:
        open_v, high_v, low_v, close_v = map(_finite, (open_, high, low, close))
        return {
            "clv": None,
            "upper_shadow_ratio": None,
            "zero_range": True,
            "invalid_ohlc": True,
            "range": None if None in (high_v, low_v) else high_v - low_v,
        }
    open_v, high_v, low_v, close_v = ohlc["open"], ohlc["high"], ohlc["low"], ohlc["close"]
    span = high_v - low_v
    return {
        "clv": (close_v - low_v) / span,
        "upper_shadow_ratio": (high_v - max(open_v, close_v)) / span,
        "zero_range": False,
        "invalid_ohlc": False,
        "range": span,
    }


def rvol_daily_20med(
    volume_t: Any,
    prior_volumes: Sequence[Any],
    *,
    lookback: int = 20,
) -> dict[str, Any]:
    """V(T) / median of the previous ``lookback`` completed session volumes."""

    current = valid_session_volume(volume_t)
    history: list[float] = []
    missing = False
    for value in prior_volumes:
        number = valid_session_volume(value)
        if number is None:
            missing = True
            continue
        history.append(number)
    if current is None:
        return {
            "status": "unavailable",
            "reason": "missing_event_volume",
            "rvol_daily_20med": None,
            "lookback_used": len(history),
            "denominator": None,
        }
    if missing or len(prior_volumes) < lookback or len(history) < lookback:
        return {
            "status": "unavailable",
            "reason": "insufficient_prior_volume",
            "rvol_daily_20med": None,
            "lookback_used": len(history),
            "denominator": None,
        }
    if len(prior_volumes) != lookback:
        return {
            "status": "unavailable",
            "reason": "prior_volume_window_mismatch",
            "rvol_daily_20med": None,
            "lookback_used": len(history),
            "denominator": None,
        }
    window = history[-lookback:]
    denom = float(median(window))
    if denom <= 0:
        return {
            "status": "unavailable",
            "reason": "zero_median_volume",
            "rvol_daily_20med": None,
            "lookback_used": lookback,
            "denominator": denom,
        }
    return {
        "status": "active",
        "reason": None,
        "rvol_daily_20med": current / denom,
        "lookback_used": lookback,
        "denominator": denom,
    }


def t1_checks(
    *,
    clv: Any,
    rvol: Any,
    upper_shadow: Any,
    distance_atr: Any,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**T1_SETTINGS, **dict(settings or {})}
    clv_v = _finite(clv)
    rvol_v = _finite(rvol)
    wick_v = _finite(upper_shadow)
    dist_v = _finite(distance_atr)
    checks = {
        "clv": clv_v is not None and clv_v >= float(cfg["clv_min"]),
        "rvol": rvol_v is not None and rvol_v >= float(cfg["rvol_min"]),
        "upper_shadow": wick_v is not None and wick_v <= float(cfg["upper_shadow_max"]),
        "distance_atr": dist_v is not None and dist_v >= float(cfg["distance_atr_min"]),
    }
    available = None not in (clv_v, rvol_v, wick_v, dist_v)
    return {
        "available": available,
        "satisfied": available and all(checks.values()),
        "confirmed": available and all(checks.values()),
        "checks": checks,
        "clv": clv_v,
        "rvol": rvol_v,
        "upper_shadow_ratio": wick_v,
        "breakout_distance_atr": dist_v,
    }
