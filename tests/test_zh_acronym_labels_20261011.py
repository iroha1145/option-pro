"""行业与商品标签允许单独写通行的大写缩写（2026-10-11）。

07:30Z 的 Sonnet 核实热点周期 aij_5c439766 整轮作废（付费约 1 到 4 美元）。错误只有
三条，原因相同：affected_sectors 里单独写的 'REIT' 没有汉字，被判
simplified_chinese_text_required：

    dominant_events.1.affected_sectors.1
    dominant_events.2.affected_sectors.2
    event_verifications.15.affected_sectors.1

同一模型在别的周期写「通信塔REIT」能通过。近 10 天新闻分析的 affected_sectors 与
affected_commodities[].name 里还有 ETF、LNG、SOL、GPU、AVAX、USDC 同类拒绝。

现在这五个标签字段（ZhLabelText）在去掉首尾空白后整体匹配 [A-Z]{2,5}s? 时直接接受，其余
照旧走 validate_simplified_chinese_text。标题、摘要、要点、风险、不确定性等正文字段不放宽，
英文单词（Telecom、Real estate）作为标签仍被拒。结果模型的 JSON schema 与任务身份不变：
校验契约版本仍是 simplified-chinese-v4，提示词不动。
"""

from __future__ import annotations

import hashlib
import json
import typing

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.services.ai_jobs import models, runtime
from app.services.ai_jobs.models import (
    MARKET_FOCUS_SCHEMA_NAME,
    RESULT_VALIDATION_CONTRACT_VERSION,
    VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    ZhShortText,
    validate_market_focus_evidence,
    validate_result,
    validate_simplified_chinese_text,
)
from test_ai_jobs_zh_contract import (
    _market_focus_payload,
    _market_focus_result,
    _news_payload,
    _news_result,
)

REQUIRED = "Value error, simplified_chinese_text_required"

# 通行的大写缩写：整体匹配 [A-Z]{2,5}s?。REIT 来自 aij_5c439766，ETF、LNG、SOL、GPU、
# AVAX、USDC 是近 10 天新闻分析的拒绝样本。
ACRONYM_LABELS = [
    "AI", "ETF", "LNG", "GPU", "REIT", "REITs", "SOL", "AVAX", "USDC",
    "ABCDE", "ABCDEs",  # 五位大写加复数 s 是上界
]
# 英文单词、小写、六位及以上、夹杂其他符号或全角字母都不是缩写标签。
WORD_LABELS = [
    "Telecom", "Real estate", "reit", "Reit", "REIt", "reits", "ABCDEF", "ABCDEFs",
    "A", "REITss", "REIT ETF", "REIT-ETF", "REIT.", "REIT/ETF", "ＲＥＩＴ",
]


def _errors(job_type: str, result: dict, payload: dict, **options) -> list[tuple[tuple, str]]:
    """每条被违反的规则：(字段位置, 信息)，按位置排序。全部通过时为空列表。"""
    try:
        validate_result(job_type, json.dumps(result, ensure_ascii=False), payload, **options)
    except ValidationError as exc:
        return sorted(((error["loc"], error["msg"]) for error in exc.errors()), key=repr)
    return []


def _at(document: dict, path: tuple):
    for segment in path:
        document = document[segment]
    return document


def _put(document: dict, path: tuple, value) -> dict:
    _at(document, path[:-1])[path[-1]] = value
    return document


# --- 生产形状：aij_5c439766 的 16 个事件、3 个主导事件 ----------------------------------

EVENT_COUNT = 16


