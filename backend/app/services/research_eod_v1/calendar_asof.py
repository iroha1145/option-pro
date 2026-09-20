"""Session cutoffs. ``as_of`` is an argument — never wall-clock ``now``."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.services.market_calendar import (
    ET,
    early_close_minutes,
    is_trading_day,
    next_trading_day,
    previous_trading_day,
)

NEW_YORK = ZoneInfo("America/New_York")
LIVE_CAPTURE = "live_capture"
HISTORICAL_RECONSTRUCTION = "historical_reconstruction"
VENDOR_WITHOUT_FINALIZED_FIELD_POLICY = "NEXT_DAY_CONFIRM"
VENDOR_WITHOUT_FINALIZED_LAG_SESSIONS = 1


def require_aware(moment: datetime, *, name: str = "as_of") -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return moment


def session_close_minutes(session: date) -> int:
    return early_close_minutes(session) or 16 * 60


def last_completed_session(as_of: datetime) -> date:
    local = require_aware(as_of).astimezone(ET)
    candidate = local.date()
    close_minutes = session_close_minutes(candidate)
    if not is_trading_day(candidate) or local.hour * 60 + local.minute < close_minutes:
        candidate = previous_trading_day(candidate, include_start=False)
    return candidate


def last_known_finalized_session(
    as_of: datetime,
    *,
    last_proven_finalized: date | None = None,
) -> date:
    """Calendar close is not vendor finalization. Without proof, keep the last proven day."""

    calendar = last_completed_session(as_of)
    if last_proven_finalized is None:
        return calendar
    proven = last_proven_finalized
    if not is_trading_day(proven):
        proven = previous_trading_day(proven, include_start=True)
    return min(calendar, proven)


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


def capture_as_of(now: datetime) -> datetime:
    """Research runners pass the real clock. They must not jump to a future close."""

    return require_aware(now, name="now")


def disclosed_source_finalized_through(
    as_of: datetime,
    *,
    vendor_finalized_through: date | None = None,
) -> date:
    """Shared research finalization policy for live_capture and reconstruction.

    A vendor ``finalized_at`` / ``finalized_through`` field is used when present.
    Yahoo and similar sources have no such field. Missing that field is not a
    permanent freeze: research uses a disclosed next-session confirmation lag.
    The recapture clock is never treated as vendor finalization.
    """

    if vendor_finalized_through is not None:
        return last_complete_eod_session(as_of, source_finalized_through=vendor_finalized_through)
    calendar = last_completed_session(as_of)
    return shift_sessions(calendar, -VENDOR_WITHOUT_FINALIZED_LAG_SESSIONS)


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


def holding_exit_session(entry_session: date, holding_sessions: int) -> date:
    """Exit open of T+1+H: H complete sessions after the entry session."""

    if type(holding_sessions) is not int or holding_sessions < 1:
        raise ValueError("holding_sessions must be a positive integer")
    return shift_sessions(entry_session, holding_sessions)
