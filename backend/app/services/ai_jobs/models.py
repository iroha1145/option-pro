from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Iterable, Literal, Optional

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    ValidationInfo,
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
RESULT_VALIDATION_CONTRACT_VERSION = "simplified-chinese-v4"
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
_SENTENCE_SPLIT = re.compile(r"[。！？!?\n]+")
_REGULATORY_RULE_PREFIX = re.compile(
    r"(?<![A-Za-z0-9])Rule\s+(?=10b5-1(?![A-Za-z0-9]))",
    re.IGNORECASE,
)
_GREEK_SCIENTIFIC_SYMBOLS = frozenset(
    "ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩαβγδεζηθικλμνξοπρστυφχψω"
)
_GREEK_SCIENTIFIC_PREFIX_CONTEXTS = (
    "亚型",
    "受体",
    "参数",
    "变体",
    "因子",
    "激酶",
    "系数",
    "细胞",
    "蛋白",
    "角度",
    "波长",
)
_GREEK_SCIENTIFIC_SUFFIX_CONTEXTS = (
    "亚型",
    "变异株",
    "受体",
    "射线",
    "综合征",
    "粒子",
    "系数",
    "细胞",
    "蛋白",
    "衰变",
)
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
        "bank",
        "before",
        "beats",
        "bear",
        "bonds",
        "boom",
        "breaking",
        "business",
        "bull",
        "cash",
        "climb",
        "climbs",
        "crash",
        "crashes",
        "crisis",
        "crunch",
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
        "job",
        "launch",
        "launched",
        "launches",
        "loss",
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
        "panic",
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
        "rate",
        "rapidly",
        "report",
        "reports",
        "results",
        "retaliates",
        "retreats",
        "revenue",
        "risk",
        "rises",
        "rose",
        "sales",
        "says",
        "sees",
        "sell",
        "sells",
        "shares",
        "shift",
        "shock",
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
    r"(?:[A-Za-z][A-Za-z0-9]*|[0-9]+[A-Za-z][A-Za-z0-9]*)"
    r"(?:(?:[.'/-][A-Za-z0-9]+)"
    r"|(?:[ \t]+[A-Za-z0-9][A-Za-z0-9.]*)"
    r"|(?:[ \t]*&[ \t]*[A-Za-z0-9][A-Za-z0-9.]*))*"
)
_CURRENCY_PAIR = re.compile(r"[A-Z]{2,6}/[A-Z]{2,6}")
_ALLOWED_CURRENCY_CODES = frozenset(
    {
        "AUD",
        "BTC",
        "CAD",
        "CHF",
        "CNH",
        "CNY",
        "ETH",
        "EUR",
        "GBP",
        "HKD",
        "JPY",
        "NZD",
        "SGD",
        "SOL",
        "USD",
        "USDT",
        "XAG",
        "XAU",
    }
)
_VERSIONED_PRODUCT = re.compile(
    r"(?P<base>[A-Za-z]+)[ -]?(?P<version>\d+(?:\.\d+)*)"
)
_ALLOWED_VERSIONED_PRODUCT_BASES = frozenset(
    {
        "Android",
        "CUDA",
        "COVID",
        "Claude",
        "F",
        "GPT",
        "Gemini",
        "Llama",
        "Python",
        "RTX",
        "Windows",
        "iOS",
        "iPhone",
        "macOS",
    }
)
_SINGLE_FOREIGN_TOKEN = re.compile(r"[A-Za-z][A-Za-z']*")
_TITLE_CASE_PROPER_NAME = re.compile(r"[A-Z][a-z]{2,31}")
_OPAQUE_INITIALISM = re.compile(
    r"(?=.{2,16}\Z)(?=.*[A-Z])"
    r"(?:[A-Z0-9]+(?:[&./+\-][A-Z0-9]+)*)"
)
_COMPACT_DIGIT_LETTER_IDENTIFIER = re.compile(
    r"(?:[0-9]{1,4}[A-Za-z]|[A-Za-z][0-9]{1,4})"
)
_PLURALIZED_INITIALISM = re.compile(r"[A-Z]{2,8}s")
_NUMERIC_SECURITY_CODE = re.compile(
    r"(?<![A-Za-z0-9^\-])[0-9]{1,12}(?![A-Za-z0-9])"
)
_FORMATTED_NUMBER_CONTINUATION = re.compile(
    r"^[,，][0-9]{3}(?:[,，][0-9]{3})*(?![0-9])"
)
_FORMATTED_NUMBER = re.compile(
    r"(?<![0-9])[0-9]{1,3}(?:[,，][0-9]{3})+(?![0-9])"
)
_ALLOWED_EXACT_FOREIGN_SPANS = frozenset(
    {
        "5G",
        "10B5-1",
        "10b5-1",
        "ADP",
        "AI",
        "APDS",
        "API",
        "AUM",
        "AWS",
        "Adobe",
        "Amazon",
        "Amazon.com",
        "Android",
        "Apple",
        "Atlas",
        "Axios",
        "Azure",
        "B200",
        "BOJ",
        "Base",
        "Blackwell",
        "Block",
        "CAGR",
        "CDN",
        "CFTC",
        "CPI",
        "CPU",
        "CRM",
        "CUDA",
        "Claude",
        "Cloudflare",
        "Copilot",
        "CrowdStrike",
        "DCF",
        "DEI",
        "DOJ",
        "DRAM",
        "EBITDA",
        "ECB",
        "EPS",
        "ETF",
        "EUV",
        "EV",
        "Eylea",
        "F-35A",
        "FCF",
        "FDA",
        "FOMC",
        "FTC",
        "Facebook",
        "GAAP",
        "GB200",
        "GDP",
        "GLP-1",
        "GPU",
        "Gemini",
        "GitHub",
        "GitLab",
        "Goodyear",
        "Google",
        "H100",
        "HBM",
        "HDD",
        "HIV",
        "Humira",
        "IDM 2.0",
        "IPO",
        "ISM",
        "Instagram",
        "IonQ",
        "JOLTS",
        "Joenja",
        "Kalshi",
        "LLM",
        "LNG",
        "LinkedIn",
        "Llama",
        "MI300X",
        "McDonald's",
        "Meta",
        "Microsoft",
        "MoM",
        "Moderna",
        "NAND",
        "NAV",
        "NASCAR",
        "NVIDIA",
        "NPU",
        "OPEC",
        "Office",
        "OpenAI",
        "Ozempic/Wegovy",
        "P/E",
        "PBOC",
        "PCE",
        "PEG",
        "PMI",
        "Palantir",
        "PayPal",
        "Pharming",
        "Photoshop/Premiere",
        "PlayStation",
        "Python",
        "Python SDK",
        "PyTorch-Lightning",
        "QoQ",
        "Qualcomm",
        "RAM",
        "ROE",
        "ROIC",
        "RSA",
        "S&P 500",
        "SEC",
        "SDK",
        "SaaS",
        "SSD",
        "Salesforce",
        "ServiceNow",
        "Skydance",
        "Snowflake",
        "Square",
        "TSMC",
        "Temu",
        "TeraWulf",
        "TikTok",
        "Varonis",
        "VIX",
        # signal_analysis 契约的 key_levels.vwap_levels 字段就要求模型讨论
        # VWAP——不进白名单会自相矛盾：输入没有 VWAP 数据时，模型如实写
        # 「未提供VWAP数据」反而被拒（2026-08-02 生产 schema_validation_failed
        # 根因之一）。
        "VWAP",
        "Visa",
        "WTI",
        "WhatsApp",
        "Windows",
        "YoY",
        "YouTube",
        "eBay",
        "gpt-oss",
        "iOS",
        "iPad",
        "iPhone",
        "iShares",
        "mRNA",
        "macOS",
        "scikit-learn",
    }
)
_CROSS_SENTENCE_SECURITY_ISSUERS = frozenset(
    {
        "Adobe",
        "Amazon",
        "Amazon.com",
        "Apple",
        "Axios",
        "Block",
        "Cloudflare",
        "CrowdStrike",
        "Facebook",
        "GitLab",
        "Goodyear",
        "Google",
        "Instagram",
        "IonQ",
        "Kalshi",
        "LinkedIn",
        "McDonald's",
        "Meta",
        "Microsoft",
        "Moderna",
        "NVIDIA",
        "OpenAI",
        "Palantir",
        "PayPal",
        "Pharming",
        "Qualcomm",
        "Salesforce",
        "ServiceNow",
        "Skydance",
        "Snowflake",
        "Square",
        "TSMC",
        "Temu",
        "TeraWulf",
        "TikTok",
        "Varonis",
        "Visa",
        "WhatsApp",
        "YouTube",
        "eBay",
    }
)
_ALLOWED_LOWERCASE_FOREIGN_NAMES = frozenset(
    {
        "leniolisib",
        "remdesivir",
        "semaglutide",
    }
)
_CJK_CONTEXT_SEPARATORS = frozenset(
    " \t，、：；,:“”‘’「」『』《》【】—–-"
)
_SECURITY_ALIAS_OPENERS = frozenset("（(【[{<《“「『〔〖〘〚\"'`＂＇｀")
_SECURITY_ALIAS_CLOSERS = frozenset("）)】]}>》”」』〕〗〙〛\"'`＂＇｀")
_SECURITY_CODE_SUFFIXES = (
    "股价",
    "股票",
    "普通股",
    "股份",
    "公司",
    "个股",
    "证券",
)
_SECURITY_PRICE_MOVEMENTS = (
    "上涨",
    "下跌",
    "涨停",
    "跌停",
    "走强",
    "走弱",
    "收涨",
    "收跌",
)
_STOCK_PRICE_SUFFIX = re.compile(
    r"^(?:的)?(?:当前|最新|今日|昨日|本周|盘前|盘后)?股价"
)
_SECURITY_NOUN_SUFFIX = re.compile(
    r"^(?:的)?(?P<noun>这只普通股|这只股票|这只个股|"
    r"普通股|股票|个股|股份|证券)(?P<tail>.*)$"
)
_SECURITY_REFERENCE_PREFIX = re.compile(
    r"(?:股票代码|普通股代码|股份代码|证券代码|证券编号|"
    r"股票|普通股|股份|个股|证券)(?:为|是)?$"
)
_NUMERIC_SECURITY_REFERENCE_PREFIX = re.compile(
    r"(?:股票代码|普通股代码|股份代码|证券代码|证券编号)(?:为|是)?$"
)
_NUMERIC_CONTEXT_BOUNDARIES = frozenset("，,；;。.!！?？%％、")
_NUMERIC_SUFFIX_HARD_BOUNDARIES = frozenset("；;。.!！?？%％")
_SECURITY_REFERENCE_MARKERS = (
    "股价",
    "股票",
    "普通股",
    "股份",
    "个股",
    "证券",
)
_SECURITY_COMPANY_BRIDGES = ("公司", "集团", "企业")
_SECURITY_FOCUS_SUFFIXES = (
    "成为当前市场焦点",
    "成为市场焦点",
    "是当前市场焦点",
    "是市场焦点",
)
_SECURITY_ROLE_SUFFIXES = (
    "作为竞争对手",
    "作为供应商",
    "作为客户",
    "作为交易所交易基金",
    "作为对冲标的",
)
_INITIALISM_CONTEXT_SUFFIXES = (
    "数据",
    "指数",
    "报告",
    "会议",
    "决议",
    "调查",
    "库存",
    "原油",
    "能源",
    "就业",
    "通胀",
    "利率",
    "制造业",
    "服务业",
    "规则",
    "标准",
    "协议",
    "系统",
    "模型",
    "计划",
    "政策",
    "机构",
    "平台",
    "工具",
    "产品",
    "技术",
    "芯片",
    "软件",
    "安全",
    "基金",
    "期货",
    "增长",
    "序列",
    "指标",
    "柱状图",
    "分位",
    "分数",
    "关键位",
)
_FOREIGN_PROPER_NAME_CONTEXT_SUFFIXES = (
    "项目",
    "产品",
    "平台",
    "系统",
    "技术",
    "芯片",
    "软件",
    "模型",
    "机器人",
    "处理器",
    "大型机",
    "车型",
    "出租车",
    "服务",
    "业务",
    "主题",
    "进展",
)
_FOREIGN_PROPER_NAME_BLOCKING_SUFFIXES = (
    "公司",
    "集团",
    "企业",
    "发布",
    "宣布",
    "推出",
    "业绩",
    "财报",
    "营收",
    "利润",
    "订单",
    "收购",
    "合作",
)
_SOURCE_BOUND_ENTITY_DISALLOWED_WORDS = frozenset(
    {
        "a",
        "after",
        "an",
        "announces",
        "before",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "names",
        "on",
        "raises",
        "reports",
        "rule",
        "says",
        "sells",
        "to",
        "with",
    }
)
_SOURCE_BOUND_ENTITY_CONNECTORS = frozenset({"and", "of", "the"})
_SOURCE_BOUND_ENTITY_PART = re.compile(r"[A-Za-z0-9][A-Za-z0-9.'/-]*")
_SOURCE_BOUND_MULTIWORD_ENTITY_ENDINGS = frozenset(
    {
        "awards",
        "conference",
        "corp",
        "corporation",
        "desk",
        "etf",
        "fund",
        "group",
        "holdings",
        "inc",
        "laboratories",
        "labs",
        "ltd",
        "mainframe",
        "platform",
        "plc",
        "systems",
        "technologies",
        "technology",
    }
)
_SOURCE_BOUND_MULTIWORD_ENTITY_NOUNS = (
    _SOURCE_BOUND_MULTIWORD_ENTITY_ENDINGS | {"growth"}
)
_SOURCE_BOUND_ROLE_TOKENS = frozenset(
    {"ceo", "cfo", "cio", "coo", "cto", "president"}
)
_SOURCE_BOUND_ROLE_PREDICATE = re.compile(
    r"^\s+(?:announces?|has|have|is|reports?|said|says|to|was|will)\b",
    re.IGNORECASE,
)
_SOURCE_BOUND_ENTITY_DEFINITION = re.compile(
    r"^(?:"
    r"\s+(?:is|remains|was)\s+(?:(?:an?|the)\s+)?"
    r"|\s*,\s*(?:(?:an?|the)\s+|"
    r"(?:[A-Z][A-Za-z.'-]*\s+){0,3}[A-Z][A-Za-z.'-]*['’]s\s+)"
    r")"
    r"(?:closed-end\s+)?(?:business|company|corporation|etf|fund|group|"
    r"index|platform|product|service|system)\b",
    re.IGNORECASE,
)
_SOURCE_BOUND_ENTITY_ALIAS = re.compile(
    r"^\s*[（(]\s*[A-Z0-9][A-Z0-9.&/+_-]{1,15}\s*[)）]"
)
_SOURCE_BOUND_HEADLINE_SEGMENT_SPLIT = re.compile(
    r"(?:\s+[|—–-]\s+|[:：;；.!?。！？\n]+)"
)
_SOURCE_BOUND_SECURITY_CONTEXT = re.compile(
    r"^[\s,:：\-—–]*(?:(?:['’]s[\s,:：\-—–]*)"
    r"(?:[0-9][0-9,]*(?:\.[0-9]+)?\s+)?|"
    r"(?:(?:has|have|had|is|are|was|were)\s+)?(?:among\s+)?)"
    r"(?:stock(?:'s|s)?|shares?|securit(?:y|ies)|stake|equity|equities|"
    r"holdings?|"
    r"investors?|gainers?|losers?|rose|rises?|fell|falls?|gained|lost|"
    r"outperform(?:ed|s|ing)?|underperform(?:ed|s|ing)?|rallied|slid|"
    r"plunged|surged|trading|price|buying|selling)\b",
    re.IGNORECASE,
)
_SOURCE_BOUND_SECURITY_CLAUSE_SPLIT = re.compile(r"[;；.!?。！？\n]+")
_SOURCE_TECHNICAL_MODIFIERS = (
    "artificial",
    "biological",
    "biotech",
    "clinical",
    "genetic",
    "genomic",
    "medical",
    "molecular",
    "quantum",
    "semiconductor",
    "synthetic",
    "therapeutic",
)
_SOURCE_TECHNICAL_NOUNS = (
    "business",
    "industry",
    "manufacturer",
    "manufacturers",
    "manufacturing",
    "market",
    "platform",
    "research",
    "sector",
    "sequencing",
    "system",
    "technology",
    "therapy",
)
_TECHNICAL_INITIALISM_SECURITY_CATEGORY_SUFFIXES = (
    "企业的股份",
    "企业的股票",
    "企业股份",
    "企业股票",
    "股票",
)
_GENERIC_SECURITY_INSTRUMENT_PREFIXES = (
    "股票",
    "债券",
    "商品",
    "行业",
    "指数",
)
_GENERIC_SECURITY_INSTRUMENT_SPANS = frozenset({"ETF"})
_GENERIC_NON_REFERENCE_SECURITY_COMPOUNDS = (
    "股份有限公司",
    "股份公司",
    "证券欺诈集体诉讼",
    "证券集体诉讼",
    "证券欺诈诉讼",
    "证券诉讼",
)
_NON_REFERENCE_SECURITY_COMPOUNDS = {
    "10B5-1": ("股票交易计划", "证券交易计划"),
    "10b5-1": ("股票交易计划", "证券交易计划"),
    "S&P 500": ("股票指数",),
    "SEC": ("证券监管",),
    "iShares": ("股票基金",),
    "Apple": ("股票应用",),
}
_JAPANESE_COMPANY_MARKERS = (
    "株式会社",
    "有限会社",
    "合同会社",
    "㈱",
    "㍿",
    "（株）",
    "(株)",
    "売上高",
    "株価",
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


@lru_cache(maxsize=8192)
def _is_cjk(char: str) -> bool:
    """Whether one character is CJK. Memoized -- it is a pure range lookup.

    The Chinese-text validator asks this twice per character of every string
    field of every stored result, and the feed validates 112 results on read.
    Measured on production: 1,378,187 calls over 1,229 distinct characters, and
    caching them took service.feed() from 1.477s to 0.833s.

    Bounded rather than unbounded: the input is untrusted model output, so an
    adversarial result full of distinct codepoints must not be able to grow this
    without limit. 8192 comfortably covers real Chinese prose -- the whole feed
    used 1,229 entries -- and _CJK_RANGES is a module constant, so nothing about
    the answer can change at runtime.
    """

    codepoint = ord(char)
    return any(start <= codepoint <= end for start, end in _CJK_RANGES)


def _is_embedded_greek_scientific_symbol(text: str, index: int) -> bool:
    if text[index] not in _GREEK_SCIENTIFIC_SYMBOLS:
        return False
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if before in _GREEK_SCIENTIFIC_SYMBOLS or after in _GREEK_SCIENTIFIC_SYMBOLS:
        return False
    prefix = _normalize_security_reference_phrase(text[:index])
    suffix = _strip_security_reference_separators(text[index + 1 :])
    if (
        _SECURITY_REFERENCE_PREFIX.search(prefix) is not None
        or suffix.startswith(_SECURITY_CODE_SUFFIXES)
        or suffix.startswith(_SECURITY_PRICE_MOVEMENTS)
    ):
        return False
    scientific_prefix = _normalize_security_reference_phrase(
        text[max(0, index - 16) : index]
    )
    scientific_suffix = _strip_security_reference_separators(
        text[index + 1 : index + 17]
    )
    return scientific_prefix.endswith(_GREEK_SCIENTIFIC_PREFIX_CONTEXTS) or (
        scientific_suffix.startswith(_GREEK_SCIENTIFIC_SUFFIX_CONTEXTS)
    )


def _is_security_reference_separator(char: str) -> bool:
    category = unicodedata.category(char)
    return (
        char.isspace()
        or category[0] in {"C", "M", "N", "P", "S", "Z"}
    )


def _is_security_alias_opener(char: str) -> bool:
    return char in _SECURITY_ALIAS_OPENERS or unicodedata.category(char) in {
        "Pi",
        "Ps",
    }


def _is_security_alias_closer(char: str) -> bool:
    return char in _SECURITY_ALIAS_CLOSERS or unicodedata.category(char) in {
        "Pe",
        "Pf",
    }


def _strip_security_reference_separators(
    value: str,
    *,
    preserve_alias_opener: bool = False,
) -> str:
    index = 0
    while index < len(value) and _is_security_reference_separator(value[index]):
        if preserve_alias_opener and _is_security_alias_opener(value[index]):
            break
        index += 1
    return value[index:]


def _normalize_security_reference_phrase(value: str) -> str:
    return "".join(
        char
        for char in value
        if not _is_security_reference_separator(char)
    )


def _consume_security_alias(value: str) -> tuple[str, str] | None:
    if not value or not _is_security_alias_opener(value[0]):
        return "", value

    for index, char in enumerate(value[1:], start=1):
        if char in "\r\n":
            break
        if not _is_security_alias_closer(char):
            continue
        return value[1:index], value[index + 1 :]
    return None


def _security_phrase_requires_ticker_binding(span: str, phrase: str) -> bool:
    phrase = _normalize_security_reference_phrase(phrase)
    while True:
        bridge = next(
            (
                item
                for item in _SECURITY_COMPANY_BRIDGES
                if phrase.startswith(item)
            ),
            None,
        )
        if bridge is None:
            break
        phrase = phrase[len(bridge) :]

    if _STOCK_PRICE_SUFFIX.match(phrase) is not None:
        return True
    if phrase.startswith(_SECURITY_FOCUS_SUFFIXES):
        return True
    if phrase.startswith(_SECURITY_ROLE_SUFFIXES):
        return True
    if phrase.startswith(_SECURITY_PRICE_MOVEMENTS) or any(
        phrase.startswith(f"{period}{movement}")
        for period in ("当前", "最新", "今日", "昨日", "本周", "盘前", "盘后")
        for movement in _SECURITY_PRICE_MOVEMENTS
    ):
        return True

    noun_match = _SECURITY_NOUN_SUFFIX.match(phrase)
    if noun_match is None:
        return False

    noun = noun_match.group("noun")
    if noun.startswith("这只"):
        return True

    tail = noun_match.group("tail")
    if not tail:
        return True

    reference = f"{noun}{tail}"
    compounds = (
        *_GENERIC_NON_REFERENCE_SECURITY_COMPOUNDS,
        *_NON_REFERENCE_SECURITY_COMPOUNDS.get(span, ()),
    )
    for compound in compounds:
        if not reference.startswith(compound):
            continue
        remainder = _strip_security_reference_separators(
            reference[len(compound) :]
        )
        if _STOCK_PRICE_SUFFIX.match(remainder) is None and (
            _SECURITY_NOUN_SUFFIX.match(remainder) is None
        ):
            return False
    return True


def _security_alias_requires_ticker_binding(span: str, alias: str) -> bool:
    alias = _normalize_security_reference_phrase(alias)
    marker_positions = (
        (index, marker)
        for marker in _SECURITY_REFERENCE_MARKERS
        if (index := alias.find(marker)) >= 0
    )
    first_marker = min(marker_positions, default=None)
    if first_marker is None:
        return False
    return _security_phrase_requires_ticker_binding(
        span,
        alias[first_marker[0] :],
    )


def _is_ticker_span(span: str, allowed_codes: frozenset[str]) -> bool:
    if span.upper() == span and span in allowed_codes:
        return True
    if _CURRENCY_PAIR.fullmatch(span) is None:
        return False
    base, quote = span.split("/", 1)
    return base in _ALLOWED_CURRENCY_CODES and quote in _ALLOWED_CURRENCY_CODES


def _is_allowed_versioned_product(span: str) -> bool:
    matched = _VERSIONED_PRODUCT.fullmatch(span)
    if matched is None:
        return False
    return matched.group("base") in _ALLOWED_VERSIONED_PRODUCT_BASES


def _is_contextual_initialism(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
    allowed_codes: frozenset[str],
) -> bool:
    """Allow compact acronyms without maintaining an entity whitelist.

    A ticker-looking token still needs payload binding when it is used as a
    stock reference. In ordinary Chinese prose, compact identifiers such as
    EIA, 3M and AT&T are treated as opaque names rather than English prose.
    """

    if _OPAQUE_INITIALISM.fullmatch(span) is None:
        return False
    if not any(char.isascii() and char.isalpha() for char in span):
        return False
    prose_tokens = re.findall(r"[A-Z]+", span)
    if any(
        token.casefold() in _ENGLISH_PROSE_WORDS
        and not (
            len(token) == 1
            and re.fullmatch(r"(?:[0-9]+[A-Z]|[A-Z][0-9]+)", span)
            is not None
        )
        for token in prose_tokens
    ):
        return False
    if span.isalpha() and len(span) > 4:
        return False
    if not any(_is_cjk(char) for char in sentence):
        return False
    suffix = _normalize_security_reference_phrase(sentence[end:]).removeprefix(
        "的"
    )
    if span.isalpha() and suffix.startswith(_SECURITY_COMPANY_BRIDGES):
        return span in allowed_codes
    if _approved_span_requires_ticker_binding(
        span,
        sentence=sentence,
        start=start,
        end=end,
    ):
        return span in allowed_codes
    if any(char.isdigit() or char in "&./+-" for char in span):
        return True
    prefix = _normalize_security_reference_phrase(sentence[:start])
    return suffix.startswith(_INITIALISM_CONTEXT_SUFFIXES) or prefix.endswith(
        ("由", "据", "根据", "来自", "未提供", "缺少", "没有", "无法取得")
    )


def _approved_span_requires_ticker_binding(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
) -> bool:
    before_index = start - 1
    while (
        before_index >= 0
        and _is_security_reference_separator(sentence[before_index])
    ):
        before_index -= 1
    prefix = sentence[: before_index + 1]

    if (
        _SECURITY_REFERENCE_PREFIX.search(
            _normalize_security_reference_phrase(prefix)
        )
        is not None
    ):
        return True

    suffix = _strip_security_reference_separators(
        sentence[end:],
        preserve_alias_opener=True,
    )
    while suffix:
        if _is_security_alias_opener(suffix[0]):
            alias_parts = _consume_security_alias(suffix)
            if alias_parts is None:
                return True
            alias, suffix_after_alias = alias_parts
            if _security_alias_requires_ticker_binding(span, alias):
                return True
            suffix = _strip_security_reference_separators(
                suffix_after_alias,
                preserve_alias_opener=True,
            )
            continue

        bridge = next(
            (
                item
                for item in _SECURITY_COMPANY_BRIDGES
                if suffix.startswith(item)
            ),
            None,
        )
        if bridge is None:
            break
        suffix = _strip_security_reference_separators(
            suffix[len(bridge) :],
            preserve_alias_opener=True,
        )

    suffix = _strip_security_reference_separators(suffix)

    return _security_phrase_requires_ticker_binding(span, suffix)


def _is_copied_source_headline_fragment(
    span: str,
    source_texts: tuple[str, ...],
) -> bool:
    parts = _SOURCE_BOUND_ENTITY_PART.findall(span)
    words = [part for part in parts if any(char.isalpha() for char in part)]
    if len(words) < 3:
        return False
    prose_words = {
        re.sub(r"[^A-Za-z]", "", word).casefold()
        for word in words
        if re.sub(r"[^A-Za-z]", "", word).casefold()
        in _ENGLISH_PROSE_WORDS
        and re.sub(r"[^A-Za-z]", "", word).casefold()
        not in _SOURCE_BOUND_ENTITY_CONNECTORS
    }
    trailing_word = re.sub(r"[^a-z]", "", words[-1].casefold())
    fragment_is_entity_shaped = (
        trailing_word in _SOURCE_BOUND_MULTIWORD_ENTITY_ENDINGS
        and not (
            prose_words - _SOURCE_BOUND_MULTIWORD_ENTITY_NOUNS
        )
    )
    span_tokens = tuple(part.casefold() for part in parts)
    compact_span = re.sub(r"[^A-Za-z0-9]", "", span).casefold()
    role_subject = bool(
        {re.sub(r"[^a-z]", "", word.casefold()) for word in words}
        & _SOURCE_BOUND_ROLE_TOKENS
    )
    exact_pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(span)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    for source in source_texts:
        if ("/" in source or "|" in source) and any(
            re.sub(r"[^A-Za-z0-9]", "", component).casefold()
            == compact_span
            for component in re.split(r"[/|]", source)
        ):
            continue
        source_defines_entity = any(
            _SOURCE_BOUND_ENTITY_DEFINITION.match(source[match.end() :])
            is not None
            or (
                not prose_words
                and _SOURCE_BOUND_ENTITY_ALIAS.match(source[match.end() :])
                is not None
            )
            or (
                role_subject
                and _SOURCE_BOUND_ROLE_PREDICATE.match(source[match.end() :])
                is not None
            )
            for match in exact_pattern.finditer(source)
        )
        for segment in _SOURCE_BOUND_HEADLINE_SEGMENT_SPLIT.split(source):
            segment_tokens = tuple(
                part.casefold()
                for part in _SOURCE_BOUND_ENTITY_PART.findall(segment)
            )
            if segment_tokens == span_tokens:
                return True
            if len(segment_tokens) <= len(span_tokens):
                continue
            contained = any(
                segment_tokens[offset : offset + len(span_tokens)]
                == span_tokens
                for offset in range(
                    len(segment_tokens) - len(span_tokens) + 1
                )
            )
            if contained and not (
                fragment_is_entity_shaped or source_defines_entity
            ):
                return True
    return False


def _is_source_bound_foreign_entity(
    span: str,
    source_texts: tuple[str, ...],
) -> bool:
    """Accept a compact proper name only when it exists in the paid input.

    The source binding replaces entity-by-entity allow-list growth.  Shape
    checks still reject copied English prose, while registered names, product
    names and event names can remain inside otherwise Chinese text.
    """

    if not source_texts or not 1 < len(span) <= 80:
        return False
    exact_pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(span)}(?:s)?(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    compact_span = re.sub(r"[^A-Za-z0-9]", "", span).casefold()
    source_bound = any(
        exact_pattern.search(source) is not None
        or any(
            re.sub(r"[^A-Za-z0-9]", "", component).casefold()
            == compact_span
            for component in re.split(r"[/|]", source)
        )
        for source in source_texts
    )
    if not source_bound:
        return False
    if _is_copied_source_headline_fragment(span, source_texts):
        return False
    parts = _SOURCE_BOUND_ENTITY_PART.findall(span)
    if not parts or len(parts) > 6:
        return False
    words = [part for part in parts if any(char.isalpha() for char in part)]
    if not words or sum(sum(char.isalpha() for char in word) for word in words) > 48:
        return False
    folded_words = {
        re.sub(r"[^A-Za-z]", "", word).casefold()
        for word in words
    }
    if folded_words & _SOURCE_BOUND_ENTITY_DISALLOWED_WORDS:
        return False
    prose_words = [
        word
        for word in folded_words
        if word in _ENGLISH_PROSE_WORDS
        and word not in _SOURCE_BOUND_ENTITY_CONNECTORS
    ]
    if len(prose_words) >= 2:
        # Source binding proves only that the provider saw the text.  It does
        # not turn a copied English headline into a registered entity.  Keep
        # one ordinary noun available for real names such as a Growth ETF,
        # while rejecting clause-like spans such as "Apple Beats Estimates".
        return False

    def entity_shaped(word: str) -> bool:
        letters = "".join(char for char in word if char.isalpha())
        if not letters:
            return True
        folded = letters.casefold()
        if len(words) > 1 and folded in _SOURCE_BOUND_ENTITY_CONNECTORS:
            return True
        if len(words) == 1 and folded in _ENGLISH_PROSE_WORDS:
            return False
        if len(words) == 1 and any(char in word for char in "-./"):
            return True
        return (
            any(char.isdigit() for char in word)
            or letters.isupper()
            or letters[0].isupper()
            or any(char.isupper() for char in letters[1:])
        )

    if not all(entity_shaped(word) for word in words):
        return False
    if (
        len(words) > 1
        and not any(char.isdigit() for char in span)
        and all(word.isupper() and len(word) > 4 for word in words)
    ):
        return False
    return True


def _source_binds_security_reference(
    span: str,
    source_texts: tuple[str, ...],
) -> bool:
    """Require the same source name to carry its own security context."""

    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(span)}(?![A-Za-z0-9])",
        re.IGNORECASE,
    )
    for source in source_texts:
        for clause in _SOURCE_BOUND_SECURITY_CLAUSE_SPLIT.split(source):
            for match in pattern.finditer(clause):
                # Security wording after the exact name binds the claim to
                # that source entity. An unrelated ticker elsewhere in the
                # payload is never enough.
                suffix = clause[match.end() : match.end() + 80]
                if _SOURCE_BOUND_SECURITY_CONTEXT.match(suffix) is not None:
                    return True
    return False


