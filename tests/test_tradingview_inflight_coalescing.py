from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import DiscoveryProfile, MarketSession
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider


NOW = datetime(2026, 7, 10, 14, 30, 10, tzinfo=timezone.utc)


def _settings(**overrides) -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        provider_retry_attempts=1,
        provider_cache_ttl_seconds=60,
        **overrides,
    )


def test_ten_identical_concurrent_scans_make_one_network_request() -> None:
    calls = 0

    async def run() -> None:
        nonlocal calls

        async def handler(_request):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.02)
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(_settings(), client=client)
        try:
            snapshots = await asyncio.gather(
                *[
                    provider.scan(
                        session=MarketSession.REGULAR,
                        as_of=NOW,
                        profile=DiscoveryProfile.REGULAR_MOVERS,
                    )
                    for _ in range(10)
                ]
            )
            assert all(snapshot == snapshots[0] for snapshot in snapshots)
            assert provider._inflight == {}
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == 1

def test_cancelled_follower_does_not_cancel_leader() -> None:
    calls = 0

    async def run() -> None:
        nonlocal calls
        started = asyncio.Event()
        release = asyncio.Event()

        async def handler(_request):
            nonlocal calls
            calls += 1
            started.set()
            await release.wait()
            return httpx.Response(200, json={"data": []})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(_settings(), client=client)
        try:
            leader = asyncio.create_task(
                provider.scan(
                    session=MarketSession.REGULAR,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
            await started.wait()
            follower = asyncio.create_task(
                provider.scan(
                    session=MarketSession.REGULAR,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
            await asyncio.sleep(0)
            follower.cancel()
            with pytest.raises(asyncio.CancelledError):
                await follower
            assert not leader.done()
            release.set()
            assert (await leader).as_of == NOW
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == 1
