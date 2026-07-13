"""Offline production-image smoke for the independent focus producer."""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, time, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from app.services.breakouts.clock import MarketClock
from app.services.catalysts.focus_config import FocusContextSettings
from app.services.catalysts.focus_worker import (
    FOCUS_PRODUCER_WORKER_PREFIX,
    LOCK_NAME,
    FocusContextProducer,
    health_payload,
)
from app.services.catalysts.repository import CatalystRepository
from app.services.market_calendar import ET


NOW = datetime(2026, 7, 13, 14, 30, tzinfo=timezone.utc)


def intraday_frame(price: float, current_volume: float) -> pd.DataFrame:
    index: list[datetime] = []
    volume: list[float] = []
    for day in (
        date(2026, 7, 6),
        date(2026, 7, 7),
        date(2026, 7, 8),
        date(2026, 7, 9),
        date(2026, 7, 10),
        date(2026, 7, 13),
    ):
        for minute in range(9 * 60 + 30, 10 * 60 + 30, 5):
            index.append(
                datetime.combine(
                    day,
                    time(hour=minute // 60, minute=minute % 60),
                    tzinfo=ET,
                )
            )
            volume.append(current_volume if day == date(2026, 7, 13) else 100.0)
    return pd.DataFrame(
        {
            "Open": price,
            "High": price,
            "Low": price,
            "Close": price,
            "Volume": volume,
        },
        index=pd.DatetimeIndex(index),
    )


async def main() -> None:
    path = Path("/tmp/focus-producer-smoke.db")
    path.unlink(missing_ok=True)
    settings = FocusContextSettings(
        _env_file=None,
        MACROLENS_CACHE_DB_PATH=path,
        FOCUS_PRODUCER_ENABLED=True,
        FOCUS_PRODUCER_INTERVAL_SECONDS=1800,
        FOCUS_PRODUCER_CANDIDATE_LIMIT=40,
        FOCUS_PRODUCER_HEARTBEAT_SECONDS=30,
        FOCUS_PRODUCER_HEALTH_STALE_SECONDS=120,
        FOCUS_PRODUCER_LEASE_SECONDS=90,
    )
    repository = CatalystRepository(path)
    repository.initialize(now=NOW)

    async def strength_loader() -> dict:
        return {
            "as_of": NOW.isoformat(),
            "universe_as_of": NOW.isoformat(),
            "universe_version": "container-smoke-v1",
            "_focus_rows": [
                {
                    "ticker": "AAPL",
                    "avg_dollar_volume_20d": 50_000_000,
                    "data_quality": 0.9,
                    "universe_member": True,
                    "universe_as_of": NOW.isoformat(),
                }
            ],
        }

    async def discovery_loader(_snapshot) -> dict:
        return {
            "provider": "offline_fixture",
            "status": "active",
            "as_of": NOW,
            "warnings": [],
            "candidates": [],
        }

    async def intraday_loader(tickers, cutoff) -> dict:
        assert tickers == ["AAPL"]
        assert cutoff.include_current_bar is False
        return {
            "AAPL": SimpleNamespace(
                frame=intraday_frame(100.0, 200.0),
                data_through=NOW,
                source="offline_fixture",
                quality=1.0,
                warnings=(),
            )
        }

    owner = f"{FOCUS_PRODUCER_WORKER_PREFIX}container-smoke"
    producer = FocusContextProducer(
        settings=settings,
        repository=repository,
        clock=MarketClock(now=lambda: NOW),
        strength_loader=strength_loader,
        discovery_loader=discovery_loader,
        intraday_loader=intraday_loader,
        breakout_loader=lambda: [],
        owner_id=owner,
    )
    token = repository.acquire_worker_lock(
        LOCK_NAME,
        owner,
        lease_seconds=settings.producer_lease_seconds,
        now=NOW,
    )
    assert token is not None
    try:
        result = await producer.run_once(fencing_token=token)
        assert result["status"] == "completed"
        assert result["intraday_enriched_count"] == 1
        current = repository.current_focus_context()
        assert current is not None and current.revision == 1
        symbol = current.symbols[0]
        assert symbol.ticker == "AAPL"
        assert symbol.dollar_volume == 240_000.0
        assert symbol.dollar_volume_basis == "intraday_completed_bars"
        assert symbol.data_through == NOW
        assert symbol.source_status == "active"
        assert symbol.data_source == "offline_fixture"
        assert symbol.rvol_time_of_day == 2.0
        encoded = json.dumps(current.model_dump(mode="json"), sort_keys=True)
        for forbidden in (
            "intrinsic_strength_score",
            "ranking_score",
            "breakout_quality_score",
            "market_shape",
        ):
            assert forbidden not in encoded
        health = health_payload(settings, repository=repository, now=NOW)
        assert health["healthy"] is True
        assert health["ready_dependency"] is False
        print(
            json.dumps(
                {
                    "status": result["status"],
                    "revision": current.revision,
                    "basis": symbol.dollar_volume_basis,
                    "health": health["status"],
                },
                sort_keys=True,
            )
        )
    finally:
        repository.release_worker_lock(LOCK_NAME, owner, token)
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    asyncio.run(main())
