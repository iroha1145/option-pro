from datetime import datetime, timedelta, timezone

import pytest

from app.services import yahoo


@pytest.fixture
def quote_cache(monkeypatch):
    clock = {"now": datetime(2026, 9, 7, 20, tzinfo=timezone.utc)}

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock["now"].astimezone(tz) if tz else clock["now"].replace(tzinfo=None)

    monkeypatch.setattr(yahoo, "datetime", FrozenDateTime)
    yahoo._cache.clear()
    expiration = "2026-10-16"

    def unavailable(_symbol):
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(yahoo, "_get_ticker", unavailable)

    def seed(age_minutes):
        observed = clock["now"] - timedelta(minutes=age_minutes)
        yahoo._cache["expirations:TEST"] = (
            clock["now"] + timedelta(hours=1), clock["now"], [expiration],
        )
        yahoo._cache["chain:TEST:" + expiration] = (
            observed + timedelta(minutes=5), observed,
            {"ticker": "TEST", "expiration": expiration, "underlying_price": 100.0,
             "calls": [{"strike": 100.0, "implied_volatility": 0.32, "iv_source": "vendor"}],
             "puts": [], "as_of": observed.isoformat()},
        )
        return observed

    yield clock, seed
    yahoo._cache.clear()


def test_iv_retains_stale_chain_timestamp(quote_cache):
    _clock, seed = quote_cache
    observed = seed(20)
    result = yahoo.get_stock_iv_snapshot("TEST")
    assert result["atm_iv"] == 0.32
    assert result["_stale"] is True
    assert result["source_status"] == "stale"
    assert result["as_of"] == observed.isoformat()
    assert result["stale_age_seconds"] == 1200


def test_iv_cache_does_not_extend_fresh_chain_deadline(quote_cache):
    clock, seed = quote_cache
    observed = seed(4)
    first = yahoo.get_stock_iv_snapshot("TEST")
    assert first["_stale"] is False
    assert first["as_of"] == observed.isoformat()
    clock["now"] += timedelta(seconds=90)
    later = yahoo.get_stock_iv_snapshot("TEST")
    assert later["_stale"] is True
    assert later["as_of"] == observed.isoformat()


def test_derived_iv_cannot_outlive_original_quote_age_limit(quote_cache):
    clock, seed = quote_cache
    seed(20)
    assert yahoo.get_stock_iv_snapshot("TEST")["atm_iv"] == 0.32
    clock["now"] += timedelta(minutes=11)
    with pytest.raises(TimeoutError, match="provider unavailable"):
        yahoo.get_stock_iv_snapshot("TEST")
