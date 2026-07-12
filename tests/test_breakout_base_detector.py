from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.base_detector import detect_base
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def _base_frame(size: int = 60) -> pd.DataFrame:
    index = pd.date_range("2026-03-02", periods=size, freq="B")
    phase = np.arange(size)
    close = 95.0 + np.sin(phase * np.pi / 3.0) * 3.5 + phase * 0.01
    volume = np.linspace(2_000_000, 1_100_000, size)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1.0,
            "Low": close - 1.0,
            "Close": close,
            "Volume": volume,
        },
        index=index,
    )


def _cutoff(frame: pd.DataFrame) -> TemporalCutoff:
    completed = frame.index[-1].date()
    return TemporalCutoff(
        event_at=datetime(
            completed.year, completed.month, completed.day, 16, 30, tzinfo=NY
        ),
        completed_daily_session=completed,
        session=MarketSession.POSTMARKET,
    )


def test_detects_repeatable_resistance_and_stable_pivot_id() -> None:
    frame = _base_frame()
    first = detect_base("TEST", frame, _cutoff(frame))
    second = detect_base("TEST", frame, _cutoff(frame))
    assert first is not None
    assert first.pivot_touch_count >= 2
    assert first.resistance_zone.low < first.resistance_zone.high
    assert first.support_zone is not None
    assert first.pivot_id == second.pivot_id
    assert 0 <= first.quality <= 1


def test_future_append_does_not_change_original_cutoff_structure() -> None:
    frame = _base_frame()
    cutoff = _cutoff(frame)
    future = _base_frame(65)
    first = detect_base("TEST", frame, cutoff)
    second = detect_base("TEST", future, cutoff)
    assert first is not None and second is not None
    assert first.pivot_id == second.pivot_id
    assert first.resistance_zone == second.resistance_zone


def test_requires_minimum_base_history() -> None:
    frame = _base_frame(9)
    assert detect_base("TEST", frame, _cutoff(frame)) is None
