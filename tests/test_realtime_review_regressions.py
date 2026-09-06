from __future__ import annotations

import asyncio
import copy
import json
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


@pytest.mark.parametrize("revision", ["still_eligible", "higher_resistance", "market_blocked", "deleted", "newer_observation"])
def test_same_trade_rechecks_revised_candidate_before_following_retreat(seeded, monkeypatch, tmp_path, revision):
    _, repo, adapter = seeded
    monkeypatch.setattr(quotes, "_utcnow", lambda: AT + timedelta(seconds=20))
    reloads = []
    commits = []
    original_load = adapter._load_events
    original_commit = repo.commit_live_trigger

    def load():
        reloads.append(True)
        return original_load()

    def commit(body, **kwargs):
        commits.append(body["evidence_at"])
        return original_commit(body, **kwargs)

    async def run():
        await adapter.radar_symbols()
        monkeypatch.setattr(adapter, "_load_events", load)
        monkeypatch.setattr(repo, "commit_live_trigger", commit)
        if revision == "deleted":
            with sqlite3.connect(repo.path) as connection:
                connection.execute("DELETE FROM breakout_events WHERE event_id='event-AAPL'")
        else:
            observed = 15 if revision == "newer_observation" else 5
            revised = event(last_seen_at=(AT + timedelta(seconds=observed)).isoformat())
            if revision == "higher_resistance":
                revised["structure"]["resistance_zone"]["high"] = 110
            if revision == "market_blocked":
                revised["data_quality"]["market_eligibility"] = "blocked"
            publish(repo, AT + timedelta(seconds=observed), [revised])
        hub = quotes.QuoteHub(SimpleNamespace(data_dir=tmp_path, quotes_enabled=True,
            quotes_signals_enabled=True, finnhub_api_key="fixture"), trade_handler=adapter.handle_trade)
        client_id = await hub.subscribe(["AAPL"])
        # Both trades are in the same frame; there is no independent inventory
        # refresh between the first crossing and the subsequent retreat.
        await hub._process_message(json.dumps({"type": "trade", "data": [
            {"s": "AAPL", "p": 105, "t": int((AT + timedelta(seconds=10)).timestamp() * 1000), "v": 100, "c": ["1"]},
            {"s": "AAPL", "p": 99, "t": int((AT + timedelta(seconds=11)).timestamp() * 1000), "v": 200, "c": ["1"]},
        ]}))
        rows = repo.recent_live_events(as_of=AT + timedelta(seconds=20))
        assert len(reloads) == 1
        assert hub._quotes["AAPL"]["price"] == 99
        assert hub._signals_resync_required is False
        assert hub._status()["last_error"] is None
        if revision == "still_eligible":
            assert len(commits) == 2
            assert [row["lifecycle_state"] for row in rows] == ["TRIGGERED"]
            assert rows[0]["event_price"] == 105
            assert datetime.fromisoformat(rows[0]["evidence_at"].replace("Z", "+00:00")) == AT + timedelta(seconds=10)
            assert hub._clients[client_id].queue.get_nowait()["data"]["events"][0]["lifecycle_state"] == "TRIGGERED"
        else:
            assert len(commits) == 1
            assert rows == []
            assert hub._clients[client_id].queue.empty()

    asyncio.run(run())


def test_same_trade_retry_stops_after_a_second_candidate_conflict(seeded, monkeypatch):
    _, repo, adapter = seeded
    commits = []
    reloads = []
    original_load = adapter._load_events
    original_commit = repo.commit_live_trigger

    def load():
        reloads.append(True)
        return original_load()

    def commit(body, **kwargs):
        commits.append(body["evidence_at"])
        revised_at = AT + timedelta(seconds=4 + len(commits))
        publish(repo, revised_at, [event(last_seen_at=revised_at.isoformat())])
        return original_commit(body, **kwargs)

    async def run():
        await adapter.radar_symbols()
        monkeypatch.setattr(adapter, "_load_events", load)
        monkeypatch.setattr(repo, "commit_live_trigger", commit)
        assert await adapter.handle_trade(trade(price=105)) == []
        assert len(commits) == 2
        assert len(reloads) == 1
        assert commits[0] == commits[1], "retry must retain the original trade evidence time"
        assert repo.recent_live_events(as_of=AT + timedelta(seconds=20)) == []

    asyncio.run(run())


# The conflict-retry reload is an inventory read, so it now reports the read
# fault and shares the inventory retry budget instead of latching a per-symbol
# write fault that only a later durable commit could clear.
@pytest.mark.parametrize("failure,reported", [
    ("reload", "radar_refresh_failed"), ("second_commit", "radar_trade_failed"),
])
def test_same_trade_retry_storage_failures_still_reach_hub(seeded, monkeypatch, tmp_path, failure, reported):
    _, repo, adapter = seeded
    monkeypatch.setattr(quotes, "_utcnow", lambda: AT + timedelta(seconds=20))
    commits = []
    original_commit = repo.commit_live_trigger

    def fail():
        raise sqlite3.OperationalError("fixture unavailable")

    def commit(body, **kwargs):
        commits.append(body["evidence_at"])
        if len(commits) == 2:
            fail()
        return original_commit(body, **kwargs)

    async def run():
        await adapter.radar_symbols()
        publish(repo, AT + timedelta(seconds=5), [event(last_seen_at=(AT + timedelta(seconds=5)).isoformat())])
        if failure == "reload":
            monkeypatch.setattr(adapter, "_load_events", fail)
        else:
            monkeypatch.setattr(repo, "commit_live_trigger", commit)
        hub = quotes.QuoteHub(SimpleNamespace(data_dir=tmp_path, quotes_enabled=True,
            quotes_signals_enabled=True, finnhub_api_key="fixture"), trade_handler=adapter.handle_trade)
        client_id = await hub.subscribe(["AAPL"])
        await hub._process_trade({"s": "AAPL", "p": 105, "t": int((AT + timedelta(seconds=10)).timestamp() * 1000), "v": 100, "c": ["1"]})
        assert hub._quotes["AAPL"]["price"] == 105
        assert hub._signals_resync_required is True
        assert hub._status()["last_error"] == reported
        assert hub._clients[client_id].resync_required is True
        assert repo.recent_live_events(as_of=AT + timedelta(seconds=20)) == []
        if failure == "second_commit":
            assert len(commits) == 2

    asyncio.run(run())
