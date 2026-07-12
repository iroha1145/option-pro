"""Narrow ports used by the Breakout Radar domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Protocol, Sequence

import pandas as pd

from app.services.breakouts.models import (
    DiscoveryProfile,
    DiscoverySnapshot,
    MarketSession,
    MarketShapeSnapshot,
    StrengthScoreSnapshot,
    TemporalCutoff,
)


@dataclass(frozen=True)
class PriceDataSnapshot:
    ticker: str
    frame: pd.DataFrame
    source: str
    raw_as_of: datetime
    cutoff: TemporalCutoff
    session: MarketSession
    adjustment: str
    completeness: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


class DiscoveryProvider(Protocol):
    async def scan(
        self,
        *,
        session: MarketSession,
        as_of: datetime,
        profile: DiscoveryProfile,
    ) -> DiscoverySnapshot: ...


class PriceDataPort(Protocol):
    async def daily(
        self,
        tickers: Sequence[str],
        *,
        cutoff: TemporalCutoff,
        period: str = "2y",
    ) -> Mapping[str, PriceDataSnapshot]: ...

    async def intraday(
        self,
        tickers: Sequence[str],
        *,
        cutoff: TemporalCutoff,
        interval: str = "5m",
    ) -> Mapping[str, PriceDataSnapshot]: ...


class StrengthScoringPort(Protocol):
    async def score_ticker_set(
        self,
        tickers: Sequence[str],
        *,
        as_of: datetime,
        include_options: bool = False,
    ) -> Mapping[str, StrengthScoreSnapshot]: ...


class MarketShapePort(Protocol):
    async def snapshot(self, *, as_of: datetime) -> MarketShapeSnapshot: ...


class CanonicalUniversePort(Protocol):
    async def tickers(self, *, as_of: datetime) -> Sequence[str]: ...

    async def distributions(
        self,
        *,
        feature: str,
        as_of: date,
        sector: str | None = None,
    ) -> Mapping[str, Any]: ...


class BreakoutRepositoryPort(Protocol):
    def publish_scan(self, snapshot: Mapping[str, Any]) -> str: ...

    def latest_completed_scan(self) -> Mapping[str, Any] | None: ...
