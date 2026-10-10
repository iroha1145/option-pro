"""Chinese-text policy change of 2026-10-10: Latin terms stay, English prose goes.

The v5 policy keeps the existing whole-text and sentence language ratios,
but stops guessing stock identity from Latin words in Chinese prose. These
production examples exercise that change while raw fields, host names,
explicit numeric code labels and structured identity remain checked.
"""

from __future__ import annotations

import json

import pytest

from app.services.ai_jobs.models import validate_result
from test_ai_analysis_fixes_20261010 import _ARTICLE, _focus_field, _news_field
from test_ai_jobs_audit_2026_09_25 import _earnings_result
from test_ai_jobs_zh_contract import _news_payload, _news_result


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


# --- Terms that are published as written -------------------------------------

_PRODUCTION_TERMS = (
    # Single tokens: drug, brand and product names, acronyms, mixed case.
    "患者接受efgartigimod治疗后症状改善。",
    "BRAFTOVI联合疗法获批。",
    "韩国决定追加部署THAAD系统。",
    "VEGF-A抑制剂销售增长。",
    "CoreWeave与微软签订算力合同。",
    "开发者转向node.js生态。",
    "SThree下调全年利润指引。",
    "Polymesh网络完成升级。",
    # Names of up to five words.
    "Simply Good Foods公布季度业绩。",
    "PAC-3 Edge拦截弹完成测试。",
    "Gooch & Housego plc公布中期业绩。",
    "Panmure Liberum维持买入评级。",
    "美国能源部启动Genesis Mission计划。",
    "该行为可能违反Rule 10b-5。",
    # Single letters with or without a sign: ratings, series, tranches.
    "标普全球评级A+、展望稳定。",
    "估值等级为B。",
    "估值评级为D。",
    "估值风格评级为F。",
    "A+级EPS修正评级。",
    "公司赎回B系列优先股。",
    "公司发行9.25% B系列累计可赎回优先股。",
    "D系列认股权证到期。",
    "E类C系列。",
    "G档定期贷款完成再融资。",
    # Units and currency prefixes.
    "反应温度达到300°C。",
    "公司出售600 MHz频谱。",
    "公司出售600MHz频谱。",
    "公司以C$1,000,000收购该资产。",
    "公司以US$5亿收购。",
    "公司以HK$3亿配售新股。",
)


@pytest.mark.parametrize("text", _PRODUCTION_TERMS)
def test_production_terms_are_published_in_news_and_hotspots(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


def test_earnings_results_share_the_term_rule():
    result = _earnings_result()
    result["summary"] = "Simply Good Foods本季度业绩超预期。"
    validated = validate_result(
        "earnings_impact", json.dumps(result, ensure_ascii=False), {"ticker": "AAPL"},
    )
    assert validated["summary"] == "Simply Good Foods本季度业绩超预期。"


# --- English prose and other non-terms are still rejected ---------------------


@pytest.mark.parametrize(("text", "published"), [
    ("公司称the company reported strong results。", None),
    ("市场（investors flee quickly）持续下跌。", "市场（investors flee quickly）持续下跌。"),
    ("Apple Beats Estimates，市场持续关注。", "Apple Beats Estimates，市场持续关注。"),
    ("market-rally带动指数。", "market-rally带动指数。"),
    ("Bank of New York Mellon Trust Company发布公告。", None),
    ("uncertainty为true。", "uncertainty为是。"),
    ("该字段为false。", "该字段为否。"),
    ("数据为null。", "数据为null。"),
    ("输入my_article_status为不可用。", None),
    ("详见https://foo.io/bar。", None),
    ("公司公布融资安排（GlobeNewswire.com）。", None),
    ("Polymesh。网络完成升级。", None),
])
def test_terms_are_published_but_english_dominance_raw_fields_and_hosts_are_rejected(text, published):
    if published is None:
        with pytest.raises(ValueError, match="english_prose_not_allowed"):
            _news_field(text)
    else:
        assert _news_field(text) == published


def test_english_prose_is_not_translated_word_by_word_into_a_term():
    # Every word but one is a payload field name or status value. Translating
    # them would leave the single word 「status」, which is now a term.
    text = "英伟达发布新品。article text truncated, source title available, summary status truncated."
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text, article_status="available", article=_ARTICLE)


# --- Red lines ---------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # A code-like fragment next to a price, a move, 股, 股票 or 代码.
        "TSLA上涨。",
        "A股价上涨。",
        "IT股价上涨。",
        "ESG股价上涨。",
        "F股上涨。",
        "C股下跌。",
        "F股受到关注。",
        "股票代码CNBC。",
        "IT大涨后回落。",
        "盘前TSLA +3.5%，市场情绪回暖。",
        "F>12美元后福特汽车加速上涨。",
        # A code-like fragment naming a company.
        "A公司宣布回购。",
        # A code-like word inside a name, next to a move.
        "T-Mobile US此前下跌约5.4%。",
    ],
)
def test_prose_security_context_does_not_guess_structured_identity(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # Known cost of the prose rule: two or more lower-case words.
        "患者接受efgartigimod alfa治疗。",
        "risk-off情绪升温。",
    ],
)
def test_multiword_drug_and_market_terms_are_published(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "市场关注特斯拉（TSLA）财报表现。",
        "CoreWeave股票受到关注。",
        "宏观环境块状态为active（代码），证据不足。",
        "团队用Rust代码重写了核心模块。",
        "用Go代码实现。",
        "Polymesh价格下跌。",
        "Fed Pauses市场上涨。",
        "末段高空区域防御系统（THAAD）部署完成。",
    ],
)
def test_cases_relaxed_by_round_three_are_published(text):
    # 2026-10-10 第三轮：证券语境的绑定只针对代码样词元，括号里的代码和「代码」标签不再算
    # 证券语境。这些原先是本文件的红线或已知代价。
    assert _news_field(text) == text


def test_bound_code_still_publishes():
    text = "TSLA上涨。"
    assert _news_field(text, allowed_tickers=["NVDA", "TSLA"]) == text


def test_explicit_numeric_security_codes_still_require_binding():
    assert _news_field("股票600519上涨。") == "股票600519上涨。"
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _news_field("股票代码600519上涨。")


def test_placeholders_and_other_scripts_are_unchanged():
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        _news_field("x")
    with pytest.raises(ValueError, match="non_chinese_script_not_allowed"):
        _news_field("マーケット上昇，市场上涨。")


def test_news_identity_still_has_to_match():
    result = _news_result()
    result["news_id"] += 1
    with pytest.raises(ValueError, match="news_identity_mismatch"):
        validate_result("news_impact", json.dumps(result, ensure_ascii=False), _news_payload())


@pytest.mark.parametrize("text", [
    "The company reported stronger revenue and raised its earnings outlook.",
    "INVESTORS ARE WATCHING DEMAND AND PRICING AFTER THE PRODUCT LAUNCH.",
])
def test_complete_english_output_is_still_rejected(text):
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        _news_field(text)
    with pytest.raises(ValueError, match="simplified_chinese_text_required"):
        _focus_field(text, field="summary_zh")
