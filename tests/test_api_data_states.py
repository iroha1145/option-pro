from __future__ import annotations

import asyncio
from datetime import date

import pytest
from fastapi import HTTPException

from app.api import earnings, options, stocks


def test_endpoint_cache_uses_only_bounded_marked_stale_data(monkeypatch):
    async def scenario():
        clock = [1_000.0]
        monkeypatch.setattr(stocks.time, "time", lambda: clock[0])
        stocks._endpoint_cache.clear()
        stocks._endpoint_locks.clear()

        async def healthy_loader():
            return {"value": 42}

        fresh = await stocks._cached_endpoint(
            "test:bounded-stale", 10, healthy_loader, stale_ttl=20
        )
        assert fresh["value"] == 42
        assert fresh["_stale"] is False
        assert fresh["source_status"] == "active"

        async def failed_loader():
            raise RuntimeError("provider unavailable")

        clock[0] = 1_011.0
        stale = await stocks._cached_endpoint(
            "test:bounded-stale", 10, failed_loader, stale_ttl=20
        )
        assert stale["value"] == 42
        assert stale["_stale"] is True
        assert stale["source_status"] == "degraded"
        assert stale["stale_reason"] == "upstream_refresh_failed"

        clock[0] = 1_031.0
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await stocks._cached_endpoint(
                "test:bounded-stale", 10, failed_loader, stale_ttl=20
            )
        assert "test:bounded-stale" not in stocks._endpoint_cache

    asyncio.run(scenario())


def test_unusual_options_reports_total_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(options, "POPULAR_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(options.yf, "Ticker", BrokenTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(options._unusual_activity_impl("all", 1.0))
    assert exc_info.value.status_code == 503


def test_expirations_route_preserves_freshness_metadata(monkeypatch):
    monkeypatch.setattr(
        options.yahoo,
        "get_expirations_snapshot",
        lambda _ticker: {
            "expirations": ["2026-08-21"],
            "_stale": True,
            "source_status": "stale",
            "as_of": "2026-07-10T00:00:00+00:00",
            "stale_age_seconds": 42.0,
        },
    )

    result = asyncio.run(options.expirations("aapl"))
    assert result["ticker"] == "AAPL"
    assert result["expirations"] == ["2026-08-21"]
    assert result["_stale"] is True
    assert result["source_status"] == "stale"
    assert result["stale_age_seconds"] == 42.0


def test_earnings_reports_total_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(earnings, "EARNINGS_TICKERS", ["AAA", "BBB"])
    monkeypatch.setattr(earnings.yf, "Ticker", BrokenTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(earnings._build_upcoming_earnings(date(2026, 7, 10)))
    assert exc_info.value.status_code == 503


def test_legacy_stock_signals_reports_provider_failure(monkeypatch):
    class BrokenTicker:
        def __init__(self, _symbol):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(stocks.yf, "Ticker", BrokenTicker)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.stock_signals("AAA"))
    assert exc_info.value.status_code == 503


def test_watchlist_reports_provider_failure_without_unbounded_stale_data(monkeypatch):
    async def failed_watchlist():
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(stocks, "_build_watchlist", failed_watchlist)
    stocks._endpoint_cache.pop("watchlist", None)
    stocks._endpoint_locks.pop("watchlist", None)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(stocks.watchlist())
    assert exc_info.value.status_code == 503
