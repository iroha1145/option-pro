from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal, Optional

from pydantic import (
    AfterValidator,
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
AIJobType = Literal[
    "earnings_impact",
    "option_alerts",
    "signal_analysis",
    "news_impact",
    "market_focus",
]
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


# This is intentionally a deterministic gate, not a language guesser. It
# catches common Traditional Chinese output and prose that is wholly English,
# while still allowing tickers and foreign proper names inside Chinese text.
_TRADITIONAL_ONLY = frozenset(
    "國體門學會發現後裡這個為與從將時點對於還來說們種過經產業機構"
    "標題聞報導總結風險關係響應該買賣價倉損獲臺萬億區網絡據礎緩趨"
    "勢優壓調查變動預測資訊財務貨幣聯儲聲稱達較啟動釋義參與並專業"
    "實確層級類別開閉觀強週數術語態處輸備註認證權錯誤歷紀錄單雙長"
    "線選擇債證監則總廣穩顯導衝擊隱憂競爭併購營運減擴張訊號圖錶檔"
    "雲軟記憶頁鏈轉換維護"
)
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z'-]*")
_TICKER_OR_CODE_WORD = re.compile(r"(?:[A-Z]{1,12}|[A-Za-z]*\d[A-Za-z0-9-]*)")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def validate_simplified_chinese_text(value: str) -> str:
    """Reject non-Chinese prose and common Traditional Chinese deterministically."""

    text = value.strip()
    if not text:
        raise ValueError("simplified_chinese_text_required")
    traditional = sorted({char for char in text if char in _TRADITIONAL_ONLY})
    if traditional:
        raise ValueError("traditional_chinese_not_allowed")
    cjk_count = sum(1 for char in text if _is_cjk(char))
    if cjk_count == 0:
        raise ValueError("simplified_chinese_text_required")
    latin_count = sum(1 for char in text if char.isascii() and char.isalpha())
    if latin_count > max(32, cjk_count * 4):
        raise ValueError("english_prose_not_allowed")
    for sentence in _SENTENCE_SPLIT.split(text):
        latin_words = _LATIN_WORD.findall(sentence)
        prose_words = [
            word
            for word in latin_words
            if _TICKER_OR_CODE_WORD.fullmatch(word) is None
        ]
        # One foreign product or company name can be necessary inside Chinese
        # prose. Two or more ordinary Latin words are treated as an English
        # fragment, even when a few Chinese characters were appended to it.
        if len(prose_words) >= 2:
            raise ValueError("english_prose_not_allowed")
        sentence_latin = sum(
            1 for char in sentence if char.isascii() and char.isalpha()
        )
        sentence_cjk = sum(1 for char in sentence if _is_cjk(char))
        if sentence_latin >= 16 and sentence_cjk == 0:
            raise ValueError("english_prose_not_allowed")
    return text


def _aware_utc_instant(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp_must_be_iso8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp_timezone_required")
    return parsed.astimezone(timezone.utc)


ZhShortText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(validate_simplified_chinese_text),
]
ZhBoundedText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    AfterValidator(validate_simplified_chinese_text),
]
ZhLongText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=3000),
    AfterValidator(validate_simplified_chinese_text),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )


class SimplifiedChineseResult(StrictModel):
    output_language: Literal["zh-CN"]


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
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        AfterValidator(validate_simplified_chinese_text),
    ]

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class EarningsImpactResult(SimplifiedChineseResult):
    ticker: Ticker
    summary: ZhShortText
    expectation: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        AfterValidator(validate_simplified_chinese_text),
    ]
    impacted: list[EarningsImpactItem] = Field(min_length=4, max_length=8)

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class OptionAlertResult(SimplifiedChineseResult):
    confidence: Literal["high", "medium", "low"]
    direction: Literal["bullish", "bearish", "mixed", "unknown"]
    direction_status: Literal["available", "unavailable_without_trade_side"]
    summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        AfterValidator(validate_simplified_chinese_text),
    ]
    analysis: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
        AfterValidator(validate_simplified_chinese_text),
    ]
    key_strikes: list[ZhShortText] = Field(max_length=3)
    risk_note: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        AfterValidator(validate_simplified_chinese_text),
    ]


EvidenceText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(validate_simplified_chinese_text),
]


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


class SignalAnalysisResult(SimplifiedChineseResult):
    asset: Ticker
    horizon: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=80),
        AfterValidator(validate_simplified_chinese_text),
    ]
    dominant_regime: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=120),
        AfterValidator(validate_simplified_chinese_text),
    ]
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
    summary: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=1200),
        AfterValidator(validate_simplified_chinese_text),
    ]

    @field_validator("asset")
    @classmethod
    def normalize_asset(cls, value: str) -> str:
        return value.upper()


class NewsStockImpact(StrictModel):
    ticker: Ticker
    company: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    impact_score: StrictInt = Field(ge=-100, le=100)
    confidence: StrictInt = Field(ge=0, le=100)
    horizon: Literal["intraday", "days", "weeks", "uncertain"]
    mechanism: Literal[
        "direct_company",
        "supplier_customer",
        "sector_readthrough",
        "macro_rate",
        "commodity_input",
        "regulatory",
        "competitive",
        "other",
    ]
    reason: ZhBoundedText

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


