from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.feature_engine import compute_time_of_day_rvol
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def _panel(history_sessions: int = 6, current_multiplier: float = 2.0) -> pd.DataFrame:
    current = datetime(2026, 7, 10, tzinfo=NY)
    rows = []
    index = []
    for offset in range(history_sessions, -1, -1):
        day = current - timedelta(days=offset)
        if day.weekday() >= 5:
            continue
        multiplier = current_multiplier if day.date() == current.date() else 1.0
        for hour, minute in ((9, 35), (9, 40), (9, 45)):
            index.append(day.replace(hour=hour, minute=minute))
            rows.append(
                {
                    "Open": 10,
                    "High": 11,
                    "Low": 9,
                    "Close": 10,
                    "Volume": 1000 * multiplier,
                }
            )
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


def test_rvol_compares_only_same_minute_of_session() -> None:
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 9, 40, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    result = compute_time_of_day_rvol(
        _panel(10),
        cutoff,
        lookback_sessions=5,
        min_sessions=3,
    )
    assert result["rvol_time_of_day"] == 2.0
    assert result["observed_cumulative_volume"] == 4000.0
    assert result["expected_cumulative_volume"] == 2000.0
    assert result["comparison_sessions"] == 5


def test_insufficient_history_returns_null_not_one() -> None:
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 9, 40, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    result = compute_time_of_day_rvol(
        _panel(2),
        cutoff,
        lookback_sessions=20,
        min_sessions=5,
    )
    assert result["rvol_time_of_day"] is None
    assert result["quality"] == 0.0


def test_zero_volume_is_missing_not_real_zero() -> None:
    frame = _panel(10)
    historical = pd.Index(frame.index.date) < datetime(2026, 7, 10).date()
    first_historical = frame.loc[historical].index[0]
    frame.loc[first_historical, "Volume"] = 0
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 9, 40, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    result = compute_time_of_day_rvol(
        frame,
        cutoff,
        lookback_sessions=10,
        min_sessions=3,
    )
    assert result["comparison_sessions"] < 10
    assert result["expected_cumulative_volume"] == 2000.0


def test_premarket_does_not_use_full_day_rvol() -> None:
    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 8, 30, tzinfo=NY),
        session=MarketSession.PREMARKET,
    )
    result = compute_time_of_day_rvol(_panel(10), cutoff)
    assert result["rvol_time_of_day"] is None
    assert result["comparison_sessions"] == 0
