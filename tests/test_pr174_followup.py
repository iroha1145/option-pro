"""PR #174 closeout follow-up regressions reconstructed from isolated_results.json.

Isolation ran 10 cases against an anchor snapshot (3 pass / 7 fail). These
tests keep the economic invariants. They do not copy isolation source blobs
over the current engine.

Run from the repository root:
    PYTHONPATH=backend pytest -q tests/test_pr174_followup.py
"""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import numpy as np
import pytest

from app.services.research_eod_v1.backtest import simulate_portfolio
from app.services.research_eod_v1.calendar_asof import (
    disclosed_source_finalized_through,
    next_session,
    VENDOR_WITHOUT_FINALIZED_FIELD_POLICY,
)
from app.services.research_eod_v1.data.capture_store import ImmutableCaptureStore
from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.fixtures import make_series, trading_days
from app.services.research_eod_v1.ledger import simulate_ledger
from app.services.research_eod_v1.residual import residual_raw_momentum


ET = ZoneInfo("America/New_York")
TEN_PCT = {
    "max_position_fraction": 0.10,
    "position_risk_budget_fraction": 1.0,
    "max_order_adv_fraction": 1.0,
}


def _flat(sid: str, days: list[date], price: float = 100.0):
    close = np.full(len(days), price)
    series = make_series(sid, days, close)
    series.open = close.copy()
    series.raw_open = close.copy()
    series.raw_close = close.copy()
    return series


def _signal(sid: str, session: date, notional: float = 2000.0, score: float = 90.0) -> dict:
    return {
        "security_id": sid,
        "session_date": session.isoformat(),
        "status": "eligible",
        "score": score,
        "adv20": 80_000_000,
        "notional": notional,
    }


def _capacity_signal(sid: str, session: date, score: float) -> dict:
    return {
        "security_id": sid,
        "session_date": session.isoformat(),
        "status": "eligible",
        "score": score,
        "planned_invalidation": 90.0,
        "atr": 1.0,
        "adv20": 80_000_000,
    }


def _buy_notional(result: dict, sid: str, session: date) -> float:
    session_s = session.isoformat()
    for event in result["events"]:
        if event["kind"] == "buy" and event["security_id"] == sid and event["session"] == session_s:
            return abs(float(event["cash_delta"]))
    raise AssertionError(f"missing buy {sid} {session_s}")


def test_control_partial_mark_preserves_other_known_positions() -> None:
    days = trading_days(date(2024, 1, 2), 12)
    a, b = _flat("A", days), _flat("B", days)
    mark_day = date(2024, 1, 4)
    a.close[days.index(mark_day)] = np.nan
    a.raw_close[days.index(mark_day)] = np.nan
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": a, "B": b},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 2)), _signal("B", date(2024, 1, 2))],
    )
    row = next(item for item in result["daily_equity"] if item["session"] == mark_day.isoformat())
    assert row["equity"] is None
    assert row["cash"] == pytest.approx(6000.0)
    assert row["receivables"] == pytest.approx(0.0)
    assert row["known_positions_value"] == pytest.approx(2000.0)
    assert row["partial_value"] == pytest.approx(8000.0)
    assert row["unknown_exposure"] == ["A"]
    assert row["positions"] == 2
    assert row["identity_ok"] is False


def test_control_raw_mark_and_split_cashflow() -> None:
    days = trading_days(date(2024, 1, 2), 12)
    series = _flat("SPL", days, 50.0)
    series.raw_open[:6] = 100.0
    series.raw_close[:6] = 100.0
    series.splits = ((days[6], 2.0),)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"SPL": series},
        capital=10_000.0,
        holding_sessions=5,
        cost_multiple=0,
        signals=[_signal("SPL", days[1], 5000.0)],
    )
    assert len(result["trades"]) == 1
    assert result["ending_equity"] == pytest.approx(10_000.0)
    assert result["trades"][0]["net_return"] == pytest.approx(0.0)


