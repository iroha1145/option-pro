from app.services.strength.market_shape import (
    MarketShapeHysteresisConfig,
    build_market_shape,
    replay_market_shape,
)

from market_shape_support import snapshot


def test_consecutive_confirmation_changes_state_and_timestamps() -> None:
    config = MarketShapeHysteresisConfig(min_dwell_days=3, history_days=20)
    first_pending = replay_market_shape(
        [
            snapshot("bull", 0),
            snapshot("bull", 1),
            snapshot("bull", 2),
            snapshot("distribution", 3),
        ],
        config=config,
    )
    assert first_pending["state"] == "BULL_TREND"
    assert first_pending["pending_state"] == "RANGE_DISTRIBUTION"
    assert first_pending["pending_days"] == 1
    stable_shape = build_market_shape(
        snapshot("bull", 2)["regime"],
        as_of=snapshot("bull", 2)["as_of"],
        history=[snapshot("bull", 0), snapshot("bull", 1)],
        config=config,
    )
    pending_shape = build_market_shape(
        snapshot("distribution", 3)["regime"],
        as_of=snapshot("distribution", 3)["as_of"],
        history=[snapshot("bull", 0), snapshot("bull", 1), snapshot("bull", 2)],
        config=config,
    )
    assert pending_shape["transition_risk"] > stable_shape["transition_risk"]

    changed = replay_market_shape(
        [
            snapshot("bull", 0),
            snapshot("bull", 1),
            snapshot("bull", 2),
            snapshot("distribution", 3),
            snapshot("distribution", 4),
        ],
        config=config,
    )
    assert changed["state"] == "RANGE_DISTRIBUTION"
    assert changed["previous_state"] == "BULL_TREND"
    assert changed["entered_at"] == snapshot("distribution", 4)["as_of"].isoformat()
    assert changed["pending_state"] is None
    assert changed["pending_days"] == 0
