"""Capital-conserving research execution. Not a product strategy.

Signals computed at T close become executable at the next session open.
The trigger/mark price is never used as a fill. Missing next opens stay
unavailable instead of being replaced by the signal close.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping

from app.services.research.calendar import next_trading_day
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import PORTFOLIO_PROTOCOL, parse_session_date


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _bps_to_fraction(bps: float) -> float:
    return float(bps) / 10_000.0


def session_fill_price(
    dataset: OfflineOHLCV,
    ticker: str,
    fill_date: date,
    *,
    side: str,
    cost_bps: float,
    adjusted: bool = True,
) -> dict[str, Any]:
    bar = dataset.bar(ticker, fill_date)
    if bar is None:
        return {
            "status": "unavailable",
            "reason": "missing_open",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
    raw = bar["adj_open" if adjusted else "open"]
    if raw <= 0:
        return {
            "status": "unavailable",
            "reason": "non_positive_open",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
    slip = _bps_to_fraction(cost_bps)
    fill = raw * (1.0 + slip) if side == "buy" else raw * (1.0 - slip)
    return {
        "status": "filled",
        "reason": None,
        "ticker": ticker,
        "fill_date": fill_date.isoformat(),
        "quote_open": raw,
        "fill_price": fill,
        "side": side,
        "cost_bps": cost_bps,
        "source": "session_open",
    }


def _iter_sessions(start: date, end: date) -> list[date]:
    cursor = start
    out: list[date] = []
    for _ in range(4000):
        out.append(cursor)
        if cursor >= end:
            return out
        cursor = next_trading_day(cursor)
    raise RuntimeError("session iteration exceeded bound")


def simulate_long_only(
    dataset: OfflineOHLCV,
    signals: Iterable[Mapping[str, Any]],
    *,
    cost_bps: float = 10.0,
    hold_days: int = 20,
    initial_cash: float = PORTFOLIO_PROTOCOL["initial_cash"],
    max_positions: int = PORTFOLIO_PROTOCOL["max_positions"],
    max_weight: float = PORTFOLIO_PROTOCOL["max_weight"],
) -> dict[str, Any]:
    """One cash account. Orders are booked on the fill date, not the signal date."""

    cash = float(initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    by_date: dict[date, list[Mapping[str, Any]]] = {}
    for item in signals:
        session = parse_session_date(str(item.get("signal_date")))
        by_date.setdefault(session, []).append(item)
    if not by_date:
        return {
            "status": "unavailable",
            "reason": "no_signals",
            "initial_cash": initial_cash,
            "final_equity": initial_cash,
            "trades": [],
            "equity_curve": [],
        }

    first = min(by_date)
    last_signal = max(by_date)
    last = last_signal
    for _ in range(hold_days + 2):
        last = next_trading_day(last)
    for session in _iter_sessions(first, last):
        still_pending: list[dict[str, Any]] = []
        newly_scheduled: list[dict[str, Any]] = []
        for order in pending:
            if parse_session_date(order["fill_date"]) != session:
                still_pending.append(order)
                continue
            ticker = str(order["ticker"])
            if order["action"] == "sell":
                if ticker not in positions:
                    rejected.append({**order, "status": "skipped", "reason": "position_missing"})
                    continue
                fill = session_fill_price(
                    dataset, ticker, session, side="sell", cost_bps=cost_bps
                )
                if fill["status"] != "filled":
                    rejected.append({**fill, "action": "sell"})
                    # Keep the position marked; do not invent a close from the
                    # last print. The open risk remains on the book.
                    continue
                pos = positions.pop(ticker)
                proceeds = float(pos["shares"]) * float(fill["fill_price"])
                cash += proceeds
                trades.append({**fill, "action": "sell", "shares": pos["shares"], "proceeds": proceeds})
                continue
            fill = session_fill_price(
                dataset, ticker, session, side="buy", cost_bps=cost_bps
            )
            if fill["status"] != "filled":
                rejected.append({**fill, "action": "buy"})
                continue
            if ticker in positions or len(positions) >= max_positions:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "capacity",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                    }
                )
                continue
            equity = _mark_equity(dataset, cash, positions, session)
            target = min(cash, max(0.0, equity * max_weight))
            price = float(fill["fill_price"])
            shares = math.floor(target / price) if price > 0 else 0
            if shares <= 0:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "insufficient_cash",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                    }
                )
                continue
            cost = shares * price
            if cost > cash + 1e-9:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "cash_conservation",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                    }
                )
                continue
            cash -= cost
            exit_signal = session
            for _ in range(hold_days):
                exit_signal = next_trading_day(exit_signal)
            positions[ticker] = {
                "shares": shares,
                "entry_fill": fill,
                "exit_fill_date": next_trading_day(exit_signal).isoformat(),
                "cost": cost,
            }
            newly_scheduled.append(
                {
                    "action": "sell",
                    "ticker": ticker,
                    "fill_date": next_trading_day(exit_signal).isoformat(),
                }
            )
            trades.append({**fill, "action": "buy", "shares": shares, "cost": cost})
        pending = still_pending + newly_scheduled

        if session in by_date:
            ranked = sorted(
                by_date[session],
                key=lambda item: (
                    int(item.get("selected_view_rank") or item.get("rank") or 999),
                    str(item.get("ticker") or ""),
                ),
            )
            for signal in ranked:
                ticker = str(signal.get("ticker") or "")
                if not ticker:
                    continue
                pending.append(
                    {
                        "action": "buy",
                        "ticker": ticker,
                        "fill_date": next_trading_day(session).isoformat(),
                        "signal_date": session.isoformat(),
                    }
                )

        equity = _mark_equity(dataset, cash, positions, session)
        if equity + 1e-6 < 0:
            raise RuntimeError("cash ledger went negative")
        equity_curve.append(
            {
                "date": session.isoformat(),
                "cash": cash,
                "positions": len(positions),
                "equity": equity,
                "open_risk": equity - cash,
            }
        )

    final = equity_curve[-1]["equity"] if equity_curve else cash
    peak = equity_curve[0]["equity"] if equity_curve else cash
    max_drawdown = 0.0
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_drawdown = min(max_drawdown, point["equity"] / peak - 1.0)
    return {
        "status": "active",
        "protocol": {
            **PORTFOLIO_PROTOCOL,
            "cost_bps": cost_bps,
            "hold_trading_days": hold_days,
        },
        "initial_cash": initial_cash,
        "final_equity": final,
        "total_return": final / initial_cash - 1.0 if initial_cash else None,
        "max_drawdown": max_drawdown,
        "trade_count": len(trades),
        "rejected_count": len(rejected),
        "trades": trades,
        "rejected": rejected,
        "equity_curve": equity_curve,
        "notes": [
            "这是研究评估协议，不是产品已有策略。",
            "入场为信号日收盘后的下一可交易开盘，扣除单边成本。",
            "未平仓市值计入权益与回撤，不只统计已平仓盈利单。",
            "现金不足或缺少下一开盘时该信号记 unavailable/skipped，不回退用收盘价成交。",
        ],
    }


def _mark_equity(
    dataset: OfflineOHLCV,
    cash: float,
    positions: Mapping[str, Mapping[str, Any]],
    session: date,
) -> float:
    equity = cash
    for ticker, pos in positions.items():
        bar = dataset.bar(ticker, session)
        if bar is None:
            fill_price = _finite((pos.get("entry_fill") or {}).get("fill_price"))
            if fill_price is not None:
                equity += float(pos["shares"]) * fill_price
            continue
        equity += float(pos["shares"]) * float(bar["adj_close"])
    return equity
