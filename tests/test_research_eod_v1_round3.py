from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.research_eod_v1 import FEATURE_VERSION, factors
from app.services.research_eod_v1.calendar_asof import (
    last_complete_eod_session,
    last_known_finalized_session,
    session_close_at,
)
from app.services.research_eod_v1.composite import m1_consensus
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.data.capture_store import (
    COMPLETE_EOD,
    INVALID_EOD_CAPTURE,
    RECAPTURE_AFTER_CLOSE,
    VENDOR_LATE,
    ImmutableCaptureStore,
)
from app.services.research_eod_v1.data.contract import ResearchBar, hash_file_bytes
from app.services.research_eod_v1.data.local_parquet import LocalParquetProvider
from app.services.research_eod_v1.data.to_series import bars_to_series
from app.services.research_eod_v1.fixtures import as_of_after_close, make_series, trading_days, trending_close
from app.services.research_eod_v1.backtest import plan_trade
from app.services.research_eod_v1.ledger import simulate_ledger
from app.services.research_eod_v1.membership import has_complete_session_bar, source_is_available
from app.services.research_eod_v1.residual import residual_raw_momentum
from app.services.research_eod_v1.report_contract import summarize_signal_row
from app.services.research_eod_v1.snapshot import compute_snapshot


ET = ZoneInfo("America/New_York")


def _signal(sid: str, session: date, notional: float = 2000.0) -> dict:
    return {
        "security_id": sid,
        "session_date": session.isoformat(),
        "status": "eligible",
        "score": 90,
        "adv20": 80_000_000,
        "notional": notional,
    }


def test_feature_version_bumped_for_round3() -> None:
    assert FEATURE_VERSION == "us-eod-research-features-v1.5"


def test_residual_short_ipo_is_not_ok() -> None:
    days = trading_days(date(2018, 1, 2), 700)
    market = make_series("SPY", days, trending_close(700, 200, 0.05), asset_track="etf", security_type="ETF")
    ipo = make_series("IPO", days[-200:], trending_close(200, 30, 0.08), industry_id=None)
    short = residual_raw_momentum(ipo, market, {"IPO": ipo, "SPY": market})
    assert short.status in {"SHORT_HISTORY", "UNALIGNED_BENCHMARK"}
    assert short.raw is None


def test_residual_internal_gap_does_not_treat_skip_as_one_day() -> None:
    days = trading_days(date(2018, 1, 2), 500)
    market = make_series("SPY", days, trending_close(500, 200, 0.05), asset_track="etf", security_type="ETF", industry_id=None)
    gapped_days = days[:250] + days[251:]
    gapped = make_series("GAP", gapped_days, trending_close(len(gapped_days), 40, 0.06), industry_id=None)
    aligned = make_series("GAP", days, trending_close(500, 40, 0.06), industry_id=None)
    hole = residual_raw_momentum(gapped, market, {"GAP": gapped, "SPY": market})
    full = residual_raw_momentum(aligned, market, {"GAP": aligned, "SPY": market})
    assert hole.status in {
        "MISSING_DAY_RETURN",
        "UNALIGNED_BENCHMARK",
        "RESIDUAL_WINDOW_INVALID",
        "SHORT_HISTORY",
        "SINGULAR_OR_THIN_REGRESSION",
    }
    assert hole.status != "OK"
    if full.status == "OK" and hole.status == "OK":
        raise AssertionError("a missing interior session must not produce the same silent OK as a dense join")


def test_residual_unused_benchmark_prefix_seed_174() -> None:
    rng = np.random.default_rng(174)
    days = trading_days(date(2018, 1, 2), 700)
    rm = rng.normal(0.0003, 0.01, 700)
    pm = 100 * np.cumprod(1 + rm)
    ri = 1.3 * rm[-400:] + rng.normal(0.0004, 0.003, 400)
    pi = 30 * np.cumprod(1 + ri)
    stock = make_series("NEWER", days[-400:], pi, industry_id=None)
    full = make_series("SPY", days, pm, asset_track="etf", security_type="ETF")
    trimmed = make_series("SPY", days[-400:], pm[-400:], asset_track="etf", security_type="ETF")
    with_prefix = residual_raw_momentum(stock, full, {"NEWER": stock, "SPY": full})
    without = residual_raw_momentum(stock, trimmed, {"NEWER": stock, "SPY": trimmed})
    assert with_prefix.status == without.status == "OK"
    assert with_prefix.raw == pytest.approx(without.raw, abs=1e-10)


