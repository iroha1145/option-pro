"""Counterexamples from the independent review of the 2026-10-10 AI fixes.

Each case below was accepted by the code under review and must be rejected
(or translated) now; the positive cases are the production sentences those
fixes were made for.
"""

from __future__ import annotations

import pytest

from test_ai_analysis_fixes_20261010 import _focus_field, _news_field


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


# --- B1. A spread is only a rate spread after a named benchmark ---------------


@pytest.mark.parametrize(
    "text",
    [
        "盘前TSLA +3.5%，市场情绪回暖。",
        "盘后AMD -2%，市场承压。",
        "今日TSLA减2%，拖累指数。",
        "科技股分化，GOOGL +2%。",
        "盘后AMD减2个基点。",
        "盘前TSLA+3.5%。",
    ],
)
def test_b1_a_ticker_followed_by_a_percentage_move_still_needs_binding(text):
    with pytest.raises(ValueError):
        _news_field(text)
    with pytest.raises(ValueError):
        _focus_field(text, field="summary_zh")


@pytest.mark.parametrize(
    "text",
    [
        "若继续存续，随后利率将转为复合SOFR加1.730%。",
        "该笔贷款利率为LIBOR加150个基点。",
        "票据按SONIA+0.5%计息。",
    ],
)
def test_b1_named_benchmarks_keep_their_spread_notation(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


# --- B2. Only real quantities exempt a number from security-code checks -------


@pytest.mark.parametrize(
    "text",
    [
        "港股09888百度集团盘中走高。",
        "关注股票600309 万华化学的走势。",
        "股票300014亿纬锂能大涨。",
        "股票600519股东大会通过分红方案。",
        "腾讯00700股东大会今日召开。",
        "港股06160百济神州获纳入指数。",
        "关注股票000002万科的走势。",
        "股票600519股本结构未变。",
        "腾讯00700港元报价走高。",
        "股票600519万股成交。",
    ],
)
def test_b2_unbound_numeric_codes_are_not_quantities(text):
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _focus_field(text, field="summary_zh")
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _news_field(text)


@pytest.mark.parametrize(
    "text",
    [
        "麦克尤恩矿业签署协议，以现金500万美元加发现矿业股份5000万美元出售安大略两处资产。",
        "对价为现金500万美元加发现矿业普通股5000万美元，需常规交割条件与监管批准。",
        "持有该公司股份5%，另有普通股300万股。",
        "发行证券1.25亿美元。",
    ],
)
def test_b2_amounts_after_share_nouns_still_pass(text):
    assert _focus_field(text, field="summary_zh") == text
