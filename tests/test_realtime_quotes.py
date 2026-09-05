from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import httpx
import pytest

from app.services import realtime_quotes as quotes


NOW = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)


def settings(tmp_path, **overrides):
    return SimpleNamespace(**{
        "data_dir": tmp_path,
        "quotes_enabled": True,
        "quotes_public_enabled": False,
        "quotes_signals_enabled": True,
        "quotes_max_symbols": 50,
        "quotes_publish_interval_ms": 250,
        "quotes_release_seconds": 30,
        "finnhub_api_key": "test-key",
        "finnhub_base_url": "https://finnhub.io/api/v1",
        **overrides,
    })


def trade(symbol="AAPL", price=101.0, at=NOW, **extra):
    return {"s": symbol, "p": price, "t": int(at.timestamp() * 1000), "v": 100, "c": ["1"], **extra}


def test_priority_dedup_capacity_and_releasing_page_symbols(tmp_path):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        hub._radar_symbols = [f"R{i}" for i in range(30)]
        first = await hub.subscribe(["AAPL", "MSFT", *[f"P{i}" for i in range(25)]])
        second = await hub.subscribe(["AAPL", "NVDA"], focus=["NVDA"])
        assert len(hub._desired_symbols) == 50
        assert hub._desired_symbols[:4] == list(quotes.TOP_SYMBOLS)
        assert hub._desired_symbols[4:34] == hub._radar_symbols
        assert hub._desired_symbols[34] == "NVDA"
        assert hub._desired_symbols.count("AAPL") == 1
        assert hub._desired_symbols[35:37] == ["AAPL", "MSFT"]
        snapshot = await hub.snapshot(["P24", "AAPL"])
        assert snapshot["quotes"][0]["subscription_status"] == "limited"
        assert snapshot["quotes"][1]["subscription_status"] == "pending"
        hub.unsubscribe(first)
        assert "MSFT" not in hub._desired_symbols
        assert "AAPL" in hub._desired_symbols
        hub._clients[second].last_seen -= 31
        hub._expire_clients()
        assert hub._desired_symbols == [*quotes.TOP_SYMBOLS, *hub._radar_symbols]

    asyncio.run(scenario())


def test_share_class_aliases_share_one_slot_and_keep_requested_browser_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path, quotes_max_symbols=6))
        client_id = await hub.subscribe(["BRK-B", "BRK.B", "AAPL"])
        assert hub._desired_symbols == [*quotes.TOP_SYMBOLS, "BRK.B", "AAPL"]
        hub._connected = True
        hub._sent_symbols = set(hub._desired_symbols)
        assert (await hub.snapshot(["BRK-B"]))["quotes"][0]["subscription_status"] == "pending"
        hub._running = True
        publisher = asyncio.create_task(hub._publisher())
        try:
            await hub._process_trade(trade(symbol="BRK.B", price=450))
            pushed = await asyncio.wait_for(hub._clients[client_id].queue.get(), timeout=1)
            rows = pushed["data"]["quotes"]
            assert [(row["symbol"], row["price"]) for row in rows[:2]] == [("BRK-B", 450), ("BRK.B", 450)]
            assert all(row["subscription_status"] == "live" for row in rows[:2])
            assert list(hub._quotes) == ["BRK.B"]
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
            hub._running = False

    asyncio.run(scenario())


def test_provider_empty_quote_is_unavailable_until_valid_trade_arrives(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        requests = []

        async def reserve(*args, **kwargs):
            return True

        def transport(request):
            requests.append(request)
            return httpx.Response(200, json={"c": 0, "pc": 0, "t": 0})

        monkeypatch.setattr(quotes, "async_reserve_finnhub_request", reserve)
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["BRK-B"])
        hub._connected = True
        hub._sent_symbols = set(hub._desired_symbols)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            hub._http = client
            await hub._warm_symbol("BRK-B")
        assert requests[0].url.params["symbol"] == "BRK.B"
        view = (await hub.snapshot(["BRK-B"]))["quotes"][0]
        assert view["symbol"] == "BRK-B"
        assert view["subscription_status"] == "unavailable"
        assert view["freshness"] == "missing"
        assert view["subscription_reason"] == "provider_quote_unavailable"
        await hub._process_trade(trade(symbol="BRK.B", price=450))
        restored = (await hub.snapshot(["BRK-B"]))["quotes"][0]
        assert restored["price"] == 450
        assert restored["subscription_status"] == "live"
        assert "subscription_reason" not in restored

    asyncio.run(scenario())


