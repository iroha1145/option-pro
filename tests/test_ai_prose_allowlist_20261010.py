"""Chinese-text allowances added after PR #237 went live (2026-10-10).

Within 20 minutes of the deploy, Luna had two more english_prose_not_allowed
failures on ordinary Chinese sentences: a rule clause ``5550(a)(2)`` (rejected
fragment ``'a'``), and ``HMO`` and ``D部分`` (Medicare Part D) in one summary.
The recovery dry run over failed jobs since 10-03 showed the same long tail:
single letters, CNBC, MHz, III, SUV, REIT, ESG, FCC, NBC and LSEG.

The v5 update retains these examples while dropping prose ticker inference.
Whole-text language checks and explicit numeric code labels remain checked.
"""

from __future__ import annotations

import pytest

from test_ai_analysis_fixes_20261010 import _focus_field, _news_field


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


# --- Production sentences ------------------------------------------------------

_PRODUCTION_SENTENCES = (
    "公司普通股收盘买价不符合纳斯达克资本市场规则5550(a)(2)的继续上市最低买价要求。",
    "阿莱恩特医疗保健披露，其加州HMO合同预计由2026年的4.0星降至2027年的3.5星。",
    "健康结果调查和处方药D部分若干三倍权重指标走弱。",
)


@pytest.mark.parametrize("text", _PRODUCTION_SENTENCES)
def test_production_sentences_publish(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


# --- Rule clause letters: 5550(a)(2) -------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "交易计划依据规则10b5-1(c)设立。",
        "适用第(2)(a)项的豁免。",
        "根据(a)(2)款，公司有180天合规期。",
        "规则5550(a)(2)与5550(b)(1)均未满足。",
    ],
)
def test_lower_case_clause_letters_next_to_a_number_or_group_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # The English article stays English.
        "报告称a deal已经达成。",
        "规则5550(a deal)。",
    ],
)
def test_short_english_phrases_in_chinese_keep_the_existing_ratio_floor(text):
    assert _news_field(text) == text


# --- A capital letter as a label before a category noun -----------------------


@pytest.mark.parametrize(
    "text",
    [
        "处方药D 部分的覆盖范围扩大。",
        "股价走出V型反转。",
        "该批次产品被评为A级。",
        "B组患者的缓解率更高。",
        "工厂C区停产检修。",
        # Existing behaviour, unchanged.
        "A股市场回暖。",
        "公司完成A轮融资。",
        "公司发行B类股。",
    ],
)
def test_letter_labels_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # Historical prose examples now remain publishable.
        "股票代码F组。",
        # Letter labels are not a structured ticker binding.
        "A股价上涨。",
        "A股票上涨。",
        "F股上涨。",
        "C股下跌。",
        "A公司宣布回购。",
    ],
)
def test_letter_labels_do_not_guess_structured_stock_identity(text):
    assert _news_field(text) == text


# --- Common abbreviations ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "CNBC报道称公司将裁员。",
        "NBC新闻称谈判仍在进行。",
        "FCC批准了频谱转让。",
        "LSEG旗下数据业务收入增长。",
        "公司ESG评级上调。",
        "医疗REIT分红保持稳定。",
        "SUV销量同比增长。",
        "PPO参保人数下降。",
    ],
)
def test_common_abbreviations_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "ESG股价上涨。",
        "股票代码CNBC。",
        "SUV股票下跌。",
        "股票代码LSEG。",
        # The display text does not determine structured stock identity.
        "REIT上涨。",
    ],
)
def test_common_abbreviations_do_not_guess_structured_stock_identity(text):
    assert _news_field(text) == text


def test_a_bound_code_that_is_also_an_abbreviation_still_publishes():
    text = "ESG股价上涨。"
    assert _news_field(text, allowed_tickers=["NVDA", "ESG"]) == text


# --- Frequency quantities --------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "公司出售600 MHz频谱。",
        "公司出售600MHz频谱。",
        "3.5GHz频段拍卖开始。",
        "2.4 GHz频段干扰增加。",
    ],
)
def test_numeric_frequency_quantities_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        "根据(a)款，公司有180天合规期。",
        "方案(a)更优。",
        "规则5550（a）条。",
        "规则5550(ab)条。",
        "规则5550(A)条。",
        "福特汽车F，部分分析师下调评级。",
        "频率MHz已公布。",
        "公司800MHz发布公告。",
        "800MHz公司宣布交易。",
        # 2026-10-10 第三轮：括号里的代码不要求绑定；频率不是代码样词元，接股价也放行。
        "福特汽车（F），部分分析师下调评级。",
        "编号800MHz继续有效。",
        "股票代码为800MHz。",
        "600MHz股价上涨。",
        "MHz股价上涨。",
    ],
)
def test_former_narrowness_cases_are_terms_after_the_2026_10_10_policy(text):
    # 2026-10-10 口径变更：这些原先用来检验本文件各条规则够窄，现在由
    # _is_term_like_span 按词条放行（第三轮又放开了括号代码和非代码样词元）。
    assert _news_field(text) == text


# --- Roman numerals as ordinals -----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "第III期临床试验达到主要终点。",
        "III期临床试验结果积极。",
        "第 III 期试验完成入组。",
        "II类医疗器械获批上市。",
        "第III代芯片开始量产。",
        "年报第II部分披露了风险因素。",
        "1/II期研究启动。",
        # Already published before this change: IV is listed as implied
        # volatility.
        "IV期非小细胞肺癌患者获益。",
        "第IV代芯片开始量产。",
    ],
)
def test_roman_numeral_ordinals_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # III is also a ticker.
        "III上涨。",
        "II股价上涨。",
        "股票代码III期。",
        "III公司股价下跌。",
        "买入III 300股。",
        "股票代码IV走强。",
    ],
)
def test_roman_numerals_do_not_guess_structured_stock_identity(text):
    assert _news_field(text) == text


# --- The audit's red lines -----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "A股价上涨。",
        "股票600519上涨。",
        "IT股价上涨。",
        "TSLA上涨。",
        "盘前TSLA +3.5%，市场情绪回暖。",
    ],
)
def test_old_prose_binding_examples_are_published(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text
