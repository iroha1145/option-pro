"""Trading-calendar helpers for research labels.

n-session labels use the NYSE calendar. A missing ticker bar on the target
session stays missing; the endpoint is never silently extended.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    market_datetime,
    next_trading_day,
    previous_trading_day,
)


def session_close(day: date) -> datetime:
    return market_datetime(day, early_close_minutes(day) or 16 * 60)


def session_open(day: date) -> datetime:
    return market_datetime(day, 9 * 60 + 30)


def nth_trading_day(start: date, steps: int, *, after: bool = True) -> date | None:
    if steps < 1:
        raise ValueError("steps must be >= 1")
    cursor = start
    remaining = steps
    for _ in range(steps * 4 + 14):
        cursor = cursor + timedelta(days=1) if after else cursor - timedelta(days=1)
        if is_trading_day(cursor):
            remaining -= 1
            if remaining == 0:
                return cursor
    return None


def trading_days_inclusive(start: date, end: date) -> list[date]:
    if end < start:
        return []
    cursor = start
    out: list[date] = []
    while cursor <= end:
        if is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out


def require_exact_session(available: Iterable[date], target: date) -> date | None:
    """Return target only when it is present. Do not pick a later substitute."""

    available_set = set(available)
    return target if target in available_set else None


__all__ = [
    "ET",
    "nth_trading_day",
    "require_exact_session",
    "session_close",
    "session_open",
    "trading_days_inclusive",
    "is_trading_day",
    "next_trading_day",
    "previous_trading_day",
]
