from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import (
    AssetType,
    BreakoutCandidate,
    DiscoverySnapshot,
    MarketSession,
    MarketShapeSnapshot,
    ProviderStatus,
    StrengthScoreSnapshot,
)
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.breakouts.service import BreakoutRadarService
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.worker import BreakoutWorker


NY = ZoneInfo("America/New_York")
AS_OF = datetime(2026, 7, 10, 10, 30, tzinfo=NY)


def _daily() -> pd.DataFrame:
    index = pd.date_range("2025-01-01", periods=390, freq="B")
    close = 60 + np.arange(len(index)) * 0.08 + np.sin(np.arange(len(index)) / 5)
    return pd.DataFrame(
        {
            "Open": close - 0.2,
            "High": close + 1,
            "Low": close - 1,
            "Close": close,
            "Volume": 2_000_000,
        },
        index=index,
    )


def _intraday() -> pd.DataFrame:
    index = pd.date_range(
        AS_OF.replace(hour=9, minute=30),
        AS_OF,
        freq="5min",
    )
    close = np.linspace(100, 104, len(index))
    return pd.DataFrame(
        {
            "Open": close - 0.1,
            "High": close + 0.5,
            "Low": close - 0.5,
            "Close": close,
            "Volume": 100_000,
        },
        index=index,
    )


class Prices:
    async def daily(self, tickers, *, cutoff, period):
        frame = _daily()
        return {
            ticker: PriceDataSnapshot(
                ticker=ticker,
                frame=frame,
                source="fixture",
                raw_as_of=AS_OF.astimezone(timezone.utc),
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="adjusted",
                completeness="complete",
            )
            for ticker in tickers
        }

    async def intraday(self, tickers, *, cutoff, interval):
        frame = _intraday()
        return {
            ticker: PriceDataSnapshot(
                ticker=ticker,
                frame=frame,
                source="fixture",
                raw_as_of=AS_OF.astimezone(timezone.utc),
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="unadjusted",
                completeness="complete",
            )
            for ticker in tickers
        }


class Strength:
    version = "strength-intrinsic-v1"

    async def score_ticker_set(self, tickers, *, as_of, include_options):
        assert include_options is False
        return {
            ticker: StrengthScoreSnapshot(
                ticker=ticker,
                score=75,
                score_scope="intrinsic",
                confidence=1,
                score_version=self.version,
                included_features=["momentum_63d"],
                factor_breakdown={
                    "range_persistence_shadow": {
                        "hypothetical_score": 76,
                    }
                },
                coverage={"ratio": 1},
                as_of=as_of,
            )
            for ticker in tickers
        }


class Market:
    version = "market-shape-adapter-v1"

    async def snapshot(self, *, as_of):
        return MarketShapeSnapshot(
            status="unavailable",
            state=None,
            confidence=0,
            as_of=as_of,
            version=self.version,
            warnings=["fixture unavailable"],
        )


class Universe:
    version = "fixture-universe-v1"

    async def tickers(self, *, as_of):
        return []

    def primary_sector(self, ticker):
        return None


