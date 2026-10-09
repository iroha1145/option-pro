"""Counterexamples from the second review of the 2026-10-10 AI fixes (head 414be099).

Every case here was checked against 414be099: the cases named as
counterexamples fail there and pass now; the positive and red-line cases
pass on both.
"""

from __future__ import annotations

import json

import pytest

from app.services.ai_jobs import models
from app.services.ai_jobs.models import validate_result
from test_ai_analysis_fixes_20261010 import _focus_field, _news_field
from test_ai_jobs_audit_2026_09_25 import _option_alert_result
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
    # Market term abbreviations, macro indicator codes and technical terms.
    "IV", "OI", "Gamma", "Delta", "PCR", "RSI", "MACD", "VWAP",
    "CPI", "PMI", "VIX", "SPX", "NDX",
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
