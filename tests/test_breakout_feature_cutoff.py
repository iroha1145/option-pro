from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.feature_engine import (
    compute_feature_snapshot,
    trim_daily_bars,
    trim_intraday_bars,
)
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def _daily(size: int = 220) -> pd.DataFrame:
    close = 50 + np.arange(size) * 0.1
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1_000_000,
        },
        index=pd.date_range("2025-09-01", periods=size, freq="B"),
    )


def test_daily_excludes_unfinished_current_session() -> None:
    frame = _daily()
    current = frame.index[-1].date()
    cutoff = TemporalCutoff(
        event_at=datetime(current.year, current.month, current.day, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    visible = trim_daily_bars(frame, cutoff)
    assert visible.index[-1].date() < current


def test_intraday_cutoff_ignores_future_bars_and_future_append() -> None:
    day = datetime(2026, 7, 10, tzinfo=NY)
    index = pd.date_range(day.replace(hour=9, minute=30), periods=20, freq="5min")
    close = np.arange(len(index), dtype=float) + 100
    frame = pd.DataFrame(
        {
            "Open": close,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 1000,
        },
        index=index,
    )
    cutoff = TemporalCutoff(
        event_at=day.replace(hour=10, minute=30),
        session=MarketSession.REGULAR,
    )
    original = trim_intraday_bars(frame.loc[: day.replace(hour=10, minute=30)], cutoff)
    appended = trim_intraday_bars(frame, cutoff)
    pd.testing.assert_frame_equal(original, appended)
    assert appended.index.max() == pd.Timestamp(day.replace(hour=10, minute=25))
    assert pd.Timestamp(day.replace(hour=10, minute=30)) not in appended.index


def test_premarket_cutoff_never_reads_regular_session() -> None:
    day = datetime(2026, 7, 10, tzinfo=NY)
    index = pd.DatetimeIndex(
        [
            day.replace(hour=8, minute=0),
            day.replace(hour=9, minute=25),
            day.replace(hour=9, minute=30),
            day.replace(hour=10, minute=0),
        ]
    )
    frame = pd.DataFrame(
        {
            "Open": [10, 11, 99, 100],
            "High": [11, 12, 100, 101],
            "Low": [9, 10, 98, 99],
            "Close": [10.5, 11.5, 99.5, 100.5],
            "Volume": [100, 200, 99999, 99999],
        },
        index=index,
    )
    cutoff = TemporalCutoff(
        event_at=day.replace(hour=9, minute=25),
        session=MarketSession.PREMARKET,
    )
    visible = trim_intraday_bars(frame, cutoff)
    assert visible.index.max().hour == 8
    assert visible.index.max().minute == 0
    assert visible["Close"].max() < 20


def test_feature_snapshot_is_unchanged_by_bars_after_cutoff() -> None:
    day = datetime(2026, 7, 10, tzinfo=NY)
    index = pd.date_range(day.replace(hour=9, minute=30), periods=18, freq="5min")
    close = np.linspace(100, 105, len(index))
    intraday = pd.DataFrame(
        {
            "Open": close - 0.1,
            "High": close + 0.4,
            "Low": close - 0.4,
            "Close": close,
            "Volume": 10_000,
        },
        index=index,
    )
    cutoff = TemporalCutoff(
        event_at=day.replace(hour=10, minute=30),
        session=MarketSession.REGULAR,
        completed_daily_session=_daily().index[-1].date(),
    )
    first = compute_feature_snapshot(
        daily=_daily(),
        intraday=intraday.loc[: cutoff.event_at],
        cutoff=cutoff,
    )
    second = compute_feature_snapshot(daily=_daily(), intraday=intraday, cutoff=cutoff)
    assert first == second


def test_include_current_bar_is_an_explicit_opt_in() -> None:
    day = datetime(2026, 7, 10, tzinfo=NY)
    index = pd.date_range(day.replace(hour=10, minute=20), periods=4, freq="5min")
    frame = pd.DataFrame(
        {
            "Open": [100, 101, 102, 103],
            "High": [101, 102, 103, 104],
            "Low": [99, 100, 101, 102],
            "Close": [100.5, 101.5, 102.5, 103.5],
            "Volume": [1000, 1000, 1000, 1000],
        },
        index=index,
    )
    complete_only = TemporalCutoff(
        event_at=day.replace(hour=10, minute=30),
        session=MarketSession.REGULAR,
        include_current_bar=False,
    )
    partial_allowed = complete_only.model_copy(update={"include_current_bar": True})
    assert trim_intraday_bars(frame, complete_only).index.max().minute == 25
    assert trim_intraday_bars(frame, partial_allowed).index.max().minute == 30