def test_residual_late_benchmark_uses_common_grid() -> None:
    rng = np.random.default_rng(17)
    days = trading_days(date(2018, 1, 2), 500)
    rm = rng.normal(0.0003, 0.01, 500)
    pm = 100 * np.cumprod(1 + rm)
    ri = 1.2 * rm[50:] + rng.normal(0.0002, 0.003, 450)
    pi = 40 * np.cumprod(1 + ri)
    stock = make_series("NEW", days[50:], pi, industry_id=None)
    late = make_series("SPY", days[50:], pm[50:], asset_track="etf", security_type="ETF")
    early = make_series("SPY", days, pm, asset_track="etf", security_type="ETF")
    a = residual_raw_momentum(stock, late, {"NEW": stock, "SPY": late})
    b = residual_raw_momentum(stock, early, {"NEW": stock, "SPY": early})
    assert a.status == b.status
    if a.status == "OK":
        assert a.raw == pytest.approx(b.raw, abs=1e-10)


def test_residual_peers_with_different_starts_still_align() -> None:
    days = trading_days(date(2018, 1, 2), 500)
    stock = make_series("AAA", days, trending_close(500, 40, 0.08), industry_id="chips")
    spy = make_series("SPY", days, trending_close(500, 200, 0.05), asset_track="etf", security_type="ETF")
    peer_a = make_series("BBB", days[20:], trending_close(480, 30, 0.07), industry_id="chips")
    peer_b = make_series("CCC", days[40:], trending_close(460, 25, 0.06), industry_id="chips")
    out = residual_raw_momentum(stock, spy, {"AAA": stock, "SPY": spy, "BBB": peer_a, "CCC": peer_b})
    lone = residual_raw_momentum(
        make_series("AAA", days, trending_close(500, 40, 0.08), industry_id=None),
        spy,
        {"AAA": stock, "SPY": spy},
    )
    assert out.status in {"OK", "SINGULAR_OR_THIN_REGRESSION", "SHORT_HISTORY", "MISSING_DAY_RETURN"}
    if out.status == "OK" and lone.status == "OK":
        assert out.raw != pytest.approx(lone.raw, abs=1e-12)


def test_missing_raw_close_does_not_size_from_geometry_close() -> None:
    from app.services.research_eod_v1.backtest import simulate_portfolio

    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("RAW", days, np.full(12, 50.0))
    series.raw_close[:] = np.nan
    series.raw_open[:] = np.nan
    result = simulate_portfolio(
        [{"security_id": "RAW", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "atr": 1.0, "planned_invalidation": 40.0}],
        {"RAW": series},
        capital=10_000,
        holding_sessions=5,
        profile={"max_name_weight": 0.1, "max_adv_frac": 0.01, "risk_per_trade": 0.01},
    )
    assert not result["trades"]
    assert result["unfilled"] >= 1 or result.get("ending_equity") == 10_000


def test_halted_entry_session_is_rejected() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("AAA", days, np.full(12, 100.0))
    series.bar_halted = np.zeros(12, dtype=bool)
    series.bar_halted[2] = True
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": series},
        capital=10_000,
        holding_sessions=5,
        signals=[_signal("AAA", days[1])],
    )
    assert not result["trades"]
    assert any(event["note"] == "HALTED_SESSION" for event in result["events"])


def test_end_of_sample_halt_does_not_block_earlier_entry() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("AAA", days, np.full(12, 100.0))
    series.halted = True
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": series},
        capital=10_000,
        holding_sessions=5,
        signals=[_signal("AAA", days[1])],
    )
    assert result["trades"]
    assert result["trades"][0]["entry_session"] == days[2]


def test_unsized_orders_are_rejected() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("AAA", days, np.full(12, 100.0))
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": series},
        capital=10_000,
        holding_sessions=5,
        signals=[{"security_id": "AAA", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000}],
    )
    assert result["unfilled"] >= 1
    assert not result["trades"]


def test_acquisition_does_not_cash_out_the_next_day() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("TGT", days, np.full(12, 40.0))
    series.raw_open = np.full(12, 40.0)
    series.raw_close = np.full(12, 40.0)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"TGT": series},
        capital=10_000,
        holding_sessions=20,
        signals=[_signal("TGT", days[1], 2000)],
        cash_acquisitions=[{"security_id": "TGT", "effective_at": days[8], "known_at": days[8], "settlement_at": days[8], "price": 45.0}],
    )
    hold_day = next(row for row in result["daily_equity"] if row["session"] == days[3].isoformat())
    assert hold_day["positions"] == 1
    assert any(event["kind"] == "cash_acquisition" and event["session"] == days[8].isoformat() for event in result["events"])


