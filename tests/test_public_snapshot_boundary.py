from __future__ import annotations

import asyncio
import time

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
    require_public_read_or_owner_access,
)
from app.api import earnings, market, options, sectors, signals, stocks, strength
from app.personal_config import AccessConfig
from app.services import signals as signal_service
from app.services import yahoo
from app.services.cache import cache


def _watchlist_payload(label: str) -> dict:
    return {
        "groups": [
            {
                "id": label,
                "name": label,
                "stocks": [
                    {
                        "ticker": "AAPL",
                        "name": "Apple",
                        "price": 100.0,
                        "change_percent": 1.25,
                        "spark": [98.0, 100.0],
                    }
                ],
            }
        ],
        "attempted": 1,
        "succeeded": 1,
        "failed": 0,
        "failed_tickers": [],
        "data_limited": False,
        "source_status": "active",
    }


@pytest.fixture(autouse=True)
def _clear_process_caches(monkeypatch: pytest.MonkeyPatch):
    stocks._endpoint_cache.clear()
    stocks._endpoint_locks.clear()
    stocks._endpoint_lock_users.clear()
    stocks._endpoint_refresh_tasks.clear()
    stocks._endpoint_refresh_retry_after.clear()
    cache.clear()
    sectors._cache.clear()
    sectors._locks.clear()
    with signal_service._cache_lock:
        signal_service._cache.clear()
    with yahoo._cache_lock:
        yahoo._cache.clear()
    monkeypatch.setattr(stocks, "_watchlist_snapshot_load_attempted", False)
    monkeypatch.setattr(stocks, "_watchlist_snapshot_observed", None)
    yield
    cache.clear()


async def _expect_unavailable(awaitable) -> HTTPException:
    with pytest.raises(HTTPException) as captured:
        await awaitable
    assert captured.value.status_code == 503
    return captured.value


def test_public_cold_cache_never_calls_market_data_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    def unexpected(name: str):
        def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"public request called {name}")

        return fail

    def unexpected_async(name: str):
        async def fail(*_args, **_kwargs):
            calls.append(name)
            raise AssertionError(f"public request called {name}")

        return fail

    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing-watchlist.json")
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_async("watchlist"))
    monkeypatch.setattr(stocks, "_fetch_company_logo", unexpected_async("logo"))
    monkeypatch.setattr(stocks, "_stock_overview_impl", unexpected_async("stock-overview"))
    monkeypatch.setattr(stocks, "_stock_chart_impl", unexpected_async("stock-chart"))
    monkeypatch.setattr(stocks.yf, "Ticker", unexpected("stock-search"))
    monkeypatch.setattr(options, "_unusual_activity_impl", unexpected_async("unusual-options"))
    monkeypatch.setattr(yahoo, "get_expirations_snapshot", unexpected("expirations"))
    monkeypatch.setattr(yahoo, "get_option_chain", unexpected("option-chain"))
    monkeypatch.setattr(earnings, "_build_upcoming_earnings", unexpected_async("earnings"))
    monkeypatch.setattr(market, "_build_indices", unexpected_async("market-indices"))
    monkeypatch.setattr(sectors, "_iv_ranking_payload", unexpected_async("sector-iv"))
    monkeypatch.setattr(signals, "compute_market_signals", unexpected("market-signals"))
    monkeypatch.setattr(signals, "compute_stock_signals", unexpected("stock-signals"))
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", tmp_path / "missing-strength.json")
    monkeypatch.setattr(strength, "stock_strength", unexpected_async("stock-strength"))
    monkeypatch.setattr(strength, "sector_strength", unexpected_async("sector-strength"))
    monkeypatch.setattr(strength, "market_strength", unexpected_async("market-strength"))

    async def scenario() -> None:
        with request_owner_access_context(False):
            await _expect_unavailable(stocks.watchlist(None))
            await _expect_unavailable(stocks.watchlist("AAPL,MSFT"))
            assert await stocks.search_stocks("ZZZZUNLISTED") == []
            await _expect_unavailable(stocks.stock_signals("AAPL"))
            await _expect_unavailable(stocks.stock_logo("AAPL"))
            await _expect_unavailable(stocks.stock_overview("AAPL"))
            await _expect_unavailable(stocks.stock_chart("AAPL", "1d", "raw"))
            await _expect_unavailable(options.unusual_activity(type="all", min_vol_oi=1.0))
            await _expect_unavailable(options.expirations("AAPL"))
            await _expect_unavailable(options.option_chain("AAPL", "2026-12-18"))
            await _expect_unavailable(earnings.upcoming_earnings())
            await _expect_unavailable(market.market_indices())
            await _expect_unavailable(sectors.iv_ranking(next(iter(sectors.SECTORS))))
            await _expect_unavailable(signals.market_signals())
            await _expect_unavailable(signals.stock_signals("AAPL"))
            await _expect_unavailable(strength.market())
            await _expect_unavailable(strength.stock("AAPL", "balanced"))
            await _expect_unavailable(strength.sectors("3mo"))

    asyncio.run(scenario())

    assert calls == []
    assert list(tmp_path.iterdir()) == []


