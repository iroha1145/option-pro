"""Explicit live all-market extension; the sealed 24-theme registry stays intact."""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Mapping

from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.constants import FACTORS

ALL_MARKET_STOCKS = "all_market_stocks"
# v1.6 leans the balanced and aggressive views from trend quality toward momentum:
# production-code replays of 2023-03 to 2026-06 improved the top-20 list on both the
# mixed and the stock-only list in both periods (research/option_pro_us_eod_v1/
# return_pack/full_market_v1_6). Conservative keeps the sealed tilt as the control.
LIVE_PROFILE_TILT_MULTIPLIERS = {
    "balanced": {"T": 0.5, "M": 2.0},
    "aggressive": {"T": 0.5, "M": 2.0},
}


def load_market_registry(*, extra_tilt_multipliers: Mapping[str, Mapping[str, float]] | None = None) -> dict[str, Any]:
    """Validate the research registry first, then add the declared generic context.

    ``extra_tilt_multipliers`` (``{profile: {factor: multiplier}}``) multiplies the
    profile tilts on top of the live v1.6 multipliers; v1.7 candidates use it for
    a G tilt and for the conservative redefinition. Omitted, the registry is v1.6.
    """
    registry = deepcopy(load_registry())
    registry["sectors"][ALL_MARKET_STOCKS] = {
        "name": "全市场通用股票",
        "asset_track": "stock",
        "configuration_source": "live_all_market_generic_v1",
        "candidates": {
            family: {
                "weights": dict(weights),
                "min_history_sessions": 330 if family == "D_residual_momentum" else 252,
            }
            for family, weights in registry["base_algorithm_weights"].items()
        },
        "gates": {
            "minimum_adv_usd": 20_000_000,
            "base_min_sessions": 20,
            "base_max_sessions": 80,
            "base_min_distinct_touches": 2,
            "breakout_rvol_min": 1.25,
            "breakout_buffer_atr": 0.15,
            "breakout_buffer_price_fraction": 0.0025,
            "pullback_depth_max_atr": 3.0,
            "event_policy": "earnings",
        },
    }
    for profile, multipliers in LIVE_PROFILE_TILT_MULTIPLIERS.items():
        tilt = registry["profiles"][profile]["factor_tilt"]
        for factor, multiplier in multipliers.items():
            tilt[FACTORS.index(factor)] *= multiplier
    registry["live_profile_tilt_multipliers"] = deepcopy(LIVE_PROFILE_TILT_MULTIPLIERS)
    if extra_tilt_multipliers:
        applied: dict[str, dict[str, float]] = {}
        for profile, multipliers in extra_tilt_multipliers.items():
            tilt = registry["profiles"][profile]["factor_tilt"]
            for factor, multiplier in multipliers.items():
                if factor not in FACTORS or not float(multiplier) > 0:
                    raise ValueError(f"tilt multiplier for {profile}/{factor} must be a positive factor multiplier")
                tilt[FACTORS.index(factor)] *= float(multiplier)
                applied.setdefault(profile, {})[factor] = float(multiplier)
        registry["v17_tilt_multipliers"] = applied
    return registry
