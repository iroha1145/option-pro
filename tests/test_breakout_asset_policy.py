from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import breakouts as api
from app.services.breakouts.asset_policy import is_leveraged_etf
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import AssetType, BreakoutCandidate, DiscoveryProfile, MarketSession
from app.services.breakouts.normalizer import filter_and_deduplicate
from app.services.breakouts.realtime import BreakoutRealtimeAdapter
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.service import BreakoutRadarService
from tests.http_response_support import anonymous_get_request
from tests.test_breakout_realtime import AT, event, publish, trade
from tests.test_breakout_service import AS_OF, Market, Prices, Strength, Universe


@pytest.mark.parametrize("name", [
    "ProShares UltraShort Bitcoin ETF",
    "ProShares UltraShort Ether ETF",
    "T-Rex 2X Inverse Bitcoin Daily Target ETF",
    "GraniteShares 2x Short COIN Daily ETF",
    "Tradr 2X Long AXTI Daily ETF",
    "Defiance Daily Target 2X Long RKLB ETF",
    "ProShares UltraPro QQQ",
    "ProShares Ultra S&P500",
    "Direxion Daily Semiconductor Bull 3X Shares",
    "Direxion Daily Semiconductor Bear 3X Shares",
    "Example Daily -2x Fund",
    "Example 1.5x Long ETF",
    "Example 2× Long ETF",
    "Example 2 times Long ETF",
    "Example Leveraged ETF",
])
@pytest.mark.parametrize("kind", [AssetType.ETF, "etf"])
def test_explicit_leveraged_funds_are_excluded(name, kind):
    assert is_leveraged_etf(kind, name)


@pytest.mark.parametrize("kind,name", [
    (AssetType.ETF, "SPDR S&P 500 ETF Trust"),
    ("etf", "Invesco QQQ Trust"),
    ("etf", "iShares Semiconductor ETF"),
    ("etf", "ProShares Short S&P500"),
    ("etf", "Direxion Daily S&P 500 Bear 1X Shares"),
    ("etf", "Example -1x Inverse ETF"),
    ("etf", "iShares Ultra Short-Term Bond ETF"),
    ("etf", "DoubleLine Ultra Short Bond ETF"),
    ("etf", "Example Non-Leveraged ETF"),
    ("etf", "Example Unleveraged ETF"),
    ("etf", None),
    ("common_stock", "2X Growth Technologies, Inc."),
    ("common_stock", "Ultra Clean Holdings, Inc."),
    ("common_stock", "Leverage Shares Management, Inc."),
])
def test_ordinary_funds_and_stocks_are_retained(kind, name):
    assert not is_leveraged_etf(kind, name)


def test_provider_metadata_and_misclassified_fund_names_are_recognized():
    assert is_leveraged_etf("unknown", None, {"typespecs": ["etf", "leveraged"]})
    assert is_leveraged_etf(AssetType.ETF, None, {"description": "Example 2x Long ETF"})
    assert is_leveraged_etf("stock", "Tradr 2X Long AXTI Daily ETF")
    assert not is_leveraged_etf("etf", None, {"typespecs": "etf,non-leveraged"})


def candidate(ticker, name):
    return BreakoutCandidate(
        ticker=ticker, name=name, asset_type=AssetType.ETF, price=104,
        provider_change_pct=8, provider_volume=2_000_000,
        provider_relative_volume=3, provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF, source="fixture", session=MarketSession.REGULAR,
    )


@pytest.mark.parametrize("session,profile", [
    (MarketSession.REGULAR, None),
    (MarketSession.REGULAR, DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS),
    (MarketSession.PREMARKET, None),
])
def test_discovery_filters_before_result_limit(session, profile):
    leveraged = candidate("SBIT", "ProShares UltraShort Bitcoin ETF")
    ordinary = candidate("SPY", "SPDR S&P 500 ETF Trust")
    kept, warnings = filter_and_deduplicate(
        [leveraged, ordinary], session=session, profile=profile,
        settings=BreakoutSettings(_env_file=None, BREAKOUT_PROVIDER_RESULT_LIMIT=1),
    )
    assert [item.ticker for item in kept] == ["SPY"]
    assert "SBIT:leveraged_etf_excluded" in warnings


def test_cached_candidates_and_old_continuations_cannot_reenter_scan():
    class RecordingPrices(Prices):
        seen = set()

        async def daily(self, tickers, **kwargs):
            self.seen.update(tickers)
            return await super().daily(tickers, **kwargs)

        async def intraday(self, tickers, **kwargs):
            self.seen.update(tickers)
            return await super().intraday(tickers, **kwargs)

    prices = RecordingPrices()
    settings = BreakoutSettings(
        _env_file=None, BREAKOUT_PROVIDER_RESULT_LIMIT=1, RANGE_PERSISTENCE_MODE="disabled",
    )
    service = BreakoutRadarService(
        settings, price_data=prices, strength=Strength(), market_shape=Market(), universe=Universe(),
    )
    legacy = event("ETHD", name="ProShares UltraShort Ether ETF", asset_type="etf")
    result = asyncio.run(service.build_snapshot(
        SimpleNamespace(
            as_of=AS_OF, session=MarketSession.REGULAR, status="active",
            candidates=[candidate("SBIT", "ProShares UltraShort Bitcoin ETF"),
                        candidate("SPY", "SPDR S&P 500 ETF Trust")],
        ),
        carryover_events=[legacy], realtime_events=[legacy],
    ))
    assert "SPY" in prices.seen
    assert not {"SBIT", "ETHD"} & prices.seen
    assert [item.ticker for item in result["events"]] == ["SPY"]
    assert result.get("realtime_events", []) == []


