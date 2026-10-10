from __future__ import annotations

from collections import OrderedDict
import copy
import hashlib
import json
import re
import threading
import unicodedata
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any, Callable, Iterable, Literal, Optional

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

from app.json_validation import canonical_json_text


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
# Longest news body a news_impact payload may carry; the catalyst article
# extractor clips to it, so an extracted body never fails payload validation.
NEWS_ARTICLE_TEXT_MAX_CHARS = 12_000
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
# 已认可产品线加版本号，可带型号档位：「iPhone 18 Pro」「iPhone 18 Pro Max」
# （2026-10-09 Luna 被拒 2 次；机型名不在输入文本里时源绑定帮不上）。
_VERSIONED_PRODUCT = re.compile(
    r"(?P<base>[A-Za-z]+)[ -]?(?P<version>\d+(?:\.\d+)*)"
    r"(?:[ -](?:Pro|Max|Plus|Ultra|Mini|Air|SE|Ti))*"
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
# 2026-09-25 审计：期权、技术指标与财务常用缩写是分析里的标准写法（Call/Put
# 还是提示词自己用的词），一个缩写就让整份付费结果判 schema_validation_failed。
# 与其余白名单一样照旧走证券语境后检：「Delta股价」「OI股票」仍要求代码绑定。
_MARKET_TERM_ABBREVIATIONS = frozenset(
    {
        "ATM",
        "ATR",
        "CEO",
        "CFO",
        "COO",
        "CTO",
        "Call",
        "Call/Put",
        "ChatGPT",
        "CoWoS",
        "DXY",
        "Delta",
        "EMA",
        "Gamma",
        "ITM",
        "IV",
        "MACD",
        "NDX",
        "NYSE",
        "Non-GAAP",
        "OI",
        "OTM",
        "PCR",
        "Put",
        "Put/Call",
        "RSI",
        "SMA",
        "SPX",
        "Theta",
        "Vega",
        "YTD",
    }
)
# 指数代码与宏观统计缩写后接涨跌，描述的是指数或指标本身（「标普500指数
# （SPX）下跌」「美国9月CPI上涨0.6%」），不是个股行情；只有「股价」「股票」
# 这类证券名词仍要求代码绑定。WTI、ADP、LNG 同时是股票代码，不在其中。
# 基准利率名同理：「复合SOFR加1.730%」「SOFR上涨5个基点」说的是利率本身。
# 其他缩写后接「+3.5%」「减2%」是涨跌（「盘前TSLA +3.5%」），照旧要求代码绑定。
_RATE_BENCHMARK_NAMES = frozenset(
    {"ESTR", "EURIBOR", "HIBOR", "LIBOR", "SHIBOR", "SOFR", "SONIA", "TONA"}
)
_SELF_DESCRIBING_CODES = frozenset(
    {"CPI", "DXY", "GDP", "ISM", "JOLTS", "NDX", "NFP", "PCE", "PMI", "PPI", "SPX", "VIX"}
) | _RATE_BENCHMARK_NAMES
# SEC 文件编号。_FOREIGN_SPAN 从字母起算，「10-K」只切出单个字母「K」。
_SEC_FORM_DESIGNATIONS = ("10-K", "10-Q", "8-K")
# 频率数量（2026-10-10 生产取证：找回试运行里 MHz 被拒 7 次）。只认紧跟在数字
# 后面的 MHz、GHz（「600 MHz」「600MHz」「3.5GHz」），边界与经核验热点的单位
# 翻译 _VERIFIED_FOCUS_FREQUENCY 相同。本条不认的写法（不带数字的「频率MHz」、
# 紧挨公司或编号等标签的「公司800MHz」）原先被拒；2026-10-10 口径变更后它们不是
# 代码样词元，由 _is_term_like_span 按词条放行。
_FREQUENCY_QUANTITY = re.compile(
    r"(?<![A-Za-z0-9_.])[0-9]+(?:\.[0-9]+)?[ \t]*(?:MHz|GHz)(?![A-Za-z0-9_])"
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
        # 2026-08-09 生产误伤修正：期权/时间通用缩写。security 语境仍走
        # _approved_span_requires_ticker_binding 后检（「ET股价」照样要绑定）。
        "DTE",
        "EDT",
        "EST",
        "ET",
        "GMT",
        "UTC",
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
        "NFP",
        "PBOC",
        "PCE",
        "PEG",
        "PMI",
        # 2026-08-13 生产事故：CPI/PCE/PMI 在列偏偏漏了 PPI——写入时靠新闻
        # 源文本绑定放行，读取投影无源文本即被拒，完整焦点周期被整体隐藏。
        "PPI",
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
        # 2026-10-10：通用技术缩写（「输入URL」）。证券语境仍走
        # _approved_span_requires_ticker_binding 后检。
        "URL",
        # 2026-10-10 生产取证：PR #237 部署后 Luna 仍因「加州HMO合同」被拒；
        # 找回工具对 10-03 起失败任务的试运行里，CNBC 7 次，SUV 3 次，REIT 2 次，
        # ESG、FCC、NBC、LSEG 各 1–2 次。PPO 是与 HMO 并列的医保计划类型，一并
        # 加入。证券语境同样走 _approved_span_requires_ticker_binding 后检
        # （「ESG股价」仍要求绑定）。频率单位不在名单里，见 _FREQUENCY_QUANTITY。
        "CNBC",
        "ESG",
        "FCC",
        "HMO",
        "LSEG",
        "NBC",
        "PPO",
        "REIT",
        "SUV",
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
) | _MARKET_TERM_ABBREVIATIONS | _RATE_BENCHMARK_NAMES
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
# 括号里的主机名不是术语注释：「（sec.gov）」「（www.nvidia.com/zh-cn）」
# 「（GlobeNewswire.com）」「（info.gov.hk）」是来源标注，未经联网来源核对就
# 不能借注释位发布。只认以 www. 开头、或最后一段是下列顶级域名的主机，不区分
# 大小写，可带路径；co.uk、com.hk 这类多段后缀按最后一段判。js、py、md、sh、
# ts、go、rs、ai、io 这类同时是技术或产品后缀的不在表里，「（node.js）」
# 「（Character.AI）」照旧算注释。
_HOST_TOP_LEVEL_DOMAINS = (
    "com", "net", "org", "gov", "edu", "info", "biz", "news", "app", "xyz", "tv", "me",
    "ly", "us", "uk", "eu", "cn", "hk", "tw", "jp", "kr", "sg", "in", "id", "my", "th",
    "vn", "ph", "au", "nz", "ca", "mx", "br", "ar", "cl", "co", "de", "fr", "it", "es",
    "nl", "be", "at", "ch", "se", "no", "fi", "dk", "ie", "pt", "gr", "cz", "hu", "pl",
    "ru", "tr", "il", "ae", "sa", "za",
)
_BRACKETED_HOSTNAME = re.compile(
    r"(?:www\.[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*"
    rf"|[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.(?:{'|'.join(_HOST_TOP_LEVEL_DOMAINS)}))"
    r"(?:/\S*)?",
    re.IGNORECASE,
)
# IT 也是股票代码（高德纳），只有这些搭配才是信息技术的意思（「企业IT服务」）。
_IT_CONTEXT_SUFFIXES = (
    "服务", "支出", "行业", "系统", "板块", "部门",
    "基础设施", "预算", "投入", "架构", "运维", "人员", "资产", "解决方案",
)
# 罗马数字序号（2026-10-10 生产取证，找回试运行里 III 被拒 5 次）。III 也是
# 股票代码，所以不进名单，只在这些位置放行：前面紧挨「第」，后面紧跟期、级、
# 类、代（「第III期」「III期临床试验」「II类医疗器械」），或用斜杠、连字符接在
# 数字后面（「1/II期」）。证券语境仍要求绑定。IV 另在名单里（隐含波动率），
# 这条规则不改变它的结果。
_ROMAN_NUMERAL_ORDINALS = frozenset({"II", "III", "IV"})
_ROMAN_NUMERAL_ORDINAL_SUFFIXES = ("期", "级", "类", "代")
_ROMAN_NUMERAL_AFTER_NUMBER = re.compile(r"[0-9][/-]$")
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
_NUMERIC_CONTEXT_BOUNDARIES = frozenset("，,；;。.!！?？%％、")
# 数字后接「量级加币种或股」（「发现矿业股份5000万美元」「300万股」，2026-10-09
# 热点周期被拒），或直接接币种、百分号（「500美元」「股份5%」）时是数量，不是
# 证券代码。量级后面不是计量单位就不算（「600309 万华化学」「300014亿纬锂能」），
# 「股」后接东、本、权、份、票、价时也不是单位。
_CURRENCY_UNITS = r"美元|美分|港元|港币|欧元|日元|英镑|人民币|元"
_MAGNITUDE_UNIT = rf"[万亿千百]+[ \t]*(?:{_CURRENCY_UNITS}|股(?![东本权份票价]))"
_NUMERIC_QUANTITY_SUFFIX = re.compile(
    rf"^[ \t]*(?:{_MAGNITUDE_UNIT}|(?:{_CURRENCY_UNITS})|[%％])"
)
_MAGNITUDE_QUANTITY_SUFFIX = re.compile(rf"^[ \t]*{_MAGNITUDE_UNIT}")
# 这些词后面的数字一般是证券代码，数量写法不豁免（「股票600519万股」）；
# 0 开头的五位数（港股代码）在更前面就已判定。例外：「股票」后接「量级加股或
# 币种」、数字又不是六位数时是数量（「回购股票1000万股」「发行股票2亿股」）。
_NUMERIC_CODE_ONLY_PREFIX = re.compile(r"(?:代码|编号|股票|港股|个股)(?:为|是)?$")
# 「港股9888百度集团」：港股代码多为四五位。只对四五位数这样判定，「港股10月
# 以来」「港股3只科技股」里的月份、只数不受影响。
_HONG_KONG_CODE_PREFIX = re.compile(r"港股(?:代码|编号)?(?:为|是)?$")
# 数字后接量词、点位、量级或币种时说的是市场概况（「港股2600家上市公司」
# 「港股1500只个股下跌」「港股26000点附近震荡」「港股5000亿成交」），不是代码。
_HONG_KONG_COUNT_SUFFIX = re.compile(
    r"^[ \t]*(?:只|家|个|名|点|余|多|亿|万|年|月|日|%|％|港元|美元)"
)
# 分号隔开的是另一个分句：「流通股份；Hexa Creation聚焦……」里的「股份」不指向
# 分号后的名称。逗号仍连着同一分句，照旧计入证券语境。
_CLAUSE_BREAKS = frozenset("；;")
# 信用或量化评级的字母等级：「A+评级」「获A+每股收益修正量化评级」。
_LETTER_GRADE = re.compile(
    r"^(?:(?:[+＋]{1,2}|[\-－])[\u4e00-\u9fff]{0,12}?|)(?:评级|等级|评分)"
)
# 单个大写字母紧跟这些分类名词时是标签（2026-10-10 生产取证：「处方药D部分」
# 被拒；同类还有「V型反转」「A级」「B组」「C区」）。名词必须紧挨字母，中间只许
# 空格。隔着标点的「福特汽车F，部分分析师」和括号里的「福特汽车（F）」原先被拒，
# 2026-10-10 口径变更后由 _is_term_like_span 放行。「轮」「类」沿用原来的判断，
# 「股」仍只认 A、B、H。
_LETTER_LABEL_NOUNS = ("部分", "型", "级", "组", "区")
# 规则条款编号里括号中的小写字母：「规则5550(a)(2)」「规则10b5-1(c)」
# （2026-10-10 生产取证，Luna 被拒片段 'a'）。只认 ASCII 括号里的单个小写
# 字母，左括号前紧挨数字或上一个括号组，或右括号后紧跟下一个条款括号组。
_RULE_CLAUSE_NEXT_GROUP = re.compile(r"\([0-9A-Za-z]{1,4}\)")
# 统计量写法「p<0.001」「n=712例」「p值」，只认这些小写统计符号；大写单字母
# 可能是股票代码（「F>12美元」的 F 是福特汽车）。
_STATISTIC_LETTERS = frozenset("dknprt")
_STATISTIC_COMPARISON = re.compile(r"^[ \t]*(?:<=|>=|[<>=≤≥＜＞＝])[ \t]*[0-9]")
# 大写的 P、N 也常这样写（「P<0.001」「N=712例」），只在比较号后接数字、
# 数字后不接币种时算统计写法：「P<10美元」里的 P 仍可能是股票代码。
_UPPERCASE_STATISTIC_LETTERS = frozenset("NP")
_STATISTIC_COMPARISON_VALUE = re.compile(
    r"^[ \t]*(?:<=|>=|[<>=≤≥＜＞＝])[ \t]*(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
)
_CURRENCY_AMOUNT_TAIL = re.compile(rf"^[ \t]*[万亿千百]*[ \t]*(?:{_CURRENCY_UNITS})")
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
    # 「（WWW.SEC.GOV）」是主机名，不是缩写。全大写、不带 www. 的「ASP.NET」
    # 照旧按缩写放行。
    if span.casefold().startswith("www."):
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
        if sentence[before_index] in _CLAUSE_BREAKS:
            before_index = -1
            break
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
        # 单词条的全小写长实体也可源绑定：药名/代号惯例全小写（生产实测
        # berobenatide 在付费源里出现 2 次仍被形状检查拒掉，market_focus
        # 因此连挂）。prose word 已在上方拦截，≥6 字母排除短介词残留。
        if len(words) == 1 and letters.islower() and len(letters) >= 6:
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


def _is_sec_form_designation(sentence: str, *, end: int) -> bool:
    for form in _SEC_FORM_DESIGNATIONS:
        form_start = end - len(form)
        if form_start < 0 or sentence[form_start:end] != form:
            continue
        before = sentence[form_start - 1] if form_start > 0 else ""
        if not (before.isascii() and before.isalnum()):
            return True
    return False


def _code_names_the_statistic(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
) -> bool:
    if span not in _SELF_DESCRIBING_CODES:
        return False
    prefix = _normalize_security_reference_phrase(sentence[:start])
    if _SECURITY_REFERENCE_PREFIX.search(prefix) is not None:
        return False
    suffix = _normalize_security_reference_phrase(sentence[end:]).removeprefix(
        "的"
    )
    # 「CPI公司」「SOFR集团」指的是公司，不是指标。
    if suffix.startswith(_SECURITY_COMPANY_BRIDGES):
        return False
    return (
        _STOCK_PRICE_SUFFIX.match(suffix) is None
        and _SECURITY_NOUN_SUFFIX.match(suffix) is None
    )


def _next_to_code_or_company_label(text: str, start: int, end: int) -> bool:
    # Bare code/company labels are not all security phrases recognized by
    # the general language gate.
    prefix = _normalize_security_reference_phrase(text[:start])
    suffix = _normalize_security_reference_phrase(text[end:])
    return bool(
        re.search(r"(?:代码|编号|公司|集团|企业|股价)(?:为|是)?$", prefix)
        or suffix.startswith(("代码", "编号", "公司", "集团", "企业"))
    )


def _is_frequency_quantity(span: str, *, sentence: str, start: int, end: int) -> bool:
    if span.lstrip("0123456789") not in ("MHz", "GHz"):
        return False
    quantity = next(
        (
            match
            for match in _FREQUENCY_QUANTITY.finditer(sentence)
            if match.start() <= start and match.end() == end
        ),
        None,
    )
    return (
        quantity is not None
        and not _next_to_code_or_company_label(sentence, quantity.start(), end)
        and not _approved_span_requires_ticker_binding(
            quantity.group(0), sentence=sentence, start=quantity.start(), end=end,
        )
    )


def _is_rule_clause_letter(span: str, *, sentence: str, start: int, end: int) -> bool:
    if len(span) != 1 or not "a" <= span <= "z":
        return False
    if start < 1 or sentence[start - 1] != "(" or sentence[end : end + 1] != ")":
        return False
    before = sentence[start - 2] if start >= 2 else ""
    return (
        "0" <= before <= "9"
        or before == ")"
        or _RULE_CLAUSE_NEXT_GROUP.match(sentence, end + 1) is not None
    )


def _is_cjk_gloss_annotation(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
) -> bool:
    """「中文术语（Foreign）」注释位：紧跟中文、独占括号的紧凑外文标注。

    行业缩写常由模型自行补注（生产实测「电动垂直起降飞行器（eVTOL）」——
    eVTOL 不在付费源文本里，任何白名单都追不上这类构词）。约束：
    - 括号内只有该片段本身（左括号紧贴、右括号紧随），括号前一字符是中文；
    - 片段紧凑（≤24、无空白）且含小写字母或数字，或长度 ≥6；
    - 1–5 位全大写的代码形状**不进此通道**——未绑定证券代码不得借括号
      漂白，仍走代码绑定规则；prose word 同样拒绝；
    - 主机名（可带路径，_BRACKETED_HOSTNAME）也不进此通道。
    """

    if not 1 < len(span) <= 24 or any(char.isspace() for char in span):
        return False
    if not any(char.isalpha() for char in span):
        return False
    if _BRACKETED_HOSTNAME.fullmatch(span) is not None:
        return False
    if span.isupper() and len(span) <= 5:
        return False
    if not (any(char.islower() for char in span) or any(char.isdigit() for char in span) or len(span) >= 6):
        return False
    if span.casefold().rstrip(".") in _ENGLISH_PROSE_WORDS:
        return False
    before = sentence[:start]
    if not before or before[-1] not in "（(":
        return False
    closer = "）" if before[-1] == "（" else ")"
    after = sentence[end:]
    if not after or after[0] != closer:
        return False
    # 证券语境不进注释通道：「公司（Tesla）股价」仍要求代码绑定，括号
    # 不是绑定规则的免检口（对应既有 unbound_approved_brand 反例）。
    if _approved_span_requires_ticker_binding(
        span,
        sentence=sentence,
        start=start,
        end=end,
    ):
        return False
    head = before[:-1].rstrip()
    return bool(head) and _is_cjk(head[-1])


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
    if (
        span == "IT"
        and sentence[end:].lstrip(" \t").startswith(_IT_CONTEXT_SUFFIXES)
        and not _approved_span_requires_ticker_binding(
            span, sentence=sentence, start=start, end=end,
        )
    ):
        return True
    if (
        span in _ROMAN_NUMERAL_ORDINALS
        and (
            sentence[:start].rstrip(" \t").endswith("第")
            or sentence[end:].lstrip(" \t").startswith(
                _ROMAN_NUMERAL_ORDINAL_SUFFIXES
            )
            or _ROMAN_NUMERAL_AFTER_NUMBER.search(sentence[:start]) is not None
        )
        and not _approved_span_requires_ticker_binding(
            span, sentence=sentence, start=start, end=end,
        )
    ):
        return True
    if len(span) == 1 and span.isascii() and span.isupper():
        if _is_sec_form_designation(sentence, end=end):
            return True
        suffix = _strip_security_reference_separators(sentence[end:])
        if suffix.startswith(("股价", "股票")):
            return span in allowed_codes
        if suffix.startswith("股"):
            return span in {"A", "B", "H"} or span in allowed_codes
        if suffix.startswith(("轮", "类")):
            return True
        if sentence[end:].lstrip(" \t").startswith(
            _LETTER_LABEL_NOUNS
        ) and not _approved_span_requires_ticker_binding(
            span, sentence=sentence, start=start, end=end,
        ):
            return True
        if suffix.startswith("细胞"):
            return span in {"B", "T"}
        if suffix.startswith(("分数", "值", "统计量")):
            return True
        if _LETTER_GRADE.match(sentence[end:]) is not None:
            return True
        if span in _UPPERCASE_STATISTIC_LETTERS:
            value = _STATISTIC_COMPARISON_VALUE.match(sentence[end:])
            if value is not None and _CURRENCY_AMOUNT_TAIL.match(sentence[end + value.end():]) is None:
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
    if span in _STATISTIC_LETTERS and (
        sentence[end:].startswith(("值", "分数", "统计量"))
        or _STATISTIC_COMPARISON.match(sentence[end:]) is not None
    ):
        return True
    if _is_rule_clause_letter(span, sentence=sentence, start=start, end=end):
        return True
    if _is_frequency_quantity(span, sentence=sentence, start=start, end=end):
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
        ) and not _code_names_the_statistic(
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
        ) and not _code_names_the_statistic(
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
    if _is_cjk_gloss_annotation(span, sentence=sentence, start=start, end=end):
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


# 2026-10-10 口径变更（用户决定）：正文仍要求中文，但拉丁字母的专名和术语可以
# 保留原文，english_prose_not_allowed 只拦英文散文。上面的规则都没认下的片段，
# 由 _is_term_like_span 按「词条」再判一次：单个词元，或不超过 5 个词、每个词都是
# 首字母大写、全大写、数字或连接词的名称。证券语境的绑定只针对代码样词元
# （见 _CODE_LIKE_TOKEN）。
_TERM_MAX_WORDS = 5
_TERM_CONNECTORS = frozenset(
    {
        "&", "and", "co", "corp", "da", "de", "der", "du", "for", "inc", "la",
        "le", "llc", "ltd", "of", "plc", "the", "van", "von",
    }
)
_TERM_SUBWORD_SPLIT = re.compile(r"[\s\-./&+']+")
_JSON_LITERALS = frozenset({"false", "null", "true"})
# 涨跌词：原有 8 个，加上第一轮审查补过、复核撤回的 14 个。那 14 个放进通用
# 涨跌词表会误伤名单里的市场术语（「IV飙升」「RSI反弹」）；这里只对没有被任何
# 规则认下的片段生效，名单里的术语走不到这一步。前面可以隔两个字的副词
# （「T-Mobile US此前下跌约5.4%」）。
_TERM_MOVEMENT_WORDS = (
    "上涨", "下跌", "涨停", "跌停", "走强", "走弱", "收涨", "收跌",
    "大涨", "大跌", "暴涨", "暴跌", "急涨", "急跌", "飙升", "重挫", "跳水",
    "拉升", "走高", "走低", "下挫", "反弹",
)
_TERM_MOVEMENT = re.compile(
    "[\u4e00-\u9fff]{0,2}?(?:" + "|".join(_TERM_MOVEMENT_WORDS) + ")"
)
_TERM_TRAILING_ALIAS = re.compile(r"[（(][^（()）]{1,24}[）)]")
# 代码样词元：1 到 5 个大写字母，按空格切分，点号、连字符连起来的不拆开。
# 2026-10-10 第三轮起只有它们在证券语境要求代码绑定；混合大小写、带小写或带点号
# 的名称（CleanSpark、C3.ai、Polymesh、BRK.B）紧挨股价、涨跌词也按词条放行。证券语境指：原有的股价、股票等判断，后面两个字以内接涨跌词，后面是
# 「股」「涨」「跌」或公司、集团、企业（中间只隔标点、空格或数字），以及后接带
# 符号的百分比、基点（「盘前TSLA +3.5%」）和价格比较（「F>12美元」）。「公司名
# （代码）」里的代码和「代码为X」不再算证券语境。
_CODE_LIKE_TOKEN = re.compile(r"[A-Z]{1,5}")
_CODE_SIGNED_MOVE = re.compile(
    r"[ \t]*(?:[+＋\-－−]|加|减)[ \t]*[0-9]+(?:\.[0-9]+)?[ \t]*(?:%|％|个?基点)"
)
_CODE_PRICE_COMPARISON = re.compile(
    r"[ \t]*(?:<=|>=|[<>=≤≥＜＞＝])[ \t]*[0-9]+(?:\.[0-9]+)?[ \t]*[万亿千百]*[ \t]*"
    rf"(?:{_CURRENCY_UNITS})"
)


def _is_name_word(word: str) -> bool:
    return (
        word.casefold() in _TERM_CONNECTORS
        or word[0].isdigit()
        or any(char.isupper() for char in word)
    )


def _is_english_prose_span(span: str) -> bool:
    """超过 5 个词、多词里有小写普通词，或出现两个以上普通英文词（含连字符、
    点号、斜杠连起来的「market-rally」）。普通英文词指三个字母以上的小写词，
    或英文标题词表里的词。"""

    words = span.split()
    if len(words) > _TERM_MAX_WORDS:
        return True
    if len(words) > 1 and not all(_is_name_word(word) for word in words):
        return True
    prose_words = 0
    for word in _TERM_SUBWORD_SPLIT.split(span):
        folded = word.casefold()
        if not word or folded in _TERM_CONNECTORS:
            continue
        if (
            word.isalpha() and word.islower() and len(word) >= 3
        ) or folded in _ENGLISH_PROSE_WORDS:
            prose_words += 1
    return prose_words >= 2


def _code_like_token_in_security_context(
    token: str, *, sentence: str, start: int, end: int,
) -> bool:
    if _approved_span_requires_ticker_binding(
        token, sentence=sentence, start=start, end=end,
    ):
        return True
    rest = sentence[end:].lstrip(" \t")
    alias = _TERM_TRAILING_ALIAS.match(rest)
    if alias is not None:
        rest = rest[alias.end() :]
    return (
        _TERM_MOVEMENT.match(_strip_security_reference_separators(rest)) is not None
        or _strip_security_reference_separators(sentence[end:]).startswith(("股", "涨", "跌"))
        or _normalize_security_reference_phrase(sentence[end:])
        .removeprefix("的")
        .startswith(_SECURITY_COMPANY_BRIDGES)
        or _CODE_SIGNED_MOVE.match(sentence, end) is not None
        or _CODE_PRICE_COMPARISON.match(sentence, end) is not None
    )


def _term_in_security_context(span: str, *, sentence: str, start: int, end: int) -> bool:
    return any(
        _CODE_LIKE_TOKEN.fullmatch(token.group(0)) is not None
        and _code_like_token_in_security_context(
            token.group(0),
            sentence=sentence,
            start=start + token.start(),
            end=start + token.end(),
        )
        for token in re.finditer(r"\S+", span)
    )


def _payload_field_name_echo(
    sentence: str, start: int, end: int, payload_field_names: frozenset[str],
) -> bool:
    def part(char: str) -> bool:
        return char.isascii() and (char.isalnum() or char == "_")

    while start > 0 and part(sentence[start - 1]):
        start -= 1
    while end < len(sentence) and part(sentence[end]):
        end += 1
    return sentence[start:end] in payload_field_names and not (
        _approved_span_requires_ticker_binding(
            sentence[start:end], sentence=sentence, start=start, end=end,
        )
    )


def _is_term_like_span(
    span: str,
    *,
    sentence: str,
    start: int,
    end: int,
    source_texts: tuple[str, ...],
    payload_field_names: frozenset[str] = frozenset(),
) -> bool:
    if not any(_is_cjk(char) for char in sentence):
        return False
    # 下划线连起来的字段名（「my_article_status」）不是词条。例外：财报照抄本条载荷
    # 的字段名（「release_status」「eps_actual」，2026-10-10 第三轮），且不在证券语境。
    if (sentence[start - 1 : start] == "_" or sentence[end : end + 1] == "_") and not (
        _payload_field_name_echo(sentence, start, end, payload_field_names)
    ):
        return False
    # 网址和主机名不是词条。
    if "://" in (sentence[max(0, start - 3) : start], sentence[end : end + 3]):
        return False
    if _BRACKETED_HOSTNAME.fullmatch(span) is not None:
        return False
    if _is_copied_source_headline_fragment(span, source_texts):
        return False
    if _is_english_prose_span(span) or span.casefold() in _JSON_LITERALS:
        return False
    return not _term_in_security_context(
        span, sentence=sentence, start=start, end=end,
    )


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
    if (
        _NUMERIC_QUANTITY_SUFFIX.match(sentence[end:]) is not None
        and _NUMERIC_CODE_ONLY_PREFIX.search(prefix) is None
    ):
        return False
    if (
        len(span) != 6
        and prefix.endswith("股票")
        and _MAGNITUDE_QUANTITY_SUFFIX.match(sentence[end:]) is not None
    ):
        return False
    if (
        len(span) in (4, 5)
        and _HONG_KONG_CODE_PREFIX.search(prefix) is not None
        and _NUMERIC_QUANTITY_SUFFIX.match(sentence[end:]) is None
        and _HONG_KONG_COUNT_SUFFIX.match(sentence[end:]) is None
    ):
        return True
    return (
        _SECURITY_REFERENCE_PREFIX.search(prefix) is not None
        or suffix.startswith(_SECURITY_CODE_SUFFIXES)
        or suffix.startswith("这只股票")
        or (
            len(span) == 6
            and suffix.startswith(_SECURITY_PRICE_MOVEMENTS)
        )
    )


def _english_prose_error(fragment: str) -> ValueError:
    # 带上被拒片段：error_detail 能直接看出是哪段外文触发了规则，排障不必
    # 再重取付费响应复现（2026-09-25 审计）。
    return ValueError(f"english_prose_not_allowed: {fragment.strip()[:80]!r}")


def _longest_unbound_foreign_span(
    text: str,
    source_texts: tuple[str, ...],
) -> str:
    spans = [
        match.group(0)
        for match in _FOREIGN_SPAN.finditer(text)
        if not _is_source_bound_foreign_entity(match.group(0), source_texts)
    ]
    return max(spans, key=len, default=text)


def _news_source_bound_name(
    span: str, sentence: str, start: int, end: int, payload: dict,
) -> bool:
    """News-only product series and legal names, bound to the supplied source."""
    sources = _validation_source_texts("news_impact", payload)
    if re.search(r"(?:股票代码|证券代码|交易代码|代码)[：:、\s]*$", sentence[:start]):
        return False
    if re.fullmatch(r"[A-Z]", span) and sentence[end:].startswith("系列"):
        pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(span)} series(?![A-Za-z0-9])"
        )
        return any(pattern.search(source) for source in sources)
    # Only a compact hyphenated legal name, never a code or a headline clause.
    if not 3 <= len(span) <= 60 or not re.fullmatch(
        r"[A-Z][A-Za-z]*-[A-Za-z]+(?: [A-Z][A-Za-z]+){0,3}", span,
    ):
        return False
    if not _is_source_bound_foreign_entity(span, sources):
        return False
    pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(span)},? "
        r"(?:Inc\.|Ltd\.|Corporation|Limited)(?![A-Za-z0-9])"
    )
    return any(pattern.search(source) for source in sources)


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
    context = (
        info.context
        if info is not None and isinstance(info.context, dict)
        else None
    )
    context_codes = (
        context.get("allowed_codes", ()) if context is not None else allowed_codes
    )
    normalized_codes = frozenset(
        str(code).strip().upper()
        for code in context_codes
        if isinstance(code, str) and str(code).strip()
    )
    context_source_texts = (
        context.get("source_texts", ()) if context is not None else source_texts
    )
    source_texts = tuple(
        source
        for source in context_source_texts
        if isinstance(source, str) and source
    )
    news_payload = context.get("news_payload") if context is not None else None
    payload_field_names = frozenset(
        context.get("payload_field_names", ()) if context is not None else ()
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
        raise _english_prose_error(
            _longest_unbound_foreign_span(scan_text, source_texts)
        )
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
            raise _english_prose_error(
                _longest_unbound_foreign_span(sentence, source_texts)
            )
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
            if isinstance(news_payload, dict) and _news_source_bound_name(
                match.group(0), sentence, match.start(), match.end(), news_payload,
            ):
                continue
            if _is_term_like_span(
                match.group(0),
                sentence=sentence,
                start=match.start(),
                end=match.end(),
                source_texts=source_texts,
                payload_field_names=payload_field_names,
            ):
                continue
            raise _english_prose_error(match.group(0))
        if sentence_latin >= 16 and sentence_cjk == 0:
            raise _english_prose_error(sentence)

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
    raw_source_texts = (
        info.context.get("source_texts", ())
        if isinstance(info.context, dict)
        else ()
    )
    impacted_code = (
        info.data.get("ticker") if isinstance(info.data, dict) else None
    )
    return validate_simplified_chinese_text(
        value,
        None,
        allowed_codes=[*source_codes, impacted_code],
        source_texts=(
            raw_source_texts
            if isinstance(raw_source_texts, (list, tuple))
            else ()
        ),
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
    return hashlib.sha256(canonical_json_text(facts).encode("utf-8")).hexdigest()


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
    # 输入只有代码、名称、行业与 EPS/营收且不许联网；下限 4 逼模型给冷门股
    # 凑数、编造中文名，付费后被拒（2026-09-25 审计）。改下限会移动结构化输出
    # 的 minItems，必须同步升 schema_name 与 PROMPT_VERSIONS。
    impacted: list[EarningsImpactItem] = Field(min_length=1, max_length=8)

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


_HTTP_STATUS_LABELS = {
    "401": "未获授权",
    "403": "访问被拒绝",
    "404": "页面不存在",
    "410": "页面已移除",
    "429": "请求过于频繁",
}


def _http_status_label(code: str) -> str:
    return _HTTP_STATUS_LABELS.get(code) or (
        "服务器错误" if code.startswith("5") else "访问失败"
    )


# 新闻任务自己的字段名与状态值在发布文本里的中文说法。只替换与本条载荷字段名、
# 正文字段名、结果字段名或抓取状态值逐字相同的词；其他英文照旧交给中文校验。
_NEWS_FIELD_LABELS = {
    "article": "新闻正文",
    "article_status": "正文状态",
    "article_reason": "正文缺失原因",
    "text": "正文",
    "source": "来源",
    "sources": "来源",
    "source_url": "来源网址",
    "url": "网址",
    "title": "标题",
    "summary": "摘要",
    "allowed_tickers": "允许股票代码名单",
    "affected_stocks": "受影响个股",
    "confidence": "置信度",
    "classification": "判断类别",
    "insufficient_context": "证据不足",
}
_NEWS_STATUS_LABELS = {
    "available": "可用",
    "unavailable": "不可用",
    "not_requested": "未请求",
    "truncated": "已截断",
    "timeout": "超时",
    "paywall": "付费墙",
    "challenge_page": "访问验证页",
    "dns_error": "域名解析失败",
    "unsafe_url": "网址不安全",
    "unsafe_address": "地址不安全",
    "unsafe_request": "请求不安全",
    "unsupported_encoding": "编码不支持",
    "unsupported_content_type": "内容类型不支持",
    "response_too_large": "响应过大",
    "incomplete_response": "响应不完整",
    "no_matching_article_body": "未找到匹配正文",
    "publisher_url_unavailable": "发布方网址不可用",
    "redirect_limit": "重定向次数超限",
    "fetch_failed": "抓取失败",
}


def _news_identifier_translations(payload: dict) -> dict[str, str]:
    article = payload.get("article")
    article = article if isinstance(article, dict) else {}
    fields = {
        *payload, *article, "article", "article_status", "article_reason",
        *NewsImpactResult.model_fields,
    }
    translations = {
        name: label for name, label in _NEWS_FIELD_LABELS.items() if name in fields
    }
    statuses = {
        payload.get("article_status"), payload.get("article_reason"), article.get("status"),
    }
    if article or payload.get("truncated_fields"):
        statuses.add("truncated")
    translations.update(
        {value: label for value, label in _NEWS_STATUS_LABELS.items() if value in statuses}
    )
    reason = re.fullmatch(r"http_([1-5][0-9]{2})", str(payload.get("article_reason") or ""))
    if reason is not None:
        translations[reason.group(0)] = f"状态码{reason.group(1)}"
    return translations


def _translate_news_metadata(value: str, payload: dict) -> str:
    """Translate only known input labels; never rewrite facts or output keys."""
    # 翻译只修正中文句子里夹带的字段名；一句汉字都没有的文本（「title summary
    # source url」）不拼成中文词串发布，原样交给中文校验拒绝。
    if not any(_is_cjk(char) for char in value):
        return value
    # 含英文散文的文本也不逐词翻译（2026-10-10 口径变更后）：「article text
    # truncated, source title available」译完只剩一个英文词，就会被当成词条放行。
    if any(_is_english_prose_span(match.group(0)) for match in _FOREIGN_SPAN.finditer(value)):
        return value
    translations = _news_identifier_translations(payload)
    # Names are exact ASCII tokens, not substrings of an unknown program label.
    tokens = "|".join(sorted(map(re.escape, translations), key=len, reverse=True))
    pattern = re.compile(r"(?<![A-Za-z0-9_])(" + tokens + r")(?![A-Za-z0-9_])")

    def replace(match: re.Match[str]) -> str:
        if any(url.start() <= match.start() < url.end() for url in re.finditer(r"https?://[^\s，。；）]+", value)):
            return match.group(0)
        if _approved_span_requires_ticker_binding(match.group(0), sentence=value, start=match.start(), end=match.end()):
            return match.group(0)
        return translations[match.group(0)]
    value = pattern.sub(replace, value)
    value = re.sub(r"((?:商业|业务)发展公司)[（(]BDC[）)]", r"\1", value)
    # 只翻译与本条输入抓取失败一致的状态码：正文因 http_401 不可用时「HTTP 401」
    # 是事实复述，状态码对不上（或输入没有失败记录）时原样留给校验器拒绝。
    status = re.fullmatch(r"http_([1-5][0-9]{2})", str(payload.get("article_reason") or ""))
    if payload.get("article_status") == "unavailable" and status is not None:
        code = status.group(1)
        value = re.sub(
            rf"(?<![A-Za-z0-9_])HTTP[ \t]*(?:状态码)?[ \t]*{code}(?![0-9])",
            f"{_http_status_label(code)}（状态码{code}）",
            value,
            flags=re.IGNORECASE,
        )
    sources = _validation_source_texts("news_impact", payload)
    if any(re.search(r"\bSarbanes[-– ]Oxley\b", source, re.I) for source in sources):
        def translate_regulation(match: re.Match[str]) -> str:
            if _approved_span_requires_ticker_binding("SOX", sentence=value, start=match.start(), end=match.end()):
                return match.group(0)
            return "《萨班斯—奥克斯利法案》"
        value = re.sub(r"(?<![A-Za-z0-9_])SOX(?=认证)", translate_regulation, value)
    return value


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

    @model_validator(mode="before")
    @classmethod
    def translate_news_input_labels(cls, value: Any, info: ValidationInfo) -> Any:
        payload = info.context.get("news_payload") if isinstance(info.context, dict) else None
        if not isinstance(value, dict) or not isinstance(payload, dict):
            return value
        result = dict(value)
        for name in ("title_zh", "summary_zh", "headline_summary", "causal_summary"):
            if isinstance(result.get(name), str):
                result[name] = _translate_news_metadata(result[name], payload)
        for name in ("key_factors", "uncertainty_notes", "affected_sectors"):
            if isinstance(result.get(name), list):
                result[name] = [
                    _translate_news_metadata(item, payload)
                    if isinstance(item, str) else item
                    for item in result[name]
                ]
        for name in ("affected_stocks", "affected_commodities"):
            if isinstance(result.get(name), list):
                result[name] = [
                    {**item, "reason": _translate_news_metadata(item["reason"], payload)}
                    if isinstance(item, dict) and isinstance(item.get("reason"), str)
                    else item
                    for item in result[name]
                ]
        return result

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


# 只翻译输入已绑定的宏观程序状态；中间从句限定为分数与日期，不能借同句
# 的「宏观环境」把另一家公司的 active 状态或英文正文一起放行。
_MACRO_ACTIVE_DESCRIPTION = re.compile(
    r"(宏观环境(?:块)?(?:"
    r"综合分为[0-9]+(?:\.[0-9]+)?，"
    r"处于[\u4e00-\u9fff]{1,10}区间，"
    r"7日变化为-?[0-9]+(?:\.[0-9]+)?，"
    r"数据截至[0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日，"
    r")?状态为)active(?=\s*(?:[，。！？!?；;\n]|$))"
)


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

    @field_validator("summary_zh", "market_summary", mode="before")
    @classmethod
    def translate_bound_macro_status(cls, value: Any, info: ValidationInfo) -> Any:
        if (
            isinstance(value, str)
            and isinstance(info.context, dict)
            and info.context.get("macro_conditions_status") == "active"
        ):
            def translate(match: re.Match[str]) -> str:
                # 先保留原有证券语境检查，不能把「active，股票代码」等
                # 引用抢先译掉，再以纯中文绕过绑定要求。
                if _approved_span_requires_ticker_binding(
                    "active", sentence=value,
                    start=match.end() - len("active"), end=match.end(),
                ):
                    return match.group(0)
                return f"{match.group(1)}有效"

            return _MACRO_ACTIVE_DESCRIPTION.sub(translate, value)
        return value

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


FOCUS_VERIFICATION_VERSION = "web-evidence-v1"


class FocusEvidenceReference(StrictModel):
    tool_use_id: Optional[Annotated[str, StringConstraints(min_length=1, max_length=256)]]
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    relation: Literal["supports", "contradicts"]


class FocusEventVerification(StrictModel):
    event_group_id: Annotated[str, StringConstraints(min_length=1, max_length=100)]
    event_group_version: StrictInt = Field(ge=1)
    verdict: Literal["supported", "contradicted", "unverifiable"]
    evidence_refs: list[FocusEvidenceReference] = Field(max_length=20)
    # These independent, event-bound texts are the only news prose published.
    # Global prose in the paid output stays available in the internal audit.
    title_zh: ZhShortText
    summary_zh: ZhBoundedText
    affected_sectors: list[ZhShortText] = Field(max_length=10)


_VERIFIED_FOCUS_METADATA = re.compile(
    r"(?<![A-Za-z0-9_])(?:as_of|catalyst_bias)(?![A-Za-z0-9_])"
)
_VERIFIED_FOCUS_BANDWIDTH = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<quantity>[0-9]+(?:\.[0-9]+)?)[ \t]*Gbps(?![A-Za-z0-9_])"
)
_VERIFIED_FOCUS_FREQUENCY = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<quantity>[0-9]+(?:\.[0-9]+)?)[ \t]*(?P<unit>Hz|kHz|MHz|GHz|THz)(?![A-Za-z0-9_])"
)
_VERIFIED_FOCUS_FREQUENCY_UNITS = {
    "Hz": "赫兹", "kHz": "千赫", "MHz": "兆赫", "GHz": "吉赫", "THz": "太赫",
}
_VERIFIED_FOCUS_TMUS_NAME = re.compile(
    r"(?<![A-Za-z0-9_])T-Mobile US(?![A-Za-z0-9_])(?P<binding> ?(?:（TMUS）|\(TMUS\)))?"
)
_VERIFIED_FOCUS_COMPANY_HEADING = re.compile(
    r"(?P<boundary>^|[。！？!?；;\n])(?P<space>[ \t]*)\((?P<ordinal>[1-9])\)(?=公司类事件[：:])"
)
_VERIFIED_FOCUS_URL = re.compile(r"https?://\S+", re.IGNORECASE)


