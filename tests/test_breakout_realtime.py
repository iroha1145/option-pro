from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from app.api import breakouts as api
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import BreakoutEvent, MarketSession, MarketShapeSnapshot
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository, SchemaVersionError
from app.services.breakouts.service import BreakoutRadarService

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


def test_batch_cross_and_retrace_preserves_only_one_observed_trigger(seeded):
    _, repo, adapter = seeded
    before = repo.latest_completed_scan()

    async def run():
        assert await adapter.radar_symbols() == ["AAPL"]
        up = await adapter.handle_trade(trade())
        assert len(up) == 1
        assert await adapter.handle_trade(trade(price=90, seconds=11)) == []
        assert await adapter.handle_trade(trade()) == []
        assert await adapter.handle_trade(trade(price=110, seconds=12)) == []
        return up[0]

    change = asyncio.run(run())
    assert change["lifecycle_state"] == "TRIGGERED"
    assert change["state_version"] == 1
    assert change["trigger_source"] == "finnhub"
    assert repo.latest_completed_scan() == before
    pure = repo.get_event("event-AAPL")
    assert pure["lifecycle_state"] == "WATCHING"
    assert pure["transitions"] == []
    effective = repo.overlay_live_events([pure], with_transitions=True)[0]
    assert effective["lifecycle_state"] == "TRIGGERED"
    assert len(effective["transitions"]) == 1
    assert effective["event_price"] == 101


@pytest.mark.parametrize("modification", [
    {"session": "premarket"}, {"source": "finnhub_rest"}, {"source": "rest_snapshot"}, {"price": float("nan")},
    {"price": 0}, {"seconds": -20}, {"seconds": 60},
])
def test_invalid_stale_future_and_snapshot_inputs_do_not_trigger(seeded, modification):
    _, repo, adapter = seeded
    assert asyncio.run(adapter.handle_trade(trade(**modification))) == []
    assert repo.overlay_live_events([repo.get_event("event-AAPL")])[0]["lifecycle_state"] == "WATCHING"


@pytest.mark.parametrize("update", [
    {"structure": None}, {"features": {"status": "insufficient_data"}},
    {"data_quality": {"market_shape_status": "unavailable"}},
    {"lifecycle_state": "EXPIRED"}, {"setup_type": "MOMENTUM_SPIKE"},
])
def test_structure_market_and_terminal_guards(seeded, update):
    _, repo, adapter = seeded
    publish(repo, AT + timedelta(seconds=1), [event(last_seen_at=(AT + timedelta(seconds=1)).isoformat(), **update)])
    assert asyncio.run(adapter.handle_trade(trade())) == []


def test_dropped_candidate_and_subscription_order_are_stable(seeded):
    _, repo, adapter = seeded
    publish(repo, AT + timedelta(seconds=1), [event("MSFT"), event("AAPL")])
    assert asyncio.run(adapter.radar_symbols()) == ["MSFT", "AAPL"]
    asyncio.run(adapter.handle_trade(trade()))
    assert asyncio.run(adapter.radar_symbols()) == ["AAPL", "MSFT"]
    # A later discovery dropout cannot erase an active event.
    publish(repo, AT + timedelta(seconds=15), [event("MSFT")])
    assert asyncio.run(adapter.radar_symbols()) == ["AAPL", "MSFT"]


def test_database_cas_and_restart_do_not_duplicate_or_regress_live_state(seeded):
    settings, repo, adapter = seeded
    asyncio.run(adapter.handle_trade(trade()))
    baseline = repo.get_event("event-AAPL")
    original_live = repo.overlay_live_events([baseline])[0]
    confirmed = copy.deepcopy(original_live) | {
        "lifecycle_state": "CONFIRMED", "previous_state": "TRIGGERED",
        "last_seen_at": (AT + timedelta(minutes=10)).isoformat(),
        "evidence_at": (AT + timedelta(minutes=10)).isoformat(),
        "state_changed_at": (AT + timedelta(minutes=10)).isoformat(),
    }
    publish(repo, AT + timedelta(minutes=10), [event(last_seen_at=(AT + timedelta(minutes=10)).isoformat())],
            realtime_events=[confirmed])
    newer = repo.overlay_live_events([baseline])[0]
    assert newer["state_version"] == 2
    assert newer["lifecycle_state"] == "CONFIRMED"
    late = original_live | {"last_seen_at": (AT + timedelta(minutes=11)).isoformat(),
                            "evidence_at": (AT + timedelta(minutes=11)).isoformat()}
    publish(repo, AT + timedelta(minutes=11), [event(last_seen_at=(AT + timedelta(minutes=11)).isoformat())],
            realtime_events=[late])
    assert repo.overlay_live_events([baseline])[0] == newer
    restarted = BreakoutRealtimeAdapter(settings, repo, now=lambda: AT + timedelta(minutes=11, seconds=2))
    assert asyncio.run(restarted.handle_trade(trade(seconds=662))) == []
    assert "finnhub" not in json.dumps(repo.latest_completed_scan(), default=str)


