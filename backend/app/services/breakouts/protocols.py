"""Narrow ports used by the Breakout Radar domain."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

import pandas as pd

from app.services.breakouts.models import (
    DiscoveryProfile,
    DiscoverySnapshot,
    MarketSession,
    TemporalCutoff,
)


@dataclass(frozen=True)
class PriceDataSnapshot:
    """A bounded price frame with explicit request and market-data clocks.

    ``raw_as_of`` remains as a compatibility alias.  New producers should set
    ``data_through``; ``__post_init__`` then guarantees that the alias can never
    describe the request cutoff instead of the last complete market bar.
    """

    ticker: str
    frame: pd.DataFrame
    source: str
    raw_as_of: datetime
    cutoff: TemporalCutoff
    session: MarketSession
    adjustment: str
    completeness: str
    warnings: tuple[str, ...] = field(default_factory=tuple)
    requested_at: datetime | None = None
    received_at: datetime | None = None
    data_through: datetime | None = None
    feature_cutoff_at: datetime | None = None
    quality: float = 1.0

    def __post_init__(self) -> None:
        feature_cutoff = self.feature_cutoff_at or self.cutoff.event_at
        data_through = self.data_through or self.raw_as_of
        requested = self.requested_at or feature_cutoff
        received = self.received_at or requested
        for name, value in (
            ("requested_at", requested),
            ("received_at", received),
            ("data_through", data_through),
            ("feature_cutoff_at", feature_cutoff),
        ):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must include a timezone")
        if not 0.0 <= float(self.quality) <= 1.0:
            raise ValueError("quality must be between 0 and 1")
        requested = requested.astimezone(timezone.utc)
        received = received.astimezone(timezone.utc)
        data_through = data_through.astimezone(timezone.utc)
        feature_cutoff = feature_cutoff.astimezone(timezone.utc)
        if received < requested:
            raise ValueError("received_at must not precede requested_at")
        if data_through > feature_cutoff:
            raise ValueError("data_through must not exceed feature_cutoff_at")
        object.__setattr__(self, "requested_at", requested)
        object.__setattr__(self, "received_at", received)
        object.__setattr__(self, "data_through", data_through)
        object.__setattr__(self, "feature_cutoff_at", feature_cutoff)
        object.__setattr__(self, "raw_as_of", data_through)


class DiscoveryProvider(Protocol):
    async def scan(
        self,
        *,
        session: MarketSession,
        as_of: datetime,
        profile: DiscoveryProfile,
    ) -> DiscoverySnapshot: ...
