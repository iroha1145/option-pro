from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.breakouts.adapters.market_shape import ExistingMarketShapeAdapter


def test_missing_six_state_market_shape_is_unavailable_not_neutral() -> None:
    snapshot = asyncio.run(
        ExistingMarketShapeAdapter().snapshot(
            as_of=datetime(2026, 7, 10, tzinfo=timezone.utc)
        )
    )
    assert snapshot.status == "unavailable"
    assert snapshot.state is None
    assert snapshot.confidence == 0.0
    assert snapshot.rules == {}
