from __future__ import annotations

from datetime import date

import numpy as np

from app.services.research_eod_v1.fixtures import make_series, trading_days_ending
from app.services.research_eod_v1.residual import residual_raw_momentum


def _residual_pair(sessions: int, excess_window: tuple[int, int], skipped_drop: float = 0.0):
    """A stock that beats SPY by 0.4%/day inside ``excess_window`` and optionally slumps in the last month."""
    rng = np.random.default_rng(11)
    days = trading_days_ending(date(2026, 9, 25), sessions)
    market_returns = rng.normal(0.0004, 0.01, sessions)
    stock_returns = 1.2 * market_returns + rng.normal(0.0, 0.01, sessions)
    first, last = excess_window
    stock_returns[sessions - 1 - first: sessions - last] += 0.004
    if skipped_drop:
        stock_returns[sessions - 20:] -= skipped_drop
    market = make_series("SPY", days, 100 * np.cumprod(1 + market_returns), industry_id=None, asset_track="etf")
    stock = make_series("AAA", days, 50 * np.cumprod(1 + stock_returns), industry_id=None)
    return stock, market, {"SPY": market, "AAA": stock}


def test_residual_window_defaults_are_unchanged_when_passed_explicitly():
    stock, market, panel = _residual_pair(400, (251, 21))
    implicit = residual_raw_momentum(stock, market, panel)
    explicit = residual_raw_momentum(stock, market, panel, sum_start=67, sum_end=5, history_min=330)
    assert implicit == explicit and implicit.status == "OK"


def test_twelve_minus_one_residual_needs_a_long_window_and_skips_the_last_month():
    short_stock, short_market, short_panel = _residual_pair(400, (251, 21))
    assert residual_raw_momentum(short_stock, short_market, short_panel, sum_start=251, sum_end=21,
                                 history_min=505).status == "SHORT_HISTORY"

    stock, market, panel = _residual_pair(506, (251, 21), skipped_drop=0.02)
    twelve_one = residual_raw_momentum(stock, market, panel, sum_start=251, sum_end=21, history_min=505)
    current = residual_raw_momentum(stock, market, panel)
    assert twelve_one.status == "OK" and twelve_one.raw > 2.0
    assert current.status == "OK" and current.raw < twelve_one.raw
