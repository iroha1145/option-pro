from app.services.strength.market_shape import (
    MarketShapeHysteresisConfig,
    replay_market_shape,
)

from market_shape_support import snapshot


def test_minimum_dwell_blocks_ordinary_transition() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=2,
        exit_confirm_days=2,
        min_dwell_days=5,
        history_days=20,
    )
    history = [snapshot("bull", 0)] + [
        snapshot("distribution", day) for day in range(1, 5)
    ]
    blocked = replay_market_shape(history, config=config)
    assert blocked["state"] == "BULL_TREND"
    assert blocked["days_in_state"] == 5
    assert blocked["pending_state"] == "RANGE_DISTRIBUTION"

    changed = replay_market_shape(
        [*history, snapshot("distribution", 5)],
        config=config,
    )
    assert changed["state"] == "RANGE_DISTRIBUTION"
    assert changed["days_in_state"] == 1
