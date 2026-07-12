from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.strength.scoring import score_intrinsic


def _inputs() -> tuple[dict, pd.DataFrame]:
    step = np.arange(320, dtype=float)
    close = 50.0 + step * 0.1
    hist = pd.DataFrame({"Close": close})
    row = {
        "return_5d": 0.02,
        "return_20d": 0.08,
        "return_63d": 0.18,
        "return_126d": 0.25,
        "return_252d": 0.35,
        "rs_spy_63d": 0.07,
        "dist_sma20": 0.04,
        "dist_sma50": 0.08,
        "dist_sma200": 0.20,
        "ma_alignment": 100.0,
        "rsi14": 62.0,
        "macd_direction": 0.3,
        "rel_volume": 1.4,
        "ath_proximity": 96.0,
        "price_action": {"status": "active", "score": 75.0},
    }
    return row, hist


def test_option_coverage_never_enters_intrinsic_score() -> None:
    row, hist = _inputs()
    missing = score_intrinsic(row, hist, range_mode="disabled")
    enriched = score_intrinsic(
        {
            **row,
            "option_heat_score": 99.0,
            "option_activity": {"status": "active", "score": 99.0},
            "option_direction": {"status": "active", "score": 90.0},
        },
        hist,
        range_mode="disabled",
    )
    assert enriched["score"] == missing["score"]
    assert enriched["contributions"] == missing["contributions"]
