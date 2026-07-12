"""Point-in-time feature extraction for breakout events."""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
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
    from app.services.market_calendar import is_trading_day as _is_trading_day

    candidate = observed - timedelta(days=1)
    while not _is_trading_day(candidate):
        candidate -= timedelta(days=1)
    return candidate


def completed_daily_session(cutoff: TemporalCutoff) -> date:
    if cutoff.completed_daily_session is not None:
        return cutoff.completed_daily_session
    from app.services.market_calendar import (
        early_close_minutes as _early_close_minutes,
        is_trading_day as _is_trading_day,
    )

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
    from app.services.market_calendar import early_close_minutes as _early_close_minutes

    return _early_close_minutes(day) or 16 * 60


def regular_session_close(day: date) -> datetime:
    """Return the actual US regular-session close for ``day`` in UTC."""

    close_minutes = _regular_close_minutes(day)
    local = datetime(
        day.year,
        day.month,
        day.day,
        close_minutes // 60,
        close_minutes % 60,
        tzinfo=NEW_YORK,
    )
    return local.astimezone(timezone.utc)


def daily_data_through(frame: pd.DataFrame) -> datetime | None:
    """Map the last delivered daily bar to that session's actual close."""

    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("daily bars require a DatetimeIndex")
    return regular_session_close(frame.index.max().date())


def intraday_data_through(
    frame: pd.DataFrame,
    *,
    interval_minutes: int = 5,
) -> datetime | None:
    """Return the end of the last Yahoo bar (whose timestamp is bar start)."""

    if interval_minutes < 1:
        raise ValueError("interval_minutes must be positive")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("intraday bars require a timezone-aware DatetimeIndex")
    bar_end = frame.index.max() + pd.Timedelta(minutes=interval_minutes)
    return bar_end.to_pydatetime().astimezone(timezone.utc)


def _intraday_session_mask(
    index: pd.DatetimeIndex,
    cutoff: TemporalCutoff,
    *,
    interval_minutes: int,
) -> np.ndarray:
    local_index = index.tz_convert(NEW_YORK)
    event_ts = pd.Timestamp(cutoff.event_at.astimezone(NEW_YORK))
    if cutoff.include_current_bar:
        mask = np.asarray(local_index <= event_ts)
    else:
        bar_ends = local_index + pd.Timedelta(minutes=interval_minutes)
        mask = np.asarray(bar_ends <= event_ts)
    minutes = np.asarray(local_index.hour * 60 + local_index.minute)
    dates = pd.Index(local_index.date)
    close_minutes = np.array([_regular_close_minutes(day) for day in dates])
    if cutoff.session is MarketSession.PREMARKET:
        mask &= (minutes >= 4 * 60) & (minutes < 9 * 60 + 30)
    elif cutoff.session is MarketSession.REGULAR:
        mask &= (minutes >= 9 * 60 + 30) & (minutes < close_minutes)
    elif cutoff.session is MarketSession.POSTMARKET:
        mask &= (minutes >= close_minutes) & (minutes < 20 * 60)
    return mask


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
    mask = _intraday_session_mask(
        clean.index,
        cutoff,
        interval_minutes=interval_minutes,
    )
    return clean.loc[mask]


def trim_intraday_liquidity_bars(
    frame: pd.DataFrame,
    cutoff: TemporalCutoff,
    interval_minutes: int = 5,
) -> pd.DataFrame:
    """Bound bars for liquidity even when High/Low are not available.

    Rows are intentionally not filled or silently dropped: the cumulative
    dollar-volume calculator must return unavailable when any required Close or
    Volume observation is missing.
    """

    if interval_minutes < 1:
        raise ValueError("interval_minutes must be positive")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return pd.DataFrame()
    if not isinstance(frame.index, pd.DatetimeIndex) or frame.index.tz is None:
        raise ValueError("intraday bars require a timezone-aware DatetimeIndex")
    lowered = {str(column).lower(): column for column in frame.columns}
    if "close" not in lowered or "volume" not in lowered:
        return pd.DataFrame()
    result = pd.DataFrame(index=frame.index)
    for name in ("open", "high", "low", "close", "volume"):
        source = lowered.get(name)
        if source is not None:
            result[name.title()] = pd.to_numeric(
                frame[source], errors="coerce"
            ).replace([np.inf, -np.inf], np.nan)
    result = result[~result.index.duplicated(keep="last")].sort_index()
    mask = _intraday_session_mask(
        result.index,
        cutoff,
        interval_minutes=interval_minutes,
    )
    return result.loc[mask]


