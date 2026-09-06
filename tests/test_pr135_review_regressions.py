"""Independent PR #135 regressions. No real network, keys, or production data.

Run against 83741316798fb39284e368d6ed248c99d5a13896.
The reviewed modules were byte-checked against GitHub blob SHAs. These tests
were first reproduced as seven failures on that head. Keep the assertions when
changing recovery behavior; a no-op must never count as a successful write.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import realtime_quotes as quotes
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository

NOW = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)


def settings(path):
    return SimpleNamespace(quotes_enabled=True, quotes_signals_enabled=True,
                           finnhub_api_key="test-no-network", data_dir=path, quotes_release_seconds=1)


def candidate(at):
    return {
        "event_id": "radar-AAPL", "ticker": "AAPL", "trading_date": at.date().isoformat(),
        "session": "regular", "setup_type": "DAILY_BASE_BREAKOUT", "lifecycle_state": "WATCHING",
        "event_at": at.isoformat(), "first_seen_at": at.isoformat(), "last_seen_at": at.isoformat(),
        "triggered_at": None, "state_changed_at": at.isoformat(), "event_price": 99,
        "pivot_id": "pivot-AAPL", "source_snapshot_id": "fixture",
        "structure": {"status": "active", "resistance_zone": {"low": 99, "high": 100},
                      "pivot_price": 100, "invalidation_price": 95},
        "scores": {"alert_priority_score": 80},
        "features": {"status": "active", "current_price": 99, "atr20": 2},
        "data_quality": {"market_shape_status": "active", "market_eligibility": "allowed"},
    }


def fixture(tmp_path, monkeypatch, *, seed=True):
    """Use the real repository and adapter. Deny writes at the SQLite level."""
    clock = [NOW]
    monkeypatch.setattr(quotes, "_utcnow", lambda: clock[0])
    config = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
                             db_path=tmp_path / "optix.db", RANGE_PERSISTENCE_MODE="disabled")
    repo = BreakoutRepository(config.db_path, clock=lambda: clock[0])
    if seed:
        repo.initialize()
        at = NOW - timedelta(seconds=20)
        scan = repo.begin_scan(provider="fixture", session="regular", scheduled_at=at,
                               config_hash="fixture", versions_hash="fixture", now=at)
        repo.publish_scan(scan, {
            "provider_snapshot": {"provider": "fixture", "status": "active", "as_of": at,
                                  "session": "regular", "schema_version": "fixture-v1", "candidates": []},
            "events": [candidate(at)],
        }, now=at)
    adapter = BreakoutRealtimeAdapter(config, repo, now=lambda: clock[0])
    hub = quotes.QuoteHub(settings(tmp_path), radar_loader=adapter.radar_symbols,
                         radar_event_loader=adapter.radar_updates, trade_handler=adapter.handle_trade)
    blocked = [True]
    writes = []
    original_connection = repo._write_connection

    def write_connection():
        connection = original_connection()
        writes.append(clock[0].isoformat())
        if blocked[0]:
            connection.execute("PRAGMA query_only=ON")
        return connection

    monkeypatch.setattr(repo, "_write_connection", write_connection)
    return hub, adapter, repo, clock, blocked, writes


def tick(clock, symbol="AAPL", price=105.0):
    clock[0] += timedelta(milliseconds=10)
    return {"s": symbol, "p": price, "t": int(clock[0].timestamp() * 1000), "v": 100, "c": ["1"]}


async def bootstrap(hub):
    await hub._poll_radar_inventory()
    await hub._poll_radar_events()
    assert hub._radar_events_loaded and getattr(hub, "_radar_events_failures", 0) == 0
    assert hub._radar_symbols == ["AAPL"]
    return await hub.subscribe(["AAPL", "SPY"])


@pytest.mark.parametrize("noop_symbol,noop_price", [("SPY", 600), ("AAPL", 99)])
def test_noop_ticks_cannot_rearm_an_unresolved_write_failure(tmp_path, monkeypatch, noop_symbol, noop_price):
    """An unrelated SPY tick does not prove AAPL writes recovered."""
    hub, adapter, repo, clock, blocked, writes = fixture(tmp_path, monkeypatch)

    async def scenario():
        client_id = await bootstrap(hub)
        client = hub._clients[client_id]
        notifications = 0
        for _ in range(6):
            await hub._process_trade(tick(clock, "AAPL"))
            notifications += int(client.resync_required)
            client.resync_required = False  # the connected browser consumed it
            # No SPY candidate exists; AAPL at 99 is below its trigger. Neither writes.
            await hub._process_trade(tick(clock, noop_symbol, noop_price))
        assert len(writes) == 6
        assert repo.overlay_live_events([repo.get_event("radar-AAPL")])[0]["lifecycle_state"] == "WATCHING"
        print(f"mixed_ticks({noop_symbol}): write_failures={len(writes)}, resync_announcements={notifications}, "
              f"signals_resync_required={hub._signals_resync_required}")
        assert notifications == 1, "A persistent write failure was announced repeatedly after no-op ticks"
        assert hub._signals_resync_required is True

    asyncio.run(scenario())


def test_real_successful_commit_clears_trade_error(tmp_path, monkeypatch):
    hub, adapter, repo, clock, blocked, writes = fixture(tmp_path, monkeypatch)

    async def scenario():
        await bootstrap(hub)
        await hub._process_trade(tick(clock))
        assert hub._signals_resync_required is True
        blocked[0] = False
        await hub._process_trade(tick(clock))
        assert repo.overlay_live_events([repo.get_event("radar-AAPL")])[0]["lifecycle_state"] == "TRIGGERED"
        await hub._poll_radar_inventory()
        await hub._poll_radar_events()
        assert not hub._signals_resync_required
        print(f"successful_write: last_error={hub._status()['last_error']!r}")
        assert hub._status()["last_error"] is None, "A committed, recovered trade still reports radar_trade_failed"

    asyncio.run(scenario())


def test_new_stream_during_write_outage_receives_resync(tmp_path, monkeypatch):
    hub, adapter, repo, clock, blocked, writes = fixture(tmp_path, monkeypatch)

    async def scenario():
        await bootstrap(hub)
        await hub._process_trade(tick(clock))
        assert hub._signals_resync_required and hub._radar_events_loaded
        hub._running = True
        late_id = await hub.subscribe(["AAPL"])
        stream = hub.events(late_id)
        try:
            first = await anext(stream)
            second = await anext(stream)
            print(f"new_stream: events={[first['event'], second['event']]}, "
                  f"resync={[e['data'].get('resync_required', False) for e in [first, second]]}")
            assert (first["data"].get("status", {}).get("resync_required")
                    or first["data"].get("resync_required") or second["data"].get("resync_required")
                    or hub._clients[late_id].resync_required), "Write-only outage omitted new stream resync"
        finally:
            await stream.aclose()
            hub._running = False

    asyncio.run(scenario())


def test_read_outage_notifies_clients_outside_first_failed_symbol(tmp_path, monkeypatch):
    hub, adapter, repo, clock, blocked, writes = fixture(tmp_path, monkeypatch)

    async def scenario():
        aapl = await bootstrap(hub)
        msft = await hub.subscribe(["MSFT"])
        await hub._process_trade(tick(clock))
        assert hub._clients[aapl].resync_required
        assert not hub._clients[msft].resync_required

        async def now_reads_fail():
            raise OSError("fixture read error")

        hub._radar_event_loader = now_reads_fail
        await hub._poll_radar_events()
        print(f"second_source: AAPL={hub._clients[aapl].resync_required}, "
              f"MSFT={hub._clients[msft].resync_required}")
        assert hub._clients[msft].resync_required, "Global degraded flag suppressed a newly affected client's notice"

    asyncio.run(scenario())


def test_recovery_of_one_reader_does_not_hide_other_reader_failure(tmp_path):
    event_reads_fail = [True]

    async def inventory():
        raise OSError("fixture inventory error")

    async def events():
        if event_reads_fail[0]:
            raise OSError("fixture event error")
        return []

    async def scenario():
        hub = quotes.QuoteHub(settings(tmp_path), radar_loader=inventory, radar_event_loader=events)
        await hub._poll_radar_inventory()
        await hub._poll_radar_events()
        event_reads_fail[0] = False
        await hub._poll_radar_events()
        assert hub._radar_failures == 1
        print(f"partial_recovery: inventory_failures={hub._radar_failures}, last_error={hub._status()['last_error']!r}")
        assert hub._status()["last_error"] == "radar_refresh_failed", "An unrecovered inventory error vanished"

    asyncio.run(scenario())


def test_missing_database_trade_path_respects_inventory_retry_budget(tmp_path, monkeypatch):
    """Known absent DB: trade callbacks must not retry reads on every tick."""
    hub, adapter, repo, clock, blocked, writes = fixture(tmp_path, monkeypatch, seed=False)
    reads = []
    real_load = adapter._load_events

    def counted_load():
        reads.append(True)
        return real_load()

    monkeypatch.setattr(adapter, "_load_events", counted_load)

    async def scenario():
        await hub.subscribe(["AAPL"])
        await hub._poll_radar_inventory()
        assert hub._radar_failures == 1
        before = len(reads)
        # A single 1-second observation window, shorter than the 2-second backoff.
        for _ in range(100):
            await hub._process_trade(tick(clock))
        attempted = len(reads) - before
        print(f"missing_database: additional_inventory_reads={attempted}, "
              f"configured_poll_backoff={hub._retry_delay(hub._radar_failures)}")
        assert attempted <= 1, "The trade path bypassed the failed-inventory backoff"

    asyncio.run(scenario())
