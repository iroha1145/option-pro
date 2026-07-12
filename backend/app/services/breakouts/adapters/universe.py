"""Stable, versioned theme-universe baseline for normalization."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.services.sectors import SECTORS


# These are liquid reference baskets, not claims about an issuer's official
# GICS classification.  A reference is used only when every matching theme
# points to the same basket, or when the upstream provider supplies a sector.
THEME_BENCHMARKS: dict[str, str] = {
    "semiconductors": "SOXX",
    "software": "XLK",
    "ai_cloud": "XLK",
    "biotech": "XBI",
    "healthcare": "XLV",
    "consumer_electronics": "XLK",
    "automotive": "XLY",
    "ev_supply": "XLY",
    "finance": "XLF",
    "fintech": "XLF",
    "retail": "XLY",
    "luxury": "XLY",
    "media_streaming": "XLC",
    "social_internet": "XLC",
    "energy": "XLE",
    "utilities": "XLU",
    "defense_aero": "XLI",
    "airlines": "XLI",
    "real_estate": "XLRE",
    "china_adr": "KWEB",
    "telecom": "XLC",
    "industrials": "XLI",
}

_PROVIDER_SECTOR_BENCHMARKS: dict[str, str | None] = {
    "technology": "XLK",
    "information technology": "XLK",
    "electronic technology": "XLK",
    "technology services": "XLK",
    "financial": "XLF",
    "financial services": "XLF",
    "finance": "XLF",
    "healthcare": "XLV",
    "health care": "XLV",
    "health technology": "XLV",
    "health services": "XLV",
    "energy": "XLE",
    "energy minerals": "XLE",
    "industrials": "XLI",
    "industrial": "XLI",
    "producer manufacturing": "XLI",
    "industrial services": "XLI",
    "transportation": "XLI",
    "commercial services": "XLI",
    "distribution services": "XLI",
    "communication services": "XLC",
    "communication": "XLC",
    "communications": "XLC",
    "consumer cyclical": "XLY",
    "consumer discretionary": "XLY",
    "consumer durables": "XLY",
    "consumer services": "XLY",
    "retail trade": "XLY",
    "consumer defensive": "XLP",
    "consumer staples": "XLP",
    "consumer non-durables": "XLP",
    "utilities": "XLU",
    "real estate": "XLRE",
    "basic materials": "XLB",
    "materials": "XLB",
    "non-energy minerals": "XLB",
    "process industries": "XLB",
    "miscellaneous": None,
}


class ThemeCanonicalUniverseAdapter:
    """Use the repository's fixed theme map without claiming full-US coverage."""

    def __init__(self) -> None:
        normalized = {
            sector_id: sorted(
                {
                    str(ticker).upper()
                    for ticker in data["tickers"]
                    if not str(ticker).upper().endswith(".PA")
                }
            )
            for sector_id, data in sorted(SECTORS.items())
        }
        digest = hashlib.sha256(
            json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()[:12]
        self.version = f"canonical-universe-v1-{digest}"
        self._sectors = normalized
        self._ticker_sectors: dict[str, list[str]] = {}
        for sector_id, tickers in normalized.items():
            for ticker in tickers:
                self._ticker_sectors.setdefault(ticker, []).append(sector_id)

    async def tickers(self, *, as_of: datetime) -> list[str]:
        if as_of.tzinfo is None or as_of.utcoffset() is None:
            raise ValueError("as_of must include a timezone")
        return sorted(self._ticker_sectors)

    def memberships(self, ticker: str) -> list[str]:
        return list(self._ticker_sectors.get(ticker.upper(), ()))

    def primary_sector(self, ticker: str) -> str | None:
        memberships = self.memberships(ticker)
        # The existing map contains investment themes, not authoritative
        # primary-sector classifications. Multi-theme members must not be
        # assigned an arbitrary first theme for sector normalization.
        return memberships[0] if len(memberships) == 1 else None

    def sector_benchmark(
        self,
        ticker: str,
        provider_sector: str | None = None,
    ) -> str | None:
        normalized_sector = " ".join(str(provider_sector or "").lower().split())
        if normalized_sector:
            # Provider classifications are exact, reviewable mappings. An
            # unknown value remains missing instead of guessing from a
            # substring such as "technology" inside "health technology".
            return _PROVIDER_SECTOR_BENCHMARKS.get(normalized_sector)
        references = {
            THEME_BENCHMARKS[theme]
            for theme in self.memberships(ticker)
            if theme in THEME_BENCHMARKS
        }
        return next(iter(references)) if len(references) == 1 else None

    async def distributions(
        self,
        *,
        feature: str,
        as_of: date,
        sector: str | None = None,
    ) -> dict[str, Any]:
        return {
            "feature": feature,
            "as_of": as_of.isoformat(),
            "sector": sector,
            "values": [],
            "status": "unavailable",
            "universe_version": self.version,
            "warning": "historical canonical distributions are not persisted yet",
        }