def compute_average_dollar_volume(
    frame: pd.DataFrame,
    *,
    lookback: int = 20,
) -> dict[str, Any]:
    """Calculate ADV from the last 20 valid daily ``Close * Volume`` values."""

    unavailable = {
        "value": None,
        "calculation_method": "unavailable",
        "sample_count": 0,
    }
    if lookback < 1:
        raise ValueError("lookback must be positive")
    if (
        not isinstance(frame, pd.DataFrame)
        or frame.empty
        or "Close" not in frame.columns
        or "Volume" not in frame.columns
    ):
        return unavailable
    close = pd.to_numeric(frame["Close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    volume = pd.to_numeric(frame["Volume"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    valid = close.notna() & volume.notna() & (close > 0) & (volume >= 0)
    values = (close.loc[valid] * volume.loc[valid]).tail(lookback)
    if len(values) < lookback:
        return {**unavailable, "sample_count": int(len(values))}
    value = _finite(values.mean())
    if value is None:
        return {**unavailable, "sample_count": int(len(values))}
    return {
        "value": value,
        "calculation_method": "mean_close_volume",
        "sample_count": int(len(values)),
    }


def compute_cumulative_dollar_volume(frame: pd.DataFrame) -> dict[str, Any]:
    """Sum each complete intraday bar's dollar volume without filling gaps."""

    unavailable = {
        "value": None,
        "cumulative_volume": None,
        "calculation_method": "unavailable",
        "sample_count": 0,
    }
    if (
        not isinstance(frame, pd.DataFrame)
        or frame.empty
        or "Close" not in frame.columns
        or "Volume" not in frame.columns
    ):
        return unavailable
    close = pd.to_numeric(frame["Close"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    volume = pd.to_numeric(frame["Volume"], errors="coerce").replace(
        [np.inf, -np.inf], np.nan
    )
    if close.isna().any() or volume.isna().any() or (close <= 0).any() or (volume < 0).any():
        return {**unavailable, "sample_count": int(len(frame))}
    method = "close_volume"
    price = close.astype(float).copy()
    if "High" in frame.columns and "Low" in frame.columns:
        high = pd.to_numeric(frame["High"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        low = pd.to_numeric(frame["Low"], errors="coerce").replace(
            [np.inf, -np.inf], np.nan
        )
        typical_rows = high.notna() & low.notna() & (high >= low)
        if typical_rows.any():
            price.loc[typical_rows] = (
                high.loc[typical_rows]
                + low.loc[typical_rows]
                + close.loc[typical_rows]
            ) / 3.0
        if typical_rows.all():
            method = "typical_price_volume"
        elif typical_rows.any():
            method = "mixed_typical_close_volume"
    value = _finite((price * volume).sum())
    cumulative_volume = _finite(volume.sum())
    if value is None or cumulative_volume is None:
        return {**unavailable, "sample_count": int(len(frame))}
    return {
        "value": value,
        "cumulative_volume": cumulative_volume,
        "calculation_method": method,
        "sample_count": int(len(frame)),
    }


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
    if not cutoff.include_current_bar:
        target_minute = (target_minute // 5) * 5 - 5
    if cutoff.session is not MarketSession.REGULAR or target_minute < 9 * 60 + 30:
        return empty
    minutes = local.index.hour * 60 + local.index.minute
    regular = local[
        (minutes >= 9 * 60 + 30)
        & np.array(
            [
                minute < _regular_close_minutes(day)
                for minute, day in zip(minutes, local.index.date)
            ]
        )
    ].copy()
    regular["Volume"] = regular["Volume"].where(regular["Volume"] > 0)
    current_day = event.date()

    def cumulative_for(day: date) -> float | None:
        if target_minute > _regular_close_minutes(day):
            return None
        session = regular[pd.Index(regular.index.date) == day]
        through = session[
            (session.index.hour * 60 + session.index.minute) <= target_minute
        ]
        if through.empty or through["Volume"].isna().any():
            return None
        observed_minutes = {
            int(timestamp.hour * 60 + timestamp.minute)
            for timestamp in through.index
        }
        expected_minutes = set(range(9 * 60 + 30, target_minute + 1, 5))
        if observed_minutes != expected_minutes:
            return None
        return _finite(through["Volume"].sum())

    observed = cumulative_for(current_day)
    history: list[float] = []
    from app.services.market_calendar import is_trading_day as _is_trading_day

    for day in sorted(
        {
            day
            for day in regular.index.date
            if day < current_day and _is_trading_day(day)
        },
        reverse=True,
    ):
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
    opening_range_minutes: int = 30,
) -> dict[str, Any]:
    if opening_range_minutes < 5 or opening_range_minutes % 5 != 0:
        raise ValueError("opening_range_minutes must be a positive multiple of 5")
    event_local = cutoff.event_at.astimezone(NEW_YORK)
    daily_visible = trim_daily_bars(daily, cutoff)
    intraday_visible = trim_intraday_bars(intraday, cutoff)
    liquidity_visible = trim_intraday_liquidity_bars(intraday, cutoff)
    liquidity_session = (
        liquidity_visible[
            pd.Index(liquidity_visible.index.tz_convert(NEW_YORK).date)
            == event_local.date()
        ]
        if not liquidity_visible.empty
        else pd.DataFrame()
    )
    cumulative_dollar = compute_cumulative_dollar_volume(liquidity_session)
    data_through = intraday_data_through(
        liquidity_visible if not liquidity_visible.empty else intraday_visible
    )
    provenance = {
        "raw_as_of": data_through.isoformat() if data_through is not None else None,
        "data_through": data_through.isoformat() if data_through is not None else None,
        "feature_cutoff_at": cutoff.event_at.astimezone(timezone.utc).isoformat(),
        "calculation_cutoff_at": cutoff.event_at.isoformat(),
    }
    liquidity_payload = {
        "cumulative_volume": cumulative_dollar["cumulative_volume"],
        "cumulative_dollar_volume": cumulative_dollar["value"],
        "cumulative_dollar_volume_calculation_method": cumulative_dollar[
            "calculation_method"
        ],
        "cumulative_dollar_volume_sample_count": cumulative_dollar["sample_count"],
    }
    if intraday_visible.empty:
        return {
            "status": "insufficient_data",
            **liquidity_payload,
            **provenance,
            "warnings": ["no_complete_intraday_bars"],
        }
    current_session = intraday_visible[
        pd.Index(intraday_visible.index.tz_convert(NEW_YORK).date) == event_local.date()
    ]
    if current_session.empty:
        return {
            "status": "insufficient_data",
            **liquidity_payload,
            **provenance,
            "warnings": ["no_complete_current_session_bars"],
        }
    bar = current_session.iloc[-1]
    atr20 = compute_atr(daily_visible, 20)
    vwap = compute_vwap(current_session)
    close = _finite(bar["Close"])
    previous_close = _finite(daily_visible["Close"].iloc[-1]) if not daily_visible.empty else None
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
    lower_wick = (
        _finite((min(open_, close) - low) / candle_range)
        if None not in (close, open_, low, candle_range) and candle_range > 0
        else None
    )
    local_index = current_session.index.tz_convert(NEW_YORK)
    local_minutes = local_index.hour * 60 + local_index.minute
    opening_start = 9 * 60 + 30
    opening_end = opening_start + opening_range_minutes
    opening = current_session[
        (local_minutes >= opening_start) & (local_minutes < opening_end)
    ]
    opening_minutes = {
        int(timestamp.hour * 60 + timestamp.minute)
        for timestamp in opening.index.tz_convert(NEW_YORK)
    }
    opening_complete = bool(
        cutoff.session is MarketSession.REGULAR
        and event_local.hour * 60 + event_local.minute >= opening_end
        and opening_minutes == set(range(opening_start, opening_end, 5))
    )
    opening_high = _finite(opening["High"].max()) if opening_complete else None
    opening_low = _finite(opening["Low"].min()) if opening_complete else None
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
        "lower_wick_ratio": lower_wick,
        "session_change_pct": (
            _finite((close / previous_close - 1.0) * 100.0)
            if close is not None and previous_close
            else None
        ),
        **liquidity_payload,
        "distance_from_vwap_atr": (
            _finite((close - vwap) / atr20)
            if None not in (close, vwap, atr20) and atr20 > 0
            else None
        ),
        "opening_range_high": opening_high,
        "opening_range_low": opening_low,
        "opening_range_complete": opening_complete,
        **rvol,
        **provenance,
        "warnings": (
            ["cumulative_dollar_volume_unavailable"]
            if cumulative_dollar["value"] is None
            else []
        ),
        "session": cutoff.session.value,
        "version": "breakout-features-v1",
    }