@pytest.fixture
def legacy_radar(tmp_path):
    settings = BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True,
                                db_path=tmp_path / "radar.db", RANGE_PERSISTENCE_MODE="disabled")
    repo = BreakoutRepository(settings.db_path, clock=lambda: AT + timedelta(seconds=20))
    repo.initialize()
    names = [
        ("SBIT", "ProShares UltraShort Bitcoin ETF"),
        ("SPY", "SPDR S&P 500 ETF Trust"),
        ("ETHD", "ProShares UltraShort Ether ETF"),
        ("QQQ", "Invesco QQQ Trust"),
        ("CONI", "GraniteShares 2x Short COIN Daily ETF"),
        ("AAPL", "Apple Inc."),
    ]
    rows = [event(ticker, name=name, asset_type="common_stock" if ticker == "AAPL" else "etf",
                  scores={"alert_priority_score": 99 - index, "data_confidence_score": 90})
            for index, (ticker, name) in enumerate(names)]
    publish(repo, AT, rows)
    return settings, repo, rows


@pytest.mark.parametrize("algorithm", ["production", "t1_daily_priority"])
def test_legacy_snapshot_filter_fills_pages_and_preserves_archive(legacy_radar, algorithm):
    _, repo, _ = legacy_radar
    page = repo.list_events(limit=2, sort_algorithm=algorithm)
    assert [item["ticker"] for item in page["events"]] == ["SPY", "QQQ"]
    assert page["next_cursor"] is not None
    final = repo.list_events(limit=2, sort_algorithm=algorithm, cursor=page["next_cursor"])
    assert [item["ticker"] for item in final["events"]] == ["AAPL"]
    assert final["next_cursor"] is None
    assert [item["ticker"] for item in repo.latest_completed_scan()["events"]] == ["SPY", "QQQ", "AAPL"]
    assert repo.get_event("event-SBIT") is None
    assert repo.events_for_ticker("SBIT") == []
    assert len(repo.events_for_ticker("SPY")) == 1
    # The user-visible policy does not erase historical research evidence.
    with sqlite3.connect(repo.path) as db:
        assert db.execute("SELECT COUNT(*) FROM breakout_scan_events").fetchone()[0] == 6


def test_excluded_legacy_events_do_not_consume_carryover_capacity(legacy_radar):
    _, repo, _ = legacy_radar
    batch = repo.load_carryover_events(as_of=AT + timedelta(seconds=20),
                                      event_ttl_seconds=3600, limit=3, expired_due_limit=1)
    assert {item["ticker"] for item in batch.events} == {"AAPL", "QQQ", "SPY"}
    assert not batch.has_more


def seed_legacy_live(repo):
    """Persist the old version's live rows without today's read/trigger policy."""
    when = (AT + timedelta(seconds=10)).isoformat(timespec="microseconds").replace("+00:00", "Z")
    with sqlite3.connect(repo.path) as db:
        rows = db.execute("SELECT event_json FROM breakout_events").fetchall()
        for (raw,) in rows:
            live = json.loads(raw)
            live.update(lifecycle_state="TRIGGERED", state_version=1, evidence_at=when,
                        triggered_at=when, state_changed_at=when, last_seen_at=when)
            db.execute("INSERT INTO breakout_live_events VALUES(?,?,?,?,?)",
                       (live["event_id"], 1, when, json.dumps(live), when))


def test_old_live_rows_do_not_reenter_subscription_recovery_or_trigger(legacy_radar):
    settings, repo, rows = legacy_radar
    # Simulate durable live records written by the old version before upgrade.
    seed_legacy_live(repo)
    assert {item["ticker"] for item in repo.recent_live_events(
        as_of=AT + timedelta(seconds=20), limit=3,
    )} == {"AAPL", "QQQ", "SPY"}
    adapter = BreakoutRealtimeAdapter(settings, repo, now=lambda: AT + timedelta(seconds=20))

    async def run():
        assert set(await adapter.radar_symbols()) == {"AAPL", "QQQ", "SPY"}
        assert {item["ticker"] for item in await adapter.radar_updates()} == {"AAPL", "QQQ", "SPY"}
        # A preexisting memory entry must also fail the last trigger guard.
        adapter._events["SBIT"] = [rows[0]]
        assert await adapter.handle_trade(trade(symbol="SBIT")) == []

    asyncio.run(run())


def test_public_endpoints_and_changed_live_metadata_exclude_old_funds(legacy_radar, monkeypatch):
    settings, repo, rows = legacy_radar
    from app.api import quotes

    monkeypatch.setattr(api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(api, "_now", lambda: AT + timedelta(seconds=20))
    monkeypatch.setattr(quotes, "realtime_visible", lambda **_: True)
    current = api.current(anonymous_get_request())
    assert {item.ticker for item in current.events} == {"AAPL", "QQQ", "SPY"}
    assert api.ticker_events("SBIT").events == []
    with pytest.raises(HTTPException) as missing:
        api.event_detail("event-SBIT")
    assert missing.value.status_code == 404
    assert api.event_detail("event-SPY").event.ticker == "SPY"

    # Independently persisted live metadata can correct a formerly ambiguous
    # name; filtering the overlay must yield a 404 rather than an index error.
    seed_legacy_live(repo)
    with sqlite3.connect(repo.path) as db:
        payload = json.loads(db.execute(
            "SELECT event_json FROM breakout_live_events WHERE event_id='event-SPY'",
        ).fetchone()[0])
        payload["name"] = "Example 2x Long ETF"
        db.execute("UPDATE breakout_live_events SET event_json=? WHERE event_id='event-SPY'",
                   (json.dumps(payload),))
    with pytest.raises(HTTPException) as hidden:
        api.event_detail("event-SPY")
    assert hidden.value.status_code == 404
