from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.access import request_owner_access_context
from app.api import sectors as sector_api
from app.services import yahoo
from app.services.cache import TTLCache


def _raise_error():
    raise RuntimeError("provider down")


def test_shared_ttl_cache_has_a_hard_capacity_limit() -> None:
    bounded = TTLCache()

    for index in range(bounded._MAX_ENTRIES + 100):
        bounded.set(f"key:{index}", index, 300)

    assert len(bounded._store) == bounded._MAX_ENTRIES
    assert "key:0" not in bounded._store
    assert bounded.get(f"key:{bounded._MAX_ENTRIES + 99}") == bounded._MAX_ENTRIES + 99


def test_shared_ttl_cache_failed_unique_loads_do_not_retain_locks() -> None:
    bounded = TTLCache()

    async def scenario() -> None:
        async def fail():
            raise RuntimeError("provider down")

        for index in range(20):
            with pytest.raises(RuntimeError, match="provider down"):
                await bounded.get_or_set(f"failure:{index}", 60, fail)

    asyncio.run(scenario())

    assert bounded._locks == {}
    assert bounded._lock_users == {}


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


def test_yahoo_cache_coalesces_concurrent_cold_loads() -> None:
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(8)

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"value": 42}

    def run(_index: int):
        start.wait()
        return yahoo._cached("single-flight", 60, loader)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run, range(8)))

    assert calls == 1
    assert [result["value"] for result in results] == [42] * 8


def test_yahoo_empty_refresh_keeps_bounded_stale_value() -> None:
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    now = datetime.now(timezone.utc)
    yahoo._cache["expirations:TEST"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=60),
        ["2026-08-21"],
    )

    value, metadata = yahoo._cached(
        "expirations:TEST",
        300,
        lambda: [],
        max_stale_seconds=120,
        with_metadata=True,
        is_valid=bool,
    )

    assert value == ["2026-08-21"]
    assert metadata["_stale"] is True
    assert metadata["source_status"] == "stale"


def test_yahoo_invalid_cached_value_is_not_reused_as_stale() -> None:
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    now = datetime.now(timezone.utc)
    yahoo._cache["expirations:EMPTY"] = (
        now - timedelta(seconds=1),
        now - timedelta(seconds=60),
        [],
    )

    with pytest.raises(RuntimeError, match="provider down"):
        yahoo._cached(
            "expirations:EMPTY",
            300,
            _raise_error,
            max_stale_seconds=120,
            with_metadata=True,
            is_valid=bool,
        )

    assert "expirations:EMPTY" not in yahoo._cache


def test_yahoo_failed_unique_loads_do_not_retain_key_locks() -> None:
    yahoo._cache.clear()
    yahoo._key_locks.clear()
    if hasattr(yahoo, "_key_lock_users"):
        yahoo._key_lock_users.clear()

    for index in range(20):
        with pytest.raises(RuntimeError, match="provider down"):
            yahoo._cached(f"failure:{index}", 60, _raise_error)

    assert yahoo._key_locks == {}
    if hasattr(yahoo, "_key_lock_users"):
        assert yahoo._key_lock_users == {}


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


def test_sector_iv_total_failure_is_cooled_and_concurrent_calls_coalesce(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sector_id = "cooldown-sector"
    monkeypatch.setitem(
        sector_api.SECTORS,
        sector_id,
        {"name": "Cooldown", "tickers": ["AAA", "BBB"]},
    )
    monkeypatch.setattr(
        sector_api,
        "_read_sector_iv_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        sector_api,
        "_read_strength_sector_iv_snapshot",
        lambda *_args, **_kwargs: None,
    )
    sector_api._cache.clear()
    sector_api._locks.clear()
    sector_api._sector_iv_failure_deadlines.clear()
    calls = 0

    async def unavailable(_sector_id: str) -> dict:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.02)
        return {
            "sector_id": sector_id,
            "rankings": [],
            "source_status": "insufficient_data",
        }

    monkeypatch.setattr(sector_api, "_iv_ranking_payload", unavailable)

    async def scenario() -> list[object]:
        # 冷却与合流是 visitor_live_pulls 开启后的保护；默认关闭时访客
        # 直接得到 public_snapshot_unavailable（见 test_visitor_action_boundaries）。
        with request_owner_access_context(False):
            return await asyncio.gather(
                *[
                    sector_api._request_iv_payload(
                        sector_id,
                        public_client_id="203.0.113.20",
                        visitor_live_allowed=True,
                    )
                    for _ in range(5)
                ],
                return_exceptions=True,
            )

    results = asyncio.run(scenario())

    assert calls == 1
    assert all(
        isinstance(result, HTTPException)
        and result.status_code == 503
        and result.detail["code"] == "sector_iv_cooldown"
        and int(result.headers["Retry-After"]) > 0
        for result in results
    )
    sector_api._sector_iv_failure_deadlines.clear()
    sector_api._public_sector_iv_recent.clear()
    sector_api._cache.clear()
    sector_api._locks.clear()


