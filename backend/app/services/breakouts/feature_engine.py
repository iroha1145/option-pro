"""Point-in-time feature extraction for breakout events."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.models import MarketSession, TemporalCutoff


NEW_YORK = ZoneInfo("America/New_York")


def _finite(value: Any, ndigits: int = 8) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, ndigits) if math.isfinite(number) else None


def _clean_frame(frame: pd.DataFrame, *, require_volume: bool = False) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    lowered = {str(column).lower(): column for column in frame.columns}
    required = ["open", "high", "low", "close"]
    if require_volume:
        required.append("volume")
    if any(name not in lowered for name in required):
        return pd.DataFrame()
    result = pd.DataFrame(index=frame.index)
    for name in ("open", "high", "low", "close", "volume"):
        source = lowered.get(name)
        if source is not None:
            result[name.title()] = pd.to_numeric(
                frame[source], errors="coerce"
            ).replace([np.inf, -np.inf], np.nan)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    valid = (
        result[["Open", "High", "Low", "Close"]].notna().all(axis=1)
        & (result["High"] >= result["Low"])
        & (result["Close"] <= result["High"])
        & (result["Close"] >= result["Low"])
    )
    return result.loc[valid]


def _previous_trading_day(observed: date) -> date:
    from app.api.market import _is_trading_day

    candidate = observed - timedelta(days=1)
    while not _is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def completed_daily_session(cutoff: TemporalCutoff) -> date:
    if cutoff.completed_daily_session is not None:
        return cutoff.completed_daily_session
    from app.api.market import _early_close_minutes, _is_trading_day

    local = cutoff.event_at.astimezone(NEW_YORK)
    observed = local.date()
    if not _is_trading_day(observed):
        return _previous_trading_day(observed)
    close_minutes = _early_close_minutes(observed) or 16 * 60
    if local.hour * 60 + local.minute < close_minutes:
        return _previous_trading_day(observed)
    return observed


def trim_daily_bars(frame: pd.DataFrame, cutoff: TemporalCutoff) -> pd.DataFrame:
    clean = _clean_frame(frame)
    if clean.empty:
        return clean
    if not isinstance(clean.index, pd.DatetimeIndex):
        raise ValueError("daily bars require a DatetimeIndex")
    completed = completed_daily_session(cutoff)
    return clean[pd.Index(clean.index.date) <= completed]


def _regular_close_minutes(day: date) -> int:
    from app.api.market import _early_close_minutes

    return _early_close_minutes(day) or 16 * 60


def trim_intraday_bars(
    frame: pd.DataFrame,
    cutoff: TemporalCutoff,
    interval_minutes: int = 5,
) -> pd.DataFrame:
    if interval_minutes < 1:
        raise ValueError("interval_minutes must be positive")
    clean = _clean_frame(frame)
    if clean.empty:
        return clean
    if not isinstance(clean.index, pd.DatetimeIndex) or clean.index.tz is None:
        raise ValueError("intraday bars require a timezone-aware DatetimeIndex")
    local_index = clean.index.tz_convert(NEW_YORK)
    event = cutoff.event_at.astimezone(NEW_YORK)
    event_ts = pd.Timestamp(event)
    aligned = event_ts.floor(f"{interval_minutes}min")
    visible_through = event_ts if cutoff.include_current_bar else aligned
    mask = local_index <= visible_through
    minutes = local_index.hour * 60 + local_index.minute
    dates = pd.Index(local_index.date)
    close_minutes = np.array([_regular_close_minutes(day) for day in dates])
    if cutoff.session is MarketSession.PREMARKET:
        mask &= (minutes >= 4 * 60) & (minutes < 9 * 60 + 30)
    elif cutoff.session is MarketSession.REGULAR:
        mask &= (minutes >= 9 * 60 + 30) & (minutes <= close_minutes)
    elif cutoff.session is MarketSession.POSTMARKET:
        mask &= (minutes > close_minutes) & (minutes < 20 * 60)
    return clean.loc[mask]


def compute_atr(frame: pd.DataFrame, period: int = 20) -> float | None:
    clean = _clean_frame(frame)
    if len(clean) < period + 1:
        return None
    previous = clean["Close"].shift(1)
    true_range = pd.concat(
        [
            (clean["High"] - clean["Low"]).abs(),
            (clean["High"] - previous).abs(),
            (clean["Low"] - previous).abs(),
        ],
        axis=1,
    ).max(axis=1)
    value = true_range.rolling(period, min_periods=period).mean().iloc[-1]
    return _finite(value)


def compute_clv(open_: Any, high: Any, low: Any, close: Any) -> float | None:
    high_value, low_value, close_value = map(_finite, (high, low, close))
    if None in (high_value, low_value, close_value) or high_value == low_value:
        return None
    return _finite(
        ((close_value - low_value) - (high_value - close_value))
        / (high_value - low_value)
    )


def compute_vwap(frame: pd.DataFrame) -> float | None:
    clean = _clean_frame(frame, require_volume=True)
    if clean.empty:
        return None
    volume = clean["Volume"].where(clean["Volume"] > 0)
    valid = volume.notna()
    if not valid.any():
        return None
    typical = (clean["High"] + clean["Low"] + clean["Close"]) / 3.0
    denominator = float(volume.loc[valid].sum())
    if denominator <= 0:
        return None
    return _finite(float((typical.loc[valid] * volume.loc[valid]).sum()) / denominator)


def compute_time_of_day_rvol(
    frame: pd.DataFrame,
    cutoff: TemporalCutoff,
    lookback_sessions: int = 20,
    min_sessions: int = 5,
) -> dict[str, Any]:
    if lookback_sessions < 1 or min_sessions < 1:
        raise ValueError("session counts must be positive")
    clean = _clean_frame(frame, require_volume=True)
    empty = {
        "rvol_time_of_day": None,
        "rvol_percentile": None,
        "rvol_robust_z": None,
        "expected_cumulative_volume": None,
        "observed_cumulative_volume": None,
        "comparison_sessions": 0,
        "quality": 0.0,
    }
    if clean.empty or not isinstance(clean.index, pd.DatetimeIndex) or clean.index.tz is None:
        return empty
    local = clean.copy()
    local.index = local.index.tz_convert(NEW_YORK)
    event = cutoff.event_at.astimezone(NEW_YORK)
    target_minute = event.hour * 60 + event.minute
    if cutoff.session is not MarketSession.REGULAR:
        return empty
    minutes = local.index.hour * 60 + local.index.minute
    regular = local[
        (minutes >= 9 * 60 + 30)
        & np.array(
            [
                minute <= _regular_close_minutes(day)
                for minute, day in zip(minutes, local.index.date)
            ]
        )
    ].copy()
    regular["Volume"] = regular["Volume"].where(regular["Volume"] > 0)
    current_day = event.date()

    def cumulative_for(day: date) -> float | None:
        session = regular[pd.Index(regular.index.date) == day]
        through = session[
            (session.index.hour * 60 + session.index.minute) <= target_minute
        ]
        if through.empty or through["Volume"].isna().any():
            return None
        last_minute = int(through.index[-1].hour * 60 + through.index[-1].minute)
        if last_minute != target_minute:
            return None
        return _finite(through["Volume"].sum())

    observed = cumulative_for(current_day)
    history: list[float] = []
    for day in sorted({day for day in regular.index.date if day < current_day}, reverse=True):
        value = cumulative_for(day)
        if value is not None:
            history.append(value)
        if len(history) >= lookback_sessions:
            break
    if observed is None or len(history) < min_sessions:
        return {**empty, "observed_cumulative_volume": observed, "comparison_sessions": len(history)}
    expected = float(np.median(history))
    if expected <= 0:
        return {**empty, "observed_cumulative_volume": observed, "comparison_sessions": len(history)}
    below = sum(value < observed for value in history)
    tied = sum(value == observed for value in history)
    median = expected
    mad = float(np.median(np.abs(np.asarray(history) - median)))
    robust_z = (observed - median) / (1.4826 * mad) if mad > 0 else None
    return {
        "rvol_time_of_day": _finite(observed / expected),
        "rvol_percentile": _finite((below + 0.5 * tied) / len(history) * 100.0),
        "rvol_robust_z": _finite(robust_z),
        "expected_cumulative_volume": _finite(expected),
        "observed_cumulative_volume": observed,
        "comparison_sessions": len(history),
        "quality": round(min(1.0, len(history) / lookback_sessions), 6),
    }


def compute_feature_snapshot(
    *,
    daily: pd.DataFrame,
    intraday: pd.DataFrame,
    cutoff: TemporalCutoff,
) -> dict[str, Any]:
    daily_visible = trim_daily_bars(daily, cutoff)
    intraday_visible = trim_intraday_bars(intraday, cutoff)
    if intraday_visible.empty:
        return {
            "status": "insufficient_data",
            "calculation_cutoff_at": cutoff.event_at.isoformat(),
            "warnings": ["no_complete_intraday_bars"],
        }
    bar = intraday_visible.iloc[-1]
    atr20 = compute_atr(daily_visible, 20)
    vwap = compute_vwap(intraday_visible)
    close = _finite(bar["Close"])
    previous_close = _finite(daily_visible["Close"].iloc[-1]) if not daily_visible.empty else None
    cumulative_volume = _finite(
        intraday_visible["Volume"].where(intraday_visible["Volume"] > 0).sum()
    )
    cumulative_dollar = (
        _finite(cumulative_volume * close)
        if cumulative_volume is not None and close is not None
        else None
    )
    rvol = compute_time_of_day_rvol(intraday, cutoff)
    high, low, open_ = (_finite(bar[name]) for name in ("High", "Low", "Open"))
    candle_range = high - low if high is not None and low is not None else None
    body_ratio = (
        _finite(abs(close - open_) / candle_range)
        if None not in (close, open_, candle_range) and candle_range > 0
        else None
    )
    upper_wick = (
        _finite((high - max(open_, close)) / candle_range)
        if None not in (close, open_, high, candle_range) and candle_range > 0
        else None
    )
    return {
        "status": "active",
        "event_price": close,
        "close": close,
        "open": open_,
        "high": high,
        "low": low,
        "atr20": atr20,
        "vwap": vwap,
        "close_location_value": compute_clv(open_, high, low, close),
        "candle_body_ratio": body_ratio,
        "upper_wick_ratio": upper_wick,
        "session_change_pct": (
            _finite((close / previous_close - 1.0) * 100.0)
            if close is not None and previous_close
            else None
        ),
        "cumulative_volume": cumulative_volume,
        "cumulative_dollar_volume": cumulative_dollar,
        "distance_from_vwap_atr": (
            _finite((close - vwap) / atr20)
            if None not in (close, vwap, atr20) and atr20 > 0
            else None
        ),
        **rvol,
        "raw_as_of": cutoff.event_at.isoformat(),
        "feature_cutoff_at": cutoff.event_at.isoformat(),
        "calculation_cutoff_at": cutoff.event_at.isoformat(),
        "session": cutoff.session.value,
        "version": "breakout-features-v1",
    }
