from __future__ import annotations

import asyncio
import copy
import json
import os
import time

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.access import OwnerAccessRuntime, hash_owner_password, require_public_read_or_owner_access
from app.api import accounts as accounts_api
from app.api import stocks
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, set_account_store
from app import stock_chart_snapshot as snapshots

HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1"}
PERIODS = ("5m", "15m", "1h", "1w")


def chart(symbol: str, period: str) -> dict:
    return {
        "ticker": symbol, "range": period, "price_adjustment": "raw",
        "price_provider": "fixture", "source_status": "active",
        "bars": [{"t": 1_700_000_000 + n * 300, "o": 100, "h": 102, "l": 99,
                  "c": 101, "v": 1000} for n in range(3)],
        "ema20": [], "sma50": [],
    }


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    set_account_store(AccountStore(tmp_path / "accounts.db"))
    accounts_api.reset_rate_limits()
    for state in (stocks._endpoint_cache, stocks._endpoint_locks,
                  stocks._endpoint_lock_users, stocks._endpoint_refresh_retry_after,
                  stocks._stock_chart_pull_tasks, stocks._public_stock_pull_recent,
                  stocks._public_stock_pull_ticker_deadlines):
        state.clear()
    yield
    set_account_store(None)
    accounts_api.reset_rate_limits()
    stocks._endpoint_cache.clear()
    stocks._stock_chart_pull_tasks.clear()


@pytest.fixture
def provider(monkeypatch):
    calls = []

    async def load(symbol, period, adjustment):
        calls.append((symbol, period, adjustment))
        return chart(symbol, period)

    monkeypatch.setattr(stocks, "_load_stock_chart", load)
    return calls


def client(*, register=True):
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"), password_hash=hash_owner_password("fixture-owner-password"),
    )
    app.include_router(stocks.router, dependencies=[Depends(require_public_read_or_owner_access)])
    app.include_router(accounts_api.router)
    result = TestClient(app, base_url="https://localhost")
    if register:
        response = result.post("/api/account/register", headers=HEADERS,
                               json={"username": "chart-user", "password": "fixture-customer-password"})
        assert response.status_code == 201
    return result


