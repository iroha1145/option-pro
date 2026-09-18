"""Optional Massive fallback. Reads MASSIVE_API_KEY from the environment only.

Never log, print, or persist the secret. Production market-data wiring is unchanged.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from app.services.research_eod_v1.data.contract import (
    UNSUPPORTED,
    CorporateAction,
    DatasetMeta,
    ProviderCapabilities,
    ResearchBar,
    SecurityIdentity,
    hash_payload,
)

ET = ZoneInfo("America/New_York")


def _key() -> str:
    return (os.environ.get("MASSIVE_API_KEY") or "").strip()


class MassiveEnvProvider:
    def __init__(self, *, allow_network: bool = True, base_url: str | None = None) -> None:
        self.allow_network = allow_network
        self.base_url = (base_url or os.environ.get("MASSIVE_BASE_URL") or "https://api.massive.com").rstrip("/")
        self.failures: list[dict[str, Any]] = []

    def probe_capabilities(self) -> ProviderCapabilities:
        configured = bool(_key())
        notes = [
            "credential_from_env_only",
            "secret_not_written_to_repo",
            "research_fallback_not_production_vendor_change",
        ]
        if not configured:
            notes.append("AUTH_REQUIRED")
        return ProviderCapabilities(
            provider="massive_env",
            dataset_id="massive-research-fallback",
            dataset_version="env-v1",
            probe_status="AUTH_REQUIRED" if not configured else ("ACCESS_TESTED" if self.allow_network else "DOCUMENTED_ONLY"),
            daily_bars="available" if configured else UNSUPPORTED,
            corporate_actions="partial" if configured else UNSUPPORTED,
            classification_history=UNSUPPORTED,
            security_master="partial" if configured else UNSUPPORTED,
            delisted_coverage="unverified",
            volume_session_scope="UNKNOWN",
            raw_price_verified=False,
            notes=tuple(notes),
            capability_level="ACCESS_TESTED" if configured else "DOCUMENTED_ONLY",
        )

    def load_security_master(self) -> list[SecurityIdentity] | str:
        return UNSUPPORTED

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str:
        return UNSUPPORTED

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str:
        return UNSUPPORTED

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
    ) -> list[ResearchBar] | str:
        if not self.allow_network:
            return UNSUPPORTED
        key = _key()
        if not key:
            self.failures.append({"symbol": symbol, "error": "AUTH_REQUIRED"})
            return UNSUPPORTED
        try:
            import httpx
        except Exception as exc:
            self.failures.append({"symbol": symbol, "error": type(exc).__name__})
            return []
        path = f"/v2/aggs/ticker/{symbol}/range/1/day/{start.isoformat()}/{end.isoformat()}"
        try:
            response = httpx.get(
                self.base_url + path,
                params={"adjusted": "false", "sort": "asc", "limit": 50000},
                headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                timeout=20.0,
            )
        except Exception as exc:
            self.failures.append({"symbol": symbol, "error": type(exc).__name__})
            return []
        if response.status_code in {401, 403}:
            self.failures.append({"symbol": symbol, "error": "AUTH_REQUIRED", "status": response.status_code})
            return UNSUPPORTED
        if response.status_code == 429:
            self.failures.append({"symbol": symbol, "error": "rate_limited"})
            return []
        if response.status_code >= 400:
            self.failures.append({"symbol": symbol, "error": f"http_{response.status_code}"})
            return []
        payload = response.json()
        rows = payload.get("results") or []
        retrieved = datetime.now(timezone.utc)
        out: list[ResearchBar] = []
        for row in rows:
            stamp = row.get("t")
            if stamp is None:
                continue
            session = datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc).astimezone(ET).date()
            if not (start <= session < end):
                continue
            close = _finite(row.get("c"))
            open_ = _finite(row.get("o"))
            volume = _finite(row.get("v"))
            out.append(
                ResearchBar(
                    security_id=identity.security_id if identity else symbol,
                    session_date=session,
                    open=open_,
                    high=_finite(row.get("h")),
                    low=_finite(row.get("l")),
                    close=close,
                    raw_open=open_,
                    raw_close=close,
                    volume=volume,
                    dollar_volume=None if close is None or volume is None else close * volume,
                    tri=close,
                    volume_scope="UNKNOWN",
                    price_adjustment="massive_adjusted_false_unverified_raw",
                    volume_adjustment="unverified",
                    missing=close is None or open_ is None,
                    source_published_at=retrieved,
                    vintage_status="download_time_not_pit",
                )
            )
        return out

    def export_snapshot(self, path: str) -> DatasetMeta | str:
        return DatasetMeta(
            provider="massive_env",
            dataset_id="massive-research-fallback",
            dataset_version="env-v1",
            retrieved_at=datetime.now(timezone.utc).isoformat(),
            request_params_redacted={"adjusted": False, "auth": "bearer_env"},
            content_sha256=hash_payload(self.failures),
        )


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number
