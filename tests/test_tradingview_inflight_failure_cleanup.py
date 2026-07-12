from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import httpx
import pytest

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import DiscoveryProfile, MarketSession
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider


NOW = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def test_unexpected_leader_failure_is_cleaned_and_next_call_can_retry(monkeypatch) -> None:
    calls = 0

    async def run() -> None:
        nonlocal calls
        client = httpx.AsyncClient()
        provider = TradingViewDiscoveryProvider(
            BreakoutSettings(
                _env_file=None,
                provider_retry_attempts=1,
                provider_cache_ttl_seconds=60,
            ),
            client=client,
        )

        async def fetch(_session):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("unexpected leader failure")
            return json.dumps({"data": []}).encode()

        monkeypatch.setattr(provider, "_fetch", fetch)
        try:
            with pytest.raises(RuntimeError, match="unexpected leader failure"):
                await provider.scan(
                    session=MarketSession.REGULAR,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            assert not provider._inflight or all(
                future.done() for future in provider._inflight.values()
            )
            recovered = await provider.scan(
                session=MarketSession.REGULAR,
                as_of=NOW,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            )
            assert recovered.as_of == NOW
            assert provider._inflight == {}
        finally:
            await client.aclose()

    asyncio.run(run())
    assert calls == 2
