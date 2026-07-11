from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import market


def test_indices_reject_total_non_finite_provider_payload(monkeypatch):
    class FakeTicker:
        fast_info = SimpleNamespace(last_price=float("nan"), previous_close=float("nan"))

    monkeypatch.setattr(market.yf, "Ticker", lambda _symbol: FakeTicker())

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(market._build_indices())
    assert exc_info.value.status_code == 503


def test_indices_degrade_invalid_symbols_without_emitting_nan(monkeypatch):
    valid_symbol = market.INDEX_SYMBOLS[0]

    class FakeTicker:
        def __init__(self, symbol: str):
            price = 100.0 if symbol == valid_symbol else float("nan")
            self.fast_info = SimpleNamespace(last_price=price, previous_close=99.0)

    monkeypatch.setattr(market.yf, "Ticker", FakeTicker)
    payload = asyncio.run(market._build_indices())

    assert payload["succeeded"] == 1
    assert payload["source_status"] == "degraded"
    assert payload["indices"][0]["price"] == 100.0
    assert all(item["price"] is None for item in payload["indices"][1:])
    json.dumps(payload, allow_nan=False)


def test_indices_keep_valid_price_when_previous_close_access_fails(monkeypatch):
    class BrokenFastInfo:
        last_price = 100.0

        @property
        def previous_close(self):
            raise RuntimeError("previous close unavailable")

    class FakeTicker:
        fast_info = BrokenFastInfo()

    monkeypatch.setattr(market.yf, "Ticker", lambda _symbol: FakeTicker())
    payload = asyncio.run(market._build_indices())

    assert payload["succeeded"] == len(market.INDEX_SYMBOLS)
    assert all(item["price"] == 100.0 for item in payload["indices"])
    assert all(item["change_percent"] == 0.0 for item in payload["indices"])
