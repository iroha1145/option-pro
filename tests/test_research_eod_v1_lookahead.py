from __future__ import annotations

from copy import deepcopy

import numpy as np

from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.factors import extract_raw
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.snapshot import compute_snapshot


def _panel(n: int = 280):
    days = trading_days(__import__("datetime").date(2018, 1, 2), n)
    names = [f"S{i:02d}" for i in range(8)]
    panel = {}
    for i, name in enumerate(names):
        close = trending_close(n, start=40 + i, drift=0.12 + i * 0.01)
        panel[name] = make_series(name, days, close, industry_id="chips", parent_industry_id="tech")
    spy = trending_close(n, start=200, drift=0.08)
    panel["SPY"] = make_series("SPY", days, spy, industry_id="broad", parent_industry_id="broad", asset_track="etf", security_type="ETF")
    return days, panel


def test_appending_future_bars_does_not_change_t_raw_or_snapshot() -> None:
    days, panel = _panel()
    t_day = days[-21]
    as_of = as_of_after_close(t_day)
    clipped = {k: v.slice_through(t_day) for k, v in panel.items()}
    clipped = {k: v for k, v in clipped.items() if v is not None}
    future = deepcopy(clipped)
    extra = trading_days(days[-20], 20)
    for sid, series in future.items():
        add = trending_close(20, start=float(series.close[-1]) + 5.0, drift=0.8)
        series.dates.extend(extra)
        series.close = np.concatenate([series.close, add])
        series.open = np.concatenate([series.open, add * 0.998])
        series.high = np.concatenate([series.high, add * 1.05])
        series.low = np.concatenate([series.low, add * 0.95])
        series.raw_close = np.concatenate([series.raw_close, add])
        series.volume = np.concatenate([series.volume, np.full(20, 9_999_999)])
        series.dollar_volume = np.concatenate([series.dollar_volume, add * 9_999_999])
        series.tri = np.concatenate([series.tri, add])
    registry = load_registry()
    first = compute_snapshot(as_of, clipped, "u-test", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    second = compute_snapshot(as_of, future, "u-test", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    assert first["session_date"] == second["session_date"] == t_day.isoformat()
    f_map = {row["security_id"]: row["factors"] for row in first["rows"]}
    s_map = {row["security_id"]: row["factors"] for row in second["rows"]}
    assert f_map == s_map
    raw_a = extract_raw(
        clipped["S00"],
        market=clipped["SPY"],
        panel=clipped,
        horizon="mid",
        momentum_blend=(0.25, 0.40, 0.35),
        sector_gates=registry["sectors"]["semiconductors"]["gates"],
    )
    raw_b = extract_raw(
        future["S00"].slice_through(t_day),
        market=future["SPY"].slice_through(t_day),
        panel={k: v.slice_through(t_day) for k, v in future.items() if v.slice_through(t_day)},
        horizon="mid",
        momentum_blend=(0.25, 0.40, 0.35),
        sector_gates=registry["sectors"]["semiconductors"]["gates"],
    )
    assert raw_a.slope50 == raw_b.slope50
    assert raw_a.structure_score == raw_b.structure_score
    assert raw_a.pivots == raw_b.pivots


def test_unconfirmed_pivot_is_not_neutral_fifty() -> None:
    days = trading_days(__import__("datetime").date(2020, 1, 2), 50)
    close = np.linspace(10, 10.4, 50)
    series = make_series("FLAT", days, close)
    raw = extract_raw(
        series,
        market=series,
        panel={"FLAT": series},
        horizon="mid",
        momentum_blend=(0.25, 0.40, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.structure_score is None
    assert raw.structure_label == "insufficient"
