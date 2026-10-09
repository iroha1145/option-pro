"""Counterexamples from the independent review of the 2026-10-10 AI fixes.

Each case below was accepted by the code under review and must be rejected
(or translated) now; the positive cases are the production sentences those
fixes were made for.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

import pytest

from app.services.ai_jobs import runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.tools import recover_ai_schema_results as recovery
from test_ai_analysis_fixes_20261010 import FIXTURES, _failed_receipt_row, _focus_field, _news_field
from test_luna_news_web_fallback import settings as luna_settings


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


# --- S4. The recovery dry run shows what it would publish ---------------------


def _receipts_only(monkeypatch, repo):
    async def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must reuse the stored receipt")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(runtime, "submit_background", forbidden)
    monkeypatch.setattr(recovery, "get_settings", lambda: luna_settings(repo.path))


def _since() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _at(value, path):
    for name, index in re.findall(r"([^.\[\]]+)|\[(\d+)\]", path):
        value = value[int(index)] if index else value[name]
    return value


def test_s4_dry_run_shows_the_text_apply_then_publishes(tmp_path, monkeypatch, capsys):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _failed_receipt_row(repo, FIXTURES[0])
    _receipts_only(monkeypatch, repo)

    assert recovery.main(["--failed-since", _since()]) == 0
    [item] = json.loads(capsys.readouterr().out)
    assert (item["job_id"], item["status"]) == (job_id, "validated")
    preview = item["narrative"]
    assert repo.get_job(job_id)["status"] == "failed"

    assert recovery.main(["--job-id", job_id, "--apply"]) == 0
    capsys.readouterr()
    stored = json.loads(repo.get_job(job_id)["result_json"])
    expected = {name: stored[name] for name in ("title_zh", "summary_zh", "headline_summary", "causal_summary")}
    for name in ("key_factors", "uncertainty_notes", "affected_sectors"):
        expected.update({f"{name}[{index}]": text for index, text in enumerate(stored[name])})
    assert expected.items() <= preview.items()
    assert {path: _at(stored, path) for path in preview} == preview
    assert "classification" not in preview


def test_s4_rows_beyond_the_limit_are_reported(tmp_path, monkeypatch, capsys):
    repo = AIJobRepository(tmp_path / "news.db")
    job_ids = [_failed_receipt_row(repo, sample) for sample in FIXTURES[:3]]
    _receipts_only(monkeypatch, repo)

    assert recovery.main(["--failed-since", _since(), "--limit", "2"]) == 0
    captured = capsys.readouterr()
    assert [item["job_id"] for item in json.loads(captured.out)] == job_ids[:2]
    assert 'selected by job type: {"news_impact": 3}' in captured.err
    assert "1 recoverable job(s) beyond --limit 2 were not selected" in captured.err
    assert all(repo.get_job(job_id)["status"] == "failed" for job_id in job_ids)


@pytest.mark.parametrize("limit", ["0", "100001", "-1", "many"])
def test_s4_limit_outside_its_range_is_a_usage_error(tmp_path, monkeypatch, capsys, limit):
    repo = AIJobRepository(tmp_path / "news.db")
    _receipts_only(monkeypatch, repo)

    with pytest.raises(SystemExit) as raised:
        recovery.main(["--failed-since", _since(), "--limit", limit])
    assert raised.value.code == 2
    error = capsys.readouterr().err
    assert "argument --limit" in error and "Traceback" not in error


# --- Suggestion: one rule decides whether the article body was obtained -------


def test_a_blank_article_body_counts_as_missing_when_publishing():
    from test_luna_news_web_fallback import payload as luna_payload
    from test_luna_news_web_fallback import response as luna_response
    from test_luna_news_web_fallback import search

    blank = {**_ARTICLE, "text": "  "}
    data = {**luna_payload(), "article_status": "available", "article": blank}
    # The request gate already treats a blank body as missing and searches.
    assert runtime.task_uses_web_search("news_impact", data, model=runtime.LUNA_MODEL)
    assert runtime.claude_tools_for("news_impact", data)

    unsupported = runtime.receipt_result(runtime.openai_receipt(luna_response()), "news_impact", data)
    assert unsupported["insufficient_context"] is True
    assert unsupported["affected_stocks"] == []

    searched = runtime.receipt_result(
        runtime.openai_receipt(luna_response(calls=[search()])), "news_impact", data,
    )
    assert searched["uncertainty_notes"][-1] == "原始正文未取得；分析采用另行联网检索的来源，请查阅来源链接。"


# --- Suggestion: a Luna search request is bounded by Luna's context window ----


def test_luna_search_reservation_uses_the_published_context_window():
    from test_luna_news_web_fallback import payload as luna_payload

    searching = luna_payload()
    with_article = {**luna_payload(), "article_status": "available", "article": _ARTICLE}
    model = runtime.LUNA_MODEL
    # gpt-5.6-luna: 1,050,000-token context window, of which 65,536 is output.
    assert runtime.max_output_tokens_for("news_impact", model=model) == 65_536
    assert runtime.max_input_tokens_for("news_impact", model=model, payload=searching) == 984_464
    assert runtime.token_reservation("news_impact", model=model, payload=searching) == 1_050_000
    assert runtime.token_reservation("news_impact", model=model, payload=with_article) == 139_264
    # Over 272K input takes the long-context rates: 984,464 x $0.50/M input,
    # 65,536 x $1.80/M output, plus three searches at $0.01.
    assert runtime.budget_reservation_microusd("news_impact", model=model, payload=searching) == 640_197
    assert runtime.budget_reservation_microusd("news_impact", model=model, payload=with_article) == 97_076