def _source_uses_initialism_as_technical_modifier(
    span: str,
    source_texts: tuple[str, ...],
) -> bool:
    if not (2 <= len(span) <= 8 and span.isascii() and span.isupper()):
        return False
    modifiers = "|".join(map(re.escape, _SOURCE_TECHNICAL_MODIFIERS))
    nouns = "|".join(map(re.escape, _SOURCE_TECHNICAL_NOUNS))
    pattern = re.compile(
        rf"\b(?:{modifiers})\s+{re.escape(span)}\s+"
        rf"(?:{nouns})(?:['’]s)?\b",
        re.IGNORECASE,
    )
    return any(pattern.search(source) is not None for source in source_texts)


def _foreign_span_context(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
    allowed_codes: frozenset[str],
    source_texts: tuple[str, ...] = (),
) -> bool:
    if _is_copied_source_headline_fragment(span, source_texts):
        return False
    if len(span) == 1 and span.isascii() and span.isupper():
        suffix = _strip_security_reference_separators(sentence[end:])
        if suffix.startswith(("股价", "股票")):
            return span in allowed_codes
        if suffix.startswith("股"):
            return span in {"A", "B", "H"} or span in allowed_codes
        if suffix.startswith(("轮", "类")):
            return True
        if suffix.startswith("细胞"):
            return span in {"B", "T"}
        if suffix.startswith(("分数", "值", "统计量")):
            return True
        if suffix.startswith(_FOREIGN_PROPER_NAME_CONTEXT_SUFFIXES) and any(
            re.search(
                rf"(?<![A-Za-z0-9]){re.escape(span)}(?![A-Za-z0-9])",
                source,
                re.IGNORECASE,
            )
            is not None
            for source in source_texts
        ):
            return True
    if _COMPACT_DIGIT_LETTER_IDENTIFIER.fullmatch(span) is not None:
        if _approved_span_requires_ticker_binding(
            span,
            sentence=sentence,
            start=start,
            end=end,
        ):
            return span.upper() in allowed_codes
        return any(_is_cjk(char) for char in sentence)
    if (
        _PLURALIZED_INITIALISM.fullmatch(span) is not None
        and span.casefold() not in _ENGLISH_PROSE_WORDS
    ):
        return any(_is_cjk(char) for char in sentence)
    if span in _GENERIC_SECURITY_INSTRUMENT_SPANS:
        normalized_prefix = _normalize_security_reference_phrase(
            sentence[:start]
        )
        suffix = _strip_security_reference_separators(sentence[end:])
        if normalized_prefix.endswith(
            _GENERIC_SECURITY_INSTRUMENT_PREFIXES
        ) and not _security_phrase_requires_ticker_binding(span, suffix):
            return True
    if _is_source_bound_foreign_entity(span, source_texts):
        if _approved_span_requires_ticker_binding(
            span,
            sentence=sentence,
            start=start,
            end=end,
        ):
            suffix = _strip_security_reference_separators(sentence[end:])
            return (
                span.upper() in allowed_codes
                or _source_binds_security_reference(span, source_texts)
                or (
                    start > 0
                    and _is_cjk(sentence[start - 1])
                    and suffix.startswith(
                        _TECHNICAL_INITIALISM_SECURITY_CATEGORY_SUFFIXES
                    )
                    and _source_uses_initialism_as_technical_modifier(
                        span,
                        source_texts,
                    )
                )
            )
        return True
    if span in _ALLOWED_EXACT_FOREIGN_SPANS:
        if _approved_span_requires_ticker_binding(
            span,
            sentence=sentence,
            start=start,
            end=end,
        ):
            return span.upper() in allowed_codes
        return True
    if _is_ticker_span(span, allowed_codes):
        return True
    if _is_contextual_initialism(
        span,
        sentence=sentence,
        start=start,
        end=end,
        allowed_codes=allowed_codes,
    ):
        return True
    folded = span.casefold().rstrip(".")
    prose_form = folded[:-2] if folded.endswith("'s") else folded
    if prose_form in _ENGLISH_PROSE_WORDS:
        return False
    if _is_allowed_versioned_product(span):
        return True
    if any(char.isspace() for char in span):
        for token_match in re.finditer(r"\S+", span):
            if re.fullmatch(
                r"[0-9]+(?:\.[0-9]+)*",
                token_match.group(0),
            ) is not None:
                continue
            if not _foreign_span_context(
                token_match.group(0),
                sentence=sentence,
                start=start + token_match.start(),
                end=start + token_match.end(),
                allowed_codes=allowed_codes,
                source_texts=source_texts,
            ):
                return False
        return True
    if _SINGLE_FOREIGN_TOKEN.fullmatch(span) is None:
        return False
    if span.upper() == span:
        return False

    before_index = start - 1
    while (
        before_index >= 0
        and sentence[before_index] in _CJK_CONTEXT_SEPARATORS
    ):
        before_index -= 1
    after_index = end
    while (
        after_index < len(sentence)
        and sentence[after_index] in _CJK_CONTEXT_SEPARATORS
    ):
        after_index += 1
    before = sentence[before_index] if before_index >= 0 else ""
    after = sentence[after_index] if after_index < len(sentence) else ""
    parenthetical = (
        before_index > 0
        and before in "（("
        and _is_cjk(sentence[before_index - 1])
    ) or (
        after_index + 1 < len(sentence)
        and after in "）)"
        and _is_cjk(sentence[after_index + 1])
    )
    alias_parenthetical = False
    if after in "（(":
        closing = "）" if after == "（" else ")"
        closing_index = sentence.find(closing, after_index + 1)
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
        return (
            span in _ALLOWED_LOWERCASE_FOREIGN_NAMES
            and (
                parenthetical
                or alias_parenthetical
                or direct_cjk
            )
        )
    if _TITLE_CASE_PROPER_NAME.fullmatch(span) is not None:
        if _approved_span_requires_ticker_binding(
            span,
            sentence=sentence,
            start=start,
            end=end,
        ):
            return span.upper() in allowed_codes
        suffix = _normalize_security_reference_phrase(
            sentence[end:]
        ).removeprefix("的")
        if suffix.startswith(_FOREIGN_PROPER_NAME_BLOCKING_SUFFIXES):
            return False
        previous = sentence[start - 1] if start > 0 else ""
        coordinated_product = False
        if bool(previous) and previous in "与和及或、":
            coordinated_prefix = _normalize_security_reference_phrase(
                sentence[: start - 1]
            ).removesuffix("的")
            coordinated_product = coordinated_prefix.endswith(
                _FOREIGN_PROPER_NAME_CONTEXT_SUFFIXES
            )
        return (
            parenthetical
            or alias_parenthetical
            or coordinated_product
            or suffix.startswith(_FOREIGN_PROPER_NAME_CONTEXT_SUFFIXES)
        )
    return False


