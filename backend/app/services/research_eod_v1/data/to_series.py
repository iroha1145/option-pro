"""Convert contract bars into the compute_snapshot series object."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import numpy as np
from zoneinfo import ZoneInfo

from app.services.research_eod_v1.data.contract import ResearchBar
from app.services.research_eod_v1.series import SecuritySeries

ET = ZoneInfo("America/New_York")


def bars_to_series(
    bars: Sequence[ResearchBar],
    *,
    security_id: str,
    asset_track: str,
    theme_ids: tuple[str, ...] = (),
    industry_id: str | None = None,
    parent_industry_id: str | None = None,
    venue_metadata: dict | None = None,
) -> SecuritySeries | None:
    usable = [bar for bar in bars if not bar.missing and bar.close is not None and bar.open is not None]
    if not usable:
        return None
    usable = sorted(usable, key=lambda bar: bar.session_date)
    close = np.array([float(bar.close) for bar in usable], dtype=float)
    open_ = np.array([float(bar.open) for bar in usable], dtype=float)
    high = np.array([float(bar.high if bar.high is not None else bar.close) for bar in usable], dtype=float)
    low = np.array([float(bar.low if bar.low is not None else bar.close) for bar in usable], dtype=float)
    raw_close = np.array([float(bar.raw_close if bar.raw_close is not None else bar.close) for bar in usable], dtype=float)
    raw_open = np.array([float(bar.raw_open if bar.raw_open is not None else bar.open) for bar in usable], dtype=float)
    volume = np.array([float(bar.volume or 0.0) for bar in usable], dtype=float)
    dollar = np.array(
        [
            float(bar.dollar_volume) if bar.dollar_volume is not None else float((bar.close or 0) * (bar.volume or 0))
            for bar in usable
        ],
        dtype=float,
    )
    tri = np.array([float(bar.tri if bar.tri is not None else bar.close) for bar in usable], dtype=float)
    last_pub = usable[-1].source_published_at
    return SecuritySeries(
        security_id=security_id,
        ticker_at_signal=security_id,
        dates=[bar.session_date for bar in usable],
        open=open_,
        high=high,
        low=low,
        close=close,
        raw_close=raw_close,
        volume=volume,
        dollar_volume=dollar,
        tri=tri,
        turnover_is_proxy=True,
        volume_session_scope=usable[-1].volume_scope or "unknown",
        asset_track=asset_track,
        industry_id=industry_id,
        parent_industry_id=parent_industry_id,
        theme_ids=theme_ids,
        venue_metadata=venue_metadata or {},
        source_available_at=last_pub if last_pub is not None else datetime(usable[-1].session_date.year, usable[-1].session_date.month, usable[-1].session_date.day, 16, 30, tzinfo=ET),
        raw_open=raw_open,
    )
