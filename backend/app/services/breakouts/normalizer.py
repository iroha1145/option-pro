"""Defensive normalization for untrusted discovery payloads."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.models import (
    AssetType,
    BreakoutCandidate,
    MarketSession,
    normalize_ticker,
)


def finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def provider_symbol(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if ":" in raw:
        prefix, symbol = raw.split(":", 1)
        if not prefix or not prefix.replace("_", "").isalnum():
            raise ValueError("invalid provider exchange prefix")
        raw = symbol
    return normalize_ticker(raw)


def asset_type_from_provider(raw_type: Any, raw_specs: Any) -> AssetType:
    type_text = str(raw_type or "").strip().lower()
    if isinstance(raw_specs, (list, tuple, set)):
        specs = {str(item).strip().lower() for item in raw_specs}
    else:
        specs = {part.strip().lower() for part in str(raw_specs or "").split(",") if part.strip()}
    joined = " ".join(sorted(specs | {type_text}))
    if any(token in joined for token in ("warrant", "right")):
        return AssetType.WARRANT
    if "unit" in joined:
        return AssetType.UNIT
    if "preferred" in joined:
        return AssetType.PREFERRED
    if any(token in joined for token in ("depositary", "adr", "dr")):
        return AssetType.ADR
    if any(token in joined for token in ("etf", "exchange traded fund")):
        return AssetType.ETF
    if any(token in joined for token in ("fund", "mutual")):
        return AssetType.FUND
    if any(token in joined for token in ("stock", "common")):
        return AssetType.COMMON_STOCK
    return AssetType.UNKNOWN


def normalize_provider_row(
    *,
    symbol: Any,
    row: Sequence[Any],
    columns: Sequence[str],
    session: MarketSession,
    as_of: datetime,
    source: str = "tradingview",
) -> tuple[BreakoutCandidate | None, list[str]]:
    warnings: list[str] = []
    if len(row) != len(columns):
        return None, ["provider_column_count_changed"]
    data = dict(zip(columns, row))
    try:
        ticker = provider_symbol(symbol or data.get("name"))
    except ValueError:
        return None, ["invalid_ticker"]

    if session is MarketSession.PREMARKET:
        price = finite_number(data.get("premarket_close"))
        change = finite_number(data.get("premarket_change"))
        volume = finite_number(data.get("premarket_volume"))
        previous_close = finite_number(data.get("close"))
    else:
        price = finite_number(data.get("close"))
        change = finite_number(data.get("change"))
        volume = finite_number(data.get("volume"))
        previous_close = None
    relative_volume = finite_number(data.get("relative_volume_10d_calc"))
    market_cap = finite_number(data.get("market_cap_basic"))
    if price is None or change is None or volume is None:
        return None, ["provider_non_numeric_required_field"]
    if relative_volume is None and session is MarketSession.REGULAR:
        warnings.append("provider_relative_volume_missing")
    asset_type = asset_type_from_provider(data.get("type"), data.get("typespecs"))
    raw = {
        key: data.get(key)
        for key in (
            "description",
            "type",
            "typespecs",
            "close",
            "change",
            "volume",
            "premarket_close",
            "premarket_change",
            "premarket_volume",
            "relative_volume_10d_calc",
            "market_cap_basic",
        )
    }
    return (
        BreakoutCandidate(
            ticker=ticker,
            exchange=str(data.get("exchange") or "").strip().upper() or None,
            asset_type=asset_type,
            name=str(data.get("description") or "").strip()[:200] or None,
            sector=str(data.get("sector") or "").strip()[:120] or None,
            price=price,
            previous_regular_close=previous_close,
            provider_change_pct=change,
            provider_volume=max(volume, 0.0),
            provider_relative_volume=max(relative_volume, 0.0)
            if relative_volume is not None
            else None,
            provider_market_cap=max(market_cap, 0.0) if market_cap is not None else None,
            provider_timestamp=as_of,
            source=source,
            session=session,
            quality=1.0 if not warnings else 0.85,
            warnings=warnings,
            raw_provider_fields=raw,
        ),
        warnings,
    )


def filter_and_deduplicate(
    candidates: Iterable[BreakoutCandidate],
    *,
    settings: BreakoutSettings,
    session: MarketSession,
) -> tuple[list[BreakoutCandidate], list[str]]:
    warnings: list[str] = []
    kept: dict[str, BreakoutCandidate] = {}
    allowed_assets = {AssetType.COMMON_STOCK, AssetType.ADR}
    if settings.allow_etf:
        allowed_assets.add(AssetType.ETF)
    for candidate in candidates:
        if candidate.asset_type not in allowed_assets:
            warnings.append(f"{candidate.ticker}:asset_type_excluded")
            continue
        if candidate.price is None or candidate.price < settings.min_price:
            continue
        if (
            candidate.provider_market_cap is not None
            and candidate.provider_market_cap < settings.provider_min_market_cap
        ):
            continue
        price = candidate.price
        volume = candidate.provider_volume or 0.0
        dollar_volume = price * volume
        if session is MarketSession.PREMARKET:
            if (candidate.provider_change_pct or 0) < settings.premarket_min_change_pct:
                continue
            if dollar_volume < settings.premarket_min_dollar_volume:
                continue
        else:
            if (candidate.provider_change_pct or 0) < settings.regular_min_change_pct:
                continue
            if (candidate.provider_relative_volume or 0) < settings.regular_min_relative_volume:
                continue
            if dollar_volume < settings.regular_min_dollar_volume:
                continue
        previous = kept.get(candidate.ticker)
        if previous is None or (candidate.provider_change_pct or 0) > (
            previous.provider_change_pct or 0
        ):
            kept[candidate.ticker] = candidate
    ordered = sorted(
        kept.values(),
        key=lambda item: (
            item.provider_change_pct or float("-inf"),
            item.ticker,
        ),
        reverse=True,
    )[: settings.provider_result_limit]
    return ordered, warnings