def _normalize_compatibility_alphanumerics(text: str) -> str:
    """Expose styled Latin letters and digits to the ASCII language gate."""

    normalized: list[str] = []
    for char in text:
        replacement = unicodedata.normalize("NFKC", char)
        if (
            replacement != char
            and any(
                item.isascii() and (item.isalpha() or item.isdigit())
                for item in replacement
            )
        ):
            normalized.append(replacement)
        else:
            normalized.append(char)
    return "".join(normalized)


def _numeric_code_is_in_security_context(
    sentence: str,
    *,
    start: int,
    end: int,
) -> bool:
    for foreign_match in _FOREIGN_SPAN.finditer(sentence):
        if foreign_match.start() > start:
            break
        if (
            foreign_match.start() <= start
            and end <= foreign_match.end()
            and foreign_match.group(0) in _ALLOWED_EXACT_FOREIGN_SPANS
        ):
            return False

    span = sentence[start:end]
    if (
        start >= 2
        and sentence[start - 1] in ".．"
        and sentence[start - 2].isascii()
        and sentence[start - 2].isalnum()
    ) or (
        end + 1 < len(sentence)
        and sentence[end] in ".．"
        and sentence[end + 1].isascii()
        and sentence[end + 1].isalnum()
    ):
        return False
    if any(
        formatted.start() <= start and end <= formatted.end()
        for formatted in _FORMATTED_NUMBER.finditer(sentence)
    ):
        return False
    if _FORMATTED_NUMBER_CONTINUATION.match(sentence[end:]) is not None:
        return False
    if len(span) == 5 and span.startswith("0"):
        return True
    before_index = start - 1
    prefix_blocked = False
    while (
        before_index >= 0
        and _is_security_reference_separator(sentence[before_index])
    ):
        if sentence[before_index] in _NUMERIC_CONTEXT_BOUNDARIES:
            prefix_blocked = True
            break
        before_index -= 1
    after_index = end
    suffix_blocked = False
    while (
        after_index < len(sentence)
        and _is_security_reference_separator(sentence[after_index])
    ):
        if sentence[after_index] in _NUMERIC_SUFFIX_HARD_BOUNDARIES:
            suffix_blocked = True
            break
        after_index += 1
    prefix = (
        ""
        if prefix_blocked
        else _normalize_security_reference_phrase(sentence[: before_index + 1])
    )
    suffix = (
        ""
        if suffix_blocked
        else _normalize_security_reference_phrase(sentence[after_index:])
    )
    if len(span) == 4 and suffix.startswith(("年", "年度", "财年")):
        return False
    return (
        _SECURITY_REFERENCE_PREFIX.search(prefix) is not None
        or suffix.startswith(_SECURITY_CODE_SUFFIXES)
        or suffix.startswith("这只股票")
        or (
            len(span) == 6
            and suffix.startswith(_SECURITY_PRICE_MOVEMENTS)
        )
    )


