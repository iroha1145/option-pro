from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

from app.services import realtime_quotes as quotes
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository


NOW = datetime(2026, 9, 4, 14, 30, tzinfo=timezone.utc)


def _hub_settings(tmp_path):
    return SimpleNamespace(quotes_enabled=True, quotes_signals_enabled=True,
                           finnhub_api_key="fixture", data_dir=tmp_path)


def _candidate(at):
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


def _publish(repo, at, event, **extra):
    scan_id = repo.begin_scan(provider="fixture", session="regular", scheduled_at=at,
                             config_hash="fixture", versions_hash="fixture", now=at)
    repo.publish_scan(scan_id, {
        "provider_snapshot": {"provider": "fixture", "status": "active", "as_of": at,
                              "session": "regular", "schema_version": "fixture-v1", "candidates": []},
        "events": [event], **extra,
    }, now=at)


def test_worker_confirmation_and_terminal_commit_reach_open_and_reconnected_browser(tmp_path, monkeypatch):
    clock = [NOW]
    monkeypatch.setattr(quotes, "_utcnow", lambda: clock[0])
    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
                                db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled")
    repo = BreakoutRepository(settings.db_path, clock=lambda: clock[0])
    repo.initialize()
    original = _candidate(NOW - timedelta(seconds=20))
    _publish(repo, NOW - timedelta(seconds=20), original)
    adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: clock[0])

    async def scenario():
        hub = quotes.QuoteHub(_hub_settings(tmp_path), radar_loader=adapter.radar_symbols,
                              radar_event_loader=adapter.radar_updates, trade_handler=adapter.handle_trade)
        hub._running = True
        await hub._poll_radar_events()
        client_id = await hub.subscribe(["AAPL"])
        stream = hub.events(client_id)
        assert (await anext(stream))["event"] == "quotes"
        await hub._process_message(json.dumps({"type": "trade", "data": [{
            "s": "AAPL", "p": 101, "t": int(NOW.timestamp() * 1000), "v": 100, "c": ["1"],
        }]}))
        first = (await anext(stream))["data"]["events"][0]
        assert (first["lifecycle_state"], first["state_version"]) == ("TRIGGERED", 1)
        # Polling the same committed row cannot deliver the tick callback twice.
        await hub._poll_radar_events()
        assert hub._clients[client_id].queue.empty()

        current = repo.overlay_live_events([repo.get_event("radar-AAPL")])[0]
        clock[0] += timedelta(minutes=5)
        confirmed = current | {"lifecycle_state": "CONFIRMED", "evidence_at": clock[0].isoformat(),
                               "last_seen_at": clock[0].isoformat(), "state_changed_at": clock[0].isoformat()}
        _publish(repo, clock[0], original, realtime_events=[confirmed])
        polling = asyncio.create_task(hub._radar_events_loop())
        try:
            pushed = (await asyncio.wait_for(anext(stream), timeout=2))["data"]["events"][0]
            assert (pushed["lifecycle_state"], pushed["state_version"]) == ("CONFIRMED", 2)
            assert hub._quotes["AAPL"]["price"] == 101  # No subsequent trade was required.
            await hub._poll_radar_events()
            hub._publish_radar_updates([first])  # A late callback is older than the worker's row.
            assert hub._clients[client_id].queue.empty()

            clock[0] += timedelta(minutes=5)
            current = repo.overlay_live_events([repo.get_event("radar-AAPL")])[0]
            failed = current | {"lifecycle_state": "FAILED", "evidence_at": clock[0].isoformat(),
                                "last_seen_at": clock[0].isoformat(), "state_changed_at": clock[0].isoformat()}
            _publish(repo, clock[0], original, realtime_events=[failed])
            terminal = (await asyncio.wait_for(anext(stream), timeout=2))["data"]["events"][0]
            assert (terminal["lifecycle_state"], terminal["state_version"]) == ("FAILED", 3)

            await stream.aclose()
            reconnect_id = await hub.subscribe(["AAPL", "MSFT"])
            reconnected = hub.events(reconnect_id)
            assert (await anext(reconnected))["event"] == "quotes"
            restored = (await anext(reconnected))["data"]["events"]
            assert len(restored) == 1
            assert restored[0]["symbol"] == "AAPL"
            assert restored[0]["state_version"] == 3
            await reconnected.aclose()
        finally:
            polling.cancel()
            await asyncio.gather(polling, return_exceptions=True)
            await stream.aclose()
            hub._running = False

        # A new service instance restores the same final state from durable
        # rows; a process restart does not depend on an in-memory trigger.
        restarted_adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: clock[0])
        restarted = quotes.QuoteHub(_hub_settings(tmp_path), radar_event_loader=restarted_adapter.radar_updates)
        restarted._running = True
        await restarted._poll_radar_events()
        restarted_stream = restarted.events(await restarted.subscribe(["AAPL"]))
        assert (await anext(restarted_stream))["event"] == "quotes"
        assert (await anext(restarted_stream))["data"]["events"][0]["state_version"] == 3
        await restarted_stream.aclose()
        restarted._running = False

    asyncio.run(scenario())


