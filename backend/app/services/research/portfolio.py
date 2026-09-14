"""Capital-conserving research execution. Not a product strategy.

Phases stay separate: signal, order, open execution, then EOD mark.
Opening orders never read that session's close/high/low.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping

from app.services.research.calendar import next_trading_day, nth_trading_day
from app.services.research.dataset import OfflineOHLCV
from app.services.research.protocol import (
    PORTFOLIO_PROTOCOL,
    assert_split_access,
    parse_session_date,
    split_for_date,
)


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
    allow_sealed: bool = False,
    signal_split: str | None = None,
    lock_split: bool = True,
) -> dict[str, Any]:
    try:
        assert_split_access(fill_date, allow_sealed=allow_sealed, purpose="portfolio_fill")
    except PermissionError:
        return {
            "status": "unavailable",
            "reason": "sealed_price_blocked",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
    if lock_split and signal_split and split_for_date(fill_date) != signal_split:
        return {
            "status": "unavailable",
            "reason": "fill_crosses_split",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
    bar = dataset.bar(ticker, fill_date)
    if bar is None:
        return {
            "status": "unavailable",
            "reason": "missing_open",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
    raw = bar.get("adj_open" if adjusted else "open")
    if raw is None:
        return {
            "status": "unavailable",
            "reason": "missing_open",
            "ticker": ticker,
            "fill_date": fill_date.isoformat(),
            "fill_price": None,
        }
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
        "open_observed": bool(bar.get("open_observed", True)),
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


def _opening_equity(cash: float, positions: Mapping[str, Mapping[str, Any]]) -> float:
    """Mark from last completed session only. Never uses today's close."""

    equity = cash
    for pos in positions.values():
        mark = _finite(pos.get("last_mark"))
        if mark is None:
            mark = _finite((pos.get("entry_fill") or {}).get("fill_price"))
        if mark is not None:
            equity += float(pos["shares"]) * mark
    return equity


def _eod_mark_positions(
    dataset: OfflineOHLCV,
    positions: dict[str, dict[str, Any]],
    session: date,
    *,
    allow_sealed: bool,
    lock_split: bool,
) -> dict[str, Any]:
    stale = 0
    unpriced = 0
    blocked = 0
    for ticker, pos in positions.items():
        signal_split = pos.get("signal_split")
        try:
            assert_split_access(session, allow_sealed=allow_sealed, purpose="portfolio_mark")
        except PermissionError:
            pos["mark_status"] = "sealed_blocked"
            blocked += 1
            continue
        if lock_split and signal_split and split_for_date(session) != signal_split:
            pos["mark_status"] = "stale_beyond_split"
            stale += 1
            continue
        bar = dataset.bar(ticker, session)
        close = None if bar is None else _finite(bar.get("adj_close"))
        if close is None or close <= 0:
            pos["mark_status"] = "stale" if pos.get("last_mark") is not None else "unpriced"
            if pos.get("last_mark") is None:
                unpriced += 1
            else:
                stale += 1
            continue
        pos["last_mark"] = close
        pos["last_mark_date"] = session.isoformat()
        pos["mark_status"] = "marked"
    return {"stale": stale, "unpriced": unpriced, "blocked": blocked}