class NewsCommodityImpact(StrictModel):
    name: ZhShortText
    impact_score: StrictInt = Field(ge=-100, le=100)
    reason: ZhBoundedText


class NewsImpactResult(SimplifiedChineseResult):
    news_id: StrictInt = Field(ge=1)
    change_sequence: StrictInt = Field(ge=1)
    content_hash: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
    ]
    title_zh: ZhShortText
    summary_zh: ZhBoundedText
    headline_summary: ZhBoundedText
    overall_sentiment: StrictInt = Field(ge=-100, le=100)
    classification: Literal["bullish", "bearish", "neutral"]
    confidence: StrictInt = Field(ge=0, le=100)
    market_relevance: StrictInt = Field(ge=0, le=100)
    affected_stocks: list[NewsStockImpact] = Field(max_length=50)
    affected_sectors: list[ZhShortText] = Field(max_length=50)
    affected_commodities: list[NewsCommodityImpact] = Field(max_length=30)
    causal_summary: ZhBoundedText
    key_factors: list[ZhShortText] = Field(max_length=30)
    uncertainty_notes: list[ZhShortText] = Field(max_length=30)
    insufficient_context: StrictBool

    @field_validator("affected_stocks")
    @classmethod
    def unique_stock_tickers(cls, values: list[NewsStockImpact]) -> list[NewsStockImpact]:
        tickers = [item.ticker for item in values]
        if len(tickers) != len(set(tickers)):
            raise ValueError("affected_stocks_contains_duplicate_ticker")
        return values


class MarketFocusTickerAssessment(StrictModel):
    ticker: Ticker
    catalyst_bias: Optional[StrictInt] = Field(ge=-100, le=100)
    confidence: StrictInt = Field(ge=0, le=100)
    horizon: Literal["intraday", "days", "weeks", "uncertain"]
    supporting_event_ids: list[
        Annotated[str, StringConstraints(min_length=1, max_length=100)]
    ] = Field(max_length=8)
    conflicting_event_ids: list[
        Annotated[str, StringConstraints(min_length=1, max_length=100)]
    ] = Field(max_length=8)
    summary: ZhBoundedText
    risks: list[ZhShortText] = Field(max_length=8)
    insufficient_evidence: StrictBool

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()

    @model_validator(mode="after")
    def evidence_semantics(self) -> "MarketFocusTickerAssessment":
        if self.insufficient_evidence and self.catalyst_bias is not None:
            raise ValueError("insufficient_evidence_requires_null_bias")
        if not self.insufficient_evidence and self.catalyst_bias is None:
            raise ValueError("supported_assessment_requires_bias")
        if set(self.supporting_event_ids) & set(self.conflicting_event_ids):
            raise ValueError("supporting_and_conflicting_evidence_overlap")
        if len(self.supporting_event_ids) != len(set(self.supporting_event_ids)):
            raise ValueError("supporting_event_ids_contains_duplicate")
        if len(self.conflicting_event_ids) != len(set(self.conflicting_event_ids)):
            raise ValueError("conflicting_event_ids_contains_duplicate")
        return self


class MarketFocusDominantEvent(StrictModel):
    event_group_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    summary: ZhBoundedText
    affected_sectors: list[ZhShortText] = Field(max_length=10)


class MarketFocusResult(SimplifiedChineseResult):
    cycle_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    as_of: Annotated[str, StringConstraints(min_length=1, max_length=40)]
    input_hash: Annotated[
        str,
        StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    ]
    title_zh: ZhShortText
    summary_zh: ZhLongText
    headline_summary: ZhLongText
    market_summary: ZhLongText
    dominant_events: list[MarketFocusDominantEvent] = Field(max_length=8)
    market_uncertainties: list[ZhShortText] = Field(max_length=20)
    affected_sectors: list[ZhShortText] = Field(max_length=20)
    focus_ticker_assessments: list[MarketFocusTickerAssessment] = Field(max_length=20)
    no_new_material_catalyst: StrictBool
    insufficient_context: StrictBool

    @field_validator("as_of")
    @classmethod
    def require_aware_as_of(cls, value: str) -> str:
        _aware_utc_instant(value)
        return value

    @field_validator("focus_ticker_assessments")
    @classmethod
    def unique_tickers(
        cls,
        values: list[MarketFocusTickerAssessment],
    ) -> list[MarketFocusTickerAssessment]:
        tickers = [item.ticker for item in values]
        if len(tickers) != len(set(tickers)):
            raise ValueError("focus_ticker_assessments_contains_duplicate_ticker")
        return values

    @model_validator(mode="after")
    def honest_empty_cycle(self) -> "MarketFocusResult":
        if self.no_new_material_catalyst and self.dominant_events:
            raise ValueError("empty_cycle_cannot_claim_dominant_events")
        event_ids = [item.event_group_id for item in self.dominant_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("dominant_events_contains_duplicate")
        return self


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
    cancel_requested: StrictBool = False
    analysis_revision: Optional[StrictInt] = Field(default=None, ge=1)
    cycle_revision: Optional[StrictInt] = Field(default=None, ge=1)
    budget_charge_usd: float = Field(default=0.0, ge=0)
    usage: dict[str, Optional[StrictInt]] = Field(default_factory=dict)


class CancelRequest(StrictModel):
    confirm: StrictBool = True


def result_model_for(job_type: str) -> type[BaseModel]:
    if job_type == "earnings_impact":
        return EarningsImpactResult
    if job_type == "option_alerts":
        return OptionAlertResult
    if job_type == "signal_analysis":
        return SignalAnalysisResult
    if job_type == "news_impact":
        return NewsImpactResult
    if job_type == "market_focus":
        return MarketFocusResult
    raise ValueError("unsupported_job_type")


def _require_identity_integer(payload: dict, field: str) -> int:
    value = payload.get(field)
    if type(value) is not int or value < 1:
        raise ValueError(f"{field}_invalid")
    return value


def _require_identity_text(
    payload: dict,
    field: str,
    *,
    max_length: int,
) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"{field}_invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"{field}_invalid")
    return normalized


def _require_unique_string_list(
    payload: dict,
    field: str,
    *,
    max_items: int,
    max_length: int,
) -> set[str]:
    values = payload.get(field)
    if not isinstance(values, list) or len(values) > max_items:
        raise ValueError(f"{field}_invalid")
    normalized: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{field}_invalid")
        item = value.strip()
        if not item or len(item) > max_length:
            raise ValueError(f"{field}_invalid")
        normalized.append(item)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field}_contains_duplicate")
    return set(normalized)