def test_public_watchlist_reads_persisted_snapshot_without_refresh_or_write(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 50_000.0
    snapshot_path = tmp_path / "watchlist.json"
    stocks._write_watchlist_snapshot(
        snapshot_path,
        payload=_watchlist_payload("saved"),
        saved_at=now - 600,
    )
    original = snapshot_path.read_bytes()
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(stocks.time, "time", lambda: now)

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("public watchlist must not refresh")

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("public watchlist must not write")

    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)
    monkeypatch.setattr(stocks, "_persist_watchlist_snapshot", unexpected_write)

    async def scenario() -> dict:
        with request_owner_access_context(False):
            return await stocks.watchlist(None)

    payload = asyncio.run(scenario())

    assert payload["groups"][0]["id"] == "saved"
    assert payload["_stale"] is True
    assert payload["stale_reason"] == "public_snapshot_only"
    assert snapshot_path.read_bytes() == original
    assert stocks._endpoint_refresh_tasks == {}


def test_public_expired_watchlist_never_refreshes_or_rewrites(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    now = 200_000.0
    snapshot_path = tmp_path / "watchlist.json"
    stocks._write_watchlist_snapshot(
        snapshot_path,
        payload=_watchlist_payload("expired"),
        saved_at=now - stocks._WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS - 1,
    )
    original = snapshot_path.read_bytes()
    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", snapshot_path)
    monkeypatch.setattr(stocks.time, "time", lambda: now)

    async def unexpected_refresh(*_args, **_kwargs):
        raise AssertionError("expired public snapshot must not refresh")

    def unexpected_write(*_args, **_kwargs):
        raise AssertionError("expired public snapshot must not write")

    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)
    monkeypatch.setattr(stocks, "_persist_watchlist_snapshot", unexpected_write)

    async def scenario() -> None:
        with request_owner_access_context(False):
            await _expect_unavailable(stocks.watchlist(None))

    asyncio.run(scenario())

    assert snapshot_path.read_bytes() == original
    assert stocks._endpoint_refresh_tasks == {}


def test_public_endpoints_serve_existing_cache_entries_without_loaders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = time.time()
    today = earnings._market_today()
    cache.set(f"earnings:upcoming:{today.isoformat()}", {"items": ["saved"]}, 60)
    cache.set("market:indices", {"indices": ["saved"]}, 60)
    cache.set(options._unusual_key("all", 1.0), {"items": ["saved"]}, 60)
    sector_id = next(iter(sectors.SECTORS))
    sectors._cache[f"iv:{sector_id}"] = (
        now + 60,
        now,
        {"sector_id": sector_id, "rankings": []},
    )

    monkeypatch.setattr(earnings, "_build_upcoming_earnings", lambda *_args: pytest.fail("loader called"))
    monkeypatch.setattr(market, "_build_indices", lambda: pytest.fail("loader called"))
    monkeypatch.setattr(options, "_unusual_activity_impl", lambda *_args: pytest.fail("loader called"))
    monkeypatch.setattr(sectors, "_iv_ranking_payload", lambda *_args: pytest.fail("loader called"))

    async def scenario() -> None:
        with request_owner_access_context(False):
            assert (await earnings.upcoming_earnings())["items"] == ["saved"]
            assert (await market.market_indices())["indices"] == ["saved"]
            assert (
                await options.unusual_activity(type="all", min_vol_oi=1.0)
            )["items"] == ["saved"]
            assert (await sectors.iv_ranking(sector_id))["sector_id"] == sector_id

    asyncio.run(scenario())


def test_request_owner_context_is_task_local_and_restored() -> None:
    async def observe(owner: bool) -> bool:
        from app.access import current_request_is_owner

        with request_owner_access_context(owner):
            await asyncio.sleep(0)
            return current_request_is_owner()

    async def scenario() -> list[bool]:
        return list(await asyncio.gather(observe(False), observe(True)))

    assert asyncio.run(scenario()) == [False, True]


def test_directly_mounted_public_router_keeps_cold_cache_snapshot_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    calls: list[str] = []

    async def unexpected_refresh(*_args, **_kwargs):
        calls.append("watchlist")
        raise AssertionError("anonymous request called the watchlist provider")

    monkeypatch.setattr(stocks, "_WATCHLIST_SNAPSHOT_PATH", tmp_path / "missing.json")
    monkeypatch.setattr(stocks, "_build_watchlist", unexpected_refresh)

    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("public-boundary-test-password"),
    )
    app.include_router(
        stocks.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get("/api/stocks/watchlist")
        head = client.head("/api/stocks/watchlist")

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "public_snapshot_unavailable"
    assert head.status_code in {405, 503}
    assert calls == []
    assert list(tmp_path.iterdir()) == []
