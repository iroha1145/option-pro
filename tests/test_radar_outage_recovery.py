"""Fault isolation, retry sharing and cancellation regression tests (offline)."""
from __future__ import annotations

import asyncio
import threading

import pytest

from app.services import realtime_quotes as quotes
from app.services.breakouts.realtime import RadarInventoryUnavailable
from test_pr135_review_regressions import NOW, bootstrap, fixture, settings, tick


def test_overlapping_sources_notify_each_affected_client_once(tmp_path, monkeypatch):
    hub, _, _, clock, _, _ = fixture(tmp_path, monkeypatch)

    async def run():
        aapl = await bootstrap(hub)
        msft = await hub.subscribe(["MSFT"])
        await hub._process_trade(tick(clock))
        assert hub._clients[aapl].resync_required
        hub._clients[aapl].resync_required = False

        async def broken():
            raise OSError("private path")

        hub._radar_event_loader = broken
        await hub._poll_radar_events()
        assert not hub._clients[aapl].resync_required
        assert hub._clients[msft].resync_required
        hub._clients[msft].resync_required = False
        for _ in range(8):
            await hub._poll_radar_events()
            await hub._process_trade(tick(clock))
        assert not any(c.resync_required for c in hub._clients.values())
        assert "private path" not in str(hub._status())

    asyncio.run(run())


