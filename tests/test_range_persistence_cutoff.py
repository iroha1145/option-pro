from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from app.services.technical.range_persistence import compute_range_persistence


def _frame(size: int = 300) -> pd.DataFrame:
    close = 50 + np.arange(size) * 0.15 + np.sin(np.arange(size) / 5)
    return pd.DataFrame(
        {"High": close + 1.0, "Low": close - 1.0, "Close": close},
        index=pd.date_range("2025-01-01", periods=size, freq="B"),
    )


def test_future_tail_does_not_change_original_cutoff_result() -> None:
    frame = _frame()
    cutoff_date = frame.index[220].date()
    cutoff = datetime.combine(
        cutoff_date,
        datetime.min.time().replace(hour=16),
        tzinfo=ZoneInfo("America/New_York"),
    )
    original = compute_range_persistence(frame.iloc[:221], cutoff=cutoff)
    appended = compute_range_persistence(frame, cutoff=cutoff)
    assert original == appended


def test_cutoff_must_be_timezone_aware() -> None:
    with pytest.raises(ValueError, match="timezone"):
        compute_range_persistence(_frame(), cutoff=datetime(2026, 7, 1, 16, 0))


def test_intraday_cutoff_excludes_same_day_unfinished_daily_bar() -> None:
    frame = _frame(220)
    day = frame.index[-1].date()
    cutoff = datetime(
        day.year,
        day.month,
        day.day,
        10,
        30,
        tzinfo=ZoneInfo("America/New_York"),
    )
    with_unfinished = compute_range_persistence(frame, cutoff=cutoff)
    completed_only = compute_range_persistence(frame.iloc[:-1], cutoff=cutoff)
    assert with_unfinished == completed_only