@pytest.mark.parametrize("period", PERIODS)
def test_customer_pulls_selected_chart_and_reads_it_after_restart(period, provider):
    with client() as api:
        path = f"/api/stocks/NVDA/chart?range={period}&adjustment=raw"
        assert api.get(path).json()["detail"]["code"] == "public_snapshot_unavailable"
        response = api.post(f"/api/stocks/NVDA/pull?chart_range={period}", json={}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json()["range"] == period
        assert response.json()["persisted"] is True
        assert response.json()["bar_count"] == 3
        assert provider == [("NVDA", period, "raw")]
        assert api.get(path).json()["bars"] == chart("NVDA", period)["bars"]
        stocks._endpoint_cache.clear()
        with client(register=False) as guest:
            result = guest.get(path)
            assert result.status_code == 200
            assert result.json()["range"] == period
            assert len(result.json()["bars"]) == 3
        assert provider == [("NVDA", period, "raw")]
        assert api.get("/api/stocks/AAPL/chart?range=" + period).status_code == 503


def test_different_periods_have_separate_cooldowns_but_share_account_budget(provider):
    with client() as api:
        for period in PERIODS:
            assert api.post(f"/api/stocks/NVDA/pull?chart_range={period}", json={}, headers=HEADERS).status_code == 200
        repeat = api.post("/api/stocks/NVDA/pull?chart_range=5m", json={}, headers=HEADERS)
        assert repeat.status_code == 429
        assert repeat.json()["detail"]["code"] == "stock_pull_cooldown"
        for symbol in ("AAPL", "MSFT"):
            assert api.post(f"/api/stocks/{symbol}/pull?chart_range=5m", json={}, headers=HEADERS).status_code == 200
        blocked = api.post("/api/stocks/AMD/pull?chart_range=5m", json={}, headers=HEADERS)
        assert blocked.status_code == 429
        assert blocked.json()["detail"]["code"] == "stock_pull_rate_limited"
        assert len(provider) == 6
        assert all(key.startswith("acct:") for key in stocks._public_stock_pull_recent)


def test_anonymous_and_cross_origin_requests_cannot_start_chart_work(provider):
    with client(register=False) as guest:
        response = guest.post("/api/stocks/NVDA/pull?chart_range=5m", json={}, headers=HEADERS)
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "account_login_required"
    with client() as api:
        response = api.post("/api/stocks/NVDA/pull?chart_range=5m", json={},
                            headers={**HEADERS, "Origin": "https://unrelated.example"})
        assert response.status_code == 403
        assert api.post("/api/stocks/NVDA/pull?chart_range=1s", json={}, headers=HEADERS).status_code == 422
    assert provider == []


def test_same_chart_shares_inflight_work_and_survives_one_client_cancellation(monkeypatch):
    async def scenario():
        started, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def load(symbol, period, adjustment):
            calls.append((symbol, period))
            started.set()
            await release.wait()
            return chart(symbol, period)

        monkeypatch.setattr(stocks, "_load_stock_chart", load)
        first = asyncio.create_task(stocks._coalesced_stock_chart_pull("NVDA", "5m", public_client_id="acct:first"))
        await started.wait()
        second = asyncio.create_task(stocks._coalesced_stock_chart_pull("NVDA", "5m", public_client_id="acct:second"))
        await asyncio.sleep(0)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        release.set()
        assert (await second)["persisted"]
        assert calls == [("NVDA", "5m")]
        assert len(stocks._public_stock_pull_recent["acct:first"]) == 1
        assert "acct:second" not in stocks._public_stock_pull_recent

    asyncio.run(scenario())


def test_empty_provider_refresh_keeps_last_valid_chart(monkeypatch):
    async def scenario():
        async def valid(*args):
            return chart("NVDA", "5m")

        monkeypatch.setattr(stocks, "_load_stock_chart", valid)
        await stocks._pull_stock_chart_once("NVDA", "5m")
        path = snapshots.stock_chart_snapshot_path("NVDA", "5m")
        before = path.read_bytes()

        async def empty(*args):
            return {**chart("NVDA", "5m"), "bars": []}

        monkeypatch.setattr(stocks, "_load_stock_chart", empty)
        with pytest.raises(HTTPException) as raised:
            await stocks._pull_stock_chart_once("NVDA", "5m")
        assert raised.value.detail["code"] == "stock_chart_pull_failed"
        assert path.read_bytes() == before
        assert len(stocks._endpoint_cache["chart:NVDA:5m:raw"].value["bars"]) == 3

    asyncio.run(scenario())


def test_real_intraday_analysis_survives_snapshot_reload(monkeypatch):
    async def source(symbol, period, adjustment):
        value = chart(symbol, period)
        value["bars"] = [
            {"t": 1_783_430_000 + n * 300, "o": 100 + n / 10, "h": 102 + n / 10,
             "l": 99 + n / 10, "c": 101 + n / 10, "v": 1000 + n, "ext": False}
            for n in range(240)
        ]
        return value

    monkeypatch.setattr(stocks, "_stock_chart_impl", source)
    asyncio.run(stocks._pull_stock_chart_once("NVDA", "5m"))
    before = stocks._endpoint_cache["chart:NVDA:5m:raw"].value
    assert isinstance(before["chart_analysis"], dict)
    assert all(bar["closed"] for bar in before["bars"])
    stocks._endpoint_cache.clear()
    restored = snapshots.read_stock_chart_resource("NVDA", "5m")["payload"]
    assert restored["chart_analysis"] == before["chart_analysis"]
    assert restored["bars"] == before["bars"]


@pytest.mark.parametrize("field,value", [("ticker", "AAPL"), ("range", "15m"), ("price_adjustment", "adjusted")])
def test_snapshots_reject_mismatched_identity(field, value):
    value_chart = copy.deepcopy(chart("NVDA", "5m"))
    value_chart[field] = value
    with pytest.raises(ValueError):
        snapshots.write_stock_chart_resource("NVDA", "5m", value_chart, time.time())


def test_snapshot_storage_is_bounded_and_does_not_evict_daily_pulls(monkeypatch, tmp_path):
    monkeypatch.setattr(snapshots, "MAX_SNAPSHOTS", 2)
    legacy = tmp_path / "stock-pull-snapshots-v1.json"
    legacy.write_text(json.dumps({"sentinel": "existing daily resources"}))
    # Prune orders by mtime. Same-second writes can share mtime and then
    # eviction follows iterdir order, so NVDA is not always the first drop.
    written_at = time.time() - 10
    for offset, symbol in enumerate(("NVDA", "AAPL", "BRK.B")):
        snapshots.write_stock_chart_resource(symbol, "5m", chart(symbol, "5m"), written_at + offset)
        path = snapshots.stock_chart_snapshot_path(symbol, "5m")
        os.utime(path, ns=(int((written_at + offset) * 1e9), int((written_at + offset) * 1e9)))
    assert not snapshots.stock_chart_snapshot_path("NVDA", "5m").exists()
    assert snapshots.read_stock_chart_resource("BRK.B", "5m") is not None
    assert len(list((tmp_path / "stock-chart-snapshots-v1").glob("*.json"))) == 2
    assert json.loads(legacy.read_text()) == {"sentinel": "existing daily resources"}