def test_control_residual_unused_prefix_is_invariant() -> None:
    rng = np.random.default_rng(174)
    days = trading_days(date(2018, 1, 2), 700)
    rm = rng.normal(0.0003, 0.01, 700)
    pm = 100 * np.cumprod(1 + rm)
    ri = 1.3 * rm[-400:] + rng.normal(0.0004, 0.003, 400)
    pi = 30 * np.cumprod(1 + ri)
    stock = make_series("NEWER", days[-400:], pi, industry_id=None)
    full = make_series("SPY", days, pm, asset_track="etf", security_type="ETF")
    trimmed = make_series("SPY", days[-400:], pm[-400:], asset_track="etf", security_type="ETF")
    long_b = residual_raw_momentum(stock, full, {"NEWER": stock, "SPY": full})
    trim_b = residual_raw_momentum(stock, trimmed, {"NEWER": stock, "SPY": trimmed})
    assert long_b.status == trim_b.status == "OK"
    assert long_b.raw == pytest.approx(trim_b.raw, abs=1e-10)
    # Isolated blob reported 0.22085; current residual on adjacent legal
    # sessions + common grid is 0.5396566315790488 (same as round-3 seed 174).


def test_deferred_exit_fills_at_first_resumed_open() -> None:
    calendar = trading_days(date(2024, 1, 2), 16)
    missing = date(2024, 1, 5)
    days = [day for day in calendar if day != missing]
    series = _flat("A", days)
    result = simulate_ledger(
        start=calendar[0],
        end=calendar[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=1,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 3), 2000.0)],
    )
    exits = [event for event in result["events"] if event["kind"] == "sell"]
    defers = [event for event in result["events"] if event["kind"] == "exit_deferred"]
    assert exits, "valid resumed opens exist but original missing exit is rechecked forever"
    assert exits[0]["session"] == date(2024, 1, 8).isoformat()
    assert any(event["session"] == missing.isoformat() for event in defers)
    assert result["trades"][0]["original_planned_exit"] == missing
    assert result["trades"][0]["filled_at"] == date(2024, 1, 8)
    assert result["trades"][0]["current_attempt"] == date(2024, 1, 8)


def test_dividend_paid_after_exit_belongs_to_original_trade() -> None:
    days = trading_days(date(2024, 1, 2), 12)
    series = _flat("A", days)
    series.dividend_events = (
        {"ex_date": date(2024, 1, 4), "pay_date": date(2024, 1, 8), "amount": 1.0},
    )
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=2,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 2), 2000.0)],
    )
    assert result["pnl_basis"] == "marked_equity"
    assert result["trade_pnl_basis"] == "settled_cash_including_lot_dividends"
    assert len(result["trades"]) == 1
    trade = result["trades"][0]
    assert trade["dividend_cash"] == pytest.approx(20.0)
    assert trade["net_return"] == pytest.approx(20.0 / 2000.0)
    assert result["ending_equity"] == pytest.approx(10_020.0)
    assert result["ending_equity"] != pytest.approx(10_000.0) or trade["net_return"] != pytest.approx(-0.01)


def test_future_signal_must_not_change_an_earlier_order() -> None:
    days = trading_days(date(2024, 1, 2), 16)
    panel = {"A": _flat("A", days), "B": _flat("B", days)}
    early = _capacity_signal("A", date(2024, 1, 2), score=50)
    future = _capacity_signal("B", date(2024, 1, 10), score=99)
    common = dict(
        panel=panel,
        capital=10_000.0,
        holding_sessions=1,
        profile=TEN_PCT,
        cost_multiple=0,
        start=days[0],
        end=days[-1],
    )
    without = simulate_portfolio([early], **common)
    with_future = simulate_portfolio([early, future], **common)
    past_without = _buy_notional(without, "A", date(2024, 1, 3))
    past_with = _buy_notional(with_future, "A", date(2024, 1, 3))
    assert past_without == pytest.approx(1000.0)
    assert past_with == pytest.approx(1000.0), f"past order {past_without} -> {past_with} after future append"