def _foreign_span_stands_alone_before_sentence_break(
    text: str,
    *,
    start: int,
    end: int,
) -> bool:
    next_boundary = _SENTENCE_SPLIT.search(text, end)
    if next_boundary is None:
        return False
    previous_boundary = max(
        (text.rfind(marker, 0, start) for marker in "。！？!?\n"),
        default=-1,
    )
    before = _normalize_security_reference_phrase(
        text[previous_boundary + 1 : start]
    )
    after = _normalize_security_reference_phrase(
        text[end : next_boundary.start()]
    )
    return not before and not after


def validate_simplified_chinese_text(
    value: str,
    info: ValidationInfo | None,
    *,
    allowed_codes: Iterable[str] = (),
    source_texts: Iterable[str] = (),
) -> str:
    """Reject non-Chinese prose and common Traditional Chinese deterministically."""

    text = _REGULATORY_RULE_PREFIX.sub("规则", value.strip())
    if not text:
        raise ValueError("simplified_chinese_text_required")
    scan_text = _normalize_compatibility_alphanumerics(text)
    compatibility_text = unicodedata.normalize("NFKC", scan_text)
    if any(
        marker in scan_text or marker in compatibility_text
        for marker in _JAPANESE_COMPANY_MARKERS
    ):
        raise ValueError("non_chinese_script_not_allowed")
    if any(
        char.isalpha()
        and not char.isascii()
        and not _is_cjk(char)
        and not _is_embedded_greek_scientific_symbol(scan_text, index)
        for index, char in enumerate(scan_text)
    ):
        raise ValueError("non_chinese_script_not_allowed")
    traditional = sorted(
        {char for char in scan_text if char in _TRADITIONAL_ONLY}
    )
    if traditional or any(
        phrase in scan_text for phrase in _TRADITIONAL_CONFLICT_PHRASES
    ):
        raise ValueError("traditional_chinese_not_allowed")
    cjk_count = sum(1 for char in scan_text if _is_cjk(char))
    if cjk_count == 0:
        raise ValueError("simplified_chinese_text_required")
    context_codes = (
        info.context.get("allowed_codes", ())
        if info is not None and isinstance(info.context, dict)
        else allowed_codes
    )
    normalized_codes = frozenset(
        str(code).strip().upper()
        for code in context_codes
        if isinstance(code, str) and str(code).strip()
    )
    context_source_texts = (
        info.context.get("source_texts", ())
        if info is not None and isinstance(info.context, dict)
        else source_texts
    )
    source_texts = tuple(
        source
        for source in context_source_texts
        if isinstance(source, str) and source
    )
    latin_count = sum(
        1 for char in scan_text if char.isascii() and char.isalpha()
    )
    source_bound_latin = sum(
        sum(char.isascii() and char.isalpha() for char in match.group(0))
        for match in _FOREIGN_SPAN.finditer(scan_text)
        if _is_source_bound_foreign_entity(match.group(0), source_texts)
    )
    if latin_count - source_bound_latin > max(32, cjk_count * 4):
        raise ValueError("english_prose_not_allowed")
    for sentence in _SENTENCE_SPLIT.split(scan_text):
        sentence_latin = sum(
            1 for char in sentence if char.isascii() and char.isalpha()
        )
        sentence_cjk = sum(1 for char in sentence if _is_cjk(char))
        sentence_source_bound_latin = sum(
            sum(char.isascii() and char.isalpha() for char in match.group(0))
            for match in _FOREIGN_SPAN.finditer(sentence)
            if _is_source_bound_foreign_entity(match.group(0), source_texts)
        )
        if sentence_latin - sentence_source_bound_latin > max(
            24,
            sentence_cjk * 5,
        ):
            raise ValueError("english_prose_not_allowed")
        for match in _NUMERIC_SECURITY_CODE.finditer(sentence):
            if not _numeric_code_is_in_security_context(
                sentence,
                start=match.start(),
                end=match.end(),
            ):
                continue
            if match.group(0) not in normalized_codes:
                raise ValueError("unbound_numeric_security_code")
        for match in _FOREIGN_SPAN.finditer(sentence):
            if _foreign_span_context(
                match.group(0),
                sentence=sentence,
                start=match.start(),
                end=match.end(),
                allowed_codes=normalized_codes,
                source_texts=source_texts,
            ):
                continue
            raise ValueError("english_prose_not_allowed")
        if sentence_latin >= 16 and sentence_cjk == 0:
            raise ValueError("english_prose_not_allowed")

    for match in _FOREIGN_SPAN.finditer(scan_text):
        span = match.group(0)
        if span not in _CROSS_SENTENCE_SECURITY_ISSUERS:
            continue
        if not _foreign_span_stands_alone_before_sentence_break(
            scan_text,
            start=match.start(),
            end=match.end(),
        ):
            continue
        if not _approved_span_requires_ticker_binding(
            span,
            sentence=scan_text,
            start=match.start(),
            end=match.end(),
        ):
            continue
        if span not in normalized_codes:
            raise ValueError("english_prose_not_allowed")
    return scan_text


