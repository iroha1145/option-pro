from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from app.api import signals


def test_signal_api_rejects_invalid_ticker_before_provider_call(monkeypatch):
    called = False

    def provider(_symbol: str):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(signals, "compute_stock_signals", provider)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(signals.stock_signals("../../secret"))
    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Invalid ticker symbol"
    assert called is False


def test_signal_api_does_not_expose_internal_provider_errors(monkeypatch):
    monkeypatch.setattr(
        signals,
        "compute_market_signals",
        lambda: (_ for _ in ()).throw(RuntimeError("secret upstream URL and path")),
    )

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(signals.market_signals())
    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "Market signals are currently unavailable"
    assert "secret" not in exc_info.value.detail
