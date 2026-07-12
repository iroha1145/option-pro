from __future__ import annotations

from app.services.strength.scoring import weighted_available


def test_quality_adjusted_active_weights_are_renormalized() -> None:
    result = weighted_available(
        {"high_quality": 80.0, "low_quality": 20.0, "missing": None},
        {"high_quality": 0.4, "low_quality": 0.4, "missing": 0.2},
        {"high_quality": 1.0, "low_quality": 0.5},
        min_active_weight=0.5,
    )
    assert result["effective_weights"] == {
        "high_quality": 0.666667,
        "low_quality": 0.333333,
    }
    assert abs(sum(result["contributions"].values()) - result["score"]) < 0.001
    assert result["confidence"] == 0.6
