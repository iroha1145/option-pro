from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace

from app.services import realtime_quotes as quotes

NOW = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)


def settings(tmp_path):
    return SimpleNamespace(quotes_enabled=True, quotes_public_enabled=False,
        quotes_signals_enabled=True, finnhub_api_key="test-key", quotes_max_symbols=50,
        quotes_publish_interval_ms=250, data_dir=tmp_path)


def test_incremental_broadcast_only_builds_changed_symbol_once(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        symbols = [f"S{i}" for i in range(46)]
        ids = [await hub.subscribe(symbols) for _ in range(100)]
        hub._publish_pending()  # Allocation changes intentionally get full state.
        for id in ids:
            assert len(hub._clients[id].queue.get_nowait()["data"]["quotes"]) == 46
        original = hub._quote_view
        calls = []
        def view(symbol):
            calls.append(symbol)
            return original(symbol)
        monkeypatch.setattr(hub, "_quote_view", view)
        hub._store_quote("S0", 101, NOW, NOW, "finnhub_websocket")
        hub._publish_pending()
        frames = [hub._clients[id].queue.get_nowait() for id in ids]
        assert calls == ["S0"]
        assert all([q["symbol"] for q in f["data"]["quotes"]] == ["S0"] for f in frames)
        assert all(f is frames[0] for f in frames)  # Shared immutable envelope.
        hub._publish_pending()
        assert all(hub._clients[id].queue.empty() for id in ids)
    asyncio.run(scenario())


def test_delta_delivery_respects_alias_and_unchanged_readers(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        a = await hub.subscribe(["BRK-B", "AAPL"])
        b = await hub.subscribe(["BRK.B"])
        c = await hub.subscribe(["MSFT"])
        hub._publish_pending()
        for id in (a, b, c):
            hub._clients[id].queue.get_nowait()
        hub._store_quote("BRK.B", 700, NOW, NOW, "finnhub_websocket")
        hub._publish_pending()
        assert hub._clients[a].queue.get_nowait()["data"]["quotes"][0]["symbol"] == "BRK-B"
        assert hub._clients[b].queue.get_nowait()["data"]["quotes"][0]["symbol"] == "BRK.B"
        assert hub._clients[c].queue.empty()
    asyncio.run(scenario())


def test_slow_reader_receives_complete_snapshot_after_dropped_deltas(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        hub._running = True
        id = await hub.subscribe(["AAPL", "MSFT"])
        stream = hub.events(id)
        assert (await anext(stream))["event"] == "quotes"
        hub._publish_pending()
        # One symbol's update will be dropped from the bounded queue.
        hub._store_quote("MSFT", 202, NOW, NOW, "finnhub_websocket")
        hub._publish_pending()
        for i in range(20):
            hub._store_quote("AAPL", 100 + i, NOW, NOW, "finnhub_websocket")
            hub._publish_pending()
        assert hub._clients[id].resync_required
        status = await anext(stream)
        assert status["data"]["resync_required"]
        full = await anext(stream)
        assert {q["symbol"]: q["price"] for q in full["data"]["quotes"]} == {"AAPL": 119, "MSFT": 202}
        assert hub._clients[id].queue.empty()
        await stream.aclose()
    asyncio.run(scenario())


def test_large_frames_yield_to_other_tasks_without_dropping_or_reordering_trades(tmp_path):
    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path))
        seen, checkpoints = [], []
        async def handler(trade):
            seen.append(trade["sequence"])
        async def observer():
            checkpoints.append(len(seen))
        hub._process_trade = handler
        other = asyncio.create_task(observer())
        await hub._process_message(json.dumps({"type": "trade", "data": [{"sequence": i} for i in range(1000)]}))
        await other
        assert seen == list(range(1000))
        assert 0 < checkpoints[0] < 1000
    asyncio.run(scenario())
