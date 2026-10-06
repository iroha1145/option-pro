from __future__ import annotations


import numpy as np
import pandas as pd

from app.services.strength import scanner


def _history(*, slope: float, offset: float = 0.0, size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-10", periods=size)
    step = np.arange(size, dtype=float)
    close = 40.0 + offset + step * slope + np.sin(step / 9.0)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.8,
            "Low": close - 0.8,
            "Close": close,
            "Volume": 1_500_000.0 + step * 1_000.0,
        },
        index=index,
    )


def test_shadow_range_weight_configuration_cannot_change_production_intrinsic() -> None:
    hist = _history(slope=0.15)
    raw = scanner._feature_row(
        "AAA",
        hist,
        _history(slope=0.08),
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert raw is not None
    feature = {
        "status": "active",
        "version": "range-fixture",
        "range_persistence_normalized_score": 90.0,
        "range_persistence_slope_5d": 4.0,
        "range_persistence_ratio_10d": 95.0,
    }
    low_weight = scanner._intrinsic_row(
        raw,
        hist,
        range_feature=feature,
        range_mode="shadow",
        range_trend_weight=0.01,
    )
    high_weight = scanner._intrinsic_row(
        raw,
        hist,
        range_feature=feature,
        range_mode="shadow",
        range_trend_weight=0.15,
    )
    assert low_weight["intrinsic_score"] == high_weight["intrinsic_score"]
    assert low_weight["contributions"] == high_weight["contributions"]
    assert low_weight["range_persistence_shadow"]["production_score"] == high_weight["range_persistence_shadow"]["production_score"]
    for row in (low_weight, high_weight):
        trend = row["factor_breakdown"]["trend_family"]
        assert trend["applied_effective_weights"] == trend["production_effective_weights"]


# ---------------- tier distribution covers the pool, not the slice ----------------
