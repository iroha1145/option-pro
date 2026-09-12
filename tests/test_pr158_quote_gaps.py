"""Focused counterexamples for the two remaining PR 158 quote-contract gaps."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pandas as pd

from app.api import stocks
from app.public_home_snapshot import create_public_home_entry
from app.services import massive
from app.stock_pull_snapshot import validate_stock_pull_payload


def _install_unknown_volume_history(monkeypatch):
    observed = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)
    history = pd.DataFrame(
        {
            "Open": [100.0],
            "High": [102.0],
            "Low": [99.0],
            "Close": [101.0],
            "Volume": [float("nan")],
        },
        index=pd.DatetimeIndex([observed]),
    )

    class FakeTicker:
        def __init__(self, symbol: str):
            assert symbol == "NVDA"

        def history(self, **_kwargs):
            return history

    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "Ticker", FakeTicker)


def _chart_with_unknown_volume(monkeypatch):
    _install_unknown_volume_history(monkeypatch)
    return asyncio.run(stocks._stock_chart_impl("NVDA", "1d", "raw"))


def test_chart_provider_does_not_turn_unknown_volume_into_observed_zero(monkeypatch):
    async def no_hydrate(*_args, **_kwargs):
        return None

    async def no_public_entry(*_args, **_kwargs):
        return None

    async def load_now(_key, _ttl, _max_age, loader, **_kwargs):
        return await loader()

    _install_unknown_volume_history(monkeypatch)
    monkeypatch.setattr(stocks, "current_request_is_owner", lambda: True)
    monkeypatch.setattr(stocks, "_hydrate_stock_pull_resource", no_hydrate)
    monkeypatch.setattr(stocks, "_reuse_fresh_public_home_entry", no_public_entry)
    monkeypatch.setattr(stocks, "_stale_while_revalidate_endpoint", load_now)

    payload = asyncio.run(stocks.stock_chart("NVDA", "1d", "raw"))

    assert payload["bars"][0]["v"] is None


def test_manual_pull_snapshot_accepts_chart_with_unknown_volume(monkeypatch):
    payload = _chart_with_unknown_volume(monkeypatch)
    payload["bars"][0]["v"] = None

    assert validate_stock_pull_payload("NVDA", "daily_chart", payload) == payload


def test_public_chart_worker_accepts_chart_with_unknown_volume(monkeypatch):
    payload = _chart_with_unknown_volume(monkeypatch)
    payload["bars"][0]["v"] = None

    entry = create_public_home_entry(
        "focus_chart",
        payload,
        saved_at=datetime.fromisoformat(payload["as_of"]).timestamp() + 1,
        parameters={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert entry["payload"] == payload


def test_massive_chart_keeps_unknown_volume_distinct_from_observed_zero(monkeypatch):
    first = int(datetime(2026, 9, 10, 20, 0, tzinfo=timezone.utc).timestamp() * 1000)
    monkeypatch.setattr(massive, "configured", lambda: True)
    monkeypatch.setattr(
        massive,
        "ticker_range",
        lambda *_args, **_kwargs: [
            {"t": first, "o": 100.0, "h": 102.0, "l": 99.0, "c": 101.0, "v": None},
            {"t": first + 86_400_000, "o": 101.0, "h": 103.0, "l": 100.0, "c": 102.0, "v": 0},
        ],
    )

    payload = asyncio.run(stocks._stock_chart_impl("NVDA", "1d", "raw"))

    assert payload["price_provider"] == "Massive"
    assert [bar["v"] for bar in payload["bars"]] == [None, 0]


def test_extended_quote_only_requires_observed_zero_volume(monkeypatch):
    eastern = ZoneInfo("America/New_York")
    history = pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [103.0, 104.0],
            "Low": [98.0, 99.0],
            "Close": [101.0, 102.0],
            "Volume": [float("nan"), 0.0],
        },
        index=pd.DatetimeIndex(
            [
                datetime(2026, 9, 11, 8, 0, tzinfo=eastern),
                datetime(2026, 9, 11, 8, 5, tzinfo=eastern),
            ]
        ),
    )
    ticker = SimpleNamespace(history=lambda **_kwargs: history)
    monkeypatch.setattr(massive, "configured", lambda: False)
    monkeypatch.setattr(stocks.yf, "Ticker", lambda _symbol: ticker)

    payload = asyncio.run(stocks._stock_chart_impl("NVDA", "5m", "raw"))

    unknown, observed_zero = payload["bars"]
    assert unknown["v"] is None
    assert unknown["quote_only"] is False
    assert (unknown["h"], unknown["l"]) == (103.0, 98.0)
    assert observed_zero["v"] == 0
    assert observed_zero["quote_only"] is True
    assert (observed_zero["h"], observed_zero["l"]) == (102.0, 101.0)
