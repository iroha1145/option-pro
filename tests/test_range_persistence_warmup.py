from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.technical.range_persistence import compute_range_persistence


def _frame(size: int) -> pd.DataFrame:
    close = np.linspace(50.0, 90.0, size)
    return pd.DataFrame(
        {"High": close + 1.0, "Low": close - 1.0, "Close": close},
        index=pd.date_range("2025-01-01", periods=size, freq="B"),
    )


def test_default_warmup_requires_175_valid_bars() -> None:
    before = compute_range_persistence(_frame(174))
    ready = compute_range_persistence(_frame(175))
    assert before["status"] == "insufficient_history"
    assert before["range_persistence"] is None
    assert ready["status"] == "active"
    assert 0 <= ready["range_persistence"] <= 100


def test_flat_current_window_is_uninformative_not_zero_or_fifty() -> None:
    frame = _frame(200)
    frame.loc[frame.index[-35:], ["High", "Low", "Close"]] = 77.0
    result = compute_range_persistence(frame)
    assert result["status"] == "uninformative"
    assert result["range_persistence"] is None
    assert result["quality"] == 0.0
    assert result["legacy_control"] is not None


def test_invalid_rows_reduce_quality_and_public_values_stay_finite() -> None:
    frame = _frame(190)
    frame.loc[frame.index[10], "High"] = np.inf
    frame.loc[frame.index[20], "Low"] = np.nan
    frame.loc[frame.index[30], ["High", "Low"]] = [1.0, 2.0]
    result = compute_range_persistence(frame)
    assert result["status"] == "active"
    assert 0 < result["quality"] < 1
    for key, value in result.items():
        if isinstance(value, float):
            assert np.isfinite(value), key
