"""Returns that compare a stock and its benchmark over the same sessions."""

from __future__ import annotations

import math

import pandas as pd


def aligned_benchmark_return(
    stock_close: pd.Series,
    benchmark_close: pd.Series,
    days: int,
) -> float | None:
    """Use the stock's exact window endpoints; never substitute a nearby bar.

    Daily providers may use naive session dates or timezone-aware timestamps.
    Match those by the New York trading date, while retaining ordinary index
    labels for callers that supply positional fixture series.
    """

    if days <= 0 or len(stock_close) <= days or benchmark_close.empty:
        return None
    stock_index = stock_close.index
    benchmark_index = benchmark_close.index
    if isinstance(stock_index, pd.DatetimeIndex) and isinstance(benchmark_index, pd.DatetimeIndex):
        if stock_index.tz is not None:
            stock_index = stock_index.tz_convert("America/New_York")
        if benchmark_index.tz is not None:
            benchmark_index = benchmark_index.tz_convert("America/New_York")
        stock_index = pd.Index(stock_index.date)
        benchmark_index = pd.Index(benchmark_index.date)
    if not benchmark_index.is_unique:
        return None
    endpoints = pd.Series(benchmark_close.to_numpy(), index=benchmark_index).reindex(
        [stock_index[-(days + 1)], stock_index[-1]]
    )
    try:
        base, current = (float(value) for value in endpoints)
    except (TypeError, ValueError, OverflowError):
        return None
    if not all(math.isfinite(value) and value > 0 for value in (base, current)):
        return None
    result = current / base - 1
    return result if math.isfinite(result) else None