def validate_simplified_chinese_company_name(
    value: str,
    info: ValidationInfo | None,
) -> str:
    """Validate a ticker-bound display name, not a prose sentence.

    Chinese translations remain preferred, but compact registered names such
    as 3M, AT&T and SAP are valid company data. This rule deliberately uses a
    shape and ticker binding instead of an ever-growing entity whitelist.
    """

    text = value.strip()
    if not text:
        raise ValueError("company_name_required")
    ticker = (
        info.data.get("ticker")
        if info is not None and isinstance(info.data, dict)
        else None
    )
    if not isinstance(ticker, str) or _TICKER_PATTERN.fullmatch(ticker) is None:
        raise ValueError("company_name_requires_ticker_binding")

    scan_text = _normalize_compatibility_alphanumerics(text)
    marker_text = unicodedata.normalize("NFKC", scan_text)
    if any(
        marker in scan_text or marker in marker_text
        for marker in _JAPANESE_COMPANY_MARKERS
    ):
        raise ValueError("company_name_must_be_simplified_chinese_or_registered_name")
    if any(
        char.isalpha() and not char.isascii() and not _is_cjk(char)
        for char in scan_text
    ):
        raise ValueError("company_name_must_be_simplified_chinese_or_registered_name")
    traditional = sorted(
        {char for char in scan_text if char in _TRADITIONAL_ONLY}
    )
    if traditional or any(
        phrase in scan_text for phrase in _TRADITIONAL_CONFLICT_PHRASES
    ):
        raise ValueError("traditional_chinese_not_allowed")

    if len(scan_text) > 80 or "\n" in scan_text or "\r" in scan_text:
        raise ValueError("company_registered_name_invalid")
    allowed_punctuation = frozenset(" .,&'’()/+-（）·")
    if any(
        not (
            _is_cjk(char)
            or char.isascii() and char.isalnum()
            or char in allowed_punctuation
        )
        for char in scan_text
    ):
        raise ValueError("company_registered_name_invalid")
    edge_text = scan_text.strip("()（）")
    if not edge_text or not (
        edge_text[0].isalnum() or _is_cjk(edge_text[0])
    ) or not (
        edge_text[-1].isalnum() or _is_cjk(edge_text[-1])
    ):
        raise ValueError("company_registered_name_invalid")
    words = re.findall(r"[A-Za-z]+", scan_text)
    has_cjk = any(_is_cjk(char) for char in scan_text)
    prose_words = sum(
        1 for word in words if word.casefold() in _ENGLISH_PROSE_WORDS
    )
    if len(words) > 6 or sum(len(word) for word in words) > 48:
        raise ValueError("company_registered_name_looks_like_english_prose")
    if prose_words >= 3:
        raise ValueError("company_registered_name_looks_like_english_prose")
    if not has_cjk and not any(
        char.isupper() or char.isdigit() for char in scan_text
    ):
        raise ValueError("company_registered_name_must_be_compact")
    return scan_text


