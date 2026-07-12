from datetime import datetime, timezone

from app.services.strength.market_regime import compute_market_regime

from market_regime_support import core_market, history


def test_missing_rates_returns_degraded_shape_with_other_risk_evidence() -> None:
    data = {
        **core_market(),
        "^VIX": history(start=18.0, step=-.005),
        "HYG": history(start=75.0, step=.08),
        "IEF": history(start=92.0, step=.03),
    }
    result = compute_market_regime(
        data,
        as_of=datetime(2026, 1, 30, 22, 0, tzinfo=timezone.utc),
    )
    shape = result["market_shape"]
    assert shape["status"] == "degraded"
    assert shape["state"] is not None
    assert "rates" in shape["optional_missing"]
    assert "volatility" in shape["active_groups"]
    assert "credit" in shape["active_groups"]
