"""Counterexamples from the second review of the 2026-10-10 AI fixes (head 414be099).

Every case here was checked against 414be099: the cases named as
counterexamples fail there and pass now; the positive and red-line cases
pass on both.
"""

from __future__ import annotations

import json

import pytest

from app.services.ai_jobs import models, runtime
from app.services.ai_jobs.models import validate_result
from test_ai_analysis_fixes_20261010 import _focus_field, _luna_receipt, _news_field
from test_ai_jobs_audit_2026_09_25 import _option_alert_result
from test_luna_news_web_fallback import payload as luna_payload
from test_signal_context import _signal_result


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


_OPTION_PAYLOAD = {
    "ticker": "AMD",
    "alerts": [
        {"type": "call", "strike": 120, "volume": 5000, "moneyness": "otm", "reasons": ["Sweep"]}
    ],
    "underlying_price": 118,
    "expiration": "2026-10-16",
}


def _option_field(text):
    return validate_result("option_alerts", _option_alert_result(text), _OPTION_PAYLOAD)["summary"]


def _signal_field(text):
    result = _signal_result()
    result["summary"] = text
    return validate_result(
        "signal_analysis",
        json.dumps(result, ensure_ascii=False),
        {"ticker": "AMD", "context_tickers": ["NVDA"]},
    )["summary"]


_PUBLISHERS = {
    "option_alerts": _option_field,
    "signal_analysis": _signal_field,
    "news_impact": _news_field,
}


# --- N1. Market vocabulary followed by a common movement word is not a stock --

_REVIEW_SENTENCES = (
    "IV飙升至历史高位，缺少主动方。",
    "IV走高",
    "OI大涨",
    "Gamma走高",
    "PCR走低",
    "Delta飙升",
    "RSI反弹至55，动能改善。",
    "VWAP走高",
    "隐含波动率IV大跌。",
    "每股收益EPS反弹。",
    "标普500指数S&P 500走高。",
    "相关ETF反弹。",
    "WTI反弹至每桶80美元。",
    "OPEC减产后WTI走高。",
    "DRAM反弹。",
    "HBM飙升。",
    "Meta大涨。",
)


@pytest.mark.parametrize("job_type", sorted(_PUBLISHERS))
@pytest.mark.parametrize("text", _REVIEW_SENTENCES)
def test_n1_review_sentences_publish_in_every_task(job_type, text):
    assert _PUBLISHERS[job_type](text) == text


_MOVEMENTS = ("大涨", "暴跌", "飙升", "反弹", "走高", "走低")
_VOCABULARY = (
    # Market term abbreviations, macro indicator codes, benchmark rate names
    # and technical terms.
    "IV", "OI", "Gamma", "Delta", "PCR", "RSI", "MACD", "VWAP",
    "CPI", "PMI", "VIX", "SPX", "NDX",
    "SOFR", "LIBOR", "SHIBOR",
    "DRAM", "HBM", "GPU", "NAND",
)


@pytest.mark.parametrize("job_type", sorted(_PUBLISHERS))
def test_n1_market_vocabulary_takes_movement_words_in_every_task(job_type):
    rejected = []
    for name in _VOCABULARY:
        for movement in _MOVEMENTS:
            text = f"{name}{movement}，后续仍需观察。"
            try:
                _PUBLISHERS[job_type](text)
            except ValueError:
                rejected.append(text)
    assert rejected == []


_COMMON_MOVEMENTS = ("大涨", "暴跌", "飙升", "反弹", "走高", "走低", "重挫", "跳水", "拉升", "下挫")


def test_n1_no_whitelisted_name_is_rejected_for_a_common_movement_word():
    rejected = []
    for name in sorted(models._ALLOWED_EXACT_FOREIGN_SPANS):
        try:
            _news_field(f"{name}，后续仍需观察。")
        except ValueError:
            continue
        for movement in _COMMON_MOVEMENTS:
            text = f"{name}{movement}，后续仍需观察。"
            try:
                _news_field(text)
            except ValueError:
                rejected.append(text)
    assert rejected == []


@pytest.mark.parametrize(
    "text",
    ["高德纳（IT）暴跌20%。", "IT大涨后回落。", "股票代码IT服务。", "股票600519大涨。", "盘前TSLA上涨3%。"],
)
def test_n1_unbound_codes_stay_rejected(text):
    with pytest.raises(ValueError):
        _news_field(text)


# --- N2. A host name in brackets is a citation, not a term gloss --------------

_BRACKETED_HOSTS = (
    "公司提交了文件（sec.gov）。",
    "公司提交了文件（www.sec.gov）。",
    "据路透社（reuters.com）报道，公司提交了文件。",
    "电视台（cnbc.com）称公司将裁员。",
    "公司公布融资安排（globenewswire.com）。",
    "公司公布融资安排（sec.gov/news.html）。",
)


@pytest.mark.parametrize("text", _BRACKETED_HOSTS)
def test_n2_bracketed_host_names_are_not_published(text):
    with pytest.raises(ValueError):
        _news_field(text)
    with pytest.raises(ValueError):
        _focus_field(text, field="summary_zh")