def test_top_and_radar_preempt_page_subscriptions_and_unsubscribe_first(tmp_path):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path, quotes_max_symbols=6))
        await hub.subscribe(["AAPL", "MSFT"])
        hub._sent_symbols = set(hub._desired_symbols)
        hub._radar_symbols = ["NVDA"]
        hub._allocate()
        sent = []

        class Socket:
            async def send(self, text):
                message = json.loads(text)
                sent.append(message)
                if message["type"] == "subscribe":
                    hub._running = False

        hub._running = True
        await hub._send_subscriptions(Socket())
        assert sent == [{"type": "unsubscribe", "symbol": "MSFT"}, {"type": "subscribe", "symbol": "NVDA"}]
        assert len(hub._sent_symbols) == 6

    asyncio.run(scenario())


def test_same_frame_cross_and_retreat_reach_radar_once_each(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        seen = []

        async def handler(event):
            seen.append(event)
            return {"symbol": event["symbol"], "state": "triggered"} if event["price"] > 100 else None

        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        client_id = await hub.subscribe(["AAPL"])
        first = trade(price=101)
        second = trade(price=99, v=200)
        await hub._process_message(json.dumps({"type": "trade", "data": [first, second, second]}))
        assert [event["price"] for event in seen] == [101, 99]
        assert (await hub.snapshot(["AAPL"]))["quotes"][0]["price"] == 99
        event = hub._clients[client_id].queue.get_nowait()
        assert event == {"event": "radar", "data": {"events": [{"symbol": "AAPL", "state": "triggered"}]}}
        assert hub._clients[client_id].queue.empty()
        assert "volume" in seen[0] and "candles" not in seen[0]

    asyncio.run(scenario())


def test_invalid_conditions_stale_replay_and_future_ticks_do_not_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        seen = []

        async def handler(event):
            seen.append(event)

        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        await hub.subscribe(["AAPL"])
        await hub._process_trade(trade(price=100))
        for invalid in [
            trade(price=-1), trade(price=float("nan")), trade(at=NOW + timedelta(minutes=1)),
            trade(at=NOW - timedelta(seconds=1)), trade(c=["3"]), trade(c=["12"]),
            trade(c=["25"]), trade(c=["39"]), trade(c=["unknown"]), trade(c="1"), trade(v=-1),
            trade(c=False), trade(c=""), trade(v=True),
            trade(symbol="UNSUBSCRIBED"),
        ]:
            await hub._process_trade(invalid)
        assert len(seen) == 1
        assert hub._quotes["AAPL"]["price"] == 100
        # Delayed frames may populate a missing quote but are not evidence of
        # a newly observed realtime breakout.
        await hub.subscribe(["MSFT"])
        await hub._process_trade(trade(symbol="MSFT", at=NOW - timedelta(minutes=2)))
        assert hub._quotes["MSFT"]["price"] == 101
        assert len(seen) == 1

    asyncio.run(scenario())


def test_zero_volume_and_unknown_conditions_are_quotes_without_formal_signals(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        seen = []

        async def handler(event):
            seen.append(event)

        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        await hub.subscribe(["AAPL"])
        for index, extra in enumerate(({"v": 0}, {"c": []}, {"c": None})):
            await hub._process_trade(trade(price=101 + index, **extra))
        missing = trade(price=104)
        missing.pop("v")
        missing.pop("c")
        await hub._process_trade(missing)
        assert hub._quotes["AAPL"]["price"] == 104
        assert not seen

    asyncio.run(scenario())


def test_radar_write_error_preserves_quote_and_requests_durable_resync(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        async def handler(event):
            raise RuntimeError("private database error")

        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        client_id = await hub.subscribe(["AAPL"])
        await hub._process_trade(trade(price=111))
        snapshot = await hub.snapshot(["AAPL"])
        assert snapshot["quotes"][0]["price"] == 111
        assert snapshot["status"]["signals_resync_required"]
        assert snapshot["status"]["last_error"] == "radar_trade_failed"
        assert hub._clients[client_id].resync_required
        assert hub._radar_refresh.is_set()
        assert "private database error" not in json.dumps(snapshot)

    asyncio.run(scenario())


def test_missing_prior_trading_day_baseline_is_unknown_not_an_old_change(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._apply_rest_quote("AAPL", {"c": 100, "pc": 95, "t": int((NOW - timedelta(days=3)).timestamp())})
        await hub._process_trade(trade(price=110))
        view = (await hub.snapshot(["AAPL"]))["quotes"][0]
        assert view["price"] == 110
        assert view["previous_close"] is None
        assert view["change_pct"] is None

    asyncio.run(scenario())


def test_rest_baseline_cannot_rewind_live_price_or_newer_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        await hub._process_trade(trade(price=110))
        hub._apply_rest_quote("AAPL", {"c": 108, "pc": 100, "t": int((NOW - timedelta(seconds=30)).timestamp())})
        view = (await hub.snapshot(["AAPL"]))["quotes"][0]
        assert (view["price"], view["previous_close"], view["change_pct"]) == (110, 100, 10)
        assert view["source"] == "finnhub_websocket"
        hub._apply_rest_quote("AAPL", {"c": 70, "pc": 65, "t": int((NOW - timedelta(days=1)).timestamp())})
        assert (await hub.snapshot(["AAPL"]))["quotes"][0]["previous_close"] == 100
        # Same-second snapshots also cannot overwrite a live observation.
        hub._apply_rest_quote("AAPL", {"c": 105, "pc": 100, "t": int(NOW.timestamp())})
        assert hub._quotes["AAPL"]["price"] == 110

    asyncio.run(scenario())


def test_premarket_compares_with_last_regular_close_and_form_t_is_labeled(tmp_path, monkeypatch):
    now = NOW.replace(hour=12)
    monkeypatch.setattr(quotes, "_utcnow", lambda: now)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._apply_rest_quote("AAPL", {"c": 100, "pc": 95, "t": int((NOW - timedelta(days=1)).timestamp())})
        await hub._process_trade(trade(price=102, at=now, c=["24"]))
        view = (await hub.snapshot(["AAPL"]))["quotes"][0]
        assert view["session"] == "premarket"
        assert view["previous_close"] == 100
        assert view["change_pct"] == 2
        assert quotes.market_session(datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)) == "postmarket"
        assert quotes.market_session(datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)) == "closed"

    asyncio.run(scenario())


def test_feature_flags_missing_key_and_readonly_snapshot_do_not_subscribe(tmp_path):
    async def scenario():
        disabled = quotes.QuoteHub(settings(tmp_path, quotes_enabled=False, quotes_signals_enabled=False))
        assert (await disabled.snapshot(["AAPL"]))["quotes"][0]["subscription_status"] == "disabled"
        missing = quotes.QuoteHub(settings(tmp_path, finnhub_api_key=""))
        snapshot = await missing.snapshot(["AAPL"])
        assert snapshot["status"]["configured"] is False
        assert snapshot["quotes"][0]["subscription_status"] == "unconfigured"
        assert snapshot["quotes"][0]["price"] is None
        assert not missing._desired_symbols
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.snapshot(["AAPL"])
        assert "AAPL" not in hub._desired_symbols
        assert not hub._quotes

    asyncio.run(scenario())


def test_same_key_process_lock_survives_handoff_without_double_owner(tmp_path):
    first, second = quotes.QuoteHub(settings(tmp_path)), quotes.QuoteHub(settings(tmp_path))
    assert first._try_lock()
    try:
        assert not second._try_lock()
        first._release_lock()
        assert second._try_lock()
        assert not first._try_lock()
    finally:
        first._release_lock()
        second._release_lock()
    assert "test-key" not in str(first._lock_path)


def test_client_and_cache_bounds_and_slow_reader_recovery(tmp_path, monkeypatch):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        with pytest.raises(ValueError):
            await hub.subscribe([f"S{i}" for i in range(201)])
        with pytest.raises(ValueError):
            await hub.subscribe(["AAPL"], focus=["MSFT"])
        with pytest.raises(ValueError):
            await hub.subscribe(["BINANCE:BTCUSDT"])
        client_id = await hub.subscribe(["AAPL"])
        client = hub._clients[client_id]
        for _ in range(20):
            hub._emit(client, {"event": "status", "data": {}})
        assert client.queue.qsize() == 8
        assert client.resync_required
        monkeypatch.setattr(quotes, "MAX_CLIENTS", 1)
        with pytest.raises(ValueError):
            await hub.subscribe(["MSFT"])
        monkeypatch.setattr(quotes, "MAX_CACHED_QUOTES", 2)
        for symbol in ("ZZZ", "YYY", "AAPL"):
            hub._store_quote(symbol, 10, NOW, NOW, "finnhub_rest")
        assert len(hub._quotes) == 2
        assert "AAPL" in hub._quotes

    asyncio.run(scenario())


def test_rest_warming_uses_budget_and_429_keeps_last_price(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        calls = []
        reservations = []
        cooldowns = []

        async def reserve(key, **kwargs):
            reservations.append(key)
            return len(reservations) <= 2

        def transport(request):
            calls.append(request)
            assert "token" not in str(request.url)
            assert request.headers["X-Finnhub-Token"] == "test-key"
            return httpx.Response(429, headers={"Retry-After": "90"})

        monkeypatch.setattr(quotes, "async_reserve_finnhub_request", reserve)
        monkeypatch.setattr(quotes, "mark_finnhub_rate_limited", lambda key, **kwargs: cooldowns.append(kwargs["retry_after"]))
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._apply_rest_quote("AAPL", {"c": 100, "pc": 95, "t": int(NOW.timestamp())})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            hub._http = client
            await hub._warm_symbol("NOT_ADMITTED")
            await hub._warm_symbol("AAPL")
            await hub._warm_symbol("AAPL")
            await hub._warm_symbol("AAPL")
        assert len(calls) == 2
        assert cooldowns == ["90", "90"]
        assert hub._quotes["AAPL"]["price"] == 100
        assert hub._last_error == "rest_rate_limited"

    asyncio.run(scenario())


def test_inflight_rest_response_does_not_cache_evicted_subscription(tmp_path, monkeypatch):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        client_id = await hub.subscribe(["AAPL"])

        async def reserve(*args, **kwargs):
            return True

        async def transport(request):
            hub.unsubscribe(client_id)
            return httpx.Response(200, json={"c": 100, "pc": 95, "t": int(NOW.timestamp())})

        monkeypatch.setattr(quotes, "async_reserve_finnhub_request", reserve)
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            hub._http = client
            await hub._warm_symbol("AAPL")
        assert "AAPL" not in hub._quotes
        assert "AAPL" not in hub._baselines

    asyncio.run(scenario())


def test_slow_radar_inventory_does_not_delay_client_expiry(tmp_path):
    async def scenario():
        entered = asyncio.Event()

        async def loader():
            entered.set()
            await asyncio.Future()

        hub = quotes.QuoteHub(settings(tmp_path), radar_loader=loader)
        client_id = await hub.subscribe(["AAPL"])
        hub._running = True
        hub._clients[client_id].last_seen -= 31
        radar = asyncio.create_task(hub._radar_loop())
        housekeeping = asyncio.create_task(hub._housekeeping())
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            assert client_id not in hub._clients
            assert not radar.done()
        finally:
            radar.cancel()
            housekeeping.cancel()
            await asyncio.gather(radar, housekeeping, return_exceptions=True)

    asyncio.run(scenario())


def test_quote_becomes_stale_without_another_trade(tmp_path, monkeypatch):
    now = [NOW]
    monkeypatch.setattr(quotes, "_utcnow", lambda: now[0])

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        await hub.subscribe(["AAPL"])
        hub._connected = True
        hub._sent_symbols = {"AAPL"}
        hub._running = True
        await hub._process_trade(trade())
        housekeeping = asyncio.create_task(hub._housekeeping())
        try:
            await asyncio.sleep(0)
            assert hub._freshness["AAPL"] == "live"
            hub._dirty_symbols.clear()
            now[0] += timedelta(seconds=61)
            await asyncio.sleep(1.05)
            assert hub._freshness["AAPL"] == "stale"
            assert "AAPL" in hub._dirty_symbols
        finally:
            housekeeping.cancel()
            await asyncio.gather(housekeeping, return_exceptions=True)

    asyncio.run(scenario())


def test_browser_coalescing_and_stream_cleanup(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        hub._running = True
        client_id = await hub.subscribe(["AAPL"])
        iterator = hub.events(client_id)
        initial = await anext(iterator)
        assert initial["event"] == "quotes"
        assert initial["data"]["quotes"][0]["price"] is None
        publisher = asyncio.create_task(hub._publisher())
        try:
            await hub._process_trade(trade(price=101))
            await hub._process_trade(trade(price=99, v=200))
            event = await asyncio.wait_for(anext(iterator), timeout=1)
            assert event["event"] == "quotes"
            assert event["data"]["quotes"][0]["price"] == 99
            assert hub._clients[client_id].queue.empty()
        finally:
            publisher.cancel()
            await asyncio.gather(publisher, return_exceptions=True)
            await iterator.aclose()
        assert client_id not in hub._clients
        assert "AAPL" not in hub._desired_symbols

    asyncio.run(scenario())


def test_disconnect_retains_prices_reconnects_and_releases_lock(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)

    async def scenario():
        sockets = []
        second_subscribed = asyncio.Event()

        class Socket:
            def __init__(self, number):
                self.number = number
                self.messages = []
                self.closed = False
                self.subscribed = asyncio.Event()

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                self.closed = True

            async def send(self, text):
                self.messages.append(json.loads(text))
                if self.messages[-1] == {"type": "subscribe", "symbol": "AAPL"}:
                    self.subscribed.set()
                    if self.number == 2:
                        second_subscribed.set()

            def __aiter__(self):
                return self.frames()

            async def frames(self):
                await self.subscribed.wait()
                if self.number == 1:
                    yield json.dumps({"type": "trade", "data": [trade(price=111)]})
                    raise ConnectionError("sensitive upstream token=test-key")
                await asyncio.Future()

        def connect(*args, **kwargs):
            socket = Socket(len(sockets) + 1)
            sockets.append(socket)
            return socket

        async def no_rest():
            await asyncio.Future()

        monkeypatch.setattr(quotes, "connect", connect)
        hub = quotes.QuoteHub(settings(tmp_path))
        hub._warm_loop = no_rest
        await hub.subscribe(["AAPL"])
        await hub.start()
        try:
            await asyncio.wait_for(second_subscribed.wait(), timeout=3)
            view = await hub.snapshot(["AAPL"])
            assert view["quotes"][0]["price"] == 111
            assert view["status"]["connected"]
            assert view["status"]["reconnect_count"] == 1
            assert "test-key" not in json.dumps(view)
            assert sockets[0].closed
            assert len(sockets) == 2
            assert {message["symbol"] for message in sockets[1].messages} == {*quotes.TOP_SYMBOLS, "AAPL"}
            contender = quotes.QuoteHub(settings(tmp_path))
            assert not contender._try_lock()
        finally:
            await hub.close()
        assert sockets[1].closed
        assert not hub._tasks
        assert contender._try_lock()
        contender._release_lock()

    asyncio.run(scenario())