def test_split_and_cash_dividend_same_day_keep_identity() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("MIX", days, np.full(12, 50.0))
    series.raw_open[:] = 100.0
    series.raw_close[:] = 100.0
    series.raw_open[6:] = 50.0
    series.raw_close[6:] = 50.0
    series.splits = ((days[6], 2.0),)
    series.dividends = ((days[6], 1.0),)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"MIX": series},
        capital=10_000,
        holding_sessions=8,
        cost_multiple=0,
        signals=[_signal("MIX", days[1], 5000)],
    )
    assert "split" in {event["kind"] for event in result["events"]}
    assert "dividend" in {event["kind"] for event in result["events"]}
    marked = [row for row in result["daily_equity"] if row["identity_ok"]]
    assert marked
    last = result["daily_equity"][-1]
    assert last["equity"] == pytest.approx(10_100.0)
    if result["trades"]:
        assert result["trades"][0]["net_return"] == pytest.approx(0.02)


def test_special_dividend_uses_dated_pay_event() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("SPC", days, np.full(12, 40.0))
    series.raw_open[:] = 40.0
    series.raw_close[:] = 40.0
    series.dividend_events = (
        {"ex_date": days[3], "pay_date": days[6], "amount": 2.0, "kind": "special"},
    )
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"SPC": series},
        capital=10_000,
        holding_sessions=8,
        cost_multiple=0,
        signals=[_signal("SPC", days[1], 2000)],
    )
    kinds = [event["kind"] for event in result["events"]]
    assert "dividend" in kinds
    assert "dividend_pay" in kinds
    ex_row = next(row for row in result["daily_equity"] if row["session"] == days[3].isoformat())
    pay_row = next(row for row in result["daily_equity"] if row["session"] == days[6].isoformat())
    assert ex_row["receivables"] == pytest.approx(100.0)
    assert pay_row["receivables"] == pytest.approx(0.0)
    assert pay_row["cash"] >= ex_row["cash"] + 99.0


def test_plan_trade_price_path_is_not_cashflow_through_a_split() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = make_series("SPL", days, np.full(12, 50.0))
    series.raw_open[:6] = 100.0
    series.raw_close[:6] = 100.0
    series.raw_open[6:] = 50.0
    series.raw_close[6:] = 50.0
    series.splits = ((days[6], 2.0),)
    planned = plan_trade(series, days[1], 5, adv20=80_000_000, cost_multiple=0)
    assert planned.gross_return == pytest.approx(-0.5)
    assert planned.net_return is None


def test_incomplete_exit_is_right_censored_or_deferred() -> None:
    days = trading_days(date(2021, 1, 4), 6)
    series = make_series("TAIL", days, np.full(6, 20.0))
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"TAIL": series},
        capital=10_000,
        holding_sessions=8,
        signals=[_signal("TAIL", days[1], 1000)],
    )
    assert result["right_censored"] or result["trades"] == [] or any(event["kind"] == "exit_deferred" for event in result["events"])


