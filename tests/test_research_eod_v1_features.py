from __future__ import annotations

import math
from datetime import date

import numpy as np

from app.services.research_eod_v1.factors import extract_raw
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close
from app.services.research_eod_v1.mathutil import clip100, max_drawdown_magnitude, midrank_percentiles
from app.services.research_eod_v1.pivots import find_confirmed_pivots
from app.services.research_eod_v1.residual import residual_raw_momentum


GATES = {"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2}


def test_single_observation_has_no_cross_section_rank() -> None:
    assert midrank_percentiles([7.0]) == [None]
    ranks = midrank_percentiles([1.0, 2.0, 2.0, 3.0])
    assert ranks[0] == 0.0
    assert ranks[-1] == 100.0


def test_max_drawdown_is_nonnegative_magnitude() -> None:
    tri = np.array([100.0, 120.0, 90.0, 95.0])
    assert math.isclose(max_drawdown_magnitude(tri), 0.25)


def test_clip100_and_missing_not_neutral() -> None:
    assert clip100(-5) == 0
    assert clip100(140) == 100
    assert clip100(None) is None


def test_pivot_confirmation_delay() -> None:
    days = trading_days(date(2020, 1, 2), 40)
    high = np.array([10 + i * 0.1 for i in range(40)])
    high[20] = 20.0
    low = high - 1.0
    highs, _ = find_confirmed_pivots(high, low, days, span=3, as_of_index=22)
    assert highs == []
    highs, _ = find_confirmed_pivots(high, low, days, span=3, as_of_index=23)
    assert highs and highs[-1].pivot_at == days[20]
    assert highs[-1].confirmed_at == days[23]


def test_no_base_is_zero_insufficient_is_null() -> None:
    days = trading_days(date(2019, 1, 2), 80)
    close = trending_close(80, start=30, drift=0.4)
    series = make_series("TREND", days, close)
    raw = extract_raw(series, market=series, panel={"TREND": series}, horizon="mid",
                      momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    assert raw.b_score == 0.0
    assert raw.b_status == "no_base_observed"
    short = make_series("SHORT", days[:10], close[:10])
    raw_short = extract_raw(short, market=short, panel={"SHORT": short}, horizon="mid",
                            momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    assert raw_short.b_score is None
    assert raw_short.b_status == "insufficient_history"


def test_split_adjusted_close_and_raw_close_are_separate() -> None:
    days = trading_days(date(2019, 1, 2), 70)
    adj = trending_close(70, 20, 0.1)
    series = make_series("SPLIT", days, adj)
    series.raw_close = adj * 2.0
    raw = extract_raw(series, market=series, panel={"SPLIT": series}, horizon="mid",
                      momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    assert raw.last_close is not None
    assert raw.raw_close is not None
    assert raw.raw_close == raw.last_close * 2.0


def test_residual_rejects_self_benchmark_and_short_history() -> None:
    days = trading_days(date(2015, 1, 2), 100)
    close = trending_close(100)
    series = make_series("AAA", days, close)
    out = residual_raw_momentum(series, series, {"AAA": series}, spy_residual_allowed=True)
    assert out.status == "INSUFFICIENT_MATCHED_BENCHMARK"
    gold = make_series("GLD", days, close, asset_track="etf", security_type="ETF")
    spy = make_series("SPY", days, close * 1.01, asset_track="etf", security_type="ETF")
    blocked = residual_raw_momentum(gold, spy, {"GLD": gold, "SPY": spy}, spy_residual_allowed=False)
    assert blocked.status == "INSUFFICIENT_MATCHED_BENCHMARK"
