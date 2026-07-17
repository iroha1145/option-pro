from __future__ import annotations

import json

import httpx
import pytest

from app.services.catalysts.etl_client import (
    EtlAuthenticationError,
    EtlClientError,
    EtlClientConfig,
    EtlCursorResetRequired,
    EtlProtocolError,
    EtlResponseTooLarge,
    MacroLensEtlClient,
)


NOW = "2026-07-15T12:00:00.123456Z"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _health() -> dict:
    return {
        "status": "ok",
        "service": "macrolens-etl",
        "as_of": NOW,
        "data_through": "2026-07-15T11:59:00Z",
        "database": "ok",
        "scheduler": "running",
        "watermarks": {"news_sequence": 7, "calendar_sequence": 3},
        "sources": [],
    }


def _news(news_id: int = 1) -> dict:
    return {
        "id": news_id,
        "source": "finnhub",
        "title": "Chip demand rises",
        "summary": "Raw source summary",
        "url": f"https://example.com/news/{news_id}",
        "image_url": None,
        "published_at": "2026-07-15T10:00:00Z",
        "fetched_at": "2026-07-15T10:01:00Z",
        "updated_at": "2026-07-15T10:02:00Z",
        "source_tickers": ["AMD", "NVDA"],
        "sources": ["finnhub", "massive"],
        "source_count": 2,
        "content_hash": f"hash-{news_id}",
    }


@pytest.mark.anyio
async def test_client_uses_one_bearer_header_and_caps_news_pages_at_five_hundred():
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "items": [],
                "has_more": False,
                "next_cursor": None,
                "watermark": {"sequence": 0, "as_of": NOW},
                "next_updated_after": NOW,
                "next_after_sequence": 0,
            },
        )

    transport = httpx.MockTransport(handler)
    config = EtlClientConfig("https://macrolens.example", "owner-secret")
    async with MacroLensEtlClient(config, transport=transport) as client:
        page = await client.news_changes(
            updated_after="1970-01-01T00:00:00Z",
            after_sequence=0,
            limit=500,
        )
        with pytest.raises(ValueError, match="between 1 and 500"):
            await client.news_changes(limit=501)

    assert page.items == []
    assert len(requests) == 1
    request = requests[0]
    assert request.url.path == "/internal/v1/news/changes"
    assert request.url.params["limit"] == "500"
    assert request.url.params["after_sequence"] == "0"
    assert request.headers.get_list("authorization") == ["Bearer owner-secret"]
    assert "owner-secret" not in str(request.url)


@pytest.mark.anyio
async def test_news_detail_accepts_the_actual_macrolens_contract():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/internal/v1/news/4"
        assert request.url.params["as_of"] == NOW
        return httpx.Response(
            200,
            json={
                "item": _news(4),
                "watermark": {"sequence": 9, "as_of": NOW},
                "available_at": NOW,
            },
        )

    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        detail = await client.news_item(4, as_of=NOW)

    assert detail.item.sources == ["finnhub", "massive"]
    assert detail.item.source_count == 2
    assert detail.item.source_observations == []
    assert detail.item.model_extra == {}


@pytest.mark.anyio
async def test_authentication_failure_is_never_retried():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(401, json={"detail": {"code": "invalid_owner_token"}})

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    client = MacroLensEtlClient(
        EtlClientConfig(
            "https://macrolens.example",
            "wrong-secret",
            max_attempts=5,
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    )
    with pytest.raises(EtlAuthenticationError):
        await client.health()
    await client.aclose()

    assert calls == 1
    assert sleeps == []


@pytest.mark.anyio
async def test_cursor_expiry_is_classified_for_checkpoint_reset_without_retry():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            409,
            json={"detail": {"code": "calendar_snapshot_expired"}},
        )

    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(EtlCursorResetRequired) as raised:
            await client.calendar(cursor="expired-cursor")

    assert raised.value.code == "calendar_snapshot_expired"
    assert calls == 1


@pytest.mark.anyio
async def test_retryable_status_is_bounded_and_eventually_succeeds():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(503, json={"detail": {"code": "not_ready"}})
        return httpx.Response(200, json=_health())

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-secret"),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    ) as client:
        health = await client.health()

    assert health.status == "ok"
    assert calls == 2
    assert sleeps == [0.25]


@pytest.mark.anyio
async def test_response_limit_applies_before_json_parsing():
    oversized = json.dumps({"padding": "x" * 2_000}).encode()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            content=oversized,
            headers={"content-type": "application/json"},
        )

    async with MacroLensEtlClient(
        EtlClientConfig(
            "https://macrolens.example",
            "owner-secret",
            max_response_bytes=1_024,
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(EtlResponseTooLarge):
            await client.health()


@pytest.mark.anyio
async def test_offline_health_probe_returns_unavailable_after_bounded_retries():
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("offline", request=request)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    async with MacroLensEtlClient(
        EtlClientConfig(
            "https://macrolens.example",
            "owner-secret",
            max_attempts=2,
        ),
        transport=httpx.MockTransport(handler),
        sleep=sleep,
    ) as client:
        probe = await client.probe_health()

    assert probe.reachable is False
    assert probe.authenticated is False
    assert probe.status == "unavailable"
    assert probe.error_code == "network_error"
    assert probe.health is None
    assert calls == 2
    assert sleeps == [0.25]


@pytest.mark.anyio
async def test_health_probe_distinguishes_a_reached_but_failing_service():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"detail": {"code": "not_ready"}})

    async with MacroLensEtlClient(
        EtlClientConfig(
            "https://macrolens.example",
            "owner-secret",
            max_attempts=1,
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        probe = await client.probe_health()

    assert probe.reachable is True
    assert probe.authenticated is True
    assert probe.status == "unavailable"
    assert probe.error_code == "not_ready"
    assert probe.health is None


@pytest.mark.anyio
async def test_sequence_checkpoint_ahead_is_not_retried_or_reset():
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.params["after_sequence"] == "7"
        return httpx.Response(
            409,
            json={"detail": {"code": "sequence_checkpoint_ahead"}},
        )

    async with MacroLensEtlClient(
        EtlClientConfig(
            "https://macrolens.example",
            "owner-secret",
            max_attempts=5,
        ),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(EtlClientError) as raised:
            await client.news_changes(after_sequence=7)

    assert raised.value.code == "sequence_checkpoint_ahead"
    assert not isinstance(raised.value, EtlCursorResetRequired)
    assert calls == 1


@pytest.mark.anyio
async def test_complete_page_rejects_a_sequence_checkpoint_that_differs_from_watermark():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [],
                "has_more": False,
                "next_cursor": None,
                "watermark": {"sequence": 4, "as_of": NOW},
                "next_updated_after": NOW,
                "next_after_sequence": 3,
            },
        )

    async with MacroLensEtlClient(
        EtlClientConfig("https://macrolens.example", "owner-secret"),
        transport=httpx.MockTransport(handler),
    ) as client:
        with pytest.raises(EtlProtocolError):
            await client.news_changes(after_sequence=2)


def test_config_requires_tls_and_rejects_credential_bearing_origins():
    with pytest.raises(ValueError, match="using HTTPS"):
        EtlClientConfig("http://macrolens.example", "owner-secret")
    with pytest.raises(ValueError, match="using HTTPS"):
        EtlClientConfig("https://user:pass@macrolens.example", "owner-secret")


def test_config_repr_never_contains_the_owner_token():
    config = EtlClientConfig("https://macrolens.example", "owner-secret")

    assert "owner-secret" not in repr(config)
