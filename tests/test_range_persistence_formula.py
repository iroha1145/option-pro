from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.services.technical.range_persistence import compute_legacy_control_exact


def _frame(size: int = 260) -> pd.DataFrame:
    close = np.linspace(40.0, 100.0, size) + np.sin(np.arange(size) / 4.0) * 2.0
    index = pd.date_range("2025-01-01", periods=size, freq="B")
    return pd.DataFrame(
        {"High": close + 2.0, "Low": close - 2.0, "Close": close},
        index=index,
    )


def test_exact_stepwise_matches_generalized_identity() -> None:
    frame = _frame()
    result = compute_legacy_control_exact(frame)
    assert result["status"] == "active"
    assert abs(result["legacy_control"] - result["legacy_control_generalized"]) < 1e-8


def test_steady_state_simplification_and_parameter_redundancy() -> None:
    frame = _frame(800)
    first = compute_legacy_control_exact(frame, length=1, m=35, n1=3)
    shifted = compute_legacy_control_exact(frame, length=1, m=45, n1=13)
    assert first["legacy_gate"] == 1.0
    assert shifted["legacy_gate"] == 1.0
    assert abs(first["legacy_control"] - shifted["legacy_control"]) < 1e-8


def test_warmup_gate_is_not_the_steady_state_identity() -> None:
    frame = pd.DataFrame(
        {
            "High": [1.0] * 35 + [101.0] * 80,
            "Low": [1.0] * 115,
            "Close": [1.0] * 35 + [101.0] * 80,
        },
        index=pd.date_range("2025-01-01", periods=115, freq="B"),
    )
    first = compute_legacy_control_exact(frame, length=35, m=35, n1=3)
    shifted = compute_legacy_control_exact(frame, length=35, m=45, n1=13)
    assert 0.0 < first["legacy_gate"] < 1.0
    assert shifted["legacy_gate"] == first["legacy_gate"]
    assert first["legacy_control"] != shifted["legacy_control"]


def test_legacy_control_can_exceed_one_hundred_and_remains_finite() -> None:
    size = 500
    low = np.full(size, 1.0)
    high = np.full(size, 101.0)
    close = np.full(size, 101.0)
    frame = pd.DataFrame(
        {"High": high, "Low": low, "Close": close},
        index=pd.date_range("2024-01-01", periods=size, freq="B"),
    )
    result = compute_legacy_control_exact(
        frame,
        cutoff=datetime(2026, 12, 31, tzinfo=timezone.utc),
    )
    assert 100 < result["legacy_control"] <= 330
    assert np.isfinite(result["legacy_control"])


def test_flat_range_preserves_pine_zero_input_semantics() -> None:
    frame = pd.DataFrame(
        {"High": [10.0] * 200, "Low": [10.0] * 200, "Close": [10.0] * 200},
        index=pd.date_range("2025-01-01", periods=200, freq="B"),
    )
    result = compute_legacy_control_exact(frame)
    assert result["legacy_control"] == 0.0
    assert result["range_position"] == 0.0
