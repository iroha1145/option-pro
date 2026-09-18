"""Archive the two measurement portfolio counterexamples. Synthetic only."""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.research_eod_v1.fixtures import make_series, trading_days  # noqa: E402
from app.services.research_eod_v1.ledger import simulate_ledger, size_notional  # noqa: E402

OUT = ROOT / "research" / "option_pro_us_eod_v1" / "return_pack" / "measurement_counterexamples"
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


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    days = trading_days(date(2024, 1, 2), 12)
    series = _flat("A", days, 100.0)
    notionals = {
        str(scale): size_notional(
            {
                "planned_invalidation": 90.0 * scale,
                "atr": 1.0 * scale,
                "geometry_close": 100.0 * scale,
                "adv20": 80_000_000,
            },
            series,
            days[0],
            10_000.0,
            TEN_PCT,
            True,
        )
        for scale in (1.0, 2.0, 0.5, 10.0)
    }
    split = size_notional(
        {"planned_invalidation": 45.0, "atr": 0.5, "geometry_close": 50.0, "adv20": 80_000_000},
        series,
        days[0],
        10_000.0,
        TEN_PCT,
        True,
    )
    series.dividend_events = (
        {"ex_date": date(2024, 1, 4), "pay_date": date(2024, 1, 8), "amount": 1.0},
    )
    acq = simulate_ledger(
        start=days[0],
        end=days[-1],
        panel={"A": series},
        capital=10_000.0,
        holding_sessions=20,
        cost_multiple=0,
        signals=[
            {
                "security_id": "A",
                "session_date": date(2024, 1, 2).isoformat(),
                "status": "eligible",
                "score": 90,
                "adv20": 80_000_000,
                "notional": 5_000.0,
            }
        ],
        cash_acquisitions=[
            {
                "security_id": "A",
                "effective_at": date(2024, 1, 5),
                "known_at": date(2024, 1, 5),
                "settlement_at": date(2024, 1, 5),
                "price": 100.0,
            }
        ],
    )
    trade = acq["trades"][0]
    payload = {
        "note": "Synthetic measurement counterexamples after D1/D2. Not market history.",
        "market_backtest_run": False,
        "executed_backtests": 0,
        "cases": [
            {
                "class": "D1_geometry_unit_sizing",
                "raw_execution_close": 100.0,
                "equivalent_support_levels": [90.0, 45.0],
                "scale_notionals": notionals,
                "split_adjusted_notional": split,
                "expected_same_risk_fraction": 0.1,
            },
            {
                "class": "D2_acquisition_then_lot_dividend",
                "ending_equity": acq["ending_equity"],
                "cash_in": trade["cash_in"],
                "filled_at": trade["filled_at"].isoformat() if trade.get("filled_at") else None,
                "exit_reason": trade.get("exit_reason"),
                "dividend_cash": trade["dividend_cash"],
                "trade_net": trade["net_return"],
                "pnl_basis": acq["pnl_basis"],
                "trade_pnl_basis": acq["trade_pnl_basis"],
            },
        ],
    }
    (OUT / "two_classes.json").write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(OUT / "two_classes.json")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
