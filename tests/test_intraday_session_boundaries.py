from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.feature_engine import trim_intraday_bars
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def _frame(day: datetime, times: list[tuple[int, int]]) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [day.replace(hour=hour, minute=minute) for hour, minute in times]
    )
    return pd.DataFrame(
        {
            "Open": [100.0] * len(index),
            "High": [101.0] * len(index),
            "Low": [99.0] * len(index),
            "Close": [100.5] * len(index),
            "Volume": [1_000.0] * len(index),
        },
        index=index,
    )


def test_normal_session_boundaries_use_bar_start_and_completion_end() -> None:
    day = datetime(2026, 7, 10, tzinfo=NY)
    frame = _frame(day, [(9, 25), (9, 30), (15, 55), (16, 0)])
    premarket = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=9, minute=30),
            session=MarketSession.PREMARKET,
        ),
    )
    regular = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=16, minute=0),
            session=MarketSession.REGULAR,
        ),
    )
    post_at_open = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=16, minute=0),
            session=MarketSession.POSTMARKET,
        ),
    )
    post_complete = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=16, minute=5),
            session=MarketSession.POSTMARKET,
        ),
    )

    assert [(item.hour, item.minute) for item in premarket.index] == [(9, 25)]
    assert [(item.hour, item.minute) for item in regular.index] == [
        (9, 30),
        (15, 55),
    ]
    assert post_at_open.empty
    assert [(item.hour, item.minute) for item in post_complete.index] == [(16, 0)]
