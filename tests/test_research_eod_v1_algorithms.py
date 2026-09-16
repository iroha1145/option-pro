from __future__ import annotations

from datetime import date

from app.services.research_eod_v1.algorithms import setup_a, setup_b, setup_c, setup_d
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.factors import extract_raw
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close
from app.services.research_eod_v1.residual import ResidualMomentum


GATES = {"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2,
         "breakout_rvol_min": 1.25, "breakout_buffer_atr": 0.15,
         "breakout_buffer_price_fraction": 0.0025, "pullback_depth_max_atr": 3.0}


def _raw(**overrides):
    days = trading_days(date(2018, 1, 2), 260)
    close = trending_close(260, 40, 0.12)
    series = make_series("AAA", days, close)
    raw = extract_raw(series, market=series, panel={"AAA": series}, horizon="mid",
                      momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    for key, value in overrides.items():
        setattr(raw, key, value)
    return raw


def test_family_a_requires_trend_and_positive_momentum() -> None:
    raw = _raw(t_direction_ok=False, lh_ll_unrepaired=False)
    raw.momentum_raw_blend["mid"] = 0.1
    assert setup_a(raw, "mid").passed is False
    raw.t_direction_ok = True
    raw.momentum_raw_blend["mid"] = -0.01
    assert "NONPOSITIVE_MOMENTUM" in setup_a(raw, "mid").reasons


def test_family_b_without_frozen_base_is_not_eligible() -> None:
    raw = _raw(frozen_setup=None, b_status="no_base_observed")
    decision = setup_b(raw, {"rvol_multiplier": 1.0}, GATES)
    assert decision.passed is False
    assert decision.state == "forming_base"


def test_family_c_rejects_broken_support_and_shallow_depth() -> None:
    raw = _raw(t_direction_ok=True, structure_invalidated=True, p_depth=0.2, rebound=True, clv=0.7, rvol=1.0)
    assert "SUPPORT_BROKEN" in setup_c(raw, GATES).reasons
    raw.structure_invalidated = False
    assert "DEPTH_OUT_OF_RANGE" in setup_c(raw, GATES).reasons


def test_family_b_requires_profile_confirm_closes() -> None:
    raw = _raw(
        frozen_setup={"resistance_high": 10.0, "setup_id": "AAA:frozen"},
        b_status="observed",
        sma50=20.0,
        sma50_prev20=19.0,
        above_sma50=True,
        extension_atr=0.4,
        platform_distance_atr=0.4,
        breakout_track={
            "through": True,
            "still_through": True,
            "tracking_expired": False,
            "max_consecutive": 1,
            "current_consecutive_closes": 1,
            "consecutive_closes": 1,
            "first_day_rvol": 2.0,
            "first_day_clv": 0.8,
        },
    )
    conservative = setup_b(raw, {"rvol_multiplier": 1.0, "confirm_closes": 2}, GATES)
    assert conservative.passed is False
    assert conservative.state == "breakout_confirming"
    aggressive = setup_b(raw, {"rvol_multiplier": 1.0, "confirm_closes": 1}, GATES)
    assert aggressive.passed is True


def test_family_b_separates_platform_distance_from_ma_distance() -> None:
    raw = _raw(
        frozen_setup={"resistance_high": 10.0, "setup_id": "AAA:frozen"},
        b_status="observed",
        sma50=20.0,
        sma50_prev20=19.0,
        above_sma50=True,
        platform_distance_atr=3.1,
        ma_distance_atr=0.4,
        breakout_track={
            "through": True,
            "still_through": True,
            "tracking_expired": False,
            "current_consecutive_closes": 2,
            "consecutive_closes": 2,
            "first_day_rvol": 2.0,
            "first_day_clv": 0.8,
        },
    )
    far_base = setup_b(raw, {"rvol_multiplier": 1.0, "confirm_closes": 1}, GATES)
    assert "TOO_FAR_FROM_BASE" in far_base.reasons
    assert "TOO_FAR_FROM_MA" not in far_base.reasons
    raw.platform_distance_atr = 0.4
    raw.ma_distance_atr = 9.0
    gates = {**GATES, "ma_distance_max_atr": 2.0}
    far_ma = setup_b(raw, {"rvol_multiplier": 1.0, "confirm_closes": 1}, gates)
    assert "TOO_FAR_FROM_MA" in far_ma.reasons
    assert "TOO_FAR_FROM_BASE" not in far_ma.reasons


def test_family_d_cannot_fall_back_to_ordinary_momentum() -> None:
    raw = _raw()
    raw.residual = ResidualMomentum(None, "INSUFFICIENT_MATCHED_BENCHMARK")
    raw.above_sma50 = True
    raw.structure_score = 80
    decision = setup_d(raw, load_registry()["profiles"]["balanced"])
    assert decision.passed is False
    assert "INSUFFICIENT_MATCHED_BENCHMARK" in decision.reasons
