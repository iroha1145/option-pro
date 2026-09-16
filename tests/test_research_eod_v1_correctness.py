from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta

import numpy as np
from zoneinfo import ZoneInfo

from app.services.research_eod_v1.composite import m1_consensus, m2_utility, m3_diversified, m4_regime
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.factors import extract_raw, resolve_frozen_setup
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.membership import economic_identity, is_theme_candidate
from app.services.research_eod_v1.snapshot import compute_snapshot

ET = ZoneInfo("America/New_York")
GATES = {
    "base_min_sessions": 20,
    "base_max_sessions": 80,
    "base_min_distinct_touches": 2,
    "breakout_buffer_price_fraction": 0.0025,
    "breakout_buffer_atr": 0.15,
}


def _platform_close(n: int, t_index: int) -> np.ndarray:
    close = np.full(n, 100.0)
    close[t_index] = 102.0
    if t_index + 1 < n:
        close[t_index + 1] = 102.5
    return close


def _platform_series(n: int = 50, extra: int = 0):
    days = trading_days(date(2019, 1, 2), n + extra)
    t_index = n - 1
    close = _platform_close(n + extra, t_index)
    series = make_series("PLAT", days, close)
    high = np.full(n + extra, 100.2)
    low = np.full(n + extra, 99.8)
    high[t_index] = 103.0
    low[t_index] = 101.0
    if t_index + 1 < n + extra:
        high[t_index + 1] = 103.0
        low[t_index + 1] = 101.5
    series.high = high
    series.low = low
    series.open = close.copy()
    series.raw_close = close.copy()
    series.raw_open = close.copy()
    series.tri = close.copy()
    return days, series, t_index


def _row(sid: str, algo: str, score: float, industry: str = "tech", r: float = 70.0, g: float = 60.0, sector: str = "semiconductors") -> dict:
    return {
        "security_id": sid,
        "algorithm_id": algo,
        "score": score,
        "status": "eligible",
        "primary_industry_id": industry,
        "sector_context": sector,
        "factors": {"R": r, "G": g},
        "R": r,
        "G": g,
    }


