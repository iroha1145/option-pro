from __future__ import annotations

from app.services.strength.scoring import score_market_fit


def test_missing_shape_returns_null_market_fit() -> None:
    result = score_market_fit({"status": "active", "score": 80.0})
    assert result["score"] is None
    assert result["status"] == "insufficient_data"


def test_degraded_shape_confidence_shrinks_market_fit_toward_fifty() -> None:
    result = score_market_fit(
        {
            "status": "degraded",
            "score": 80.0,
            "market_shape": {
                "status": "degraded",
                "state": "BULL_TREND",
                "confidence": 0.5,
            },
        }
    )
    assert result["raw_score"] == 80.0
    assert result["score"] == 65.0
    assert result["confidence"] == 0.5
