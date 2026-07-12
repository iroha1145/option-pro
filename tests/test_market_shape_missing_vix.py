from datetime import datetime, timezone

from app.services.strength.market_regime import compute_market_regime

from market_regime_support import core_market, credit_market, history


def test_missing_vix_returns_degraded_shape_with_credit_and_rates() -> None:
    data = {**core_market(), **credit_market(), "^TNX": history(start=4.0, step=.001)}
    result = compute_market_regime(
        data,
        as_of=datetime(2026, 1, 30, 22, 0, tzinfo=timezone.utc),
    )
    shape = result["market_shape"]
    assert shape["status"] == "degraded"
    assert shape["state"] is not None
    assert "volatility" in shape["optional_missing"]
    assert "credit" in shape["active_groups"]
    assert "rates" in shape["active_groups"]
