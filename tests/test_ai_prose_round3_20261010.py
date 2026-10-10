"""Round three of the 2026-10-10 language policy, replayed from production.

After PR #241 went live, Luna news still failed on citations
(ai_news_unbound_or_unhandled_url), on names next to a stock move
(「CleanSpark股价…」「C3.ai…」), and earnings failed on peer tickers and on
echoed payload field names. The user's lenient policy now says:

* Markdown citations and URLs in Luna news text are stripped, never a reason
  to reject. The receipt keeps its evidence_sources for the owner view.
* Only code-like words (one to five capital letters) need ticker binding in
  a security context. Names in mixed case or with a dot do not, and a code in
  「公司名（代码）」 or after 「代码为」 is not a security context.
* Earnings may echo its own payload field names (「release_status」).

The fixture rows are copied verbatim from production; no test contacts a
provider. news_identity_mismatch is diagnosed, not changed: the model
mistyped the 64-character content_hash.
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import validate_result, validate_simplified_chinese_text
from test_ai_analysis_fixes_20261010 import _focus_field, _luna_receipt, _news_field
from test_ai_jobs_audit_2026_09_25 import _earnings_result
from test_luna_news_web_fallback import payload as luna_payload


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "ai_round3_failures_20261010.json").read_text(
        encoding="utf-8"
    )
)
_STRUCTURAL_EARNINGS = {
    "aij_5d112d3ed8f94fb2b1e65240fe5b14c1",  # ticker missing
    "aij_9e4a209c856b40c1b0dd78666e06623f",  # impacted is empty
}


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


def _rows(*groups):
    return [row for group in groups for row in FIXTURES[group]]


def _narrative(result):
    fields = ("title_zh", "summary_zh", "headline_summary", "causal_summary")
    texts = [result[name] for name in fields if isinstance(result.get(name), str)]
    for name in ("key_factors", "uncertainty_notes", "affected_sectors"):
        texts.extend(result.get(name) or [])
    return texts


# --- Production receipts -------------------------------------------------------


@pytest.mark.parametrize(
    "row",
    [
        row
        for row in _rows("url_unbound", "prose_names", "earnings")
        if row["job_id"] not in _STRUCTURAL_EARNINGS
    ],
    ids=lambda row: row["job_id"],
)
def test_production_receipts_now_validate(row):
    receipt = deepcopy(row["receipt"])
    result = runtime.receipt_result(receipt, row["job_type"], row["payload"])
    assert receipt == row["receipt"]
    if row["job_type"] == "news_impact":
        assert not any("http" in text or "](" in text for text in _narrative(result))


@pytest.mark.parametrize(
    "row",
    [row for row in FIXTURES["earnings"] if row["job_id"] in _STRUCTURAL_EARNINGS],
    ids=lambda row: row["job_id"],
)
def test_structural_earnings_failures_stay_rejected(row):
    with pytest.raises(ValueError):
        runtime.receipt_result(deepcopy(row["receipt"]), row["job_type"], row["payload"])


@pytest.mark.parametrize("row", FIXTURES["identity_mismatch"], ids=lambda row: row["job_id"])
def test_identity_mismatch_is_a_mistyped_content_hash(row):
    output = json.loads(row["receipt"]["output_text"])
    payload = row["payload"]
    assert output["news_id"] == payload["news_id"]
    assert output["change_sequence"] == payload["change_sequence"]
    assert output["content_hash"] != payload["content_hash"]
    with pytest.raises(ValueError, match="news_identity_mismatch"):
        runtime.receipt_result(deepcopy(row["receipt"]), row["job_type"], payload)


# --- Citations and URLs in Luna news -----------------------------------------

_URL = "https://www.zacks.com/stock/news/3003909/swk-vs-leco?cid=CS-ZC-FT-3003909"
_RETRIEVED = ["https://www.zacks.com/stock/news/3003909/swk-vs-leco"]


@pytest.mark.parametrize(
    ("text", "published"),
    [
        # The input's own URL is not in the receipt; it is still stripped.
        (f"直接来源页面未能打开。 ([zacks.com]({_URL}))", "直接来源页面未能打开。"),
        (f"制造业工具（[zacks.com]({_URL})）", "制造业工具"),
        (f"相关数据来自[扎克斯研究]({_URL})。", "相关数据来自扎克斯研究。"),
        (f"详见[zacks.com]({_URL})。", "详见。"),
        (f"详见{_URL}。", "详见。"),
        (f"公司说明（{_URL}）已核对。", "公司说明已核对。"),
        (f"估值偏低。来源：[zacks.com]({_URL})", "估值偏低。"),
        (f"估值偏低，([zacks.com]({_URL}))。", "估值偏低。"),
    ],
)
def test_citations_and_urls_are_stripped(text, published):
    receipt = _luna_receipt(_RETRIEVED, summary_zh=text)
    original = deepcopy(receipt)
    assert runtime.receipt_result(receipt, "news_impact", luna_payload())["summary_zh"] == published
    assert receipt == original


def test_a_bracketed_domain_without_a_url_keeps_its_old_rule():
    # Not a URL: only a retrieved site's bare domain is removed.
    receipt = _luna_receipt(_RETRIEVED, summary_zh="会议延期（othersite.com）。")
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        runtime.receipt_result(receipt, "news_impact", luna_payload())


def test_url_stripping_is_only_for_luna_news():
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(f"详见{_URL}。")


# --- Names, codes and security context ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # Names in mixed case or with a dot, next to a stock move.
        "CleanSpark股价在大盘上涨时下跌。",
        "2026年10月9日，C3.ai盘后股价大跌。",
        "Polymesh价格下跌。",
        "Fed Pauses市场上涨。",
        "BRK.B股价上涨。",
        # A code in 「公司名（代码）」 or after 「代码为」.
        "市场关注特斯拉（TSLA）财报表现。",
        "前十大供应商中开利（CARR）占62%。",
        "检索结果指向代码为BCML的另一家公司。",
    ],
)
def test_names_and_aliases_are_published(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("TSLA上涨。", "english_prose_not_allowed"),
        ("A股价上涨。", "english_prose_not_allowed"),
        ("IT股价上涨。", "english_prose_not_allowed"),
        ("ESG股价上涨。", "english_prose_not_allowed"),
        ("F股受到关注。", "english_prose_not_allowed"),
        ("A公司宣布回购。", "english_prose_not_allowed"),
        ("特斯拉（TSLA）股价上涨。", "english_prose_not_allowed"),
        ("IT大涨后回落。", "english_prose_not_allowed"),
        ("盘前TSLA +3.5%，市场情绪回暖。", "english_prose_not_allowed"),
        ("F>12美元后福特汽车加速上涨。", "english_prose_not_allowed"),
        ("T-Mobile US此前下跌约5.4%。", "english_prose_not_allowed"),
        ("股票600519上涨。", "unbound_numeric_security_code"),
    ],
)
def test_code_like_words_in_security_context_still_need_binding(text, error):
    with pytest.raises(ValueError, match=error):
        _news_field(text)
    with pytest.raises(ValueError, match=error):
        _focus_field(text, field="summary_zh")


def test_bound_code_in_security_context_still_publishes():
    assert _news_field("TSLA上涨。", allowed_tickers=["NVDA", "TSLA"]) == "TSLA上涨。"


# --- Earnings echoing its own payload field names ----------------------------

_EARNINGS_PAYLOAD = {
    "ticker": "AAPL",
    "name": "Apple",
    "release_status": "scheduled",
    "eps_actual": None,
    "eps_estimate": 1.25,
    "revenue_actual": None,
}


def _earnings_summary(text, payload=_EARNINGS_PAYLOAD):
    result = _earnings_result()
    result["summary"] = text
    return validate_result("earnings_impact", json.dumps(result, ensure_ascii=False), payload)["summary"]


def test_earnings_may_echo_its_own_payload_field_names():
    text = "财报尚未发布（release_status为scheduled），eps_actual与revenue_actual均为空。"
    assert _earnings_summary(text) == text


@pytest.mark.parametrize(
    "text",
    [
        # Not one of this payload's field names.
        "财报状态为my_release_status。",
        # A field name in a security context.
        "股票代码eps_actual上涨。",
    ],
)
def test_other_field_names_and_security_context_stay_rejected(text):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _earnings_summary(text)


def test_field_name_echo_is_only_for_earnings():
    # News translates its field names first; a hotspot does not echo them.
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _focus_field("未知cycle_id显示事件仍待公布。", field="summary_zh")
