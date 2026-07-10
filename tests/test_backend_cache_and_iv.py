from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.api import sectors as sector_api
from app.services import yahoo


def _raise_error():
    raise RuntimeError("provider down")


def test_yahoo_stale_cache_is_marked_and_has_a_hard_limit() -> None:
    yahoo._cache.clear()
    now = datetime.now(timezone.utc)
    yahoo._cache["recent"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=60),
        {"value": 42},
    )

    recent = yahoo._cached("recent", 300, _raise_error, max_stale_seconds=120)
    assert recent["value"] == 42
    assert recent["_stale"] is True
    assert recent["source_status"] == "stale"
    assert recent["as_of"]

    yahoo._cache["old"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=300),
        {"value": 99},
    )
    with pytest.raises(RuntimeError, match="provider down"):
        yahoo._cached("old", 300, _raise_error, max_stale_seconds=120)
    assert "old" not in yahoo._cache


def test_expiration_snapshot_and_stock_iv_snapshot_expose_freshness(monkeypatch: pytest.MonkeyPatch) -> None:
    yahoo._cache.clear()
    now = datetime.now(timezone.utc)
    yahoo._cache["expirations:TEST"] = (
        now + timedelta(seconds=60),
        now,
        ["2026-08-21"],
    )
    expirations = yahoo.get_expirations_snapshot("test")
    assert expirations["expirations"] == ["2026-08-21"]
    assert expirations["_stale"] is False
    assert expirations["source_status"] == "active"

    yahoo._cache["stock_iv:TEST"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=60),
        0.35,
    )
    monkeypatch.setattr(yahoo, "_get_ticker", lambda symbol: (_ for _ in ()).throw(RuntimeError("down")))
    iv = yahoo.get_stock_iv_snapshot("test")
    assert iv["atm_iv"] == 0.35
    assert iv["_stale"] is True
    assert iv["source_status"] == "stale"


def test_sector_cache_stale_fallback_is_marked_and_expires() -> None:
    sector_api._cache.clear()
    sector_api._locks.clear()
    now = time.time()
    sector_api._cache["recent"] = (
        now - 1,
        now - 60,
        {"rankings": [{"ticker": "AAA"}], "source_status": "active"},
    )

    async def fail():
        raise RuntimeError("provider down")

    recent = asyncio.run(sector_api._cached("recent", 600, fail, max_stale_seconds=120))
    assert recent["_stale"] is True
    assert recent["source_status"] == "stale"
    assert recent["as_of"]

    sector_api._locks.clear()
    sector_api._cache["old"] = (
        now - 1,
        now - 300,
        {"rankings": [{"ticker": "OLD"}]},
    )
    with pytest.raises(RuntimeError, match="provider down"):
        asyncio.run(sector_api._cached("old", 600, fail, max_stale_seconds=120))
    assert "old" not in sector_api._cache


def test_sector_iv_fields_are_explicit_and_sorted_high_to_low(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(
        sector_api.SECTORS,
        "test-sector",
        {"name": "Test Sector", "tickers": ["LOW", "HIGH", "MID"]},
    )

    async def rows(_sector_id: str):
        return [
            {"ticker": "LOW", "name": "Low", "price": 10, "iv": 0.20, "_stale": False, "as_of": "2026-01-01T00:00:00+00:00"},
            {"ticker": "HIGH", "name": "High", "price": 20, "iv": 0.80, "_stale": False, "as_of": "2026-01-01T00:00:00+00:00"},
            {"ticker": "MID", "name": "Mid", "price": 15, "iv": 0.50, "_stale": False, "as_of": "2026-01-01T00:00:00+00:00"},
        ]

    monkeypatch.setattr(sector_api, "_sector_iv_rows", rows)
    payload = asyncio.run(sector_api._iv_ranking_payload("test-sector"))

    assert [item["ticker"] for item in payload["rankings"]] == ["HIGH", "MID", "LOW"]
    assert [item["sector_iv_rank"] for item in payload["rankings"]] == [100.0, 50.0, 0.0]
    assert payload["rankings"][0]["atm_iv_percent"] == 80.0
    assert payload["rankings"][0]["iv_rank"] is None
    assert payload["rankings"][0]["iv_percentile"] is None
    assert payload["source_status"] == "active"
    assert payload["data_limited"] is False


def test_sector_iv_all_failures_are_not_reported_as_valid_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sector_api.SECTORS, "empty-sector", {"name": "Empty", "tickers": ["AAA", "BBB"]})

    async def rows(_sector_id: str):
        return [
            {"ticker": "AAA", "iv": None, "source_status": "error"},
            {"ticker": "BBB", "iv": None, "source_status": "error"},
        ]

    monkeypatch.setattr(sector_api, "_sector_iv_rows", rows)
    payload = asyncio.run(sector_api._iv_ranking_payload("empty-sector"))

    assert payload["rankings"] == []
    assert payload["source_status"] == "insufficient_data"
    assert payload["data_limited"] is True
    assert payload["success_rate"] == 0.0
    assert payload["failed_symbols"] == ["AAA", "BBB"]
