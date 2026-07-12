from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from app.services.breakouts.breakout_detector import detect_breakout
from app.services.breakouts.models import (
    AssetType,
    BreakoutCandidate,
    BreakoutLifecycleState,
    BreakoutSetupType,
    BreakoutStructure,
    MarketSession,
    PriceZone,
    TemporalCutoff,
)


NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 7, 10, 10, 30, tzinfo=NY)


def _candidate(session=MarketSession.REGULAR) -> BreakoutCandidate:
    return BreakoutCandidate(
        ticker="TEST",
        exchange="NASDAQ",
        asset_type=AssetType.COMMON_STOCK,
        name="Test",
        price=102.0,
        provider_change_pct=8.0,
        provider_volume=2_000_000,
        provider_relative_volume=3.0,
        provider_market_cap=1_000_000_000,
        provider_timestamp=NOW,
        source="fixture",
        session=session,
    )


def _structure() -> BreakoutStructure:
    return BreakoutStructure(
        ticker="TEST",
        base_start=date(2026, 5, 1),
        base_end=date(2026, 7, 9),
        calculation_cutoff_at=datetime(2026, 7, 9, 16, 0, tzinfo=NY),
        base_duration_days=40,
        support_zone=PriceZone(low=90, high=92, touches=2),
        resistance_zone=PriceZone(low=99, high=100, touches=3),
        pivot_price=99.5,
        pivot_id="pivot-123456",
        pivot_touch_count=3,
        invalidation_price=89.5,
        quality=0.8,
        status="active",
    )


def _cutoff(session=MarketSession.REGULAR):
    return TemporalCutoff(event_at=NOW, session=session)


def test_high_mover_without_structure_is_momentum_spike() -> None:
    result = detect_breakout(
        _candidate(),
        None,
        {"event_price": 105.0, "atr20": 2.0},
        _cutoff(),
    )
    assert result["setup_type"] is BreakoutSetupType.MOMENTUM_SPIKE
    assert result["lifecycle_state"] is BreakoutLifecycleState.WATCHING
    assert result["triggered"] is False


def test_valid_cross_is_triggered_but_not_confirmed_without_evidence() -> None:
    result = detect_breakout(
        _candidate(),
        _structure(),
        {"event_price": 101.0, "atr20": 2.0, "hold_bars_above_pivot": 1},
        _cutoff(),
    )
    assert result["setup_type"] is BreakoutSetupType.DAILY_BASE_BREAKOUT
    assert result["lifecycle_state"] is BreakoutLifecycleState.TRIGGERED
    assert result["confirmed"] is False


def test_two_holding_bars_confirm_breakout() -> None:
    result = detect_breakout(
        _candidate(),
        _structure(),
        {"event_price": 101.0, "atr20": 2.0, "hold_bars_above_pivot": 2},
        _cutoff(),
    )
    assert result["lifecycle_state"] is BreakoutLifecycleState.CONFIRMED
    assert result["confirmed"] is True


def test_premarket_gap_never_directly_confirms_daily_breakout() -> None:
    result = detect_breakout(
        _candidate(MarketSession.PREMARKET),
        _structure(),
        {"event_price": 110.0, "atr20": 2.0, "hold_bars_above_pivot": 5},
        _cutoff(MarketSession.PREMARKET),
    )
    assert result["setup_type"] is BreakoutSetupType.PREMARKET_GAP
    assert result["confirmed"] is False


def test_opening_range_requires_completed_range() -> None:
    features = {
        "event_price": 103.0,
        "atr20": 2.0,
        "opening_range_high": 101.0,
        "opening_range_complete": True,
    }
    result = detect_breakout(_candidate(), _structure(), features, _cutoff())
    assert result["setup_type"] is BreakoutSetupType.OPENING_RANGE_BREAKOUT
    assert result["lifecycle_state"] is BreakoutLifecycleState.TRIGGERED
    assert result["secondary_detection"]["setup_type"] is BreakoutSetupType.DAILY_BASE_BREAKOUT
    assert result["secondary_detection"]["triggered"] is True