def _verified_case(label: str = "REIT") -> tuple[dict, dict]:
    """``label`` 放在 aij_5c439766 出错的三个位置；正文字段的用例改用带汉字的写法。"""
    ids = [f"event_{index:02d}" for index in range(EVENT_COUNT)]
    payload = {
        "cycle_id": "mfc_acronym_labels", "as_of": "2026-10-11T07:30:00Z", "input_hash": "a" * 64,
        "prepared_revision": 1, "verification_version": "web-evidence-v1",
        "allowed_event_group_ids": ids, "allowed_tickers": ["AMT"],
        "events": [
            {"event_group_id": event_id, "event_group_version": 1, "title_zh": f"第{index + 1}号事件"}
            for index, event_id in enumerate(ids)
        ],
    }
    result = {
        "output_language": "zh-CN", "cycle_id": payload["cycle_id"], "as_of": payload["as_of"],
        "input_hash": payload["input_hash"],
        "title_zh": "利率预期压制房地产信托",
        "summary_zh": "利率预期上行，市场关注房地产信托与通信基础设施的估值压力。",
        "headline_summary": "利率预期上行压制估值，房地产信托承压。",
        "market_summary": "十六个事件里多数证据不足，仅少数事件可以核验。",
        "dominant_events": [
            {"event_group_id": ids[0], "summary": "大型银行披露季度业绩。", "affected_sectors": ["银行"]},
            {"event_group_id": ids[1], "summary": "通信塔运营商下调指引。", "affected_sectors": ["通信", label]},
            {"event_group_id": ids[2], "summary": "多家公司宣布并购。", "affected_sectors": ["银行", "能源", label]},
        ],
        "market_uncertainties": ["利率路径仍不明朗。"],
        "affected_sectors": ["银行", "通信塔REIT"],
        "focus_ticker_assessments": [{
            "ticker": "AMT", "catalyst_bias": -10, "confidence": 55, "horizon": "days",
            "supporting_event_ids": [ids[1]], "conflicting_event_ids": [],
            "summary": "利率上行压制估值，但缺少可核验的公司事件。", "risks": ["指引下调。"],
            "insufficient_evidence": False,
        }],
        "no_new_material_catalyst": False, "insufficient_context": False,
        "event_verifications": [
            {
                "event_group_id": event_id, "event_group_version": 1, "verdict": "unverifiable",
                "evidence_refs": [], "title_zh": f"第{index + 1}号事件", "summary_zh": "未能核实该事件。",
                "affected_sectors": ["通信", label] if index == EVENT_COUNT - 1 else ["通信"],
            }
            for index, event_id in enumerate(ids)
        ],
    }
    return payload, result


# 与 aij_5c439766 的三条错误逐一对应的位置。
INCIDENT_PATHS = [
    ("dominant_events", 1, "affected_sectors", 1),
    ("dominant_events", 2, "affected_sectors", 2),
    ("event_verifications", 15, "affected_sectors", 1),
]


@pytest.mark.parametrize("schema_version", [None, VERIFIED_MARKET_FOCUS_SCHEMA_NAME])
def test_the_incident_cycle_passes_with_bare_reit_labels(schema_version):
    payload, result = _verified_case()
    assert [_at(result, path) for path in INCIDENT_PATHS] == ["REIT", "REIT", "REIT"]
    checked = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload, schema_version=schema_version,
    )
    assert [_at(checked, path) for path in INCIDENT_PATHS] == ["REIT", "REIT", "REIT"]
    assert checked["dominant_events"][1]["affected_sectors"] == ["通信", "REIT"]
    assert checked["affected_sectors"] == ["银行", "通信塔REIT"]
    # 发布前的核验照常进行：标签放宽不碰事件绑定和证据规则。
    validate_market_focus_evidence(checked, payload, [])


@pytest.mark.parametrize("schema_version", [None, VERIFIED_MARKET_FOCUS_SCHEMA_NAME])
@pytest.mark.parametrize("label", ["ETF", "REITs"])
def test_every_focus_label_slot_of_the_verified_contract_takes_acronyms(label, schema_version):
    payload, result = _verified_case()
    slots = [
        ("affected_sectors", 0), ("dominant_events", 0, "affected_sectors", 0),
        ("event_verifications", 0, "affected_sectors", 0),
    ]
    for slot in slots:
        _put(result, slot, label)
    checked = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload, schema_version=schema_version,
    )
    assert [_at(checked, slot) for slot in slots] == [label] * 3