def _translate_verified_focus_prose(value: str) -> str:
    """Localize known prose labels, never security references or source URLs."""
    def in_url(text: str, start: int) -> bool:
        return any(match.start() <= start < match.end() for match in _VERIFIED_FOCUS_URL.finditer(text))

    def protected(text: str, start: int, end: int) -> bool:
        if in_url(text, start) or _approved_span_requires_ticker_binding(
            text[start:end], sentence=text, start=start, end=end,
        ):
            return True
        # Do not turn foreign names next to a code or company label Chinese.
        return _next_to_code_or_company_label(text, start, end)

    translations = {"as_of": "分析截止时点", "catalyst_bias": "催化因素倾向评分"}
    original = value
    value = _VERIFIED_FOCUS_METADATA.sub(
        lambda match: match.group(0) if protected(original, match.start(), match.end()) else translations[match.group(0)],
        value,
    )
    original = value
    value = _VERIFIED_FOCUS_BANDWIDTH.sub(
        lambda match: match.group(0) if protected(original, match.start(), match.end()) else match.group("quantity") + "吉比特每秒",
        value,
    )
    original = value
    value = _VERIFIED_FOCUS_FREQUENCY.sub(
        lambda match: match.group(0) if protected(original, match.start(), match.end()) else (
            match.group("quantity") + _VERIFIED_FOCUS_FREQUENCY_UNITS[match.group("unit")]
        ),
        value,
    )
    original = value
    # Only the observed company-event heading grammar, not parenthesized
    # numeric company names or security codes such as (700)公司.
    value = _VERIFIED_FOCUS_COMPANY_HEADING.sub(
        lambda match: match.group(0) if in_url(original, match.start()) else (
            match.group("boundary") + match.group("space")
            + "（" + "一二三四五六七八九"[int(match.group("ordinal")) - 1] + "）"
        ),
        value,
    )
    return value