def validate_earnings_impact_reason(
    value: str,
    info: ValidationInfo,
) -> str:
    """Bind each earnings reason to its source and impacted ticker only."""

    raw_source_codes = (
        info.context.get("allowed_codes", ())
        if isinstance(info.context, dict)
        else ()
    )
    source_codes = (
        raw_source_codes
        if isinstance(raw_source_codes, (list, tuple, set, frozenset))
        else ()
    )
    impacted_code = (
        info.data.get("ticker") if isinstance(info.data, dict) else None
    )
    return validate_simplified_chinese_text(
        value,
        None,
        allowed_codes=[*source_codes, impacted_code],
    )


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
ZhCompanyName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(validate_simplified_chinese_company_name),
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
    year: Optional[StrictInt] = Field(default=None, ge=1900, le=2200)
    quarter: Optional[StrictInt] = Field(default=None, ge=1, le=4)
    eps_estimate: Optional[float] = Field(
        default=None,
        ge=-1_000_000,
        le=1_000_000,
    )
    eps_actual: Optional[float] = Field(
        default=None,
        ge=-1_000_000,
        le=1_000_000,
    )
    revenue_estimate: Optional[float] = Field(default=None, ge=0, le=1e16)
    revenue_actual: Optional[float] = Field(default=None, ge=0, le=1e16)
    market_cap: Optional[float] = Field(default=None, ge=0, le=1e16)
    release_status: Literal[
        "scheduled",
        "reported_pending_actual",
        "released",
    ] = "scheduled"
    analysis_stage: Literal[
        "pre_release",
        "post_release_manual",
        "post_release_final",
    ] = "pre_release"
    analysis_phase: Literal[
        "pre_release",
        "post_release_manual",
        "post_release_final",
    ] = "pre_release"
    report_id: Annotated[str, StringConstraints(max_length=96)] = ""
    input_hash: Annotated[
        str,
        StringConstraints(max_length=64, pattern=r"^(?:[0-9a-f]{64})?$"),
    ] = ""

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()


