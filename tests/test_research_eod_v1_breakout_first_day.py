"""B first-day rvol/clv must freeze and reject missing evidence."""

from __future__ import annotations

import math
from types import SimpleNamespace

from app.services.research_eod_v1 import FEATURE_VERSION
from app.services.research_eod_v1.algorithms import setup_b
from app.services.research_eod_v1.snapshot import _v_state


def _raw(**overrides):
    raw = SimpleNamespace(
        frozen_setup={"setup_id": "x", "resistance_high": 10.0},
        b_status="observed",
        sma50=99.0,
        sma50_prev20=98.0,
        above_sma50=True,
        rvol=2.0,
        clv=0.8,
        clv5=0.8,
        platform_distance_atr=0.4,
        ma_distance_atr=0.4,
        down_ratio=None,
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
    for key, value in overrides.items():
        setattr(raw, key, value)
    return raw


def test_feature_version_bumped_for_parent_and_first_day_fixes() -> None:
    assert FEATURE_VERSION == "us-eod-research-features-v1.6"


def test_b_v_state_keeps_frozen_first_day_rvol() -> None:
    expected = 50.0 + 30.0 * math.log(2.0)
    same = _raw(rvol=2.0)
    dropped = _raw(rvol=0.7)
    assert _v_state("B_confirmed_base_breakout", same) == expected
    assert _v_state("B_confirmed_base_breakout", dropped) == expected
    assert expected == 70.79441541679836


def test_b_v_state_current_day_rvol_does_not_replace_missing_first_day() -> None:
    raw = _raw(rvol=2.0, breakout_track={"first_day_rvol": None})
    assert _v_state("B_confirmed_base_breakout", raw) is None


def test_b_v_state_keeps_first_day_when_current_rvol_missing() -> None:
    expected = 50.0 + 30.0 * math.log(2.0)
    raw = _raw(rvol=None)
    assert _v_state("B_confirmed_base_breakout", raw) == expected
    assert expected == 70.79441541679836


def test_setup_b_rejects_nonfinite_first_day_inputs() -> None:
    track = {
        "through": True,
        "still_through": True,
        "tracking_expired": False,
        "current_consecutive_closes": 2,
        "consecutive_closes": 2,
        "first_day_rvol": float("nan"),
        "first_day_clv": float("inf"),
    }
    raw = _raw(rvol=2.0, clv=0.8, breakout_track=track)
    decision = setup_b(raw, {"confirm_closes": 1, "rvol_multiplier": 1.0}, {"breakout_rvol_min": 1.2})
    assert decision.passed is False
    assert "MISSING_FIRST_DAY_RVOL" in decision.reasons
    assert "MISSING_FIRST_DAY_CLV" in decision.reasons


def test_setup_b_rejects_missing_first_day_even_if_current_day_is_strong() -> None:
    track = {
        "through": True,
        "still_through": True,
        "tracking_expired": False,
        "current_consecutive_closes": 2,
        "consecutive_closes": 2,
        "first_day_rvol": None,
        "first_day_clv": None,
    }
    raw = _raw(rvol=2.0, clv=0.8, breakout_track=track)
    decision = setup_b(raw, {"confirm_closes": 1, "rvol_multiplier": 1.0}, {"breakout_rvol_min": 1.2})
    assert decision.passed is False
    assert "MISSING_FIRST_DAY_RVOL" in decision.reasons
    assert "MISSING_FIRST_DAY_CLV" in decision.reasons
    assert "LOW_EVENT_RVOL" not in decision.reasons
    assert "LOW_CLV" not in decision.reasons