def test_halted_exit_must_not_fill_a_stale_raw_quote() -> None:
    days = trading_days(date(2024, 1, 2), 12)
    series = _flat("A", days)
    halted = date(2024, 1, 5)
    series.bar_halted = np.zeros(len(days), dtype=bool)
    series.bar_halted[days.index(halted)] = True
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=1,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 3), 2000.0)],
    )
    halted_sells = [
        event
        for event in result["events"]
        if event["kind"] == "sell" and event["session"] == halted.isoformat()
    ]
    assert not halted_sells, "sell executed on declared halted session"
    sells = [event for event in result["events"] if event["kind"] == "sell"]
    assert sells and sells[0]["session"] == date(2024, 1, 8).isoformat()
    assert any(
        event["kind"] == "exit_deferred" and event["note"] == "HALTED_SESSION"
        for event in result["events"]
    )


def test_old_dividend_cannot_be_credited_to_new_position() -> None:
    days = trading_days(date(2024, 1, 2), 14)
    series = _flat("A", days)
    series.dividend_events = (
        {"ex_date": date(2024, 1, 4), "pay_date": date(2024, 1, 10), "amount": 1.0},
    )
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=2,
        cost_multiple=0,
        signals=[
            _signal("A", date(2024, 1, 2), 2000.0, score=90),
            _signal("A", date(2024, 1, 8), 2000.0, score=80),
        ],
    )
    assert len(result["trades"]) == 2
    first, second = result["trades"]
    nets = [first["net_return"], second["net_return"]]
    assert first["dividend_cash"] == pytest.approx(20.0)
    assert second.get("dividend_cash", 0.0) == pytest.approx(0.0)
    assert nets[0] == pytest.approx(20.0 / 2000.0)
    assert nets[1] == pytest.approx(0.0), f"old entitlement attributed by ticker, not trade: {nets}"
    assert result["ending_equity"] == pytest.approx(10_020.0)


def test_released_cash_is_available_for_a_later_nonoverlap_trade() -> None:
    days = trading_days(date(2024, 1, 2), 14)
    panel = {"A": _flat("A", days), "B": _flat("B", days)}
    first = _capacity_signal("A", date(2024, 1, 2), score=90)
    later = _capacity_signal("B", date(2024, 1, 8), score=80)
    result = simulate_portfolio(
        [first, later],
        panel,
        capital=10_000.0,
        holding_sessions=1,
        profile=TEN_PCT,
        cost_multiple=0,
        start=days[0],
        end=days[-1],
    )
    amounts = [
        _buy_notional(result, "A", date(2024, 1, 3)),
        _buy_notional(result, "B", date(2024, 1, 9)),
    ]
    assert amounts[0] == pytest.approx(1000.0)
    assert amounts[1] == pytest.approx(1000.0), (
        f"flat, nonoverlap trades should have unchanged capacity; got {amounts}"
    )
    assert result["ending_equity"] == pytest.approx(10_000.0)


def test_residual_excluded_target_does_not_raise_keyerror() -> None:
    days = trading_days(date(2018, 1, 2), 400)
    stale = make_series("STALE", days, 20 + 0.05 * np.arange(400), industry_id="semiconductors")
    spy = make_series(
        "SPY",
        days,
        200 + 0.04 * np.arange(400),
        asset_track="etf",
        security_type="ETF",
        industry_id=None,
    )
    peer_a = make_series("NVDA", days, 40 + 0.08 * np.arange(400), industry_id="semiconductors")
    peer_b = make_series("AMD", days, 30 + 0.07 * np.arange(400), industry_id="semiconductors")
    panel = {"NVDA": peer_a, "AMD": peer_b, "SPY": spy}
    try:
        result = residual_raw_momentum(stale, spy, panel)
    except KeyError as exc:
        raise AssertionError(
            f"excluded target with long history crashes residual path: {exc}"
        ) from exc
    assert result.status != "OK" or result.raw is None or np.isfinite(result.raw)
    assert result.status in {
        "OK",
        "SHORT_HISTORY",
        "UNALIGNED_BENCHMARK",
        "MISSING_DAY_RETURN",
        "SINGULAR_OR_THIN_REGRESSION",
        "RESIDUAL_WINDOW_INVALID",
        "ZERO_RESIDUAL_VOL",
        "INSUFFICIENT_MATCHED_BENCHMARK",
    }