def test_future_bars_do_not_change_complete_session() -> None:
    days = trading_days(date(2018, 1, 2), 260)
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(260, 40, 0.1)),
        "SPY": make_series("SPY", days, trending_close(260, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    as_of = as_of_after_close(days[-21])
    first = compute_snapshot(as_of, panel, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality")
    future = deepcopy(panel)
    extra = trading_days(days[-20], 8)
    for series in future.values():
        add = trending_close(len(extra), start=float(series.close[-1]) + 8.0, drift=1.2)
        series.dates.extend(extra)
        series.close = np.concatenate([series.close, add])
        series.open = np.concatenate([series.open, add * 0.998])
        series.high = np.concatenate([series.high, add * 1.05])
        series.low = np.concatenate([series.low, add * 0.95])
        series.raw_close = np.concatenate([series.raw_close, add])
        series.raw_open = np.concatenate([series.raw_open, add * 0.998])
        series.volume = np.concatenate([series.volume, np.full(len(extra), 9_999_999)])
        series.dollar_volume = np.concatenate([series.dollar_volume, add * 9_999_999])
        series.tri = np.concatenate([series.tri, add])
    second = compute_snapshot(as_of, future, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality")
    assert first["session_date"] == second["session_date"] == days[-21].isoformat()
    assert first["rows"] == second["rows"]


def test_next_day_replay_keeps_prior_complete_session() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    session = days[-2]
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(80, 40, 0.1)),
        "SPY": make_series("SPY", days, trending_close(80, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    close = session_close_at(session)
    same_evening = close + timedelta(minutes=30)
    next_morning = datetime(days[-1].year, days[-1].month, days[-1].day, 10, 0, tzinfo=ET)
    first = compute_snapshot(same_evening, panel, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality")
    replay = compute_snapshot(next_morning, panel, "u", load_registry(), sector_id="semiconductors", algorithm="A_trend_quality", source_finalized_through=session)
    assert first["session_date"] == replay["session_date"] == session.isoformat()


def test_intraday_clock_cannot_select_same_day_session() -> None:
    clock = datetime(2026, 9, 16, 13, 24, tzinfo=ET)
    assert last_complete_eod_session(clock) == date(2026, 9, 15)


def test_after_close_without_vendor_proof_keeps_last_proven_session() -> None:
    clock = datetime(2026, 9, 16, 16, 9, tzinfo=ET)
    assert last_complete_eod_session(clock) == date(2026, 9, 16)
    assert last_known_finalized_session(clock, last_proven_finalized=date(2026, 9, 15)) == date(2026, 9, 15)


def test_early_close_holiday_and_dst_cutoffs() -> None:
    half = date(2024, 7, 3)
    assert last_complete_eod_session(datetime(2024, 7, 3, 12, 59, tzinfo=ET)) == date(2024, 7, 2)
    assert last_complete_eod_session(datetime(2024, 7, 3, 13, 0, tzinfo=ET)) == half
    labor = datetime(2026, 9, 7, 10, 0, tzinfo=ET)
    assert last_complete_eod_session(labor) == date(2026, 9, 4)
    before_dst = session_close_at(date(2026, 3, 6))
    after_dst = session_close_at(date(2026, 3, 9))
    assert before_dst.utcoffset() != after_dst.utcoffset()
    assert last_complete_eod_session(after_dst) == date(2026, 3, 9)


def test_failed_platform_events_include_later_base(monkeypatch) -> None:
    days = trading_days(date(2015, 1, 2), 500)
    closes = np.full(500, 100.0)
    closes[40:300] = 70.0
    closes[300:] = 195.0
    series = make_series("PLAT", days, closes)
    series.low = closes - 1
    series.high = closes + 1

    def geometry(_series, t, **kwargs):
        if t == 25:
            return 80.0, "observed", {"support": 90.0, "resistance_high": 100.0}
        if t >= 450:
            return 80.0, "observed", {"support": 190.0, "resistance_high": 200.0}
        return 0.0, "no_base_observed", None

    monkeypatch.setattr(factors, "_base_geometry", geometry)
    _, status, setup = factors.resolve_frozen_setup(series, 499, min_sessions=20, max_sessions=80, min_touches=2)
    assert status == "observed"
    assert setup is not None
    assert setup["resistance_high"] == 200.0
    kinds = [event["kind"] for event in setup.get("events", [])]
    assert kinds.count("formed") >= 2
    assert "failed" in kinds
    assert setup["setup_id"] != f"PLAT:{days[24].isoformat()}:r100.0"


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


def test_failed_platform_can_repair_before_a_new_base_forms(monkeypatch) -> None:
    days = trading_days(date(2018, 1, 2), 80)
    closes = np.full(80, 100.0)
    closes[40:43] = 80.0
    closes[43:] = 95.0
    series = make_series("FIX", days, closes)
    series.low = closes - 1
    series.high = closes + 1

    def geometry(_series, t, **kwargs):
        if 25 <= t < 40:
            return 80.0, "observed", {"support": 90.0, "resistance_high": 100.0}
        return 0.0, "no_base_observed", None

    monkeypatch.setattr(factors, "_base_geometry", geometry)
    _, status, setup = factors.resolve_frozen_setup(series, 50, min_sessions=20, max_sessions=80, min_touches=2)
    assert status == "observed"
    assert setup is not None
    kinds = [event["kind"] for event in setup.get("events", [])]
    assert "failed" in kinds
    assert "repaired" in kinds
    assert setup["setup_id"] == f"FIX:{days[24].isoformat()}:r100.0"


def test_event_log_keeps_distinct_setup_ids_for_sequential_platforms(monkeypatch) -> None:
    days = trading_days(date(2015, 1, 2), 500)
    closes = np.full(500, 100.0)
    closes[40:300] = 70.0
    closes[300:] = 195.0
    series = make_series("TWO", days, closes)
    series.low = closes - 1
    series.high = closes + 1

    def geometry(_series, t, **kwargs):
        if t == 25:
            return 80.0, "observed", {"support": 90.0, "resistance_high": 100.0}
        if t >= 450:
            return 80.0, "observed", {"support": 190.0, "resistance_high": 200.0}
        return 0.0, "no_base_observed", None

    monkeypatch.setattr(factors, "_base_geometry", geometry)
    _, _, setup = factors.resolve_frozen_setup(series, 499, min_sessions=20, max_sessions=80, min_touches=2)
    assert setup is not None
    ids = {event["setup_id"] for event in setup.get("events", [])}
    assert len(ids) >= 2


def test_wide_old_platform_expires_by_age_so_later_base_can_form(monkeypatch) -> None:
    days = trading_days(date(2015, 1, 2), 500)
    closes = np.linspace(100.0, 108.0, 500)
    series = make_series("OLD", days, closes)
    series.low = closes - 1
    series.high = closes + 1

    def geometry(_series, t, **kwargs):
        if t == 25:
            return 80.0, "observed", {"support": 40.0, "resistance_high": 200.0}
        if t >= 400:
            return 80.0, "observed", {"support": 104.0, "resistance_high": 110.0}
        return 0.0, "no_base_observed", None

    monkeypatch.setattr(factors, "_base_geometry", geometry)
    _, status, setup = factors.resolve_frozen_setup(series, 499, min_sessions=20, max_sessions=80, min_touches=2)
    assert status == "observed"
    assert setup is not None
    assert setup["resistance_high"] == 110.0
    kinds = [event["kind"] for event in setup.get("events", [])]
    assert "expired" in kinds
    assert kinds.count("formed") >= 2


def test_two_distinct_platforms_can_be_live_at_once(monkeypatch) -> None:
    days = trading_days(date(2018, 1, 2), 160)
    closes = np.full(160, 95.0)
    series = make_series("BOTH", days, closes)
    series.low = closes - 1
    series.high = closes + 1

    def geometry(_series, t, **kwargs):
        if t == 80:
            return 70.0, "observed", {"support": 90.0, "resistance_high": 100.0}
        if t == 120:
            return 85.0, "observed", {"support": 80.0, "resistance_high": 110.0}
        return 0.0, "no_base_observed", None

    monkeypatch.setattr(factors, "_base_geometry", geometry)
    score, status, setup = factors.resolve_frozen_setup(series, 150, min_sessions=20, max_sessions=80, min_touches=2)
    assert status == "observed"
    assert setup is not None
    concurrent = setup.get("concurrent_setups") or []
    assert len(concurrent) >= 2
    ids = {item["setup_id"] for item in concurrent}
    assert len(ids) >= 2
    assert setup["resistance_high"] == 110.0
    assert score == 85.0
    kinds = [event["kind"] for event in setup.get("events", [])]
    assert kinds.count("formed") >= 2


def test_first_cross_is_frozen_after_known_at() -> None:
    days, series, t_index = _platform_series(50, extra=4)
    raw_t = factors.extract_raw(
        series.slice_through(days[t_index]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2, "breakout_buffer_price_fraction": 0.0025, "breakout_buffer_atr": 0.15},
    )
    first = raw_t.breakout_track["first_cross_date"]
    series.close[t_index + 3] = 110.0
    series.high[t_index + 3] = 111.0
    later = factors.extract_raw(
        series.slice_through(days[t_index + 3]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index + 3])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2, "breakout_buffer_price_fraction": 0.0025, "breakout_buffer_atr": 0.15},
    )
    assert later.breakout_track["first_cross_date"] == first
    series.close[t_index + 2] = 99.0
    series.high[t_index + 2] = 99.5
    series.low[t_index + 2] = 98.5
    series.close[t_index + 3] = 110.0
    series.high[t_index + 3] = 111.0
    rebreak = factors.extract_raw(
        series.slice_through(days[t_index + 3]),
        market=series,
        panel={"PLAT": series.slice_through(days[t_index + 3])},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2, "breakout_buffer_price_fraction": 0.0025, "breakout_buffer_atr": 0.15},
    )
    assert rebreak.breakout_track["first_cross_date"] == first


def test_m1_duplicate_theme_rows_are_idempotent() -> None:
    row = {
        "security_id": "AAA",
        "algorithm_id": "A_trend_quality",
        "score": 90,
        "status": "eligible",
        "primary_industry_id": "tech",
        "sector_context": "semiconductors",
        "session_date": "2024-01-02",
        "horizon": "mid",
        "profile": "balanced",
        "factors": {"R": 80.0, "G": 70.0},
    }
    other = {**row, "algorithm_id": "D_residual_momentum", "score": 88}
    first = m1_consensus([row, other], "balanced", 10)
    polluted = m1_consensus([row, {**row, "score": 40}, other], "balanced", 10)
    assert [item["security_id"] for item in first] == [item["security_id"] for item in polluted]


def test_reconstruction_does_not_use_download_time_to_block_history() -> None:
    days = trading_days(date(2020, 1, 2), 40)
    retrieved = datetime(2026, 9, 16, 13, 24, tzinfo=ET)
    bars = [
        ResearchBar(
            security_id="AAA",
            session_date=day,
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            raw_open=10.0,
            raw_close=10.5,
            volume=1000.0,
            dollar_volume=10500.0,
            tri=10.5,
            economic_known_at=session_close_at(day),
            retrieved_at=retrieved,
            vintage_status="download_time_not_pit",
        )
        for day in days
    ]
    series = bars_to_series(bars, security_id="AAA", asset_track="stock")
    assert series is not None
    assert series.source_available_at is None
    mid = days[20]
    sliced = series.slice_through(mid)
    assert sliced is not None
    as_of = session_close_at(mid) + timedelta(minutes=30)
    assert source_is_available(sliced, as_of)
    assert has_complete_session_bar(sliced, mid)


def test_partial_bar_is_not_a_complete_session() -> None:
    day = date(2026, 9, 16)
    bar = ResearchBar(
        security_id="AAA",
        session_date=day,
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.5,
        raw_open=10.0,
        raw_close=10.5,
        volume=1000.0,
        dollar_volume=10500.0,
        tri=10.5,
        vintage_status="PARTIAL",
        partial=True,
    )
    series = bars_to_series([bar], security_id="AAA", asset_track="stock")
    assert series is None or not has_complete_session_bar(series, day)


def test_offline_combined_parquet_roundtrip_hashes_bytes(tmp_path: Path) -> None:
    pytest.importorskip("pyarrow")
    import pandas as pd

    frame = pd.DataFrame(
        {
            "security_id": ["AAA", "SPY"],
            "session_date": ["2024-01-02", "2024-01-02"],
            "open": [10.0, 400.0],
            "high": [11.0, 402.0],
            "low": [9.0, 399.0],
            "close": [10.5, 401.0],
            "raw_open": [10.0, 400.0],
            "raw_close": [10.5, 401.0],
            "volume": [1000.0, 1_000_000.0],
            "tri": [10.5, 401.0],
        }
    )
    path = tmp_path / "daily_bars.parquet"
    frame.to_parquet(path, index=False)
    provider = LocalParquetProvider(tmp_path)
    rows = provider.fetch_daily_bars("AAA", date(2024, 1, 2), date(2024, 1, 3))
    assert len(rows) == 1
    assert rows[0].high == 11.0
    meta = provider.export_snapshot(str(tmp_path / "snapshot.txt"))
    assert meta.content_sha256 == hash_file_bytes([path])
    assert meta.content_sha256 != hash_file_bytes([])


def _capture_bar(
    sid: str,
    session: date,
    *,
    close: float = 10.5,
    retrieved=None,
    partial: bool = False,
) -> ResearchBar:
    return ResearchBar(
        security_id=sid,
        session_date=session,
        open=10.0,
        high=11.0,
        low=9.0,
        close=close,
        raw_open=10.0,
        raw_close=close,
        volume=1000.0,
        dollar_volume=close * 1000.0,
        tri=close,
        retrieved_at=retrieved,
        vintage_status="PARTIAL" if partial else "download_time_not_pit",
        partial=partial,
    )


def test_intraday_capture_claiming_same_day_is_invalid_eod() -> None:
    clock = datetime(2026, 9, 16, 13, 24, 41, tzinfo=ET)
    store = ImmutableCaptureStore()
    version = store.record_capture(
        clock=clock,
        bars=(
            _capture_bar("AAA", date(2026, 9, 15), close=10.0),
            _capture_bar("AAA", date(2026, 9, 16), close=10.4, partial=True),
        ),
        claimed_session=date(2026, 9, 16),
    )
    assert version.eod_status == INVALID_EOD_CAPTURE
    assert version.last_complete_eod_session == date(2026, 9, 15)
    assert date(2026, 9, 16) in version.isolated_partial_sessions
    assert version.bars[-1].partial
    assert version.bars[-1].retrieved_at == clock


def test_regular_close_and_vendor_late_capture_status() -> None:
    store = ImmutableCaptureStore()
    at_close = datetime(2026, 9, 16, 16, 0, tzinfo=ET)
    complete = store.record_capture(
        clock=at_close,
        bars=(_capture_bar("AAA", date(2026, 9, 16), close=10.8),),
        claimed_session=date(2026, 9, 16),
    )
    assert complete.eod_status == COMPLETE_EOD
    assert complete.last_complete_eod_session == date(2026, 9, 16)
    late = store.record_capture(
        clock=datetime(2026, 9, 16, 17, 0, tzinfo=ET),
        bars=(_capture_bar("AAA", date(2026, 9, 15), close=10.0),),
        claimed_session=date(2026, 9, 15),
        source_finalized_through=date(2026, 9, 15),
    )
    assert late.eod_status == VENDOR_LATE
    assert late.last_complete_eod_session == date(2026, 9, 15)


def test_recapture_after_close_is_new_version_and_does_not_mutate_old_retrieved_at() -> None:
    store = ImmutableCaptureStore()
    first_clock = datetime(2026, 9, 16, 13, 24, 41, tzinfo=ET)
    first = store.record_capture(
        clock=first_clock,
        bars=(
            _capture_bar("AAA", date(2026, 9, 15), close=10.0),
            _capture_bar("AAA", date(2026, 9, 16), close=10.4, partial=True),
        ),
        claimed_session=date(2026, 9, 16),
    )
    after_close = datetime(2026, 9, 16, 16, 30, tzinfo=ET)
    second = store.recapture_last_bar(
        predecessor_id=first.capture_id,
        clock=after_close,
        bars=(
            _capture_bar("AAA", date(2026, 9, 15), close=99.0),
            _capture_bar("AAA", date(2026, 9, 16), close=11.2),
        ),
        claimed_session=date(2026, 9, 16),
        source_finalized_through=date(2026, 9, 16),
    )
    stored_first = store.get(first.capture_id)
    assert second.capture_id != first.capture_id
    assert stored_first.retrieved_at == first_clock
    assert stored_first.content_sha256 == first.content_sha256
    assert stored_first.bars == first.bars
    assert stored_first.eod_status == INVALID_EOD_CAPTURE
    prior = next(bar for bar in stored_first.bars if bar.session_date == date(2026, 9, 16))
    assert prior.partial and prior.close == 10.4
    hist = next(bar for bar in second.bars if bar.session_date == date(2026, 9, 15))
    assert hist.retrieved_at == first_clock
    assert hist.close == 10.0
    last = next(bar for bar in second.bars if bar.session_date == date(2026, 9, 16))
    assert last.retrieved_at == after_close
    assert last.close == 11.2
    assert last.vintage_status == RECAPTURE_AFTER_CLOSE
    assert not last.partial
    assert second.eod_status == COMPLETE_EOD
    assert second.last_complete_eod_session == date(2026, 9, 16)
    with pytest.raises(TypeError):
        store.rewrite_retrieved_at(first.capture_id, after_close)
    with pytest.raises(TypeError):
        store.overwrite(first.capture_id, retrieved_at=after_close)
    with pytest.raises(Exception):
        stored_first.retrieved_at = after_close  # type: ignore[misc]


def test_next_day_replay_and_future_bars_do_not_mutate_prior_capture() -> None:
    store = ImmutableCaptureStore()
    evening = datetime(2026, 9, 15, 16, 30, tzinfo=ET)
    first = store.record_capture(
        clock=evening,
        bars=(_capture_bar("AAA", date(2026, 9, 15), close=10.0),),
        claimed_session=date(2026, 9, 15),
    )
    next_morning = datetime(2026, 9, 16, 10, 0, tzinfo=ET)
    replay = store.recapture_last_bar(
        predecessor_id=first.capture_id,
        clock=next_morning,
        bars=(
            _capture_bar("AAA", date(2026, 9, 15), close=10.0),
            _capture_bar("AAA", date(2026, 9, 16), close=10.6, partial=True),
        ),
        claimed_session=date(2026, 9, 15),
        source_finalized_through=date(2026, 9, 15),
    )
    stored = store.get(first.capture_id)
    assert stored.retrieved_at == evening
    assert stored.content_sha256 == first.content_sha256
    assert stored.last_complete_eod_session == date(2026, 9, 15)
    assert replay.last_complete_eod_session == date(2026, 9, 15)
    assert date(2026, 9, 16) in replay.isolated_partial_sessions
    later = store.record_capture(
        clock=datetime(2026, 9, 17, 17, 0, tzinfo=ET),
        bars=(
            _capture_bar("AAA", date(2026, 9, 15), close=10.0),
            _capture_bar("AAA", date(2026, 9, 16), close=11.0),
            _capture_bar("AAA", date(2026, 9, 17), close=12.0),
        ),
        claimed_session=date(2026, 9, 17),
        predecessor_id=first.capture_id,
    )
    assert store.get(first.capture_id).bars == first.bars
    assert store.get(first.capture_id).content_sha256 == first.content_sha256
    assert later.capture_id != first.capture_id
    assert any(bar.session_date == date(2026, 9, 17) for bar in later.bars)
    assert all(bar.session_date != date(2026, 9, 17) for bar in store.get(first.capture_id).bars)


def test_late_security_is_dropped_from_eod_pools() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    panel = {
        "NVDA": make_series("NVDA", days, trending_close(80, 40, 0.1)),
        "LATE": make_series("LATE", days, trending_close(80, 50, 0.1)),
        "SPY": make_series("SPY", days, trending_close(80, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    snap = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
        extra_members={"LATE"},
        late_securities=("LATE",),
    )
    assert "LATE" not in snap["candidate_ids"]
    assert "LATE" not in snap["reference_ids"]
    assert "LATE" in snap["late_securities"]


def test_snapshot_rows_carry_halt_and_adjustment_fields() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    series = make_series("NVDA", days, trending_close(80, 40, 0.1))
    series.halted = True
    series.price_adjustment = tuple(["unverified"] * 80)
    series.volume_adjustment = tuple(["unverified"] * 80)
    series.vintage_status = tuple(["download_time_not_pit"] * 80)
    panel = {
        "NVDA": series,
        "SPY": make_series("SPY", days, trending_close(80, 200, 0.08), asset_track="etf", security_type="ETF"),
    }
    snap = compute_snapshot(
        as_of_after_close(days[-1]),
        panel,
        "u",
        load_registry(),
        sector_id="semiconductors",
        algorithm="A_trend_quality",
    )
    assert snap["rows"]
    row = next(item for item in snap["rows"] if item["security_id"] == "NVDA")
    assert row["halted"] is True
    assert row["currently_tradable"] is False
    assert row["price_adjustment"] == "unverified"
    assert row["vintage_status"] == "download_time_not_pit"


def test_later_halt_flag_does_not_rewrite_earlier_session() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    flagged = make_series("FLAG", days, trending_close(80, 40, 0.1))
    flagged.halted = True
    earlier_flag = flagged.slice_through(days[-5])
    assert earlier_flag is not None
    assert earlier_flag.halted is False
    series = make_series("NVDA", days, trending_close(80, 40, 0.1))
    series.halted = True
    series.bar_halted = np.zeros(80, dtype=bool)
    series.bar_halted[-1] = True
    earlier = series.slice_through(days[-5])
    assert earlier is not None
    assert earlier.halted is False
    assert earlier.bar_halted is not None
    assert not bool(earlier.bar_halted[-1])
    raw = factors.extract_raw(
        earlier,
        market=earlier,
        panel={"NVDA": earlier},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.halted is False
    assert raw.currently_tradable is True


def test_signal_report_keeps_halt_and_adjustment_fields() -> None:
    summary = summarize_signal_row(
        {
            "security_id": "AAA",
            "status": "rejected",
            "score": 10,
            "setup_state": "rejected",
            "rejection_reasons": ("NOT_TRADABLE",),
            "halted": True,
            "currently_tradable": False,
            "zero_volume": False,
            "price_adjustment": "yahoo_unverified_raw",
            "volume_adjustment": "yahoo_unverified",
            "vintage_status": "download_time_not_pit",
            "tri_verified": False,
            "identity_confidence": "unverified_default_not_checked",
            "industry_source": "theme_tag_diagnostic_not_economic_parent",
            "factors": {"M": 1.0, "T": 2.0},
        }
    )
    assert summary["halted"] is True
    assert summary["currently_tradable"] is False
    assert summary["price_adjustment"] == "yahoo_unverified_raw"
    assert summary["vintage_status"] == "download_time_not_pit"
    assert summary["identity_confidence"] == "unverified_default_not_checked"


def test_missing_volume_is_not_a_known_zero_or_halt() -> None:
    days = trading_days(date(2018, 1, 2), 80)
    series = make_series("NVDA", days, trending_close(80, 40, 0.1))
    series.volume[-1] = np.nan
    series.dollar_volume[-1] = np.nan
    raw = factors.extract_raw(
        series,
        market=series,
        panel={"NVDA": series},
        horizon="mid",
        momentum_blend=(0.25, 0.4, 0.35),
        sector_gates={"base_min_sessions": 20, "base_max_sessions": 80, "base_min_distinct_touches": 2},
    )
    assert raw.zero_volume is False
    assert raw.halted is False
