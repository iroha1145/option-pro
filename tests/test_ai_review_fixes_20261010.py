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
