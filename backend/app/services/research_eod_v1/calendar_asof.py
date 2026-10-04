"""Session cutoffs. ``as_of`` is an argument — never wall-clock ``now``."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    last_completed_trading_day,
    next_trading_day,
    previous_trading_day,
)

NEW_YORK = ZoneInfo("America/New_York")
LIVE_CAPTURE = "live_capture"
HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
VENDOR_WITHOUT_FINALIZED_FIELD_POLICY = "NEXT_DAY_CONFIRM"
VENDOR_WITHOUT_FINALIZED_LAG_SESSIONS = 1
# Delayed vendor bars for the session that just closed keep changing for a
# while after the bell (closing auction prints, late trade reports). Live EOD
# runs, scheduled or manual, count a session as complete only after this.
LIVE_SETTLE_BUFFER = timedelta(minutes=60)


def require_aware(moment: datetime, *, name: str = "as_of") -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def session_close_minutes(session: date) -> int:
    return early_close_minutes(session) or 16 * 60


def last_completed_session(as_of: datetime) -> date:
    return last_completed_trading_day(require_aware(as_of))


def last_complete_eod_session(
    as_of: datetime,
    *,
    source_finalized_through: date | None = None,
    late_securities: tuple[str, ...] = (),
) -> date:
    """Calendar close plus vendor finalization. Never invents a later session than ``as_of``.

    ``late_securities`` is recorded for audits; a single late name does not
    invent a later common session. Callers must drop those names from EOD pools.
    """

    del late_securities  # audit-only; session is the intersection close, not a promotion
    session = last_completed_session(as_of)
    if source_finalized_through is None:
        return session
    finalized = source_finalized_through
    if not is_trading_day(finalized):
        finalized = previous_trading_day(finalized, include_start=True)
    return min(session, finalized)


def settled_eod_session(
    as_of: datetime,
    *,
    source_finalized_through: date | None = None,
) -> date:
    """Latest session whose close is at least ``LIVE_SETTLE_BUFFER`` before ``as_of``."""

    # Subtract in UTC: aware-datetime arithmetic in a DST zone is wall-clock arithmetic.
    return last_complete_eod_session(
        require_aware(as_of).astimezone(timezone.utc) - LIVE_SETTLE_BUFFER,
        source_finalized_through=source_finalized_through,
    )


def eod_evaluation_as_of(session: date) -> datetime:
    """Shared post-close evaluation instant for historical reconstruction."""

    close = session_close_at(session)
    return close + timedelta(minutes=30)


def session_is_partial(session: date, as_of: datetime) -> bool:
    local = require_aware(as_of).astimezone(ET)
    if local.date() != session:
        return local.date() < session
    return local.hour * 60 + local.minute < session_close_minutes(session)


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