def _translate_verified_focus_tmus_name(value: str, *, assessment: bool = False) -> str:
    """The caller requires input-allowed TMUS; global prose also needs (TMUS)."""
    def replace(match: re.Match) -> str:
        if not match.group("binding") and (
            not assessment or value[match.end():].lstrip().startswith(("（", "("))
        ):
            return match.group(0)
        if any(url.start() <= match.start() < url.end() for url in _VERIFIED_FOCUS_URL.finditer(value)):
            return match.group(0)
        prefix = _normalize_security_reference_phrase(value[:match.start()])
        suffix = _normalize_security_reference_phrase(value[match.end():])
        if re.search(r"(?:代码|编号)(?:为|是)?$", prefix) or suffix.startswith(("代码", "编号")):
            return match.group(0)
        return "TMUS"
    return _VERIFIED_FOCUS_TMUS_NAME.sub(replace, value)


class VerifiedMarketFocusResult(MarketFocusResult):
    event_verifications: list[FocusEventVerification] = Field(max_length=200)

    @model_validator(mode="before")
    @classmethod
    def translate_known_prose(cls, value: Any, info: ValidationInfo) -> Any:
        if not isinstance(value, dict):
            return value

        allowed_codes = (info.context or {}).get("allowed_codes", ()) if info else ()

        def translate_fields(item: dict, text_fields: tuple[str, ...], list_fields: tuple[str, ...], *, tmus_assessment: bool = False) -> dict:
            def translate(text: str) -> str:
                text = _translate_verified_focus_prose(text)
                return _translate_verified_focus_tmus_name(text, assessment=tmus_assessment) if "TMUS" in allowed_codes else text

            translated = dict(item)
            for name in text_fields:
                if isinstance(translated.get(name), str):
                    translated[name] = translate(translated[name])
            for name in list_fields:
                if isinstance(translated.get(name), list):
                    translated[name] = [
                        translate(text) if isinstance(text, str) else text
                        for text in translated[name]
                    ]
            return translated

        result = translate_fields(
            value, ("title_zh", "summary_zh", "headline_summary", "market_summary"),
            ("market_uncertainties", "affected_sectors"),
        )
        for name, text_fields, list_fields in (
            ("dominant_events", ("summary",), ("affected_sectors",)),
            ("focus_ticker_assessments", ("summary",), ("risks",)),
            ("event_verifications", ("title_zh", "summary_zh"), ("affected_sectors",)),
        ):
            if isinstance(result.get(name), list):
                result[name] = [
                    translate_fields(
                        item, text_fields, list_fields,
                        tmus_assessment=(name == "focus_ticker_assessments" and item.get("ticker") == "TMUS" and "TMUS" in allowed_codes),
                    ) if isinstance(item, dict) else item
                    for item in result[name]
                ]
        return result


