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


def _run(provider, *, as_of=NOW):
    return asyncio.run(
        provider.scan(
            session=MarketSession.REGULAR,
            as_of=as_of,
            profile=DiscoveryProfile.REGULAR_MOVERS,
        )
    )


def test_explicit_settings_override_environment_alias(monkeypatch) -> None:
    monkeypatch.setenv("BREAKOUT_PROVIDER_RETRY_ATTEMPTS", "2")
    monkeypatch.setenv("BREAKOUT_PROVIDER_CACHE_TTL_SECONDS", "60")
    monkeypatch.setenv("BREAKOUT_PROVIDER_MAX_RESPONSE_BYTES", "2000000")
    monkeypatch.setenv("BREAKOUT_PROVIDER_FAILURE_THRESHOLD", "3")

    settings = BreakoutSettings(
        _env_file=None,
        provider_retry_attempts=1,
        provider_cache_ttl_seconds=1,
        provider_max_response_bytes=1024,
        provider_failure_threshold=2,
    )

    assert settings.provider_retry_attempts == 1
    assert settings.provider_cache_ttl_seconds == 1
    assert settings.provider_max_response_bytes == 1024
    assert settings.provider_failure_threshold == 2


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


def test_stale_fallback_finds_previous_cache_time_bucket() -> None:
    clock = Clock()
    responses = [httpx.Response(200, json=FIXTURE), httpx.Response(503)]

    async def handler(_request):
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client, monotonic=clock)
    fresh = _run(provider, as_of=NOW)
    clock.value += 2
    requested_at = NOW.replace(second=NOW.second + 2)
    stale = _run(provider, as_of=requested_at)
    asyncio.run(client.aclose())

    assert fresh.cache_key != provider._cache_key(
        session=MarketSession.REGULAR,
        profile=DiscoveryProfile.REGULAR_MOVERS,
        as_of=requested_at,
    )
    assert stale.status is ProviderStatus.STALE
    assert stale.as_of == fresh.as_of
    assert "provider_server_error" in stale.warnings
    assert provider.health["stale_snapshot_available"] is True


def test_previous_cache_time_bucket_expires_at_stale_ttl() -> None:
    clock = Clock()
    responses = [httpx.Response(200, json=FIXTURE), httpx.Response(503)]

    async def handler(_request):
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client, monotonic=clock)
    fresh = _run(provider, as_of=NOW)
    clock.value += 31
    requested_at = NOW.replace(second=NOW.second + 2)
    unavailable = _run(provider, as_of=requested_at)
    asyncio.run(client.aclose())

    assert fresh.cache_key != unavailable.cache_key
    assert unavailable.status is ProviderStatus.UNAVAILABLE
    assert provider.health["stale_snapshot_available"] is False


def test_cross_bucket_stale_fallback_never_uses_future_snapshot() -> None:
    clock = Clock()
    responses = [
        httpx.Response(200, json=FIXTURE),
        httpx.Response(200, json=FIXTURE),
        httpx.Response(503),
    ]

    async def handler(_request):
        return responses.pop(0)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client, monotonic=clock)
    earlier = _run(provider, as_of=NOW.replace(second=0))
    clock.value += 2
    later = _run(provider, as_of=NOW.replace(second=50))
    clock.value += 2
    replay = _run(provider, as_of=NOW.replace(second=10))
    asyncio.run(client.aclose())

    assert later.as_of > replay.as_of
    assert replay.status is ProviderStatus.STALE
    assert replay.as_of == earlier.as_of


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


def test_same_day_replay_never_reuses_a_future_cached_snapshot() -> None:
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=FIXTURE)

    async def run() -> tuple:
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        provider = TradingViewDiscoveryProvider(
            _settings(provider_cache_ttl_seconds=60),
            client=client,
        )
        later = NOW.replace(second=50)
        earlier = NOW.replace(second=10)
        try:
            first = await provider.scan(
                session=MarketSession.REGULAR,
                as_of=later,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            )
            second = await provider.scan(
                session=MarketSession.REGULAR,
                as_of=earlier,
                profile=DiscoveryProfile.REGULAR_MOVERS,
            )
            return first, second
        finally:
            await client.aclose()

    first, replay = asyncio.run(run())
    assert first.as_of > replay.as_of
    assert replay.as_of == NOW.replace(second=10)
    assert all(candidate.provider_timestamp <= replay.as_of for candidate in replay.candidates)
    assert calls == 2
