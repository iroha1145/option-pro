from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.access import request_owner_access_context
from app.api import signals as signals_api
from app.api import stocks
from app import stock_data_reads
from app.public_stock_data import public_stock_snapshot_path
from app.services import signals as signal_service
from app.services import yahoo
from app.stock_pull_snapshot import read_stock_pull_resource, write_stock_pull_resources


def _overview(symbol="AAOI", price=100):
    return {"ticker": symbol, "price": price, "price_provider": "fixture"}


def _chart(symbol="AAOI", price=100, adjustment="raw"):
    return {
        "ticker": symbol, "range": "1d", "price_adjustment": adjustment,
        "price_provider": "fixture",
        "bars": [
            {"t": 1_700_000_000 + i * 86400, "o": price + i,
             "h": price + i + 1, "l": price + i - 1,
             "c": price + i, "v": 1000 + i}
            for i in range(80)
        ],
    }


def _signals(value=55):
    return {"rsi14": {"value": value}, "return_20d": {"value": 2},
            "macd_hist": {"value": 0.5}}


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    stocks._endpoint_cache.clear()
    signal_service._cache.clear()
    yield tmp_path
    stocks._endpoint_cache.clear()
    signal_service._cache.clear()


def test_newest_valid_resource_wins_independently_of_file_time(isolated_data):
    now = time.time()
    path = public_stock_snapshot_path("AAOI")
    write_stock_pull_resources("AAOI", {
        "overview": (_overview(price=90), now - 20),
        "daily_chart": (_chart(price=90), now - 5),
    })
    write_stock_pull_resources("AAOI", {
        "overview": (_overview(price=100), now - 10),
        "daily_chart": (_chart(price=100), now - 15),
    }, path=path)
    read = stock_data_reads.read_latest_stock_resource
    assert read("AAOI", "overview")["source"] == "public_stock_data"
    assert read("AAOI", "daily_chart")["source"] == "manual_pull"
    assert read("AAOI", "daily_chart")["payload"]["bars"][0]["c"] == 90
    document = json.loads(path.read_text())
    document["entries"]["AAOI"]["overview"]["payload"]["price"] = -1
    path.write_text(json.dumps(document))
    assert read("AAOI", "overview")["payload"]["price"] == 90


def test_public_resources_survive_restart_and_replace_fresh_old_get_cache(monkeypatch):
    now = time.time()
    write_stock_pull_resources("AAOI", {
        "overview": (_overview(), now - 1),
        "daily_chart": (_chart(), now - 1),
        "signals": (_signals(), now - 1),
    }, path=public_stock_snapshot_path("AAOI"))
    stocks._endpoint_cache["stock:AAOI"] = stocks._EndpointCacheEntry(
        expires_at=now + 60, stale_until=now + 600,
        fetched_at=now - 30, value=_overview(price=1),
    )
    # A different process can have an older signal result still in memory.
    signal_service._cache["stock_signals:AAOI"] = (
        datetime.now(timezone.utc) + timedelta(minutes=5), _signals(1),
    )

    def forbidden(*args, **kwargs):
        pytest.fail("Guest reads must not contact a market-data provider")

    monkeypatch.setattr(stocks, "_stock_overview_impl", forbidden)
    monkeypatch.setattr(stocks, "_stock_chart_impl", forbidden)
    monkeypatch.setattr(signals_api, "compute_stock_signals", forbidden)

    async def reads():
        with request_owner_access_context(False):
            return (await stocks.stock_overview("AAOI"),
                    await stocks.stock_chart("AAOI", "1d", "raw"),
                    await signals_api.stock_signals("AAOI"))

    overview, chart, signals = asyncio.run(reads())
    assert overview["price"] == 100
    assert len(chart["bars"]) == 80
    assert signals["signals"]["rsi14"]["value"] == 55
    assert signals["snapshot_source"] == "public_stock_data"
    assert datetime.fromisoformat(signals["as_of"]).timestamp() == pytest.approx(now - 1)
    stocks._endpoint_cache.clear()
    signal_service._cache.clear()
    assert asyncio.run(reads())[0]["price"] == 100


@pytest.mark.parametrize("adjusted_result", ["valid", "empty", "failure"])
def test_background_pull_never_fetches_benchmark_or_options_and_keeps_partial(
    monkeypatch, adjusted_result,
):
    def forbidden(*args, **kwargs):
        pytest.fail("Background stock coverage started benchmark/options fallback")

    async def overview(symbol):
        return _overview(symbol)

    async def chart(symbol, range_key, adjustment):
        if adjustment == "adjusted":
            if adjusted_result == "failure":
                raise RuntimeError("fixture adjusted source unavailable")
            if adjusted_result == "empty":
                return {**_chart(symbol, adjustment=adjustment), "bars": []}
        return _chart(symbol, adjustment=adjustment)

    monkeypatch.setattr(stocks, "_stock_overview_impl", overview)
    monkeypatch.setattr(stocks, "_stock_chart_impl", chart)
    monkeypatch.setattr(signal_service, "_history", forbidden)
    monkeypatch.setattr(signal_service, "compute_stock_signals", forbidden)
    monkeypatch.setattr(yahoo, "get_stock_iv", forbidden)
    path = public_stock_snapshot_path("AAOI")
    result = asyncio.run(stocks._pull_stock_data_once(
        "AAOI", snapshot_path=path, include_options=False,
    ))
    assert read_stock_pull_resource("AAOI", "overview") is None
    assert read_stock_pull_resource("AAOI", "overview", path=path)["payload"]["price"] == 100
    assert read_stock_pull_resource("AAOI", "daily_chart", path=path)
    if adjusted_result == "valid":
        assert result["status"] == "completed"
        payload = read_stock_pull_resource("AAOI", "signals", path=path)["payload"]
        assert payload["atm_iv_percent"]["value"] is None
        assert payload["relative_strength_spy"]["value"] is None
    else:
        assert result["status"] == "partial"
        assert result["resources"]["signals"]["status"] == "failed"
        assert read_stock_pull_resource("AAOI", "signals", path=path) is None


