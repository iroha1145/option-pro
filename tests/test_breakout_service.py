from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.clock import MarketClock
from app.services.breakouts.models import (
    AssetType,
    BreakoutCandidate,
    BreakoutLifecycleState,
    BreakoutSetupType,
    BreakoutStructure,
    DiscoverySnapshot,
    MarketSession,
    MarketShapeSnapshot,
    ProviderStatus,
    PriceZone,
    StrengthScoreSnapshot,
)
from app.services.breakouts.protocols import PriceDataSnapshot
from app.services.breakouts.service import BreakoutRadarService, _safe_range_feature
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


class ActiveBullMarket(Market):
    version = "market-shape-v2"

    async def snapshot(self, *, as_of):
        return MarketShapeSnapshot(
            status="active",
            state="BULL_TREND",
            confidence=0.9,
            transition_risk=0.1,
            as_of=as_of,
            version=self.version,
            rules={
                "ordinary_breakout_fit": 88,
                "recovery_breakout_fit": 84,
                "preferred_setups": [
                    "DAILY_BASE_BREAKOUT",
                    "OPENING_RANGE_BREAKOUT",
                ],
                "caution_setups": ["MOMENTUM_SPIKE"],
                "confirmation_bar_delta": 0,
                "allow_single_bar_confirmation": True,
                "eligibility": "normal",
            },
        )


class Universe:
    version = "fixture-universe-v1"

    async def tickers(self, *, as_of):
        return []

    def primary_sector(self, ticker):
        return None


class BrokenCanonicalUniverse(Universe):
    async def tickers(self, *, as_of):
        return ["BROKEN"]


class BrokenCanonicalPrices(Prices):
    async def daily(self, tickers, *, cutoff, period):
        snapshots = await super().daily(tickers, cutoff=cutoff, period=period)
        broken = snapshots.get("BROKEN")
        if broken is not None:
            snapshots["BROKEN"] = PriceDataSnapshot(
                **{
                    **vars(broken),
                    "frame": broken.frame[["Close", "Volume"]],
                }
            )
        return snapshots


class LiquidityRecordingPrices(Prices):
    def __init__(self):
        self.daily_tickers = []
        self.intraday_tickers = []

    async def daily(self, tickers, *, cutoff, period):
        self.daily_tickers = list(tickers)
        snapshots = await super().daily(tickers, cutoff=cutoff, period=period)
        low = snapshots["LOW"]
        snapshots["LOW"] = PriceDataSnapshot(
            **{
                **vars(low),
                "frame": low.frame.assign(Volume=1_000),
            }
        )
        missing = snapshots["MISSING"]
        snapshots["MISSING"] = PriceDataSnapshot(
            **{
                **vars(missing),
                "frame": missing.frame.drop(columns="Volume"),
            }
        )
        return snapshots

    async def intraday(self, tickers, *, cutoff, interval):
        self.intraday_tickers = list(tickers)
        return await super().intraday(tickers, cutoff=cutoff, interval=interval)


class LiquidityRecordingStrength(Strength):
    def __init__(self):
        self.ticker_calls = []
        self.snapshot_calls = []

    async def score_from_daily_snapshots(
        self,
        tickers,
        *,
        snapshots,
        as_of,
        include_options,
        range_mode,
        range_trend_weight,
        range_final_cap,
    ):
        self.ticker_calls.append(list(tickers))
        self.snapshot_calls.append(sorted(snapshots))
        return await Strength.score_ticker_set(
            self,
            tickers,
            as_of=as_of,
            include_options=include_options,
        )