def test_success_for_another_symbol_cannot_clear_failed_write(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(quotes, "_utcnow", lambda: clock[0])
    recovered = [False]

    async def handler(trade):
        if trade["symbol"] == "AAPL" and not recovered[0]:
            raise OSError("AAPL write failed")
        return [{"event_id": trade["symbol"], "symbol": trade["symbol"], "state_version": 1}]

    async def run():
        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        client = hub._clients[await hub.subscribe(["AAPL", "MSFT"])]
        await hub._process_trade(tick(clock))
        client.resync_required = False
        await hub._process_trade(tick(clock, "MSFT"))
        assert hub._signals_resync_required
        assert hub._status()["last_error"] == "radar_trade_failed"
        await hub._process_trade(tick(clock))
        assert not client.resync_required
        recovered[0] = True
        await hub._process_trade(tick(clock))
        assert not hub._signals_resync_required
        assert hub._status()["last_error"] is None
        # A genuinely new episode after a verified recovery is announced again.
        recovered[0] = False
        await hub._process_trade(tick(clock))
        assert client.resync_required

    asyncio.run(run())


def test_partial_alias_commit_is_delivered_even_if_later_alias_fails(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(quotes, "_utcnow", lambda: clock[0])

    async def handler(trade):
        if trade["symbol"] == "BRK.B":
            raise OSError("second alias failed")
        return [{"event_id": "first", "symbol": "BRK-B", "state_version": 1}]

    async def run():
        hub = quotes.QuoteHub(settings(tmp_path), trade_handler=handler)
        client = hub._clients[await hub.subscribe(["BRK-B", "BRK.B"])]
        assert hub._desired_symbols.count("BRK.B") == 1
        await hub._process_trade(tick(clock, "BRK.B"))
        assert hub._radar_events["first"]["state_version"] == 1
        assert client.resync_required
        client.resync_required = False
        await hub._process_trade(tick(clock, "BRK.B"))
        assert not client.resync_required
        assert hub._signals_resync_required

    asyncio.run(run())


def test_new_write_outage_stream_announces_exactly_once(tmp_path, monkeypatch):
    hub, _, _, clock, _, _ = fixture(tmp_path, monkeypatch)

    async def run():
        await bootstrap(hub)
        await hub._process_trade(tick(clock))
        hub._running = True
        client_id = await hub.subscribe(["AAPL"])
        stream = hub.events(client_id)
        try:
            assert (await anext(stream))["event"] == "quotes"
            assert (await anext(stream))["data"]["resync_required"]
            await hub._process_trade(tick(clock, "AAPL", 99))
            await hub._process_trade(tick(clock))
            assert not hub._clients[client_id].resync_required
            heartbeat = await asyncio.wait_for(anext(stream), timeout=1)
            assert not heartbeat["data"].get("resync_required", False)
        finally:
            await stream.aclose()
            hub._running = False

    asyncio.run(run())


def test_failed_inventory_does_not_create_a_sticky_trade_error(tmp_path, monkeypatch):
    hub, adapter, repo, clock, blocked, _ = fixture(tmp_path, monkeypatch, seed=False)
    monotonic = [0.0]
    adapter._monotonic = lambda: monotonic[0]

    async def run():
        await hub.subscribe(["AAPL"])
        await hub._poll_radar_inventory()
        for _ in range(10):
            await hub._process_trade(tick(clock))
        assert hub._status()["last_error"] == "radar_refresh_failed"
        blocked[0] = False
        repo.initialize()
        monotonic[0] = 2.1
        await hub._poll_radar_inventory()
        await hub._poll_radar_events()
        assert not hub._signals_resync_required
        assert hub._status()["last_error"] is None

    asyncio.run(run())


def test_shared_inventory_backoff_is_bounded_and_resets_after_success(tmp_path, monkeypatch):
    _, adapter, repo, _, blocked, _ = fixture(tmp_path, monkeypatch, seed=False)
    monotonic, attempts = [0.0], []
    adapter._monotonic = lambda: monotonic[0]
    original = adapter._load_events

    def counted():
        attempts.append(True)
        return original()

    monkeypatch.setattr(adapter, "_load_events", counted)

    async def run():
        for number, delay in enumerate([2, 4, 8, 16, 30, 30], start=1):
            with pytest.raises(RadarInventoryUnavailable) as failure:
                await adapter.radar_symbols()
            assert failure.value.retry_after == delay
            # A burst of independent callers cannot bypass the same deadline.
            results = await asyncio.gather(*(adapter.radar_symbols() for _ in range(40)), return_exceptions=True)
            assert all(isinstance(row, RadarInventoryUnavailable) for row in results)
            assert len(attempts) == number
            monotonic[0] += delay + .01
        blocked[0] = False
        repo.initialize()
        assert await adapter.radar_symbols() == []
        assert adapter._inventory_failures == 0
        assert adapter._inventory_retry_at == 0
        assert await adapter.radar_symbols() == []
        assert len(attempts) == 8

    asyncio.run(run())


def test_cancelled_inventory_waiter_reuses_read_task_without_another_thread(tmp_path, monkeypatch):
    _, adapter, _, _, _, _ = fixture(tmp_path, monkeypatch)
    monotonic, reads = [0.0], []
    adapter._monotonic = lambda: monotonic[0]
    release = threading.Event()

    async def run():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_load():
            reads.append(True)
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "test did not release the inventory read"
            return []

        monkeypatch.setattr(adapter, "_load_events", slow_load)
        first = asyncio.create_task(adapter.radar_symbols())
        second = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            with pytest.raises(RadarInventoryUnavailable):
                await adapter.radar_symbols()
            monotonic[0] = 2.1
            second = asyncio.create_task(adapter.radar_symbols())
            await asyncio.sleep(0)
            assert len(reads) == 1
            release.set()
            assert await asyncio.wait_for(second, timeout=1) == []
            assert adapter._inventory_failures == 0
        finally:
            release.set()
            await asyncio.gather(first, *([second] if second else []), return_exceptions=True)

    asyncio.run(run())


def test_inventory_refresh_cannot_replace_candidate_mid_commit(tmp_path, monkeypatch):
    hub, adapter, repo, clock, blocked, _ = fixture(tmp_path, monkeypatch)
    blocked[0] = False
    release = threading.Event()
    reads = []
    load = adapter._load_events
    commit = repo.commit_live_trigger

    async def run():
        await bootstrap(hub)
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def slow_commit(*args, **kwargs):
            loop.call_soon_threadsafe(entered.set)
            assert release.wait(3), "test did not release the commit"
            return commit(*args, **kwargs)

        def counted_load():
            reads.append(True)
            return load()

        monkeypatch.setattr(repo, "commit_live_trigger", slow_commit)
        monkeypatch.setattr(adapter, "_load_events", counted_load)
        writing = asyncio.create_task(hub._process_trade(tick(clock)))
        polling = None
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            polling = asyncio.create_task(adapter.radar_symbols())
            await asyncio.sleep(0)
            assert reads == []
            release.set()
            await asyncio.wait_for(writing, timeout=1)
            assert await asyncio.wait_for(polling, timeout=1) == ["AAPL"]
            assert adapter._events["AAPL"][0]["lifecycle_state"] == "TRIGGERED"
        finally:
            release.set()
            await asyncio.gather(writing, *([polling] if polling else []), return_exceptions=True)

    asyncio.run(run())


@pytest.mark.parametrize("event_reads", [False, True])
def test_subscription_wakeups_do_not_bypass_failed_poll_delay(tmp_path, event_reads):
    async def run():
        attempted = asyncio.Event()
        reads = []

        async def broken():
            reads.append(True)
            attempted.set()
            raise OSError("unavailable")

        kwargs = {"radar_event_loader" if event_reads else "radar_loader": broken}
        hub = quotes.QuoteHub(settings(tmp_path), **kwargs)
        hub._running = True
        task = asyncio.create_task(hub._radar_events_loop() if event_reads else hub._radar_loop())
        try:
            await attempted.wait()
            # Let wait_for observe the loader's failure and enter its backoff.
            for _ in range(10):
                await asyncio.sleep(0)
            for _ in range(25):
                client_id = await hub.subscribe(["AAPL"])
                await asyncio.sleep(0)
                hub.unsubscribe(client_id)
            assert len(reads) == 1
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            hub._running = False

    asyncio.run(run())
