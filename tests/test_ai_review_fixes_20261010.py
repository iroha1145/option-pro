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


# --- B3. Field names are translated, never let through as English ------------

_ARTICLE = {
    "status": "available",
    "text": "英伟达公布新芯片。",
    "source_url": "https://www.reuters.com/x",
    "fetched_at": "2026-10-09T00:00:00Z",
    "truncated": False,
}


@pytest.mark.parametrize(
    ("text", "changes", "published"),
    [
        ("输入article标记为可用，已按正文分析。", {"article_status": "available", "article": _ARTICLE},
         "输入新闻正文标记为可用，已按正文分析。"),
        ("article_reason为http_403，正文不可用。", {"article_status": "unavailable", "article_reason": "http_403"},
         "正文缺失原因为状态码403，正文不可用。"),
        ("正文因unsupported_encoding无法解析", {"article_status": "unavailable", "article_reason": "unsupported_encoding"},
         "正文因编码不支持无法解析"),
        ("insufficient_context设为真", {}, "证据不足设为真"),
        ("模型confidence较低，classification为看多。", {}, "模型置信度较低，判断类别为看多。"),
        ("输入summary仅含代码标记", {}, "输入摘要仅含代码标记"),
        ("原文title与url均来自输入。", {}, "原文标题与网址均来自输入。"),
        ("正文text已截断，article_status为available。", {"article_status": "available", "article": _ARTICLE},
         "正文正文已截断，正文状态为可用。"),
    ],
)
def test_b3_field_names_are_published_in_chinese(text, changes, published):
    assert _news_field(text, **changes) == published


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        # Ordinary English left after translation is still rejected.
        ("英伟达发布新品。article text truncated, source title available, summary status truncated.",
         {"article_status": "available", "article": _ARTICLE}),
        # Without an article there is no text field to translate.
        ("原文text缺失。", {}),
        # A status value counts only when it is this payload's own value.
        ("正文状态为available。", {"article_status": "unavailable"}),
        ("正文因http_404无法读取。", {"article_status": "unavailable", "article_reason": "http_403"}),
        ("输入status为可用。", {"article_status": "available", "article": _ARTICLE}),
    ],
)
def test_b3_words_outside_the_payload_vocabulary_are_still_rejected(text, changes):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text, **changes)


# --- Suggestion: only domains of retrieved sites are removed ------------------


@pytest.mark.parametrize(
    "text",
    ["开发者转向（node.js）生态。", "监管文件见（sec.gov/news.html）。"],
)
def test_bracketed_text_that_is_not_a_retrieved_site_is_not_deleted(text):
    assert _news_field(text) == text
    assert _focus_field(text, field="summary_zh") == text


# --- S2. IT means information technology only in its own phrases -------------


@pytest.mark.parametrize(
    "text",
    [
        "高德纳（IT）暴跌20%。",
        "IT大涨后回落。",
        "股票代码IT服务。",
        "Apple暴跌拖累科技股。",
        "股票600519大涨。",
    ],
)
def test_s2_price_moves_and_it_outside_its_phrases_need_binding(text):
    with pytest.raises(ValueError):
        _news_field(text)


@pytest.mark.parametrize(
    "text",
    ["属于企业IT服务订单，金额未知。", "企业IT支出放缓。", "IT行业整体承压。", "IT系统升级完成。"],
)
def test_s2_it_phrases_still_pass(text):
    assert _news_field(text) == text


# --- S3. Only lower-case statistic symbols take a comparison -----------------


@pytest.mark.parametrize(
    "text",
    [
        "F>12美元后福特汽车加速上涨。",
        "福特汽车F<10美元后买盘涌入。",
        "试验结果P<0.001。",
        "当x<5时信号失效。",
    ],
)
def test_s3_upper_case_letters_and_other_symbols_take_no_comparison_exemption(text):
    with pytest.raises(ValueError):
        _news_field(text)


@pytest.mark.parametrize(
    "text",
    ["主要终点达到统计学显著（p<0.001），共纳入n=712例患者。", "p值低于0.05。", "相关系数r=0.82。"],
)
def test_s3_statistic_notation_still_passes(text):
    assert _news_field(text) == text


# --- S1. The worker supervisor never cuts a paid wait short -------------------


def _ai_jobs_task_timeout(tmp_path) -> float:
    from app.worker.tasks import build_default_tasks
    from test_macro_worker import _settings as worker_settings

    specs = build_default_tasks("review", settings=worker_settings(tmp_path))
    return next(spec.timeout_seconds for spec in specs if spec.name == "ai_jobs")


def test_s1_ai_jobs_pass_outlasts_the_default_paid_wait(tmp_path):
    from app.config import Settings

    paid_wait = Settings.model_fields["openai_background_poll_timeout_seconds"].default
    # A paid Claude stream needs room to finish before the supervisor's
    # asyncio.wait_for cancels the whole pass (and every other slot in it).
    assert _ai_jobs_task_timeout(tmp_path) >= paid_wait + 300.0


@pytest.mark.parametrize("configured", [60.0, 3600.0, 86_400.0])
def test_s1_paid_stream_deadline_stays_inside_the_pass(tmp_path, configured):
    from types import SimpleNamespace

    from app.execution_limits import AI_JOBS_SUPERVISOR_MARGIN_SECONDS
    from app.services.ai_jobs import worker

    task_timeout = _ai_jobs_task_timeout(tmp_path)
    seconds = worker._paid_stream_seconds(
        SimpleNamespace(openai_background_poll_timeout_seconds=configured)
    )
    assert seconds == min(configured, task_timeout - AI_JOBS_SUPERVISOR_MARGIN_SECONDS)
    assert seconds <= task_timeout - AI_JOBS_SUPERVISOR_MARGIN_SECONDS