def test_slow_older_inventory_cannot_erase_concurrent_trade_commit(tmp_path):
    async def scenario():
        entered, release = asyncio.Event(), asyncio.Event()
        rows = []

        async def loader():
            old = list(rows)
            entered.set()
            await release.wait()
            return old

        hub = quotes.QuoteHub(_hub_settings(tmp_path), radar_event_loader=loader)
        client_id = await hub.subscribe(["AAPL"])
        poll = asyncio.create_task(hub._poll_radar_events())
        await entered.wait()
        change = {"event_id": "radar-AAPL", "symbol": "AAPL", "state_version": 1, "lifecycle_state": "TRIGGERED"}
        hub._publish_radar_updates([change])
        release.set()
        await poll
        assert hub._radar_events["radar-AAPL"] == change
        assert hub._clients[client_id].queue.qsize() == 1
        rows.append(change)
        await hub._poll_radar_events()
        assert hub._clients[client_id].queue.qsize() == 1

    asyncio.run(scenario())


def test_provider_dot_symbol_triggers_saved_hyphen_radar_and_notifies_alias_page(tmp_path, monkeypatch):
    monkeypatch.setattr(quotes, "_utcnow", lambda: NOW)
    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
                                db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled")
    repo = BreakoutRepository(settings.db_path, clock=lambda: NOW)
    repo.initialize()
    original = _candidate(NOW - timedelta(seconds=20)) | {"event_id": "radar-BRK-B", "ticker": "BRK-B"}
    _publish(repo, NOW - timedelta(seconds=20), original)
    adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: NOW)

    async def scenario():
        hub = quotes.QuoteHub(_hub_settings(tmp_path), radar_loader=adapter.radar_symbols,
                              radar_event_loader=adapter.radar_updates, trade_handler=adapter.handle_trade)
        await hub._poll_radar_inventory()
        client_id = await hub.subscribe(["BRK.B"])
        assert hub._desired_symbols.count("BRK.B") == 1
        assert "BRK-B" not in hub._desired_symbols
        await hub._process_trade({"s": "BRK.B", "p": 101, "t": int(NOW.timestamp() * 1000), "v": 100, "c": ["1"]})
        pushed = hub._clients[client_id].queue.get_nowait()
        row = pushed["data"]["events"][0]
        assert row["ticker"] == "BRK-B"
        assert row["lifecycle_state"] == "TRIGGERED"
        assert (await hub.snapshot(["BRK.B"]))["quotes"][0]["price"] == 101
        assert repo.overlay_live_events([repo.get_event("radar-BRK-B")])[0]["state_version"] == 1

    asyncio.run(scenario())


def test_missing_or_failed_radar_bootstrap_requests_resync_and_keeps_cache_bounded(tmp_path, monkeypatch):
    async def scenario():
        async def loader():
            raise OSError("private repository error")

        hub = quotes.QuoteHub(_hub_settings(tmp_path), radar_event_loader=loader)
        hub._running = True
        client_id = await hub.subscribe(["AAPL"])
        stream = hub.events(client_id)
        assert (await anext(stream))["event"] == "quotes"
        assert (await anext(stream))["data"]["resync_required"]
        await hub._poll_radar_events()
        assert not hub._radar_events_loaded
        assert hub._clients[client_id].resync_required
        monkeypatch.setattr(quotes, "MAX_RADAR_EVENTS", 2)
        for index in range(3):
            hub._publish_radar_updates([{"event_id": f"radar-{index}", "symbol": "AAPL", "state_version": 1}])
        assert len(hub._radar_events) == 2
        assert len(hub._clients[client_id].radar_versions) == 2
        assert len(hub._radar_event_sequences) == 2
        await stream.aclose()

    asyncio.run(scenario())
