from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.feature_engine import trim_intraday_bars
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def test_early_close_moves_regular_and_postmarket_boundary_to_1300() -> None:
    day = datetime(2026, 11, 27, tzinfo=NY)
    index = pd.DatetimeIndex(
        [day.replace(hour=12, minute=55), day.replace(hour=13, minute=0)]
    )
    frame = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [1_000.0, 1_000.0],
        },
        index=index,
    )

    regular = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=13, minute=0),
            session=MarketSession.REGULAR,
        ),
    )
    post_at_open = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=13, minute=0),
            session=MarketSession.POSTMARKET,
        ),
    )
    post_complete = trim_intraday_bars(
        frame,
        TemporalCutoff(
            event_at=day.replace(hour=13, minute=5),
            session=MarketSession.POSTMARKET,
        ),
    )

    assert [(item.hour, item.minute) for item in regular.index] == [(12, 55)]
    assert post_at_open.empty
    assert [(item.hour, item.minute) for item in post_complete.index] == [(13, 0)]
