from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from app.services.breakouts.feature_engine import daily_data_through


def test_daily_data_through_uses_actual_early_close() -> None:
    frame = pd.DataFrame(
        {"Close": [100.0], "Volume": [1_000.0]},
        index=pd.DatetimeIndex(["2026-11-27"]),
    )

    assert daily_data_through(frame) == datetime(
        2026,
        11,
        27,
        18,
        0,
        tzinfo=timezone.utc,
    )
