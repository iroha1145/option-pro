"""Convert contract bars into the compute_snapshot series object."""

from __future__ import annotations

from datetime import datetime
from typing import Sequence

import numpy as np
from zoneinfo import ZoneInfo

from app.services.research_eod_v1.calendar_asof import session_close_at
from app.services.research_eod_v1.data.contract import ResearchBar, validate_research_bars
from app.services.research_eod_v1.series import SecuritySeries

ET = ZoneInfo("America/New_York")


def _optional_float(value: float | None) -> float:
    return float("nan") if value is None else float(value)


def bars_to_series(
    bars: Sequence[ResearchBar],
    *,
    security_id: str,
    asset_track: str,
    theme_ids: tuple[str, ...] = (),
    industry_id: str | None = None,
    parent_industry_id: str | None = None,
    venue_metadata: dict | None = None,
    reconstruction_mode: str = "historical_reconstruction",
) -> SecuritySeries | None:
    usable = [bar for bar in bars if not bar.missing and bar.close is not None and bar.open is not None]
    if not usable:
        return None
    usable = sorted(usable, key=lambda bar: bar.session_date)
    validate_research_bars(usable)
    close = np.array([float(bar.close) for bar in usable], dtype=float)
    open_ = np.array([float(bar.open) for bar in usable], dtype=float)
    high = np.array([_optional_float(bar.high) for bar in usable], dtype=float)
    low = np.array([_optional_float(bar.low) for bar in usable], dtype=float)
    raw_close = np.array([_optional_float(bar.raw_close) for bar in usable], dtype=float)
    raw_open = np.array([_optional_float(bar.raw_open) for bar in usable], dtype=float)
    volume = np.array([_optional_float(bar.volume) for bar in usable], dtype=float)
    dollar = np.array(
        [
            float(bar.dollar_volume)
            if bar.dollar_volume is not None
            else (
                float(bar.close) * float(bar.volume)
                if bar.close is not None and bar.volume is not None
                else float("nan")
            )
            for bar in usable
        ],
        dtype=float,
    )
    tri = np.array([_optional_float(bar.tri) for bar in usable], dtype=float)
    economic = []
    published = []
    retrieved = []
    finalized = []
    partial = []
    halted = []
    for bar in usable:
        known = bar.economic_known_at or session_close_at(bar.session_date)
        economic.append(known)
        published.append(bar.source_published_at)
        retrieved.append(bar.retrieved_at)
        finalized.append(bar.finalized_at)
        partial.append(bar.vintage_status == "PARTIAL" or bar.partial)
        halted.append(bool(bar.halted))
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
        source_available_at=None,
        raw_open=raw_open,
        economic_known_at=economic,
        source_published_at=published,
        retrieved_at=retrieved,
        finalized_at=finalized,
        bar_partial=np.asarray(partial, dtype=bool),
        bar_halted=np.asarray(halted, dtype=bool),
        vintage_status=tuple(bar.vintage_status for bar in usable),
        price_adjustment=tuple(bar.price_adjustment for bar in usable),
        volume_adjustment=tuple(bar.volume_adjustment for bar in usable),
        tri_verified=all(bar.tri is not None and bar.price_adjustment == "verified_tri" for bar in usable),
        reconstruction_mode=reconstruction_mode,
    )
