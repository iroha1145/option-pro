from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import pytest
from fastapi import HTTPException

from app.api import stocks
from app.services import realtime_quotes as quotes
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository
from tests.test_realtime_quotes import NOW, settings, trade
from tests.test_realtime_review_regressions import AT, event, publish


def test_invalid_symbol_error_quarantines_unknowns_without_dropping_the_stream(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL", "DELISTD"])
        hub._apply_rest_quote("AAPL", {
            "c": 100, "pc": 95,
            "t": int(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc).timestamp()),
        })
        hub._sent_symbols = set(hub._desired_symbols)
        await hub._process_message('{"type":"error","msg":"Invalid symbol"}')
        assert "DELISTD" in hub._provider_unavailable
        assert "DELISTD" not in hub._desired_symbols
        assert "AAPL" in hub._desired_symbols
        assert hub._status()["last_error"] is None
        with pytest.raises(RuntimeError):
            await hub._process_message('{"type":"error","msg":"Invalid API key"}')

    asyncio.run(scenario())


def test_rest_warmup_backs_off_and_skips_unavailable_symbols(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)
    hub = quotes.QuoteHub(settings(tmp_path))
    hub._provider_unavailable.add("DELISTD")
    hub._desired_symbols = ["AAPL", "DELISTD"]
    hub._note_rest_failure("AAPL")
    assert hub._rest_backoff["AAPL"] == quotes.REST_MIN_BACKOFF
    hub._note_rest_failure("AAPL")
    assert hub._rest_backoff["AAPL"] == quotes.REST_MIN_BACKOFF * 2
    assert "DELISTD" not in hub._rest_backoff


def test_after_hours_rest_close_is_not_next_day_previous_close(tmp_path, monkeypatch):
    now = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, "_utcnow", lambda: now)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._apply_rest_quote("AAPL", {
            "c": 110, "pc": 100,
            "t": int(datetime(2026, 9, 3, 23, 59, tzinfo=timezone.utc).timestamp()),
        })
        await hub._process_trade(trade(price=105, at=now))
        view = (await hub.snapshot(["AAPL"]))["quotes"][0]
        assert view["previous_close"] == 100
        assert view["change_pct"] == 5

    asyncio.run(scenario())


def test_regular_close_print_is_regular_session():
    assert quotes.market_session(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)) == "regular"
    assert quotes.market_session(datetime(2026, 9, 3, 20, 0, 1, tzinfo=timezone.utc)) == "postmarket"
    assert quotes._is_official_close_print(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc))


def test_newer_rest_print_cannot_replace_websocket_last(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        await hub._process_trade(trade(price=110))
        hub._apply_rest_quote("AAPL", {
            "c": 200, "pc": 100, "t": int((NOW + timedelta(seconds=5)).timestamp()),
        })
        assert hub._quotes["AAPL"]["price"] == 110
        assert hub._quotes["AAPL"]["source"] == "finnhub_websocket"

    asyncio.run(scenario())


def test_trade_fault_clears_when_symbol_leaves_desired_and_radar(tmp_path):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        client = await hub.subscribe(["AAPL"])
        hub._set_error("trade:AAPL", "radar_trade_failed")
        hub.unsubscribe(client)
        assert hub._last_error is None

    asyncio.run(scenario())


def test_owner_slots_are_reserved_only_when_capacity_is_wide(tmp_path, monkeypatch):
    async def scenario():
        monkeypatch.setattr(quotes, "MAX_CLIENTS", 1)
        tight = quotes.QuoteHub(settings(tmp_path))
        await tight.subscribe(["AAPL"])
        with pytest.raises(ValueError):
            await tight.subscribe(["MSFT"])

        monkeypatch.setattr(quotes, "MAX_CLIENTS", 10)
        wide = quotes.QuoteHub(settings(tmp_path))
        await wide.subscribe(["AAPL"])
        await wide.subscribe(["MSFT"])
        with pytest.raises(ValueError):
            await wide.subscribe(["NVDA"])
        owner = await wide.subscribe(["TSLA"], owner=True)
        assert owner

    asyncio.run(scenario())


def test_radar_symbols_skips_reload_when_revision_is_unchanged(tmp_path):
    settings_obj = BreakoutSettings(
        _env_file=None, BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled",
    )
    repo = BreakoutRepository(settings_obj.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    publish(repo, AT, [event()])
    adapter = BreakoutRealtimeAdapter(settings_obj, repo, now=lambda: AT + timedelta(seconds=20))
    loads = []
    original = adapter._load_events

    def load():
        loads.append(1)
        return original()

    adapter._load_events = load

    async def run():
        first = await adapter.radar_symbols()
        second = await adapter.radar_symbols()
        assert first == second == ["AAPL"]
        assert len(loads) == 1
        assert repo.inventory_revision()[0]

    asyncio.run(run())


def test_chart_and_daily_pull_budgets_are_independent():
    stocks._public_stock_pull_recent.clear()
    stocks._public_chart_pull_recent.clear()
    stocks._public_stock_pull_ticker_deadlines.clear()
    for index in range(24):
        stocks._reserve_public_stock_pull(
            "acct:chart", f"S{index}", resource_key=f"chart:S{index}:5m:raw", bucket="chart",
        )
    with pytest.raises(HTTPException) as chart_blocked:
        stocks._reserve_public_stock_pull(
            "acct:chart", "ZZZ", resource_key="chart:ZZZ:5m:raw", bucket="chart",
        )
    assert chart_blocked.value.detail["code"] == "stock_pull_rate_limited"
    assert "行情获取过于频繁" in chart_blocked.value.detail["message"]
    for index in range(6):
        stocks._reserve_public_stock_pull("acct:daily", f"D{index}")
    with pytest.raises(HTTPException) as daily_blocked:
        stocks._reserve_public_stock_pull("acct:daily", "D6")
    assert daily_blocked.value.detail["code"] == "stock_pull_rate_limited"
    assert "手动拉取过于频繁" in daily_blocked.value.detail["message"]
    stocks._reserve_public_stock_pull(
        "acct:chart", "KEEP", resource_key="chart:KEEP:5m:raw", bucket="chart",
        skip_client_budget=True,
    )
