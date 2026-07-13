from __future__ import annotations

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
from app.services.breakouts.providers.tradingview import (
    PREMARKET_COLUMNS,
    REGULAR_COLUMNS,
    TradingViewDiscoveryProvider,
)


FIXTURES = Path(__file__).parent / "fixtures" / "breakouts"
NOW = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)


def _settings(**overrides) -> BreakoutSettings:
    return BreakoutSettings(_env_file=None, **overrides)


async def _scan(
    payload,
    *,
    session=MarketSession.REGULAR,
    settings=None,
    profile=None,
):
    async def handler(_request):
        return httpx.Response(200, json=payload)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(settings or _settings(), client=client)
    profile = profile or (
        DiscoveryProfile.PREMARKET_GAPPERS
        if session is MarketSession.PREMARKET
        else DiscoveryProfile.REGULAR_MOVERS
    )
    try:
        return await provider.scan(session=session, as_of=NOW, profile=profile)
    finally:
        await client.aclose()


def test_regular_response_is_normalized_and_filtered() -> None:
    import asyncio

    payload = json.loads((FIXTURES / "provider_regular.json").read_text())
    snapshot = asyncio.run(_scan(payload))
    assert snapshot.status is ProviderStatus.ACTIVE
    assert snapshot.candidate_count == 1
    assert snapshot.candidates[0].ticker == "AAPL"
    assert snapshot.candidates[0].price == 225.5


def test_regular_dollar_volume_profile_has_no_change_or_rvol_threshold() -> None:
    import asyncio

    values = {
        "name": "FLAT",
        "exchange": "NASDAQ",
        "description": "Flat Corp",
        "type": "stock",
        "typespecs": ["common"],
        "close": 100.0,
        "change": None,
        "volume": 2_000_000,
        "relative_volume_10d_calc": None,
        # The volume-leader profile must not inherit the movers market-cap gate.
        "market_cap_basic": 1,
        "sector": "Technology",
    }
    second = {**values, "name": "FAST", "close": 20.0, "volume": 5_000_000}
    payload = {
        "columns": list(REGULAR_COLUMNS),
        "data": [
            {
                "s": "NASDAQ:FAST",
                "d": [second[column] for column in REGULAR_COLUMNS],
            },
            {
                "s": "NASDAQ:FLAT",
                "d": [values[column] for column in REGULAR_COLUMNS],
            },
        ],
    }
    snapshot = asyncio.run(
        _scan(
            payload,
            profile=DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS,
        )
    )

    assert [item.ticker for item in snapshot.candidates] == ["FLAT", "FAST"]
    assert snapshot.candidate_count == 2


def test_regular_dollar_volume_profile_enforces_only_its_liquidity_boundary() -> None:
    import asyncio

    base = {
        "exchange": "NASDAQ",
        "description": "Fixture Corp",
        "type": "stock",
        "typespecs": ["common"],
        "close": 10.0,
        "change": -50.0,
        "relative_volume_10d_calc": 0.01,
        "market_cap_basic": 1,
        "sector": "Technology",
    }
    rows = [
        {**base, "name": "ENOUGH", "volume": 600_000},
        {**base, "name": "TOOSMALL", "volume": 499_999},
    ]
    payload = {
        "columns": list(REGULAR_COLUMNS),
        "data": [
            {
                "s": f"NASDAQ:{row['name']}",
                "d": [row[column] for column in REGULAR_COLUMNS],
            }
            for row in rows
        ],
    }
    snapshot = asyncio.run(
        _scan(
            payload,
            profile=DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS,
        )
    )

    assert [item.ticker for item in snapshot.candidates] == ["ENOUGH"]


def test_regular_dollar_volume_payload_keeps_movers_payload_unchanged() -> None:
    import asyncio

    provider = TradingViewDiscoveryProvider(_settings())
    try:
        movers = provider._payload(
            MarketSession.REGULAR,
            DiscoveryProfile.REGULAR_MOVERS,
        )
        leaders = provider._payload(
            MarketSession.REGULAR,
            DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS,
        )

        assert {item["left"] for item in movers["filter"]} == {
            "close",
            "change",
            "relative_volume_10d_calc",
        }
        assert {item["left"] for item in leaders["filter"]} == {
            "close",
            "Value.Traded",
        }
        assert next(
            item for item in leaders["filter"] if item["left"] == "Value.Traded"
        )["right"] == 5_000_000
        assert leaders["sort"] == {"sortBy": "Value.Traded", "sortOrder": "desc"}
        assert leaders["range"] == [0, 100]
    finally:
        asyncio.run(provider.aclose())


