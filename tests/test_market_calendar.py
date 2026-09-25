from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import MarketSession
from app.services.market_calendar import (
    SPECIAL_CLOSURES,
    early_close_minutes,
    is_trading_day,
    last_completed_trading_day,
    market_holidays,
    next_trading_day,
    options_close_minutes,
    previous_trading_day,
    prior_trading_sessions,
    trading_days_between,
)


def test_new_year_on_saturday_does_not_close_the_preceding_friday() -> None:
    assert date(2021, 12, 31) not in market_holidays(2021)
    assert is_trading_day(date(2021, 12, 31)) is True
    assert next_trading_day(date(2021, 12, 30)) == date(2021, 12, 31)


@pytest.mark.parametrize(
    "holiday",
    [
        date(2022, 1, 17),
        date(2022, 7, 4),
        date(2022, 12, 26),
    ],
)
def test_known_nyse_holidays_are_closed(holiday: date) -> None:
    assert is_trading_day(holiday) is False


def test_early_close_is_shared_with_breakout_clock() -> None:
    early_day = date(2026, 11, 27)
    assert early_close_minutes(early_day) == 13 * 60

    before_close = MarketClock(
        now=lambda: datetime(2026, 11, 27, 17, 59, tzinfo=timezone.utc)
    ).snapshot()
    after_close = MarketClock(
        now=lambda: datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    ).snapshot()

    assert before_close.session is MarketSession.REGULAR
    assert after_close.session is MarketSession.POSTMARKET
    # Single-stock options (the default) stop with the equity close; index
    # options keep their extra 15 minutes on half days too.
    assert options_close_minutes(early_day) == 13 * 60
    assert options_close_minutes(early_day, style="index") == 13 * 60 + 15
    assert options_close_minutes(date(2026, 11, 30)) == 16 * 60
    assert options_close_minutes(date(2026, 11, 30), style="index") == 16 * 60 + 15


@pytest.mark.parametrize(
    ("half_day", "equity_close", "index_close"),
    [
        (date(2025, 7, 3), 13 * 60, 13 * 60 + 15),
        (date(2025, 11, 28), 13 * 60, 13 * 60 + 15),
        (date(2025, 12, 24), 13 * 60, 13 * 60 + 15),
        (date(2026, 12, 24), 13 * 60, 13 * 60 + 15),
    ],
)
def test_half_day_options_close_keeps_the_two_styles_apart(
    half_day: date, equity_close: int, index_close: int
) -> None:
    assert early_close_minutes(half_day) == 13 * 60
    assert options_close_minutes(half_day) == equity_close
    assert options_close_minutes(half_day, style="equity") == equity_close
    assert options_close_minutes(half_day, style="index") == index_close


def test_options_close_is_none_when_the_market_is_closed_and_rejects_unknown_styles() -> None:
    # 2026-07-03 is the observed Independence Day, not a half day.
    assert options_close_minutes(date(2026, 7, 3)) is None
    assert options_close_minutes(date(2025, 1, 9), style="index") is None
    with pytest.raises(ValueError, match="options close style"):
        options_close_minutes(date(2026, 11, 30), style="spx")


@pytest.mark.parametrize(
    "closure",
    [
        date(1994, 4, 27),
        date(2001, 9, 11),
        date(2001, 9, 14),
        date(2004, 6, 11),
        date(2007, 1, 2),
        date(2012, 10, 29),
        date(2012, 10, 30),
        date(2018, 12, 5),
        date(2025, 1, 9),
    ],
)
def test_unscheduled_closures_are_not_trading_days(closure: date) -> None:
    assert closure.weekday() < 5
    assert closure in SPECIAL_CLOSURES
    assert is_trading_day(closure) is False
    assert market_holidays(closure.year)[closure] == SPECIAL_CLOSURES[closure]
    assert early_close_minutes(closure) is None


def test_session_walks_skip_unscheduled_closures() -> None:
    # Carter national day of mourning, Thursday 2025-01-09.
    assert next_trading_day(date(2025, 1, 8)) == date(2025, 1, 10)
    assert previous_trading_day(date(2025, 1, 10)) == date(2025, 1, 8)
    assert prior_trading_sessions(date(2025, 1, 13), 3) == [
        date(2025, 1, 7),
        date(2025, 1, 8),
        date(2025, 1, 10),
    ]
    assert trading_days_between(date(2025, 1, 8), date(2025, 1, 10)) == 1
    # Hurricane Sandy closed Monday and Tuesday.
    assert trading_days_between(date(2012, 10, 26), date(2012, 10, 31)) == 1
    assert next_trading_day(date(2012, 10, 26)) == date(2012, 10, 31)
    # September 2001: four closed sessions in a row.
    assert next_trading_day(date(2001, 9, 10)) == date(2001, 9, 17)


def test_last_completed_session_never_lands_on_a_closure() -> None:
    evening = datetime(2025, 1, 9, 23, 0, tzinfo=timezone.utc)
    assert last_completed_trading_day(evening) == date(2025, 1, 8)
    assert last_completed_trading_day(datetime(2018, 12, 5, 22, 0, tzinfo=timezone.utc)) == date(2018, 12, 4)
