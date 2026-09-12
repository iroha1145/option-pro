"""An ORB keeps its own trigger/stop through persistence and later bar checks."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta
import json
import sqlite3

import pandas as pd
import pytest

from app.api.breakouts import _public_event
from app.services.breakouts.anchors import resolve_event_anchor
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import BreakoutCandidate, BreakoutStructure, MarketSession, PriceZone
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.service import BreakoutRadarService
from test_breakout_service import AS_OF, NY, ActiveBullMarket, Prices, Strength, Universe, _discovery


@pytest.fixture
def orb_service(monkeypatch, tmp_path):
    background = BreakoutStructure(
        ticker="TEST", base_start=date(2026, 5, 1), base_end=date(2026, 7, 9),
        calculation_cutoff_at=datetime(2026, 7, 9, 16, tzinfo=NY), base_duration_days=40,
        support_zone=PriceZone(low=90, high=92, touches=2),
        resistance_zone=PriceZone(low=109, high=111, touches=3), pivot_price=110,
        pivot_id="unrelated-daily-pivot", pivot_touch_count=3, invalidation_price=89.5,
        quality=0.9, status="active",
    )
    monkeypatch.setattr("app.services.breakouts.service.detect_base", lambda *a, **k: background)
    settings = BreakoutSettings(_env_file=None, db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled")
    return BreakoutRadarService(settings, price_data=Prices(), strength=Strength(),
        market_shape=ActiveBullMarket(), universe=Universe())


def _scan(service, at=AS_OF, prior=None):
    candidates = [] if prior is not None else [BreakoutCandidate(
        ticker="TEST", price=104, provider_change_pct=8, provider_volume=2_000_000,
        provider_relative_volume=3, provider_market_cap=1_000_000_000,
        provider_timestamp=at, source="fixture", session=MarketSession.REGULAR,
    )]
    payload = asyncio.run(service.build_snapshot(
        _discovery(at=at, session=MarketSession.REGULAR, candidates=candidates),
        carryover_events=[prior] if isinstance(prior, dict) else [prior.model_dump(mode="python")] if prior is not None else [],
    ))
    return payload["events"][0], payload


def _public(service, event):
    return _public_event(service.settings, event.model_dump(mode="json"), observed_at=AS_OF)


def test_continuing_rise_is_holding_above_original_orb_not_retesting_daily_base(orb_service):
    first, _ = _scan(orb_service)
    confirmed, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), first)
    holding, _ = _scan(orb_service, AS_OF + timedelta(minutes=10), confirmed)
    assert [event.lifecycle_state.value for event in (first, confirmed, holding)] == ["CONFIRMED", "HOLDING", "HOLDING"]
    assert first.event_price < confirmed.event_price < holding.event_price
    for event in (first, confirmed, holding):
        public = _public(orb_service, event)
        assert public.pivot_price == pytest.approx(102.16666667)
        assert public.invalidation_price == pytest.approx(99.5)
        assert event.structure.pivot_price == 110  # daily background is retained
        assert event.structure.invalidation_price == 89.5
        assert event.features["breakout_distance_atr"] > 0
        assert event.features["close_above_zone_quality"] > 0
        assert public.event_anchor["source"] == "completed_opening_range"


class TailPrices(Prices):
    def __init__(self, closes):
        self.closes = closes

    async def intraday(self, tickers, *, cutoff, interval):
        snapshots = await super().intraday(tickers, cutoff=cutoff, interval=interval)
        changed = {}
        for ticker, snapshot in snapshots.items():
            frame = snapshot.frame.copy()
            for when, close in self.closes.items():
                stamp = pd.Timestamp(when)
                if stamp in frame.index:
                    frame.loc[stamp, ["Open", "High", "Low", "Close"]] = [close, close + 0.1, close - 0.1, close]
            changed[ticker] = PriceDataSnapshot(**{**vars(snapshot), "frame": frame})
        return changed


def test_orb_retest_reclaim_and_complete_bar_failure_use_the_same_saved_range(orb_service):
    first, _ = _scan(orb_service)
    confirmed, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), first)
    closes = {AS_OF + timedelta(minutes=5): 102.2}
    orb_service.price_data = TailPrices(closes)
    retesting, _ = _scan(orb_service, AS_OF + timedelta(minutes=10), confirmed)
    assert retesting.lifecycle_state.value == "RETESTING"
    closes[AS_OF + timedelta(minutes=10)] = 104.0
    reclaimed, _ = _scan(orb_service, AS_OF + timedelta(minutes=15), retesting)
    assert reclaimed.lifecycle_state.value == "RETEST_HELD"
    closes[AS_OF + timedelta(minutes=15)] = 98.0
    closes[AS_OF + timedelta(minutes=20)] = 106.0
    failed, _ = _scan(orb_service, AS_OF + timedelta(minutes=25), reclaimed)
    assert failed.event_price == 106.0
    assert failed.lifecycle_state.value == "FAILED"
    assert failed.transition_reason == "complete_bar_below_invalidation"
    assert failed.event_anchor == first.event_anchor


def test_unfinished_bar_below_orb_low_does_not_fail_the_event(orb_service):
    first, _ = _scan(orb_service)
    confirmed, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), first)
    orb_service.price_data = TailPrices({AS_OF + timedelta(minutes=10): 98.0})
    unchanged, _ = _scan(orb_service, AS_OF + timedelta(minutes=10), confirmed)
    assert unchanged.lifecycle_state.value == "HOLDING"


def test_legacy_saved_event_recovers_levels_for_read_and_next_scan_without_mutating_history(orb_service):
    event, payload = _scan(orb_service)
    legacy = event.model_dump(mode="json")
    legacy.pop("event_anchor")
    legacy["features"].update(resistance_price=110, invalidation_price=89.5)
    repository = BreakoutRepository(orb_service.settings.db_path)
    repository.initialize()
    scan_id = repository.begin_scan(provider="fixture", session="regular", scheduled_at=AS_OF,
        config_hash="test", versions_hash="test", now=AS_OF)
    payload["events"] = [legacy]
    repository.publish_scan(scan_id, payload, now=AS_OF)
    with sqlite3.connect(orb_service.settings.db_path) as connection:
        before = connection.execute("SELECT event_json FROM breakout_events").fetchone()[0]
    saved = BreakoutRepository(orb_service.settings.db_path, read_only=True).get_event(event.event_id)
    public = _public_event(orb_service.settings, saved, observed_at=AS_OF)
    assert public.pivot_price == pytest.approx(102.16666667)
    assert public.invalidation_price == 99.5
    assert public.event_anchor["source"] == "legacy_opening_range"
    with sqlite3.connect(orb_service.settings.db_path) as connection:
        assert connection.execute("SELECT event_json FROM breakout_events").fetchone()[0] == before
    # The public timeline is not an input field of the strict event model.
    continued, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), json.loads(before))
    assert continued.lifecycle_state.value == "HOLDING"
    assert continued.event_anchor.invalidation_price == 99.5
    assert continued.event_id == event.event_id


@pytest.mark.parametrize("change", ["missing_low", "different_high", "different_day"])
def test_legacy_missing_or_conflicting_evidence_never_borrows_daily_or_another_session_low(orb_service, change):
    event, _ = _scan(orb_service)
    legacy = event.model_dump(mode="json")
    legacy.pop("event_anchor")
    if change == "missing_low":
        legacy["features"].pop("opening_range_low")
    elif change == "different_high":
        legacy["features"]["opening_range_high"] = 112.0
    else:
        legacy["features"]["feature_cutoff_at"] = (AS_OF + timedelta(days=1)).isoformat()
    public = _public_event(orb_service.settings, legacy, observed_at=AS_OF)
    assert public.pivot_price == pytest.approx(102.16666667, abs=0.000001)
    assert public.invalidation_price is None
    assert public.event_anchor["status"] == "partial"
    assert public.support_zone is None


def test_missing_legacy_orb_anchor_is_unavailable_and_cannot_advance_from_daily_levels(orb_service):
    event, _ = _scan(orb_service)
    legacy = event.model_dump(mode="json")
    legacy.pop("event_anchor")
    legacy["pivot_id"] = "legacy-unknown-anchor"
    legacy["features"].update(opening_range_high=None, opening_range_low=None)
    public = _public_event(orb_service.settings, legacy, observed_at=AS_OF)
    assert public.pivot_price is public.invalidation_price is None
    assert public.event_anchor["status"] == "unavailable"
    deferred, payload = _scan(orb_service, AS_OF + timedelta(minutes=5), legacy)
    assert deferred.lifecycle_state == event.lifecycle_state
    assert "opening_range_anchor_unavailable" in deferred.warnings
    assert not payload["transitions"]


def test_saved_anchor_survives_new_provider_range_and_continuation_label(orb_service):
    event, _ = _scan(orb_service)
    changed = deepcopy(event.model_dump(mode="json"))
    changed["features"].update(opening_range_high=150, opening_range_low=140)
    changed["setup_type"] = "RETEST_BREAKOUT"
    assert resolve_event_anchor(changed) == event.event_anchor


def test_partial_anchor_can_recover_its_low_from_matching_original_session_evidence(orb_service):
    event, _ = _scan(orb_service)
    saved = event.model_dump(mode="json")
    saved["event_anchor"].update(invalidation_price=None, status="partial", source="legacy_pivot_id")
    recovered = resolve_event_anchor(saved)
    assert recovered.status == "active"
    assert recovered.invalidation_price == 99.5
    assert recovered.pivot_price == event.event_anchor.pivot_price


def test_parallel_daily_breakout_keeps_its_own_levels_and_score_inputs(orb_service, monkeypatch):
    background = BreakoutStructure(
        ticker="TEST", base_start=date(2026, 5, 1), base_end=date(2026, 7, 9),
        calculation_cutoff_at=datetime(2026, 7, 9, 16, tzinfo=NY), base_duration_days=40,
        support_zone=PriceZone(low=90, high=92, touches=2),
        resistance_zone=PriceZone(low=99, high=100, touches=3), pivot_price=99.5,
        pivot_id="parallel-daily-pivot", pivot_touch_count=3, invalidation_price=89.5,
        quality=0.9, status="active",
    )
    monkeypatch.setattr("app.services.breakouts.service.detect_base", lambda *a, **k: background)
    _first, payload = _scan(orb_service)
    by_type = {event.setup_type.value: event for event in payload["events"]}
    daily, orb = by_type["DAILY_BASE_BREAKOUT"], by_type["OPENING_RANGE_BREAKOUT"]
    assert daily.event_anchor is None
    assert _public(orb_service, daily).pivot_price == 99.5
    assert _public(orb_service, daily).invalidation_price == 89.5
    assert daily.features["breakout_distance_atr"] > orb.features["breakout_distance_atr"]


def test_legacy_continuation_label_recovers_original_orb_identity_on_following_scans(orb_service):
    event, _ = _scan(orb_service)
    legacy = event.model_dump(mode="json")
    legacy.pop("event_anchor")
    legacy.pop("origin_setup_type")
    legacy["features"].pop("origin_setup_type")
    legacy["setup_type"] = "RETEST_BREAKOUT"
    legacy["lifecycle_state"] = "RETEST_HELD"
    continued, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), legacy)
    assert continued.origin_setup_type.value == "OPENING_RANGE_BREAKOUT"
    assert continued.event_anchor.pivot_price == pytest.approx(102.16666667)
    following, _ = _scan(orb_service, AS_OF + timedelta(minutes=10), continued)
    assert following.event_anchor == continued.event_anchor


def test_daily_base_carryover_cannot_confirm_only_because_opening_range_was_crossed(orb_service):
    event, _ = _scan(orb_service)
    daily = event.model_dump(mode="json")
    daily.update(event_anchor=None, setup_type="DAILY_BASE_BREAKOUT", origin_setup_type="DAILY_BASE_BREAKOUT",
        lifecycle_state="WATCHING", pivot_id=event.structure.pivot_id, triggered_at=None)
    daily["features"]["origin_setup_type"] = "DAILY_BASE_BREAKOUT"
    continued, _ = _scan(orb_service, AS_OF + timedelta(minutes=5), daily)
    assert continued.event_price > continued.features["opening_range_high"]
    assert continued.event_price < continued.structure.resistance_zone.high
    assert continued.lifecycle_state.value == "WATCHING"
    assert continued.event_anchor is None


def test_conflicting_saved_anchor_cannot_override_the_event_identity(orb_service):
    event, _ = _scan(orb_service)
    saved = event.model_dump(mode="json")
    saved["event_anchor"].update(pivot_price=120, invalidation_price=115)
    recovered = resolve_event_anchor(saved)
    assert recovered.pivot_price == pytest.approx(102.16666667)
    assert recovered.invalidation_price == 99.5
    assert recovered.source == "legacy_opening_range"


def test_identity_for_another_session_is_not_rebound_to_current_features(orb_service):
    event, _ = _scan(orb_service)
    saved = event.model_dump(mode="json")
    saved["pivot_id"] = "orb-TEST-2026-07-09-102.166667"
    recovered = resolve_event_anchor(saved)
    assert recovered.status == "unavailable"
    assert recovered.pivot_price is recovered.invalidation_price is None


def test_realtime_legacy_orb_trigger_persists_the_same_recovered_range(tmp_path):
    from app.services.breakouts.realtime import BreakoutRealtimeAdapter
    from test_breakout_realtime import AT, event, publish, trade

    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "live-orb.db", RANGE_PERSISTENCE_MODE="disabled")
    repository = BreakoutRepository(settings.db_path, clock=lambda: AT + timedelta(seconds=20))
    repository.initialize()
    legacy = event(setup_type="OPENING_RANGE_BREAKOUT", pivot_id="orb-AAPL-2026-07-13-100.000000")
    legacy["structure"].update(pivot_price=110, resistance_zone={"low": 109, "high": 111}, invalidation_price=89.5)
    legacy["features"].update(opening_range_complete=True, opening_range_high=100, opening_range_low=98)
    publish(repository, AT, [legacy])
    adapter = BreakoutRealtimeAdapter(settings, repository, now=lambda: AT + timedelta(seconds=20))
    assert asyncio.run(adapter.handle_trade(trade(price=101)))[0]["lifecycle_state"] == "TRIGGERED"
    live = repository.overlay_live_events([repository.get_event("event-AAPL")], with_transitions=True)[0]
    assert live["event_anchor"]["pivot_price"] == 100
    assert live["event_anchor"]["invalidation_price"] == 98
    assert live["transitions"][0]["transition_sequence"] == 1
