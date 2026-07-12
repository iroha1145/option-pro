from app.services.strength.market_shape import (
    MarketShapeHysteresisConfig,
    replay_market_shape,
)

from market_shape_support import snapshot


def test_extreme_bear_condition_bypasses_dwell_and_confirmation() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=5,
        exit_confirm_days=5,
        min_dwell_days=10,
        history_days=20,
    )
    result = replay_market_shape(
        [snapshot("bull", 0), snapshot("bear", 1)],
        config=config,
    )
    assert result["state"] == "BEAR_TREND"
    assert result["previous_state"] == "BULL_TREND"
    assert result["emergency_override"] is True
    assert result["emergency_version"] == "market-shape-emergency-v1"


def test_one_healthy_risk_group_prevents_emergency_bear_override() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=5,
        exit_confirm_days=5,
        min_dwell_days=10,
        history_days=20,
    )
    mixed_risk = snapshot("bear", 1)
    mixed_risk["regime"]["risk_on_spread_score"] = 90.0

    result = replay_market_shape(
        [snapshot("bull", 0), mixed_risk],
        config=config,
    )

    assert result["state"] == "BULL_TREND"
    assert result["raw_state"] == "BEAR_TREND"
    assert result["pending_state"] == "BEAR_TREND"
    assert result["pending_days"] == 1
    assert result["emergency_override"] is False
