"""Provider fallbacks keep latest quotes, unknown baselines and market sessions honest."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

from app.api import market, sectors, stocks
from app.services import massive


@pytest.mark.parametrize("previous", [None, 0, -1, float("nan"), float("inf"), "invalid"])
def test_index_keeps_price_without_inventing_change(monkeypatch, previous):
    monkeypatch.setattr(market.yf, "Ticker", lambda _ticker: SimpleNamespace(
        fast_info=SimpleNamespace(last_price=123.0, previous_close=previous),
    ))
    payload = asyncio.run(market._build_indices())
    assert payload["succeeded"] == len(market.INDEX_SYMBOLS)
    assert all(row["price"] == 123.0 and row["change_percent"] is None for row in payload["indices"])


@pytest.mark.parametrize("minute", [{}, {"c": 100.0}, {"c": 100.0, "t": 0}, {"c": 100.0, "t": float("nan")}])
def test_daily_close_cannot_override_latest_alternative_quote(monkeypatch, minute):
    at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _ticker: SimpleNamespace(
        fast_info=SimpleNamespace(last_price=105.0, previous_close=100.0),
        info={"regularMarketTime": int(at.timestamp())},
    ))
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": minute, "day": {"c": 100.0}, "day_close": 100.0, "prev_close": 100.0},
    })
    payload = asyncio.run(stocks._stock_overview_impl("AAPL"))
    assert payload["price"] == 105.0
    assert payload["change_percent"] == 5.0
    assert payload["price_provider"] == "Yahoo/yfinance"
    assert payload["as_of"] == at.isoformat()


def test_daily_only_snapshot_without_alternative_is_unavailable(monkeypatch):
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _ticker: SimpleNamespace(fast_info=None, info={}))
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": {}, "day": {"c": 100.0}, "prev_close": 100.0},
    })
    with pytest.raises(RuntimeError, match="Price provider unavailable"):
        asyncio.run(stocks._stock_overview_impl("AAPL"))


def test_minute_quote_uses_its_own_timestamp_not_snapshot_refresh_time(monkeypatch):
    at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _ticker: SimpleNamespace(fast_info=None, info={}))
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": {"c": 105., "t": int(at.timestamp() * 1000)},
                 "day": {"c": 100.}, "as_of": "2026-09-11T20:00:00+00:00", "prev_close": 100.},
    })
    payload = asyncio.run(stocks._stock_overview_impl("AAPL"))
    assert payload["price"] == 105.
    assert payload["as_of"] == at.isoformat()


@pytest.mark.parametrize("previous", [None, 0, -1, float("nan"), float("inf"), "invalid", 100.0])
def test_overview_never_pairs_massive_last_with_yahoo_previous_close(monkeypatch, previous):
    at = datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _ticker: SimpleNamespace(
        fast_info=SimpleNamespace(last_price=104.0, previous_close=80.0),
        info={"regularMarketTime": int(at.timestamp())},
    ))
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": {"c": 105.0, "t": int(at.timestamp() * 1000)}, "prev_close": previous},
    })
    payload = asyncio.run(stocks._stock_overview_impl("AAPL"))
    assert payload["price"] == 105.0
    assert payload["price_provider"] == "Massive"
    assert payload["as_of"] == at.isoformat()
    if previous == 100.0:
        assert payload["prev_close"] == 100.0
        assert payload["change"] == 5.0
        assert payload["change_percent"] == 5.0
    else:
        assert payload["prev_close"] is None
        assert payload["change"] is None
        assert payload["change_percent"] is None


@pytest.mark.parametrize("yahoo_previous", [None, 100.0])
def test_yahoo_fallback_uses_only_yahoo_previous_close(monkeypatch, yahoo_previous):
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _ticker: SimpleNamespace(
        fast_info=SimpleNamespace(last_price=105.0, previous_close=yahoo_previous), info={},
    ))
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": {}, "day": {"c": 104.0}, "prev_close": 80.0},
    })
    payload = asyncio.run(stocks._stock_overview_impl("AAPL"))
    assert payload["price"] == 105.0
    assert payload["price_provider"] == "Yahoo/yfinance"
    assert payload["prev_close"] == yahoo_previous
    assert payload["change"] == (5.0 if yahoo_previous else None)
    assert payload["change_percent"] == (5.0 if yahoo_previous else None)


def test_sector_daily_only_price_falls_back_without_losing_option_iv(monkeypatch):
    monkeypatch.setitem(sectors.SECTORS, "audit", {"tickers": ["AAPL"]})
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(massive, "snapshot_batch", lambda _symbols: {
        "AAPL": {"minute": {}, "day_close": 100.},
    })
    monkeypatch.setattr(sectors.yahoo, "get_stock_iv_snapshot", lambda _ticker: {"atm_iv": .25})
    monkeypatch.setattr(sectors.yahoo, "get_last_price", lambda _ticker: 105.)
    rows = asyncio.run(sectors._sector_iv_rows("audit"))
    assert rows[0]["price"] == 105.
    assert rows[0]["price_provider"] == "Yahoo/yfinance"
    assert rows[0]["iv"] == .25


@pytest.mark.parametrize(("when", "expected"), [
    ("2026-11-27 12:55", "regular"),
    ("2026-11-27 13:00", "regular"),
    ("2026-11-27 13:30", "post_market"),
    ("2026-11-25 13:30", "regular"),
    ("2026-11-25 08:30", "pre_market"),
])
def test_watchlist_session_follows_half_day_calendar(monkeypatch, when, expected):
    def download(*, interval, **_kwargs):
        if interval == "5m":
            return pd.DataFrame({"Close": [105.]}, index=pd.DatetimeIndex([when], tz="America/New_York"))
        return pd.DataFrame({"Close": [98., 100.]}, index=pd.to_datetime(["2026-11-23", "2026-11-24"]))

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "download", download)
    payload = asyncio.run(stocks._build_watchlist(["AAPL"]))
    row = payload["groups"][0]["stocks"][0]
    assert row["price"] == 105.
    assert row["quote_session"] == expected
