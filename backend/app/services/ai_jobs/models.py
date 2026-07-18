from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path
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
_TICKER_PATTERN_TEXT = r"^[A-Za-z0-9][A-Za-z0-9.\-^]*$"
_TICKER_PATTERN = re.compile(_TICKER_PATTERN_TEXT)
Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=12,
        pattern=_TICKER_PATTERN_TEXT,
    ),
]
BoundedText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)]


# This is intentionally a deterministic gate, not a language guesser. Unicode
# supplies the broad character conflicts, while this short supplement covers
# common regional orthography that Unihan does not model as simplification.
_UNIHAN_CONFLICT_PATH = (
    Path(__file__).with_name("data") / "unihan_17_traditional_conflicts.txt"
)
_UNIHAN_CONFLICT_COUNT = 6498
_UNIHAN_CONFLICT_SHA256 = (
    "a158ff7730d734ebfe0f11d3062ac1921ab4831c6adf348943b1001d4642f80f"
)


def _load_unihan_traditional_conflicts() -> frozenset[str]:
    try:
        lines = _UNIHAN_CONFLICT_PATH.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise RuntimeError("unihan_traditional_conflicts_unavailable") from exc
    payload = "".join(
        line.strip() for line in lines if line.strip() and not line.startswith("#")
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    conflicts = frozenset(payload)
    if (
        len(payload) != _UNIHAN_CONFLICT_COUNT
        or len(conflicts) != _UNIHAN_CONFLICT_COUNT
        or digest != _UNIHAN_CONFLICT_SHA256
    ):
        raise RuntimeError("unihan_traditional_conflicts_invalid")
    return conflicts


_COMMON_TRADITIONAL_ORTHOGRAPHY = frozenset(
    "國體門學會發現後裡這個為與從將時點對於還來說們種過經產業機構"
    "標題聞報導總結風險關係響應該買賣價倉損獲臺萬億區網絡據礎緩趨"
    "勢優壓調查變動預測資訊財務貨幣聯儲聲稱達較啟動釋義參與並專業"
    "實確層級類別開閉觀強週數術語態處輸備註認證權錯誤歷紀錄單雙長"
    "線選擇債證監則總廣穩顯導衝擊隱憂競爭併購營運減擴張訊號圖錶檔"
    "雲軟記憶頁鏈轉換維護佔佈週祕"
)
_TRADITIONAL_ONLY = (
    _load_unihan_traditional_conflicts() | _COMMON_TRADITIONAL_ORTHOGRAPHY
) - frozenset("查")
_TRADITIONAL_CONFLICT_PHRASES = frozenset(
    {
        "乾旱",
        "乾涸",
        "乾燥",
        "徵信",
        "徵兆",
        "徵收",
        "徵求",
        "特徵",
        "瞭解",
        "著手",
        "著眼",
        "著重",
        "藉此",
        "藉由",
        "象徵",
    }
)
_SENTENCE_SPLIT = re.compile(r"[。！？!?；;\n]+")
_AMBIGUOUS_FINANCE_CODES = frozenset({"A", "AN", "ON", "NOW"})
_ENGLISH_PROSE_WORDS = frozenset(
    {
        "a",
        "after",
        "an",
        "and",
        "announces",
        "alert",
        "attack",
        "attacks",
        "awards",
        "before",
        "beats",
        "breaking",
        "business",
        "climb",
        "climbs",
        "crash",
        "crashes",
        "crisis",
        "cuts",
        "company",
        "conference",
        "demand",
        "deal",
        "drops",
        "earnings",
        "equities",
        "estimates",
        "expands",
        "expects",
        "fall",
        "falls",
        "fell",
        "for",
        "from",
        "gains",
        "group",
        "growth",
        "guidance",
        "hard",
        "in",
        "jumps",
        "launch",
        "launched",
        "launches",
        "market",
        "markets",
        "meltdown",
        "military",
        "miss",
        "misses",
        "new",
        "now",
        "of",
        "on",
        "order",
        "ordered",
        "orders",
        "outlook",
        "pause",
        "paused",
        "pauses",
        "partnership",
        "plans",
        "president",
        "price",
        "prices",
        "profit",
        "profits",
        "plunges",
        "quantum",
        "raises",
        "raised",
        "rally",
        "rapidly",
        "report",
        "reports",
        "results",
        "retaliates",
        "retreats",
        "revenue",
        "rises",
        "rose",
        "sales",
        "says",
        "sees",
        "sells",
        "shares",
        "stock",
        "stocks",
        "strong",
        "stronger",
        "supply",
        "surges",
        "sink",
        "sinks",
        "slumps",
        "soars",
        "spikes",
        "strikes",
        "systems",
        "tariff",
        "tariffs",
        "the",
        "to",
        "tumble",
        "tumbles",
        "unveils",
        "update",
        "war",
        "warns",
        "fear",
        "loom",
        "looms",
        "with",
    }
)
_FOREIGN_SPAN = re.compile(
    r"[A-Za-z][A-Za-z0-9]*"
    r"(?:(?:[.'/-][A-Za-z0-9]+)"
    r"|(?:[ \t]+[A-Za-z0-9][A-Za-z0-9.]*)"
    r"|(?:[ \t]*&[ \t]*[A-Za-z0-9][A-Za-z0-9.]*))*"
)
_CURRENCY_PAIR = re.compile(r"[A-Z]{2,6}/[A-Z]{2,6}")
_VERSIONED_PRODUCT = re.compile(
    r"(?:[A-Za-z][A-Za-z0-9]*[ -]?\d+(?:\.\d+)*"
    r"|[A-Za-z]+-\d+[A-Za-z0-9-]*)"
)
_CORPORATE_NAME = re.compile(
    r"[A-Z][A-Za-z0-9']+ (?:Inc|Corp|Ltd|LLC)\.?"
)
_DOMAIN_STYLE_NAME = re.compile(r"[A-Z][A-Za-z0-9]*\.[a-z]{2,}")
_SINGLE_FOREIGN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9']*")
_ALLOWED_EXACT_FOREIGN_SPANS = frozenset(
    {
        "Nookplot Python SDK",
        "Python SDK",
        "S&P 500",
        "nookplot-runtime",
    }
)
_MEDICAL_FOREIGN_SUFFIXES = (
    "用于",
    "治疗",
    "试验",
    "获批",
    "药物",
    "疗法",
    "患者",
    "剂量",
)
_SHA256 = re.compile(r"[0-9a-f]{64}")

_CJK_RANGES = (
    (0x3400, 0x4DBF),
    (0x4E00, 0x9FFF),
    (0xF900, 0xFAFF),
    (0x20000, 0x2A6DF),
    (0x2A700, 0x2B73F),
    (0x2B740, 0x2B81F),
    (0x2B820, 0x2CEAF),
    (0x2CEB0, 0x2EBEF),
    (0x2EBF0, 0x2EE5F),
    (0x2F800, 0x2FA1F),
    (0x30000, 0x3134F),
    (0x31350, 0x323AF),
    (0x323B0, 0x3347F),
)


def _is_cjk(char: str) -> bool:
    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def _is_ticker_span(span: str) -> bool:
    if _CURRENCY_PAIR.fullmatch(span) is not None:
        return True
    if len(span) > 12 or _TICKER_PATTERN.fullmatch(span) is None:
        return False
    if span.upper() != span:
        return False
    if "." in span or "^" in span:
        return True
    if "-" in span:
        suffix = span.rsplit("-", 1)[-1]
        return len(suffix) <= 2 or any(char.isdigit() for char in suffix)
    return len(span) <= 6


def _foreign_span_context(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
) -> bool:
    if span in _ALLOWED_EXACT_FOREIGN_SPANS:
        return True
    if span in _AMBIGUOUS_FINANCE_CODES:
        return True
    folded = span.casefold().rstrip(".")
    prose_form = folded[:-2] if folded.endswith("'s") else folded
    if prose_form in _ENGLISH_PROSE_WORDS:
        return False
    if _is_ticker_span(span):
        return True
    if _VERSIONED_PRODUCT.fullmatch(span) is not None:
        product = re.split(r"[ -]?\d", span, maxsplit=1)[0].casefold()
        return product not in _ENGLISH_PROSE_WORDS
    if _CORPORATE_NAME.fullmatch(span) is not None:
        return True
    if _DOMAIN_STYLE_NAME.fullmatch(span) is not None:
        return True
    if _SINGLE_FOREIGN_TOKEN.fullmatch(span) is None:
        return False

    before = sentence[start - 1] if start > 0 else ""
    after = sentence[end] if end < len(sentence) else ""
    parenthetical = (
        start > 1
        and before in "（("
        and _is_cjk(sentence[start - 2])
    ) or (
        end + 1 < len(sentence)
        and after in "）)"
        and _is_cjk(sentence[end + 1])
    )
    alias_parenthetical = False
    if after in "（(":
        closing = "）" if after == "（" else ")"
        closing_index = sentence.find(closing, end + 1)
        alias_parenthetical = (
            closing_index >= 0
            and closing_index + 1 < len(sentence)
            and _is_cjk(sentence[closing_index + 1])
        )
    direct_cjk = (bool(before) and _is_cjk(before)) or (
        bool(after) and _is_cjk(after)
    )
    letters = span.replace("'", "")
    if letters.islower():
        suffix = sentence[end:]
        return parenthetical or alias_parenthetical or any(
            suffix.startswith(item) for item in _MEDICAL_FOREIGN_SUFFIXES
        )
    return parenthetical or alias_parenthetical or direct_cjk


def validate_simplified_chinese_text(value: str) -> str:
    """Reject non-Chinese prose and common Traditional Chinese deterministically."""

    text = value.strip()
    if not text:
        raise ValueError("simplified_chinese_text_required")
    traditional = sorted({char for char in text if char in _TRADITIONAL_ONLY})
    if traditional or any(
        phrase in text for phrase in _TRADITIONAL_CONFLICT_PHRASES
    ):
        raise ValueError("traditional_chinese_not_allowed")
    cjk_count = sum(1 for char in text if _is_cjk(char))
    if cjk_count == 0:
        raise ValueError("simplified_chinese_text_required")
    latin_count = sum(1 for char in text if char.isascii() and char.isalpha())
    if latin_count > max(32, cjk_count * 4):
        raise ValueError("english_prose_not_allowed")
    for sentence in _SENTENCE_SPLIT.split(text):
        sentence_latin = sum(
            1 for char in sentence if char.isascii() and char.isalpha()
        )
        sentence_cjk = sum(1 for char in sentence if _is_cjk(char))
        if sentence_latin > max(16, sentence_cjk * 5):
            raise ValueError("english_prose_not_allowed")
        for match in _FOREIGN_SPAN.finditer(sentence):
            if _foreign_span_context(
                match.group(0),
                sentence=sentence,
                start=match.start(),
                end=match.end(),
            ):
                continue
            raise ValueError("english_prose_not_allowed")
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
        if any(_TICKER_PATTERN.fullmatch(ticker) is None for ticker in tickers):
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
        if any(_TICKER_PATTERN.fullmatch(ticker) is None for ticker in tickers):
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
