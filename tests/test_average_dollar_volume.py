from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.breakouts.feature_engine import compute_average_dollar_volume


def test_adv20_is_mean_of_each_days_close_times_volume() -> None:
    close = np.arange(1.0, 21.0)
    volume = np.arange(1_000.0, 21_000.0, 1_000.0)
    frame = pd.DataFrame({"Close": close, "Volume": volume})

    result = compute_average_dollar_volume(frame)

    assert result["value"] == round(float(np.mean(close * volume)), 8)
    assert result["calculation_method"] == "mean_close_volume"
    assert result["sample_count"] == 20


def test_adv20_uses_last_twenty_valid_values_and_never_fills_zero() -> None:
    frame = pd.DataFrame(
        {
            "Close": [10.0] * 21,
            "Volume": [1_000.0] * 20 + [float("nan")],
        }
    )
    valid = compute_average_dollar_volume(frame)
    insufficient = compute_average_dollar_volume(frame.iloc[1:])

    assert valid["value"] == 10_000.0
    assert insufficient["value"] is None
    assert insufficient["calculation_method"] == "unavailable"
    assert insufficient["sample_count"] == 19
