"""Daily confirmation proxies. Production detect_breakout stays unchanged.

T1 and T2 are independent timing-layer experiments. Pure helpers live here so
the production radar package can import them later without enabling them.
Dataset adapters live in ``app.services.research.timing``.
"""

from __future__ import annotations

from statistics import median
from typing import Any, Mapping, Sequence


T1_VARIANT = "t1_daily_strong_proxy"
T2_VARIANT = "t2_next_close_hold"

T1_SETTINGS = {
    "variant": T1_VARIANT,
    "clv_min": 0.70,
    "rvol_min": 1.5,
    "upper_shadow_max": 0.15,
    "distance_atr_min": 0.20,
    "rvol_lookback": 20,
    "clv_definition": "range_position_0_1",
    "clv_formula": "(close - low) / (high - low); None when high <= low",
    "clv_note": (
        "This matches research radar `_clv` and detect_breakout's 0.70 threshold. "
        "It is not feature_engine.compute_clv, which is signed [-1, 1]."
    ),
    "rvol_formula": "V(T) / median(V(T-20), ..., V(T-1))",
    "rvol_note": (
        "Denominator uses only the previous 20 completed sessions. "
        "This is not production rvol_time_of_day."
    ),
    "atr": "compute_atr(daily_through_T, 20)",
    "zero_range": "CLV and upper-shadow unavailable; T1 cannot confirm",
    "information_ready": "T regular close",
    "executable_from": "T+1 open",
}

T2_SETTINGS = {
    "variant": T2_VARIANT,
    "hold_rule": "close > frozen_resistance_high + frozen_buffer",
    "buffer_formula": "max(T_close * 0.0025, atr20_T * 0.10)",
    "buffer_source": "T-visible only; applied to both T and T+1 closes",
    "require_bars": ("T", "T+1"),
    "structure": "T-event frozen resistance; never redrawn after T+1",
    "information_ready": "T+1 regular close",
    "executable_from": "T+2 open",
    "note": "Independent of T1. Using T+2 close would move execution to T+3 open.",
}

BREAK_BUFFER_PCT = 0.0025
BREAK_BUFFER_ATR = 0.10


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def daily_bar_location(open_: Any, high: Any, low: Any, close: Any) -> dict[str, Any]:
    open_v, high_v, low_v, close_v = map(_finite, (open_, high, low, close))
    if None in (open_v, high_v, low_v, close_v) or high_v <= low_v:
        return {
            "clv": None,
            "upper_shadow_ratio": None,
            "zero_range": True,
            "range": None if None in (high_v, low_v) else high_v - low_v,
        }
    span = high_v - low_v
    return {
        "clv": (close_v - low_v) / span,
        "upper_shadow_ratio": (high_v - max(open_v, close_v)) / span,
        "zero_range": False,
        "range": span,
    }


def rvol_daily_20med(
    volume_t: Any,
    prior_volumes: Sequence[Any],
    *,
    lookback: int = 20,
) -> dict[str, Any]:
    """V(T) / median of the previous ``lookback`` completed session volumes."""

    current = _finite(volume_t)
    history: list[float] = []
    for value in prior_volumes:
        number = _finite(value)
        if number is None:
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
    if len(history) < lookback:
        return {
            "status": "unavailable",
            "reason": "insufficient_prior_volume",
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


def frozen_hold_buffer(close_t: Any, atr_t: Any) -> float | None:
    close_v = _finite(close_t)
    atr_v = _finite(atr_t)
    if close_v is None or atr_v is None or atr_v <= 0 or close_v <= 0:
        return None
    return max(close_v * BREAK_BUFFER_PCT, atr_v * BREAK_BUFFER_ATR)


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
        "confirmed": available and all(checks.values()),
        "checks": checks,
        "clv": clv_v,
        "rvol": rvol_v,
        "upper_shadow_ratio": wick_v,
        "breakout_distance_atr": dist_v,
    }


def t2_holds(
    *,
    close_t: Any,
    close_t1: Any,
    resistance_high: Any,
    buffer: Any,
) -> dict[str, Any]:
    close_t_v = _finite(close_t)
    close_t1_v = _finite(close_t1)
    resistance = _finite(resistance_high)
    buffer_v = _finite(buffer)
    available = None not in (close_t_v, close_t1_v, resistance, buffer_v)
    hold_t = (
        available
        and close_t_v is not None
        and resistance is not None
        and buffer_v is not None
        and close_t_v > resistance + buffer_v
    )
    hold_t1 = (
        available
        and close_t1_v is not None
        and resistance is not None
        and buffer_v is not None
        and close_t1_v > resistance + buffer_v
    )
    return {
        "available": available,
        "confirmed": bool(hold_t and hold_t1),
        "hold_t": hold_t,
        "hold_t1": hold_t1,
        "close_t": close_t_v,
        "close_t1": close_t1_v,
        "resistance_high_frozen": resistance,
        "buffer_frozen": buffer_v,
    }
