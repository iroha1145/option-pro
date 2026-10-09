"""Counterexamples from the last review of the 2026-10-10 AI fixes (head 0b7b68fa).

The cases named as counterexamples fail on 0b7b68fa and pass now; the
positive and red-line cases pass on both.
"""

from __future__ import annotations

import pytest

from test_ai_analysis_fixes_20261010 import _focus_field, _news_field


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


def _publish_both(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


def _reject_both(text, match=None):
    with pytest.raises(ValueError, match=match):
        _news_field(text)
    with pytest.raises(ValueError, match=match):
        _focus_field(text, field="summary_zh")


# --- M1. Market overview counts after 港股 are not codes ----------------------


@pytest.mark.parametrize(
    "text",
    [
        "港股2600家上市公司中多数收跌。",
        "港股1500只个股下跌。",
        "港股1200余只个股上涨。",
        "港股2000多只股票下跌。",
        "港股5000亿成交创新高。",
        "港股26000点附近震荡。",
        "港股20000点关口失守。",
    ],
)
def test_m1_market_overview_counts_after_hong_kong_stocks_publish(text):
    _publish_both(text)


@pytest.mark.parametrize("text", ["港股1810小米集团盘中走高。", "腾讯港股代码为0700。"])
def test_m1_hong_kong_codes_still_need_binding(text):
    _reject_both(text, match="unbound_numeric_security_code")