def test_multiday_halt_then_resume_and_end_sample_open() -> None:
    days = trading_days(date(2024, 1, 2), 10)
    series = _flat("A", days)
    series.bar_halted = np.zeros(len(days), dtype=bool)
    for halt_day in (date(2024, 1, 5), date(2024, 1, 8)):
        series.bar_halted[days.index(halt_day)] = True
    halted = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=1,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 3), 2000.0)],
    )
    sells = [event for event in halted["events"] if event["kind"] == "sell"]
    assert sells and sells[0]["session"] == date(2024, 1, 9).isoformat()
    assert sum(1 for event in halted["events"] if event["kind"] == "exit_deferred") >= 2

    open_end = simulate_ledger(
        start=days[0],
        end=date(2024, 1, 4),
        panel={"A": _flat("A", days)},
        capital=10_000.0,
        holding_sessions=5,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 2), 2000.0)],
    )
    assert not [event for event in open_end["events"] if event["kind"] == "sell"]
    assert open_end["open_at_end"]
    assert open_end["open_at_end"][0]["filled_at"] is None


def test_delist_terminal_does_not_fake_an_exit() -> None:
    days = trading_days(date(2024, 1, 2), 8)
    series = _flat("DEAD", days)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"DEAD": series},
        capital=10_000.0,
        holding_sessions=5,
        cost_multiple=0,
        signals=[_signal("DEAD", date(2024, 1, 2), 2000.0)],
        unknown_terminals={"DEAD": date(2024, 1, 5)},
    )
    assert "DEAD" in result["right_censored"]
    assert not [event for event in result["events"] if event["kind"] == "sell"]


def test_recapture_does_not_write_clock_as_vendor_finalized_at() -> None:
    first_clock = datetime(2026, 9, 16, 13, 24, 41, tzinfo=ET)
    after = datetime(2026, 9, 16, 16, 30, tzinfo=ET)
    bar = ResearchBar(
        security_id="AAA",
        session_date=date(2026, 9, 16),
        open=10.0,
        high=11.0,
        low=9.0,
        close=10.4,
        raw_open=10.0,
        raw_close=10.4,
        volume=1000.0,
        dollar_volume=10400.0,
        tri=10.4,
        partial=True,
        vintage_status="PARTIAL",
    )
    store = ImmutableCaptureStore()
    first = store.record_capture(
        clock=first_clock,
        bars=(bar,),
        claimed_session=date(2026, 9, 16),
        capture_mode="live_capture",
    )
    recaptured = ResearchBar(
        security_id="AAA",
        session_date=date(2026, 9, 16),
        open=10.0,
        high=11.2,
        low=9.0,
        close=11.0,
        raw_open=10.0,
        raw_close=11.0,
        volume=2000.0,
        dollar_volume=22000.0,
        tri=11.0,
    )
    second = store.recapture_last_bar(
        predecessor_id=first.capture_id,
        clock=after,
        bars=(recaptured,),
        claimed_session=date(2026, 9, 16),
        capture_mode="live_capture",
    )
    assert first.capture_mode == "live_capture"
    assert second.capture_mode == "live_capture"
    assert store.get(first.capture_id).retrieved_at == first_clock
    assert all(bar.finalized_at is None for bar in second.bars)
    assert second.bars[0].retrieved_at == after


def test_next_day_confirm_advances_without_freezing_research() -> None:
    after_16 = datetime(2026, 9, 16, 17, 0, tzinfo=ET)
    after_17 = datetime(2026, 9, 17, 17, 0, tzinfo=ET)
    first = disclosed_source_finalized_through(after_16)
    second = disclosed_source_finalized_through(after_17)
    assert first == date(2026, 9, 15)
    assert second == date(2026, 9, 16)
    assert VENDOR_WITHOUT_FINALIZED_FIELD_POLICY == "NEXT_DAY_CONFIRM"
    assert next_session(first) == second