def test_service_revalidates_candidate_without_optional_context() -> None:
    candidate = BreakoutCandidate(
        ticker="TEST",
        exchange="NASDAQ",
        asset_type=AssetType.COMMON_STOCK,
        price=104,
        provider_change_pct=8,
        provider_volume=2_000_000,
        provider_relative_volume=3,
        provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    discovery = DiscoverySnapshot(
        provider="fixture",
        status=ProviderStatus.ACTIVE,
        as_of=AS_OF,
        session=MarketSession.REGULAR,
        schema_version="fixture-v1",
        candidate_count=1,
        candidates=[candidate],
        cache_key="fixture-snapshot",
    )
    service = BreakoutRadarService(
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
            RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=True,
        ),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    payload = asyncio.run(service.build_snapshot(discovery))
    assert len(payload["events"]) == 1
    event = payload["events"][0]
    assert event.ticker == "TEST"
    assert event.scores.intrinsic_strength_score == 75
    assert event.scores.market_fit_score is None
    assert event.source_snapshot_id == "fixture-snapshot"
    assert payload["range_persistence_shadow"][0]["production_score"] == 75
    assert (
        payload["range_persistence_shadow"][0]["breakout_production_priority"]
        == event.scores.alert_priority_score
    )
    assert payload["range_persistence_shadow"][0]["production_rank"] == 1
    assert payload["range_persistence_shadow"][0]["hypothetical_rank"] == 1
    assert payload["range_persistence_shadow"][0]["rank_delta"] == 0
    assert event.features["range_persistence_global_percentile"] is None
    assert event.features["canonical_universe_member"] is False


class Provider:
    def __init__(self, candidate: BreakoutCandidate):
        self.candidate = candidate
        self.calls = 0

    @property
    def health(self):
        return {
            "provider": "fixture",
            "status": "active",
            "consecutive_failures": 0,
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile):
        self.calls += 1
        return DiscoverySnapshot(
            provider="fixture",
            status=ProviderStatus.ACTIVE,
            as_of=as_of,
            session=session,
            schema_version="fixture-v1",
            candidate_count=1,
            candidates=[self.candidate.model_copy(update={"provider_timestamp": as_of})],
            cache_key=f"fixture-{as_of.isoformat()}",
        )


def test_real_worker_service_repository_chain_publishes_and_preserves_first_seen(
    tmp_path,
) -> None:
    candidate = BreakoutCandidate(
        ticker="TEST",
        exchange="NASDAQ",
        asset_type=AssetType.COMMON_STOCK,
        price=104,
        previous_regular_close=100,
        provider_change_pct=8,
        provider_volume=2_000_000,
        provider_relative_volume=3,
        provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        BREAKOUT_DISCOVERY_PROVIDER="tradingview",
        BREAKOUT_DB_PATH=tmp_path / "worker.db",
    )

    def run(at: datetime, owner: str):
        service = BreakoutRadarService(
            settings,
            price_data=Prices(),
            strength=Strength(),
            market_shape=Market(),
            universe=Universe(),
        )
        worker = BreakoutWorker(
            settings,
            BreakoutRepository(settings.db_path),
            provider=Provider(candidate),
            scan_service=service,
            clock=MarketClock(now=lambda: at),
            owner_id=owner,
        )
        return asyncio.run(worker.run_once())

    first = run(AS_OF, "worker-first")
    assert first["status"] == "completed"
    reader = BreakoutRepository(settings.db_path, read_only=True)
    first_event = reader.latest_completed_scan()["events"][0]
    first_seen = first_event["first_seen_at"]

    second = run(AS_OF + pd.Timedelta(minutes=5), "worker-second")
    assert second["status"] == "completed"
    latest_event = reader.latest_completed_scan()["events"][0]
    assert latest_event["event_id"] == first_event["event_id"]
    assert latest_event["first_seen_at"] == first_seen
    detail = reader.get_event(latest_event["event_id"])
    assert detail is not None
    assert detail["transitions"]
    assert all(item.get("evidence_at") for item in detail["transitions"])


class NoIntradayPrices(Prices):
    async def daily(self, tickers, *, cutoff, period):
        index = pd.bdate_range(end="2026-07-09", periods=220)
        close = np.linspace(80, 90, 220)
        phase = np.arange(60)
        close[-60:] = 95.0 + np.sin(phase * np.pi / 3.0) * 3.5 + phase * 0.01
        frame = pd.DataFrame(
            {
                "Open": close - 0.2,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
                "Volume": np.linspace(2_000_000, 1_100_000, 220),
            },
            index=index,
        )
        return {
            ticker: PriceDataSnapshot(
                ticker=ticker,
                frame=frame,
                source="fixture",
                raw_as_of=AS_OF.astimezone(timezone.utc),
                cutoff=cutoff,
                session=cutoff.session,
                adjustment="adjusted",
                completeness="complete",
            )
            for ticker in tickers
        }

    async def intraday(self, tickers, *, cutoff, interval):
        return {}


def test_provider_price_cannot_trigger_without_a_complete_intraday_bar() -> None:
    candidate = BreakoutCandidate(
        ticker="TEST",
        exchange="NASDAQ",
        asset_type=AssetType.COMMON_STOCK,
        price=120,
        provider_change_pct=20,
        provider_volume=3_000_000,
        provider_relative_volume=4,
        provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    discovery = DiscoverySnapshot(
        provider="fixture",
        status=ProviderStatus.ACTIVE,
        as_of=AS_OF,
        session=MarketSession.REGULAR,
        schema_version="fixture-v1",
        candidate_count=1,
        candidates=[candidate],
    )
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None),
        price_data=NoIntradayPrices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    event = asyncio.run(service.build_snapshot(discovery))["events"][0]
    assert event.structure is not None
    assert event.lifecycle_state.name == "WATCHING"
    assert event.features["detection"]["triggered"] is False
    assert event.scores.breakout_confirmation_score is None


class FailingStrength:
    version = "fixture-strength-failed"

    async def score_ticker_set(self, *args, **kwargs):
        raise RuntimeError("strength unavailable")


class FailingMarket:
    version = "fixture-market-failed"

    async def snapshot(self, *, as_of):
        raise RuntimeError("market unavailable")


def test_optional_adapter_failures_degrade_instead_of_aborting_scan() -> None:
    candidate = BreakoutCandidate(
        ticker="TEST",
        price=104,
        provider_change_pct=8,
        provider_volume=2_000_000,
        provider_relative_volume=3,
        provider_market_cap=1_000_000_000,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    discovery = DiscoverySnapshot(
        provider="fixture",
        status=ProviderStatus.ACTIVE,
        as_of=AS_OF,
        session=MarketSession.REGULAR,
        schema_version="fixture-v1",
        candidate_count=1,
        candidates=[candidate],
    )
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None),
        price_data=Prices(),
        strength=FailingStrength(),
        market_shape=FailingMarket(),
        universe=Universe(),
    )
    payload = asyncio.run(service.build_snapshot(discovery))
    assert payload["events"]
    assert payload["events"][0].scores.intrinsic_strength_score is None
    assert payload["source_status"]["strength"] == "unavailable"
    assert payload["source_status"]["market_shape"] == "unavailable"
