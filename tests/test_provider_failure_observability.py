from __future__ import annotations

import pandas as pd

from app.api import stocks
from app.services import signals


def test_single_symbol_history_failure_is_logged_and_returns_empty(monkeypatch, caplog):
    def failed_ticker(_symbol):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(signals.yf, "Ticker", failed_ticker)
    result = signals._yahoo_history("AAPL")
    assert result.empty
    assert any("Yahoo history failed for AAPL (RuntimeError)" in item.getMessage()
               for item in caplog.records)


def test_bulk_history_failure_uses_per_symbol_fallback(monkeypatch, caplog):
    monkeypatch.setattr(signals.massive, "configured", lambda: False)

    def failed_download(**_kwargs):
        raise RuntimeError("bulk unavailable")

    monkeypatch.setattr(signals.yf, "download", failed_download)
    monkeypatch.setattr(signals, "_yahoo_history", lambda _symbol, _period: pd.DataFrame({"Close": [10.0]}))
    result = signals._bulk_history(["AAPL", "MSFT"])
    assert set(result) == {"AAPL", "MSFT"}
    assert all(not frame.empty for frame in result.values())
    assert any("Yahoo bulk history failed (RuntimeError)" in item.getMessage()
               for item in caplog.records)


def test_previous_close_provider_failure_is_logged_without_inventing_price(monkeypatch, caplog):
    def failed_ticker(_symbol, *, session):
        raise RuntimeError("metadata unavailable")

    monkeypatch.setattr(stocks.yf, "Ticker", failed_ticker)
    result = stocks._fetch_watchlist_provider_previous_close("^N225", session=object())
    assert result is None
    assert any("Watchlist provider previous close failed for ^N225 (RuntimeError)"
               in item.getMessage() for item in caplog.records)