@pytest.mark.parametrize("schema_version", [None, MARKET_FOCUS_SCHEMA_NAME])
def test_the_unverified_focus_contract_takes_acronym_labels_too(schema_version):
    result = _market_focus_result()
    result["dominant_events"][0]["affected_sectors"] = ["REIT", "半导体"]
    result["affected_sectors"] = ["ETF", "信息技术"]
    checked = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), _market_focus_payload(),
        schema_version=schema_version,
    )
    assert checked["dominant_events"][0]["affected_sectors"] == ["REIT", "半导体"]
    assert checked["affected_sectors"] == ["ETF", "信息技术"]


# --- 新闻：行业与商品名 -------------------------------------------------------------------


def _news_case(*, sector=None, commodity=None) -> tuple[dict, dict]:
    result = _news_result()
    result["affected_commodities"] = [
        {"name": "液化天然气", "impact_score": 10, "reason": "供应变化可能影响短期价格。"},
    ]
    if sector is not None:
        result["affected_sectors"] = ["半导体", sector]
    if commodity is not None:
        result["affected_commodities"][0]["name"] = commodity
    return result, _news_payload()


@pytest.mark.parametrize("label", ACRONYM_LABELS)
def test_news_sectors_and_commodity_names_take_acronym_labels(label):
    result, payload = _news_case(sector=label, commodity=label)
    checked = validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)
    assert checked["affected_sectors"] == ["半导体", label]
    assert checked["affected_commodities"][0]["name"] == label


@pytest.mark.parametrize("raw", [" REIT ", "\tETF\n", "　LNG　"])
def test_surrounding_whitespace_is_removed_before_the_shape_is_judged(raw):
    result, payload = _news_case(sector=raw, commodity=raw)
    checked = validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)
    assert checked["affected_sectors"][1] == raw.strip()
    assert checked["affected_commodities"][0]["name"] == raw.strip()


@pytest.mark.parametrize("label", WORD_LABELS)
def test_news_labels_that_are_words_not_acronyms_are_still_rejected(label):
    result, payload = _news_case(sector=label)
    assert _errors("news_impact", result, payload) == [(("affected_sectors", 1), REQUIRED)]
    result, payload = _news_case(commodity=label)
    assert _errors("news_impact", result, payload) == [(("affected_commodities", 0, "name"), REQUIRED)]


@pytest.mark.parametrize("schema_version", [None, VERIFIED_MARKET_FOCUS_SCHEMA_NAME])
@pytest.mark.parametrize("label", WORD_LABELS)
def test_focus_labels_that_are_words_not_acronyms_are_still_rejected(label, schema_version):
    payload, result = _verified_case(label)
    assert _errors("market_focus", result, payload, schema_version=schema_version) == sorted(
        ((path, REQUIRED) for path in INCIDENT_PATHS), key=repr,
    )


def test_labels_with_chinese_characters_keep_the_full_text_rules():
    result, payload = _news_case(sector="通信塔REIT", commodity="REIT板块")
    checked = validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)
    assert checked["affected_sectors"][1] == "通信塔REIT"
    assert checked["affected_commodities"][0]["name"] == "REIT板块"
    # 繁体字走原来的规则，不会因为同一字段放宽了缩写而漏过。
    result, payload = _news_case(sector="半導體")
    assert _errors("news_impact", result, payload) == [
        (("affected_sectors", 1), "Value error, traditional_chinese_not_allowed"),
    ]
    # 带缩写的英文句子不是标签。
    result, payload = _news_case(sector="REIT stocks are likely to rise")
    assert _errors("news_impact", result, payload) == [(("affected_sectors", 1), REQUIRED)]


def test_labels_still_get_the_ticker_binding_context_of_the_text_rule():
    # 非缩写的标签带着 ValidationInfo 走原校验：数字证券代码是否放行，取决于载荷里的
    # allowed_tickers。包装函数如果丢掉 info，这一条先坏。
    label = "贵州茅台（股票代码600519）"
    result, payload = _news_case(sector=label)
    assert _errors("news_impact", result, payload) == [
        (("affected_sectors", 1), "Value error, unbound_numeric_security_code"),
    ]
    payload = {**payload, "allowed_tickers": ["NVDA", "600519"]}
    checked = validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)
    assert checked["affected_sectors"][1] == label


