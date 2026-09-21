"""All-market security eligibility derived from Massive reference metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from app.services.research_eod_v1.venue import classify_venue

from . import RETURN_BASIS
from .panel import current_universe_tickers


ELIGIBLE_PRIMARY_EXCHANGES = frozenset({"XNYS", "XNAS", "ARCX", "BATS", "XASE"})
STOCK_PROVIDER_TYPES = frozenset({"CS", "ADRC"})
FUND_PROVIDER_TYPES = frozenset({"ETF", "ETS", "ETV", "ETN", "FUND"})
ELIGIBLE_PROVIDER_TYPES = STOCK_PROVIDER_TYPES | FUND_PROVIDER_TYPES


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    name: str
    provider_type: str
    primary_exchange: str
    asset_track: str
    theme_ids: tuple[str, ...]
    venue_metadata: dict[str, Any]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _provider_security_type(provider_type: str) -> str:
    if provider_type == "ADRC":
        return "ADR"
    if provider_type in FUND_PROVIDER_TYPES:
        return "ETF" if provider_type != "ETN" else "ETN"
    return provider_type


def _theme_ids(ticker: str, *, asset_track: str) -> tuple[str, ...]:
    if asset_track == "etf":
        return ("etfs",)
    known = current_universe_tickers().get(ticker) or ()
    return tuple(dict.fromkeys(str(value) for value in known)) or ("all_market_stocks",)


def _excluded_reason(raw: Mapping[str, Any], requested: set[str] | None) -> str | None:
    ticker = _text(raw.get("ticker"))
    if not ticker:
        return "INVALID_TICKER"
    if requested is not None and ticker not in requested:
        return "NOT_REQUESTED"
    if raw.get("active") is not True:
        return "INACTIVE"
    if _text(raw.get("market")).lower() != "stocks":
        return "NON_STOCK_MARKET"
    if _text(raw.get("locale")).lower() != "us":
        return "NON_US_LOCALE"
    provider_type = _text(raw.get("type")).upper()
    if provider_type not in ELIGIBLE_PROVIDER_TYPES:
        return f"UNSUPPORTED_SECURITY_TYPE:{provider_type or 'MISSING'}"
    exchange = _text(raw.get("primary_exchange")).upper()
    if exchange not in ELIGIBLE_PRIMARY_EXCHANGES:
        return f"NOT_US_MAJOR_EXCHANGE:{exchange or 'MISSING'}"
    return None


def select_all_market_universe(
    directory: Sequence[Mapping[str, Any]],
    *,
    tickers: Iterable[str] | None = None,
) -> tuple[dict[str, UniverseMember], list[dict[str, Any]]]:
    """Return eligible members and one deterministic coverage row per ticker.

    ``tickers`` is an explicit diagnostic subset. It never intersects the
    checked-in theme catalogue; directory rows outside the subset remain in the
    coverage ledger as ``excluded:NOT_REQUESTED``.
    """

    requested = None
    if tickers is not None:
        requested = {str(value).strip().upper() for value in tickers if str(value).strip()}
    members: dict[str, UniverseMember] = {}
    coverage: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in directory:
        ticker = _text(raw.get("ticker"))
        if not ticker or ticker in seen:
            raise ValueError("Massive directory contains an invalid or duplicate ticker")
        seen.add(ticker)
        provider_type = _text(raw.get("type")).upper()
        exchange = _text(raw.get("primary_exchange")).upper()
        reason = _excluded_reason(raw, requested)
        base = {
            "ticker": ticker,
            "name": _text(raw.get("name")) or ticker,
            "provider_type": provider_type,
            "primary_exchange": exchange,
        }
        if reason is not None:
            coverage.append({**base, "status": f"excluded:{reason}", "bars": 0})
            continue

        asset_track = "stock" if provider_type in STOCK_PROVIDER_TYPES else "etf"
        themes = _theme_ids(ticker, asset_track=asset_track)
        venue = {
            "listing_country": "US",
            "locale": "us",
            "exchange": exchange,
            "primary_exchange": exchange,
            "mic": exchange,
            "primary_mic": exchange,
            "security_type": _provider_security_type(provider_type),
            "provider_type": provider_type,
            "asset_track": asset_track,
            "name": base["name"],
            "identity_confidence": "massive_reference_directory",
            "industry_source": "none_without_independent_classification",
            "return_basis": RETURN_BASIS,
            "return_transform_version": "all-market-split-adjusted-close-v1",
        }
        decision = classify_venue(venue)
        if not decision.eligible:
            coverage.append(
                {**base, "status": f"excluded:{decision.reason}", "bars": 0}
            )
            continue
        members[ticker] = UniverseMember(
            ticker=ticker,
            name=base["name"],
            provider_type=provider_type,
            primary_exchange=exchange,
            asset_track=asset_track,
            theme_ids=themes,
            venue_metadata=venue,
        )
        coverage.append(
            {
                **base,
                "asset_track": asset_track,
                "theme_ids": list(themes),
                "status": "pending",
                "bars": 0,
            }
        )
    return members, coverage


__all__ = [
    "ELIGIBLE_PRIMARY_EXCHANGES",
    "ELIGIBLE_PROVIDER_TYPES",
    "FUND_PROVIDER_TYPES",
    "STOCK_PROVIDER_TYPES",
    "UniverseMember",
    "select_all_market_universe",
]
