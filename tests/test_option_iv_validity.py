"""Rejected supplier IV must stay missing unless a credible quote can invert."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.services import yahoo
from app.services.quote_quality import vendor_iv


@pytest.fixture
def chain_fixture(monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime(2026, 9, 8, 15, tzinfo=timezone.utc)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    monkeypatch.setattr(yahoo, "datetime", FixedDateTime)
    monkeypatch.setattr(yahoo, "run_yahoo_option_io", lambda call: call())
    yahoo._cache.clear()

    def load(iv, bid=0, ask=0):
        row = {"contractSymbol": "TEST", "strike": 100.0,
               "impliedVolatility": iv, "lastPrice": 4.0, "bid": bid, "ask": ask,
               "volume": 6000, "openInterest": 100}
        ticker = SimpleNamespace(fast_info=SimpleNamespace(last_price=100.0),
            option_chain=lambda _date: SimpleNamespace(calls=pd.DataFrame([row]), puts=pd.DataFrame([row])))
        monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: ticker)
        return yahoo.get_option_chain("TEST", "2026-10-08")

    yield load
    yahoo._cache.clear()


@pytest.mark.parametrize("iv", [0.00001, 0.001953125, 0.005, 0, -0.2, float("nan"), float("inf")])
def test_rejected_iv_is_missing_in_contract_greeks_and_alerts(chain_fixture, iv):
    result = chain_fixture(iv)
    assert vendor_iv(iv) is None
    for contract in result["calls"] + result["puts"]:
        assert contract["implied_volatility"] is None
        assert contract["iv_source"] == "missing"
        assert all(contract[key] is None for key in ("delta", "gamma", "theta", "vega", "rho"))
    assert result["alerts"]
    assert all(alert["implied_volatility"] is None for alert in result["alerts"])
    assert all(alert["iv_source"] == "missing" for alert in result["alerts"])


def test_failed_quality_quote_inversion_never_revives_vendor_placeholder(chain_fixture, monkeypatch):
    calls = []
    def fail(*args, **kwargs):
        calls.append(args)
        return None
    monkeypatch.setattr(yahoo, "compute_iv", fail)
    result = chain_fixture(0.001953125, bid=3.9, ask=4.1)
    assert len(calls) == 2
    assert all(c["implied_volatility"] is None for c in result["calls"] + result["puts"])


def test_genuine_low_vendor_iv_is_preserved(chain_fixture):
    result = chain_fixture(0.02)
    assert result["calls"][0]["implied_volatility"] == 0.02
    assert result["calls"][0]["iv_source"] == "vendor"
    assert result["calls"][0]["delta"] is not None


def test_low_model_iv_from_valid_quotes_is_preserved_and_reprices(chain_fixture, monkeypatch):
    expiry = yahoo.option_expiry_metrics("2026-10-08")
    term = expiry["time_to_expiry_years"]
    mark = yahoo._bs_price(100, 100, term, 0.05, 0.02, True)
    result = chain_fixture(0.00001, bid=mark * 0.995, ask=mark * 1.005)
    call = result["calls"][0]
    assert call["iv_source"] == "model_inversion"
    assert call["implied_volatility"] == pytest.approx(0.02, abs=0.002)
    assert yahoo._bs_price(100, 100, term, 0.05, call["implied_volatility"], True) == pytest.approx(mark, abs=0.001)
