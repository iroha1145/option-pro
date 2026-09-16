"""Explicit cash / share / receivable ledger. Execution uses raw unadjusted opens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

import numpy as np

from app.services.research_eod_v1.backtest import apply_cost, plan_trade, slippage_bps
from app.services.research_eod_v1.calendar_asof import next_session
from app.services.research_eod_v1.series import SecuritySeries


@dataclass
class LedgerEvent:
    session: date
    kind: str
    security_id: str | None
    cash_delta: float
    shares_delta: float
    price: float | None
    note: str = ""


@dataclass
class LedgerState:
    cash: float
    shares: dict[str, float] = field(default_factory=dict)
    cost_basis: dict[str, float] = field(default_factory=dict)
    receivables: float = 0.0
    fees_paid: float = 0.0
    marks: dict[str, float] = field(default_factory=dict)
    status: dict[str, str] = field(default_factory=dict)
    unknown_sids: set[str] = field(default_factory=set)

    def known_positions_value(self) -> float:
        total = 0.0
        for sid, qty in self.shares.items():
            if qty == 0 or sid in self.unknown_sids:
                continue
            if sid not in self.marks:
                continue
            total += qty * self.marks[sid]
        return total

    def marked_equity(self) -> float:
        if self.unknown_sids:
            raise ValueError("PARTIAL_MARK")
        positions = 0.0
        for sid, qty in self.shares.items():
            if qty == 0:
                continue
            if sid not in self.marks:
                raise ValueError(f"NO_MARK:{sid}")
            positions += qty * self.marks[sid]
        return self.cash + self.receivables + positions


def equity_identity_holds(state: LedgerState, *, tol: float = 1e-6) -> bool:
    try:
        equity = state.marked_equity()
    except ValueError:
        return False
    rebuilt = state.cash + state.receivables + state.known_positions_value()
    return abs(equity - rebuilt) <= tol


def _raw_open(series: SecuritySeries, session: date) -> float | None:
    if session not in series.dates:
        return None
    idx = series.dates.index(session)
    raw = series.raw_open
    if raw is None:
        return None
    value = float(raw[idx])
    if not np.isfinite(value) or value <= 0:
        return None
    return value


def _mark_price(series: SecuritySeries, session: date) -> tuple[float | None, str]:
    if session not in series.dates:
        return None, "MISSING_SESSION"
    idx = series.dates.index(session)
    if series.bar_halted is not None and bool(series.bar_halted[idx]):
        raw = float(series.raw_close[idx])
        if np.isfinite(raw) and raw > 0:
            return raw, "HALTED_LAST_RAW_CLOSE"
        return None, "HALTED_NO_MARK"
    volume = float(series.volume[idx]) if idx < len(series.volume) else float("nan")
    raw = float(series.raw_close[idx])
    if not np.isfinite(raw) or raw <= 0:
        return None, "MISSING_RAW_CLOSE"
    if np.isfinite(volume) and volume <= 0:
        return raw, "HALTED_LAST_RAW_CLOSE"
    return raw, "RAW_CLOSE"


def _normalize_acquisitions(raw: Any) -> list[dict[str, Any]]:
    if not raw:
        return []
    if isinstance(raw, Mapping):
        values = list(raw.values())
        if values and isinstance(values[0], (int, float)):
            raise ValueError("cash_acquisitions require dated events; sid->price is not an effective date")
        out: list[dict[str, Any]] = []
        for sid, event in raw.items():
            if not isinstance(event, Mapping):
                raise ValueError("cash_acquisitions values must be dated mappings")
            item = dict(event)
            item.setdefault("security_id", sid)
            out.append(item)
        return out
    return [dict(item) for item in raw]


def _normalize_unknown(raw: Any) -> dict[str, date]:
    if not raw:
        return {}
    if isinstance(raw, Mapping):
        out: dict[str, date] = {}
        for sid, value in raw.items():
            if isinstance(value, date):
                out[str(sid)] = value
            elif isinstance(value, str):
                out[str(sid)] = date.fromisoformat(value)
            else:
                raise ValueError("unknown_terminals must be dated; a bare sid set applies a future halt to the past")
        return out
    if isinstance(raw, (set, frozenset, list, tuple)):
        raise ValueError("unknown_terminals must be {security_id: effective_at}")
    return {}


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def simulate_ledger(
    *,
    start: date,
    end: date,
    panel: Mapping[str, SecuritySeries],
    capital: float,
    holding_sessions: int,
    signals: Sequence[Mapping[str, Any]] | None = None,
    fee: float = 0.0,
    cost_multiple: float = 1.0,
    peek_entry_open: bool = False,
    cash_acquisitions: Any = None,
    unknown_terminals: Any = None,
    allow_implicit_sizing: bool = False,
) -> dict[str, Any]:
    """Full-period ledger. Empty days stay in cash. Do not peek at T+1 open to cancel."""

    if peek_entry_open:
        raise ValueError("cannot cancel a registered open fill after observing the open")
    if start > end:
        raise ValueError("ledger start must precede end")

    days: list[date] = []
    cursor = start
    while cursor <= end:
        days.append(cursor)
        try:
            cursor = next_session(cursor)
        except RuntimeError:
            break

    state = LedgerState(cash=float(capital))
    events: list[LedgerEvent] = [LedgerEvent(start, "cash_open", None, float(capital), 0.0, None, "starting_capital")]
    daily: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []
    open_positions: dict[str, dict[str, Any]] = {}
    unknown = _normalize_unknown(unknown_terminals)
    deals = _normalize_acquisitions(cash_acquisitions)
    pending_div: list[dict[str, Any]] = []

    by_day: dict[date, list[Mapping[str, Any]]] = {}
    for row in signals or []:
        day = date.fromisoformat(row["session_date"]) if isinstance(row["session_date"], str) else row["session_date"]
        by_day.setdefault(day, []).append(row)

    if not signals:
        for day in days:
            daily.append(
                {
                    "session": day.isoformat(),
                    "equity": float(capital),
                    "cash": float(capital),
                    "receivables": 0.0,
                    "known_positions_value": 0.0,
                    "partial_value": float(capital),
                    "unknown_exposure": [],
                    "positions": 0,
                    "identity_ok": True,
                }
            )
        return {
            "starting_capital": capital,
            "ending_equity": capital,
            "status": "ZERO_SIGNALS",
            "events": [event.__dict__ for event in events],
            "trades": [],
            "daily_equity": daily,
            "unfilled": 0,
            "right_censored": [],
        }

    unfilled = 0
    right_censored: list[str] = []
    for day in days:
        for sid, series in panel.items():
            qty = state.shares.get(sid, 0.0)
            for split_day, ratio in series.splits:
                if split_day == day and qty and ratio > 0:
                    state.shares[sid] = qty * ratio
                    if sid in state.marks:
                        state.marks[sid] = state.marks[sid] / ratio
                    events.append(LedgerEvent(day, "split", sid, 0.0, qty * (ratio - 1.0), ratio, "split_wealth_conserved"))
                    qty = state.shares[sid]
            for action in getattr(series, "dividend_events", ()) or ():
                ex_day = _as_date(action.get("ex_date") or action.get("session_date"))
                pay_day = _as_date(action.get("pay_date")) or (next_session(ex_day) if ex_day else None)
                amount = float(action.get("amount") or 0.0)
                if ex_day == day and qty and amount:
                    due = qty * amount
                    state.receivables += due
                    pending_div.append({"security_id": sid, "pay_date": pay_day, "amount": due})
                    events.append(LedgerEvent(day, "dividend", sid, 0.0, 0.0, amount, "ex_date_receivable"))
            if not getattr(series, "dividend_events", ()):
                for div_day, amount in series.dividends:
                    if div_day == day and qty and amount:
                        due = qty * float(amount)
                        state.receivables += due
                        pending_div.append({"security_id": sid, "pay_date": next_session(div_day), "amount": due})
                        events.append(LedgerEvent(day, "dividend", sid, 0.0, 0.0, amount, "ex_date_receivable"))
            for deal in deals:
                if str(deal.get("security_id")) != sid:
                    continue
                effective = _as_date(deal.get("effective_at") or deal.get("session_date"))
                known = _as_date(deal.get("known_at")) or effective
                settle = _as_date(deal.get("settlement_at")) or effective
                if effective is None or known is None:
                    raise ValueError("cash acquisition missing effective_at/known_at")
                if day < max(effective, known):
                    continue
                qty = state.shares.get(sid, 0.0)
                if not qty:
                    continue
                if settle and day < settle:
                    continue
                cash_in = qty * float(deal["price"])
                state.cash += cash_in
                state.shares[sid] = 0.0
                state.marks.pop(sid, None)
                events.append(LedgerEvent(day, "cash_acquisition", sid, cash_in, -qty, float(deal["price"]), "terminal_cash"))
                if sid in open_positions:
                    pos = open_positions.pop(sid)
                    pos["exit_open"] = float(deal["price"])
                    pos["net_return"] = _trade_net(pos, cash_in)
                    trades.append(pos)
            if sid in unknown and day >= unknown[sid] and state.shares.get(sid, 0.0):
                state.status[sid] = "TERMINAL_VALUE_UNKNOWN"
                right_censored.append(sid)

        still_pending: list[dict[str, Any]] = []
        for item in pending_div:
            if item["pay_date"] == day:
                state.receivables -= item["amount"]
                state.cash += item["amount"]
                events.append(LedgerEvent(day, "dividend_pay", item["security_id"], item["amount"], 0.0, None, "pay_date_cash"))
            else:
                still_pending.append(item)
        pending_div = still_pending

        for sid, pos in list(open_positions.items()):
            if pos.get("exit_session") != day:
                continue
            series = panel[sid]
            planned = plan_trade(
                series,
                pos["signal_session"],
                holding_sessions,
                adv20=pos["adv20"],
                fee=fee,
                cost_multiple=cost_multiple,
            )
            raw_exit = _raw_open(series, day)
            if planned.label_status != "MATURE" or raw_exit is None:
                retry = None
                try:
                    retry = next_session(day)
                except RuntimeError:
                    retry = None
                if retry is not None and retry <= end and retry in series.dates:
                    pos["exit_session"] = retry
                    events.append(LedgerEvent(day, "exit_deferred", sid, 0.0, 0.0, None, "missing_exit_bar"))
                    continue
                state.status[sid] = planned.label_status
                right_censored.append(sid)
                continue
            qty = state.shares.get(sid, 0.0)
            sell = apply_cost(raw_exit, side="sell", bps=planned.cost_bps_round_trip / 2.0 if planned.cost_bps_round_trip else 0.0, fee=fee)
            proceeds = qty * sell
            state.cash += proceeds
            state.fees_paid += qty * (raw_exit - sell)
            state.shares[sid] = 0.0
            events.append(LedgerEvent(day, "sell", sid, proceeds, -qty, raw_exit, "raw_open_exit"))
            pos["exit_open"] = raw_exit
            pos["net_return"] = _trade_net(pos, proceeds)
            trades.append(pos)
            del open_positions[sid]

        prior = None
        for candidate in reversed(days):
            if candidate < day:
                prior = candidate
                break
        new_rows = sorted(by_day.get(prior, []) if prior is not None else [], key=lambda r: (-float(r.get("score") or 0), r["security_id"]))
        for row in new_rows:
            sid = str(row["security_id"])
            if sid in open_positions or state.shares.get(sid, 0.0):
                continue
            if row.get("status") != "eligible":
                continue
            series = panel[sid]
            if series.halted and (series.bar_halted is None):
                # series.halted is an end-of-sample flag; do not apply it to every date.
                pass
            planned = plan_trade(
                series,
                prior,
                holding_sessions,
                adv20=float(row.get("adv20") or 0.0),
                fee=fee,
                cost_multiple=cost_multiple,
            )
            if planned.entry_session != day or planned.entry_open is None:
                unfilled += 1
                continue
            raw_px = _raw_open(series, day)
            if raw_px is None:
                unfilled += 1
                events.append(LedgerEvent(day, "reject", sid, 0.0, 0.0, None, "NO_RAW_OPEN"))
                continue
            bps = slippage_bps(float(row.get("adv20") or 0.0)) * cost_multiple
            buy = apply_cost(raw_px, side="buy", bps=bps, fee=fee)
            notional = float(row.get("notional") or 0.0)
            if notional <= 0:
                if not allow_implicit_sizing:
                    unfilled += 1
                    events.append(LedgerEvent(day, "reject", sid, 0.0, 0.0, None, "UNSIZED_ORDER"))
                    continue
                notional = min(state.cash, max(0.0, float(row.get("adv20") or 0.0) * 0.01))
            if notional <= 0 or buy <= 0 or notional > state.cash:
                unfilled += 1
                continue
            qty = notional / buy
            state.cash -= qty * buy
            state.fees_paid += qty * (buy - raw_px)
            state.shares[sid] = state.shares.get(sid, 0.0) + qty
            state.cost_basis[sid] = buy
            open_positions[sid] = {
                "security_id": sid,
                "signal_session": prior,
                "entry_session": day,
                "exit_session": planned.exit_session,
                "notional": qty * buy,
                "shares": qty,
                "adv20": float(row.get("adv20") or 0.0),
                "entry_raw_open": raw_px,
                "cash_out": qty * buy,
                "cash_in": 0.0,
            }
            events.append(LedgerEvent(day, "buy", sid, -qty * buy, qty, raw_px, "raw_open_entry"))

        state.unknown_sids.clear()
        known_value = 0.0
        unknown_exposure: list[str] = []
        for sid, qty in list(state.shares.items()):
            if not qty:
                continue
            if sid in unknown and day >= unknown[sid]:
                state.status[sid] = "TERMINAL_VALUE_UNKNOWN"
                state.unknown_sids.add(sid)
                unknown_exposure.append(sid)
                continue
            series = panel[sid]
            mark, mark_status = _mark_price(series, day)
            if mark is None:
                state.status[sid] = mark_status
                state.unknown_sids.add(sid)
                unknown_exposure.append(sid)
                continue
            state.marks[sid] = mark
            state.status[sid] = mark_status
            known_value += qty * mark
        identity_ok = not unknown_exposure
        partial = state.cash + state.receivables + known_value
        equity = None if unknown_exposure else partial
        daily.append(
            {
                "session": day.isoformat(),
                "equity": equity,
                "cash": state.cash,
                "receivables": state.receivables,
                "known_positions_value": known_value,
                "partial_value": partial,
                "unknown_exposure": unknown_exposure,
                "positions": sum(1 for qty in state.shares.values() if qty),
                "identity_ok": identity_ok and (equity_identity_holds(state) if identity_ok else False),
            }
        )

    ending = daily[-1]["equity"] if daily else capital
    return {
        "starting_capital": capital,
        "ending_equity": ending,
        "status": "LEDGER",
        "events": [
            {
                "session": event.session.isoformat(),
                "kind": event.kind,
                "security_id": event.security_id,
                "cash_delta": event.cash_delta,
                "shares_delta": event.shares_delta,
                "price": event.price,
                "note": event.note,
            }
            for event in events
        ],
        "trades": trades,
        "daily_equity": daily,
        "unfilled": unfilled,
        "right_censored": sorted(set(right_censored)),
        "fees_paid": state.fees_paid,
    }


def _trade_net(pos: Mapping[str, Any], exit_cash: float) -> float:
    cash_out = float(pos.get("cash_out") or pos.get("notional") or 0.0)
    if cash_out <= 0:
        return 0.0
    return (float(exit_cash) - cash_out) / cash_out
