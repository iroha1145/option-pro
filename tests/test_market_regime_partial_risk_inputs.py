from datetime import datetime, timezone

from app.services.strength.market_regime import compute_market_regime

from market_regime_support import core_market, credit_market, history


AS_OF = datetime(2026, 1, 30, 22, 0, tzinfo=timezone.utc)


def test_partial_risk_groups_degrade_without_erasing_shape() -> None:
    data = {**core_market(), **credit_market(), "^TNX": history(start=4.0, step=.001)}
    result = compute_market_regime(data, as_of=AS_OF)

    assert result["status"] == "degraded"
    assert result["score"] is not None
    assert result["market_shape"]["status"] == "degraded"
    assert result["market_shape"]["state"] is not None
    assert "volatility" in result["optional_missing"]
    assert not result["hard_missing"]