def test_candidate_and_reference_pools_are_separated() -> None:
    days = trading_days(date(2018, 1, 2), 260)
    nvda = make_series("NVDA", days, trending_close(260, 40, 0.12), theme_ids=("semiconductors", "ai_cloud"))
    amd = make_series("AMD", days, trending_close(260, 30, 0.10), theme_ids=("semiconductors",))
    msft = make_series("MSFT", days, trending_close(260, 50, 0.08), theme_ids=("software", "ai_cloud"), industry_id="software")
    qqq = make_series("QQQ", days, trending_close(260, 200, 0.06), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    zzzz = make_series("ZZZZ", days, trending_close(260, 15, 0.05), theme_ids=(), industry_id="unknown")
    spy = make_series("SPY", days, trending_close(260, 210, 0.07), theme_ids=("etfs",), asset_track="etf", security_type="ETF")
    panel = {"NVDA": nvda, "AMD": amd, "MSFT": msft, "QQQ": qqq, "ZZZZ": zzzz, "SPY": spy}
    registry = load_registry()
    as_of = as_of_after_close(days[-1])
    semi = compute_snapshot(as_of, panel, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    soft = compute_snapshot(as_of, panel, "u", registry, sector_id="software", algorithm="A_trend_quality")
    etf = compute_snapshot(as_of, panel, "u", registry, sector_id="etfs", algorithm="A_trend_quality")
    assert set(semi["candidate_ids"]) == {"NVDA", "AMD"}
    assert "ZZZZ" not in semi["candidate_ids"]
    assert "QQQ" not in semi["candidate_ids"]
    assert "MSFT" not in semi["candidate_ids"]
    assert "SPY" in semi["reference_ids"]
    assert {row["security_id"] for row in semi["rows"]} <= {"NVDA", "AMD"}
    assert set(soft["candidate_ids"]) == {"MSFT"}
    assert "QQQ" in etf["candidate_ids"]
    assert "SPY" in etf["candidate_ids"]
    assert "NVDA" not in etf["candidate_ids"]
    extra = compute_snapshot(
        as_of, panel, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality", extra_members={"QQQ", "ZZZZ"}
    )
    assert "QQQ" not in extra["candidate_ids"]
    assert "ZZZZ" in extra["candidate_ids"]
    shuffled = make_series("NVDA", days, trending_close(260, 40, 0.12), theme_ids=("ai_cloud", "semiconductors"))
    assert economic_identity(shuffled) == economic_identity(nvda) == "NVDA"
    ok, reason = is_theme_candidate(qqq, sector_id="semiconductors", session=days[-1], target_track="stock", extra_members={"QQQ"})
    assert ok is False
    assert reason == "TRACK_MISMATCH"


def test_missing_t_bar_and_late_source_are_excluded() -> None:
    days = trading_days(date(2018, 1, 2), 260)
    nvda = make_series("NVDA", days, trending_close(260, 40, 0.12))
    amd = make_series("AMD", days[:-1], trending_close(259, 30, 0.10))
    spy = make_series("SPY", days, trending_close(260, 210, 0.07), asset_track="etf", security_type="ETF")
    late = make_series("AVGO", days, trending_close(260, 35, 0.09))
    late.source_available_at = as_of_after_close(days[-1]) + timedelta(hours=6)
    registry = load_registry()
    as_of = as_of_after_close(days[-1])
    payload = compute_snapshot(as_of, {"NVDA": nvda, "AMD": amd, "AVGO": late, "SPY": spy}, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    assert "AMD" not in payload["candidate_ids"]
    assert "AMD" not in payload["reference_ids"]
    assert "AVGO" not in payload["candidate_ids"]
    late.source_available_at = as_of
    available = compute_snapshot(as_of, {"NVDA": nvda, "AMD": amd, "AVGO": late, "SPY": spy}, "u", registry, sector_id="semiconductors", algorithm="A_trend_quality")
    assert "AVGO" in available["candidate_ids"]


def test_session_join_is_by_date_not_array_index() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    full = make_series("FULL", days, trending_close(80, 40, 0.1))
    hole = make_series("HOLE", days[:70] + days[71:], trending_close(79, 41, 0.1))
    raw_full = extract_raw(full, market=full, panel={"FULL": full}, horizon="mid", momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    raw_hole = extract_raw(hole, market=hole, panel={"HOLE": hole}, horizon="mid", momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    assert raw_full.session_date == days[-1]
    assert raw_hole.session_date == days[-1]
    assert len(full.dates) != len(hole.dates)


def test_frozen_platform_survives_later_highs_and_resets_consecutive() -> None:
    days, series, t_index = _platform_series(50, extra=3)
    raw_t = extract_raw(
        series.slice_through(days[t_index]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates=GATES,
    )
    raw_t1 = extract_raw(
        series.slice_through(days[t_index + 1]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index + 1])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates=GATES,
    )
    assert raw_t.frozen_setup is not None
    assert raw_t1.frozen_setup is not None
    assert raw_t.frozen_setup["resistance_high"] == raw_t1.frozen_setup["resistance_high"]
    assert raw_t.frozen_setup["resistance_high"] < 101.0
    assert raw_t1.frozen_setup["resistance_high"] != 103.0
    assert raw_t1.breakout_track["current_consecutive_closes"] == 2
    assert raw_t1.breakout_track["confirmed_at"] == days[t_index + 1].isoformat()
    # Drop then reclaim cannot look like two consecutive days.
    series.close[t_index + 2] = 99.0
    series.high[t_index + 2] = 100.0
    series.low[t_index + 2] = 98.5
    series.close[t_index + 3] = 102.5
    series.high[t_index + 3] = 103.0
    series.low[t_index + 3] = 101.0
    raw_reclaim = extract_raw(
        series.slice_through(days[t_index + 3]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index + 3])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates=GATES,
    )
    assert raw_reclaim.breakout_track["current_consecutive_closes"] == 1
    assert raw_reclaim.breakout_track["max_consecutive_closes"] >= 2
    first_id = raw_t.frozen_setup["setup_id"]
    raw_future = extract_raw(
        series,
        market=series,
        panel={"PLAT": series},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates=GATES,
    )
    assert raw_future.frozen_setup["setup_id"] == first_id
    score, status, setup = resolve_frozen_setup(series.slice_through(days[t_index + 1]), t_index + 1, min_sessions=20, max_sessions=80, min_touches=2)
    assert setup is not None
    assert setup["resistance_high"] < 101.0


def test_snapshot_row_schema_has_profile_horizon_adv20() -> None:
    import json
    from pathlib import Path

    days = trading_days(date(2018, 1, 2), 260)
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(260, 40, 0.1)),
        "SPY": make_series("SPY", days, trending_close(260, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    payload = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
    )
    assert payload["rows"]
    row = payload["rows"][0]
    schema = json.loads(Path("research/option_pro_us_eod_v1/schemas/snapshot_row.schema.json").read_text(encoding="utf-8"))
    for key in schema["required"]:
        assert key in row


def test_adv20_uses_twenty_bars_excluding_t() -> None:
    days = trading_days(date(2019, 1, 2), 40)
    close = np.full(40, 10.0)
    series = make_series("ADV", days, close)
    series.dollar_volume = np.arange(40, dtype=float)
    raw = extract_raw(series, market=series, panel={"ADV": series}, horizon="mid", momentum_blend=(0.25, 0.4, 0.35), sector_gates=GATES)
    expected = float(np.mean(np.arange(19, 39)))
    assert raw.adv20 == expected
    assert raw.rvol == float(series.dollar_volume[-1] / expected)
    assert raw.ma_distance_atr is not None or raw.history_sessions < 20


def test_composite_permutation_dedup_and_m3_skip() -> None:
    rows = [
        _row("AAA", "A_trend_quality", 95, r=40, g=20, sector="semiconductors"),
        _row("AAA", "A_trend_quality", 93, r=90, g=90, sector="ai_cloud"),
        _row("AAA", "D_residual_momentum", 92, r=90, g=90),
        _row("BBB", "A_trend_quality", 89, r=81, g=71),
        _row("CCC", "A_trend_quality", 87, r=82, g=72),
    ]
    first = m1_consensus(rows, "balanced", 10)
    second = m1_consensus(list(reversed(rows)), "balanced", 10)
    assert [row["security_id"] for row in first] == [row["security_id"] for row in second]
    assert first[0]["R"] == 77.5
    future = m2_utility(
        rows,
        "balanced",
        5,
        matured_returns={"AAA": {"returns": [0.01] * 100, "label_matured_at": date(2030, 1, 1)}},
        as_of=date(2024, 1, 1),
    )
    assert future == []
    immature = m2_utility(
        rows,
        "balanced",
        5,
        matured_returns={"AAA": [0.01] * 100},
        as_of=date(2024, 1, 1),
    )
    assert immature == []
    matured = m2_utility(
        rows,
        "balanced",
        5,
        matured_returns={"AAA": {"returns": [0.02] * 100, "label_matured_at": date(2023, 12, 1), "fold": "cal", "provenance": "fixture"}},
        as_of=date(2024, 1, 1),
    )
    assert matured and matured[0]["m2_status"] == "RESEARCH_POINT_ESTIMATE"
    corr = {("AAA", "BBB"): 0.95, ("AAA", "CCC"): 0.10, ("BBB", "CCC"): 0.20}
    selected = m3_diversified(rows, "balanced", 5, corr=corr)
    ids = [row["security_id"] for row in selected]
    assert ids[0] == "AAA"
    assert "BBB" not in ids
    assert "CCC" in ids
    missing = m3_diversified(rows, "balanced", 5, corr={})
    assert missing[0]["security_id"] == "AAA"
    assert len(missing) == 1
    last_theme = [
        _row("AAA", "A_trend_quality", 40, sector="software"),
        _row("AAA", "A_trend_quality", 92, sector="semiconductors"),
        _row("AAA", "B_confirmed_base_breakout", 90),
        _row("AAA", "C_trend_pullback", 90),
        _row("AAA", "D_residual_momentum", 100),
    ]
    bull = m4_regime(last_theme, "aggressive", 5, "bull")
    assert bull and bull[0]["security_id"] == "AAA"
    expected = 0.35 * 66.0 + 0.30 * 90.0 + 0.20 * 90.0 + 0.15 * 100.0
    assert abs(bull[0]["score"] - expected) < 1e-9
