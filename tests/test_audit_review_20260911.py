from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import HTTPException

from app.api import stocks
from app.services import realtime_quotes as quotes
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository
from tests.test_realtime_quotes import NOW, settings, trade
from tests.test_realtime_review_regressions import AT, event, publish


def test_unnamed_invalid_symbol_does_not_drop_unwarmed_codes(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL", "DELISTD"])
        hub._sent_symbols = set(hub._desired_symbols)
        before = list(hub._desired_symbols)
        await hub._process_message('{"type":"error","msg":"Invalid symbol"}')
        assert hub._desired_symbols == before
        assert hub._provider_unavailable == set()
        hub._apply_rest_quote("DELISTD", {"c": 0, "pc": 0, "t": 0})
        assert "DELISTD" in hub._provider_unavailable
        assert "DELISTD" not in hub._desired_symbols
        assert "AAPL" in hub._desired_symbols
        await hub._process_message('{"type":"error","msg":"Invalid symbol: FAKE.X"}')
        assert "FAKE.X" not in hub._provider_unavailable
        await hub._process_message('{"type":"error","msg":"Invalid symbol: AAPL"}')
        assert "AAPL" in hub._provider_unavailable
        assert hub._status()["last_error"] is None
        with pytest.raises(RuntimeError):
            await hub._process_message('{"type":"error","msg":"Invalid API key"}')

    asyncio.run(scenario())


def test_unavailable_backoff_survives_trim_while_still_demanded(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL", "DELISTD"])
        hub._apply_rest_quote("DELISTD", {"c": 0, "pc": 0, "t": 0})
        hub._rest_attempts["DELISTD"] = 1.0
        hub._note_rest_failure("DELISTD")
        assert "DELISTD" in hub._provider_unavailable
        assert "DELISTD" in hub._demanded_symbols
        assert "DELISTD" not in hub._desired_symbols
        hub._trim_cache()
        hub._radar_symbols = ["DELISTD"]
        hub._allocate()
        assert "DELISTD" in hub._provider_unavailable
        assert hub._rest_backoff["DELISTD"] == quotes.REST_MIN_BACKOFF
        assert hub._rest_attempts["DELISTD"] == 1.0
        assert "DELISTD" not in hub._desired_symbols

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
        assert view["previous_close"] is None
        assert view["change_pct"] is None

    asyncio.run(scenario())


def test_regular_close_print_is_regular_session():
    assert quotes.market_session(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)) == "regular"
    assert quotes.market_session(datetime(2026, 9, 3, 20, 0, 1, tzinfo=timezone.utc)) == "postmarket"
    assert quotes._is_official_close_print(datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc))
    assert not quotes._is_official_close_print(datetime(2026, 9, 3, 19, 58, tzinfo=timezone.utc))
    assert not quotes._is_official_close_print(datetime(2026, 9, 3, 20, 0, 45, tzinfo=timezone.utc))


def test_near_close_and_stale_official_prints_do_not_invent_change(tmp_path, monkeypatch):
    now = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)
    monkeypatch.setattr(quotes, "_utcnow", lambda: now)

    async def scenario():
        early = quotes.QuoteHub(settings(tmp_path))
        await early.subscribe(["AAPL"])
        early._apply_rest_quote("AAPL", {
            "c": 110, "pc": 100,
            "t": int(datetime(2026, 9, 3, 19, 58, tzinfo=timezone.utc).timestamp()),
        })
        await early._process_trade(trade(price=105, at=now))
        early_view = (await early.snapshot(["AAPL"]))["quotes"][0]
        assert early_view["previous_close"] is None
        assert early_view["change_pct"] is None

        stale = quotes.QuoteHub(settings(tmp_path))
        await stale.subscribe(["AAPL"])
        stale._baselines["AAPL"] = {
            "close": 100, "previous_close": 95, "official_close": 100,
            "official_close_day": datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc).date(),
            "trade_day": datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc).date(),
            "trade_time": datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc),
            "fetched_day": datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc).date(),
            "needs_refresh": False,
        }
        await stale._process_trade(trade(price=105, at=now))
        stale_view = (await stale.snapshot(["AAPL"]))["quotes"][0]
        assert stale_view["previous_close"] is None
        assert stale_view["change_pct"] is None

    asyncio.run(scenario())


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


