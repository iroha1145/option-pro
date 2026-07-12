from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from app.services.breakouts.adapters.market_shape import ExistingMarketShapeAdapter
from app.services.breakouts.models import BreakoutSetupType
from app.services.strength.market_shape import (
    MARKET_SHAPE_VERSION,
    apply_confirmation_rules,
    build_market_shape,
    market_fit_for_setup,
)


NOW = datetime(2026, 7, 10, 20, 30, tzinfo=timezone.utc)


def _regime(
    *,
    score: float,
    trend: float,
    momentum: float,
    breadth: float,
    risk_on: float,
    above_50: bool = False,
    above_200: bool = False,
    slope_up: bool = False,
    spy_20d: float = 0.0,
    drawdown: float = -2.0,
    vix: float = 18.0,
) -> dict:
    return {
        "status": "active",
        "score": score,
        "index_trend_score": trend,
        "market_momentum_score": momentum,
        "market_breadth_score": breadth,
        "market_volume_score": 55.0,
        "risk_on_spread_score": risk_on,
        "risk_appetite_score": 55.0,
        "trend": {
            "spy_above_sma50": above_50,
            "spy_above_sma200": above_200,
            "spy_sma200_slope_up": slope_up,
        },
        "momentum": {"spy_20d": spy_20d},
        "risk": {"spy_drawdown_50d": drawdown, "vix": vix},
        "warnings": [],
    }


@pytest.mark.parametrize(
    ("regime", "expected"),
    [
        (
            _regime(
                score=76,
                trend=90,
                momentum=68,
                breadth=70,
                risk_on=72,
                above_50=True,
                above_200=True,
                slope_up=True,
                spy_20d=6,
            ),
            "BULL_TREND",
        ),
        (
            _regime(
                score=56,
                trend=70,
                momentum=42,
                breadth=55,
                risk_on=52,
                above_200=True,
                slope_up=True,
                spy_20d=-3,
            ),
            "BULL_PULLBACK",
        ),
        (
            _regime(score=61, trend=58, momentum=57, breadth=65, risk_on=62),
            "RANGE_ACCUMULATION",
        ),
        (
            _regime(score=47, trend=50, momentum=46, breadth=42, risk_on=43),
            "RANGE_DISTRIBUTION",
        ),
        (
            _regime(score=31, trend=25, momentum=32, breadth=30, risk_on=28),
            "BEAR_TREND",
        ),
        (
            _regime(
                score=55,
                trend=48,
                momentum=66,
                breadth=54,
                risk_on=62,
                spy_20d=4,
                drawdown=-9,
                vix=25,
            ),
            "CAPITULATION_RECOVERY",
        ),
    ],
)
def test_six_state_market_shape_rules_are_deterministic(regime: dict, expected: str) -> None:
    shape = build_market_shape(regime, as_of=NOW)
    assert shape["status"] == "active"
    assert shape["state"] == expected
    assert shape["version"] == MARKET_SHAPE_VERSION
    assert 0.0 <= shape["confidence"] <= 1.0
    assert 0.0 <= shape["transition_risk"] <= 1.0
    assert shape["rules"]["ordinary_breakout_fit"] >= 0


def test_insufficient_market_data_stays_unavailable_without_neutral_fit() -> None:
    shape = build_market_shape(
        {"status": "insufficient_data", "score": None, "warnings": ["missing"]},
        as_of=NOW,
    )
    assert shape["state"] is None
    assert shape["confidence"] == 0.0
    assert market_fit_for_setup(shape, BreakoutSetupType.DAILY_BASE_BREAKOUT) is None


def test_partial_optional_market_dimensions_degrade_without_neutral_fifties() -> None:
    regime = _regime(
        score=61,
        trend=58,
        momentum=57,
        breadth=65,
        risk_on=62,
    )
    regime["market_volume_score"] = None
    shape = build_market_shape(regime, as_of=NOW)
    assert shape["status"] == "degraded"
    assert shape["state"] is not None
    assert shape["evidence"]["volume_score"] is None
    assert "market_volume" in shape["optional_missing"]


def test_recovery_state_prefers_recovery_breakout_over_fresh_breakout() -> None:
    shape = build_market_shape(
        _regime(
            score=55,
            trend=48,
            momentum=66,
            breadth=54,
            risk_on=62,
            spy_20d=4,
            drawdown=-9,
            vix=25,
        ),
        as_of=NOW,
    )
    recovery = market_fit_for_setup(shape, BreakoutSetupType.RECOVERY_BREAKOUT)
    fresh = market_fit_for_setup(shape, BreakoutSetupType.DAILY_BASE_BREAKOUT)
    assert recovery is not None and fresh is not None and recovery > fresh


def test_distribution_requires_extra_complete_bar_and_disallows_single_bar_bypass() -> None:
    shape = build_market_shape(
        _regime(score=47, trend=50, momentum=46, breadth=42, risk_on=43),
        as_of=NOW,
    )
    detection = apply_confirmation_rules(
        {
            "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "triggered": True,
            "confirmed": True,
            "strong_single_bar_confirmation": True,
            "warnings": [],
        },
        {"hold_bars_above_pivot": 2},
        shape,
        base_confirmation_bars=2,
    )
    assert detection["confirmed"] is False
    assert detection["market_confirmation_bars_required"] == 3
    assert detection["market_single_bar_confirmation_allowed"] is False
    assert "market_confirmation_tightened" in detection["warnings"]


def test_adapter_returns_real_shape_from_strength_service(monkeypatch) -> None:
    shape = build_market_shape(
        _regime(
            score=76,
            trend=90,
            momentum=68,
            breadth=70,
            risk_on=72,
            above_50=True,
            above_200=True,
            slope_up=True,
            spy_20d=6,
        ),
        as_of=NOW,
    )

    async def fake_market_strength(*, as_of):
        assert as_of == NOW
        return {"market_regime": {"market_shape": shape}}

    monkeypatch.setattr(
        "app.services.strength.scanner.market_strength",
        fake_market_strength,
    )
    snapshot = asyncio.run(ExistingMarketShapeAdapter().snapshot(as_of=NOW))
    assert snapshot.status == "active"
    assert snapshot.state == "BULL_TREND"
    assert snapshot.rules["eligibility"] == "normal"
