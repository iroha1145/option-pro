from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any


START = datetime(2026, 6, 1, 20, 0, tzinfo=timezone.utc)


def regime(kind: str = "bull", *, status: str = "active") -> dict[str, Any]:
    templates = {
        "bull": {
            "score": 76.0,
            "trend": 90.0,
            "momentum": 68.0,
            "breadth": 70.0,
            "risk_on": 72.0,
            "risk_appetite": 68.0,
            "above_50": True,
            "above_200": True,
            "slope_up": True,
            "spy_20d": 6.0,
            "drawdown": -1.0,
            "vix": 17.0,
        },
        "distribution": {
            "score": 47.0,
            "trend": 50.0,
            "momentum": 46.0,
            "breadth": 42.0,
            "risk_on": 43.0,
            "risk_appetite": 45.0,
            "above_50": False,
            "above_200": True,
            "slope_up": True,
            "spy_20d": -1.0,
            "drawdown": -4.0,
            "vix": 21.0,
        },
        "bear": {
            "score": 22.0,
            "trend": 18.0,
            "momentum": 24.0,
            "breadth": 25.0,
            "risk_on": 20.0,
            "risk_appetite": 24.0,
            "above_50": False,
            "above_200": False,
            "slope_up": False,
            "spy_20d": -12.0,
            "drawdown": -18.0,
            "vix": 36.0,
        },
    }
    item = templates[kind]
    return {
        "status": status,
        "score": item["score"],
        "index_trend_score": item["trend"],
        "market_momentum_score": item["momentum"],
        "market_breadth_score": item["breadth"],
        "market_volume_score": 58.0,
        "risk_on_spread_score": item["risk_on"],
        "risk_appetite_score": item["risk_appetite"],
        "trend": {
            "spy_above_sma50": item["above_50"],
            "spy_above_sma200": item["above_200"],
            "spy_sma200_slope_up": item["slope_up"],
        },
        "momentum": {"spy_20d": item["spy_20d"], "qqq_20d": item["spy_20d"]},
        "breadth": {"rsp_spy_20d": 1.0, "iwm_spy_20d": 0.5},
        "risk": {"spy_drawdown_50d": item["drawdown"], "vix": item["vix"]},
        "hard_missing": [],
        "optional_missing": [],
        "active_groups": [
            "core_trend",
            "core_momentum",
            "core_breadth",
            "market_volume",
            "volatility",
            "credit",
            "rates",
        ],
        "input_coverage": {"ratio": 1.0},
        "degraded_reasons": [],
        "warnings": [],
    }


def snapshot(kind: str, day: int, *, status: str = "active") -> dict[str, Any]:
    observed_at = START + timedelta(days=day)
    payload = deepcopy(regime(kind, status=status))
    payload["as_of"] = observed_at.isoformat()
    return {"as_of": observed_at, "regime": payload}
