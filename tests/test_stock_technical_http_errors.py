"""Offline HTTP checks for the technical route's provider error boundary."""

import asyncio
from types import SimpleNamespace

import httpx
import pandas as pd
import pytest
from fastapi import FastAPI

from app.api import stocks
from app.services import massive
from app.services.technical import structure


@pytest.fixture(autouse=True)
def isolated_endpoint(monkeypatch):
    for name in (
        "_endpoint_cache", "_endpoint_locks", "_endpoint_lock_users",
        "_endpoint_refresh_tasks", "_endpoint_refresh_retry_after",
    ):
        monkeypatch.setattr(stocks, name, {})
    monkeypatch.setattr(stocks, "current_request_is_owner", lambda: True)
    monkeypatch.setattr(massive, "configured", lambda: False)

    async def no_hydrate(*args, **kwargs):
        return None

    monkeypatch.setattr(stocks, "_hydrate_stock_pull_resource", no_hydrate)


def request(path="/api/stocks/NVDA/technical"):
    app = FastAPI()
    app.include_router(stocks.router)

    async def get():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://offline.test",
        ) as client:
            return await client.get(path)

    return asyncio.run(get())


@pytest.mark.parametrize("error_type", [RuntimeError, httpx.ConnectError])
def test_provider_failure_returns_safe_503(monkeypatch, error_type):
    def fail(**kwargs):
        raise error_type("secret-key https://internal.example/path?apiKey=secret-key traceback")

    monkeypatch.setattr(stocks.yf, "Ticker", lambda symbol: SimpleNamespace(history=fail))
    response = request()
    assert response.status_code == 503
    assert response.json() == {"detail": {
        "code": "stock_technical_provider_unavailable",
        "message": "Stock technical data is currently unavailable",
    }}
    assert "secret-key" not in response.text
    assert "internal.example" not in response.text
    assert "traceback" not in response.text
    assert not stocks._endpoint_cache


def test_technical_computation_failure_remains_500(monkeypatch):
    async def chart(*args):
        return {"bars": [{"t": 1, "c": 10}], "as_of": "fixture"}

    async def spy(owner):
        return None

    def fail(*args, **kwargs):
        raise RuntimeError("internal calculation failed")

    monkeypatch.setattr(stocks, "_load_stock_chart", chart)
    monkeypatch.setattr(stocks, "_spy_closes_by_date", spy)
    monkeypatch.setattr(structure, "compute_technical_structure", fail)
    response = request()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_provider_constructor_failure_returns_503(monkeypatch):
    def fail(symbol):
        raise RuntimeError("secret provider constructor failure")

    monkeypatch.setattr(stocks.yf, "Ticker", fail)
    response = request()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "stock_technical_provider_unavailable"
    assert "secret" not in response.text


def test_chart_indicator_bug_remains_500(monkeypatch):
    frame = pd.DataFrame(
        {"Open": [10], "High": [11], "Low": [9], "Close": [10], "Volume": [1]},
        index=pd.DatetimeIndex(["2024-01-02"], tz="America/New_York"),
    )
    monkeypatch.setattr(stocks.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kw: frame))

    def fail(*args):
        raise RuntimeError("internal indicator bug")

    monkeypatch.setattr(stocks, "_compute_ema", fail)
    response = request()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_malformed_provider_frame_remains_500(monkeypatch):
    monkeypatch.setattr(stocks.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kw: None))
    response = request()
    assert response.status_code == 500
    assert response.text == "Internal Server Error"


def test_short_daily_series_preserves_existing_503(monkeypatch):
    async def chart(*args):
        return {"bars": [{"t": 1, "o": 10, "h": 11, "l": 9, "c": 10, "v": 1}]}

    async def spy(owner):
        return None

    monkeypatch.setattr(stocks, "_load_stock_chart", chart)
    monkeypatch.setattr(stocks, "_spy_closes_by_date", spy)
    response = request()
    assert response.status_code == 503
    assert response.json() == {"detail": "Not enough daily bars for technical structure"}


def test_empty_chart_impl_preserves_legal_empty_payload(monkeypatch):
    monkeypatch.setattr(stocks.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kw: pd.DataFrame()))
    payload = asyncio.run(stocks._stock_chart_impl("NVDA", "1d", "raw"))
    assert payload["source_status"] == "empty"
    assert payload["bars"] == payload["ema20"] == payload["sma50"] == []


def test_empty_daily_provider_response_returns_technical_503(monkeypatch):
    monkeypatch.setattr(stocks.yf, "Ticker", lambda symbol: SimpleNamespace(history=lambda **kw: pd.DataFrame()))
    response = request()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "stock_technical_provider_unavailable"
    assert not stocks._endpoint_cache


def test_successful_technical_response_matches_structure(monkeypatch):
    bars = [
        {"t": 1704200400 + i * 86400, "o": 100 + i, "h": 102 + i,
         "l": 99 + i, "c": 101 + i, "v": 1000000}
        for i in range(140)
    ]
    expected = structure.compute_technical_structure(bars, ticker="NVDA", spy_closes=None)
    assert expected is not None

    async def chart(*args):
        return {"bars": bars, "as_of": "2024-06-01T00:00:00+00:00"}

    async def spy(owner):
        return None

    monkeypatch.setattr(stocks, "_load_stock_chart", chart)
    monkeypatch.setattr(stocks, "_spy_closes_by_date", spy)
    response = request()
    assert response.status_code == 200
    expected["chart_analysis"].update(ticker="NVDA", range="1d", adjustment="raw")
    assert response.json() == stocks._sanitize({
        "ticker": "NVDA", "basis": "raw_daily", "as_of": "2024-06-01T00:00:00+00:00",
        **expected, "_stale": False, "source_status": "active",
    })
