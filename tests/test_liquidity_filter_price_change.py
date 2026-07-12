from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.breakouts.service import BreakoutRadarService


def test_liquidity_filter_uses_daily_dollar_values_not_latest_price_times_mean_volume() -> None:
    close = np.arange(1.0, 21.0)
    frame = pd.DataFrame(
        {"Close": close, "Volume": np.full(20, 1_000_000.0)}
    )

    production = BreakoutRadarService._average_dollar_volume(frame)
    old_formula = float(close[-1] * frame["Volume"].mean())

    assert production == 10_500_000.0
    assert old_formula == 20_000_000.0
    assert production < 15_000_000 < old_formula