def test_public_sector_iv_provider_work_has_a_per_client_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sector_ids = ["budget-one", "budget-two", "budget-three"]
    for sector_id in sector_ids:
        monkeypatch.setitem(
            sector_api.SECTORS,
            sector_id,
            {"name": sector_id, "tickers": ["AAA"]},
        )
    monkeypatch.setattr(
        sector_api,
        "_read_sector_iv_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        sector_api,
        "_read_strength_sector_iv_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        sector_api,
        "_write_sector_iv_snapshot",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(sector_api, "_PUBLIC_SECTOR_IV_CLIENT_LIMIT", 2)
    sector_api._cache.clear()
    sector_api._locks.clear()
    sector_api._sector_iv_failure_deadlines.clear()
    sector_api._public_sector_iv_recent.clear()

    async def available(sector_id: str) -> dict:
        return {
            "sector_id": sector_id,
            "sector_name": sector_id,
            "rankings": [{"ticker": "AAA", "atm_iv_percent": 25.0}],
            "source_status": "active",
        }

    monkeypatch.setattr(sector_api, "_iv_ranking_payload", available)

    async def scenario() -> None:
        with request_owner_access_context(False):
            for sector_id in sector_ids[:2]:
                await sector_api._request_iv_payload(
                    sector_id,
                    public_client_id="203.0.113.21",
                    visitor_live_allowed=True,
                )
            with pytest.raises(HTTPException) as captured:
                await sector_api._request_iv_payload(
                    sector_ids[2],
                    public_client_id="203.0.113.21",
                    visitor_live_allowed=True,
                )
            assert captured.value.status_code == 429
            assert captured.value.detail["code"] == "sector_iv_rate_limited"
            assert int(captured.value.headers["Retry-After"]) > 0

    asyncio.run(scenario())
    sector_api._public_sector_iv_recent.clear()
    sector_api._cache.clear()
    sector_api._locks.clear()


def test_sector_iv_uses_massive_price_then_yahoo_for_uncovered_symbols(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sector_api.SECTORS,
        "price-fallback-sector",
        {"name": "Price Fallback", "tickers": ["AAA", "BBB"]},
    )
    monkeypatch.setattr(sector_api.massive, "configured", lambda: True)
    monkeypatch.setattr(sector_api.massive, "to_symbol", lambda ticker: ticker)
    monkeypatch.setattr(
        sector_api.massive,
        "snapshot_batch",
        lambda symbols: {
            "AAA": {
                "minute": {"c": 101.25},
                "day_close": 100.0,
            }
        },
    )
    monkeypatch.setattr(
        yahoo,
        "get_stock_iv_snapshot",
        lambda ticker: {
            "atm_iv": 0.25 if ticker == "AAA" else 0.5,
            "_stale": False,
            "as_of": "2026-07-24T05:00:00+00:00",
            "source_status": "active",
        },
    )
    yahoo_price_calls: list[str] = []

    def yahoo_price(ticker: str) -> float:
        yahoo_price_calls.append(ticker)
        return 202.5

    monkeypatch.setattr(yahoo, "get_last_price", yahoo_price)

    rows = asyncio.run(sector_api._sector_iv_rows("price-fallback-sector"))
    by_ticker = {row["ticker"]: row for row in rows}

    assert by_ticker["AAA"]["price"] == 101.25
    assert by_ticker["AAA"]["price_provider"] == "Massive"
    assert by_ticker["BBB"]["price"] == 202.5
    assert by_ticker["BBB"]["price_provider"] == "Yahoo/yfinance"
    assert yahoo_price_calls == ["BBB"]
    assert {row["provider"] for row in rows} == {"Yahoo/yfinance"}


def test_sector_iv_keeps_valid_iv_when_both_price_sources_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(
        sector_api.SECTORS,
        "iv-without-price",
        {"name": "IV Without Price", "tickers": ["AAA"]},
    )
    monkeypatch.setattr(sector_api.massive, "configured", lambda: False)
    monkeypatch.setattr(
        yahoo,
        "get_stock_iv_snapshot",
        lambda _ticker: {
            "atm_iv": 0.35,
            "_stale": False,
            "as_of": "2026-07-24T05:00:00+00:00",
            "source_status": "active",
        },
    )
    monkeypatch.setattr(
        yahoo,
        "get_last_price",
        lambda _ticker: (_ for _ in ()).throw(RuntimeError("price unavailable")),
    )

    rows = asyncio.run(sector_api._sector_iv_rows("iv-without-price"))

    assert rows[0]["iv"] == 0.35
    assert rows[0]["price"] is None
    assert rows[0]["price_provider"] is None
    assert rows[0]["provider"] == "Yahoo/yfinance"
