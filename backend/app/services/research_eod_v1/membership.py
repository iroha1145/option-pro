"""Separate reference-panel peers from theme candidates.

Manual watchlists cannot bypass venue, security type, or ETF/stock tracks.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping

import numpy as np

from app.services.research_eod_v1.series import SecuritySeries
from app.services.research_eod_v1.venue import classify_venue


def current_theme_tickers(sector_id: str) -> frozenset[str]:
    from app.services.sectors import SECTORS

    sector = SECTORS.get(sector_id) or {}
    return frozenset(str(ticker).upper() for ticker in sector.get("tickers", []))


def has_complete_session_bar(series: SecuritySeries, session: date) -> bool:
    index = series.index_on_or_before(session)
    if index is None or series.dates[index] != session:
        return False
    if series.bar_partial is not None and bool(series.bar_partial[index]):
        return False
    return bool(
        np.isfinite(series.open[index])
        and np.isfinite(series.high[index])
        and np.isfinite(series.low[index])
        and np.isfinite(series.close[index])
    )


def economic_identity(series: SecuritySeries) -> str:
    """Theme tags do not change the economic identity of a security."""

    return series.security_id


def theme_membership(
    series: SecuritySeries,
    *,
    sector_id: str,
    target_track: str,
    extra_members: set[str] | None = None,
) -> tuple[bool, str]:
    """Theme / track / venue only. Missing T is a separate structured reject."""

    ticker = (series.ticker_at_signal or series.security_id).upper()
    members = set(current_theme_tickers(sector_id))
    if extra_members:
        members.update(item.upper() for item in extra_members)
    in_theme = sector_id in set(series.theme_ids) or ticker in members or series.security_id.upper() in members
    if not in_theme:
        return False, "NOT_IN_THEME"
    if series.asset_track != target_track:
        return False, "TRACK_MISMATCH"
    venue = classify_venue(dict(series.venue_metadata))
    if not venue.eligible:
        return False, venue.reason
    return True, "ok"


def is_theme_candidate(
    series: SecuritySeries,
    *,
    sector_id: str,
    session: date,
    target_track: str,
    extra_members: set[str] | None = None,
) -> tuple[bool, str]:
    ok, reason = theme_membership(
        series,
        sector_id=sector_id,
        target_track=target_track,
        extra_members=extra_members,
    )
    if not ok:
        return False, reason
    if not has_complete_session_bar(series, session):
        return False, "MISSING_SESSION_BAR"
    if series.source_available_at is not None:
        # Availability is checked by the caller against as_of; flag only.
        pass
    return True, "ok"


def is_reference_name(series: SecuritySeries, *, session: date, target_track: str) -> bool:
    if series.security_id in {"SPY", "QQQ"}:
        return has_complete_session_bar(series, session) or bool(series.dates)
    if series.asset_track != target_track and series.asset_track != "etf":
        return False
    return True


def source_is_available(series: SecuritySeries, as_of) -> bool:
    if series.source_available_at is not None:
        return series.source_available_at <= as_of
    if series.economic_known_at:
        last = series.economic_known_at[-1]
        return last is None or last <= as_of
    return True