def test_old_scheduled_scan_cannot_append_regressive_transitions(seeded):
    _, repo, _ = seeded
    recent = event(lifecycle_state="CONFIRMED", triggered_at=(AT + timedelta(minutes=5)).isoformat(),
                   state_changed_at=(AT + timedelta(minutes=10)).isoformat(),
                   last_seen_at=(AT + timedelta(minutes=10)).isoformat())
    publish(repo, AT + timedelta(minutes=10), [recent])
    old = event(lifecycle_state="TRIGGERED", triggered_at=(AT + timedelta(minutes=5)).isoformat(),
                last_seen_at=(AT + timedelta(minutes=5)).isoformat())
    publish(repo, AT + timedelta(minutes=11), [old], transitions=[{
        "event_id": old["event_id"], "from_state": "WATCHING", "to_state": "TRIGGERED",
        "reason": "late_scan", "evidence_at": (AT + timedelta(minutes=5)).isoformat(),
    }])
    current = repo.get_event("event-AAPL")
    assert current["lifecycle_state"] == "CONFIRMED"
    assert current["transitions"] == []


def test_public_scan_api_has_no_live_signal_until_allowed(seeded, monkeypatch):
    settings, repo, adapter = seeded
    from app.api import quotes
    asyncio.run(adapter.handle_trade(trade()))
    monkeypatch.setattr(api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(api, "_now", lambda: AT + timedelta(seconds=20))
    monkeypatch.setattr(quotes, "realtime_visible", lambda **_: False)
    public = api.current().model_dump(mode="json")
    assert public["events"][0]["lifecycle_state"] == "WATCHING"
    assert public["events"][0]["triggered_at"] is None
    monkeypatch.setattr(quotes, "realtime_visible", lambda **_: True)
    owner = api.current().model_dump(mode="json")
    assert owner["events"][0]["lifecycle_state"] == "TRIGGERED"
    assert owner["events"][0]["trigger_source"] == "finnhub"
    assert api.event_detail("event-AAPL").transitions[0]["source"] == "finnhub"
    assert repo.latest_completed_scan()["events"][0]["lifecycle_state"] == "WATCHING"


def _frames():
    close = 90 + np.arange(100) * 0.04
    daily = pd.DataFrame({"Open": close, "High": close + 1, "Low": close - 1,
                          "Close": close, "Volume": 2_000_000},
                         index=pd.bdate_range(end="2026-07-10", periods=100))
    intra = pd.DataFrame({"Open": 100.7, "High": 101.2, "Low": 100.5,
                          "Close": 101.0, "Volume": 100_000},
                         index=pd.date_range(AT - timedelta(minutes=30), AT + timedelta(minutes=20), freq="5min"))
    return daily, intra


def _continue(settings, prior, as_of):
    service = BreakoutRadarService(settings)
    daily, intra = _frames()
    prior = {key: value for key, value in prior.items() if key != "transitions"}
    return service._continue_event(
        prior, daily_snapshot=SimpleNamespace(frame=daily, source="fixture"),
        intraday_snapshot=SimpleNamespace(frame=intra, source="fixture"),
        market_daily_snapshot=None, sector_daily_snapshot=None, sector_symbol=None,
        liquidity_distribution={}, cutoff=service._cutoff(as_of, MarketSession.REGULAR),
        observed_at=as_of, observed_session=MarketSession.REGULAR,
        market=MarketShapeSnapshot(status="active", state="UPTREND", confidence=0.9,
                                  as_of=as_of, version="fixture",
                                  rules={"allow_single_bar_confirmation": False}),
        source_snapshot_id="scheduled-snapshot", versions={}, expired_due=False,
    )[0]


def test_live_confirmation_uses_complete_post_trigger_bars_only(seeded):
    settings, repo, adapter = seeded
    asyncio.run(adapter.handle_trade(trade()))
    live = repo.overlay_live_events([repo.get_event("event-AAPL")])[0]
    # Even though many previous completed bars hold above the pivot, they
    # cannot confirm an observed trigger at 14:00:10.
    awaiting = _continue(settings, live, AT + timedelta(minutes=5))
    assert awaiting.lifecycle_state.value == "TRIGGERED"
    assert "awaiting_complete_post_trigger_bar" in awaiting.warnings
    one_bar = _continue(settings, live, AT + timedelta(minutes=10))
    assert one_bar.lifecycle_state.value == "TRIGGERED"
    assert one_bar.features["hold_bars_above_pivot"] == 1
    two_bars = _continue(settings, live, AT + timedelta(minutes=15))
    assert two_bars.lifecycle_state.value in {"CONFIRMED", "HOLDING"}
    assert two_bars.features["hold_bars_above_pivot"] == 2
    assert two_bars.triggered_at == datetime.fromisoformat(trade()["trade_at"])
    assert two_bars.evidence_at == AT + timedelta(minutes=15)
    # The independent bar-only scan neither inherits the live timestamp nor
    # exports the trade provider, even when it reaches confirmation first.
    baseline = _continue(settings, repo.get_event("event-AAPL") | {"transitions": []}, AT + timedelta(minutes=5))
    assert baseline.trigger_source is None


def test_missing_database_loader_is_read_only(tmp_path):
    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True, db_path=tmp_path / "missing.db")
    adapter = BreakoutRealtimeAdapter(settings)
    assert asyncio.run(adapter.radar_symbols()) == []
    assert not settings.db_path.exists()


