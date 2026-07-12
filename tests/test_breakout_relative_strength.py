from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.breakouts.relative_strength import (
    percentile_rank,
    relative_strength_features,
)


def _frame(start: float, daily_return: float) -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=100, freq="B")
    close = start * np.power(1 + daily_return, np.arange(len(index)))
    return pd.DataFrame({"Close": close}, index=index)


def test_relative_strength_scores_leadership_against_market_and_sector() -> None:
    result = relative_strength_features(
        _frame(100, 0.003),
        _frame(100, 0.001),
        _frame(100, 0.0015),
        sector_symbol="XLK",
    )

    assert result["relative_strength_structure"] > 50
    assert result["relative_strength_confirmation"] > 50
    assert result["sector_fit_score"] > 50
    assert result["relative_strength_status"] == "active"


def test_relative_strength_keeps_missing_sector_explicit() -> None:
    result = relative_strength_features(
        _frame(100, 0.001),
        _frame(100, 0.001),
    )
    assert result["sector_fit_score"] is None
    assert result["relative_strength_sector_symbol"] is None


def test_dollar_volume_percentile_requires_fixed_distribution_coverage() -> None:
    distribution = {f"T{index}": float(index) for index in range(40)}
    assert percentile_rank(30, distribution) > 70
    assert percentile_rank(10, {"A": 1, "B": 2}) is None
