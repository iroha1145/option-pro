from app.services.strength.market_shape import build_market_shape, market_fit_for_setup

from market_shape_support import START, regime


def test_optional_input_loss_keeps_state_but_reduces_confidence() -> None:
    complete = regime("bull")
    degraded = regime("bull", status="degraded")
    degraded["optional_missing"] = ["volatility"]
    degraded["input_coverage"] = {"ratio": .82}
    degraded["degraded_reasons"] = ["missing_optional:volatility"]

    active_shape = build_market_shape(complete, as_of=START)
    degraded_shape = build_market_shape(degraded, as_of=START)

    assert active_shape["status"] == "active"
    assert degraded_shape["status"] == "degraded"
    assert degraded_shape["state"] == active_shape["state"]
    assert degraded_shape["confidence"] < active_shape["confidence"]
    assert degraded_shape["optional_missing"] == ["volatility"]
    active_fit = market_fit_for_setup(active_shape, "DAILY_BASE_BREAKOUT")
    degraded_fit = market_fit_for_setup(degraded_shape, "DAILY_BASE_BREAKOUT")
    assert active_fit is not None and degraded_fit is not None
    assert abs(degraded_fit - 50.0) < abs(active_fit - 50.0)