def test_additive_live_schema_checksums_and_readonly_compatibility(seeded):
    settings, repo, adapter = seeded
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("UPDATE breakout_live_schema SET checksum='corrupt'")
    with pytest.raises(SchemaVersionError):
        repo.initialize()
    assert asyncio.run(adapter.radar_symbols()) == []


def test_two_live_writers_cannot_commit_duplicate_trade_transition(seeded):
    settings, repo, first = seeded
    second = BreakoutRealtimeAdapter(settings, BreakoutRepository(settings.db_path),
                                     now=lambda: AT + timedelta(seconds=20))

    async def race():
        await asyncio.gather(first.radar_symbols(), second.radar_symbols())
        results = await asyncio.gather(first.handle_trade(trade()), second.handle_trade(trade()))
        assert sum(len(result) for result in results) == 1

    asyncio.run(race())
    row = repo.overlay_live_events([repo.get_event("event-AAPL")], with_transitions=True)[0]
    assert row["state_version"] == 1
    assert len(row["transitions"]) == 1


def test_worker_passes_bar_only_and_live_carryover_separately(seeded):
    settings, repo, adapter = seeded
    from app.services.breakouts.clock import MarketClock
    from app.services.breakouts.worker import BreakoutWorker
    asyncio.run(adapter.handle_trade(trade()))
    captured = {}

    async def capture(**kwargs):
        captured.update(kwargs)
        return {"events": [], "realtime_events": kwargs["realtime_events"]}

    worker = BreakoutWorker(settings, repo, scan_service=capture)
    result = asyncio.run(worker._invoke_scan_service(
        SimpleNamespace(candidates=[]), MarketClock().snapshot(AT + timedelta(seconds=20)),
    ))
    assert captured["carryover_events"][0]["lifecycle_state"] == "WATCHING"
    assert captured["previous_events"]["AAPL"][0]["triggered_at"] is None
    assert captured["realtime_events"][0]["lifecycle_state"] == "TRIGGERED"
    assert result["realtime_events"][0]["state_version"] == 1


def test_legacy_v3_readers_need_not_create_live_tables(seeded):
    settings, repo, _ = seeded
    with sqlite3.connect(settings.db_path) as connection:
        connection.execute("DROP TABLE breakout_live_transitions")
        connection.execute("DROP TABLE breakout_live_events")
        connection.execute("DROP TABLE breakout_live_schema")
    reader = BreakoutRepository(settings.db_path, read_only=True)
    baseline = reader.latest_completed_scan()["events"]
    assert reader.overlay_live_events(baseline) == baseline
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT 1 FROM sqlite_master WHERE name='breakout_live_schema'").fetchone() is None
    repo.initialize()
    with sqlite3.connect(settings.db_path) as connection:
        assert connection.execute("SELECT 1 FROM breakout_live_schema").fetchone() is not None