def simulate_long_only(
    dataset: OfflineOHLCV,
    signals: Iterable[Mapping[str, Any]],
    *,
    cost_bps: float = 10.0,
    hold_days: int = 20,
    initial_cash: float = PORTFOLIO_PROTOCOL["initial_cash"],
    max_positions: int = PORTFOLIO_PROTOCOL["max_positions"],
    max_weight: float = PORTFOLIO_PROTOCOL["max_weight"],
    allow_sealed: bool = False,
    lock_split: bool = True,
) -> dict[str, Any]:
    """One cash account. Orders are booked on the fill date, not the signal date."""

    cash = float(initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    unexecuted_exits = 0

    by_date: dict[date, list[Mapping[str, Any]]] = {}
    for item in signals:
        session = parse_session_date(str(item.get("signal_date")))
        try:
            assert_split_access(session, allow_sealed=allow_sealed, purpose="portfolio_signal")
        except PermissionError:
            rejected.append(
                {
                    "status": "skipped",
                    "reason": "sealed_signal_blocked",
                    "ticker": item.get("ticker"),
                    "signal_date": session.isoformat(),
                }
            )
            continue
        by_date.setdefault(session, []).append(item)
    if not by_date:
        return {
            "status": "unavailable",
            "reason": "no_signals",
            "initial_cash": initial_cash,
            "final_equity": initial_cash,
            "total_return": 0.0,
            "trade_count": 0,
            "rejected_count": len(rejected),
            "unexecuted_exits": 0,
            "open_positions_at_end": 0,
            "end_mark_status": {},
            "trades": [],
            "equity_curve": [],
            "rejected": rejected,
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
            signal_split = order.get("signal_split")
            if order["action"] == "sell":
                if ticker not in positions:
                    rejected.append({**order, "status": "skipped", "reason": "position_missing"})
                    continue
                fill = session_fill_price(
                    dataset,
                    ticker,
                    session,
                    side="sell",
                    cost_bps=cost_bps,
                    allow_sealed=allow_sealed,
                    signal_split=signal_split,
                    lock_split=lock_split,
                )
                if fill["status"] != "filled":
                    rejected.append({**fill, "action": "sell"})
                    unexecuted_exits += 1
                    still_pending.append(order)
                    continue
                pos = positions.pop(ticker)
                proceeds = float(pos["shares"]) * float(fill["fill_price"])
                cash += proceeds
                trades.append({**fill, "action": "sell", "shares": pos["shares"], "proceeds": proceeds})
                continue
            fill = session_fill_price(
                dataset,
                ticker,
                session,
                side="buy",
                cost_bps=cost_bps,
                allow_sealed=allow_sealed,
                signal_split=signal_split,
                lock_split=lock_split,
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
            equity = _opening_equity(cash, positions)
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
            exit_date = nth_trading_day(session, hold_days, after=True)
            if exit_date is None:
                rejected.append({**fill, "action": "buy", "status": "skipped", "reason": "exit_calendar_unresolved"})
                cash += cost
                continue
            positions[ticker] = {
                "shares": shares,
                "entry_fill": fill,
                "entry_date": session.isoformat(),
                "exit_fill_date": exit_date.isoformat(),
                "hold_days": hold_days,
                "hold_convention": "entry_open_to_exit_open_nth_trading_day",
                "cost": cost,
                "last_mark": price,
                "last_mark_date": session.isoformat(),
                "mark_status": "entry_fill",
                "signal_split": signal_split,
                "signal_date": order.get("signal_date"),
            }
            newly_scheduled.append(
                {
                    "action": "sell",
                    "ticker": ticker,
                    "fill_date": exit_date.isoformat(),
                    "signal_split": signal_split,
                    "signal_date": order.get("signal_date"),
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
                        "signal_split": split_for_date(session),
                    }
                )

        mark_stats = _eod_mark_positions(
            dataset,
            positions,
            session,
            allow_sealed=allow_sealed,
            lock_split=lock_split,
        )
        equity = _opening_equity(cash, positions)
        if equity + 1e-6 < 0:
            raise RuntimeError("cash ledger went negative")
        equity_curve.append(
            {
                "date": session.isoformat(),
                "cash": cash,
                "positions": len(positions),
                "equity": equity,
                "open_risk": equity - cash,
                "stale_marks": mark_stats["stale"],
                "unpriced_marks": mark_stats["unpriced"],
                "blocked_marks": mark_stats["blocked"],
            }
        )

    final = equity_curve[-1]["equity"] if equity_curve else cash
    peak = equity_curve[0]["equity"] if equity_curve else cash
    max_drawdown = 0.0
    for point in equity_curve:
        peak = max(peak, point["equity"])
        if peak > 0:
            max_drawdown = min(max_drawdown, point["equity"] / peak - 1.0)
    occupancy = [point["positions"] / max_positions if max_positions else 0.0 for point in equity_curve]
    time_in_market = (
        sum(1 for point in equity_curve if point["positions"] > 0) / len(equity_curve)
        if equity_curve
        else 0.0
    )
    return {
        "status": "active",
        "protocol": {
            **PORTFOLIO_PROTOCOL,
            "cost_bps": cost_bps,
            "hold_trading_days": hold_days,
            "hold_convention": "entry_open_plus_hold_days_trading_sessions",
            "allow_sealed": allow_sealed,
            "lock_split": lock_split,
        },
        "initial_cash": initial_cash,
        "final_equity": final,
        "total_return": final / initial_cash - 1.0 if initial_cash else None,
        "max_drawdown": max_drawdown,
        "trade_count": len(trades),
        "rejected_count": len(rejected),
        "unexecuted_exits": unexecuted_exits,
        "open_positions_at_end": len(positions),
        "end_mark_status": {
            ticker: pos.get("mark_status") for ticker, pos in positions.items()
        },
        "average_positions": sum(point["positions"] for point in equity_curve) / len(equity_curve) if equity_curve else 0.0,
        "average_occupancy": sum(occupancy) / len(occupancy) if occupancy else 0.0,
        "time_in_market": time_in_market,
        "trades": trades,
        "rejected": rejected,
        "equity_curve": equity_curve,
        "notes": [
            "这是研究评估协议，不是产品已有策略。",
            "开盘定仓只用上一完整会话估值与当日开盘，不读当日收盘。",
            f"持有 {hold_days} 个交易日：入场开盘到第 {hold_days} 个后续交易日开盘退出。",
            "缺价沿用最后已知估值并标记 stale/unpriced，不回退到买入成本。",
            "默认拒绝封存期信号/成交/估值；跨 split 的退出记 unavailable。",
        ],
    }
