"""Capital-conserving research execution. Not a product strategy.

Phases stay separate: signal, order, open execution, then EOD mark.
Opening orders never read that session's close/high/low.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.services.research.calendar import next_trading_day, nth_trading_day, previous_trading_day
from app.services.strength.ranking_variants import UNCLASSIFIED_SECTOR, _sector_bucket
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
    max_gross_exposure: float | None = None,
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
            invested = equity - cash
            if max_gross_exposure is not None and equity > 0 and invested / equity >= max_gross_exposure - 1e-12:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "gross_exposure_cap",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                    }
                )
                continue
            target = min(cash, max(0.0, equity * max_weight))
            if max_gross_exposure is not None and equity > 0:
                room = max(0.0, equity * max_gross_exposure - invested)
                target = min(target, room)
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
            "max_gross_exposure": max_gross_exposure,
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


def _planning_close(
    dataset: OfflineOHLCV,
    ticker: str,
    fill_date: date,
    *,
    allow_sealed: bool,
) -> float | None:
    prior = previous_trading_day(fill_date)
    try:
        from app.services.research.protocol import assert_split_access

        assert_split_access(prior, allow_sealed=allow_sealed, purpose="c1_plan")
    except PermissionError:
        return None
    bar = dataset.bar(ticker, prior)
    if bar is None:
        return None
    close = _finite(bar.get("adj_close"))
    if close is None or close <= 0:
        return None
    return close


def _sector_weight(
    positions: Mapping[str, Mapping[str, Any]],
    *,
    equity: float,
    extra: Sequence[tuple[str, float]] | None = None,
) -> dict[str, float]:
    notionals: dict[str, float] = {}
    for pos in positions.values():
        mark = _finite(pos.get("last_mark"))
        if mark is None:
            mark = _finite((pos.get("entry_fill") or {}).get("fill_price"))
        if mark is None:
            continue
        bucket = str(pos.get("sector_bucket") or UNCLASSIFIED_SECTOR)
        notionals[bucket] = notionals.get(bucket, 0.0) + float(pos["shares"]) * mark
    for bucket, notional in extra or []:
        notionals[bucket] = notionals.get(bucket, 0.0) + notional
    if equity <= 0:
        return {key: 0.0 for key in notionals}
    return {key: value / equity for key, value in notionals.items()}


def simulate_c1_sector_budget(
    dataset: OfflineOHLCV,
    signals: Iterable[Mapping[str, Any]],
    *,
    cost_bps: float = 10.0,
    hold_days: int = 20,
    initial_cash: float = PORTFOLIO_PROTOCOL["initial_cash"],
    max_positions: int = PORTFOLIO_PROTOCOL["max_positions"],
    max_weight: float = PORTFOLIO_PROTOCOL["max_weight"],
    sector_notional_cap: float = 0.20,
    allow_sealed: bool = False,
    lock_split: bool = True,
) -> dict[str, Any]:
    """Original ranking, 10% names, 20d exit, 20% live-book sector cap.

    T close marks freeze T+1 share counts. Open gaps that breach the cap are
    recorded; there is no same-open resize and no forced trim.
    """

    cash = float(initial_cash)
    positions: dict[str, dict[str, Any]] = {}
    pending: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    overruns: list[dict[str, Any]] = []
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
            "trades": [],
            "equity_curve": [],
            "rejected": rejected,
            "sector_overruns": [],
        }

    first = min(by_date)
    last = max(by_date)
    for _ in range(hold_days + 2):
        last = next_trading_day(last)
    for session in _iter_sessions(first, last):
        still_pending: list[dict[str, Any]] = []
        today_sells: list[dict[str, Any]] = []
        today_buys: list[dict[str, Any]] = []
        newly_scheduled: list[dict[str, Any]] = []
        for order in pending:
            if parse_session_date(order["fill_date"]) != session:
                still_pending.append(order)
            elif order["action"] == "sell":
                today_sells.append(order)
            else:
                today_buys.append(order)

        for order in today_sells:
            ticker = str(order["ticker"])
            signal_split = order.get("signal_split")
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

        today_buys.sort(
            key=lambda item: (
                int(item.get("selected_view_rank") or item.get("rank") or 999),
                str(item.get("ticker") or ""),
            )
        )
        equity = _opening_equity(cash, positions)
        planned_sector: dict[str, float] = {}
        for pos in positions.values():
            mark = _finite(pos.get("last_mark"))
            if mark is None:
                continue
            bucket = str(pos.get("sector_bucket") or UNCLASSIFIED_SECTOR)
            planned_sector[bucket] = planned_sector.get(bucket, 0.0) + float(pos["shares"]) * mark

        for order in today_buys:
            ticker = str(order["ticker"])
            signal_split = order.get("signal_split")
            bucket = str(order.get("sector_bucket") or UNCLASSIFIED_SECTOR)
            if ticker in positions or len(positions) >= max_positions:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "capacity",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                        "sector_bucket": bucket,
                    }
                )
                continue
            plan_px = _planning_close(dataset, ticker, session, allow_sealed=allow_sealed)
            if plan_px is None:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "missing_plan_close",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                        "sector_bucket": bucket,
                    }
                )
                continue
            sector_used = planned_sector.get(bucket, 0.0)
            sector_room = max(0.0, equity * sector_notional_cap - sector_used)
            target = min(cash, max(0.0, equity * max_weight), sector_room)
            shares = math.floor(target / plan_px) if plan_px > 0 else 0
            if shares <= 0:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "sector_budget" if sector_room <= equity * max_weight + 1e-9 else "insufficient_cash",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                        "sector_bucket": bucket,
                        "planned_notional": target,
                    }
                )
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
                rejected.append({**fill, "action": "buy", "sector_bucket": bucket})
                continue
            price = float(fill["fill_price"])
            affordable = math.floor(cash / price) if price > 0 else 0
            filled_shares = min(shares, affordable)
            if filled_shares <= 0:
                rejected.append(
                    {
                        "status": "skipped",
                        "reason": "cash_short_after_gap",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                        "frozen_shares": shares,
                    }
                )
                continue
            if filled_shares < shares:
                rejected.append(
                    {
                        "status": "partial",
                        "reason": "cash_short_after_gap",
                        "ticker": ticker,
                        "fill_date": session.isoformat(),
                        "frozen_shares": shares,
                        "filled_shares": filled_shares,
                    }
                )
            cost = filled_shares * price
            cash -= cost
            exit_date = nth_trading_day(session, hold_days, after=True)
            if exit_date is None:
                rejected.append({**fill, "action": "buy", "status": "skipped", "reason": "exit_calendar_unresolved"})
                cash += cost
                continue
            positions[ticker] = {
                "shares": filled_shares,
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
                "sector_bucket": bucket,
                "frozen_shares": shares,
                "plan_price": plan_px,
            }
            planned_sector[bucket] = planned_sector.get(bucket, 0.0) + shares * plan_px
            realized_equity = _opening_equity(cash, positions)
            realized = _sector_weight(positions, equity=realized_equity)
            if realized.get(bucket, 0.0) > sector_notional_cap + 1e-9:
                overruns.append(
                    {
                        "date": session.isoformat(),
                        "ticker": ticker,
                        "sector_bucket": bucket,
                        "realized_weight": realized.get(bucket),
                        "cap": sector_notional_cap,
                        "plan_price": plan_px,
                        "fill_price": price,
                    }
                )
            newly_scheduled.append(
                {
                    "action": "sell",
                    "ticker": ticker,
                    "fill_date": exit_date.isoformat(),
                    "signal_split": signal_split,
                    "signal_date": order.get("signal_date"),
                    "sector_bucket": bucket,
                }
            )
            trades.append(
                {
                    **fill,
                    "action": "buy",
                    "shares": filled_shares,
                    "cost": cost,
                    "sector_bucket": bucket,
                    "frozen_shares": shares,
                    "plan_price": plan_px,
                }
            )
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
                        "selected_view_rank": signal.get("selected_view_rank") or signal.get("rank"),
                        "rank": signal.get("selected_view_rank") or signal.get("rank"),
                        "sector_bucket": _sector_bucket(signal),
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
        weights = _sector_weight(positions, equity=equity)
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
                "sector_weights": weights,
                "max_sector_weight": None if not weights else max(weights.values()),
                "over_sector_cap": any(value > sector_notional_cap + 1e-9 for value in weights.values()),
            }
        )

    summary = summarize_ledger(
        {
            "initial_cash": initial_cash,
            "equity_curve": equity_curve,
            "trades": trades,
            "rejected": rejected,
            "max_positions": max_positions,
        }
    )
    return {
        "status": "active",
        "candidate_id": "C1",
        "protocol": {
            **PORTFOLIO_PROTOCOL,
            "cost_bps": cost_bps,
            "hold_trading_days": hold_days,
            "sector_notional_cap": sector_notional_cap,
            "plan_price": "signal_close",
            "gap_overrun_rule": "record_and_hold_to_scheduled_exit",
            "no_leverage": True,
            "allow_sealed": allow_sealed,
            "lock_split": lock_split,
        },
        **summary,
        "unexecuted_exits": unexecuted_exits,
        "open_positions_at_end": len(positions),
        "end_mark_status": {ticker: pos.get("mark_status") for ticker, pos in positions.items()},
        "sector_overruns": overruns,
        "overrun_day_share": (
            None
            if not equity_curve
            else sum(1 for point in equity_curve if point.get("over_sector_cap")) / len(equity_curve)
        ),
        "trades": trades,
        "rejected": rejected,
        "equity_curve": equity_curve,
        "notes": [
            "C1 is a live-book sector budget, not C0's daily name quota.",
            "Shares are frozen from T close. T+1 gaps are not used to resize.",
            "Primary sector is a static theme map, not point-in-time GICS.",
        ],
    }


def summarize_ledger(result: Mapping[str, Any]) -> dict[str, Any]:
    curve = list(result.get("equity_curve") or [])
    trades = list(result.get("trades") or [])
    rejected = list(result.get("rejected") or [])
    initial = float(result.get("initial_cash") or 0.0)
    final = curve[-1]["equity"] if curve else initial
    peak = curve[0]["equity"] if curve else initial
    max_drawdown = 0.0
    dd_start = None
    dd_trough = None
    dd_recover = None
    peak_date = curve[0]["date"] if curve else None
    current_dd_start = None
    for point in curve:
        equity = float(point["equity"])
        if equity >= peak:
            if current_dd_start is not None and dd_recover is None and max_drawdown < 0:
                dd_recover = point["date"]
            peak = equity
            peak_date = point["date"]
            current_dd_start = None
        else:
            if current_dd_start is None:
                current_dd_start = peak_date
            draw = equity / peak - 1.0 if peak > 0 else 0.0
            if draw < max_drawdown:
                max_drawdown = draw
                dd_start = current_dd_start
                dd_trough = point["date"]
                dd_recover = None
    occupancy = []
    max_positions = float(result.get("max_positions") or PORTFOLIO_PROTOCOL["max_positions"])
    exposures = []
    for point in curve:
        if max_positions:
            occupancy.append(point["positions"] / max_positions)
        if point["equity"]:
            exposures.append(point["open_risk"] / point["equity"])
    reasons: dict[str, int] = {}
    for row in rejected:
        reason = str(row.get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    buys = [trade for trade in trades if trade.get("action") == "buy"]
    sell_queue: dict[str, list[dict[str, Any]]] = {}
    for trade in trades:
        if trade.get("action") != "sell":
            continue
        sell_queue.setdefault(str(trade.get("ticker") or ""), []).append(trade)
    pnls: list[dict[str, Any]] = []
    for buy in buys:
        queue = sell_queue.get(str(buy.get("ticker") or ""), [])
        if not queue:
            continue
        sell = queue.pop(0)
        pnl = float(sell.get("proceeds") or 0.0) - float(buy.get("cost") or 0.0)
        pnls.append(
            {
                "ticker": buy.get("ticker"),
                "sector_bucket": buy.get("sector_bucket"),
                "pnl": pnl,
            }
        )
    worst_names = sorted(pnls, key=lambda item: item["pnl"])[:8]
    sector_pnl: dict[str, float] = {}
    for row in pnls:
        bucket = str(row.get("sector_bucket") or UNCLASSIFIED_SECTOR)
        sector_pnl[bucket] = sector_pnl.get(bucket, 0.0) + float(row["pnl"])
    return {
        "initial_cash": initial,
        "final_equity": final,
        "total_return": final / initial - 1.0 if initial else None,
        "max_drawdown": max_drawdown,
        "drawdown_start": dd_start,
        "drawdown_trough": dd_trough,
        "drawdown_recover": dd_recover,
        "trade_count": len(trades),
        "rejected_count": len(rejected),
        "reject_reasons": reasons,
        "average_positions": (
            sum(point["positions"] for point in curve) / len(curve) if curve else 0.0
        ),
        "average_occupancy": sum(occupancy) / len(occupancy) if occupancy else 0.0,
        "average_gross_exposure": sum(exposures) / len(exposures) if exposures else 0.0,
        "time_in_market": (
            sum(1 for point in curve if point["positions"] > 0) / len(curve) if curve else 0.0
        ),
        "worst_name_pnls": worst_names,
        "sector_pnl": sector_pnl,
    }