def validate_market_focus_evidence(
    result: dict, payload: dict, tool_evidence: list[dict] | None,
) -> None:
    """Bind verdicts to the immutable input and actual successful web receipts.

    This verifies provenance, not real-world truth: the model still evaluates
    whether a retrieved source supports the individual claim.
    """
    if payload.get("verification_version") != FOCUS_VERIFICATION_VERSION:
        return
    from app.services.ai_jobs.claude_provider import _public_source_url

    expected = {
        event["event_group_id"]: event["event_group_version"]
        for event in payload.get("events", [])
        if isinstance(event, dict)
    }
    verifications = result.get("event_verifications", [])
    actual = {entry["event_group_id"]: entry["event_group_version"] for entry in verifications}
    if len(actual) != len(verifications) or actual != expected:
        raise ValueError("market_focus_verification_event_mismatch")
    receipts = {
        (entry.get("tool_use_id"), entry.get("url"))
        for entry in tool_evidence or []
        if isinstance(entry, dict)
        and isinstance(entry.get("tool_use_id"), str)
        and bool(entry["tool_use_id"])
        and entry.get("status") == "success"
        and entry.get("tool_name") in {"web_search", "web_fetch"}
        and isinstance(entry.get("content_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", entry["content_sha256"])
        and _public_source_url(entry.get("url")) == entry.get("url")
    }
    for entry in verifications:
        refs = entry["evidence_refs"]
        for ref in refs:
            normalized_url = _public_source_url(ref["url"])
            if ref["tool_use_id"] is None:
                # The model may not see server-generated IDs. Resolve only a
                # unique successful call in this exact persisted receipt.
                candidates = {call_id for call_id, url in receipts if url == normalized_url}
                if len(candidates) != 1:
                    raise ValueError("market_focus_verification_evidence_ambiguous")
                ref["tool_use_id"] = candidates.pop()
            if (ref["tool_use_id"], normalized_url) not in receipts:
                raise ValueError("market_focus_verification_evidence_unbound")
            ref["url"] = normalized_url
        keys = [(ref["tool_use_id"], ref["url"]) for ref in refs]
        if len(keys) != len(set(keys)):
            raise ValueError("market_focus_verification_duplicate_evidence")
        if any(key not in receipts for key in keys):
            raise ValueError("market_focus_verification_evidence_unbound")
        required = {"supported": "supports", "contradicted": "contradicts"}.get(entry["verdict"])
        if required and not any(ref["relation"] == required for ref in refs):
            raise ValueError("market_focus_verification_evidence_missing")


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
    # 失败诊断细节（如 pydantic 校验的字段路径与规则消息）。仅 owner 可见：
    # API 层对非 owner 置空。
    error_detail: Optional[
        Annotated[str, StringConstraints(max_length=2000)]
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
    evidence_sources: list[dict[str, str]] = Field(default_factory=list, max_length=10)


class CancelRequest(StrictModel):
    confirm: StrictBool = True


def result_model_for(
    job_type: str, *, payload: dict | None = None, model: str | None = None,
) -> type[BaseModel]:
    if job_type == "earnings_impact":
        return EarningsImpactResult
    if job_type == "option_alerts":
        return OptionAlertResult
    if job_type == "signal_analysis":
        return SignalAnalysisResult
    if job_type == "news_impact":
        return NewsImpactResult
    if job_type == "market_focus":
        if (payload or {}).get("verification_version") == FOCUS_VERIFICATION_VERSION or (
            payload is None and isinstance(model, str) and "sonnet" in model
        ):
            return VerifiedMarketFocusResult
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


class InvalidJobPayloadError(ValueError):
    """The queued input itself is malformed; the model output is not at fault."""


def validate_job_payload(job_type: str, payload: dict) -> None:
    """Validate identities needed to bind paid output to its local snapshot."""

    try:
        _validate_job_payload_identities(job_type, payload)
    except ValueError as exc:
        # 入队 payload 自身不合法与「模型输出不合规」分开归类：两者都记成
        # schema_validation_failed 时，排障会先去翻模型输出（2026-09-25 审计）。
        raise InvalidJobPayloadError(str(exc)) from exc


def _validate_job_payload_identities(job_type: str, payload: dict) -> None:
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
        article = payload.get("article")
        if payload.get("article_status") not in {None, "available", "unavailable", "not_requested"}:
            raise ValueError("article_status_invalid")
        if article is not None:
            if (
                not isinstance(article, dict)
                or article.get("status") != "available"
                or payload.get("article_status") != "available"
                or not isinstance(article.get("text"), str)
                or not 1 <= len(article["text"]) <= NEWS_ARTICLE_TEXT_MAX_CHARS
                or not isinstance(article.get("source_url"), str)
                or not article["source_url"].startswith("https://")
                or type(article.get("truncated")) is not bool
            ):
                raise ValueError("article_content_invalid")
            _aware_utc_instant(article.get("fetched_at"))
        elif payload.get("article_status") == "available":
            raise ValueError("article_content_missing")
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
        marker = payload.get("verification_version")
        if marker is not None:
            if marker != FOCUS_VERIFICATION_VERSION:
                raise ValueError("market_focus_verification_version_invalid")
            events = payload.get("events")
            if not isinstance(events, list) or len(events) > 200:
                raise ValueError("market_focus_verification_events_invalid")
            event_ids = []
            for event in events:
                if not isinstance(event, dict):
                    raise ValueError("market_focus_verification_events_invalid")
                event_ids.append(_require_identity_text(event, "event_group_id", max_length=100))
                _require_identity_integer(event, "event_group_version")
            if len(set(event_ids)) != len(event_ids) or set(event_ids) != set(payload["allowed_event_group_ids"]):
                raise ValueError("market_focus_verification_events_invalid")
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
        article = payload.get("article")
        if isinstance(article, dict):
            collect(article.get("text"))
    elif job_type == "market_focus":
        collect(payload.get("events"))
    elif job_type == "option_alerts":
        # 异动的类型、价内外、理由等原样来自输入；复述其中的专有写法（如
        # 理由里的 Sweep）不是英文叙述。
        collect(payload.get("alerts"))
    elif job_type == "earnings_impact":
        # 输入里的公司名与行业本来就是英文；复述它们被判英文叙述是付费后
        # 被拒的主要来源之一（2026-09-25 审计：「Micron Technology本季度…」）。
        collect(payload.get("name"))
        collect(payload.get("sector"))
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
    model = result_model_for(job_type, payload=payload)
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
            "news_payload": payload if job_type == "news_impact" else None,
            "source_texts": _validation_source_texts(job_type, payload),
            # 只有财报没有字段名翻译，照抄的载荷字段名按词条放行（新闻先翻译）。
            "payload_field_names": (
                frozenset(map(str, payload)) if job_type == "earnings_impact" else frozenset()
            ),
            "macro_conditions_status": (
                payload["macro_conditions"].get("status")
                if job_type == "market_focus"
                and isinstance(payload.get("macro_conditions"), dict)
                else None
            ),
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
        if not 1 <= len(data["impacted"]) <= 8:
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
        # 身份只比对 news_id 与 change_sequence。content_hash 是 64 位十六进制
        # 摘要，模型照抄时会漏位、多位或改错一位（2026-10-03 起 Luna 约 13 条
        # 新闻因此失败，抄错的都只有这一项），所以结果一律改用载荷里的值。
        # 提示词仍要求原样复制：改提示词会移动任务身份，这里只是不再校验。
        if (
            data["news_id"] != payload["news_id"]
            or data["change_sequence"] != payload["change_sequence"]
        ):
            raise ValueError("news_identity_mismatch")
        data["content_hash"] = str(payload["content_hash"]).strip()
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
        if payload.get("verification_version") == FOCUS_VERIFICATION_VERSION:
            expected = {event["event_group_id"]: event["event_group_version"] for event in payload["events"]}
            verifications = data["event_verifications"]
            actual = {entry["event_group_id"]: entry["event_group_version"] for entry in verifications}
            if len(actual) != len(verifications) or actual != expected:
                raise ValueError("market_focus_verification_event_mismatch")
        allowed_tickers = {
            str(ticker).strip().upper() for ticker in payload["allowed_tickers"]
        }
        output_tickers = {
            assessment["ticker"] for assessment in data["focus_ticker_assessments"]
        }
        if not output_tickers <= allowed_tickers:
            raise ValueError("market_focus_ticker_binding_mismatch")
    return data


_RESULT_VERDICT_LIMIT = 2048
_result_verdicts: OrderedDict[tuple[Any, ...], tuple[bool, Any]] = OrderedDict()
_result_verdicts_lock = threading.Lock()


def validate_result_cached(
    job_type: str,
    raw_json: str,
    payload: dict,
    *,
    validator: Callable[[str, str, dict], dict] = validate_result,
) -> dict:
    """``validator(job_type, raw_json, payload)`` with its verdict remembered per process.

    validate_result reads no clock and no mutable state, so the job type, the
    result bytes and the payload fix its outcome; rejections are remembered
    and raised again. A deploy restarts the process, so changed rules always
    start from an empty cache.
    """

    try:
        key = (
            validator,
            job_type,
            hashlib.sha256(raw_json.encode("utf-8")).digest(),
            hashlib.sha256(canonical_json_text(payload).encode("utf-8")).digest(),
        )
    except (TypeError, ValueError):
        return validator(job_type, raw_json, payload)
    with _result_verdicts_lock:
        verdict = _result_verdicts.get(key)
        if verdict is not None:
            _result_verdicts.move_to_end(key)
    if verdict is None:
        try:
            verdict = (True, validator(job_type, raw_json, payload))
        except (TypeError, ValueError) as exc:
            verdict = (False, exc)
        with _result_verdicts_lock:
            _result_verdicts[key] = verdict
            while len(_result_verdicts) > _RESULT_VERDICT_LIMIT:
                _result_verdicts.popitem(last=False)
    accepted, outcome = verdict
    if not accepted:
        raise outcome.with_traceback(None)
    return copy.deepcopy(outcome)
