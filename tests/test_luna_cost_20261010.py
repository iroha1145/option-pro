"""2026-10-10 cost controls for GPT-5.6 Luna news analysis.

1. A Luna news task without a body whose feed summary is long enough is
   analyzed from that summary alone, without web search.
2. A searching Luna news task makes at most one tool call.
3. Scheduled analysis skips Zacks template articles; reads mark them skipped.
4. GPT models accept the max, xhigh and high reasoning efforts.

No test contacts a provider.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from pydantic import ValidationError

from app import personal_config as personal_config_module
from app.access import request_owner_access_context
from app.config import Settings
from app.personal_config import (
    AIConfig,
    CatalystConfig,
    FeatureConfig,
    PersonalConfig,
    load_personal_config,
)
from app.services.ai_jobs import repository as ai_jobs_repository_module
from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts import template_commentary
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService
from app.services.catalysts.template_commentary import (
    TEMPLATE_COMMENTARY_REASON,
    ZACKS_TEMPLATE_PATTERNS,
    ZACKS_TEMPLATE_SUFFIXES,
    template_commentary_reason,
)
from app.worker.tasks import _build_local_intelligence
from test_ai_analysis_fixes_20261010 import _ARTICLE, _pending_luna_job, _submit_pending
from test_catalyst_local_intelligence import (
    _apply_news,
    _fail_job,
    _finish_job,
    _news_change,
    _news_result,
    _stack,
)
from test_luna_news_web_fallback import payload as luna_payload
from test_luna_news_web_fallback import response as luna_response
from test_luna_news_web_fallback import search
from test_luna_news_web_fallback import settings as luna_settings
from test_personal_worker import _runtime_settings, _worker_config


ZACKS = "massive/Zacks Investment Research"
# About 430 characters, the median of 40 Zacks summaries sampled 2026-10-10.
LONG_SUMMARY = (
    "Novartis said the U.S. Food and Drug Administration approved a label expansion "
    "for Kisqali in combination with an aromatase inhibitor for adults with hormone "
    "receptor-positive early breast cancer at high risk of recurrence. The company "
    "expects the broader label to lift prescriptions next year and said the approval "
    "follows three-year data from the NATALEE study presented at an oncology meeting."
)
YSEOP_URL = (
    "https://www.zacks.com/stock/news/2787551/swk-vs-leco-which-stock-is-the-better-value-option"
    "?cid=CS-ZC-FT-tale_of_the_tape%7Cyseop_template_9-2787551"
)


@pytest.fixture(autouse=True)
def owner_context():
    with request_owner_access_context(True):
        yield


def summary_payload(reason: str = "no_matching_article_body", summary: str = LONG_SUMMARY) -> dict:
    return {**luna_payload(), "summary": summary, "article_status": "unavailable", "article_reason": reason}


def article_payload() -> dict:
    return {**luna_payload(), "article_status": "available", "article": _ARTICLE}


def sent_payload(params: dict) -> dict:
    match = re.fullmatch(r"<untrusted_news_data>(.*)</untrusted_news_data>", params["input"], re.S)
    assert match is not None
    return json.loads(match.group(1))


# --- 1. A long summary stands in for the missing body -----------------------


def test_summary_length_counts_visible_text_only():
    assert runtime.NEWS_SUMMARY_AS_BODY_MIN_CHARS == 300
    padded = "<p>" + " \n\t".join("x" * 299) + "</p>&nbsp;"
    assert len(padded) > 1_000
    assert not runtime.news_summary_sufficient({"summary": padded})
    assert runtime.news_summary_sufficient({"summary": "<b>" + "x" * 300 + "</b>"})
    # Entities decode to one character each.
    assert runtime.news_summary_sufficient({"summary": "&amp;" * 300})
    assert not runtime.news_summary_sufficient({"summary": "&amp;" * 299})
    assert not runtime.news_summary_sufficient({"summary": None})
    assert not runtime.news_summary_sufficient({})
    assert runtime.news_summary_sufficient(summary_payload())


@pytest.mark.parametrize(
    "reason",
    ["no_matching_article_body", "challenge_page", "http_403", "http_401", "publisher_url_unavailable", "paywall"],
)
def test_long_summary_without_a_body_is_analyzed_without_search(tmp_path, reason):
    data = summary_payload(reason)
    assert runtime.luna_news_mode("news_impact", data, model=runtime.LUNA_MODEL) == "summary"
    assert not runtime.task_uses_web_search("news_impact", data, model=runtime.LUNA_MODEL)

    params = runtime._create_params(luna_settings(tmp_path / "x"), "news_impact", data)
    assert not {"tools", "tool_choice", "max_tool_calls", "include"} & set(params)
    instructions = params["instructions"]
    assert "以下输入中的summary是来源提供的摘要，没有全文" in instructions
    assert "只分析标题与摘要内容，不联网，不要臆测全文" in instructions
    assert "联网搜索" not in instructions
    assert "本任务已提供正文" not in instructions
    # The request still says the body was not obtained, and why.
    sent = sent_payload(params)
    assert (sent["article_status"], sent["article_reason"]) == ("unavailable", reason)
    assert "article" not in sent
    assert sent["summary"] == LONG_SUMMARY


def test_a_short_summary_still_searches(tmp_path):
    almost = summary_payload(summary="y" * 299)
    assert runtime.task_uses_web_search("news_impact", almost, model=runtime.LUNA_MODEL)
    params = runtime._create_params(luna_settings(tmp_path / "x"), "news_impact", almost)
    assert params["tools"][0]["type"] == "web_search"


def test_summary_tasks_reserve_like_article_tasks():
    model = runtime.LUNA_MODEL
    data = summary_payload()
    assert runtime.max_tool_calls_for("news_impact", model=model, payload=data) == 0
    assert runtime.token_reservation("news_impact", model=model, payload=data) == 139_264
    assert runtime.token_reservation("news_impact", model=model, payload=article_payload()) == 139_264
    assert runtime.budget_reservation_microusd("news_impact", model=model, payload=data) == 97_076
    assert runtime.budget_reservation_microusd("news_impact", model=model, payload=article_payload()) == 97_076


def test_summary_tasks_have_their_own_identity_and_keep_the_queue_current():
    model = runtime.LUNA_MODEL
    prompt = runtime.PROMPT_VERSIONS["news_impact"]
    summary = runtime.schema_identity("news_impact", model=model, payload=summary_payload())
    assert summary == runtime.LUNA_SUMMARY_NEWS_IDENTITY
    assert runtime.schema_identity("news_impact", model=model, payload=luna_payload()) == runtime.LUNA_WEB_NEWS_IDENTITY
    assert runtime.schema_identity("news_impact", model=model, payload=article_payload()) == runtime.LUNA_ARTICLE_NEWS_IDENTITY
    # The body variant did not move; the searching one did (one tool call).
    assert runtime.LUNA_ARTICLE_NEWS_IDENTITY[1].startswith("e64f4252")
    assert runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY[1].startswith("cda8f76d")
    assert len({
        runtime.LUNA_WEB_NEWS_IDENTITY, runtime.LUNA_ARTICLE_NEWS_IDENTITY,
        runtime.LUNA_SUMMARY_NEWS_IDENTITY, runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY,
    }) == 4
    for stored in (
        runtime.LUNA_SUMMARY_NEWS_IDENTITY,
        runtime.LUNA_WEB_NEWS_IDENTITY,
        runtime.LUNA_ARTICLE_NEWS_IDENTITY,
        runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY,
        runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY,
        runtime.LEGACY_LUNA_NEWS_IDENTITY,
    ):
        assert runtime.schema_identity_current("news_impact", prompt, *stored, current_identity=summary, model=model)
    # Readers check a stored job against the model's default (searching)
    # identity; a finished summary-only job must stay current there too.
    assert runtime.schema_identity("news_impact", model=model) == runtime.LUNA_WEB_NEWS_IDENTITY
    for stored in (runtime.LUNA_SUMMARY_NEWS_IDENTITY, runtime.LUNA_ARTICLE_NEWS_IDENTITY):
        assert runtime.schema_identity_current("news_impact", prompt, *stored, model=model)
    assert runtime._IDENTITY_PREDECESSORS[runtime.LUNA_SUMMARY_NEWS_IDENTITY] == {
        runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY,
        runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY,
        runtime.LEGACY_LUNA_NEWS_IDENTITY,
    }
    assert runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY in runtime._IDENTITY_PREDECESSORS[runtime.LUNA_WEB_NEWS_IDENTITY]
    # A Luna identity never makes another model's row current.
    assert not runtime.schema_identity_current(
        "news_impact", prompt, *runtime.LUNA_SUMMARY_NEWS_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL,
    )


def test_a_policy_change_still_retires_every_luna_variant(monkeypatch):
    prompt = runtime.PROMPT_VERSIONS["news_impact"]
    monkeypatch.setitem(runtime.AI_TASK_MAX_OUTPUT_TOKENS, "news_impact", 98_304)
    for stored in (
        runtime.LUNA_SUMMARY_NEWS_IDENTITY,
        runtime.LUNA_WEB_NEWS_IDENTITY,
        runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY,
    ):
        assert not runtime.schema_identity_current("news_impact", prompt, *stored, model=runtime.LUNA_MODEL)


def test_queued_three_call_job_with_a_long_summary_is_submitted_without_search(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _pending_luna_job(repo, summary_payload("challenge_page"), runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY)
    (params,) = _submit_pending(repo, monkeypatch)
    row = repo.get_job(job_id)
    assert row["error_code"] is None
    assert row["openai_response_id"] == "resp_policy_transition"
    assert not {"tools", "tool_choice", "max_tool_calls", "include"} & set(params)
    assert "以下输入中的summary是来源提供的摘要" in params["instructions"]


def test_queued_three_call_job_with_a_short_summary_searches_once(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _pending_luna_job(repo, luna_payload(), runtime.LUNA_THREE_CALL_WEB_NEWS_IDENTITY)
    (params,) = _submit_pending(repo, monkeypatch)
    assert repo.get_job(job_id)["error_code"] is None
    assert params["tool_choice"] == "required"
    assert params["max_tool_calls"] == 1


def test_a_summary_receipt_publishes_the_reading_without_sources():
    result = runtime.receipt_result(runtime.openai_receipt(luna_response()), "news_impact", summary_payload())
    assert result["insufficient_context"] is False
    assert result["title_zh"] == "英伟达发布新一代芯片平台"
    assert [stock["ticker"] for stock in result["affected_stocks"]] == ["NVDA"]
    assert not any("联网" in note for note in result["uncertainty_notes"])


def test_a_searching_receipt_without_sources_stays_insufficient():
    # Submitted with tools before this change, and the search found nothing.
    failed = runtime.openai_receipt(luna_response(calls=[search(status="failed")]))
    assert runtime.receipt_result(failed, "news_impact", summary_payload())["insufficient_context"] is True
    # A short summary is a searching task even without a recorded call.
    plain = runtime.openai_receipt(luna_response())
    unsupported = runtime.receipt_result(plain, "news_impact", luna_payload())
    assert unsupported["insufficient_context"] is True
    assert unsupported["affected_stocks"] == []
    # With sources the note about separately retrieved sources stays.
    searched = runtime.receipt_result(
        runtime.openai_receipt(luna_response(calls=[search()])), "news_impact", luna_payload(),
    )
    assert searched["uncertainty_notes"][-1] == "原始正文未取得；分析采用另行联网检索的来源，请查阅来源链接。"


def test_the_summary_rule_is_luna_only():
    data = summary_payload()
    assert runtime.luna_news_mode("news_impact", data, model=runtime.OFFICIAL_CLAUDE_MODEL) is None
    assert runtime.luna_news_mode("news_impact", data, model=runtime.OFFICIAL_OPENAI_MODEL) is None
    # Claude keeps its own tool policy: no body means tools are offered.
    assert runtime.claude_tools_for("news_impact", data)
    assert runtime.schema_identity("news_impact", model=runtime.OFFICIAL_OPENAI_MODEL, payload=data) == runtime.TERRA_NEWS_IDENTITY


# --- 2. One tool call for a searching task ------------------------------------


def test_a_searching_task_makes_at_most_one_tool_call(tmp_path):
    model = runtime.LUNA_MODEL
    params = runtime._create_params(luna_settings(tmp_path / "x"), "news_impact", luna_payload())
    assert params["tools"][0]["type"] == "web_search"
    assert params["tool_choice"] == "required"
    assert params["max_tool_calls"] == runtime.LUNA_MAX_TOOL_CALLS == 1
    assert "只能调用一次，用这一次搜索原始事件" in params["instructions"]
    assert runtime.max_tool_calls_for("news_impact", model=model, payload=luna_payload()) == 1
    # The input bound is still the context window; one search at $0.01 replaces three.
    assert runtime.token_reservation("news_impact", model=model, payload=luna_payload()) == 1_050_000
    assert runtime.budget_reservation_microusd("news_impact", model=model, payload=luna_payload()) == 620_197


# --- 3. Zacks template articles -------------------------------------------------


@pytest.mark.parametrize("suffix", ZACKS_TEMPLATE_SUFFIXES)
def test_every_listed_suffix_is_a_template(suffix):
    title = f"Nvidia (NVDA) Rises Higher Than Market: {suffix}"
    assert template_commentary_reason(ZACKS, title) == TEMPLATE_COMMENTARY_REASON


TEMPLATE_TITLES = (
    "Nvidia (NVDA) Outpaced the Stock Market Today",
    "Amazon (AMZN) Dips More Than Broader Market",
    "Investors Heavily Search Apple Inc. (AAPL)",
    "Why Nvidia (NVDA) is a Top-Ranked Growth Stock",
    "Why Pfizer (PFE) is a Top-Ranked Value Stock for the Long-Term",
    "Caterpillar (CAT) Shows Fast-paced Momentum But Is Still a Bargain Stock",
    "Best Income Stocks to Buy for October 9th",
    "The Zacks Analyst Blog Highlights Apple, Microsoft and Nvidia",
    "Should Vanguard Growth ETF (VUG) Be on Your Investing Radar?",
    "Bull of the Day: Nvidia (NVDA)",
    "Bear of the Day: Intel (INTC)",
    "What Makes Nvidia (NVDA) a New Buy Stock",
    "Oracle (ORCL) Earnings Expected to Grow: Should You Buy?",
)


@pytest.mark.parametrize("title", TEMPLATE_TITLES)
def test_listed_title_templates_are_skipped(title):
    assert template_commentary_reason(ZACKS, title) == TEMPLATE_COMMENTARY_REASON


def test_every_title_pattern_has_a_sample():
    normalized = [template_commentary._normalize(title) for title in TEMPLATE_TITLES]
    for pattern in ZACKS_TEMPLATE_PATTERNS:
        assert any(re.search(pattern, title) for title in normalized), pattern


@pytest.mark.parametrize(
    "title",
    [
        "Microsoft (MSFT) Stock Sinks As Market Gains: what you should know",
        "MICROSOFT (MSFT) STOCK SINKS AS MARKET GAINS: WHAT YOU SHOULD KNOW",
        "Is Nvidia (NVDA) a Buy Now? Here’s Why",
        "Is Nvidia (NVDA) a Buy Now? Heres Why",
        "Oracle (ORCL) Earnings Expected to Grow: Should You Buy",
        "AMD (AMD) — Facts to Know Before Betting on It",
        "AMD (AMD) - Facts to Know Before Betting on It",
        "Intel (INTC) | Important Facts to Note.",
        "Apple (AAPL)：Key Facts",
        "Nike (NKE) Q1 Earnings: What to Know Ahead of Next Week’s Release",
        "Why Nvidia (NVDA) is a Top Ranked Growth Stock",
        "Caterpillar (CAT) Shows Fast Paced Momentum",
    ],
)
def test_case_and_punctuation_do_not_matter(title):
    assert template_commentary_reason(ZACKS, title) == TEMPLATE_COMMENTARY_REASON


REAL_ZACKS_NEWS = (
    "NVS Wins FDA Nod for Label Expansion of Kisqali in Early Breast Cancer",
    "Canadian National Unveils Grain Movement Plan for 2026-27 Crop Year",
    "Pfizer to Acquire Metsera in $4.9 Billion Obesity Drug Deal",
    "Delta Air Lines Q3 Earnings Beat Estimates, Revenues Rise Y/Y",
    "JPMorgan Q3 Earnings: Net Interest Income Rises, Trading Revenues Jump",
    "Microsoft Unveils Maia 200 AI Chip: Key Details on Azure Rollout",
    "Tesla Recalls 1.2 Million Vehicles Over Rearview Camera Defect",
    "Intel Outpaces Rivals in Foundry Orders as Market Share Grows",
    "Best Buy Raises Full-Year Outlook on Strong Holiday Demand",
    "Bull Market Enters Fourth Year as S&P 500 Hits Record High",
)


@pytest.mark.parametrize("title", REAL_ZACKS_NEWS)
def test_real_zacks_news_is_analyzed(title):
    assert template_commentary_reason(ZACKS, title, "https://www.zacks.com/stock/news/2787000/real") is None


def test_generated_zacks_links_are_templates_whatever_the_title():
    assert template_commentary_reason(ZACKS, "Stanley Black & Decker and Lincoln Electric", YSEOP_URL) == TEMPLATE_COMMENTARY_REASON
    assert template_commentary_reason(ZACKS, "SWK vs. LECO: Which Stock Is the Better Value Option?", YSEOP_URL)
    unencoded = "https://www.zacks.com/stock/news/2787551/a?cid=CS-ZC-FT-analyst_blog|yseop_template_6-2787551"
    assert template_commentary_reason(ZACKS, REAL_ZACKS_NEWS[0], unencoded) == TEMPLATE_COMMENTARY_REASON
    # Only the query string counts.
    assert template_commentary_reason(ZACKS, REAL_ZACKS_NEWS[0], "https://www.zacks.com/yseop_template/news") is None
    assert template_commentary_reason(ZACKS, REAL_ZACKS_NEWS[0], None) is None


@pytest.mark.parametrize("source", ["seekingalpha/breaking", "finnhub/Reuters", "globenewswire/public_companies", "", None])
def test_other_sources_are_never_skipped(source):
    assert template_commentary_reason(source, "Apple (AAPL): What You Should Know", YSEOP_URL) is None
    assert template_commentary_reason(source, "Bull of the Day: Nvidia (NVDA)") is None


def _zacks_change(sequence: int, news_id: int, title: str, *, now: datetime, url: str | None = None, ticker: str = "NVDA"):
    change = _news_change(
        sequence, news_id, available_at=now - timedelta(minutes=sequence + 1),
        title=title, source=ZACKS, tickers=(ticker,),
    )
    if url is not None:
        change["news"]["url"] = url
    return change


def _scheduled_news(ai: AIJobRepository) -> list[dict]:
    with sqlite3.connect(ai.path) as connection:
        return [
            {"news_id": json.loads(row[0])["news_id"], "reasoning": row[1], "model": row[2]}
            for row in connection.execute(
                "SELECT payload_json,reasoning,model FROM ai_jobs WHERE job_type='news_impact' ORDER BY created_at,job_id"
            )
        ]


def _mixed_news(etl, now):
    _apply_news(
        etl,
        [
            _zacks_change(1, 701, "Nvidia (NVDA) Rises Higher Than Market: Key Facts", now=now),
            _zacks_change(2, 702, "Stanley Black & Decker and Lincoln Electric face off", now=now, url=YSEOP_URL),
            _zacks_change(3, 703, REAL_ZACKS_NEWS[0], now=now, ticker="AMD"),
            _news_change(
                4, 704, available_at=now - timedelta(minutes=5),
                title="AMD (AMD): What You Should Know", source="seekingalpha/breaking", tickers=("AMD",),
            ),
        ],
        as_of=now - timedelta(seconds=1),
    )


@pytest.mark.parametrize(("skip", "expected"), [(True, {703, 704}), (False, {701, 702, 703, 704})])
def test_scheduled_analysis_skips_templates_only_while_switched_on(tmp_path, monkeypatch, skip, expected):
    etl, ai, base = _stack(tmp_path, mode="scheduled")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    intelligence = LocalCatalystIntelligence(
        base.db_path, ai, mode="scheduled", canonical_tickers=("NVDA", "AMD"),
        scheduled_skip_template_commentary=skip,
    )
    intelligence.initialize()
    _mixed_news(etl, now)
    intelligence.reconcile()

    # Templates take no candidate position: the two newest are templates.
    candidates = intelligence._scheduled_news_candidates(now=now, limit=2)
    assert [row["news_id"] for row in candidates] == sorted(expected)[:2]

    outcome = intelligence.run_scheduled(now=now)
    assert outcome["queued"] == len(expected)
    assert {job["news_id"] for job in _scheduled_news(ai)} == expected


def test_reads_mark_templates_skipped_and_leave_them_out_of_pending(tmp_path, monkeypatch):
    etl, ai, intelligence = _stack(tmp_path, mode="scheduled")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    _mixed_news(etl, now)
    intelligence.reconcile()
    keep_all = LocalCatalystIntelligence(
        intelligence.db_path, ai, mode="scheduled", canonical_tickers=("NVDA", "AMD"),
        scheduled_skip_template_commentary=False,
    )

    for owner in (True, False):
        with request_owner_access_context(owner):
            feed = intelligence.feed(as_of=now, window_hours=24, limit=20)
            # Visitor reads share an item cache; the other setting must not reuse it.
            unskipped = keep_all.feed(as_of=now, window_hours=24, limit=20)
        items = {item["news_id"]: item for item in feed["items"]}
        for news_id in (701, 702):
            assert items[news_id]["analysis_status"] == "skipped"
            assert items[news_id]["analysis_skip_reason"] == TEMPLATE_COMMENTARY_REASON
        for news_id in (703, 704):
            assert items[news_id]["analysis_status"] == "not_requested"
            assert "analysis_skip_reason" not in items[news_id]
        assert (feed["summary"]["count"], feed["summary"]["pending"]) == (4, 2)
        assert {item["analysis_status"] for item in unskipped["items"]} == {"not_requested"}
        assert unskipped["summary"]["pending"] == 4

    waiting = intelligence.feed(as_of=now, window_hours=24, limit=20, analysis_status="not_requested")
    assert {item["news_id"] for item in waiting["items"]} == {703, 704}
    skipped = intelligence.feed(as_of=now, window_hours=24, limit=20, analysis_status="skipped")
    assert {item["news_id"] for item in skipped["items"]} == {701, 702}
    batch = intelligence.batch(["NVDA", "AMD"], as_of=now, window_hours=24, limit=20)
    assert batch["results"]["NVDA"]["summary"]["count"] == 2
    assert batch["results"]["NVDA"]["summary"]["pending"] == 0
    assert batch["results"]["AMD"]["summary"]["pending"] == 2


def test_a_requested_template_shows_its_job_instead(tmp_path, monkeypatch):
    etl, ai, intelligence = _stack(tmp_path, mode="scheduled")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    _mixed_news(etl, now)
    intelligence.reconcile()
    # An owner may still ask for one explicitly.
    job = intelligence.request_analysis(701, force=False)
    assert job["status"] in {"pending", "queued"}
    feed = intelligence.feed(as_of=now + timedelta(seconds=1), window_hours=24, limit=20)
    item = next(item for item in feed["items"] if item["news_id"] == 701)
    assert item["analysis_status"] in {"pending", "queued"}
    assert "analysis_skip_reason" not in item


def _service(intelligence, ai) -> PersonalCatalystService:
    return PersonalCatalystService(
        type("SettingsStub", (), {"cache_db_path": str(intelligence.db_path), "model": "gpt-5.6-terra", "reasoning": "max"})(),
        intelligence=intelligence,
        ai_repository=ai,
        personal_config=PersonalConfig(features=FeatureConfig(catalyst_mode="read")),
        ai_settings=type("AISettingsStub", (), {"personal_etl_enabled": True})(),
    )


def test_public_reads_do_not_report_templates_as_waiting_for_chinese(tmp_path, monkeypatch):
    etl, ai, intelligence = _stack(tmp_path, mode="scheduled")
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    _mixed_news(etl, now)
    intelligence.reconcile()
    service = _service(intelligence, ai)

    for owner in (True, False):
        with request_owner_access_context(owner):
            page = service.feed(as_of=now, window_hours=24, limit=20, page_mode="visible")
            full = service.feed(as_of=now, window_hours=24, limit=20)
            batch = service.batch(["NVDA", "AMD"], as_of=now, window_hours=24, limit=20)
            detail = service.news(701, as_of=now)
        # English news without a Chinese title stays hidden; only the two
        # that will be analyzed count as waiting for Chinese copy.
        assert page["items"] == [] and page["hidden_unanalyzed"] == 2
        assert full["items"] == [] and full["hidden_unanalyzed"] == 2
        assert full["summary"]["pending"] == 2
        assert batch["results"]["NVDA"]["hidden_unanalyzed"] == 0
        assert batch["results"]["AMD"]["hidden_unanalyzed"] == 2
        assert detail["item"]["analysis_status"] == "skipped"
        assert detail["item"]["analysis_skip_reason"] == TEMPLATE_COMMENTARY_REASON


def test_template_hotspots_do_not_hold_back_the_focus_cycle(tmp_path, monkeypatch):
    etl, ai, intelligence = _stack(tmp_path, mode="scheduled")
    first_now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local_module, "_utc_now", lambda: first_now)
    _apply_news(
        etl,
        [
            _news_change(1, 590, available_at=first_now - timedelta(minutes=2)),
            _zacks_change(2, 591, "Advanced Micro Devices (AMD) Dips More Than Broader Market", now=first_now, ticker="AMD"),
        ],
        as_of=first_now - timedelta(minutes=1),
    )
    intelligence.reconcile()
    hotspots = intelligence.hotspots(limit=20, now=first_now)
    # Both are hotspot representatives, so the template could block the cycle.
    assert {item["representative_news_id"] for item in hotspots["items"]} == {590, 591}

    first = intelligence.run_scheduled(now=first_now)
    assert first["queued"] == 1
    (news_job,) = _scheduled_news(ai)
    assert news_job["news_id"] == 590
    with sqlite3.connect(ai.path) as connection:
        job_id = connection.execute("SELECT job_id FROM ai_jobs WHERE job_type='news_impact'").fetchone()[0]
    _finish_job(ai, job_id, _news_result(news_id=590, change_sequence=1, content_hash="hash-590-1"))
    second_now = datetime.now(timezone.utc).replace(microsecond=0) + timedelta(minutes=1)
    monkeypatch.setattr(local_module, "_utc_now", lambda: second_now)
    intelligence.reconcile()

    second = intelligence.run_scheduled(now=second_now)
    assert second["queued"] == 1
    with sqlite3.connect(ai.path) as connection:
        focus = connection.execute("SELECT payload_json FROM ai_jobs WHERE job_type='market_focus'").fetchone()
    assert focus is not None
    events = json.loads(focus[0])["events"]
    assert [event["title_zh"] for event in events] == ["英伟达发布新一代芯片平台"]


def test_the_switch_reaches_both_builders(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: _runtime_settings())
    settings = _worker_config(tmp_path)
    options: dict = {}

    class Factory:
        def __init__(self, _path, _repository, **kwargs):
            options.update(kwargs)

        def initialize(self):
            pass

    base = NS(ai=NS(model="gpt-5.6-terra", reasoning="max"), features=NS(catalyst_mode="manual"))
    asyncio.run(_build_local_intelligence(base, settings, factory=Factory))
    assert options["scheduled_skip_template_commentary"] is True
    off = NS(**vars(base), catalyst=NS(scheduled_skip_template_commentary=False))
    asyncio.run(_build_local_intelligence(off, settings, factory=Factory))
    assert options["scheduled_skip_template_commentary"] is False

    ai_settings = luna_settings(tmp_path / "ai-jobs.db")
    for flag in (True, False):
        service = PersonalCatalystService(
            type("SettingsStub", (), {"cache_db_path": tmp_path / "cache.db"})(),
            personal_config=PersonalConfig(catalyst=CatalystConfig(scheduled_skip_template_commentary=flag)),
            ai_settings=ai_settings,
        )
        assert service.intelligence.scheduled_skip_template_commentary is flag


def test_the_switch_is_on_by_default_and_in_the_example_config():
    assert CatalystConfig().scheduled_skip_template_commentary is True
    assert load_personal_config().catalyst.scheduled_skip_template_commentary is True
    assert CatalystConfig(scheduled_skip_template_commentary=False).scheduled_skip_template_commentary is False


# --- 4. Reasoning efforts for GPT models -----------------------------------------


def test_config_mirror_matches_the_runtime():
    assert personal_config_module.OPENAI_REASONING_EFFORTS == runtime.OPENAI_REASONING_EFFORTS == ("max", "xhigh", "high")
    for model in (runtime.LUNA_MODEL, runtime.OFFICIAL_OPENAI_MODEL):
        for effort in runtime.OPENAI_REASONING_EFFORTS:
            assert runtime.analysis_identity_supported(model, effort)
        for effort in ("medium", "low", "none", "minimal", None):
            assert not runtime.analysis_identity_supported(model, effort)
    assert runtime.analysis_identity_supported(runtime.OFFICIAL_CLAUDE_MODEL, "xhigh")
    for effort in ("max", "high"):
        assert not runtime.analysis_identity_supported(runtime.OFFICIAL_CLAUDE_MODEL, effort)
        assert not runtime.analysis_identity_supported(runtime.SONNET_MODEL, effort)


@pytest.mark.parametrize("effort", ["max", "xhigh", "high"])
def test_gpt_news_reasoning_accepts_three_efforts(effort):
    assert AIConfig(news_model="gpt-5.6-luna", news_reasoning=effort).news_reasoning == effort
    assert AIConfig(model="gpt-5.6-luna", reasoning=effort).reasoning == effort
    assert AIConfig(model="gpt-5.6-terra", reasoning=effort, max_concurrency=1).reasoning == effort
    settings = Settings(_env_file=None, openai_news_model="gpt-5.6-luna", openai_news_reasoning=effort)
    assert runtime.model_identity_for_job(settings, "news_impact") == ("gpt-5.6-luna", effort)


@pytest.mark.parametrize("effort", ["medium", "low", "none", "minimal", "ultra"])
def test_unsupported_efforts_are_rejected(effort):
    with pytest.raises(ValidationError):
        AIConfig(news_model="gpt-5.6-luna", news_reasoning=effort)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_news_model="gpt-5.6-luna", openai_news_reasoning=effort)


@pytest.mark.parametrize("effort", ["max", "high"])
def test_claude_tasks_keep_xhigh(effort):
    with pytest.raises(ValidationError):
        AIConfig(news_model="claude-haiku-5-5", news_reasoning=effort)
    with pytest.raises(ValidationError):
        AIConfig(reasoning=effort)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_news_model="claude-haiku-5-5", openai_news_reasoning=effort)


@pytest.mark.parametrize("effort", ["max", "xhigh", "high"])
def test_example_config_loads_with_each_news_effort(tmp_path, effort):
    path = Path(personal_config_module.DEFAULT_PERSONAL_CONFIG_PATH)
    text = path.read_text(encoding="utf-8")
    assert 'news_reasoning = "max"' in text
    changed = tmp_path / "personal.toml"
    changed.write_text(text.replace('news_reasoning = "max"', f'news_reasoning = "{effort}"'), encoding="utf-8")
    assert load_personal_config(changed).ai.news_reasoning == effort


@pytest.mark.parametrize("effort", ["max", "xhigh", "high"])
def test_news_effort_reaches_the_request(tmp_path, effort):
    base = luna_settings(tmp_path / "x")
    base.openai_model, base.openai_reasoning = runtime.OFFICIAL_CLAUDE_MODEL, "xhigh"
    base.openai_news_model, base.openai_news_reasoning = runtime.LUNA_MODEL, effort
    selected = runtime.settings_for_job(base, "news_impact")
    assert (selected.openai_model, selected.openai_reasoning) == (runtime.LUNA_MODEL, effort)
    assert runtime.runtime_configuration_valid(selected)
    for data in (luna_payload(), summary_payload(), article_payload()):
        assert runtime._create_params(selected, "news_impact", data)["reasoning"] == {"effort": effort}


def test_a_queued_job_from_another_effort_is_retired_before_submission(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _pending_luna_job(repo, luna_payload(), runtime.LUNA_WEB_NEWS_IDENTITY)
    assert repo.get_job(job_id)["reasoning"] == "max"

    async def no_submission(*_args, **_kwargs):
        raise AssertionError("a retired job must not reach the provider")

    monkeypatch.setattr(runtime, "submit_background", no_submission)
    settings = luna_settings(repo.path)
    settings.openai_reasoning = "xhigh"
    claimed = repo.claim_due("owner", 60)
    asyncio.run(worker.process_job(repo, settings, claimed, "owner"))
    row = repo.get_job(job_id)
    assert (row["status"], row["error_code"]) == ("failed", "runtime_configuration_changed")
    assert row["submission_started_at"] is None and row["openai_response_id"] is None
    # The structural identity holds no effort; the stored effort decides.
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL, payload=luna_payload()) == (
        row["schema_version"], row["schema_sha256"],
    )


def test_the_scheduler_rebuilds_a_retired_job_with_the_new_effort(tmp_path, monkeypatch):
    first_now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = {"now": first_now}
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock["now"])
    monkeypatch.setattr(ai_jobs_repository_module, "_utcnow", lambda: clock["now"])
    etl, ai, base = _stack(tmp_path, mode="scheduled")

    def intelligence(effort: str) -> LocalCatalystIntelligence:
        engine = LocalCatalystIntelligence(
            base.db_path, ai, mode="scheduled", canonical_tickers=("NVDA",),
            news_model=runtime.LUNA_MODEL, news_reasoning=effort,
        )
        engine.initialize()
        return engine

    _apply_news(etl, [_news_change(1, 801, available_at=first_now - timedelta(minutes=2))], as_of=first_now - timedelta(minutes=1))
    before = intelligence("max")
    before.reconcile()
    assert before.run_scheduled(now=first_now)["queued"] == 1
    (job,) = _scheduled_news(ai)
    assert (job["model"], job["reasoning"]) == (runtime.LUNA_MODEL, "max")
    with sqlite3.connect(ai.path) as connection:
        job_id = connection.execute("SELECT job_id FROM ai_jobs").fetchone()[0]
    # The worker of the new configuration retires it before submission.
    _fail_job(ai, job_id, "runtime_configuration_changed")

    clock["now"] = first_now + timedelta(hours=1)
    after = intelligence("xhigh")
    after.reconcile()
    assert after.run_scheduled(now=clock["now"])["queued"] == 1
    assert [(job["news_id"], job["reasoning"]) for job in _scheduled_news(ai)] == [(801, "max"), (801, "xhigh")]
