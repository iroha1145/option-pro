from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)


AIJobStatus = Literal[
    "pending",
    "queued",
    "in_progress",
    "completed",
    "failed",
    "cancelled",
    "insufficient_context",
    "budget_blocked",
]
AIJobType = Literal["earnings_impact", "option_alerts", "signal_analysis"]
Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=12,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-^]*$",
    ),
]
BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class EarningsImpactJobRequest(StrictModel):
    ticker: Ticker
    force: StrictBool = False
    name: BoundedText = ""
    sector: BoundedText = ""
    earnings_date: Annotated[str, StringConstraints(max_length=10)] = ""
    eps_estimate: Optional[float] = Field(
        default=None,
        ge=-1_000_000,
        le=1_000_000,
    )
    revenue_estimate: Optional[float] = Field(default=None, ge=0, le=1e16)
    market_cap: Optional[float] = Field(default=None, ge=0, le=1e16)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class OptionAlertJobRequest(StrictModel):
    ticker: Ticker
    alerts: list[dict] = Field(default_factory=list, max_length=10)
    underlying_price: float = Field(default=0, ge=0, le=10_000_000)
    expiration: Annotated[str, StringConstraints(max_length=10)] = ""

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class SignalAnalysisJobRequest(StrictModel):
    ticker: Ticker
    signals: dict[str, Any]
    scores: dict[str, Any]
    as_of: Annotated[str, StringConstraints(min_length=1, max_length=40)]

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class EarningsImpactItem(StrictModel):
    ticker: Ticker
    name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    relation: Literal["competitor", "supplier", "customer", "etf", "opposing"]
    direction: Literal["bullish", "bearish", "mixed"]
    reason: Annotated[str, StringConstraints(min_length=1, max_length=300)]

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class EarningsImpactResult(StrictModel):
    ticker: Ticker
    summary: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    expectation: Annotated[str, StringConstraints(max_length=300)]
    impacted: list[EarningsImpactItem] = Field(min_length=4, max_length=8)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class OptionAlertResult(StrictModel):
    confidence: Literal["high", "medium", "low"]
    direction: Literal["bullish", "bearish", "mixed", "unknown"]
    direction_status: Literal["available", "unavailable_without_trade_side"]
    summary: Annotated[str, StringConstraints(min_length=1, max_length=300)]
    analysis: Annotated[str, StringConstraints(min_length=1, max_length=1200)]
    key_strikes: list[Annotated[str, StringConstraints(max_length=80)]] = Field(
        max_length=3,
    )
    risk_note: Annotated[str, StringConstraints(max_length=300)]


EvidenceText = Annotated[str, StringConstraints(min_length=1, max_length=500)]


class OptionsFlowRead(StrictModel):
    net_direction: Literal["bullish", "bearish", "mixed", "unknown"]
    confidence: StrictInt = Field(ge=0, le=100)
    bullish_flow_evidence: list[EvidenceText] = Field(max_length=10)
    bearish_flow_evidence: list[EvidenceText] = Field(max_length=10)
    unknown_or_neutral_flow: list[EvidenceText] = Field(max_length=10)
    warnings: list[EvidenceText] = Field(max_length=10)


class SignalKeyLevels(StrictModel):
    support: list[EvidenceText] = Field(max_length=10)
    resistance: list[EvidenceText] = Field(max_length=10)
    vwap_levels: list[EvidenceText] = Field(max_length=10)
    options_levels: list[EvidenceText] = Field(max_length=10)


class SignalAnalysisResult(StrictModel):
    asset: Ticker
    horizon: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    dominant_regime: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    trend_bias_confidence: StrictInt = Field(ge=0, le=100)
    top_risk_confidence: StrictInt = Field(ge=0, le=100)
    bottom_opportunity_confidence: StrictInt = Field(ge=0, le=100)
    dip_buy_quality: StrictInt = Field(ge=0, le=100)
    breakdown_risk: StrictInt = Field(ge=0, le=100)
    data_quality: StrictInt = Field(ge=0, le=100)
    final_bias: Literal[
        "bullish_continuation",
        "healthy_rotation",
        "trend_pullback",
        "range_consolidation",
        "tactical_top_risk",
        "dip_buy_setup",
        "capitulation_bottom_setup",
        "bearish_breakdown",
        "insufficient_data",
    ]
    top_evidence: list[EvidenceText] = Field(max_length=12)
    bottom_evidence: list[EvidenceText] = Field(max_length=12)
    dip_buy_evidence: list[EvidenceText] = Field(max_length=12)
    bearish_evidence: list[EvidenceText] = Field(max_length=12)
    contradictions: list[EvidenceText] = Field(max_length=12)
    options_flow_read: OptionsFlowRead
    key_levels: SignalKeyLevels
    confirmation_signals: list[EvidenceText] = Field(max_length=12)
    invalidation_signals: list[EvidenceText] = Field(max_length=12)
    event_risks: list[EvidenceText] = Field(max_length=12)
    data_quality_notes: list[EvidenceText] = Field(max_length=12)
    summary: Annotated[str, StringConstraints(min_length=1, max_length=1200)]

    @field_validator("asset")
    @classmethod
    def normalize_asset(cls, value: str) -> str:
        return value.upper()


class AIJobPublic(StrictModel):
    job_id: Annotated[str, StringConstraints(min_length=10, max_length=80)]
    job_type: AIJobType
    status: AIJobStatus
    model: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"]
    submitted_at: Optional[str] = None
    updated_at: str
    completed_at: Optional[str] = None
    error_code: Optional[
        Annotated[str, StringConstraints(max_length=120)]
    ] = None
    retry_after: Optional[StrictInt] = Field(default=None, ge=0)
    result: Optional[dict] = None
    cached: StrictBool = False
    cancellable: StrictBool = False


class CancelRequest(StrictModel):
    confirm: StrictBool = True


def result_model_for(job_type: str) -> type[BaseModel]:
    if job_type == "earnings_impact":
        return EarningsImpactResult
    if job_type == "option_alerts":
        return OptionAlertResult
    if job_type == "signal_analysis":
        return SignalAnalysisResult
    raise ValueError("unsupported_job_type")


def validate_result(job_type: str, raw_json: str, payload: dict) -> dict:
    model = result_model_for(job_type)
    result = model.model_validate_json(raw_json)
    data = result.model_dump(mode="json")
    if job_type == "earnings_impact":
        expected = str(payload.get("ticker") or "").upper()
        if data["ticker"] != expected:
            raise ValueError("earnings_ticker_mismatch")
        data["impacted"] = [
            item for item in data["impacted"] if item["ticker"] != expected
        ]
        if not 4 <= len(data["impacted"]) <= 8:
            raise ValueError("earnings_impacted_count_invalid")
    elif job_type == "option_alerts":
        has_direction = any(
            str(item.get("direction_status") or "") == "available"
            and str(item.get("direction") or "").lower()
            in {"bullish", "bearish", "mixed"}
            for item in payload.get("alerts") or []
            if isinstance(item, dict)
        )
        if not has_direction:
            data["direction"] = "unknown"
            data["direction_status"] = "unavailable_without_trade_side"
    elif job_type == "signal_analysis":
        expected = str(payload.get("ticker") or "").upper()
        if data["asset"] != expected:
            raise ValueError("signal_ticker_mismatch")
    return data
