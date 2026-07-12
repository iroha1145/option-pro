from __future__ import annotations

import pandas as pd

from app.services.breakouts.feature_engine import compute_cumulative_dollar_volume


def test_cumulative_dollar_volume_sums_each_bars_typical_price() -> None:
    frame = pd.DataFrame(
        {
            "High": [12.0, 24.0],
            "Low": [8.0, 16.0],
            "Close": [10.0, 20.0],
            "Volume": [100.0, 200.0],
        }
    )

    result = compute_cumulative_dollar_volume(frame)

    assert result["value"] == 5_000.0
    assert result["cumulative_volume"] == 300.0
    assert result["calculation_method"] == "typical_price_volume"


def test_cumulative_dollar_volume_falls_back_to_close_and_missing_volume_is_null() -> None:
    close_only = pd.DataFrame(
        {"Close": [10.0, 20.0], "Volume": [100.0, 200.0]}
    )
    missing_volume = close_only.copy()
    missing_volume.loc[1, "Volume"] = float("nan")

    fallback = compute_cumulative_dollar_volume(close_only)
    unavailable = compute_cumulative_dollar_volume(missing_volume)

    assert fallback["value"] == 5_000.0
    assert fallback["calculation_method"] == "close_volume"
    assert unavailable["value"] is None
    assert unavailable["cumulative_volume"] is None
    assert unavailable["calculation_method"] == "unavailable"


def test_cumulative_dollar_volume_uses_close_only_for_rows_missing_high_low() -> None:
    frame = pd.DataFrame(
        {
            "High": [12.0, float("nan")],
            "Low": [8.0, float("nan")],
            "Close": [10.0, 20.0],
            "Volume": [100.0, 200.0],
        }
    )

    result = compute_cumulative_dollar_volume(frame)

    assert result["value"] == 5_000.0
    assert result["cumulative_volume"] == 300.0
    assert result["sample_count"] == 2
    assert result["calculation_method"] == "mixed_typical_close_volume"
