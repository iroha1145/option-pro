from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from app.services.research_eod_v1.backtest import plan_trade
from app.services.research_eod_v1.calendar_asof import next_session
from app.services.research_eod_v1.fixtures import make_series, trading_days
from app.services.research_eod_v1.ledger import simulate_ledger


def _flat(security_id: str, days, price: float = 100.0):
    close = np.full(len(days), price)
    series = make_series(security_id, days, close)
    series.raw_open = close.copy()
    series.raw_close = close.copy()
    return series


def test_par_with_costs_is_a_loss_and_higher_cost_is_worse() -> None:
    days = trading_days(date(2021, 1, 4), 20)
    series = _flat("PAR", days, 100.0)
    cheap = plan_trade(series, days[2], 5, adv20=80_000_000, cost_multiple=1)
    dear = plan_trade(series, days[2], 5, adv20=80_000_000, cost_multiple=4)
    assert cheap.gross_return == 0.0
    assert cheap.net_return < 0
    assert dear.net_return < cheap.net_return


def test_split_conserves_wealth_and_dividend_hits_cash() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    series = _flat("SPL", days, 100.0)
    split_day = days[6]
    series.splits = ((split_day, 2.0),)
    series.dividends = ((days[3], 1.0),)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"SPL": series},
        capital=10_000,
        holding_sessions=5,
        signals=[
            {
                "security_id": "SPL",
                "session_date": days[1].isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 5_000,
            }
        ],
    )
    kinds = [event["kind"] for event in result["events"]]
    assert "split" in kinds
    assert "dividend" in kinds
    assert all(day["identity_ok"] for day in result["daily_equity"] if day["positions"])


def test_zero_signals_keep_every_cash_day() -> None:
    days = trading_days(date(2021, 1, 4), 8)
    series = _flat("AAA", days)
    result = simulate_ledger(start=days[0], end=days[-1], panel={"AAA": series}, capital=25_000, holding_sessions=5, signals=[])
    assert result["status"] == "ZERO_SIGNALS"
    assert len(result["daily_equity"]) == 8
    assert all(row["equity"] == 25_000 for row in result["daily_equity"])


def test_gap_cannot_cancel_registered_open() -> None:
    days = trading_days(date(2021, 1, 4), 12)
    close = np.full(12, 100.0)
    series = make_series("GAP", days, close)
    entry = next_session(days[1])
    series.raw_open = close.copy()
    series.raw_open[days.index(entry)] = 120.0
    with pytest.raises(ValueError, match="open"):
        simulate_ledger(
            start=days[0],
            end=days[-1],
            panel={"GAP": series},
            capital=10_000,
            holding_sessions=5,
            signals=[{"security_id": "GAP", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 1_000}],
            peek_entry_open=True,
        )
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"GAP": series},
        capital=10_000,
        holding_sessions=5,
        signals=[{"security_id": "GAP", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 1_000}],
    )
    buys = [event for event in result["events"] if event["kind"] == "buy"]
    assert buys and buys[0]["price"] == 120.0


def test_unknown_terminal_is_not_marked_at_cost() -> None:
    days = trading_days(date(2021, 1, 4), 10)
    series = _flat("DEAD", days, 50.0)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"DEAD": series},
        capital=10_000,
        holding_sessions=5,
        signals=[{"security_id": "DEAD", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 2_000}],
        unknown_terminals={"DEAD"},
    )
    assert "DEAD" in result["right_censored"] or any(not row["identity_ok"] for row in result["daily_equity"] if row["positions"])


def test_cash_acquisition_clears_shares() -> None:
    days = trading_days(date(2021, 1, 4), 10)
    series = _flat("TGT", days, 40.0)
    result = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"TGT": series},
        capital=10_000,
        holding_sessions=20,
        signals=[{"security_id": "TGT", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 2_000}],
        cash_acquisitions={"TGT": 45.0},
    )
    assert any(event["kind"] == "cash_acquisition" for event in result["events"])


def test_trade_set_change_does_not_use_monotonic_cost_assertion() -> None:
    days = trading_days(date(2021, 1, 4), 15)
    a = _flat("AAA", days, 20.0)
    b = _flat("BBB", days, 80.0)
    one = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": a, "BBB": b},
        capital=10_000,
        holding_sessions=5,
        signals=[{"security_id": "AAA", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 3_000}],
    )
    two = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"AAA": a, "BBB": b},
        capital=10_000,
        holding_sessions=5,
        signals=[
            {"security_id": "AAA", "session_date": days[1].isoformat(), "status": "eligible", "score": 90, "adv20": 80_000_000, "notional": 3_000},
            {"security_id": "BBB", "session_date": days[2].isoformat(), "status": "eligible", "score": 80, "adv20": 80_000_000, "notional": 3_000},
        ],
    )
    assert one["fees_paid"] != two["fees_paid"] or len(one["trades"]) != len(two["trades"]) or one["unfilled"] != two["unfilled"]
    assert two["ending_equity"] > 0
