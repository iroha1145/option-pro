"""Dataset adapters for T1/T2. Confirmation math lives in daily_confirmation."""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

from app.services.breakouts.daily_confirmation import (
    T1_SETTINGS,
    T1_VARIANT,
    T2_SETTINGS,
    T2_VARIANT,
    daily_bar_location,
    frozen_hold_buffer,
    rvol_daily_20med,
    t1_checks,
    t2_holds,
)
from app.services.breakouts.feature_engine import compute_atr
from app.services.research.calendar import nth_trading_day
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import parse_session_date


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in {float("inf"), float("-inf")}:
        return None
    return number


def _adjusted_parts(bar: Mapping[str, Any] | None) -> dict[str, float | None]:
    if bar is None:
        return {"open": None, "high": None, "low": None, "close": None, "volume": None}
    return {
        "open": _finite(bar.get("adj_open") if bar.get("adj_open") is not None else bar.get("open")),
        "high": _finite(bar.get("adj_high") if bar.get("adj_high") is not None else bar.get("high")),
        "low": _finite(bar.get("adj_low") if bar.get("adj_low") is not None else bar.get("low")),
        "close": _finite(bar.get("adj_close") if bar.get("adj_close") is not None else bar.get("close")),
        "volume": _finite(bar.get("volume")),
    }


def evaluate_t1(
    dataset: OfflineOHLCV,
    event: Mapping[str, Any],
    *,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**T1_SETTINGS, **dict(settings or {})}
    session = parse_session_date(str(event.get("trading_date") or event.get("signal_date")))
    ticker = str(event.get("ticker") or "")
    resistance = _finite(event.get("resistance_high"))
    daily = dataset.frame(ticker, through=session, allow_sealed=False)
    bar = dataset.bar(ticker, session)
    executable = nth_trading_day(session, 1, after=True)
    base = {
        "variant": T1_VARIANT,
        "settings": {
            "clv_min": cfg["clv_min"],
            "rvol_min": cfg["rvol_min"],
            "upper_shadow_max": cfg["upper_shadow_max"],
            "distance_atr_min": cfg["distance_atr_min"],
            "clv_definition": cfg["clv_definition"],
        },
        "information_ready": session.isoformat(),
        "executable_date": None if executable is None else executable.isoformat(),
        "executable_from": cfg["executable_from"],
        "resistance_high_frozen": resistance,
    }
    if daily.empty or bar is None or daily.index.max().date() != session:
        return {
            **base,
            "confirmed": False,
            "status": "unavailable",
            "reason": "missing_event_bar",
        }

    volumes = [float(value) for value in daily["Volume"].astype(float).tolist()]
    rvol = rvol_daily_20med(volumes[-1], volumes[:-1], lookback=int(cfg["rvol_lookback"]))
    parts = _adjusted_parts(bar)
    location = daily_bar_location(parts["open"], parts["high"], parts["low"], parts["close"])
    atr = compute_atr(daily, 20)
    distance = None
    if parts["close"] is not None and resistance is not None and atr is not None and atr > 0:
        distance = (parts["close"] - resistance) / atr
    judged = t1_checks(
        clv=location["clv"],
        rvol=rvol.get("rvol_daily_20med"),
        upper_shadow=location["upper_shadow_ratio"],
        distance_atr=distance,
        settings=cfg,
    )
    return {
        **base,
        "status": "active" if judged["available"] else "unavailable",
        "reason": None if judged["available"] else "missing_t1_inputs",
        "confirmed": judged["confirmed"],
        "checks": judged["checks"],
        "clv": location["clv"],
        "upper_shadow_ratio": location["upper_shadow_ratio"],
        "zero_range": location["zero_range"],
        "rvol": rvol,
        "atr20": atr,
        "breakout_distance_atr": distance,
    }


def evaluate_t2(
    dataset: OfflineOHLCV,
    event: Mapping[str, Any],
    *,
    settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = {**T2_SETTINGS, **dict(settings or {})}
    session = parse_session_date(str(event.get("trading_date") or event.get("signal_date")))
    ticker = str(event.get("ticker") or "")
    resistance = _finite(event.get("resistance_high"))
    next_session = nth_trading_day(session, 1, after=True)
    bar_t = dataset.bar(ticker, session)
    daily_t = dataset.frame(ticker, through=session, allow_sealed=False)
    executable = nth_trading_day(session, 2, after=True)
    base = {
        "variant": T2_VARIANT,
        "settings": {
            "hold_rule": cfg["hold_rule"],
            "buffer_formula": cfg["buffer_formula"],
        },
        "executable_from": cfg["executable_from"],
        "executable_date": None if executable is None else executable.isoformat(),
    }
    if bar_t is None or resistance is None or next_session is None:
        return {
            **base,
            "confirmed": False,
            "status": "unavailable",
            "reason": "missing_t_or_resistance",
            "information_ready": None,
        }
    bar_next = dataset.bar(ticker, next_session)
    atr_t = compute_atr(daily_t, 20) if not daily_t.empty else None
    close_t = _adjusted_parts(bar_t)["close"]
    close_next = _adjusted_parts(bar_next)["close"]
    buffer = frozen_hold_buffer(close_t, atr_t)
    judged = t2_holds(
        close_t=close_t,
        close_t1=close_next,
        resistance_high=resistance,
        buffer=buffer,
    )
    return {
        **base,
        "status": "active" if judged["available"] else "unavailable",
        "reason": None if judged["available"] else "missing_t2_inputs",
        "confirmed": judged["confirmed"],
        "hold_t": judged["hold_t"],
        "hold_t1": judged["hold_t1"],
        "close_t": judged["close_t"],
        "close_t1": judged["close_t1"],
        "atr20_t_frozen": atr_t,
        "buffer_frozen": judged["buffer_frozen"],
        "resistance_high_frozen": judged["resistance_high_frozen"],
        "information_ready": next_session.isoformat(),
    }


def attach_timing_candidates(
    dataset: OfflineOHLCV,
    events: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for event in events:
        item = dict(event)
        item["t1"] = evaluate_t1(dataset, item)
        item["t2"] = evaluate_t2(dataset, item)
        out.append(item)
    return out
