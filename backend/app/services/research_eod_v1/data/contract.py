"""Vendor-neutral research data contract. Providers never feed raw JSON into compute."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Protocol

UNSUPPORTED = "UNSUPPORTED"


@dataclass(frozen=True)
class SecurityIdentity:
    security_id: str
    provider_symbol: str
    share_class: str | None
    security_type: str
    primary_mic: str | None
    listing_country: str
    asset_track: str
    listed_at: date | None = None
    delisted_at: date | None = None
    aliases: tuple[str, ...] = ()
    identity_confidence: str = "unverified"


@dataclass(frozen=True)
class ResearchBar:
    security_id: str
    session_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    raw_open: float | None
    raw_close: float | None
    volume: float | None
    dollar_volume: float | None
    tri: float | None
    volume_scope: str = "UNKNOWN"
    price_adjustment: str = "unverified"
    volume_adjustment: str = "unverified"
    missing: bool = False
    halted: bool = False
    economic_known_at: datetime | None = None
    source_published_at: datetime | None = None
    vintage_status: str = "download_time_not_pit"


@dataclass(frozen=True)
class CorporateAction:
    security_id: str
    session_date: date
    kind: str
    value: float
    economic_known_at: datetime | None = None


@dataclass(frozen=True)
class ProviderCapabilities:
    provider: str
    dataset_id: str
    dataset_version: str
    probe_status: str
    daily_bars: str
    corporate_actions: str
    classification_history: str
    security_master: str
    delisted_coverage: str
    volume_session_scope: str
    raw_price_verified: bool
    notes: tuple[str, ...] = ()
    capability_level: str = "DOCUMENTED_ONLY"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DatasetMeta:
    provider: str
    dataset_id: str
    dataset_version: str
    retrieved_at: str
    request_params_redacted: dict[str, Any] = field(default_factory=dict)
    content_sha256: str = ""


def hash_payload(payload: Any) -> str:
    blob = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


class ResearchDataProvider(Protocol):
    def probe_capabilities(self) -> ProviderCapabilities: ...

    def load_security_master(self) -> list[SecurityIdentity] | str: ...

    def fetch_daily_bars(
        self,
        symbol: str,
        start: date,
        end: date,
        *,
        identity: SecurityIdentity | None = None,
    ) -> list[ResearchBar] | str: ...

    def fetch_corporate_actions(self, symbol: str, start: date, end: date) -> list[CorporateAction] | str: ...

    def load_classification_history(self, symbol: str) -> Mapping[str, Any] | str: ...

    def export_snapshot(self, path: str) -> DatasetMeta | str: ...
