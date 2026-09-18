"""Archive the four closeout cashflow / date counterexamples. Synthetic only."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.backtest import simulate_portfolio  # noqa: E402
from app.services.research_eod_v1.fixtures import make_series, trading_days  # noqa: E402
from app.services.research_eod_v1.ledger import simulate_ledger  # noqa: E402
from app.services.research_eod_v1.residual import residual_raw_momentum  # noqa: E402

OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "closeout_counterexamples"
TEN_PCT = {
    "max_position_fraction": 0.10,
    "position_risk_budget_fraction": 1.0,
    "max_order_adv_fraction": 1.0,
}


def _flat(sid: str, days, price: float = 100.0):
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


def _capacity(sid: str, session: date, score: float) -> dict:
    return {
        "security_id": sid,
        "session_date": session.isoformat(),
        "status": "eligible",
        "score": score,
        "planned_invalidation": 90.0,
        "atr": 1.0,
        "adv20": 80_000_000,
    }


def _buy(result: dict, sid: str, session: date) -> float:
    key = session.isoformat()
    for event in result["events"]:
        if event["kind"] == "buy" and event["security_id"] == sid and event["session"] == key:
            return abs(float(event["cash_delta"]))
    return 0.0


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    days = trading_days(date(2024, 1, 2), 16)
    panel = {"A": _flat("A", days), "B": _flat("B", days)}

    future = {
        "class": "A_future_signal_and_leftover",
        "without_future_notional": _buy(
            simulate_portfolio(
                [_capacity("A", date(2024, 1, 2), 50)],
                panel,
                capital=10_000,
                holding_sessions=1,
                profile=TEN_PCT,
                cost_multiple=0,
                start=days[0],
                end=days[-1],
            ),
            "A",
            date(2024, 1, 3),
        ),
        "with_future_notional": _buy(
            simulate_portfolio(
                [_capacity("A", date(2024, 1, 2), 50), _capacity("B", date(2024, 1, 10), 99)],
                panel,
                capital=10_000,
                holding_sessions=1,
                profile=TEN_PCT,
                cost_multiple=0,
                start=days[0],
                end=days[-1],
            ),
            "A",
            date(2024, 1, 3),
        ),
    }
    recycle = simulate_portfolio(
        [_capacity("A", date(2024, 1, 2), 90), _capacity("B", date(2024, 1, 8), 80)],
        panel,
        capital=10_000,
        holding_sessions=1,
        profile=TEN_PCT,
        cost_multiple=0,
        start=days[0],
        end=days[-1],
    )
    future["later_nonoverlap_amounts"] = [
        _buy(recycle, "A", date(2024, 1, 3)),
        _buy(recycle, "B", date(2024, 1, 9)),
    ]

    calendar = trading_days(date(2024, 1, 2), 16)
    missing = date(2024, 1, 5)
    hole_days = [day for day in calendar if day != missing]
    deferred = simulate_ledger(
        start=calendar[0],
        end=calendar[-1],
        panel={"A": _flat("A", hole_days)},
        capital=10_000,
        holding_sessions=1,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 3))],
    )
    halted_series = _flat("A", days)
    halted_series.bar_halted = np.zeros(len(days), dtype=bool)
    halted_series.bar_halted[days.index(date(2024, 1, 5))] = True
    halted = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": halted_series},
        capital=10_000,
        holding_sessions=1,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 3))],
    )
    exits = {
        "class": "B_pending_exit_and_halt",
        "deferred_exit_session": next(
            (event["session"] for event in deferred["events"] if event["kind"] == "sell"),
            None,
        ),
        "deferred_original_planned_exit": str(deferred["trades"][0]["original_planned_exit"]),
        "halted_sell_sessions": [
            event["session"] for event in halted["events"] if event["kind"] == "sell"
        ],
        "halted_defer_notes": [
            event["note"] for event in halted["events"] if event["kind"] == "exit_deferred"
        ],
    }

    div_series = _flat("A", days)
    div_series.dividend_events = (
        {"ex_date": date(2024, 1, 4), "pay_date": date(2024, 1, 10), "amount": 1.0},
    )
    after_exit = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": div_series},
        capital=10_000,
        holding_sessions=2,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 2))],
    )
    reentry = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": div_series},
        capital=10_000,
        holding_sessions=2,
        cost_multiple=0,
        signals=[_signal("A", date(2024, 1, 2), score=90), _signal("A", date(2024, 1, 8), score=80)],
    )
    dividends = {
        "class": "C_lot_bound_dividends",
        "after_exit_equity": after_exit["ending_equity"],
        "after_exit_trade_net": after_exit["trades"][0]["net_return"],
        "after_exit_dividend_cash": after_exit["trades"][0]["dividend_cash"],
        "reentry_nets": [trade["net_return"] for trade in reentry["trades"]],
        "reentry_dividends": [trade.get("dividend_cash", 0.0) for trade in reentry["trades"]],
        "pnl_basis": after_exit["pnl_basis"],
        "trade_pnl_basis": after_exit["trade_pnl_basis"],
        "dividend_pay_date_policy": after_exit["dividend_pay_date_policy"],
    }

    hist = trading_days(date(2018, 1, 2), 400)
    stale = make_series("STALE", hist, 20 + 0.05 * np.arange(400), industry_id="semiconductors")
    spy = make_series("SPY", hist, 200 + 0.04 * np.arange(400), asset_track="etf", security_type="ETF")
    peers = {
        "NVDA": make_series("NVDA", hist, 40 + 0.08 * np.arange(400), industry_id="semiconductors"),
        "AMD": make_series("AMD", hist, 30 + 0.07 * np.arange(400), industry_id="semiconductors"),
        "SPY": spy,
    }
    residual = residual_raw_momentum(stale, spy, peers)
    residual_case = {
        "class": "D_excluded_target_residual",
        "history_sessions": len(hist),
        "target_in_panel": False,
        "status": residual.status,
        "raw_is_finite": residual.raw is not None and bool(np.isfinite(residual.raw)),
        "raised": False,
    }

    payload = {
        "note": "Synthetic closeout counterexamples after A-E. Not market history.",
        "cases": [future, exits, dividends, residual_case],
    }
    path = OUT / "four_classes.json"
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(path), "classes": [item["class"] for item in payload["cases"]]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