@pytest.mark.parametrize(
    "text",
    ["据路透社（reuters.com）报道，英伟达发布新芯片。", "英伟达发布新芯片（www.nvidia.com/zh-cn）。"],
)
def test_n2_luna_hosts_other_than_a_retrieved_bare_domain_are_rejected(text):
    retrieved = ["https://www.nvidia.com/en-us/about-nvidia/"]
    with pytest.raises(ValueError):
        runtime.receipt_result(_luna_receipt(retrieved, summary_zh=text), "news_impact", luna_payload())


def test_n2_a_retrieved_bare_domain_is_still_removed():
    receipt = _luna_receipt(["https://www.nvidia.com/en-us/about-nvidia/"], summary_zh="英伟达发布新芯片（nvidia.com）。")
    assert runtime.receipt_result(receipt, "news_impact", luna_payload())["summary_zh"] == "英伟达发布新芯片。"


@pytest.mark.parametrize(
    "text",
    ["价格出现上冲回落（Upthrust）形态。", "公司首席执行官（CEO）辞职。", "电动垂直起降飞行器（eVTOL）获批。"],
)
def test_n2_term_glosses_still_publish(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


# --- Suggestion 7. Benchmark rate names describe the rate itself --------------


@pytest.mark.parametrize("job_type", sorted(_PUBLISHERS))
@pytest.mark.parametrize(
    "text",
    ["SOFR上涨5个基点。", "LIBOR下跌10个基点。", "隔夜SONIA走强。", "三个月SHIBOR收跌。"],
)
def test_s7_benchmark_rates_take_the_original_movement_words(job_type, text):
    assert _PUBLISHERS[job_type](text) == text


@pytest.mark.parametrize(
    "text",
    [
        "股票代码SOFR上涨。",
        "SOFR股价上涨。",
        "SOFR公司股价下跌。",
        "CPI公司股价下跌。",
        "盘前TSLA +3.5%，市场情绪回暖。",
    ],
)
def test_s7_security_context_still_needs_binding(text):
    with pytest.raises(ValueError):
        _news_field(text)


# --- Suggestion 1. Upper-case P and N statistics ------------------------------


@pytest.mark.parametrize(
    "text",
    ["主要终点达到统计学显著（P<0.001）。", "共纳入N=712例患者。", "组间差异显著（P = 0.03）。", "试验结果P<.05。"],
)
def test_s1r2_upper_case_p_and_n_statistics_publish(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


@pytest.mark.parametrize(
    "text",
    ["P<10美元后买盘涌入。", "N>5万美元时触发止损。", "F>12美元后福特汽车加速上涨。"],
)
def test_s1r2_a_letter_compared_with_a_price_still_needs_binding(text):
    with pytest.raises(ValueError):
        _news_field(text)


# --- Suggestion 2. Share counts after 股票 ------------------------------------


@pytest.mark.parametrize(
    "text",
    ["公司计划回购股票1000万股。", "大股东减持股票500万股。", "公司拟发行股票2亿股。", "公司拟发行股票20亿美元。"],
)
def test_s2r2_share_counts_after_the_word_stock_publish(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


@pytest.mark.parametrize(
    "text",
    ["股票600519万股成交。", "关注股票000002万科的走势。", "股票300014亿纬锂能大涨。", "股票600519上涨。"],
)
def test_s2r2_code_shaped_numbers_after_the_word_stock_still_need_binding(text):
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _news_field(text)


# --- Suggestion 5. Four-digit Hong Kong codes ---------------------------------


@pytest.mark.parametrize("text", ["港股9888百度集团盘中走高。", "港股代码1810小米集团走强。"])
def test_s5r2_four_digit_hong_kong_codes_need_binding(text):
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _news_field(text)
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _focus_field(text, field="summary_zh")


@pytest.mark.parametrize(
    "text",
    ["港股10月以来累计上涨。", "港股3只科技股走强。", "港股2026年表现分化。", "港股1000万股成交。"],
)
def test_s5r2_months_counts_and_years_after_hong_kong_stocks_publish(text):
    assert _news_field(text) == text


# --- Suggestion 3. More information-technology phrases ------------------------


@pytest.mark.parametrize(
    "text",
    [
        "企业IT基础设施更新加快。",
        "客户削减IT预算。",
        "公司加大IT投入。",
        "IT架构迁移到云端。",
        "IT运维外包比例上升。",
        "IT人员招聘放缓。",
        "IT资产管理需求增长。",
        "公司提供IT解决方案。",
    ],
)
def test_s3r2_more_it_phrases_publish(text):
    assert _news_field(text) == text


@pytest.mark.parametrize("text", ["股票代码IT基础设施。", "IT股价上涨。", "高德纳（IT）暴跌20%。"])
def test_s3r2_it_in_security_context_still_needs_binding(text):
    with pytest.raises(ValueError):
        _news_field(text)