def test_the_label_validator_is_the_text_validator_plus_one_shortcut():
    label_rule = models.validate_simplified_chinese_label
    assert label_rule("REIT", None) == "REIT" and label_rule(" REITs ", None) == "REITs"
    assert label_rule("纽约证券交易所（NYSE）", None) == "纽约证券交易所（NYSE）"
    with pytest.raises(ValueError, match="traditional_chinese_not_allowed"):
        label_rule("市場消息彙整", None)
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        label_rule("Telecom", None)


# --- 正文字段不放宽 -----------------------------------------------------------------------

NEWS_BODY_PATHS = [
    ("title_zh",), ("summary_zh",), ("headline_summary",), ("causal_summary",),
    ("key_factors", 0), ("uncertainty_notes", 0),
    ("affected_stocks", 0, "reason"), ("affected_commodities", 0, "reason"),
]
FOCUS_BODY_PATHS = [
    ("title_zh",), ("summary_zh",), ("headline_summary",), ("market_summary",),
    ("market_uncertainties", 0), ("dominant_events", 0, "summary"),
    ("focus_ticker_assessments", 0, "summary"), ("focus_ticker_assessments", 0, "risks", 0),
]
VERIFIED_BODY_PATHS = FOCUS_BODY_PATHS + [
    ("event_verifications", 0, "title_zh"), ("event_verifications", 0, "summary_zh"),
]


@pytest.mark.parametrize("path", NEWS_BODY_PATHS, ids=str)
def test_news_prose_fields_still_reject_a_bare_acronym(path):
    result, payload = _news_case()
    _put(result, path, "REIT")
    assert _errors("news_impact", result, payload) == [(path, REQUIRED)]


@pytest.mark.parametrize("schema_version", [None, MARKET_FOCUS_SCHEMA_NAME])
@pytest.mark.parametrize("path", FOCUS_BODY_PATHS, ids=str)
def test_focus_prose_fields_still_reject_a_bare_acronym(path, schema_version):
    result = _market_focus_result()
    _put(result, path, "REIT")
    assert _errors("market_focus", result, _market_focus_payload(), schema_version=schema_version) == [(path, REQUIRED)]


@pytest.mark.parametrize("schema_version", [None, VERIFIED_MARKET_FOCUS_SCHEMA_NAME])
@pytest.mark.parametrize("path", VERIFIED_BODY_PATHS, ids=str)
def test_verified_focus_prose_fields_still_reject_a_bare_acronym(path, schema_version):
    payload, result = _verified_case("通信塔REIT")
    _put(result, path, "REIT")
    assert _errors("market_focus", result, payload, schema_version=schema_version) == [(path, REQUIRED)]


def test_the_text_rule_itself_is_not_loosened():
    for text in ("REIT", "ETF", "REITs", "AI"):
        with pytest.raises(ValueError, match="simplified_chinese_text_required"):
            validate_simplified_chinese_text(text, None)
        with pytest.raises(ValidationError, match="simplified_chinese_text_required"):
            TypeAdapter(ZhShortText).validate_python(text)
        assert TypeAdapter(models.ZhLabelText).validate_python(text) == text


def _fields_using_the_label_rule() -> set[tuple[str, str]]:
    def has_rule(metadata) -> bool:
        return any(
            getattr(item, "func", None) is models.validate_simplified_chinese_label for item in metadata
        )

    found = set()
    for name, cls in vars(models).items():
        if not (isinstance(cls, type) and issubclass(cls, BaseModel) and cls.__module__ == models.__name__):
            continue
        for field_name in cls.__dict__.get("__annotations__", {}):
            field = cls.model_fields.get(field_name)
            if field is None:
                continue
            nested = [getattr(arg, "__metadata__", ()) for arg in typing.get_args(field.annotation)]
            if has_rule(field.metadata) or any(has_rule(metadata) for metadata in nested):
                found.add((name, field_name))
    return found


def test_exactly_five_declarations_use_the_label_rule():
    # 子类（Concise*）继承这些声明，自己没有重新声明标签字段。
    assert _fields_using_the_label_rule() == {
        ("NewsCommodityImpact", "name"),
        ("NewsImpactResult", "affected_sectors"),
        ("MarketFocusDominantEvent", "affected_sectors"),
        ("MarketFocusResult", "affected_sectors"),
        ("FocusEventVerification", "affected_sectors"),
    }


