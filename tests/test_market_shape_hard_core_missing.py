from datetime import datetime, timezone

from app.services.strength.market_regime import compute_market_regime

from market_regime_support import core_market


def test_missing_spy_long_term_trend_keeps_shape_unavailable() -> None:
    data = core_market()
    data["SPY"] = data["SPY"].tail(80)
    result = compute_market_regime(
        data,
        as_of=datetime(2026, 1, 30, 22, 0, tzinfo=timezone.utc),
    )
    shape = result["market_shape"]
    assert result["status"] == "insufficient_data"
    assert shape["status"] == "unavailable"
    assert shape["state"] is None
    assert "spy_long_trend" in shape["hard_missing"]
