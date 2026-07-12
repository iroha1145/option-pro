from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from app.services.breakouts.feature_engine import (
    compute_feature_snapshot,
    intraday_data_through,
)
from app.services.breakouts.models import MarketSession, TemporalCutoff


NY = ZoneInfo("America/New_York")


def _bars() -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [
            datetime(2026, 7, 10, 10, 10, tzinfo=NY),
            datetime(2026, 7, 10, 10, 15, tzinfo=NY),
        ]
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [101.0, 102.0],
            "Low": [99.0, 100.0],
            "Close": [100.5, 101.5],
            "Volume": [1_000.0, 2_000.0],
        },
        index=index,
    )


def test_five_minute_data_through_is_last_bar_end_not_request_time() -> None:
    frame = _bars()
    assert intraday_data_through(frame) == datetime(
        2026,
        7,
        10,
        14,
        20,
        tzinfo=timezone.utc,
    )

    cutoff = TemporalCutoff(
        event_at=datetime(2026, 7, 10, 10, 30, tzinfo=NY),
        session=MarketSession.REGULAR,
    )
    daily = pd.DataFrame(
        {
            "Open": [99.0],
            "High": [101.0],
            "Low": [98.0],
            "Close": [100.0],
            "Volume": [1_000.0],
        },
        index=pd.DatetimeIndex(["2026-07-09"]),
    )
    features = compute_feature_snapshot(daily=daily, intraday=frame, cutoff=cutoff)

    assert features["data_through"] == "2026-07-10T14:20:00+00:00"
    assert features["raw_as_of"] == features["data_through"]
    assert features["feature_cutoff_at"] == "2026-07-10T14:30:00+00:00"
