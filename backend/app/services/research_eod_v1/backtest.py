"""T-close signal, T+1 open entry, fixed H-session hold. No peeking at the open."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any, Mapping, Sequence

from app.services.research_eod_v1.calendar_asof import holding_exit_session, next_session
from app.services.research_eod_v1.constants import SLIPPAGE_BPS
from app.services.research_eod_v1.series import SecuritySeries


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
    def _raw_open_at(session: date) -> float | None:
        if session not in dates:
            return None
        idx = dates.index(session)
        raw = series.raw_open
        if raw is None:
            return None
        value = float(raw[idx])
        if value != value or value <= 0:
            return None
        return value

    if entry_session not in dates:
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, None, None, "IMMATURE_ENTRY", None, None, None)
    entry_open = _raw_open_at(entry_session)
    if entry_open is None:
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, None, None, "NO_RAW_OPEN", None, None, None)
    if exit_session not in dates:
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, entry_open, None, "IMMATURE_LABEL", None, None, None)
    exit_open = _raw_open_at(exit_session)
    if exit_open is None:
        return PlannedTrade(series.security_id, signal_session, entry_session, exit_session,
                            holding_sessions, entry_open, None, "NO_RAW_OPEN", None, None, None)
    bps = slippage_bps(adv20) * cost_multiple
    buy = apply_cost(entry_open, side="buy", bps=bps, fee=fee)
    sell = apply_cost(exit_open, side="sell", bps=bps, fee=fee)
    gross = exit_open / entry_open - 1.0 if entry_open > 0 else None
    price_net = sell / buy - 1.0 if buy > 0 else None
    hold_start = entry_session
    hold_end = exit_session
    corporate_in_hold = any(hold_start < day <= hold_end for day, _ratio in series.splits) or any(
        hold_start < day <= hold_end for day, _amount in series.dividends
    )
    if not corporate_in_hold:
        for action in getattr(series, "dividend_events", ()) or ():
            ex_day = action.get("ex_date") or action.get("session_date")
            if ex_day is None:
                continue
            ex_date = ex_day if isinstance(ex_day, date) else date.fromisoformat(str(ex_day)[:10])
            if hold_start < ex_date <= hold_end:
                corporate_in_hold = True
                break
    # Price-path net is not cashflow when splits or dividends occur. Ledger owns that.
    net = None if corporate_in_hold else price_net
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
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """One position per security. Empty days stay in cash. No interest.

    Sizing happens in the ledger at each decision using current cash.
    Future signals cannot rewrite earlier orders. Research dates are explicit
    or the panel bounds — never inferred from whichever signals happen to exist.
    """

    from app.services.research_eod_v1.ledger import simulate_ledger

    first = min((series.dates[0] for series in panel.values() if series.dates), default=None)
    last = max((series.dates[-1] for series in panel.values() if series.dates), default=None)
    research_start = start or first or date(2020, 1, 2)
    research_end = end or last or date(2020, 1, 2)
    result = simulate_ledger(
        start=research_start,
        end=research_end,
        panel=panel,
        capital=capital,
        holding_sessions=holding_sessions,
        signals=list(signals),
        cost_multiple=cost_multiple,
        allow_implicit_sizing=True,
        profile=profile,
    )
    result["cash_interest"] = 0.0
    result["research_start"] = research_start.isoformat()
    result["research_end"] = research_end.isoformat()
    if result["status"] == "LEDGER":
        result["status"] = "ENGINEERING_SIMULATION"
    return result


def np_finite(value: float) -> bool:
    return value == value and value not in (float("inf"), float("-inf"))


def higher_cost_cannot_increase_net(base_net: float | None, stressed_net: float | None) -> bool:
    if base_net is None or stressed_net is None:
        return True
    return stressed_net <= base_net + 1e-12