# --- 提示、结构和任务身份不动 -----------------------------------------------------------------

# origin/main 6d62347f（本改动之前）上各结果模型 model_json_schema(mode="validation") 的规范化
# JSON（排序键、紧凑分隔、不转义中文，与 runtime._validation_schema_json 同一写法）的 SHA-256。
# 有意改结果结构时它们和 runtime.py 的身份常量一起移动，到那时再换新的基准。
SCHEMA_BASELINE = {
    "EarningsImpactResult": "1e91590723b6292e23b6accf5fc3f19af897f5cebf6b16af85248b1ca4e7c111",
    "OptionAlertResult": "97b9a02a941485660f13aad69d8026db714b0985b064c1b445027556932405f9",
    "SignalAnalysisResult": "bd6620a223cb77dd83c066c3d080dbe5a26d203eeaa3afb934661f2fd30d71c5",
    "NewsImpactResult": "a4b1aab908547d8f4499a6ccbc5a6d98073011c11fdc2bc568540ece7062e27a",
    "MarketFocusResult": "c7cf3258dbf7a1710663696c1530d81c28326ee65978cc504b38831fc1b6a1c3",
    "VerifiedMarketFocusResult": "52df0c7a48c2d99b0690de31a9006057e568f7abf926a4e739f546f10f587822",
    "ConciseMarketFocusResult": "96a75514eda2a1f4d34fd0031b471240f1205e4a0fdc970ab843332aab51ffae",
    "ConciseVerifiedMarketFocusResult": "a3769460c93df22a98ae878b9f39530cadb26014e6850c665f5ff3d76127760f",
}


def _canonical(schema: dict) -> str:
    return json.dumps(schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("name", sorted(SCHEMA_BASELINE))
def test_result_model_schemas_are_byte_identical_to_main_before_the_label_type(name):
    cls = getattr(models, name)
    assert _digest(_canonical(cls.model_json_schema(mode="validation"))) == SCHEMA_BASELINE[name]


@pytest.mark.parametrize(("job_type", "model", "name"), [
    ("earnings_impact", None, "EarningsImpactResult"),
    ("option_alerts", None, "OptionAlertResult"),
    ("signal_analysis", None, "SignalAnalysisResult"),
    ("news_impact", None, "NewsImpactResult"),
    ("market_focus", None, "ConciseMarketFocusResult"),
    ("market_focus", runtime.SONNET_MODEL, "ConciseVerifiedMarketFocusResult"),
])
def test_the_schema_text_the_runtime_embeds_in_requests_is_unchanged(job_type, model, name):
    assert _digest(runtime._validation_schema_json(job_type, model)) == SCHEMA_BASELINE[name]


def test_the_label_type_has_the_json_schema_of_zh_short_text():
    assert TypeAdapter(models.ZhLabelText).json_schema() == TypeAdapter(ZhShortText).json_schema()
    assert TypeAdapter(list[models.ZhLabelText]).json_schema() == TypeAdapter(list[ZhShortText]).json_schema()


def test_validation_contract_and_task_identities_do_not_move():
    assert RESULT_VALIDATION_CONTRACT_VERSION == "simplified-chinese-v4"
    assert runtime.schema_identity("market_focus", model=runtime.SONNET_MODEL) == runtime.VERIFIED_FOCUS_IDENTITY
    assert runtime.schema_identity("market_focus", model="claude-haiku-5-5") == runtime.FOCUS_CLAUDE_IDENTITY
    assert runtime.schema_identity("market_focus", model="gpt-5.6-terra") == runtime.FOCUS_OPENAI_IDENTITY
    assert runtime.schema_identity("news_impact", model="gpt-5.6-terra") == runtime.TERRA_NEWS_IDENTITY
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL) == runtime.LUNA_SUMMARY_NEWS_IDENTITY
    assert runtime.schema_identity("earnings_impact", model="gpt-5.6-terra") == runtime.OPENAI_EARNINGS_IDENTITY
