from __future__ import annotations

from datetime import date

import pytest

from app.services.research_eod_v1.backtest import (
    higher_cost_cannot_increase_net,
    plan_trade,
    simulate_portfolio,
)
from app.services.research_eod_v1.calendar_asof import holding_exit_session, next_session
from app.services.research_eod_v1.config_load import load_registry
from app.services.research_eod_v1.fixtures import make_series, trading_days, trending_close


def test_entry_is_next_session_not_calendar_plus_one() -> None:
    days = trading_days(date(2021, 1, 4), 40)
    friday = date(2021, 1, 15)
    assert friday in days
    assert next_session(friday) == date(2021, 1, 19)
    series = make_series("AAA", days, trending_close(40, 50, 0.2))
    planned = plan_trade(series, friday, 5, adv20=80_000_000)
    assert planned.entry_session == date(2021, 1, 19)
    assert planned.exit_session == holding_exit_session(planned.entry_session, 5)
    assert planned.label_status == "MATURE"
    assert planned.net_return is not None
    assert planned.net_return < planned.gross_return


def test_cannot_peek_at_entry_open() -> None:
    days = trading_days(date(2021, 1, 4), 20)
    series = make_series("AAA", days, trending_close(20))
    with pytest.raises(ValueError, match="open"):
        plan_trade(series, days[5], 5, adv20=80_000_000, peek_entry_open=True)


def test_immature_label_at_sample_end() -> None:
    days = trading_days(date(2021, 1, 4), 10)
    series = make_series("AAA", days, trending_close(10))
    planned = plan_trade(series, days[-2], 5, adv20=80_000_000)
    assert planned.label_status in {"IMMATURE_LABEL", "IMMATURE_ENTRY"}
    assert planned.net_return is None


def test_higher_cost_cannot_increase_net() -> None:
    days = trading_days(date(2021, 1, 4), 40)
    series = make_series("AAA", days, trending_close(40, 50, 0.3))
    base = plan_trade(series, days[5], 5, adv20=80_000_000, cost_multiple=1)
    stressed = plan_trade(series, days[5], 5, adv20=80_000_000, cost_multiple=4)
    assert higher_cost_cannot_increase_net(base.net_return, stressed.net_return)


def test_portfolio_does_not_reuse_cash_same_day_and_keeps_empty_cash() -> None:
    days = trading_days(date(2021, 1, 4), 40)
    a = make_series("AAA", days, trending_close(40, 20, 0.1))
    b = make_series("BBB", days, trending_close(40, 25, 0.1))
    profile = load_registry()["profiles"]["balanced"]
    signal_day = days[5].isoformat()
    signals = [
        {
            "security_id": "AAA",
            "session_date": signal_day,
            "status": "eligible",
            "score": 90,
            "planned_invalidation": 15.0,
            "atr": 1.0,
            "adv20": 80_000_000,
        },
        {
            "security_id": "BBB",
            "session_date": signal_day,
            "status": "eligible",
            "score": 80,
            "planned_invalidation": 15.0,
            "atr": 1.0,
            "adv20": 80_000_000,
        },
    ]
    result = simulate_portfolio(signals, {"AAA": a, "BBB": b}, capital=25_000, holding_sessions=5, profile=profile)
    assert result["cash_interest"] == 0.0
    assert result["starting_capital"] == 25_000
    empties = simulate_portfolio([], {"AAA": a}, capital=25_000, holding_sessions=5, profile=profile)
    assert empties["ending_equity"] == 25_000
    assert empties["status"] == "ZERO_SIGNALS"
    for row in result["daily_equity"]:
        assert row["cash"] >= -1e-9
    assert result["ending_equity"] >= 0
