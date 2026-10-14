"""Vendor-neutral research data contract. Providers never feed raw JSON into compute."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence


@dataclass(frozen=True)
class ResearchBar:
    security_id: str
    session_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    raw_open: float | None
    raw_close: float | None
    volume: float | None
    dollar_volume: float | None
    tri: float | None
    volume_scope: str = "UNKNOWN"
    price_adjustment: str = "unverified"
    volume_adjustment: str = "unverified"
    missing: bool = False
    halted: bool = False
    economic_known_at: datetime | None = None
    source_published_at: datetime | None = None
    retrieved_at: datetime | None = None
    finalized_at: datetime | None = None
    vintage_status: str = "download_time_not_pit"
    partial: bool = False


def validate_research_bars(bars: Sequence[ResearchBar]) -> None:
    seen: set[date] = set()
    previous: date | None = None
    for bar in bars:
        if bar.session_date in seen:
            raise ValueError(f"duplicate session_date {bar.session_date}")
        seen.add(bar.session_date)
        if previous is not None and bar.session_date < previous:
            raise ValueError("session dates must be sorted")
        previous = bar.session_date
        prices = [bar.open, bar.high, bar.low, bar.close, bar.raw_open, bar.raw_close]
        for price in prices:
            if price is None:
                continue
            if price != price or price in (float("inf"), float("-inf")):
                raise ValueError("non-finite price")
            if price <= 0:
                raise ValueError("non-positive price")
        if None not in (bar.open, bar.high, bar.low, bar.close):
            top = max(bar.open, bar.close)  # type: ignore[arg-type]
            bottom = min(bar.open, bar.close)  # type: ignore[arg-type]
            if bar.high < top or bar.low > bottom:  # type: ignore[operator]
                raise ValueError("OHLC relationship violated")