def test_daily_average_dollar_volume_filters_before_strength_and_intraday() -> None:
    candidates = [
        BreakoutCandidate(
            ticker=ticker,
            price=104,
            provider_change_pct=8,
            provider_volume=2_000_000,
            provider_relative_volume=3,
            provider_market_cap=1_000_000_000,
            provider_timestamp=AS_OF,
            source="fixture",
            session=MarketSession.REGULAR,
        )
        for ticker in ("HIGH", "LOW", "MISSING")
    ]
    discovery = DiscoverySnapshot(
        provider="fixture",
        status=ProviderStatus.ACTIVE,
        as_of=AS_OF,
        session=MarketSession.REGULAR,
        schema_version="fixture-v1",
        candidate_count=len(candidates),
        candidates=candidates,
    )
    prices = LiquidityRecordingPrices()
    strength = LiquidityRecordingStrength()
    service = BreakoutRadarService(
        BreakoutSettings(
            _env_file=None,
            BREAKOUT_MIN_AVG_DOLLAR_VOLUME=10_000_000,
            RANGE_PERSISTENCE_MODE="disabled",
        ),
        price_data=prices,
        strength=strength,
        market_shape=Market(),
        universe=Universe(),
    )

    payload = asyncio.run(service.build_snapshot(discovery))

    assert {"HIGH", "LOW", "MISSING"}.issubset(prices.daily_tickers)
    assert strength.ticker_calls == [["HIGH"]]
    assert strength.snapshot_calls == [["HIGH", "SPY"]]
    assert prices.intraday_tickers == ["HIGH"]
    assert [event.ticker for event in payload["events"]] == ["HIGH"]


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


def test_range_feature_failure_is_unavailable_instead_of_raising() -> None:
    result = _safe_range_feature(
        _daily()[["Close", "Volume"]],
        cutoff=AS_OF,
        length=35,
        fast_length=3,
        slope_lookback=5,
        ratio_window=10,
        ratio_threshold=60,
        min_history_multiplier=5,
        version="range-persistence-v1",
    )

    assert result["status"] == "unavailable"
    assert result["range_persistence"] is None
    assert result["warnings"] == ["range_persistence_calculation_failed"]


def test_broken_canonical_range_member_does_not_abort_breakout_snapshot() -> None:
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
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
            RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=True,
        ),
        price_data=BrokenCanonicalPrices(),
        strength=Strength(),
        market_shape=Market(),
        universe=BrokenCanonicalUniverse(),
    )

    payload = asyncio.run(service.build_snapshot(discovery))

    assert [event.ticker for event in payload["events"]] == ["TEST"]
    assert payload["events"][0].features["canonical_universe_status"] == "unavailable"


def test_active_market_shape_populates_fit_and_only_changes_contextual_scores() -> None:
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
    unavailable_service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    active_service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=ActiveBullMarket(),
        universe=Universe(),
    )

    missing = asyncio.run(unavailable_service.build_snapshot(discovery))["events"][0]
    active = asyncio.run(active_service.build_snapshot(discovery))["events"][0]

    assert active.scores.market_fit_score is not None
    assert active.scores.alert_priority_score != missing.scores.alert_priority_score
    assert active.scores.intrinsic_strength_score == missing.scores.intrinsic_strength_score
    assert active.scores.base_quality_score == missing.scores.base_quality_score
    assert active.features["market_shape_state"] == "BULL_TREND"
    assert active.data_quality["market_shape_version"] == "market-shape-v2"


def test_opening_range_and_daily_base_are_recorded_as_distinct_events(
    monkeypatch,
) -> None:
    structure = BreakoutStructure(
        ticker="TEST",
        base_start=date(2026, 5, 1),
        base_end=date(2026, 7, 9),
        calculation_cutoff_at=datetime(2026, 7, 9, 16, 0, tzinfo=NY),
        base_duration_days=40,
        support_zone=PriceZone(low=90, high=92, touches=2),
        resistance_zone=PriceZone(low=99, high=100, touches=3),
        pivot_price=99.5,
        pivot_id="parallel-pivot",
        pivot_touch_count=3,
        invalidation_price=89.5,
        quality=0.9,
        status="active",
    )
    monkeypatch.setattr(
        "app.services.breakouts.service.detect_base",
        lambda *args, **kwargs: structure,
    )
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
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=ActiveBullMarket(),
        universe=Universe(),
    )

    payload = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[candidate],
            )
        )
    )

    assert {event.setup_type for event in payload["events"]} == {
        BreakoutSetupType.OPENING_RANGE_BREAKOUT,
        BreakoutSetupType.DAILY_BASE_BREAKOUT,
    }
    assert len({event.event_id for event in payload["events"]}) == 2
    assert len({item["event_id"] for item in payload["range_persistence_shadow"]}) == 2


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


