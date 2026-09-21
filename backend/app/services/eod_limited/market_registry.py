"""Explicit live all-market extension; the sealed 24-theme registry stays intact."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from app.services.research_eod_v1.config_load import load_registry

ALL_MARKET_STOCKS = "all_market_stocks"


def load_market_registry() -> dict[str, Any]:
    """Validate the research registry first, then add the declared generic context."""
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
    return registry
