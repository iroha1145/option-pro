"""Explicit cash / share / receivable ledger. Execution uses raw unadjusted opens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping, Sequence

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

    def marked_equity(self) -> float:
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
    rebuilt = state.cash + state.receivables + sum(
        qty * state.marks[sid] for sid, qty in state.shares.items() if qty
    )
    return abs(equity - rebuilt) <= tol


def _raw_open(series: SecuritySeries, session: date) -> float | None:
    if session not in series.dates:
        return None
    idx = series.dates.index(session)
    raw = series.raw_open if series.raw_open is not None else series.open
    value = float(raw[idx])
    return value if value > 0 else None


def _mark_price(series: SecuritySeries, session: date) -> tuple[float | None, str]:
    if session not in series.dates:
        return None, "MISSING_SESSION"
    idx = series.dates.index(session)
    if series.halted or (float(series.volume[idx]) <= 0 if idx < len(series.volume) else False):
        close = float(series.close[idx]) if idx < len(series.close) else None
        if close and close > 0:
            return close, "HALTED_LAST_CLOSE"
        return None, "HALTED_NO_MARK"
    close = float(series.close[idx])
    if close > 0:
        return close, "CLOSE"
    return None, "MISSING_CLOSE"


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
    cash_acquisitions: Mapping[str, float] | None = None,
    unknown_terminals: set[str] | None = None,
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
    unknown = set(unknown_terminals or ())
    deals = dict(cash_acquisitions or {})

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
        # Corporate actions first.
        for sid, series in panel.items():
            qty = state.shares.get(sid, 0.0)
            for split_day, ratio in series.splits:
                if split_day == day and qty and ratio > 0:
                    state.shares[sid] = qty * ratio
                    if sid in state.marks:
                        state.marks[sid] = state.marks[sid] / ratio
                    events.append(LedgerEvent(day, "split", sid, 0.0, qty * (ratio - 1.0), ratio, "split_wealth_conserved"))
                    qty = state.shares[sid]
            for div_day, amount in series.dividends:
                if div_day == day and qty and amount:
                    cash_in = qty * float(amount)
                    state.cash += cash_in
                    events.append(LedgerEvent(day, "dividend", sid, cash_in, 0.0, amount, "cash_dividend"))
            if sid in deals and day >= start:
                qty = state.shares.get(sid, 0.0)
                if qty:
                    cash_in = qty * float(deals[sid])
                    state.cash += cash_in
                    state.shares[sid] = 0.0
                    state.marks.pop(sid, None)
                    events.append(LedgerEvent(day, "cash_acquisition", sid, cash_in, -qty, deals[sid], "terminal_cash"))
                    open_positions.pop(sid, None)
            if sid in unknown and sid in state.shares and state.shares[sid]:
                state.status[sid] = "TERMINAL_VALUE_UNKNOWN"
                right_censored.append(sid)

        # Exits at the raw open. Registered yesterday; the observed open cannot cancel them.
        for sid, pos in list(open_positions.items()):
            if pos["exit_session"] != day:
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
            if planned.label_status != "MATURE" or planned.exit_open is None:
                state.status[sid] = planned.label_status
                right_censored.append(sid)
                continue
            qty = state.shares.get(sid, 0.0)
            sell = apply_cost(planned.exit_open, side="sell", bps=planned.cost_bps_round_trip / 2.0 if planned.cost_bps_round_trip else 0.0, fee=fee)
            proceeds = qty * sell
            state.cash += proceeds
            state.fees_paid += qty * (planned.exit_open - sell)
            state.shares[sid] = 0.0
            events.append(LedgerEvent(day, "sell", sid, proceeds, -qty, planned.exit_open, "raw_open_exit"))
            trades.append({**pos, "exit_open": planned.exit_open, "net_return": planned.net_return})
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
            if series.halted:
                unfilled += 1
                events.append(LedgerEvent(day, "reject", sid, 0.0, 0.0, None, "HALTED"))
                continue
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
                # Size by remaining cash if the caller did not pre-size.
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
            }
            events.append(LedgerEvent(day, "buy", sid, -qty * buy, qty, raw_px, "raw_open_entry"))

        identity_ok = True
        for sid, qty in list(state.shares.items()):
            if not qty:
                continue
            if sid in unknown:
                identity_ok = False
                state.status[sid] = "TERMINAL_VALUE_UNKNOWN"
                continue
            series = panel[sid]
            mark, mark_status = _mark_price(series, day)
            if mark is None:
                identity_ok = False
                state.status[sid] = mark_status
                continue
            state.marks[sid] = mark
            state.status[sid] = mark_status
        try:
            equity = state.marked_equity() if identity_ok else state.cash + state.receivables
        except ValueError:
            identity_ok = False
            equity = state.cash + state.receivables
        daily.append(
            {
                "session": day.isoformat(),
                "equity": equity,
                "cash": state.cash,
                "receivables": state.receivables,
                "positions": sum(1 for qty in state.shares.values() if qty),
                "identity_ok": identity_ok and equity_identity_holds(state) if identity_ok else False,
            }
        )

    return {
        "starting_capital": capital,
        "ending_equity": daily[-1]["equity"] if daily else capital,
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
