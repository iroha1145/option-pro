"""Injectable US market clock for the Breakout Radar worker."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timezone
from typing import Callable

from app.services.market_calendar import (
    ET,
    early_close_minutes as _early_close_minutes,
    is_trading_day as _is_trading_day,
    market_holidays as _market_holidays,
    next_trading_day as _next_trading_day,
)
from app.services.breakouts.models import DiscoveryProfile, MarketSession


@dataclass(frozen=True)
class MarketClockSnapshot:
    as_of: datetime
    market_time: datetime
    session: MarketSession
    trading_date: date
    market_open: datetime
    market_close: datetime
    next_transition: datetime
    holiday: str | None
    early_close: bool

    @property
    def open(self) -> datetime:
        return self.market_open

    @property
    def close(self) -> datetime:
        return self.market_close


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


def _at_minutes(day: date, minutes: int) -> datetime:
    return datetime.combine(
        day,
        datetime_time(hour=minutes // 60, minute=minutes % 60),
        tzinfo=ET,
    )


class MarketClock:
    """Classify sessions using the same holiday rules as ``app.api.market``."""

    def __init__(
        self,
        now: Callable[[], datetime] = _default_now,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._now = now
        self._monotonic = monotonic

    def now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("market clock requires a timezone-aware datetime")
        return value.astimezone(timezone.utc)

    def monotonic(self) -> float:
        return float(self._monotonic())

    def snapshot(self, as_of: datetime | None = None) -> MarketClockSnapshot:
        value = as_of if as_of is not None else self.now()
        if value.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        as_of_utc = value.astimezone(timezone.utc)
        market_time = as_of_utc.astimezone(ET)
        today = market_time.date()
        minutes = market_time.hour * 60 + market_time.minute
        trading_today = _is_trading_day(today)
        early_minutes = _early_close_minutes(today) if trading_today else None
        regular_close = early_minutes or 16 * 60

        if not trading_today:
            session = MarketSession.CLOSED
        elif 4 * 60 <= minutes < 9 * 60 + 30:
            session = MarketSession.PREMARKET
        elif 9 * 60 + 30 <= minutes < regular_close:
            session = MarketSession.REGULAR
        elif regular_close <= minutes < 20 * 60:
            session = MarketSession.POSTMARKET
        else:
            session = MarketSession.CLOSED

        if trading_today and minutes < 20 * 60:
            trading_date = today
        elif trading_today and minutes < 4 * 60:
            trading_date = today
        else:
            trading_date = _next_trading_day(today)

        market_open = _at_minutes(trading_date, 9 * 60 + 30)
        market_close = _at_minutes(
            trading_date,
            _early_close_minutes(trading_date) or 16 * 60,
        )

        if session is MarketSession.PREMARKET:
            next_transition = _at_minutes(today, 9 * 60 + 30)
        elif session is MarketSession.REGULAR:
            next_transition = _at_minutes(today, regular_close)
        elif session is MarketSession.POSTMARKET:
            next_transition = _at_minutes(today, 20 * 60)
        elif trading_today and minutes < 4 * 60:
            next_transition = _at_minutes(today, 4 * 60)
        else:
            next_transition = _at_minutes(trading_date, 4 * 60)

        return MarketClockSnapshot(
            as_of=as_of_utc,
            market_time=market_time,
            session=session,
            trading_date=trading_date,
            market_open=market_open,
            market_close=market_close,
            next_transition=next_transition,
            holiday=_market_holidays(today.year).get(today),
            early_close=early_minutes is not None,
        )

    @staticmethod
    def next_supported_session_at(snapshot: MarketClockSnapshot) -> str:
        """Return the next premarket/regular discovery time in UTC."""

        local = snapshot.market_time
        today = local.date()
        minutes = local.hour * 60 + local.minute
        if _is_trading_day(today) and minutes < 4 * 60:
            discovery_day = today
        else:
            discovery_day = _next_trading_day(today)
        return (
            _at_minutes(discovery_day, 4 * 60)
            .astimezone(timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )

    @staticmethod
    def profile_for(session: MarketSession) -> DiscoveryProfile:
        if session is MarketSession.PREMARKET:
            return DiscoveryProfile.PREMARKET_GAPPERS
        return DiscoveryProfile.REGULAR_MOVERS

    @staticmethod
    def interval_seconds(snapshot: MarketClockSnapshot, settings: object) -> int:
        if snapshot.session is MarketSession.PREMARKET:
            return int(getattr(settings, "scan_interval_premarket_seconds"))
        if snapshot.session is MarketSession.REGULAR:
            return int(getattr(settings, "scan_interval_regular_seconds"))
        return int(getattr(settings, "scan_interval_closed_seconds"))


__all__ = ["MarketClock", "MarketClockSnapshot"]
