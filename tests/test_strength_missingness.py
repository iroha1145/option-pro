from __future__ import annotations

import numpy as np
import pandas as pd

from app.services.strength import scanner
from app.services.strength.scoring import score_intrinsic, weighted_available


def _history(size: int = 320) -> pd.DataFrame:
    index = pd.bdate_range(end="2026-07-10", periods=size)
    step = np.arange(size, dtype=float)
    close = 80.0 + step * 0.16 + np.sin(step / 8.0)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 0.9,
            "Low": close - 0.9,
            "Close": close,
            "Volume": 1_000_000.0 + step * 500.0,
        },
        index=index,
    )


def _intrinsic_row() -> dict:
    hist = _history()
    row = scanner._feature_row(
        "AAA",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert row is not None
    return scanner._intrinsic_row(
        row,
        hist,
        range_feature={"status": "disabled", "version": "fixture"},
        range_mode="disabled",
    )


def test_weighted_available_renormalizes_only_real_evidence() -> None:
    result = weighted_available(
        {"known": 80.0, "missing": None},
        {"known": 0.5, "missing": 0.5},
        min_active_weight=0.4,
    )
    assert result["score"] == 80.0
    assert result["confidence"] == 0.5
    assert result["effective_weights"] == {"known": 1.0}
    assert result["contributions"] == {"known": 80.0}
    assert result["missing_components"] == ["missing"]


def test_weighted_available_reports_insufficient_data() -> None:
    result = weighted_available(
        {"known": 80.0, "missing": None},
        {"known": 0.5, "missing": 0.5},
        min_active_weight=0.6,
    )
    assert result["score"] is None
    assert result["status"] == "insufficient_data"
    assert result["effective_weights"] == {}


def test_missing_252d_and_rsi_never_create_neutral_contributions() -> None:
    hist = _history()
    raw = scanner._feature_row(
        "AAA",
        hist,
        hist,
        {"sector_id": "software", "sector_name": "软件"},
    )
    assert raw is not None
    complete = scanner._intrinsic_row(
        raw,
        hist,
        range_feature={"status": "disabled", "version": "fixture"},
        range_mode="disabled",
    )
    missing_raw = {**raw, "return_252d": None, "rsi14": None}
    missing = scanner._intrinsic_row(
        missing_raw,
        hist,
        range_feature={"status": "disabled", "version": "fixture"},
        range_mode="disabled",
    )

    assert {"return_252d", "rsi14"}.issubset(set(missing["missing_components"]))
    long_detail = missing["factor_breakdown"]["family_details"]["long"]
    short_detail = missing["factor_breakdown"]["family_details"]["short"]
    assert "return_252d" not in long_detail["contributions"]
    assert "rsi14" not in short_detail["contributions"]
    assert missing["confidence"] < complete["confidence"]
    assert abs(sum(missing["contributions"].values()) - missing["intrinsic_score"]) <= 0.1


def test_unknown_market_and_options_do_not_change_intrinsic() -> None:
    intrinsic = _intrinsic_row()
    active_market = scanner._score_rows(
        [intrinsic],
        {"status": "active", "score": 80.0, "confidence": 1.0},
        "balanced",
        0,
    )[0]
    missing_market = scanner._score_rows(
        [{**intrinsic, "option_heat_score": None}],
        {"status": "insufficient_data", "score": None},
        "balanced",
        0,
    )[0]
    assert active_market["intrinsic_score"] == missing_market["intrinsic_score"]
    assert missing_market["market_fit_score"] is None
    assert missing_market["option_heat_score"] is None
    assert missing_market["option_score_weight"] == 0.0


def test_empty_intrinsic_evidence_is_not_forced_to_fifty() -> None:
    result = score_intrinsic(
        {},
        pd.DataFrame(),
        range_feature={"status": "disabled", "version": "fixture"},
        range_mode="disabled",
    )
    assert result["score"] is None
    assert result["status"] == "insufficient_data"
    assert result["confidence"] == 0.0