def test_radar_publish_during_first_read_does_not_pin_stale_inventory(tmp_path):
    settings_obj = BreakoutSettings(
        _env_file=None, BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled",
    )
    repo = BreakoutRepository(settings_obj.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    publish(repo, AT, [event()])
    adapter = BreakoutRealtimeAdapter(settings_obj, repo, now=lambda: AT + timedelta(seconds=20))
    original = adapter._load_events
    published = []

    def load():
        rows = original()
        if not published:
            published.append(True)
            publish(repo, AT + timedelta(seconds=1), [event(), event(symbol="MSFT")])
        return rows

    adapter._load_events = load

    async def run():
        first = await adapter.radar_symbols()
        second = await adapter.radar_symbols()
        assert "MSFT" in first or "MSFT" in second
        assert set(second) == {"AAPL", "MSFT"}
        assert adapter._inventory_revision == repo.inventory_revision()

    asyncio.run(run())


def test_radar_publish_during_recovery_read_does_not_pin_stale_inventory(tmp_path):
    settings_obj = BreakoutSettings(
        _env_file=None, BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled",
    )
    repo = BreakoutRepository(settings_obj.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    publish(repo, AT, [event()])
    adapter = BreakoutRealtimeAdapter(settings_obj, repo, now=lambda: AT + timedelta(seconds=20))

    async def run():
        assert await adapter.radar_symbols() == ["AAPL"]
        adapter._inventory_failures = 1
        adapter._inventory_retry_at = 0.0
        original = adapter._load_events
        published = []

        def load():
            rows = original()
            if not published:
                published.append(True)
                publish(repo, AT + timedelta(seconds=1), [event(), event(symbol="MSFT")])
            return rows

        adapter._load_events = load
        async with adapter._serial:
            recovered = await adapter._refresh_inventory_locked()
        later = await adapter.radar_symbols()
        assert "MSFT" in recovered or "MSFT" in later
        assert set(later) == {"AAPL", "MSFT"}
        assert adapter._inventory_revision == repo.inventory_revision()
        assert adapter._inventory_failures == 0

    asyncio.run(run())


def test_waiting_poll_does_not_use_another_read_revision(tmp_path):
    settings_obj = BreakoutSettings(
        _env_file=None, BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled",
    )
    repo = BreakoutRepository(settings_obj.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    publish(repo, AT, [event(), event(symbol="MSFT")])
    adapter = BreakoutRealtimeAdapter(settings_obj, repo, now=lambda: AT + timedelta(seconds=20))

    async def run():
        holding = asyncio.Event()
        proceed = asyncio.Event()
        poll_loaded = asyncio.Event()
        original_shared = adapter._load_events_shared

        async def load_shared(*, force: bool = False):
            page = await original_shared(force=force)
            if not poll_loaded.is_set():
                poll_loaded.set()
            return page

        adapter._load_events_shared = load_shared

        async def recover():
            async with adapter._serial:
                holding.set()
                await proceed.wait()
                publish(repo, AT + timedelta(seconds=1), [
                    event(), event(symbol="MSFT"), event(symbol="NVDA"),
                ])
                return await adapter._refresh_inventory_locked()

        recover_task = asyncio.create_task(recover())
        await holding.wait()
        poll_task = asyncio.create_task(adapter.radar_symbols())
        await poll_loaded.wait()
        proceed.set()
        recovered = await recover_task
        polled = await poll_task
        later = await adapter.radar_symbols()
        assert "NVDA" in recovered
        assert "NVDA" in polled
        assert set(later) == {"AAPL", "MSFT", "NVDA"}
        assert adapter._inventory_revision == repo.inventory_revision()

    asyncio.run(run())


@pytest.mark.parametrize("now,payload", [
    (
        datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc),
        {"c": 110, "pc": 0, "t": int(datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc).timestamp())},
    ),
    (
        datetime(2026, 9, 3, 23, 59, tzinfo=timezone.utc),
        {"c": 110, "pc": 100, "t": int(datetime(2026, 9, 3, 23, 59, tzinfo=timezone.utc).timestamp())},
    ),
])
def test_unresolved_baseline_warmup_does_not_repeat_every_minute(tmp_path, monkeypatch, now, payload):
    clock = [0.0]
    observed = [now]
    monkeypatch.setattr(quotes, "_utcnow", lambda: observed[0])
    monkeypatch.setattr(quotes.time, "monotonic", lambda: clock[0])

    async def scenario():
        calls = []

        async def reserve(*args, **kwargs):
            return True

        def transport(request):
            calls.append(str(request.url.params.get("symbol")))
            return httpx.Response(200, json=payload)

        monkeypatch.setattr(quotes, "async_reserve_finnhub_request", reserve)
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._desired_symbols = ["AAPL"]
        hub._demanded_symbols = ["AAPL"]
        hub._lock_file = object()
        hub._running = True

        async def advance(seconds):
            clock[0] += seconds
            observed[0] += timedelta(seconds=seconds)
            if clock[0] >= 600:
                hub._running = False

        monkeypatch.setattr(quotes.asyncio, "sleep", advance)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            hub._http = client
            await hub._warm_loop()
        assert 1 <= calls.count("AAPL") <= 4
        if payload["pc"] == 0:
            assert hub._baselines["AAPL"]["needs_refresh"] is True
            assert hub._rest_backoff["AAPL"] >= quotes.REST_MIN_BACKOFF
        else:
            assert hub._baselines["AAPL"].get("official_close") is None
            assert "AAPL" not in hub._rest_backoff or not hub._quote_warmup_needed("AAPL")

    asyncio.run(scenario())


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
