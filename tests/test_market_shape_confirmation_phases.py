from __future__ import annotations

from copy import deepcopy

from app.services.strength.market_shape import (
    MARKET_SHAPE_VERSION,
    MarketShapeHysteresisConfig,
    replay_market_shape,
)

from market_shape_support import snapshot


def _accumulation(day: int):
    item = deepcopy(snapshot("distribution", day))
    regime = item["regime"]
    regime.update(
        {
            "score": 56.0,
            "index_trend_score": 50.0,
            "market_momentum_score": 56.0,
            "market_breadth_score": 62.0,
            "risk_on_spread_score": 58.0,
        }
    )
    return item


def test_market_shape_version_is_a_code_constant(monkeypatch) -> None:
    monkeypatch.setenv("MARKET_SHAPE_VERSION", "forged-from-env")
    assert MARKET_SHAPE_VERSION == "market-shape-v3"


def test_enter_and_exit_confirmation_have_independent_phases() -> None:
    fast_exit = MarketShapeHysteresisConfig(
        enter_confirm_days=3,
        exit_confirm_days=1,
        min_dwell_days=1,
        history_days=20,
    )
    slow_exit = MarketShapeHysteresisConfig(
        enter_confirm_days=1,
        exit_confirm_days=3,
        min_dwell_days=1,
        history_days=20,
    )
    history = [snapshot("bull", 0), snapshot("distribution", 1)]

    fast = replay_market_shape(history, config=fast_exit)
    slow = replay_market_shape(history, config=slow_exit)

    assert fast["pending_phase"] == "enter"
    assert fast["exit_confirmed"] is True
    assert fast["exit_pending_days"] == 1
    assert fast["enter_pending_days"] == 0
    assert slow["pending_phase"] == "exit"
    assert slow["exit_confirmed"] is False
    assert slow["exit_pending_days"] == 1

    for config in (fast_exit, slow_exit):
        changed = replay_market_shape(
            [
                snapshot("bull", 0),
                snapshot("distribution", 1),
                snapshot("distribution", 2),
                snapshot("distribution", 3),
                snapshot("distribution", 4),
            ],
            config=config,
        )
        assert changed["state"] == "RANGE_DISTRIBUTION"


def test_candidate_change_resets_only_enter_counter() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=2,
        exit_confirm_days=1,
        min_dwell_days=1,
        history_days=20,
    )
    result = replay_market_shape(
        [
            snapshot("bull", 0),
            snapshot("distribution", 1),
            snapshot("distribution", 2),
            _accumulation(3),
        ],
        config=config,
    )

    assert result["state"] == "BULL_TREND"
    assert result["pending_phase"] == "enter"
    assert result["pending_state"] == "RANGE_ACCUMULATION"
    assert result["exit_pending_days"] == 1
    assert result["enter_pending_days"] == 1


def test_raw_return_to_stable_clears_all_pending_state() -> None:
    config = MarketShapeHysteresisConfig(
        enter_confirm_days=3,
        exit_confirm_days=1,
        min_dwell_days=1,
        history_days=20,
    )
    result = replay_market_shape(
        [
            snapshot("bull", 0),
            snapshot("distribution", 1),
            snapshot("distribution", 2),
            snapshot("bull", 3),
        ],
        config=config,
    )

    assert result["state"] == "BULL_TREND"
    assert result["pending_state"] is None
    assert result["pending_phase"] is None
    assert result["pending_days"] == 0
    assert result["exit_pending_days"] == 0
    assert result["enter_pending_days"] == 0
    assert result["exit_confirmed"] is False
