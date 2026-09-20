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
    economic_known_at: list[datetime | None] | None = None
    source_published_at: list[datetime | None] | None = None
    retrieved_at: list[datetime | None] | None = None
    finalized_at: list[datetime | None] | None = None
    bar_partial: np.ndarray | None = None
    bar_halted: np.ndarray | None = None
    vintage_status: tuple[str, ...] = ()
    price_adjustment: tuple[str, ...] = ()
    volume_adjustment: tuple[str, ...] = ()
    tri_verified: bool = False
    reconstruction_mode: str = "historical_reconstruction"
    dividend_events: tuple[dict[str, Any], ...] = ()

    def __post_init__(self) -> None:
        n = len(self.dates)
        for name in ("open", "high", "low", "close", "raw_close", "volume", "dollar_volume", "tri"):
            arr = np.asarray(getattr(self, name), dtype=float)
            if arr.shape != (n,):
                raise ValueError(f"{name} length must match dates")
            setattr(self, name, arr)
        if self.raw_open is None:
            self.raw_open = np.full(n, np.nan)
        else:
            self.raw_open = np.asarray(self.raw_open, dtype=float)
            if self.raw_open.shape != (n,):
                raise ValueError("raw_open length must match dates")
        for name in ("bar_partial", "bar_halted"):
            arr = getattr(self, name)
            if arr is None:
                continue
            arr = np.asarray(arr, dtype=bool)
            if arr.shape != (n,):
                raise ValueError(f"{name} length must match dates")
            setattr(self, name, arr)
        for name in ("economic_known_at", "source_published_at", "retrieved_at", "finalized_at"):
            values = getattr(self, name)
            if values is not None and len(values) != n:
                raise ValueError(f"{name} length must match dates")

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
            halted=_halted_through(self, index),
            raw_open=self.raw_open[:end].copy() if self.raw_open is not None else None,
            dividends=tuple(item for item in self.dividends if item[0] <= session),
            splits=tuple(item for item in self.splits if item[0] <= session),
            economic_known_at=None if self.economic_known_at is None else list(self.economic_known_at[:end]),
            source_published_at=None if self.source_published_at is None else list(self.source_published_at[:end]),
            retrieved_at=None if self.retrieved_at is None else list(self.retrieved_at[:end]),
            finalized_at=None if self.finalized_at is None else list(self.finalized_at[:end]),
            bar_partial=None if self.bar_partial is None else self.bar_partial[:end].copy(),
            bar_halted=None if self.bar_halted is None else self.bar_halted[:end].copy(),
            vintage_status=self.vintage_status[:end] if self.vintage_status else (),
            price_adjustment=self.price_adjustment[:end] if self.price_adjustment else (),
            volume_adjustment=self.volume_adjustment[:end] if self.volume_adjustment else (),
            tri_verified=self.tri_verified,
            reconstruction_mode=self.reconstruction_mode,
            dividend_events=tuple(item for item in self.dividend_events if _event_session(item) <= session),
        )

    def last_n(self, count: int) -> SecuritySeries:
        """Keep the newest ``count`` bars. Warmup windows still fit if count is large enough."""

        if count <= 0 or len(self.dates) <= count:
            return self
        start = len(self.dates) - int(count)
        session = self.dates[-1]
        return SecuritySeries(
            security_id=self.security_id,
            ticker_at_signal=self.ticker_at_signal,
            dates=list(self.dates[start:]),
            open=self.open[start:].copy(),
            high=self.high[start:].copy(),
            low=self.low[start:].copy(),
            close=self.close[start:].copy(),
            raw_close=self.raw_close[start:].copy(),
            volume=self.volume[start:].copy(),
            dollar_volume=self.dollar_volume[start:].copy(),
            tri=self.tri[start:].copy(),
            turnover_is_proxy=self.turnover_is_proxy,
            volume_session_scope=self.volume_session_scope,
            asset_track=self.asset_track,
            industry_id=self.industry_id,
            parent_industry_id=self.parent_industry_id,
            theme_ids=self.theme_ids,
            venue_metadata=self.venue_metadata,
            source_available_at=self.source_available_at,
            halted=_halted_through(self, len(self.dates) - 1),
            raw_open=self.raw_open[start:].copy() if self.raw_open is not None else None,
            dividends=tuple(item for item in self.dividends if item[0] <= session),
            splits=tuple(item for item in self.splits if item[0] <= session),
            economic_known_at=None if self.economic_known_at is None else list(self.economic_known_at[start:]),
            source_published_at=None if self.source_published_at is None else list(self.source_published_at[start:]),
            retrieved_at=None if self.retrieved_at is None else list(self.retrieved_at[start:]),
            finalized_at=None if self.finalized_at is None else list(self.finalized_at[start:]),
            bar_partial=None if self.bar_partial is None else self.bar_partial[start:].copy(),
            bar_halted=None if self.bar_halted is None else self.bar_halted[start:].copy(),
            vintage_status=self.vintage_status[start:] if self.vintage_status else (),
            price_adjustment=self.price_adjustment[start:] if self.price_adjustment else (),
            volume_adjustment=self.volume_adjustment[start:] if self.volume_adjustment else (),
            tri_verified=self.tri_verified,
            reconstruction_mode=self.reconstruction_mode,
            dividend_events=tuple(item for item in self.dividend_events if _event_session(item) <= session),
        )


def session_is_halted(series: "SecuritySeries", t: int) -> bool:
    """Halt is a dated event. A later end-of-sample flag cannot rewrite earlier days."""

    if series.bar_halted is not None:
        return bool(series.bar_halted[t])
    return bool(series.halted) and t == len(series.dates) - 1


def session_date_is_halted(series: "SecuritySeries", session: date) -> bool:
    if session not in series.dates:
        return False
    return session_is_halted(series, series.dates.index(session))


def _halted_through(series: "SecuritySeries", index: int) -> bool:
    """Slice keeps only a halt that is known on the last kept session."""

    return session_is_halted(series, index)


def _event_session(event: Mapping[str, Any]) -> date:
    value = event.get("ex_date") or event.get("session_date") or event.get("effective_at")
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


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
