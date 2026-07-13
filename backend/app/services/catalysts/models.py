from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Annotated, Literal, Optional
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


SCHEMA_VERSION = "macrolens-option-pro-v1"
TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.^/_-]{0,19}$")
ContractTicker = Annotated[
    str,
    Field(min_length=1, max_length=20, pattern=TICKER_PATTERN.pattern),
]
AnalysisListItem = Annotated[str, Field(min_length=1, max_length=500)]
SourceName = Annotated[str, Field(min_length=1, max_length=100)]
HealthWarning = Annotated[str, Field(min_length=1, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PublicState(str, Enum):
    ACTIVE = "active"
    DEGRADED = "degraded"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"
    EMPTY = "empty"


class JobStatus(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    BUDGET_BLOCKED = "budget_blocked"


ACTIVE_JOB_STATUSES = {
    JobStatus.PENDING,
    JobStatus.QUEUED,
    JobStatus.IN_PROGRESS,
}
TERMINAL_JOB_STATUSES = set(JobStatus) - ACTIVE_JOB_STATUSES


class Sentiment(str, Enum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


# Kept as a source-compatible name for the local cache fixtures and callers.
Classification = Sentiment


class ImpactHorizon(str, Enum):
    INTRADAY = "intraday"
    DAYS = "days"
    WEEKS = "weeks"
    UNCERTAIN = "uncertain"


class ImpactMechanism(str, Enum):
    DIRECT_COMPANY = "direct_company"
    SUPPLIER_CUSTOMER = "supplier_customer"
    SECTOR_READTHROUGH = "sector_readthrough"
    MACRO_RATE = "macro_rate"
    COMMODITY_INPUT = "commodity_input"
    REGULATORY = "regulatory"
    COMPETITIVE = "competitive"
    OTHER = "other"


class AnalysisStatus(str, Enum):
    NOT_REQUESTED = "not_requested"
    PENDING = "pending"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INSUFFICIENT_CONTEXT = "insufficient_context"
    BUDGET_BLOCKED = "budget_blocked"


class AffectedStockImpact(StrictModel):
    ticker: ContractTicker
    company: str = Field(min_length=1, max_length=200)
    impact_score: int = Field(ge=-100, le=100)
    confidence: int = Field(ge=0, le=100)
    horizon: ImpactHorizon
    mechanism: ImpactMechanism
    reason: str = Field(min_length=1, max_length=2000)

    @field_validator("ticker", mode="before")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not re.fullmatch(r"^[A-Z0-9][A-Z0-9.^/_-]{0,19}$", normalized):
            raise ValueError("invalid ticker")
        return normalized


class AffectedCommodityImpact(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    impact_score: int = Field(ge=-100, le=100)
    reason: str = Field(min_length=1, max_length=2000)


class NewsImpactAnalysis(StrictModel):
    title_zh: str = Field(min_length=1, max_length=500)
    headline_summary: str = Field(min_length=1, max_length=2000)
    overall_sentiment: int = Field(ge=-100, le=100)
    classification: Sentiment
    confidence: int = Field(ge=0, le=100)
    market_relevance: int = Field(ge=0, le=100)
    affected_stocks: list[AffectedStockImpact] = Field(default_factory=list, max_length=50)
    affected_sectors: list[AnalysisListItem] = Field(default_factory=list, max_length=50)
    affected_commodities: list[AffectedCommodityImpact] = Field(default_factory=list, max_length=30)
    causal_summary: str = Field(min_length=1, max_length=2000)
    key_factors: list[AnalysisListItem] = Field(default_factory=list, max_length=30)
    uncertainty_notes: list[AnalysisListItem] = Field(default_factory=list, max_length=30)
    insufficient_context: bool

    @field_validator("affected_stocks")
    @classmethod
    def reject_duplicate_stock_impacts(
        cls, values: list[AffectedStockImpact]
    ) -> list[AffectedStockImpact]:
        tickers = [value.ticker for value in values]
        if len(tickers) != len(set(tickers)):
            raise ValueError("affected_stocks contains a duplicate ticker")
        return values

    @field_validator("affected_sectors", "key_factors", "uncertainty_notes")
    @classmethod
    def bound_array_strings(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 500 for item in value):
            raise ValueError("analysis list item is too long")
        return value


class RemoteAnalysis(NewsImpactAnalysis):
    analysis_id: int = Field(ge=1)
    revision: int = Field(ge=1)
    model: str = Field(min_length=1, max_length=200)
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"]
    prompt_version: str = Field(min_length=1, max_length=100)
    schema_version: str = Field(min_length=1, max_length=100)
    analyzed_at: AwareDatetime
    available_at: AwareDatetime


class ContractEnvelope(StrictModel):
    schema_version: Literal[SCHEMA_VERSION] = SCHEMA_VERSION
    schema_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_id: str = Field(min_length=8, max_length=100)


class CatalystItem(StrictModel):
    news_id: int = Field(ge=1)
    content_hash: str = Field(min_length=8, max_length=128)
    source: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=2000)
    summary: Optional[str] = Field(default=None, max_length=20_000)
    url: str = Field(min_length=1, max_length=4096)
    published_at: Optional[AwareDatetime] = None
    fetched_at: AwareDatetime
    updated_at: AwareDatetime
    change_sequence: int = Field(ge=1)
    source_tickers: list[str] = Field(default_factory=list, max_length=100)
    analysis_status: AnalysisStatus
    analysis: Optional[RemoteAnalysis] = None
    analyzed_at: Optional[AwareDatetime] = None
    available_at: Optional[AwareDatetime] = None
    is_stale: bool = False

    @field_validator("source_tickers")
    @classmethod
    def normalize_source_tickers(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            ticker = value.strip().upper()
            if not TICKER_PATTERN.fullmatch(ticker):
                raise ValueError("invalid source ticker")
            if ticker not in seen:
                seen.add(ticker)
                output.append(ticker)
        return output

    @field_validator("url")
    @classmethod
    def validate_news_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or any(character.isspace() for character in value)
        ):
            raise ValueError("news URL must use http or https")
        return value

    @model_validator(mode="after")
    def validate_point_in_time_fields(self) -> "CatalystItem":
        if self.analysis is None:
            if self.analyzed_at is not None or self.available_at is not None:
                raise ValueError("analysis timestamps require an analysis")
            return self
        if self.analyzed_at is None or self.available_at is None:
            raise ValueError("completed analysis requires analyzed_at and available_at")
        expected = max(self.fetched_at, self.analyzed_at)
        if self.available_at != expected:
            raise ValueError("available_at must equal max(fetched_at, analyzed_at)")
        if self.analysis.analyzed_at != self.analyzed_at or self.analysis.available_at != self.available_at:
            raise ValueError("item and analysis timestamps must agree")
        return self


class FeedResponse(ContractEnvelope):
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    items: list[CatalystItem] = Field(default_factory=list, max_length=1000)
    next_cursor: Optional[str] = Field(default=None, max_length=4096)
    has_more: bool


class TickerResponse(ContractEnvelope):
    ticker: str = Field(min_length=1, max_length=20)
    status: Literal["active", "empty", "stale", "unavailable"]
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    items: list[CatalystItem] = Field(default_factory=list, max_length=1000)
    next_cursor: Optional[str] = Field(default=None, max_length=4096)
    has_more: bool


class LatestResponse(ContractEnvelope):
    snapshot_token: str = Field(min_length=8, max_length=200)
    data_through: Optional[AwareDatetime] = None
    next_updated_after: Optional[AwareDatetime] = None
    next_cursor: Optional[str] = Field(default=None, max_length=4096)
    has_more: bool
    items: list[CatalystItem] = Field(default_factory=list, max_length=1000)


class CalendarEvent(StrictModel):
    event_id: str = Field(min_length=8, max_length=128)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    title: str = Field(min_length=1, max_length=2000)
    impact: Literal["low", "medium", "high", "holiday"]
    scheduled_at: AwareDatetime
    forecast: Optional[str] = Field(default=None, max_length=500)
    previous: Optional[str] = Field(default=None, max_length=500)
    actual: Optional[str] = Field(default=None, max_length=500)
    is_stale: bool = False
    source_fetched_at: AwareDatetime
    available_at: AwareDatetime

class CalendarResponse(ContractEnvelope):
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    items: list[CalendarEvent] = Field(default_factory=list, max_length=5000)


class ComponentHealth(StrictModel):
    status: Literal["ok", "degraded", "unavailable", "not_configured", "disabled"]
    last_attempt_at: Optional[AwareDatetime] = None
    last_success_at: Optional[AwareDatetime] = None
    data_through: Optional[AwareDatetime] = None
    consecutive_failures: int = Field(default=0, ge=0)
    next_attempt_at: Optional[AwareDatetime] = None
    raw_count: Optional[int] = Field(default=None, ge=0)
    inserted_count: Optional[int] = Field(default=None, ge=0)
    duplicates_count: Optional[int] = Field(default=None, ge=0)
    detail: Optional[str] = Field(default=None, max_length=500)


class QueueHealth(StrictModel):
    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    pending: int = Field(ge=0)
    queued: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    oldest_job_at: Optional[AwareDatetime] = None
    budget_status: Literal["ok", "budget_configuration_required", "budget_blocked"]


class HealthResponse(ContractEnvelope):
    model_config = ConfigDict(title="IntegrationHealthResponse")

    status: Literal["ok", "degraded", "unavailable", "not_configured"]
    as_of: AwareDatetime
    data_through: Optional[AwareDatetime] = None
    database: ComponentHealth
    scheduler: ComponentHealth
    analysis_queue: QueueHealth
    model: str = Field(min_length=1, max_length=200)
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"]
    execution_mode: Literal["background", "worker_sync"]
    analysis_trigger_enabled: bool
    sources: dict[SourceName, ComponentHealth]
    warnings: list[HealthWarning] = Field(max_length=50)


class RemoteJobResponse(ContractEnvelope):
    job_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    news_id: int = Field(ge=1)
    content_hash: str = Field(min_length=8, max_length=128)
    input_hash: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    change_sequence: Optional[int] = Field(default=None, ge=1)
    status: JobStatus
    model: str = Field(min_length=1, max_length=200)
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"]
    submitted_at: Optional[AwareDatetime] = None
    updated_at: AwareDatetime
    completed_at: Optional[AwareDatetime] = None
    error_code: Optional[str] = Field(default=None, max_length=100)
    retry_after: Optional[int] = Field(default=None, ge=0, le=86_400)
    result: Optional[RemoteAnalysis] = None


class CreateAnalysisJobRequest(StrictModel):
    news_id: int = Field(ge=1)
    expected_content_hash: str = Field(min_length=8, max_length=128)
    expected_change_sequence: Optional[int] = Field(default=None, ge=1)
    force: bool = False


class CatalystBatchRequest(StrictModel):
    tickers: list[ContractTicker] = Field(min_length=1, max_length=50)
    as_of: Optional[AwareDatetime] = None
    window_hours: int = Field(default=72, ge=1, le=720)
    limit: int = Field(default=20, ge=1, le=100)
    min_confidence: int = Field(default=0, ge=0, le=100)
    include_neutral: bool = False
    include_unanalyzed: bool = True

    @field_validator("tickers", mode="before")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            ticker = value.strip().upper()
            if not TICKER_PATTERN.fullmatch(ticker):
                raise ValueError(f"invalid ticker: {value!r}")
            if ticker not in seen:
                seen.add(ticker)
                output.append(ticker)
        if not output:
            raise ValueError("at least one ticker is required")
        return output


class HotspotStatusResponse(ContractEnvelope):
    prepared_revision: int = Field(ge=0)
    last_consumed_revision: int = Field(ge=0)
    prepared_hot_count: int = Field(ge=0)
    prepared_since: Optional[AwareDatetime] = None
    last_cycle_at: Optional[AwareDatetime] = None
    next_scheduled_at: Optional[AwareDatetime] = None
    active_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[a-f0-9]{32}$"
    )
    cooldown_until: Optional[AwareDatetime] = None
    manual_enabled: bool
    capability: Literal[
        "enabled",
        "disabled",
        "budget_configuration_required",
    ]
    model: str = Field(min_length=1, max_length=200)
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"]
    data_through: Optional[AwareDatetime] = None


class HotspotPreparationItem(StrictModel):
    prepared_revision: int = Field(ge=1)
    event_group_id: str = Field(min_length=1, max_length=100)
    event_group_version: int = Field(ge=1)
    gate_version: str = Field(min_length=1, max_length=100)
    hot_score: float = Field(ge=0, le=100)
    # Missing evidence is represented as null and excluded from MacroLens'
    # deterministic weight re-normalisation.  In particular,
    # market_confirmation is legitimately null when the focus snapshot is not
    # current; coercing it to a neutral score would change the gate semantics.
    component_scores: dict[str, Optional[float]]
    active_weights: dict[str, float]
    reasons: list[AnalysisListItem] = Field(default_factory=list, max_length=30)
    event_snapshot_json: str = Field(min_length=2, max_length=100_000)
    status: Literal["PREPARED", "LEASED", "CONSUMED"]
    prepared_at: AwareDatetime
    leased_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[a-f0-9]{32}$"
    )
    consumed_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[a-f0-9]{32}$"
    )
    consumed_at: Optional[AwareDatetime] = None
    created_at: AwareDatetime
    representative_title: str = Field(min_length=1, max_length=2000)
    event_type: str = Field(min_length=1, max_length=100)
    available_at: AwareDatetime
    first_published_at: Optional[AwareDatetime] = None
    last_published_at: Optional[AwareDatetime] = None
    source_count: int = Field(ge=1)
    source_names: list[AnalysisListItem] = Field(default_factory=list, max_length=100)
    validated_tickers: list[ContractTicker] = Field(default_factory=list, max_length=100)


class HotspotListResponse(ContractEnvelope):
    as_of: AwareDatetime
    items: list[HotspotPreparationItem] = Field(default_factory=list, max_length=100)


class PublicFocusTickerAssessment(StrictModel):
    ticker: Annotated[str, Field(pattern=TICKER_PATTERN.pattern)]
    catalyst_bias: Optional[int] = Field(default=None, ge=-100, le=100)
    confidence: int = Field(ge=0, le=100)
    horizon: Literal["intraday", "days", "weeks", "uncertain"]
    supporting_event_ids: list[str] = Field(default_factory=list, max_length=8)
    conflicting_event_ids: list[str] = Field(default_factory=list, max_length=8)
    summary: str = Field(min_length=1, max_length=1000)
    risks: list[str] = Field(default_factory=list, max_length=8)
    insufficient_evidence: bool
    weighted_catalyst_context: Optional[float] = Field(
        default=None, ge=-100, le=100
    )

    @model_validator(mode="after")
    def validate_evidence_semantics(self) -> "PublicFocusTickerAssessment":
        if self.insufficient_evidence != (self.catalyst_bias is None):
            raise ValueError("catalyst_bias must be null exactly when evidence is insufficient")
        if self.insufficient_evidence and self.weighted_catalyst_context is not None:
            raise ValueError(
                "weighted_catalyst_context must be null when evidence is insufficient"
            )
        return self


class DominantEvent(StrictModel):
    event_group_id: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=1000)
    affected_sectors: list[str] = Field(default_factory=list, max_length=10)


