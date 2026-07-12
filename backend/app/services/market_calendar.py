"""Single source of truth for the NYSE trading calendar used by the app."""

from __future__ import annotations

from datetime import date, datetime, time as datetime_time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    last = (
        date(year, month + 1, 1) - timedelta(days=1)
        if month < 12
        else date(year, 12, 31)
    )
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _monday_if_sunday(value: date) -> date:
    return value + timedelta(days=1) if value.weekday() == 6 else value


def _friday_or_monday_observed(value: date) -> date:
    if value.weekday() == 5:
        return value - timedelta(days=1)
    if value.weekday() == 6:
        return value + timedelta(days=1)
    return value


def _new_year_observed(value: date) -> date:
    """NYSE does not close the preceding Friday when New Year is Saturday."""

    return _monday_if_sunday(value)


@lru_cache(maxsize=32)
def market_holidays(year: int) -> dict[date, str]:
    holidays = {
        _new_year_observed(date(year, 1, 1)): "new_year",
        _nth_weekday(year, 1, 0, 3): "martin_luther_king_jr_day",
        _nth_weekday(year, 2, 0, 3): "presidents_day",
        _easter(year) - timedelta(days=2): "good_friday",
        _last_weekday(year, 5, 0): "memorial_day",
        _friday_or_monday_observed(date(year, 7, 4)): "independence_day",
        _nth_weekday(year, 9, 0, 1): "labor_day",
        _nth_weekday(year, 11, 3, 4): "thanksgiving",
        _friday_or_monday_observed(date(year, 12, 25)): "christmas",
    }
    if year >= 2022:
        holidays[
            _friday_or_monday_observed(date(year, 6, 19))
        ] = "juneteenth"

    # A Sunday New Year in the next calendar year is observed on Monday in
    # that next year. A Saturday New Year deliberately adds no prior-Friday
    # closure, which keeps 2021-12-31 as a normal NYSE session.
    next_new_year = date(year + 1, 1, 1)
    if next_new_year.weekday() == 6:
        holidays[next_new_year + timedelta(days=1)] = "new_year"
    return holidays


def early_close_minutes(value: date) -> int | None:
    thanksgiving = _nth_weekday(value.year, 11, 3, 4)
    early_dates = {
        thanksgiving + timedelta(days=1),
        date(value.year, 12, 24),
    }
    july3 = date(value.year, 7, 3)
    if july3.weekday() < 5:
        early_dates.add(july3)
    return 13 * 60 if value in early_dates and is_trading_day(value) else None


def options_close_minutes(value: date) -> int | None:
    """Close time for standard US equity options on a valid trading day."""

    if not is_trading_day(value):
        return None
    equity_close = early_close_minutes(value)
    return equity_close + 15 if equity_close is not None else 16 * 60


def is_trading_day(value: date) -> bool:
    return value.weekday() < 5 and value not in market_holidays(value.year)


def next_trading_day(start: date, *, include_start: bool = False) -> date:
    candidate = start if include_start else start + timedelta(days=1)
    for _ in range(15):
        if is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise RuntimeError("Unable to determine the next US trading day")


def market_datetime(value: date, minutes: int) -> datetime:
    return datetime.combine(
        value,
        datetime_time(hour=minutes // 60, minute=minutes % 60),
        tzinfo=ET,
    )


# Compatibility aliases for callers that historically imported the helpers
# from app.api.market.
_early_close_minutes = early_close_minutes
_is_trading_day = is_trading_day
_market_holidays = market_holidays
_next_trading_day = next_trading_day
_market_datetime = market_datetime


__all__ = [
    "ET",
    "early_close_minutes",
    "is_trading_day",
    "market_datetime",
    "market_holidays",
    "next_trading_day",
    "options_close_minutes",
]
