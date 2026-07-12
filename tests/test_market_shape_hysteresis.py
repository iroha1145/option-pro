from app.services.strength.market_shape import (
    MarketShapeHysteresisConfig,
    replay_market_shape,
)

from market_shape_support import snapshot


def test_threshold_oscillation_does_not_flip_stable_state() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=2,
        exit_confirm_days=2,
        min_dwell_days=3,
        history_days=20,
    )
    history = [
        snapshot("bull", 0),
        snapshot("bull", 1),
        snapshot("bull", 2),
        snapshot("distribution", 3),
        snapshot("bull", 4),
        snapshot("distribution", 5),
        snapshot("bull", 6),
    ]
    for size in range(1, len(history) + 1):
        result = replay_market_shape(history[:size], config=config)
        assert result["state"] == "BULL_TREND"
    assert result["pending_state"] is None
    assert result["pending_days"] == 0
