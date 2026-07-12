from __future__ import annotations

from app.services.strength.scoring import weighted_available


def test_effective_weight_below_threshold_returns_null_score() -> None:
    result = weighted_available(
        {"small_fragment": 80.0, "missing_core": None},
        {"small_fragment": 0.2, "missing_core": 0.8},
        min_active_weight=0.25,
    )
    assert result["score"] is None
    assert result["status"] == "insufficient_data"
    assert result["confidence"] == 0.2
