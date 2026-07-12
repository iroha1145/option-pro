from __future__ import annotations

from datetime import datetime, timezone

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import AssetType, MarketSession
from app.services.breakouts.normalizer import (
    asset_type_from_provider,
    filter_and_deduplicate,
    normalize_provider_row,
    provider_symbol,
)
from app.services.breakouts.providers.tradingview import REGULAR_COLUMNS


def _row(**overrides):
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
    values.update(overrides)
    return [values[column] for column in REGULAR_COLUMNS]


def test_provider_symbol_rejects_injection_and_accepts_exchange_prefix() -> None:
    assert provider_symbol("NASDAQ:AAPL") == "AAPL"
    for value in ("../../AAPL", "AAPL?x", "AAPL\nMSFT", "ＡＡＰＬ", "A" * 16):
        try:
            provider_symbol(value)
        except ValueError:
            pass
        else:
            raise AssertionError(value)


def test_asset_type_is_defensive() -> None:
    assert asset_type_from_provider("stock", ["common"]) is AssetType.COMMON_STOCK
    assert asset_type_from_provider("stock", ["preferred"]) is AssetType.PREFERRED
    assert asset_type_from_provider("fund", ["etf"]) is AssetType.ETF


def test_normalizer_rejects_non_numeric_required_fields() -> None:
    candidate, warnings = normalize_provider_row(
        symbol="NASDAQ:AAPL",
        row=_row(close="not-a-number"),
        columns=REGULAR_COLUMNS,
        session=MarketSession.REGULAR,
        as_of=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    assert candidate is None
    assert warnings == ["provider_non_numeric_required_field"]


def test_no_technology_microcap_exemption_and_high_price_survives() -> None:
    settings = BreakoutSettings(_env_file=None)
    high, _ = normalize_provider_row(
        symbol="NASDAQ:AAPL",
        row=_row(close=225.0),
        columns=REGULAR_COLUMNS,
        session=MarketSession.REGULAR,
        as_of=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    small, _ = normalize_provider_row(
        symbol="NASDAQ:SMALL",
        row=_row(
            name="SMALL",
            close=8.0,
            change=12.0,
            volume=2_000_000,
            relative_volume_10d_calc=4.0,
            market_cap_basic=50_000_000,
        ),
        columns=REGULAR_COLUMNS,
        session=MarketSession.REGULAR,
        as_of=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )
    kept, _ = filter_and_deduplicate(
        [high, small],
        settings=settings,
        session=MarketSession.REGULAR,
    )
    assert [item.ticker for item in kept] == ["AAPL"]
    assert kept[0].price > 100
