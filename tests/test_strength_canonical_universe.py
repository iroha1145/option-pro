from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.strength import scanner


def _history(size: int = 100) -> pd.DataFrame:
    step = np.arange(size, dtype=float)
    close = 20.0 + step
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": np.full(size, 1_000_000.0),
        }
    )


def test_theme_universe_keeps_primary_sector_separate_from_theme_membership() -> None:
    tickers, metadata = scanner._theme_universe()
    assert len(tickers) == len(set(tickers))
    assert "NVDA" in tickers
    assert metadata["NVDA"]["primary_sector_id"] == "semiconductors"
    assert {"semiconductors", "ai_cloud"}.issubset(set(metadata["NVDA"]["theme_ids"]))
    first = scanner._canonical_universe_version(tickers, metadata)
    second = scanner._canonical_universe_version(list(reversed(tickers)), metadata)
    assert first == second


def test_single_observation_has_no_cross_sectional_percentile() -> None:
    assert scanner._pct_rank([{"ticker": "AAA", "value": 10.0}], "value") == {}


def test_average_dollar_volume_uses_daily_close_times_volume() -> None:
    hist = _history()
    row = scanner._feature_row(
        "AAA",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert row is not None
    expected = round(float((hist["Close"].tail(20) * hist["Volume"].tail(20)).mean()))
    old_formula = round(float(hist["Close"].iloc[-1] * hist["Volume"].tail(20).mean()))
    assert row["avg_dollar_volume_20d"] == expected
    assert row["avg_dollar_volume_20d"] != old_formula
    assert row["avg_dollar_volume_20d_calculation_method"] == "mean_close_times_volume_20d"


def test_average_dollar_volume_requires_twenty_valid_samples() -> None:
    hist = _history()
    hist.loc[: len(hist) - 20, "Volume"] = np.nan
    row = scanner._feature_row(
        "AAA",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert row is not None
    assert row["avg_dollar_volume_20d"] is None
    assert row["avg_dollar_volume_20d_calculation_method"] == "unavailable"
