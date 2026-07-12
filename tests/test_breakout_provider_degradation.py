from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import (
    DiscoveryProfile,
    MarketSession,
    ProviderStatus,
)
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider


FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "breakouts" / "provider_regular.json").read_text()
)
NOW = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.value = 100.0

    def __call__(self) -> float:
        return self.value


def _settings(**overrides) -> BreakoutSettings:
    defaults = {
        "provider_retry_attempts": 1,
        "provider_failure_threshold": 3,
        "provider_cache_ttl_seconds": 1,
        "provider_stale_ttl_seconds": 30,
    }
    defaults.update(overrides)
    return BreakoutSettings(_env_file=None, **defaults)


def _run(provider):
    return asyncio.run(
        provider.scan(
            session=MarketSession.REGULAR,
            as_of=NOW,
            profile=DiscoveryProfile.REGULAR_MOVERS,
        )
    )


def test_429_and_5xx_are_bounded_and_return_unavailable() -> None:
    for status in (429, 503):
        calls = 0

        async def handler(_request):
            nonlocal calls
            calls += 1
            return httpx.Response(status, headers={"Retry-After": "999"})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(_settings(), client=client)
        snapshot = _run(provider)
        asyncio.run(client.aclose())
        assert calls == 1
        assert snapshot.status is ProviderStatus.UNAVAILABLE


def test_timeout_is_unavailable_without_stale_snapshot() -> None:
    async def handler(_request):
        raise httpx.ReadTimeout("slow")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client)
    snapshot = _run(provider)
    asyncio.run(client.aclose())
    assert snapshot.status is ProviderStatus.UNAVAILABLE
    assert "provider_timeout" in snapshot.warnings


def test_stale_on_error_is_bounded() -> None:
    clock = Clock()
    responses = [httpx.Response(200, json=FIXTURE), httpx.Response(503)]

    async def handler(_request):
        return responses.pop(0) if responses else httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client, monotonic=clock)
    fresh = _run(provider)
    clock.value += 2
    stale = _run(provider)
    clock.value += 31
    unavailable = _run(provider)
    asyncio.run(client.aclose())
    assert fresh.status is ProviderStatus.ACTIVE
    assert stale.status is ProviderStatus.STALE
    assert unavailable.status is ProviderStatus.UNAVAILABLE


def test_circuit_breaker_stops_transport_calls() -> None:
    clock = Clock()
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(503)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(
        _settings(provider_failure_threshold=2),
        client=client,
        monotonic=clock,
    )
    assert _run(provider).status is ProviderStatus.UNAVAILABLE
    assert _run(provider).status is ProviderStatus.UNAVAILABLE
    assert _run(provider).status is ProviderStatus.UNAVAILABLE
    asyncio.run(client.aclose())
    assert calls == 2
    assert provider.health["circuit_open"] is True


def test_oversized_response_is_rejected_before_json_parse() -> None:
    async def handler(_request):
        return httpx.Response(200, content=b"x" * 1025)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(
        _settings(provider_max_response_bytes=1024),
        client=client,
    )
    snapshot = _run(provider)
    asyncio.run(client.aclose())
    assert snapshot.status is ProviderStatus.UNAVAILABLE
    assert "provider_payload_too_large" in snapshot.warnings


def test_retry_is_bounded_and_can_recover() -> None:
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503)
        return httpx.Response(200, json=FIXTURE)

    async def no_sleep(_seconds):
        return None

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(
        _settings(provider_retry_attempts=2),
        client=client,
        sleeper=no_sleep,
    )
    snapshot = _run(provider)
    asyncio.run(client.aclose())
    assert calls == 2
    assert snapshot.status is ProviderStatus.ACTIVE
