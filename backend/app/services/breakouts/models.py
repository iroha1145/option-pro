"""Versioned public domain models for Breakout Radar."""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
MARKET_TIMEZONE = "America/New_York"


def normalize_ticker(value: Any) -> str:
    symbol = str(value or "").strip().upper()
    if (
        not TICKER_PATTERN.fullmatch(symbol)
        or symbol.endswith((".", "-"))
        or ".." in symbol
        or "--" in symbol
    ):
        raise ValueError("invalid ticker")
    return symbol


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


class MarketSession(str, Enum):
    PREMARKET = "premarket"
    REGULAR = "regular"
    POSTMARKET = "postmarket"
    CLOSED = "closed"


class DiscoveryProfile(str, Enum):
    REGULAR_MOVERS = "regular_movers"
    PREMARKET_GAPPERS = "premarket_gappers"


class AssetType(str, Enum):
    COMMON_STOCK = "common_stock"
    ETF = "etf"
    ADR = "adr"
    PREFERRED = "preferred"
    WARRANT = "warrant"
    UNIT = "unit"
    FUND = "fund"
    UNKNOWN = "unknown"


class BreakoutSetupType(str, Enum):
    DAILY_BASE_BREAKOUT = "DAILY_BASE_BREAKOUT"
    OPENING_RANGE_BREAKOUT = "OPENING_RANGE_BREAKOUT"
    PREMARKET_GAP = "PREMARKET_GAP"
    GAP_AND_GO = "GAP_AND_GO"
    GAP_HOLD = "GAP_HOLD"
    GAP_FADE = "GAP_FADE"
    RETEST_BREAKOUT = "RETEST_BREAKOUT"
    MOMENTUM_SPIKE = "MOMENTUM_SPIKE"
    RECOVERY_BREAKOUT = "RECOVERY_BREAKOUT"


class BreakoutLifecycleState(str, Enum):
    DISCOVERED = "DISCOVERED"
    WATCHING = "WATCHING"
    TRIGGERED = "TRIGGERED"
    CONFIRMED = "CONFIRMED"
    HOLDING = "HOLDING"
    RETESTING = "RETESTING"
    RETEST_HELD = "RETEST_HELD"
    REACCELERATING = "REACCELERATING"
    EXTENDED = "EXTENDED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"