def _discovery(
    *,
    at: datetime,
    session: MarketSession,
    candidates: list[BreakoutCandidate],
) -> DiscoverySnapshot:
    return DiscoverySnapshot(
        provider="fixture",
        status=ProviderStatus.ACTIVE,
        as_of=at,
        session=session,
        schema_version="fixture-v1",
        candidate_count=len(candidates),
        candidates=candidates,
        cache_key=f"fixture-{session.value}-{at.isoformat()}",
    )


def _premarket_event(service: BreakoutRadarService, ticker: str = "GAP"):
    premarket_at = AS_OF.replace(hour=8, minute=0)
    candidate = BreakoutCandidate(
        ticker=ticker,
        price=100,
        previous_regular_close=90,
        provider_change_pct=10,
        provider_volume=2_000_000,
        provider_relative_volume=3,
        provider_market_cap=1_000_000_000,
        provider_timestamp=premarket_at,
        source="fixture",
        session=MarketSession.PREMARKET,
    )
    payload = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=premarket_at,
                session=MarketSession.PREMARKET,
                candidates=[candidate],
            )
        )
    )
    assert len(payload["events"]) == 1
    return payload["events"][0]


def test_premarket_gap_continues_after_discovery_dropout_with_same_identity() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="shadow"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(service)
    regular = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[first.model_dump(mode="python")],
        )
    )
    assert len(regular["events"]) == 1
    continued = regular["events"][0]
    assert continued.event_id == first.event_id
    assert continued.pivot_id == first.pivot_id
    assert continued.trading_date == first.trading_date
    assert continued.first_seen_at == first.first_seen_at
    assert continued.origin_setup_type is BreakoutSetupType.PREMARKET_GAP
    assert continued.setup_type is BreakoutSetupType.GAP_AND_GO
    assert continued.features["secondary_setup_type"] == "OPENING_RANGE_BREAKOUT"
    assert continued.features["range_persistence"] == first.features[
        "range_persistence"
    ]
    assert continued.features["event_freshness_score"] < first.features[
        "event_freshness_score"
    ]
    assert (
        continued.scores.alert_priority_score
        != first.scores.alert_priority_score
    )
    assert (
        "range_persistence_adjustment"
        not in continued.scores.details[
            "breakout_confirmation"
        ].contribution_breakdown
    )
    assert {item["event_id"] for item in regular["transitions"]} == {
        first.event_id
    }


def test_carryover_does_not_double_count_range_persistence_in_intrinsic_score() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="enabled",
            RANGE_PERSISTENCE_VALIDATION_VERSION="range-persistence-v1",
            RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED=True,
        ),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(service)
    prior = first.model_dump(mode="python")
    prior["features"] = {
        **prior["features"],
        "range_persistence_status": "active",
        "range_persistence_slope_5d": 8.0,
        "range_persistence_ratio_10d": 100.0,
        "strength_included_features": ["range_persistence"],
        "range_persistence_mode_at_score": "enabled",
    }

    continued = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[prior],
        )
    )["events"][0]

    interaction = continued.features["range_persistence_interaction"]
    assert interaction["confirmation_adjustment"] > 0
    assert "range_persistence_adjustment" not in continued.scores.details[
        "breakout_confirmation"
    ].contribution_breakdown


def test_prior_trading_day_carryover_does_not_block_new_same_ticker_event() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="shadow",
            BREAKOUT_EVENT_TTL_SECONDS=172800,
        ),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    old_at = AS_OF.replace(hour=8, minute=0) - pd.Timedelta(days=1)
    new_at = AS_OF.replace(hour=8, minute=0)

    def candidate(at):
        return BreakoutCandidate(
            ticker="GAP",
            price=100,
            previous_regular_close=90,
            provider_change_pct=10,
            provider_volume=2_000_000,
            provider_relative_volume=3,
            provider_market_cap=1_000_000_000,
            provider_timestamp=at,
            source="fixture",
            session=MarketSession.PREMARKET,
        )

    old_event = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=old_at,
                session=MarketSession.PREMARKET,
                candidates=[candidate(old_at)],
            )
        )
    )["events"][0]
    payload = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=new_at,
                session=MarketSession.PREMARKET,
                candidates=[candidate(new_at)],
            ),
            carryover_events=[old_event.model_dump(mode="python")],
        )
    )

    assert {event.trading_date for event in payload["events"]} == {
        old_at.date(),
        new_at.date(),
    }
    assert len({event.event_id for event in payload["events"]}) == 2


