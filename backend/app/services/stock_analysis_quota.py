"""One new stock analysis per account between consecutive US market closes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.services.market_calendar import (
    early_close_minutes,
    last_completed_trading_day,
    market_datetime,
    next_trading_day,
)


@dataclass(frozen=True)
class StockAnalysisPeriod:
    start: datetime
    reset_at: datetime

    @property
    def key(self) -> str:
        return self.start.isoformat()


def stock_analysis_period(now: datetime | None = None) -> StockAnalysisPeriod:
    observed = now or datetime.now(timezone.utc)
    if observed.tzinfo is None or observed.utcoffset() is None:
        raise ValueError("stock analysis time must be timezone-aware")
    session = last_completed_trading_day(observed)
    following = next_trading_day(session)
    start = market_datetime(session, early_close_minutes(session) or 16 * 60)
    reset = market_datetime(following, early_close_minutes(following) or 16 * 60)
    return StockAnalysisPeriod(
        start=start.astimezone(timezone.utc),
        reset_at=reset.astimezone(timezone.utc),
    )


class StockAnalysisQuotaExceeded(RuntimeError):
    def __init__(self, period: StockAnalysisPeriod):
        super().__init__("stock_analysis_daily_limit")
        self.period = period


def public_stock_quota(period: StockAnalysisPeriod, *, used: bool) -> dict:
    return {
        "limit": 1,
        "used": int(used),
        "remaining": int(not used),
        "period_start": period.key,
        "reset_at": period.reset_at.isoformat(),
    }
