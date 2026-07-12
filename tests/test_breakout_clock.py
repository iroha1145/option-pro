from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import MarketSession


NY = ZoneInfo("America/New_York")


def test_market_clock_session_boundaries_and_weekend() -> None:
    clock = MarketClock()
    day = datetime(2026, 7, 10, tzinfo=NY)
    cases = {
        (3, 59): MarketSession.CLOSED,
        (4, 0): MarketSession.PREMARKET,
        (9, 29): MarketSession.PREMARKET,
        (9, 30): MarketSession.REGULAR,
        (16, 0): MarketSession.POSTMARKET,
        (20, 0): MarketSession.CLOSED,
    }
    for (hour, minute), expected in cases.items():
        assert clock.snapshot(day.replace(hour=hour, minute=minute)).session is expected

    weekend = clock.snapshot(datetime(2026, 7, 11, 12, 0, tzinfo=NY))
    assert weekend.session is MarketSession.CLOSED
    assert weekend.next_transition > weekend.market_time


def test_market_clock_honors_early_close_boundary() -> None:
    clock = MarketClock()
    day = datetime(2026, 11, 27, tzinfo=NY)
    before = clock.snapshot(day.replace(hour=12, minute=59))
    after = clock.snapshot(day.replace(hour=13, minute=0))
    assert before.early_close is True
    assert before.session is MarketSession.REGULAR
    assert after.session is MarketSession.POSTMARKET