class FlatRegularPrices(Prices):
    def __init__(self, close: float):
        self.close = close

    async def intraday(self, tickers, *, cutoff, interval):
        snapshots = await super().intraday(tickers, cutoff=cutoff, interval=interval)
        return {
            ticker: PriceDataSnapshot(
                **{
                    **vars(snapshot),
                    "frame": snapshot.frame.assign(
                        Open=self.close,
                        High=self.close + 0.2,
                        Low=self.close - 0.2,
                        Close=self.close,
                    ),
                }
            )
            for ticker, snapshot in snapshots.items()
        }


class RecoveredAfterGapFadePrices(Prices):
    async def intraday(self, tickers, *, cutoff, interval):
        snapshots = await super().intraday(
            tickers,
            cutoff=cutoff,
            interval=interval,
        )
        result = {}
        for ticker, snapshot in snapshots.items():
            frame = snapshot.frame.assign(
                Open=95.0,
                High=95.2,
                Low=94.8,
                Close=95.0,
            )
            faded_at = frame.index[2]
            frame.loc[faded_at, ["Open", "High", "Low", "Close"]] = [
                89.0,
                89.2,
                88.8,
                89.0,
            ]
            result[ticker] = PriceDataSnapshot(
                **{**vars(snapshot), "frame": frame}
            )
        return result


class EarlierSessionHighPrices(FlatRegularPrices):
    async def intraday(self, tickers, *, cutoff, interval):
        snapshots = await super().intraday(
            tickers,
            cutoff=cutoff,
            interval=interval,
        )
        result = {}
        for ticker, snapshot in snapshots.items():
            frame = snapshot.frame.copy()
            frame.loc[frame.index[1], "High"] = 110.0
            result[ticker] = PriceDataSnapshot(
                **{**vars(snapshot), "frame": frame}
            )
        return result


class PreEventOutlierPrices(Prices):
    def __init__(self, *, invalidation: float, safe_price: float):
        self.invalidation = invalidation
        self.safe_price = safe_price

    async def intraday(self, tickers, *, cutoff, interval):
        snapshots = await super().intraday(
            tickers,
            cutoff=cutoff,
            interval=interval,
        )
        result = {}
        for ticker, snapshot in snapshots.items():
            frame = snapshot.frame.assign(
                Open=self.safe_price,
                High=self.safe_price + 0.5,
                Low=self.safe_price - 0.5,
                Close=self.safe_price,
            )
            frame.loc[frame.index[0], ["Open", "High", "Low", "Close"]] = [
                self.invalidation - 1.0,
                self.safe_price + 100.0,
                self.invalidation - 1.5,
                self.invalidation - 1.0,
            ]
            result[ticker] = PriceDataSnapshot(
                **{**vars(snapshot), "frame": frame}
            )
        return result


