from __future__ import annotations

from app.services.strength import scanner


def _intrinsic() -> dict:
    return {
        "ticker": "AAA",
        "intrinsic_score": 72.0,
        "score_status": "active",
        "confidence": 0.9,
        "ma_alignment": 80.0,
        "avg_dollar_volume_20d": 50_000_000,
        "atr_pct": 3.0,
        "factor_breakdown": {},
        "coverage": {"ratio": 0.9},
    }


def test_missing_or_active_option_snapshot_does_not_change_strength_scores() -> None:
    market = {
        "status": "active",
        "score": 70.0,
        "market_shape": {"status": "active", "state": "BULL_TREND", "confidence": 1.0},
    }
    missing = scanner._score_rows([_intrinsic()], market, "balanced", 0)[0]
    active = scanner._score_rows(
        [{**_intrinsic(), "option_heat_score": 95.0}],
        market,
        "balanced",
        0,
    )[0]
    assert active["intrinsic_score"] == missing["intrinsic_score"]
    assert active["ranking_score"] == missing["ranking_score"]
    assert active["option_score_weight"] == 0.0
