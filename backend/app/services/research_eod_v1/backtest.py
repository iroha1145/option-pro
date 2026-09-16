"""T-close signal, T+1 open entry, fixed H-session hold. No peeking at the open."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import holding_exit_session, next_session
from app.services.research_eod_v1.constants import SLIPPAGE_BPS
from app.services.research_eod_v1.paths import ensure_reference_on_path
from app.services.research_eod_v1.series import SecuritySeries

ensure_reference_on_path()
from registry import position_capacity  # type: ignore


def slippage_bps(adv20: float) -> float:
    for floor, bps in SLIPPAGE_BPS:
        if adv20 >= floor:
            return bps
    return 25.0


def apply_cost(price: float, *, side: str, bps: float, fee: float = 0.0) -> float:
    slip = price * (bps / 10_000.0)
    if side == "buy":
        return price + slip + fee
    return max(0.0, price - slip - fee)


@dataclass(frozen=True)
class PlannedTrade:
    security_id: str
    signal_session: date
    entry_session: date
    exit_session: date
    holding_sessions: int
    entry_open: float | None
    exit_open: float | None
    label_status: str
    gross_return: float | None
    net_return: float | None
    cost_bps_round_trip: float | None


def plan_trade(
    series: SecuritySeries,
    signal_session: date,
    holding_sessions: int,
    *,
    adv20: float,
    fee: float = 0.0,
    cost_multiple: float = 1.0,
    peek_entry_open: bool = False,
) -> PlannedTrade:
    """Signal is known at ``signal_session`` close. Entry open is not an input."""

    if peek_entry_open:
        raise ValueError("cannot condition a same-open fill on the observed open")
    dates = series.dates
    if signal_session not in dates:
        return PlannedTrade(series.security_id, signal_session, signal_session, signal_session,
                            holding_sessions, None, None, "SIGNAL_NOT_IN_SERIES", None, None, None)
    entry_session = next_session(signal_session)
    exit_session = holding_exit_session(entry_session, holding_sessions)
    def _raw_open_at(session: date) -> float:
        idx = dates.index(session)
        raw = series.raw_open if series.raw_open is not None else series.open
        return float(raw[idx])

    if entry_session not in dates:
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, None, None, "IMMATURE_ENTRY", None, None, None)
    if exit_session not in dates:
        entry_open = _raw_open_at(entry_session)
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, entry_open, None, "IMMATURE_LABEL", None, None, None)
    entry_open = _raw_open_at(entry_session)
    exit_open = _raw_open_at(exit_session)
    bps = slippage_bps(adv20) * cost_multiple
    buy = apply_cost(entry_open, side="buy", bps=bps, fee=fee)
    sell = apply_cost(exit_open, side="sell", bps=bps, fee=fee)
    gross = exit_open / entry_open - 1.0 if entry_open > 0 else None
    net = sell / buy - 1.0 if buy > 0 else None
    return PlannedTrade(
        series.security_id, signal_session, entry_session, exit_session,
        holding_sessions, entry_open, exit_open, "MATURE", gross, net, 2.0 * bps,
    )


def simulate_portfolio(
    signals: Sequence[Mapping[str, Any]],
    panel: Mapping[str, SecuritySeries],
    *,
    capital: float,
    holding_sessions: int,
    profile: Mapping[str, Any],
    cost_multiple: float = 1.0,
) -> dict[str, Any]:
    """One position per security. Empty days stay in cash. No interest."""

    cash = float(capital)
    equity = float(capital)
    open_positions: dict[str, dict[str, Any]] = {}
    trades: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
    if not signals:
        return {
            "starting_capital": capital,
            "ending_equity": capital,
            "cash_interest": 0.0,
            "trades": trades,
            "daily_equity": daily,
            "unfilled": 0,
            "status": "ZERO_SIGNALS",
        }
    sessions = sorted({
        date.fromisoformat(row["session_date"]) if isinstance(row["session_date"], str) else row["session_date"]
        for row in signals
    })
    from app.services.research_eod_v1.calendar_asof import next_session as _next

    all_days = []
    if sessions:
        cursor = sessions[0]
        last = _next(sessions[-1])
        last = holding_exit_session(last, holding_sessions)
        while cursor <= last:
            all_days.append(cursor)
            try:
                cursor = _next(cursor)
            except RuntimeError:
                break
    by_day: dict[date, list[Mapping[str, Any]]] = {}
    for row in signals:
        day = date.fromisoformat(row["session_date"]) if isinstance(row["session_date"], str) else row["session_date"]
        by_day.setdefault(day, []).append(row)

    unfilled = 0
    for day in all_days:
        # Exits first at the open.
        for sid, pos in list(open_positions.items()):
            if pos["exit_session"] == day:
                series = panel[sid]
                planned = plan_trade(
                    series, pos["signal_session"], holding_sessions,
                    adv20=pos["adv20"], cost_multiple=cost_multiple,
                )
                if planned.label_status != "MATURE" or planned.net_return is None:
                    continue
                proceeds = pos["notional"] * (1.0 + planned.net_return)
                cash += proceeds
                trades.append({**pos, "net_return": planned.net_return, "exit_open": planned.exit_open})
                del open_positions[sid]
        # New entries only from yesterday's close signals — never today's open peek.
        prior = None
        for candidate in reversed(all_days):
            if candidate < day:
                prior = candidate
                break
        new_rows = by_day.get(prior, []) if prior is not None else []
        new_rows = sorted(new_rows, key=lambda r: (-float(r.get("score") or 0), r["security_id"]))
        for row in new_rows:
            sid = row["security_id"]
            if sid in open_positions:
                continue
            if row.get("status") != "eligible":
                continue
            series = panel[sid]
            planned = plan_trade(
                series, prior, holding_sessions,
                adv20=float(row.get("adv20") or 0.0),
                cost_multiple=cost_multiple,
            )
            if planned.entry_session != day or planned.entry_open is None:
                continue
            close = planned.entry_open
            invalid = float(row.get("planned_invalidation") or 0.0)
            atr = float(row.get("atr") or 0.0)
            adv20 = float(row.get("adv20") or 0.0)
            if invalid <= 0 or invalid >= close or atr <= 0 or adv20 <= 0:
                unfilled += 1
                continue
            cap = position_capacity(cash + sum(p["notional"] for p in open_positions.values()), close, invalid, atr, adv20, profile)
            if cap.shares <= 0:
                unfilled += 1
                continue
            notional = cap.notional
            if notional > cash:
                unfilled += 1
                continue
            cash -= notional
            open_positions[sid] = {
                "security_id": sid,
                "signal_session": prior,
                "entry_session": day,
                "exit_session": planned.exit_session,
                "notional": notional,
                "shares": cap.shares,
                "adv20": adv20,
            }
        marked = cash
        for sid, pos in open_positions.items():
            series = panel[sid]
            if day in series.dates:
                idx = series.dates.index(day)
                marked += pos["shares"] * float(series.close[idx])
            else:
                marked += pos["notional"]
        equity = marked
        daily.append({"session": day.isoformat(), "equity": equity, "cash": cash, "positions": len(open_positions)})
    return {
        "starting_capital": capital,
        "ending_equity": equity,
        "cash_interest": 0.0,
        "trades": trades,
        "daily_equity": daily,
        "unfilled": unfilled,
        "status": "ENGINEERING_SIMULATION",
    }


def higher_cost_cannot_increase_net(base_net: float | None, stressed_net: float | None) -> bool:
    if base_net is None or stressed_net is None:
        return True
    return stressed_net <= base_net + 1e-12
