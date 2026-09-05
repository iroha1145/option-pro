from __future__ import annotations

import asyncio
import copy
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import realtime_quotes as quotes
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.realtime import BreakoutRealtimeAdapter, RealtimeRadarError
from app.services.breakouts.repository import BreakoutRepository

AT = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def event(symbol="AAPL", **updates):
    body = {
        "event_id": f"event-{symbol}", "ticker": symbol, "trading_date": AT.date().isoformat(),
        "session": "regular", "setup_type": "DAILY_BASE_BREAKOUT", "lifecycle_state": "WATCHING",
        "event_at": AT.isoformat(), "first_seen_at": AT.isoformat(), "last_seen_at": AT.isoformat(),
        "triggered_at": None, "state_changed_at": AT.isoformat(), "event_price": 99.0,
        "pivot_id": f"pivot-{symbol}", "source_snapshot_id": "fixture-snapshot",
        "structure": {"ticker": symbol, "base_start": "2026-06-01", "base_end": "2026-07-10",
                      "calculation_cutoff_at": AT.isoformat(), "base_duration_days": 20,
                      "resistance_zone": {"low": 99.8, "high": 100}, "pivot_price": 100,
                      "pivot_id": f"pivot-{symbol}", "pivot_touch_count": 3,
                      "quality": 0.8, "status": "active", "invalidation_price": 95},
        "scores": {"alert_priority_score": 80.0, "data_confidence_score": 90.0},
        "features": {"status": "active", "current_price": 99, "atr20": 2.0,
                     "market_eligibility": "allowed"},
        "data_quality": {"market_shape_status": "active", "market_eligibility": "allowed"},
    }
    return body | updates


def publish(repo, when, events, **extra):
    scan = repo.begin_scan(provider="fixture", session="regular", scheduled_at=when,
                           config_hash="c", versions_hash="v", now=when)
    repo.publish_scan(scan, {
        "provider_snapshot": {"provider": "fixture", "status": "active", "as_of": when,
                              "session": "regular", "schema_version": "fixture-v1", "candidates": []},
        "events": events, **extra,
    }, now=when)
    return scan


@pytest.fixture
def seeded(tmp_path):
    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
                                db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled")
    repo = BreakoutRepository(settings.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    publish(repo, AT, [event()])
    adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: AT + timedelta(seconds=20))
    return settings, repo, adapter


def trade(price=101.0, seconds=10, **updates):
    return {"symbol": "AAPL", "price": price, "trade_at": (AT + timedelta(seconds=seconds)).isoformat(),
            "received_at": (AT + timedelta(seconds=seconds)).isoformat(), "session": "regular",
            "source": "finnhub", **updates}



@pytest.mark.parametrize("observed_seconds", [0, 5])
def test_scan_revising_candidate_between_load_and_trigger_wins(seeded, observed_seconds):
    _, repo, adapter = seeded

    async def run():
        await adapter.radar_symbols()
        # The same WATCHING event survives, but its valid resistance moved.
        revised = event(last_seen_at=(AT + timedelta(seconds=observed_seconds)).isoformat())
        revised["structure"]["resistance_zone"]["high"] = 110
        publish(repo, AT + timedelta(seconds=5), [revised])
        assert await adapter.handle_trade(trade(price=105)) == []
        assert repo.recent_live_events(as_of=AT + timedelta(seconds=20)) == []
        # A fresh observation can still trigger the revised structure normally.
        await adapter.radar_symbols()
        assert len(await adapter.handle_trade(trade(price=112, seconds=11))) == 1

    asyncio.run(run())


def test_real_adapter_storage_failure_reaches_hub_without_erasing_price(seeded, monkeypatch, tmp_path):
    _, repo, adapter = seeded
    now = AT + timedelta(seconds=20)
    monkeypatch.setattr(quotes, "_utcnow", lambda: now)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture database is locked")

    async def run():
        await adapter.radar_symbols()
        hub = quotes.QuoteHub(SimpleNamespace(data_dir=tmp_path, quotes_enabled=True,
            quotes_signals_enabled=True, finnhub_api_key="fixture"), trade_handler=adapter.handle_trade)
        client_id = await hub.subscribe(["AAPL"])
        monkeypatch.setattr(repo, "commit_live_trigger", fail)
        await hub._process_trade({"s": "AAPL", "p": 105, "t": int((AT + timedelta(seconds=10)).timestamp()*1000), "v": 100, "c": ["1"]})
        assert (await hub.snapshot(["AAPL"]))["quotes"][0]["price"] == 105
        assert hub._signals_resync_required is True
        assert hub._status()["last_error"] == "radar_trade_failed"
        assert hub._clients[client_id].resync_required is True

    asyncio.run(run())


@pytest.mark.parametrize("operation", ["radar_symbols", "radar_updates"])
def test_radar_read_failure_is_not_a_successful_empty_inventory(seeded, monkeypatch, operation):
    _, repo, adapter = seeded

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("fixture unavailable")

    async def run():
        await adapter.radar_symbols()
        original = copy.deepcopy(adapter._events)
        target = "load_carryover_events" if operation == "radar_symbols" else "recent_live_events"
        monkeypatch.setattr(repo, target, fail)
        with pytest.raises(RealtimeRadarError):
            await getattr(adapter, operation)()
        assert adapter._events == original

    asyncio.run(run())


def test_rest_price_ahead_of_trade_does_not_hide_observed_crossing(tmp_path, monkeypatch):
    now = AT + timedelta(seconds=20)
    monkeypatch.setattr(quotes, "_utcnow", lambda: now)
    observed = []

    async def handle(value):
        observed.append(value["price"])

    async def run():
        hub = quotes.QuoteHub(SimpleNamespace(data_dir=tmp_path, quotes_enabled=True,
            quotes_signals_enabled=True, finnhub_api_key="fixture"), trade_handler=handle)
        await hub.subscribe(["AAPL"])
        hub._apply_rest_quote("AAPL", {"c": 99, "pc": 98, "t": int((AT + timedelta(seconds=12)).timestamp())})
        # REST is a point snapshot, not evidence that no crossing occurred before it.
        await hub._process_trade({"s": "AAPL", "p": 105, "t": int((AT + timedelta(seconds=10)).timestamp()*1000), "v": 100, "c": ["1"]})
        assert observed == [105]
        assert (await hub.snapshot(["AAPL"]))["quotes"][0]["price"] == 99, "display must not rewind the newer REST price"
        # Genuine out-of-order websocket trades still cannot trigger old evidence.
        await hub._process_trade({"s": "AAPL", "p": 110, "t": int((AT + timedelta(seconds=9)).timestamp()*1000), "v": 100, "c": ["1"]})
        assert observed == [105]

    asyncio.run(run())
