"""Conservative adapter while the repository lacks a six-state market engine."""

from __future__ import annotations

from datetime import datetime

from app.services.breakouts.models import MarketShapeSnapshot


class ExistingMarketShapeAdapter:
    version = "market-shape-adapter-v1"

    async def snapshot(self, *, as_of: datetime) -> MarketShapeSnapshot:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return MarketShapeSnapshot(
            status="unavailable",
            state=None,
            confidence=0.0,
            transition_risk=None,
            as_of=as_of,
            rules={},
            warnings=[
                "existing market regime is a scalar score, not the frozen six-state contract"
            ],
            version=self.version,
        )