def test_premarket_response_uses_real_premarket_fields() -> None:
    import asyncio

    payload = json.loads((FIXTURES / "provider_premarket.json").read_text())
    snapshot = asyncio.run(_scan(payload, session=MarketSession.PREMARKET))
    assert snapshot.status is ProviderStatus.ACTIVE
    assert snapshot.candidate_count == 1
    candidate = snapshot.candidates[0]
    assert candidate.price == 160.0
    assert candidate.previous_regular_close == 150.0
    assert candidate.provider_change_pct == 6.67


def test_empty_response_is_active_empty_snapshot() -> None:
    import asyncio

    payload = json.loads((FIXTURES / "provider_empty.json").read_text())
    snapshot = asyncio.run(_scan(payload))
    assert snapshot.status is ProviderStatus.ACTIVE
    assert snapshot.candidates == []


def test_closed_and_postmarket_return_unavailable_without_transport() -> None:
    import asyncio

    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported sessions must not call the Provider")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client)
    try:
        for session in (MarketSession.POSTMARKET, MarketSession.CLOSED):
            snapshot = asyncio.run(
                provider.scan(
                    session=session,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
            assert snapshot.status is ProviderStatus.UNAVAILABLE
            assert snapshot.candidates == []
            assert snapshot.warnings == ["session_not_supported"]
    finally:
        asyncio.run(client.aclose())
    assert calls == 0


def test_column_count_change_degrades_and_skips_row() -> None:
    import asyncio

    payload = {
        "data": [{"s": "NASDAQ:AAPL", "d": ["AAPL", "NASDAQ"]}],
    }
    snapshot = asyncio.run(_scan(payload))
    assert snapshot.status is ProviderStatus.DEGRADED
    assert snapshot.candidate_count == 0
    assert "provider_column_count_changed" in snapshot.warnings


def test_reordered_fields_are_mapped_by_returned_column_names() -> None:
    import asyncio

    columns = list(reversed(REGULAR_COLUMNS))
    values = {
        "name": "AAPL",
        "exchange": "NASDAQ",
        "description": "Apple",
        "type": "stock",
        "typespecs": ["common"],
        "close": 225.0,
        "change": 4.0,
        "volume": 30_000_000,
        "relative_volume_10d_calc": 2.0,
        "market_cap_basic": 3_000_000_000_000,
        "sector": "Technology",
    }
    payload = {
        "columns": columns,
        "data": [{"s": "NASDAQ:AAPL", "d": [values[column] for column in columns]}],
    }
    snapshot = asyncio.run(_scan(payload))
    assert snapshot.status is ProviderStatus.DEGRADED
    assert snapshot.candidates[0].ticker == "AAPL"
    assert "provider_column_order_changed" in snapshot.warnings


def test_invalid_ticker_is_rejected_before_candidate_creation() -> None:
    import asyncio

    payload = json.loads((FIXTURES / "provider_regular.json").read_text())
    payload["data"][0]["s"] = "NASDAQ:AAPL\nMSFT"
    snapshot = asyncio.run(_scan(payload))
    assert all(item.ticker != "AAPL\nMSFT" for item in snapshot.candidates)
    assert "invalid_ticker" in snapshot.warnings


def test_profile_must_match_session() -> None:
    import asyncio

    async def handler(_request):
        return httpx.Response(200, json={"data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    provider = TradingViewDiscoveryProvider(_settings(), client=client)
    try:
        try:
            asyncio.run(
                provider.scan(
                    session=MarketSession.PREMARKET,
                    as_of=NOW,
                    profile=DiscoveryProfile.REGULAR_MOVERS,
                )
            )
        except ValueError:
            pass
        else:
            raise AssertionError("mismatched profile was accepted")
    finally:
        asyncio.run(client.aclose())


def test_malformed_row_is_skipped_without_bypassing_provider_degradation() -> None:
    import asyncio

    payload = json.loads((FIXTURES / "provider_regular.json").read_text())
    columns = payload.get("columns") or list(REGULAR_COLUMNS)
    row = payload["data"][0]["d"]
    row[columns.index("exchange")] = "X" * 200
    row[columns.index("close")] = 0
    snapshot = asyncio.run(_scan(payload))
    assert snapshot.status is ProviderStatus.DEGRADED
    assert snapshot.candidates == []
    assert "provider_non_numeric_required_field" in snapshot.warnings