def test_same_snapshot_updates_freshness_when_market_phase_changes(monkeypatch):
    now = time.time()
    saved = now - 600
    write_stock_pull_resources("AAOI", {"overview": (_overview(), saved)},
                               path=public_stock_snapshot_path("AAOI"))
    stocks._endpoint_cache["stock:AAOI"] = stocks._EndpointCacheEntry(
        expires_at=saved + 60, stale_until=saved + 1800,
        fetched_at=saved, value=_overview(),
    )
    monkeypatch.setattr("app.public_stock_data._market_phase", lambda _now: "closed")
    closed = asyncio.run(stocks._hydrate_stock_pull_resource("AAOI", "overview", "stock:AAOI"))
    assert closed.expires_at == saved + 6 * 3600
    monkeypatch.setattr("app.public_stock_data._market_phase", lambda _now: "active")
    active = asyncio.run(stocks._hydrate_stock_pull_resource("AAOI", "overview", "stock:AAOI"))
    assert active.expires_at == saved + 30 * 60
    assert active.fetched_at == saved


def test_existing_home_watchlist_gets_new_public_daily_curve_without_quote_refresh(monkeypatch):
    now = time.time()
    original = {"groups": [{"stocks": [{"ticker": "AAOI", "price": 999}]}]}
    stocks._endpoint_cache["watchlist"] = stocks._EndpointCacheEntry(
        expires_at=now + 300, stale_until=now + 1800,
        fetched_at=now, value=original,
    )
    monkeypatch.setattr(stocks, "_load_watchlist_snapshot_once", lambda _now: None)

    def forbidden():
        pytest.fail("New daily bars should not trigger a watchlist provider fetch")

    monkeypatch.setattr(stocks, "_build_watchlist", forbidden)
    with request_owner_access_context(False):
        before = asyncio.run(stocks.watchlist(None))
    assert "daily_trend" not in before["groups"][0]["stocks"][0]
    write_stock_pull_resources("AAOI", {"daily_chart": (_chart(), now)},
                               path=public_stock_snapshot_path("AAOI"))
    with request_owner_access_context(False):
        after = asyncio.run(stocks.watchlist(None))
    points = after["groups"][0]["stocks"][0]["daily_trend"]["points"]
    assert len(points) == 30
    assert points[-1]["close"] == 179
    assert "daily_trend" not in original["groups"][0]["stocks"][0]


def test_status_is_read_only_bounded_and_reports_real_resource_times(monkeypatch, isolated_data):
    now = time.time()
    write_stock_pull_resources("AAOI", {"daily_chart": (_chart(), now - 600)},
                               path=public_stock_snapshot_path("AAOI"))

    def forbidden(*args, **kwargs):
        pytest.fail("Status reads must not schedule or pull stocks")

    monkeypatch.setattr(stocks, "_pull_stock_data_once", forbidden)
    monkeypatch.setattr("app.public_stock_data.register_public_stock_demand", forbidden)
    app = FastAPI()
    app.include_router(stocks.router)
    client = TestClient(app)
    response = client.get("/api/stocks/data/status?tickers=aaoi,AAOI,SPX,^GSPC,MSFT")
    assert response.status_code == 200
    items = response.json()["items"]
    assert [item["ticker"] for item in items] == ["AAOI", "^GSPC", "MSFT"]
    assert items[0]["status"] == "partial"
    assert items[0]["resources"]["daily_chart"]["available"] is True
    assert items[0]["resources"]["overview"]["available"] is False
    assert datetime.fromisoformat(items[0]["as_of"]).timestamp() == pytest.approx(now - 600)
    assert items[2]["status"] == "pending"
    assert items[2]["as_of"] is None
    assert not (isolated_data / "public-stock-data-v1" / "demand").exists()
    assert client.get("/api/stocks/data/status", params={"tickers": "../bad"}).status_code == 400
    assert client.get("/api/stocks/data/status", params={"tickers": ",".join(["AAPL"] * 201)}).status_code == 400


def test_status_separates_usable_old_resources_from_failed_refresh(monkeypatch):
    now = time.time()
    write_stock_pull_resources("AAOI", {
        "overview": (_overview(), now - 10),
        "daily_chart": (_chart(), now - 10),
        "signals": (_signals(), now - 10),
    }, path=public_stock_snapshot_path("AAOI"))
    monkeypatch.setattr(stocks, "read_public_stock_status", lambda *args, **kwargs: {
        "status": "partial", "retry_after_seconds": 60,
    })
    item = stocks._stock_data_status_items(["AAOI"], now)[0]
    assert item["status"] == "ready"
    assert item["refresh_status"] == "failed"
    assert all(resource["available"] for resource in item["resources"].values())
