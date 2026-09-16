from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping

import numpy as np

from app.services.research_eod_v1.calendar_asof import last_completed_session, require_aware


@dataclass
class SecuritySeries:
    security_id: str
    ticker_at_signal: str
    dates: list[date]
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    raw_close: np.ndarray
    volume: np.ndarray
    dollar_volume: np.ndarray
    tri: np.ndarray
    turnover_is_proxy: bool = True
    volume_session_scope: str = "unknown"
    asset_track: str = "stock"
    industry_id: str | None = None
    parent_industry_id: str | None = None
    theme_ids: tuple[str, ...] = ()
    venue_metadata: Mapping[str, Any] = field(default_factory=dict)
    source_available_at: datetime | None = None
    halted: bool = False
    raw_open: np.ndarray | None = None
    dividends: tuple[tuple[date, float], ...] = ()
    splits: tuple[tuple[date, float], ...] = ()

    def __post_init__(self) -> None:
        n = len(self.dates)
        for name in ("open", "high", "low", "close", "raw_close", "volume", "dollar_volume", "tri"):
            arr = np.asarray(getattr(self, name), dtype=float)
            if arr.shape != (n,):
                raise ValueError(f"{name} length must match dates")
            setattr(self, name, arr)
        if self.raw_open is None:
            self.raw_open = self.open.copy()
        else:
            self.raw_open = np.asarray(self.raw_open, dtype=float)
            if self.raw_open.shape != (n,):
                raise ValueError("raw_open length must match dates")

    def index_on_or_before(self, session: date) -> int | None:
        for index in range(len(self.dates) - 1, -1, -1):
            if self.dates[index] <= session:
                return index
        return None

    def slice_through(self, session: date) -> SecuritySeries | None:
        index = self.index_on_or_before(session)
        if index is None:
            return None
        end = index + 1
        return SecuritySeries(
            security_id=self.security_id,
            ticker_at_signal=self.ticker_at_signal,
            dates=list(self.dates[:end]),
            open=self.open[:end].copy(),
            high=self.high[:end].copy(),
            low=self.low[:end].copy(),
            close=self.close[:end].copy(),
            raw_close=self.raw_close[:end].copy(),
            volume=self.volume[:end].copy(),
            dollar_volume=self.dollar_volume[:end].copy(),
            tri=self.tri[:end].copy(),
            turnover_is_proxy=self.turnover_is_proxy,
            volume_session_scope=self.volume_session_scope,
            asset_track=self.asset_track,
            industry_id=self.industry_id,
            parent_industry_id=self.parent_industry_id,
            theme_ids=self.theme_ids,
            venue_metadata=self.venue_metadata,
            source_available_at=self.source_available_at,
            halted=self.halted,
            raw_open=self.raw_open[:end].copy() if self.raw_open is not None else None,
            dividends=tuple(item for item in self.dividends if item[0] <= session),
            splits=tuple(item for item in self.splits if item[0] <= session),
        )


def daily_returns(tri: np.ndarray) -> np.ndarray:
    out = np.full(len(tri), np.nan)
    prev = tri[:-1]
    cur = tri[1:]
    ok = np.isfinite(prev) & np.isfinite(cur) & (prev > 0)
    out[1:][ok] = cur[ok] / prev[ok] - 1.0
    return out


def clip_panel_to_as_of(
    panel: Mapping[str, SecuritySeries],
    as_of: datetime,
) -> dict[str, SecuritySeries]:
    session = last_completed_session(require_aware(as_of))
    clipped: dict[str, SecuritySeries] = {}
    for key, series in panel.items():
        sliced = series.slice_through(session)
        if sliced is not None:
            clipped[key] = sliced
    return clipped
