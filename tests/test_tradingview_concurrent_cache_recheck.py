from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import DiscoveryProfile, MarketSession
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider


NOW = datetime(2026, 7, 10, 14, 30, 10, tzinfo=timezone.utc)


def _settings() -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        provider_retry_attempts=1,
        provider_cache_ttl_seconds=60,
        provider_max_concurrency=4,
    )


def test_completed_leader_is_reused_from_cache_without_a_second_request() -> None:
    calls = 0

    async def run() -> None:
        nonlocal calls

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(_settings(), client=client)
        try:
            first = await provider.scan(
                session=MarketSession.REGULAR,
                as_of=NOW,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            )
            second = await provider.scan(
                session=MarketSession.REGULAR,
                as_of=NOW,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            )
            assert second == first
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == 1


def test_different_as_of_values_in_one_cache_bucket_do_not_coalesce() -> None:
    calls = 0

    async def run() -> None:
        nonlocal calls
        release = asyncio.Event()

        async def handler(_request):
            nonlocal calls
            calls += 1
            await release.wait()
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(_settings(), client=client)
        later = NOW + timedelta(seconds=20)
        try:
            first = asyncio.create_task(
                provider.scan(
                    session=MarketSession.REGULAR,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
            second = asyncio.create_task(
                provider.scan(
                    session=MarketSession.REGULAR,
                    as_of=later,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
            for _ in range(20):
                if calls == 2:
                    break
                await asyncio.sleep(0)
            assert calls == 2
            release.set()
            earlier_snapshot, later_snapshot = await asyncio.gather(first, second)
            assert earlier_snapshot.as_of == NOW
            assert later_snapshot.as_of == later
        finally:
            await client.aclose()

    asyncio.run(run())


def test_session_schema_and_threshold_profiles_have_distinct_safety_keys() -> None:
    settings = _settings()
    provider = TradingViewDiscoveryProvider(settings, client=httpx.AsyncClient())
    regular = provider._cache_key(
        session=MarketSession.REGULAR,
        profile=DiscoveryProfile.REGULAR_MOVERS,
        as_of=NOW,
    )
    premarket = provider._cache_key(
        session=MarketSession.PREMARKET,
        profile=DiscoveryProfile.PREMARKET_GAPPERS,
        as_of=NOW,
    )
    settings.provider_schema_version = "tradingview-discovery-test-v2"
    schema_changed = provider._cache_key(
        session=MarketSession.REGULAR,
        profile=DiscoveryProfile.REGULAR_MOVERS,
        as_of=NOW,
    )
    settings.regular_min_change_pct += 1
    threshold_changed = provider._cache_key(
        session=MarketSession.REGULAR,
        profile=DiscoveryProfile.REGULAR_MOVERS,
        as_of=NOW,
    )
    asyncio.run(provider._client.aclose())

    assert len({regular, premarket, schema_changed, threshold_changed}) == 4