class MarketFocusCyclePublicAnalysis(StrictModel):
    cycle_id: str = Field(min_length=1, max_length=100)
    as_of: AwareDatetime
    market_summary: str = Field(min_length=1, max_length=3000)
    dominant_events: list[DominantEvent] = Field(default_factory=list, max_length=8)
    market_uncertainties: list[str] = Field(default_factory=list, max_length=20)
    affected_sectors: list[str] = Field(default_factory=list, max_length=20)
    focus_ticker_assessments: list[PublicFocusTickerAssessment] = Field(
        default_factory=list, max_length=20
    )
    no_new_material_catalyst: bool
    insufficient_context: bool
    display_only: Literal[True] = True


class MarketFocusCyclePublic(StrictModel):
    cycle_id: str = Field(pattern=r"^mfc_[a-f0-9]{32}$")
    scheduled_slot: Optional[str] = Field(default=None, max_length=100)
    idempotency_key: str = Field(min_length=1, max_length=200)
    retry_of_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[a-f0-9]{32}$"
    )
    execution_number: int = Field(ge=1)
    trigger_type: Literal[
        "manual",
        "scheduled_0800",
        "scheduled_1200",
        "scheduled_1600",
        "scheduled_2000",
    ]
    status: Literal[
        "pending",
        "queued",
        "in_progress",
        "completed",
        "failed",
        "cancelled",
        "budget_blocked",
        "incomplete_output",
        "insufficient_context",
    ]
    no_new_hot_events: bool
    prepared_revision: int = Field(ge=0)
    last_consumed_revision_at_start: int = Field(ge=0)
    consumes_through_revision: Optional[int] = Field(default=None, ge=1)
    focus_revision: Optional[int] = Field(default=None, ge=1)
    snapshot_as_of: AwareDatetime
    input_schema_version: str = Field(min_length=1, max_length=100)
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    event_group_count: int = Field(ge=0)
    focus_symbol_count: int = Field(ge=0)
    provider: str = Field(min_length=1, max_length=100)
    model: str = Field(min_length=1, max_length=200)
    reasoning_effort: Literal["none", "low", "medium", "high", "xhigh", "max"]
    execution_mode: Literal["background", "worker_sync"]
    max_output_tokens: int = Field(ge=256, le=128_000)
    prompt_version: str = Field(min_length=1, max_length=100)
    output_schema_version: str = Field(min_length=1, max_length=100)
    result: Optional[MarketFocusCyclePublicAnalysis] = None
    error_code: Optional[str] = Field(default=None, max_length=100)
    attempt_count: int = Field(ge=0)
    retrieve_error_count: int = Field(ge=0)
    cancel_attempt_count: int = Field(ge=0)
    next_attempt_at: Optional[AwareDatetime] = None
    cancel_requested_at: Optional[AwareDatetime] = None
    latency_ms: Optional[int] = Field(default=None, ge=0)
    usage_input_tokens: int = Field(ge=0)
    usage_cached_input_tokens: int = Field(ge=0)
    usage_cache_write_tokens: int = Field(ge=0)
    usage_reasoning_tokens: int = Field(ge=0)
    usage_output_tokens: int = Field(ge=0)
    usage_total_tokens: int = Field(ge=0)
    created_at: AwareDatetime
    started_at: Optional[AwareDatetime] = None
    completed_at: Optional[AwareDatetime] = None
    updated_at: AwareDatetime