def earnings_report_id(payload: dict[str, Any]) -> str:
    """Build a stable report identity independent of model and prompt versions."""

    ticker = str(payload.get("ticker") or "").strip().upper()
    report_date = str(payload.get("earnings_date") or "").strip()
    year = payload.get("year")
    quarter = payload.get("quarter")
    year_part = str(year) if type(year) is int else "na"
    quarter_part = f"q{quarter}" if type(quarter) is int else "qna"
    return f"earnings:{ticker}:{report_date or 'undated'}:{year_part}:{quarter_part}"


def earnings_input_hash(payload: dict[str, Any]) -> str:
    """Hash only the bound earnings facts, excluding execution controls."""

    facts = {
        field: payload.get(field)
        for field in (
            "ticker",
            "name",
            "sector",
            "earnings_date",
            "year",
            "quarter",
            "eps_estimate",
            "eps_actual",
            "revenue_estimate",
            "revenue_actual",
            "market_cap",
            "release_status",
        )
    }
    raw = json.dumps(
        facts,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def normalize_earnings_analysis_payload(
    payload: dict[str, Any],
    *,
    analysis_stage: Literal[
        "pre_release",
        "post_release_manual",
        "post_release_final",
    ],
) -> dict[str, Any]:
    """Normalize server-bound facts and derive immutable analysis identities."""

    normalized = dict(payload)
    normalized.pop("force", None)
    has_actual = (
        normalized.get("eps_actual") is not None
        or normalized.get("revenue_actual") is not None
    )
    if analysis_stage != "pre_release" and not has_actual:
        raise ValueError("post_release_analysis_requires_actuals")
    if analysis_stage == "post_release_final" and not (
        (
            normalized.get("eps_actual") is not None
            and normalized.get("eps_estimate") is not None
        )
        or (
            normalized.get("revenue_actual") is not None
            and normalized.get("revenue_estimate") is not None
        )
    ):
        raise ValueError("final_earnings_analysis_requires_comparable_actuals")
    if analysis_stage == "pre_release" and has_actual:
        raise ValueError("pre_release_analysis_cannot_include_actuals")
    normalized["release_status"] = (
        "released"
        if has_actual
        else (
            "reported_pending_actual"
            if normalized.get("release_status") == "reported_pending_actual"
            else "scheduled"
        )
    )
    normalized["analysis_stage"] = analysis_stage
    normalized["analysis_phase"] = analysis_stage
    normalized["report_id"] = earnings_report_id(normalized)
    normalized["input_hash"] = earnings_input_hash(normalized)
    return EarningsImpactJobRequest.model_validate(normalized).model_dump(
        mode="json",
        exclude={"force"},
    )


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
    name: ZhCompanyName
    relation: Literal["competitor", "supplier", "customer", "etf", "opposing"]
    direction: Literal["bullish", "bearish", "mixed"]
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
        AfterValidator(validate_earnings_impact_reason),
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
    company: ZhCompanyName
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
    if job_type == "signal_analysis":
        # 证据包 v2 的上下文代码表（并入 allowed_codes）。自建载荷始终合规，
        # 这里的检查保护未来的其他调用方不把无界列表带进付费边界。
        if payload.get("context_tickers") is not None:
            tickers = _require_unique_string_list(
                payload,
                "context_tickers",
                max_items=24,
                max_length=12,
            )
            if any(
                _TICKER_PATTERN.fullmatch(ticker) is None for ticker in tickers
            ):
                raise ValueError("context_tickers_invalid")
        return
    if job_type not in {"earnings_impact", "option_alerts"}:
        raise ValueError("unsupported_job_type")


def _validation_source_texts(job_type: str, payload: dict) -> tuple[str, ...]:
    values: list[str] = []

    def collect(value: Any) -> None:
        if len(values) >= 200:
            return
        if isinstance(value, str):
            if value and value not in values:
                values.append(value)
            return
        if isinstance(value, dict):
            for nested in value.values():
                collect(nested)
            return
        if isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested)

    if job_type == "news_impact":
        for field in ("title", "summary", "source", "sources"):
            collect(payload.get(field))
    elif job_type == "market_focus":
        collect(payload.get("events"))
    elif job_type == "signal_analysis":
        collect(payload.get("signals"))
        collect(payload.get("scores"))
        # 证据包 v2 的上下文块。新闻标题/摘要先收集：模型引用其中的外文
        # 实体（公司、产品名）依赖 source-binding 豁免，截断顺序上它们
        # 最不能被 200 条上限挤掉。
        collect(payload.get("recent_news"))
        collect(payload.get("options_chain"))
        collect(payload.get("market_context"))
        collect(payload.get("macro_conditions"))
        collect(payload.get("upcoming_earnings"))
    return tuple(values)