def test_real_quote_hub_frame_triggers_actual_scheduled_candidate(seeded, monkeypatch):
    settings, repo, _ = seeded
    from app.services import realtime_quotes

    # Generate the eligible candidate through the actual completed-bar service
    # to catch changes to both the market filter fields and trade wire shape.
    baseline = event()
    baseline["structure"]["resistance_zone"] = {"low": 102.8, "high": 103.0}
    baseline["structure"]["pivot_price"] = 103.0
    scan_at = AT + timedelta(minutes=5)
    candidate = _continue(settings, baseline, scan_at)
    assert candidate.lifecycle_state.value == "WATCHING"
    assert candidate.data_quality["market_shape_status"] == "active"
    assert candidate.data_quality["market_eligibility"] == "allowed"
    publish(repo, scan_at, [candidate])
    now = scan_at + timedelta(seconds=20)
    monkeypatch.setattr(realtime_quotes, "_utcnow", lambda: now)
    adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: now)

    async def run():
        hub = realtime_quotes.QuoteHub(SimpleNamespace(
            quotes_enabled=True, quotes_signals_enabled=True, finnhub_api_key="fixture",
            data_dir=settings.db_path.parent,
        ), radar_loader=adapter.radar_symbols, trade_handler=adapter.handle_trade)
        client = await hub.subscribe(["AAPL"])
        above = {"s": "AAPL", "p": 104, "t": int((scan_at + timedelta(seconds=10)).timestamp() * 1000),
                 "v": 100, "c": ["1"]}
        below = above | {"p": 102, "t": above["t"] + 1, "v": 200}
        await hub._process_message(json.dumps({"type": "trade", "data": [above, below]}))
        pushed = hub._clients[client].queue.get_nowait()
        assert pushed["event"] == "radar"
        assert pushed["data"]["events"][0]["lifecycle_state"] == "TRIGGERED"
        assert hub._clients[client].queue.empty()
        assert (await hub.snapshot(["AAPL"]))["quotes"][0]["price"] == 102

    asyncio.run(run())
    pure = repo.get_event("event-AAPL")
    assert pure["lifecycle_state"] == "WATCHING"
    live = repo.overlay_live_events([pure], with_transitions=True)[0]
    assert live["lifecycle_state"] == "TRIGGERED"
    assert live["event_price"] == 104
    assert len(live["transitions"]) == 1


def test_live_reconciliation_rolls_back_with_failed_scan_publication(seeded, monkeypatch):
    _, repo, adapter = seeded
    asyncio.run(adapter.handle_trade(trade()))
    baseline = repo.get_event("event-AAPL")
    original = repo.overlay_live_events([baseline])[0]
    candidate = original | {
        "lifecycle_state": "CONFIRMED", "evidence_at": (AT + timedelta(minutes=10)).isoformat(),
        "last_seen_at": (AT + timedelta(minutes=10)).isoformat(),
    }

    def fail_after_reconciliation(phase, _connection):
        if phase == "before_complete":
            raise RuntimeError("test interrupted scan publication")

    monkeypatch.setattr(repo, "_publish_hook", fail_after_reconciliation)
    with pytest.raises(RuntimeError, match="interrupted scan publication"):
        publish(repo, AT + timedelta(minutes=10), [event()], realtime_events=[candidate])
    assert repo.overlay_live_events([baseline])[0] == original
    assert repo.latest_completed_scan()["scheduled_at"] == AT.isoformat(timespec="microseconds").replace("+00:00", "Z")


def test_recovery_updates_include_recent_confirmed_and_terminal_versions(seeded):
    settings, repo, adapter = seeded
    asyncio.run(adapter.handle_trade(trade()))
    first = asyncio.run(adapter.radar_updates())
    assert first[0]["state_version"] == 1
    assert first[0]["lifecycle_state"] == "TRIGGERED"
    live = repo.overlay_live_events([repo.get_event("event-AAPL")])[0]
    failed_at = AT + timedelta(minutes=10)
    failed = live | {"lifecycle_state": "FAILED", "state_changed_at": failed_at.isoformat(),
                     "last_seen_at": failed_at.isoformat(), "evidence_at": failed_at.isoformat()}
    publish(repo, failed_at, [event()], realtime_events=[failed])
    resumed = BreakoutRealtimeAdapter(settings, repo, now=lambda: failed_at + timedelta(seconds=1))
    update = asyncio.run(resumed.radar_updates())[0]
    assert update["lifecycle_state"] == "FAILED"
    assert update["state_version"] == 2
    assert update["symbol"] == "AAPL"
    assert asyncio.run(resumed.radar_symbols()) == []
    assert len(repo.recent_live_events(as_of=failed_at + timedelta(seconds=1), limit=1)) == 1
    assert repo.recent_live_events(as_of=failed_at + timedelta(days=3)) == []
    assert repo.recent_live_events(as_of=AT) == []
