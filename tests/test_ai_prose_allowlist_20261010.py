"""Chinese-text allowances added after PR #237 went live (2026-10-10).

Within 20 minutes of the deploy, Luna had two more english_prose_not_allowed
failures on ordinary Chinese sentences: a rule clause ``5550(a)(2)`` (rejected
fragment ``'a'``), and ``HMO`` and ``D部分`` (Medicare Part D) in one summary.
The recovery dry run over failed jobs since 10-03 showed the same long tail:
single letters, CNBC, MHz, III, SUV, REIT, ESG, FCC, NBC and LSEG.

Each allowance has positive cases and red-line counterexamples. The red lines
are the 2026-10-10 audit's: an unbound code in a security context stays
rejected, and so does English prose.
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
        # Chinese on both sides: not a clause chain.
        "根据(a)款，公司有180天合规期。",
        "方案(a)更优。",
        # Full-width brackets, two letters, an upper-case letter.
        "规则5550（a）条。",
        "规则5550(ab)条。",
        "规则5550(A)条。",
        # The English article stays English.
        "报告称a deal已经达成。",
        "规则5550(a deal)。",
    ],
)
def test_other_bracketed_or_bare_lower_case_letters_stay_rejected(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)


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
        # A comma or bracket between the letter and the noun: 部分 means "some".
        "福特汽车（F），部分分析师下调评级。",
        "福特汽车F，部分分析师下调评级。",
        # A security prefix still asks for the code.
        "股票代码F组。",
        # 股 keeps its own rule: only A, B and H, and never 股价 or 股票.
        "A股价上涨。",
        "A股票上涨。",
        "F股上涨。",
        "C股下跌。",
        "A公司宣布回购。",
    ],
)
def test_letter_labels_keep_the_security_red_line(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)


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
        # 上涨 after a listed name is a stock move unless the name is a
        # self-describing statistic; REIT is not one.
        "REIT上涨。",
    ],
)
def test_common_abbreviations_keep_the_security_red_line(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)


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
        # The verified-focus unit translation leaves these alone and its
        # red-line test expects the language gate to reject them
        # (tests/test_verified_focus_prose_compatibility.py); news agrees.
        "频率MHz已公布。",
        "公司800MHz发布公告。",
        "编号800MHz继续有效。",
        "800MHz公司宣布交易。",
        "股票代码为800MHz。",
        "600MHz股价上涨。",
        "MHz股价上涨。",
    ],
)
def test_bare_units_and_labelled_frequencies_stay_rejected(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)


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
def test_roman_numerals_keep_the_security_red_line(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)


# --- The audit's red lines -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("A股价上涨。", "english_prose_not_allowed"),
        ("股票600519上涨。", "unbound_numeric_security_code"),
        ("IT股价上涨。", "english_prose_not_allowed"),
        ("TSLA上涨。", "english_prose_not_allowed"),
        ("盘前TSLA +3.5%，市场情绪回暖。", "english_prose_not_allowed"),
    ],
)
def test_audit_red_lines_are_unchanged(text, error):
    with pytest.raises(ValueError, match=error):
        _news_field(text)
    with pytest.raises(ValueError, match=error):
        _focus_field(text, field="summary_zh")
