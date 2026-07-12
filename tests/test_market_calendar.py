from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import MarketSession
from app.services.market_calendar import (
    early_close_minutes,
    is_trading_day,
    market_holidays,
    next_trading_day,
    options_close_minutes,
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
    assert options_close_minutes(early_day) == 13 * 60 + 15
    assert options_close_minutes(date(2026, 11, 30)) == 16 * 60
