"""Ordinary ETF discovery rows leave both the TradingView query and normalization by default."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import AssetType, DiscoveryProfile, MarketSession
from app.services.breakouts.normalizer import filter_and_deduplicate, normalize_provider_row
from app.services.breakouts.providers.tradingview import (
    REGULAR_COLUMNS,
    TradingViewDiscoveryProvider,
)

ETF_FILTER = {"left": "type", "operation": "in_range", "right": ["stock", "dr"]}


def _settings(**overrides) -> BreakoutSettings:
    return BreakoutSettings(_env_file=None, BREAKOUT_RADAR_ENABLED=True, **overrides)


def _candidate(ticker: str, description: str, kind: str, typespecs: list[str], change: float):
    candidate, warnings = normalize_provider_row(
        symbol=f"NASDAQ:{ticker}",
        row=[ticker, "NASDAQ", description, kind, typespecs, 25.0, change, 5_000_000, 2.5, 1_500_000_000, "Technology"],
        columns=REGULAR_COLUMNS,
        session=MarketSession.REGULAR,
        as_of=datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc),
    )
    assert candidate is not None and not warnings
    return candidate


def test_allow_etf_defaults_off() -> None:
    assert _settings().allow_etf is False
    assert _settings(BREAKOUT_ALLOW_ETF=True).allow_etf is True


def test_every_query_profile_keeps_only_stocks_and_receipts_before_the_result_window() -> None:
    provider = TradingViewDiscoveryProvider(_settings())
    try:
        for session, profile in (
            (MarketSession.REGULAR, DiscoveryProfile.REGULAR_MOVERS),
            (MarketSession.REGULAR, DiscoveryProfile.REGULAR_DOLLAR_VOLUME_LEADERS),
            (MarketSession.PREMARKET, DiscoveryProfile.PREMARKET_GAPPERS),
        ):
            payload = provider._payload(session, profile)
            type_filters = [item for item in payload["filter"] if item["left"] == "type"]
            # The exclusion sits in the same request whose range TradingView cuts.
            assert type_filters == [ETF_FILTER], (session, profile)
    finally:
        asyncio.run(provider.aclose())


def test_allow_etf_removes_the_query_filter_and_changes_the_cache_boundary() -> None:
    default = TradingViewDiscoveryProvider(_settings())
    permissive = TradingViewDiscoveryProvider(_settings(BREAKOUT_ALLOW_ETF=True))
    try:
        payload = permissive._payload(MarketSession.REGULAR, DiscoveryProfile.REGULAR_MOVERS)
        assert all(item["left"] != "type" for item in payload["filter"])
        assert default._safety_boundary(
            session=MarketSession.REGULAR, profile=DiscoveryProfile.REGULAR_MOVERS
        ) != permissive._safety_boundary(
            session=MarketSession.REGULAR, profile=DiscoveryProfile.REGULAR_MOVERS
        )
    finally:
        asyncio.run(default.aclose())
        asyncio.run(permissive.aclose())


def test_normalization_drops_ordinary_etfs_by_default_and_keeps_them_when_allowed() -> None:
    stock = _candidate("AAPL", "Apple Inc.", "stock", ["common"], 4.0)
    etf = _candidate("XLK", "Technology Select Sector SPDR Fund", "fund", ["etf"], 5.0)
    assert etf.asset_type is AssetType.ETF

    kept, warnings = filter_and_deduplicate([etf, stock], settings=_settings(), session=MarketSession.REGULAR)
    assert [item.ticker for item in kept] == ["AAPL"]
    assert warnings == ["XLK:etf_excluded"]

    kept, warnings = filter_and_deduplicate(
        [etf, stock], settings=_settings(BREAKOUT_ALLOW_ETF=True), session=MarketSession.REGULAR
    )
    assert [item.ticker for item in kept] == ["XLK", "AAPL"]
    assert warnings == []


def test_depositary_receipts_stay_in_the_default_universe() -> None:
    # Live TradingView rows type ADRs as "dr" with an empty typespec (checked 2026-10-05).
    receipt = _candidate("TSM", "Taiwan Semiconductor Manufacturing Co. Ltd.", "dr", [""], 4.5)
    assert receipt.asset_type is AssetType.ADR
    kept, warnings = filter_and_deduplicate([receipt], settings=_settings(), session=MarketSession.REGULAR)
    assert [item.ticker for item in kept] == ["TSM"]
    assert warnings == []


def test_leveraged_funds_are_still_reported_as_leveraged_first() -> None:
    leveraged = _candidate("TQQQ", "ProShares UltraPro QQQ", "fund", ["etf"], 6.0)
    for settings in (_settings(), _settings(BREAKOUT_ALLOW_ETF=True)):
        kept, warnings = filter_and_deduplicate([leveraged], settings=settings, session=MarketSession.REGULAR)
        assert kept == []
        assert warnings == ["TQQQ:leveraged_etf_excluded"]