# 信号引擎的相对基准池（services/signals.py 的 benchmarks，去掉 ^ 前缀的
# 指数代码）。每个 signal_analysis 输入都自带 vs 基准的对比读数，结果里
# 谈及基准（如「相对SPY走弱」）不是幻觉实体——不加进 allowed_codes 时，
# 基准代码后随中文谓语会被 ticker 绑定规则拒掉（2026-08-02 生产三连
# schema_validation_failed 根因之一）。
_SIGNAL_BENCHMARK_CODES = ("SPY", "QQQ", "IWM", "RSP", "HYG", "TLT")


def validate_result(job_type: str, raw_json: str, payload: dict) -> dict:
    model = result_model_for(job_type)
    if job_type in {"news_impact", "market_focus"}:
        raw_allowed_codes = list(payload.get("allowed_tickers") or [])
    elif job_type == "signal_analysis":
        # context_tickers 是证据包新闻块里实际出现过的代码（入队时经
        # validate_job_payload 校验有界）。分析引用新闻里的同行/对手代码
        # 不是幻觉实体，不并入会重演 2026-08-02 的 SPY 误杀。
        raw_allowed_codes = [
            payload.get("ticker"),
            *_SIGNAL_BENCHMARK_CODES,
            *(payload.get("context_tickers") or []),
        ]
    else:
        raw_allowed_codes = [payload.get("ticker")]
    allowed_codes = [
        str(code).strip().upper()
        for code in raw_allowed_codes or []
        if (
            isinstance(code, str)
            and str(code).strip()
            and _TICKER_PATTERN.fullmatch(str(code).strip()) is not None
        )
    ]
    result = model.model_validate_json(
        raw_json,
        context={
            "allowed_codes": allowed_codes,
            "source_texts": _validation_source_texts(job_type, payload),
        },
    )
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