class ProviderStatus(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"


class ScanStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    ABANDONED = "abandoned"


class TemporalCutoff(_StrictModel):
    event_at: AwareDatetime
    market_timezone: str = MARKET_TIMEZONE
    include_current_bar: bool = False
    completed_daily_session: Optional[date] = None
    session: MarketSession

    @field_validator("market_timezone")
    @classmethod
    def validate_market_timezone(cls, value: str) -> str:
        if value != MARKET_TIMEZONE:
            raise ValueError(f"market_timezone must be {MARKET_TIMEZONE}")
        return value


class BreakoutCandidate(_StrictModel):
    ticker: str
    exchange: Optional[str] = Field(default=None, max_length=24)
    asset_type: AssetType = AssetType.UNKNOWN
    name: Optional[str] = Field(default=None, max_length=200)
    sector: Optional[str] = Field(default=None, max_length=120)
    industry: Optional[str] = Field(default=None, max_length=120)
    price: Optional[float] = Field(default=None, gt=0)
    previous_regular_close: Optional[float] = Field(default=None, gt=0)
    provider_change_pct: Optional[float] = None
    provider_volume: Optional[float] = Field(default=None, ge=0)
    provider_relative_volume: Optional[float] = Field(default=None, ge=0)
    provider_market_cap: Optional[float] = Field(default=None, ge=0)
    provider_timestamp: AwareDatetime
    source: str = Field(min_length=1, max_length=40)
    session: MarketSession
    quality: float = Field(default=1.0, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=32)
    raw_provider_fields: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def validate_ticker(cls, value: Any) -> str:
        return normalize_ticker(value)

    @field_validator(
        "price",
        "previous_regular_close",
        "provider_change_pct",
        "provider_volume",
        "provider_relative_volume",
        "provider_market_cap",
    )
    @classmethod
    def validate_finite(cls, value: Optional[float]) -> Optional[float]:
        if value is None:
            return None
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("numeric fields must be finite")
        return number

    @field_validator("raw_provider_fields")
    @classmethod
    def validate_raw_provider_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        try:
            encoded = json.dumps(value, allow_nan=False, default=str).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("raw provider fields must be serializable") from exc
        if len(encoded) > 8_192:
            raise ValueError("raw provider fields exceed 8192 bytes")
        return value


class DiscoverySnapshot(_StrictModel):
    provider: str = Field(min_length=1, max_length=40)
    status: ProviderStatus
    as_of: AwareDatetime
    session: MarketSession
    schema_version: str = Field(min_length=1, max_length=80)
    candidate_count: int = Field(default=0, ge=0, le=150)
    warnings: list[str] = Field(default_factory=list, max_length=64)
    candidates: list[BreakoutCandidate] = Field(default_factory=list, max_length=150)
    cache_key: Optional[str] = Field(default=None, max_length=128)


class PriceZone(_StrictModel):
    low: float
    high: float
    strength: Optional[float] = Field(default=None, ge=0, le=1)
    touches: Optional[int] = Field(default=None, ge=0)
    age_days: Optional[int] = Field(default=None, ge=0)

    @field_validator("high")
    @classmethod
    def high_not_below_low(cls, value: float, info: Any) -> float:
        low = info.data.get("low")
        if low is not None and value < low:
            raise ValueError("zone high must not be below low")
        return value


class BreakoutStructure(_StrictModel):
    ticker: str
    base_start: date
    base_end: date
    calculation_cutoff_at: AwareDatetime
    base_duration_days: int = Field(ge=1)
    support_zone: Optional[PriceZone] = None
    resistance_zone: PriceZone
    pivot_price: float
    pivot_id: str = Field(min_length=8, max_length=128)
    pivot_touch_count: int = Field(ge=1)
    pivot_last_touch_at: Optional[AwareDatetime] = None
    base_width_pct: Optional[float] = None
    base_width_atr: Optional[float] = None
    atr_contraction: Optional[float] = None
    volume_contraction: Optional[float] = None
    relative_strength_slope: Optional[float] = None
    invalidation_price: Optional[float] = None
    quality: float = Field(ge=0, le=1)
    status: str = Field(min_length=1, max_length=40)
    metrics: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ticker", mode="before")
    @classmethod
    def validate_ticker(cls, value: Any) -> str:
        return normalize_ticker(value)


class ScoreResult(_StrictModel):
    score: Optional[float] = Field(default=None, ge=0, le=100)
    status: str
    confidence: float = Field(ge=0, le=1)
    configured_weights: dict[str, float] = Field(default_factory=dict)
    effective_weights: dict[str, float] = Field(default_factory=dict)
    contribution_breakdown: dict[str, float] = Field(default_factory=dict)
    penalties: dict[str, float] = Field(default_factory=dict)
    missing_components: list[str] = Field(default_factory=list)
    score_version: str


class BreakoutScores(_StrictModel):
    intrinsic_strength_score: Optional[float] = Field(default=None, ge=0, le=100)
    base_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    breakout_confirmation_score: Optional[float] = Field(default=None, ge=0, le=100)
    liquidity_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    chase_risk_score: Optional[float] = Field(default=None, ge=0, le=100)
    sector_fit_score: Optional[float] = Field(default=None, ge=0, le=100)
    market_fit_score: Optional[float] = Field(default=None, ge=0, le=100)
    breakout_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    alert_priority_score: Optional[float] = Field(default=None, ge=0, le=100)
    data_confidence_score: Optional[float] = Field(default=None, ge=0, le=100)
    details: dict[str, ScoreResult] = Field(default_factory=dict)


class BreakoutEvent(_StrictModel):
    event_id: str = Field(min_length=8, max_length=128)
    trading_date: date
    ticker: str
    name: Optional[str] = Field(default=None, max_length=200)
    exchange: Optional[str] = Field(default=None, max_length=24)
    asset_type: AssetType = AssetType.UNKNOWN
    sector: Optional[str] = Field(default=None, max_length=120)
    session: MarketSession
    setup_type: BreakoutSetupType
    origin_setup_type: Optional[BreakoutSetupType] = None
    lifecycle_state: BreakoutLifecycleState
    event_at: AwareDatetime
    first_seen_at: AwareDatetime
    triggered_at: Optional[AwareDatetime] = None
    state_changed_at: AwareDatetime
    last_seen_at: AwareDatetime
    pivot_id: str = Field(min_length=1, max_length=128)
    event_price: Optional[float] = Field(default=None, gt=0)
    event_bar_interval: Optional[str] = Field(default=None, max_length=20)
    source_snapshot_id: str
    previous_state: Optional[BreakoutLifecycleState] = None
    transition_reason: Optional[str] = Field(default=None, max_length=120)
    structure: Optional[BreakoutStructure] = None
    scores: BreakoutScores = Field(default_factory=BreakoutScores)
    data_quality: dict[str, Any] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    features: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("ticker", mode="before")
    @classmethod
    def validate_ticker(cls, value: Any) -> str:
        return normalize_ticker(value)

    @model_validator(mode="before")
    @classmethod
    def normalize_event_times(cls, value: Any) -> Any:
        """Keep legacy payloads readable while enforcing stable time meanings."""

        if not isinstance(value, dict):
            return value
        data = dict(value)
        event_at = data.get("event_at")
        first_seen_at = data.get("first_seen_at") or event_at
        lifecycle_state = str(
            getattr(data.get("lifecycle_state"), "value", data.get("lifecycle_state"))
            or "DISCOVERED"
        )
        triggered_at_supplied = "triggered_at" in data
        triggered_at = data.get("triggered_at")
        if not triggered_at_supplied and lifecycle_state in {
            BreakoutLifecycleState.TRIGGERED.value,
            BreakoutLifecycleState.CONFIRMED.value,
            BreakoutLifecycleState.HOLDING.value,
            BreakoutLifecycleState.RETESTING.value,
            BreakoutLifecycleState.RETEST_HELD.value,
            BreakoutLifecycleState.REACCELERATING.value,
            BreakoutLifecycleState.EXTENDED.value,
            BreakoutLifecycleState.FAILED.value,
        }:
            # Pre-v2 event payloads only carried event_at.  The repository
            # migration performs the authoritative backfill; this fallback
            # keeps fixtures and imported mappings compatible.
            triggered_at = event_at
        if first_seen_at is not None:
            data["first_seen_at"] = first_seen_at
        data["triggered_at"] = triggered_at
        data["event_at"] = triggered_at or first_seen_at or event_at
        data["state_changed_at"] = (
            data.get("state_changed_at") or event_at or first_seen_at
        )
        return data


class StrengthScoreSnapshot(_StrictModel):
    ticker: str
    score: Optional[float] = Field(default=None, ge=0, le=100)
    score_scope: str
    confidence: float = Field(ge=0, le=1)
    score_version: str
    included_features: list[str] = Field(default_factory=list)
    factor_breakdown: dict[str, Any] = Field(default_factory=dict)
    coverage: dict[str, Any] = Field(default_factory=dict)
    as_of: AwareDatetime

    @field_validator("ticker", mode="before")
    @classmethod
    def validate_ticker(cls, value: Any) -> str:
        return normalize_ticker(value)


class MarketShapeSnapshot(_StrictModel):
    status: str
    state: Optional[str] = None
    raw_state: Optional[str] = None
    previous_state: Optional[str] = None
    confidence: float = Field(ge=0, le=1)
    transition_risk: Optional[float] = Field(default=None, ge=0, le=1)
    state_instability_score: Optional[float] = Field(default=None, ge=0, le=1)
    entered_at: Optional[AwareDatetime] = None
    days_in_state: int = Field(default=0, ge=0)
    pending_state: Optional[str] = None
    pending_days: int = Field(default=0, ge=0)
    as_of: AwareDatetime
    input_coverage: dict[str, Any] = Field(default_factory=dict)
    hard_missing: list[str] = Field(default_factory=list)
    optional_missing: list[str] = Field(default_factory=list)
    active_groups: list[str] = Field(default_factory=list)
    degraded_reasons: list[str] = Field(default_factory=list)
    rules: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    version: str
