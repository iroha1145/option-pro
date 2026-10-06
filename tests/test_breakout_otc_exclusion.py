"""OTC discovery rows leave both the TradingView query and normalization by default."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import DiscoveryProfile, MarketSession
from app.services.breakouts.normalizer import filter_and_deduplicate, normalize_provider_row
from app.services.breakouts.providers.tradingview import (
    REGULAR_COLUMNS,
    TradingViewDiscoveryProvider,
)


def _settings(**overrides) -> BreakoutSettings:
    return BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True, **overrides)


def _row(ticker: str, exchange: str, change: float) -> list:
    return [
        ticker,
        exchange,
        f"{ticker} Inc.",
        "stock",
        ["common"],
        25.0,
        change,
        5_000_000,
        2.5,
        1_500_000_000,
        "Technology",
    ]


def _candidate(ticker: str, exchange: str, change: float):
    candidate, warnings = normalize_provider_row(
        symbol=f"{exchange}:{ticker}",
        row=_row(ticker, exchange, change),
        columns=REGULAR_COLUMNS,
        session=MarketSession.REGULAR,
        as_of=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
    )
    assert candidate is not None and not warnings
    return candidate


def test_allow_otc_defaults_off_and_is_the_only_new_setting() -> None:
    settings = _settings()
    assert settings.allow_otc is False
    assert _settings(BREAKOUT_ALLOW_OTC=True).allow_otc is True


def test_every_query_profile_excludes_otc_before_the_result_window() -> None:
    provider = TradingViewDiscoveryProvider(_settings())
    try:
        for session, profile in (
            (MarketSession.REGULAR, DiscoveryProfile.REGULAR_MOVERS),
            (MarketSession.REGULAR, DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS),
            (MarketSession.PREMARKET, DiscoveryProfile.PREMARKET_GAPPERS),
        ):
            payload = provider._payload(session, profile)
            exchange_filters = [item for item in payload["filter"] if item["left"] == "exchange"]
            assert exchange_filters == [
                {"left": "exchange", "operation": "nequal", "right": "OTC"}
            ], (session, profile)
            # The exclusion sits in the same request whose range TradingView cuts.
            assert payload["range"][0] == 0
    finally:
        asyncio.run(provider.aclose())


def test_allow_otc_removes_the_query_filter_and_changes_the_cache_boundary() -> None:
    default = TradingViewDiscoveryProvider(_settings())
    permissive = TradingViewDiscoveryProvider(_settings(BREAKOUT_ALLOW_OTC=True))
    try:
        payload = permissive._payload(MarketSession.REGULAR, DiscoveryProfile.REGULAR_MOVERS)
        assert all(item["left"] != "exchange" for item in payload["filter"])
        assert default._safety_boundary(
            session=MarketSession.REGULAR, profile=DiscoveryProfile.REGULAR_MOVERS
        ) != permissive._safety_boundary(
            session=MarketSession.REGULAR, profile=DiscoveryProfile.REGULAR_MOVERS
        )
    finally:
        asyncio.run(default.aclose())
        asyncio.run(permissive.aclose())


def test_normalization_drops_otc_rows_by_default_and_keeps_them_when_allowed() -> None:
    listed = _candidate("AAPL", "NASDAQ", 4.0)
    otc = _candidate("NSRGY", "OTC", 6.0)

    kept, warnings = filter_and_deduplicate(
        [otc, listed], settings=_settings(), session=MarketSession.REGULAR
    )
    assert [item.ticker for item in kept] == ["AAPL"]
    assert warnings == ["NSRGY:otc_exchange_excluded"]

    kept, warnings = filter_and_deduplicate(
        [otc, listed],
        settings=_settings(BREAKOUT_ALLOW_OTC=True),
        session=MarketSession.REGULAR,
    )
    assert [item.ticker for item in kept] == ["NSRGY", "AAPL"]
    assert warnings == []