def test_gap_hold_and_gap_fade_keep_the_premarket_event_id() -> None:
    initial_service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(initial_service, ticker="PHASE")
    regular_discovery = _discovery(
        at=AS_OF,
        session=MarketSession.REGULAR,
        candidates=[],
    )
    hold_service = BreakoutRadarService(
        initial_service.settings,
        price_data=FlatRegularPrices(95),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    held = asyncio.run(
        hold_service.build_snapshot(
            regular_discovery,
            carryover_events=[first.model_dump(mode="python")],
        )
    )["events"][0]
    assert held.event_id == first.event_id
    assert held.setup_type is BreakoutSetupType.GAP_HOLD
    assert held.lifecycle_state is BreakoutLifecycleState.HOLDING

    fade_service = BreakoutRadarService(
        initial_service.settings,
        price_data=FlatRegularPrices(80),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    faded_payload = asyncio.run(
        fade_service.build_snapshot(
            _discovery(
                at=AS_OF + pd.Timedelta(minutes=5),
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[held.model_dump(mode="python")],
        )
    )
    faded = faded_payload["events"][0]
    assert faded.event_id == first.event_id
    assert faded.setup_type is BreakoutSetupType.GAP_FADE
    assert faded.lifecycle_state is BreakoutLifecycleState.FAILED
    assert faded_payload["transitions"][-1]["reason"] == (
        "gap_filled_on_complete_bar"
    )


def test_pre_event_bars_do_not_fail_or_raise_high_watermark_on_carryover() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    candidate = BreakoutCandidate(
        ticker="BOUNDARY",
        price=104,
        provider_change_pct=8,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    first = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[candidate],
            )
        )
    )["events"][0]
    structure = BreakoutStructure(
        ticker="BOUNDARY",
        base_start=datetime(2026, 6, 1).date(),
        base_end=datetime(2026, 7, 9).date(),
        calculation_cutoff_at=AS_OF,
        base_duration_days=30,
        resistance_zone=PriceZone(low=99.5, high=100.0),
        pivot_price=100.0,
        pivot_id="pivot-boundary",
        pivot_touch_count=2,
        invalidation_price=90.0,
        quality=0.8,
        status="active",
    )
    prior_high = float(first.features["event_high_watermark"] or first.event_price)
    carryover = first.model_copy(
        update={
            "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "origin_setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "lifecycle_state": BreakoutLifecycleState.CONFIRMED,
            "pivot_id": structure.pivot_id,
            "structure": structure,
            "features": {
                **first.features,
                "event_high_watermark": prior_high,
            },
        }
    )
    safe_price = max(
        structure.resistance_zone.high + 2.0,
        structure.invalidation_price + 2.0,
    )
    later = AS_OF + pd.Timedelta(minutes=5)
    continuation_service = BreakoutRadarService(
        service.settings,
        price_data=PreEventOutlierPrices(
            invalidation=structure.invalidation_price,
            safe_price=safe_price,
        ),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    continued = asyncio.run(
        continuation_service.build_snapshot(
            _discovery(
                at=later,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[carryover.model_dump(mode="python")],
        )
    )["events"][0]

    assert continued.lifecycle_state is not BreakoutLifecycleState.FAILED
    assert continued.features["event_high_watermark"] == max(
        prior_high,
        safe_price + 0.5,
    )
    assert continued.features["event_high_watermark"] < safe_price + 100.0


def test_gap_fade_and_high_watermark_include_all_complete_bars_after_missed_scan() -> None:
    initial_service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(initial_service, ticker="MISSED")
    regular_discovery = _discovery(
        at=AS_OF,
        session=MarketSession.REGULAR,
        candidates=[],
    )

    high_service = BreakoutRadarService(
        initial_service.settings,
        price_data=EarlierSessionHighPrices(95),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    high_event = asyncio.run(
        high_service.build_snapshot(
            regular_discovery,
            carryover_events=[first.model_dump(mode="python")],
        )
    )["events"][0]
    assert high_event.features["event_high_watermark"] == 110.0
    assert high_event.event_price == 95.0

    fade_service = BreakoutRadarService(
        initial_service.settings,
        price_data=RecoveredAfterGapFadePrices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    faded = asyncio.run(
        fade_service.build_snapshot(
            regular_discovery,
            carryover_events=[first.model_dump(mode="python")],
        )
    )["events"][0]
    assert faded.event_price == 95.0
    assert faded.setup_type is BreakoutSetupType.GAP_FADE
    assert faded.lifecycle_state is BreakoutLifecycleState.FAILED


class RecoveryMarket(Market):
    async def snapshot(self, *, as_of):
        return MarketShapeSnapshot(
            status="active",
            state="CAPITULATION_RECOVERY",
            confidence=1,
            as_of=as_of,
            version=self.version,
        )


class StructuredCarryoverPrices(NoIntradayPrices):
    async def intraday(self, tickers, *, cutoff, interval):
        return await Prices().intraday(tickers, cutoff=cutoff, interval=interval)


def test_retest_and_recovery_labels_update_an_existing_event_only() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=StructuredCarryoverPrices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    candidate = BreakoutCandidate(
        ticker="RETEST",
        price=104,
        previous_regular_close=95,
        provider_change_pct=8,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    first = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[candidate],
            )
        )
    )["events"][0]
    retesting = first.model_copy(
        update={
            "setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "origin_setup_type": BreakoutSetupType.DAILY_BASE_BREAKOUT,
            "lifecycle_state": BreakoutLifecycleState.RETESTING,
        }
    )
    later = AS_OF + pd.Timedelta(minutes=5)
    held = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=later,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[retesting.model_dump(mode="python")],
        )
    )["events"][0]
    assert held.event_id == first.event_id
    assert held.setup_type is BreakoutSetupType.RETEST_BREAKOUT
    assert held.lifecycle_state is BreakoutLifecycleState.RETEST_HELD

    recovery_service = BreakoutRadarService(
        service.settings,
        price_data=StructuredCarryoverPrices(),
        strength=Strength(),
        market_shape=RecoveryMarket(),
        universe=Universe(),
    )
    recovered = asyncio.run(
        recovery_service.build_snapshot(
            _discovery(
                at=later + pd.Timedelta(minutes=5),
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[held.model_dump(mode="python")],
        )
    )["events"][0]
    assert recovered.event_id == first.event_id
    assert recovered.setup_type is BreakoutSetupType.RECOVERY_BREAKOUT


class LowLiquidityCarryoverPrices(Prices):
    async def daily(self, tickers, *, cutoff, period):
        snapshots = await super().daily(tickers, cutoff=cutoff, period=period)
        return {
            ticker: PriceDataSnapshot(
                **{**vars(snapshot), "frame": snapshot.frame.assign(Volume=1_000)}
            )
            for ticker, snapshot in snapshots.items()
        }


def test_low_liquidity_carryover_updates_only_the_existing_event() -> None:
    first_service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(first_service, ticker="LOW")
    continuation_service = BreakoutRadarService(
        BreakoutSettings(
            _env_file=None,
            RANGE_PERSISTENCE_MODE="disabled",
            BREAKOUT_MIN_AVG_DOLLAR_VOLUME=10_000_000,
        ),
        price_data=LowLiquidityCarryoverPrices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    current_candidate = BreakoutCandidate(
        ticker="LOW",
        price=104,
        provider_change_pct=8,
        provider_timestamp=AS_OF,
        source="fixture",
        session=MarketSession.REGULAR,
    )
    payload = asyncio.run(
        continuation_service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[current_candidate],
            ),
            carryover_events=[first.model_dump(mode="python")],
        )
    )
    assert [event.event_id for event in payload["events"]] == [first.event_id]


def test_expired_due_carryover_becomes_terminal_without_market_data() -> None:
    settings = BreakoutSettings(
        _env_file=None,
        RANGE_PERSISTENCE_MODE="disabled",
        BREAKOUT_EVENT_TTL_SECONDS=300,
    )
    service = BreakoutRadarService(
        settings,
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(service, ticker="OLD")
    expired_at = first.first_seen_at + pd.Timedelta(minutes=6)
    payload = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=expired_at,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[first.model_dump(mode="python")],
            expired_due_event_ids=[first.event_id],
        )
    )
    assert payload["events"][0].event_id == first.event_id
    assert payload["events"][0].lifecycle_state is BreakoutLifecycleState.EXPIRED
    assert payload["transitions"][-1]["reason"] == "event_ttl_expired"


def test_future_or_terminal_carryover_is_not_reactivated() -> None:
    service = BreakoutRadarService(
        BreakoutSettings(_env_file=None, RANGE_PERSISTENCE_MODE="disabled"),
        price_data=Prices(),
        strength=Strength(),
        market_shape=Market(),
        universe=Universe(),
    )
    first = _premarket_event(service, ticker="FUTURE")
    future = first.model_copy(
        update={
            "first_seen_at": AS_OF + pd.Timedelta(minutes=5),
            "last_seen_at": AS_OF + pd.Timedelta(minutes=5),
        }
    )
    terminal = first.model_copy(
        update={"lifecycle_state": BreakoutLifecycleState.FAILED}
    )
    payload = asyncio.run(
        service.build_snapshot(
            _discovery(
                at=AS_OF,
                session=MarketSession.REGULAR,
                candidates=[],
            ),
            carryover_events=[
                future.model_dump(mode="python"),
                terminal.model_dump(mode="python"),
            ],
        )
    )
    assert payload["events"] == []
