from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import time
from zoneinfo import ZoneInfo

import pytest

from app.access import request_owner_access_context
from app.api import stocks
from app.services.watchlist_trend import daily_trend
from app.stock_pull_snapshot import write_stock_pull_resources

NY = ZoneInfo("America/New_York")


def chart(ticker="NVDA", count=40):
    day = datetime.now(NY).replace(hour=16, minute=0, second=0, microsecond=0) - timedelta(days=80)
    bars = []
    while len(bars) < count:
        day += timedelta(days=1)
        if day.weekday() >= 5:
            continue
        close = 100 + len(bars) + (len(bars) % 3 - 1) * 2
        bars.append({"t": int(day.timestamp()), "o": close, "h": close + 1, "l": close - 1, "c": close, "v": 10})
    return {"ticker": ticker, "range": "1d", "price_adjustment": "raw", "bars": bars}


def test_projects_actual_last_30_sessions_without_mutation():
    data = chart()
    original = deepcopy(data)
    actual = daily_trend(data, market_timezone=NY)
    assert len(actual["points"]) == 30
    assert [p["close"] for p in actual["points"]] == [b["c"] for b in data["bars"][-30:]]
    assert actual["points"][0]["date"] == datetime.fromtimestamp(data["bars"][-30]["t"], NY).date().isoformat()
    assert data == original


@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, None])
def test_invalid_closes_do_not_become_a_curve(bad):
    data = chart()
    data["bars"][-1]["c"] = bad
    assert daily_trend(data, market_timezone=NY) is None


def test_rejects_duplicate_sessions_adjusted_prices_and_short_data():
    data = chart()
    data["bars"].append(data["bars"][-1].copy())
    assert daily_trend(data, market_timezone=NY) is None
    data = chart()
    data["price_adjustment"] = "adjusted"
    assert daily_trend(data, market_timezone=NY) is None
    assert daily_trend(chart(count=1), market_timezone=NY) is None


def test_does_not_append_extended_or_quote_only_points():
    data = chart(count=3)
    expected = daily_trend(data, market_timezone=NY)
    data["bars"] += [{"t": 1, "c": 999, "ext": True}, {"t": 2, "c": 999, "quote_only": True}]
    assert daily_trend(data, market_timezone=NY) == expected


def test_watchlist_response_reuses_daily_cache_and_shares_each_ticker_read(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    monkeypatch.setattr(stocks, "_load_watchlist_snapshot_once", lambda _: None)
    now = time.time()
    row = {"ticker": "NVDA", "price": 999, "spark": [1, 2, 3]}
    payload = {"groups": [{"id": "a", "stocks": [row]}, {"id": "b", "stocks": [row]}]}
    stocks._endpoint_cache["watchlist"] = stocks._EndpointCacheEntry(now + 60, now + 300, now, payload)
    stocks._endpoint_cache["chart:NVDA:1d:raw"] = stocks._EndpointCacheEntry(now + 60, now + 300, now, chart())
    reads = []

    def read(symbol, resource, **_):
        reads.append((symbol, resource))
        return None

    async def upstream_forbidden(*args, **kwargs):
        pytest.fail("watchlist trend must never fetch a chart or refresh the watchlist")

    monkeypatch.setattr(stocks, "read_stock_pull_resource", read)
    monkeypatch.setattr(stocks, "_load_stock_chart", upstream_forbidden)
    monkeypatch.setattr(stocks, "_build_watchlist", upstream_forbidden)
    with request_owner_access_context(False):
        result = asyncio.run(stocks.watchlist(None))
    assert reads == [("NVDA", "daily_chart")]
    assert len(result["groups"][0]["stocks"][0]["daily_trend"]["points"]) == 30
    assert result["groups"][1]["stocks"][0]["daily_trend"] == result["groups"][0]["stocks"][0]["daily_trend"]
    assert "daily_trend" not in row
    assert result["groups"][0]["stocks"][0]["daily_trend"]["points"][-1]["close"] != 999


def test_restart_uses_persisted_chart_and_expired_chart_has_no_trend(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    now = time.time()
    write_stock_pull_resources("NVDA", {"daily_chart": (chart(), now)}, now=now)
    payload = {"groups": [{"stocks": [{"ticker": "NVDA"}, {"ticker": "TSLA", "spark": [1, 2, 3]}]}]}
    stocks._endpoint_cache["chart:TSLA:1d:raw"] = stocks._EndpointCacheEntry(now - 20, now - 1, now - 30, chart("TSLA"))
    result = asyncio.run(stocks._with_watchlist_daily_trends(payload))
    assert len(result["groups"][0]["stocks"][0]["daily_trend"]["points"]) == 30
    assert "daily_trend" not in result["groups"][0]["stocks"][1]


def test_worker_public_daily_snapshot_is_reused_before_any_manual_pull(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(stocks, "_endpoint_cache", {})
    reads = []

    def public_read(resource, *, parameters, now):
        reads.append((resource, parameters))
        return chart() if resource == "focus_chart" and parameters["ticker"] == "NVDA" else None

    monkeypatch.setattr(stocks, "read_public_home_resource", public_read)
    result = asyncio.run(stocks._with_watchlist_daily_trends({"groups": [{"stocks": [{"ticker": "NVDA"}]}]}))
    assert len(result["groups"][0]["stocks"][0]["daily_trend"]["points"]) == 30
    assert reads == [("focus_chart", {"ticker": "NVDA", "range": "1d", "adjustment": "raw"})]