def validate_job_payload(job_type: str, payload: dict) -> None:
    """Validate identities needed to bind paid output to its local snapshot."""

    if job_type == "news_impact":
        _require_identity_integer(payload, "news_id")
        _require_identity_integer(payload, "change_sequence")
        _require_identity_text(payload, "content_hash", max_length=256)
        tickers = _require_unique_string_list(
            payload,
            "allowed_tickers",
            max_items=200,
            max_length=12,
        )
        if any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-^]*", ticker) is None
            for ticker in tickers
        ):
            raise ValueError("allowed_tickers_invalid")
        return
    if job_type == "market_focus":
        _require_identity_text(payload, "cycle_id", max_length=100)
        as_of = _require_identity_text(payload, "as_of", max_length=40)
        _aware_utc_instant(as_of)
        input_hash = _require_identity_text(payload, "input_hash", max_length=64)
        if _SHA256.fullmatch(input_hash) is None:
            raise ValueError("input_hash_invalid")
        _require_unique_string_list(
            payload,
            "allowed_event_group_ids",
            max_items=200,
            max_length=100,
        )
        tickers = _require_unique_string_list(
            payload,
            "allowed_tickers",
            max_items=200,
            max_length=12,
        )
        if any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9.\-^]*", ticker) is None
            for ticker in tickers
        ):
            raise ValueError("allowed_tickers_invalid")
        return
    if job_type not in {"earnings_impact", "option_alerts", "signal_analysis"}:
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
    elif job_type == "news_impact":
        validate_job_payload(job_type, payload)
        if (
            data["news_id"] != payload["news_id"]
            or data["change_sequence"] != payload["change_sequence"]
            or data["content_hash"] != str(payload["content_hash"]).strip()
        ):
            raise ValueError("news_identity_mismatch")
        allowed_tickers = {
            str(ticker).strip().upper() for ticker in payload["allowed_tickers"]
        }
        output_tickers = {item["ticker"] for item in data["affected_stocks"]}
        if not output_tickers <= allowed_tickers:
            raise ValueError("news_ticker_binding_mismatch")
    elif job_type == "market_focus":
        validate_job_payload(job_type, payload)
        expected = str(payload["cycle_id"]).strip()
        if data["cycle_id"] != expected:
            raise ValueError("market_focus_cycle_mismatch")
        expected_as_of = str(payload["as_of"]).strip()
        if _aware_utc_instant(data["as_of"]) != _aware_utc_instant(expected_as_of):
            raise ValueError("market_focus_as_of_mismatch")
        if data["input_hash"] != str(payload["input_hash"]).strip():
            raise ValueError("market_focus_input_hash_mismatch")
        allowed_event_ids = {
            str(event_id).strip()
            for event_id in payload["allowed_event_group_ids"]
        }
        output_event_ids = {
            item["event_group_id"] for item in data["dominant_events"]
        }
        for assessment in data["focus_ticker_assessments"]:
            output_event_ids.update(assessment["supporting_event_ids"])
            output_event_ids.update(assessment["conflicting_event_ids"])
        if not output_event_ids <= allowed_event_ids:
            raise ValueError("market_focus_event_binding_mismatch")
        allowed_tickers = {
            str(ticker).strip().upper() for ticker in payload["allowed_tickers"]
        }
        output_tickers = {
            assessment["ticker"] for assessment in data["focus_ticker_assessments"]
        }
        if not output_tickers <= allowed_tickers:
            raise ValueError("market_focus_ticker_binding_mismatch")
    return data
