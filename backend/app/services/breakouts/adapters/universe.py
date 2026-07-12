"""Stable, versioned theme-universe baseline for normalization."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Any

from app.services.sectors import SECTORS


class ThemeCanonicalUniverseAdapter:
    """Use the repository's fixed theme map without claiming full-US coverage."""

    def __init__(self) -> None:
        normalized = {
            sector_id: sorted({str(ticker).upper() for ticker in data["tickers"]})
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
        return memberships[0] if memberships else None

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
