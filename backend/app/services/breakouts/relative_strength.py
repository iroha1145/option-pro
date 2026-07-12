"""Point-in-time market and sector relative-strength features."""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _closes(frame: pd.DataFrame | None) -> pd.Series:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "Close" not in frame:
        return pd.Series(dtype=float)
    values = pd.to_numeric(frame["Close"], errors="coerce").dropna()
    values = values[~values.index.duplicated(keep="last")].sort_index()
    return values[values > 0]


def _excess_return(
    stock: pd.Series,
    benchmark: pd.Series,
    periods: int,
) -> float | None:
    aligned = pd.concat(
        [stock.rename("stock"), benchmark.rename("benchmark")],
        axis=1,
        join="inner",
    ).dropna()
    if len(aligned) <= periods:
        return None
    stock_return = aligned["stock"].iloc[-1] / aligned["stock"].iloc[-periods - 1] - 1.0
    benchmark_return = (
        aligned["benchmark"].iloc[-1]
        / aligned["benchmark"].iloc[-periods - 1]
        - 1.0
    )
    return _finite(stock_return - benchmark_return)


def _score(weighted_excess: float | None, multiplier: float) -> float | None:
    value = _finite(weighted_excess)
    if value is None:
        return None
    return round(max(0.0, min(100.0, 50.0 + value * multiplier)), 4)


def relative_strength_features(
    stock_frame: pd.DataFrame,
    market_frame: pd.DataFrame | None,
    sector_frame: pd.DataFrame | None = None,
    *,
    market_symbol: str = "SPY",
    sector_symbol: str | None = None,
    sector_breadth_score: float | None = None,
) -> dict[str, Any]:
    """Build auditable relative returns and the three production scores."""

    stock = _closes(stock_frame)
    market = _closes(market_frame)
    market_excess = {
        period: _excess_return(stock, market, period)
        for period in (5, 20, 63)
    }
    structure_inputs = [
        (market_excess[20], 0.4),
        (market_excess[63], 0.6),
    ]
    active_structure = [(value, weight) for value, weight in structure_inputs if value is not None]
    structure_excess = (
        sum(value * weight for value, weight in active_structure)
        / sum(weight for _, weight in active_structure)
        if active_structure
        else None
    )
    confirmation = _score(market_excess[5], 400.0)
    structure = _score(structure_excess, 250.0)

    sector = _closes(sector_frame)
    sector_excess = {
        period: _excess_return(stock, sector, period)
        for period in (20, 63)
    }
    sector_market_20 = _excess_return(sector, market, 20)
    sector_inputs = [
        (sector_excess[20], 0.35),
        (sector_excess[63], 0.25),
        (sector_market_20, 0.20),
        (_finite(sector_breadth_score / 250.0 - 0.2) if sector_breadth_score is not None else None, 0.20),
    ]
    active_sector = [(value, weight) for value, weight in sector_inputs if value is not None]
    sector_weighted = (
        sum(value * weight for value, weight in active_sector)
        / sum(weight for _, weight in active_sector)
        if sector_symbol and active_sector
        else None
    )
    sector_fit = _score(sector_weighted, 250.0)

    return {
        "relative_strength_structure": structure,
        "relative_strength_confirmation": confirmation,
        "sector_fit_score": sector_fit,
        "relative_strength_market_symbol": market_symbol if not market.empty else None,
        "relative_strength_sector_symbol": sector_symbol if not sector.empty else None,
        "relative_strength_market_excess_5d": market_excess[5],
        "relative_strength_market_excess_20d": market_excess[20],
        "relative_strength_market_excess_63d": market_excess[63],
        "relative_strength_sector_excess_20d": sector_excess[20],
        "relative_strength_sector_excess_63d": sector_excess[63],
        "sector_vs_market_excess_20d": sector_market_20,
        "sector_breadth_score": _finite(sector_breadth_score),
        "relative_strength_status": (
            "active" if structure is not None or confirmation is not None else "unavailable"
        ),
    }


def percentile_rank(value: Any, distribution: Mapping[str, Any]) -> float | None:
    """Mid-rank percentile against a fixed, versioned ticker universe."""

    target = _finite(value)
    values = sorted(
        number
        for number in (_finite(item) for item in distribution.values())
        if number is not None
    )
    if target is None or len(values) < 30:
        return None
    below = sum(item < target for item in values)
    equal = sum(item == target for item in values)
    return round((below + 0.5 * equal) / len(values) * 100.0, 4)
