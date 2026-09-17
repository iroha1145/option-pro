"""Session cutoffs. ``as_of`` is an argument — never wall-clock ``now``."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
)

NEW_YORK = ZoneInfo("America/New_York")


def require_aware(moment: datetime, *, name: str = "as_of") -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def session_close_minutes(session: date) -> int:
    return early_close_minutes(session) or 16 * 60


def last_completed_session(as_of: datetime) -> date:
    local = require_aware(as_of).astimezone(ET)
    candidate = local.date()
    close_minutes = session_close_minutes(candidate)
    if not is_trading_day(candidate) or local.hour * 60 + local.minute < close_minutes:
        candidate = previous_trading_day(candidate, include_start=False)
    return candidate


def session_close_at(session: date) -> datetime:
    minutes = session_close_minutes(session)
    return datetime(
        session.year,
        session.month,
        session.day,
        minutes // 60,
        minutes % 60,
        tzinfo=NEW_YORK,
    )


def next_session(session: date) -> date:
    return next_trading_day(session, include_start=False)


def shift_sessions(session: date, steps: int) -> date:
    cursor = session
    if steps >= 0:
        for _ in range(steps):
            cursor = next_session(cursor)
        return cursor
    for _ in range(-steps):
        cursor = previous_trading_day(cursor, include_start=False)
    return cursor


def validate_information_cutoff(
    *,
    signal_time: datetime,
    session_close: datetime,
    source_available_at: datetime,
) -> None:
    for value, name in (
        (signal_time, "signal_time"),
        (session_close, "session_close"),
        (source_available_at, "source_available_at"),
    ):
        require_aware(value, name=name)
    if source_available_at < session_close:
        raise ValueError("completed-session data cannot be available before the session closes")
    if signal_time < source_available_at:
        raise ValueError("signal predates source availability")


def holding_exit_session(entry_session: date, holding_sessions: int) -> date:
    """Exit open of T+1+H: H complete sessions after the entry session."""

    if type(holding_sessions) is not int or holding_sessions < 1:
        raise ValueError("holding_sessions must be a positive integer")
    return shift_sessions(entry_session, holding_sessions)
