"""Synthetic bars for engineering tests. Not market history."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from app.services.market_calendar import is_trading_day
from app.services.research_eod_v1.series import SecuritySeries

ET = ZoneInfo("America/New_York")


def trading_days(start: date, count: int) -> list[date]:
    days: list[date] = []
    cursor = start
    while len(days) < count:
        if is_trading_day(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def make_series(
    security_id: str,
    dates: list[date],
    close: np.ndarray,
    *,
    industry_id: str = "semiconductors",
    parent_industry_id: str = "technology",
    asset_track: str = "stock",
    theme_ids: tuple[str, ...] = ("semiconductors",),
    volume: float = 5_000_000,
    ticker: str | None = None,
    exchange: str = "NASDAQ",
    listing_country: str = "US",
    security_type: str = "CS",
) -> SecuritySeries:
    close = np.asarray(close, dtype=float)
    open_ = close * 0.998
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    vol = np.full(len(dates), volume)
    return SecuritySeries(
        security_id=security_id,
        ticker_at_signal=ticker or security_id,
        dates=list(dates),
        open=open_,
        high=high,
        low=low,
        close=close,
        raw_close=close.copy(),
        volume=vol,
        dollar_volume=close * vol,
        tri=close.copy(),
        turnover_is_proxy=True,
        volume_session_scope="regular",
        asset_track=asset_track,
        industry_id=industry_id,
        parent_industry_id=parent_industry_id,
        theme_ids=theme_ids,
        venue_metadata={
            "exchange": exchange,
            "listing_country": listing_country,
            "security_type": security_type,
            "mic": "XNAS" if exchange == "NASDAQ" else "XNYS",
        },
        source_available_at=datetime(dates[-1].year, dates[-1].month, dates[-1].day, 18, 0, tzinfo=ET),
    )


def trending_close(n: int, start: float = 50.0, drift: float = 0.15) -> np.ndarray:
    return start + drift * np.arange(n, dtype=float)


def as_of_after_close(session: date) -> datetime:
    return datetime(session.year, session.month, session.day, 17, 0, tzinfo=ET)


def utc_as_of_after_close(session: date) -> datetime:
    return as_of_after_close(session).astimezone(timezone.utc)