class MarketFocusCycleResponse(ContractEnvelope):
    cycle: Optional[MarketFocusCyclePublic] = None


class MarketFocusCycleCreateRequest(StrictModel):
    trigger: Literal[
        "manual",
        "scheduled_0800",
        "scheduled_1200",
        "scheduled_1600",
        "scheduled_2000",
    ] = "manual"
    expected_prepared_revision: Optional[int] = Field(default=None, ge=0)
    retry_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[a-f0-9]{32}$"
    )


# Compatibility names retained for the existing repository, worker and tests.
# The concrete classes above deliberately use the public contract names so
# nested JSON Schema titles and $defs remain byte-for-byte comparable.
HotspotPreparationStatus = HotspotStatusResponse
HotspotPreparationResponse = HotspotListResponse
FocusTickerAssessment = PublicFocusTickerAssessment
MarketFocusCycleAnalysis = MarketFocusCyclePublicAnalysis
RemoteMarketFocusCycle = MarketFocusCyclePublic
MarketFocusCycleEnvelope = MarketFocusCycleResponse
CreateMarketFocusCycleRequest = MarketFocusCycleCreateRequest


class RemoteErrorBody(ContractEnvelope):
    code: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool
    retry_after_seconds: Optional[int] = Field(default=None, ge=0, le=86_400)


def utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.isoformat().replace("+00:00", "Z")
