"""2026-10-10 news and hotspot analysis failures, replayed from production.

The Luna fixtures are failed paid rows copied verbatim (payload and stored
provider receipt). No test contacts a provider.
"""

from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import AIJobRepository
from test_ai_jobs_zh_contract import (
    _market_focus_payload,
    _market_focus_result,
    _news_payload,
    _news_result,
)
from test_luna_news_web_fallback import payload as luna_payload
from test_luna_news_web_fallback import response as luna_response
from test_luna_news_web_fallback import settings as luna_settings


FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "ai_luna_news_failures_20261009.json").read_text(
        encoding="utf-8"
    )
)["luna_news"]
_ARTICLE = {
    "status": "available",
    "text": "英伟达公布新芯片。",
    "source_url": "https://www.reuters.com/technology/nvidia-chip",
    "fetched_at": "2026-10-09T00:00:00Z",
    "truncated": False,
}


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


def _production_news_payload(**changes):
    # The keys every production news payload carries (fixture row 0).
    payload = {
        **_news_payload(),
        "analysis_revision": 1,
        "fetched_at": "2026-10-09T12:41:20.546679Z",
        "published_at": "2026-10-09T12:38:34Z",
        "source": "seekingalpha/breaking",
        "source_count": 1,
        "source_ticker_hints": ["NVDA"],
        "sources": ["seekingalpha/breaking"],
        "summary": "[NVDA]",
        "url": "https://seekingalpha.com/news/4651723",
    }
    payload.update(changes)
    return payload


def _news_field(text, field="summary_zh", **payload_changes):
    result = _news_result()
    result[field] = [text] if field in {"key_factors", "uncertainty_notes", "affected_sectors"} else text
    data = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _production_news_payload(**payload_changes),
    )
    return data[field][0] if isinstance(data[field], list) else data[field]


def _focus_field(text, field="headline_summary", **payload_changes):
    result = _market_focus_result()
    result[field] = text
    return validate_result(
        "market_focus",
        json.dumps(result, ensure_ascii=False),
        _market_focus_payload(**payload_changes),
    )[field]


def _pending_luna_job(repo, payload, identity):
    job, _ = repo.create_job(
        job_type="news_impact", payload=payload, model=runtime.LUNA_MODEL, reasoning="max",
        execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["news_impact"],
        schema_version=identity[0], schema_sha256=identity[1], max_queued=10,
    )
    return job["job_id"]


def _submit_pending(repo, monkeypatch):
    submitted = []

    async def submit(*_args, prepared, **_kwargs):
        submitted.append(prepared.params)
        return NS(id="resp_policy_transition", status="queued", model=runtime.LUNA_MODEL)

    monkeypatch.setattr(runtime, "submit_background", submit)
    claimed = repo.claim_due("owner", 60)
    asyncio.run(worker.process_job(repo, luna_settings(repo.path), claimed, "owner"))
    return submitted


# --- A. Validator false positives -------------------------------------------


@pytest.mark.parametrize("sample", FIXTURES, ids=[item["job_id"] for item in FIXTURES])
def test_every_complete_production_luna_failure_now_validates(sample):
    receipt = copy.deepcopy(sample["receipt"])
    result = runtime.receipt_result(receipt, "news_impact", sample["payload"])
    assert receipt == sample["receipt"]
    assert result == validate_result(
        "news_impact", json.dumps(result, ensure_ascii=False), sample["payload"],
    )
    narrative = json.dumps(
        {key: result[key] for key in result if key not in {"content_hash"}},
        ensure_ascii=False,
    )
    for domain in ("globenewswire.com", "sec.gov", "tradingview.com", "businesswire.com", "cnbc.com"):
        assert f"({domain})" not in narrative
    assert "https://" not in narrative


def test_domain_label_must_name_the_linked_site():
    url = "https://www.sec.gov/Archives/edgar/data/1137774/d11107dex991.htm"
    receipt = runtime.openai_receipt(luna_response(
        calls=[dict(type="web_search_call", id="ws_1", status="completed",
                    action=dict(type="search", sources=[dict(url=url, title="核验来源")]))],
        text=json.dumps({
            **_news_result(),
            "news_id": 1, "change_sequence": 1, "content_hash": "hash-1",
            "summary_zh": f"暂停期限延长至2027年1月31日。([sec.gov]({url}))",
            "causal_summary": f"监管命令要求整改。 ([www.sec.gov]({url})) 可能限制新单销售。",
            "key_factors": [f"命令已披露（[ec.gov]({url})）"],
        }, ensure_ascii=False),
    ))
    result = runtime.receipt_result(receipt, "news_impact", luna_payload())
    assert result["summary_zh"] == "暂停期限延长至2027年1月31日。"
    assert result["causal_summary"] == "监管命令要求整改。可能限制新单销售。"
    # A label that is not the linked site is reduced to its text; a bare
    # domain in brackets then leaves the published prose entirely.
    assert result["key_factors"] == ["命令已披露"]


def test_bare_trusted_url_in_brackets_is_a_citation_but_unbound_url_still_rejects():
    url = "https://www.sec.gov/Archives/edgar/data/1/a.htm"

    def receipt_for(text):
        return runtime.openai_receipt(luna_response(
            calls=[dict(type="web_search_call", id="ws_1", status="completed",
                        action=dict(type="search", sources=[dict(url=url, title="核验来源")]))],
            text=json.dumps({**_news_result(), "news_id": 1, "change_sequence": 1,
                             "content_hash": "hash-1", "summary_zh": text}, ensure_ascii=False),
        ))

    result = runtime.receipt_result(receipt_for(f"公司提交了整改计划（{url}）。"), "news_impact", luna_payload())
    assert result["summary_zh"] == "公司提交了整改计划。"
    with pytest.raises(ValueError, match="ai_news_unbound_or_unhandled_url"):
        runtime.receipt_result(
            receipt_for("公司提交了整改计划（https://www.sec.gov/other.htm）。"),
            "news_impact", luna_payload(),
        )


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("公司公布融资安排。(globenewswire.com)", "公司公布融资安排。"),
        ("公司概述1.25亿美元额度 (tradingview.com)", "公司概述1.25亿美元额度"),
        ("会议延期（sec.gov、cnbc.com）至10月19日。", "会议延期至10月19日。"),
        ("资料来自投资者关系网站（来源：investor.kimberly-clark.com）。", "资料来自投资者关系网站。"),
    ],
)
def test_bracketed_source_domains_leave_the_published_text(text, expected):
    assert _news_field(text) == expected
    assert _focus_field(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "(Globenewswire.com)公司宣布回购。",
        "公司官网globenewswire.com显示回购计划。",
        "来源https://example.com/a。",
    ],
)
def test_domains_outside_a_bracketed_citation_are_still_prose(text):
    with pytest.raises(ValueError):
        _news_field(text)


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        ("输入article标记为可用，已按正文分析。", {"article_status": "available", "article": _ARTICLE}),
        ("输入article字段缺失，只能依据标题判断。", {}),
        ("输入的source为美国财经资讯网站。", {}),
        ("summary只有代码标记，未提供实质信息。", {}),
        ("article_reason为http_403，正文不可用。", {"article_status": "unavailable", "article_reason": "http_403"}),
        ("正文因unsupported_encoding无法解析。", {"article_status": "unavailable", "article_reason": "unsupported_encoding"}),
        ("insufficient_context设为真，affected_stocks留空。", {}),
    ],
)
def test_exact_payload_field_names_are_not_english_prose(text, changes):
    assert _news_field(text, field="uncertainty_notes", **changes)


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        ("股票代码allowed_tickers上涨", {}),
        ("输入my_article_status为不可用", {"article_status": "unavailable"}),
        ("输入article_status_extra为不可用", {"article_status": "unavailable"}),
        ("正文article_status为unavailable", {"article_status": "not_requested"}),
        ("正文因http_404无法读取", {"article_status": "unavailable", "article_reason": "http_403"}),
        ("The article is unavailable and stocks are falling", {}),
    ],
)
def test_field_name_rule_stays_exact_and_outside_security_context(text, changes):
    with pytest.raises(ValueError):
        _news_field(text, field="uncertainty_notes", **changes)


def test_non_news_text_gets_no_field_name_exemption():
    with pytest.raises(ValueError):
        _focus_field("输入article标记为可用。")


def test_http_status_is_kept_only_when_it_matches_the_input_failure():
    assert _news_field(
        "原始链接因HTTP 401无法读取。",
        field="uncertainty_notes",
        article_status="unavailable",
        article_reason="http_401",
    ) == "原始链接因未获授权（状态码401）无法读取。"
    result = _news_result()
    result["affected_stocks"][0]["reason"] = "正文因HTTP 403不可得，影响判断有限。"
    data = validate_result(
        "news_impact",
        json.dumps(result, ensure_ascii=False),
        _production_news_payload(article_status="unavailable", article_reason="http_403"),
    )
    assert data["affected_stocks"][0]["reason"] == "正文因访问被拒绝（状态码403）不可得，影响判断有限。"
    for changes in (
        {"article_status": "unavailable", "article_reason": "http_403"},
        {"article_reason": "http_401"},
        {},
    ):
        with pytest.raises(ValueError):
            _news_field("原始链接因HTTP 401无法读取。", field="uncertainty_notes", **changes)


def test_semicolon_ends_the_security_prefix_for_a_source_bound_name():
    title = "XMax (XMAX) to acquire Hexa Creation, expanding AI infrastructure strategy"
    text = "拟收购Hexa Creation全部已发行及流通股份；Hexa Creation聚焦功率半导体。"
    assert _news_field(text, field="headline_summary", title=title) == text
    for rejected in ("股票；TSLA上涨", "股票代码；TSLA", "流通股份，Hexa Creation股价上涨"):
        with pytest.raises(ValueError):
            _news_field(rejected, title=title)


def test_macro_statistic_movement_names_the_statistic_not_a_stock():
    title = "Pantheon sees September CPI rising 0.6% as gasoline prices jump"
    text = "潘森宏观经济学预计：汽油价格跳涨将推动美国9月CPI上涨0.6%"
    assert _news_field(text, field="title_zh", title=title) == text
    assert _news_field("美国9月PPI下跌0.2%", field="title_zh") == "美国9月PPI下跌0.2%"
    for rejected in ("CPI股价上涨", "股票代码CPI上涨"):
        with pytest.raises(ValueError):
            _news_field(rejected, field="title_zh", title=title)


@pytest.mark.parametrize(
    "text",
    [
        "麦克尤恩矿业签署协议，以现金500万美元加发现矿业股份5000万美元出售安大略两处资产。",
        "对价为现金500万美元加发现矿业普通股5000万美元，需常规交割条件与监管批准。",
        "持有该公司股份5%，另有普通股300万股。",
    ],
)
def test_amounts_after_share_nouns_are_quantities_not_codes(text):
    assert _focus_field(text) == text


@pytest.mark.parametrize(
    "text",
    ["股票600519上涨", "腾讯（00700）股价上涨", "证券代码700股价上涨", "股票600519股价下跌"],
)
def test_numeric_security_codes_still_require_binding(text):
    with pytest.raises(ValueError, match="unbound_numeric_security_code"):
        _focus_field(text)


@pytest.mark.parametrize(
    "text",
    [
        "20家医疗保健公司获A+每股收益修正量化评级；安进位列其中。",
        "A+评级反映分析师盈利估计的正向变化。",
        "主要终点达到统计学显著（p<0.001），共纳入n=712例患者。",
        "p值低于0.05。",
        "若继续存续，随后利率将转为复合SOFR加1.730%。",
        "该笔贷款利率为LIBOR加150个基点。",
        "属于企业IT服务订单，金额未知。",
        "以下事实来自对输入URL对应原文的另行检索。",
        "报道称苹果削减部分iPhone 18 Pro零部件订单。",
        "iPhone 18 Pro Max销量预期下调。",
    ],
)
def test_grades_statistics_rate_spreads_and_generic_initialisms(text):
    assert _news_field(text) == text


@pytest.mark.parametrize(
    "text",
    ["A股价上涨", "A公司宣布回购", "股票代码SOFR加1%", "IT股价上涨", "URL股票下跌", "TSLA上涨",
     "Galaxy 18 Pro销量预期下调。", "iPhone 18 Pro Deluxe销量下调。"],
)
def test_single_letters_and_initialisms_keep_the_security_red_line(text):
    with pytest.raises(ValueError):
        _news_field(text)


# --- C. Limits and the identity transition they cause ------------------------


def test_repository_defaults_track_the_shared_budget_without_blocking():
    from app.config import Settings
    from app.personal_config import load_personal_config

    config = load_personal_config()
    assert config.model_budget.daily_budget_usd > 0
    assert config.model_budget.enforce_limit is False
    assert Settings.model_fields["openai_background_poll_timeout_seconds"].default == 3600.0


def test_token_ledger_never_blocks_under_the_tracking_budget(tmp_path):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _pending_luna_job(repo, luna_payload(), runtime.LUNA_WEB_NEWS_IDENTITY)
    assert repo.claim_due("owner", 60)["job_id"] == job_id
    state = repo.mark_submission_started(
        job_id, "owner",
        daily_token_limit=102_400,
        shared_daily_budget_usd=10.0,
        shared_budget_enforce_limit=False,
        max_concurrency=4,
    )
    assert state == "started"
    snapshot = repo.budget_snapshot(
        daily_limit=0, daily_budget_usd=0, daily_token_limit=102_400,
        shared_daily_budget_usd=10.0, shared_budget_enforce_limit=False,
        model=runtime.LUNA_MODEL, max_concurrency=4,
    )
    assert snapshot["token_budget_available"] is True
    assert snapshot["budget_mode"] == "tracking"


def test_previous_identities_stay_current_only_for_the_exact_policy(monkeypatch):
    prompt = runtime.PROMPT_VERSIONS["news_impact"]
    for stored in (
        runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY,
        runtime.LEGACY_LUNA_NEWS_IDENTITY,
        runtime.LUNA_WEB_NEWS_IDENTITY,
    ):
        assert runtime.schema_identity_current("news_impact", prompt, *stored, model=runtime.LUNA_MODEL)
    assert runtime.schema_identity_current("news_impact", prompt, *runtime.NEWS_CONTENT_SCHEMA_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL)
    assert runtime.schema_identity_current(
        "earnings_impact", runtime.PROMPT_VERSIONS["earnings_impact"],
        *runtime.LEGACY_OPENAI_EARNINGS_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL,
    )
    # A Luna-only identity never makes another model's row current.
    assert not runtime.schema_identity_current("news_impact", prompt, *runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL)
    assert not runtime.schema_identity_current("news_impact", prompt, *runtime.LEGACY_LUNA_NEWS_IDENTITY, model=runtime.OFFICIAL_CLAUDE_MODEL)

    monkeypatch.setitem(runtime.AI_TASK_MAX_OUTPUT_TOKENS, "news_impact", 98_304)
    for stored in (runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY, runtime.LUNA_WEB_NEWS_IDENTITY):
        assert not runtime.schema_identity_current("news_impact", prompt, *stored, model=runtime.LUNA_MODEL)
    assert not runtime.schema_identity_current("news_impact", prompt, *runtime.NEWS_CONTENT_SCHEMA_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL)


@pytest.mark.parametrize(
    "stored_identity",
    [runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY, runtime.LEGACY_LUNA_NEWS_IDENTITY],
    ids=["always-web", "legacy-plain"],
)
def test_queued_job_from_the_previous_policy_is_submitted_under_the_new_one(
    tmp_path, monkeypatch, stored_identity,
):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _pending_luna_job(repo, luna_payload(), stored_identity)
    (params,) = _submit_pending(repo, monkeypatch)
    row = repo.get_job(job_id)
    assert row["error_code"] != "runtime_configuration_changed"
    assert row["status"] in {"queued", "in_progress"}
    assert row["openai_response_id"] == "resp_policy_transition"
    assert params["max_output_tokens"] == 65_536
    assert params["tools"][0]["type"] == "web_search"


def test_completed_always_web_result_stays_in_the_feed_without_a_new_paid_job(tmp_path):
    from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
    from test_catalyst_local_intelligence import _apply_news, _finish_job, _news_change, _stack
    from test_catalyst_local_intelligence import _news_result as catalyst_news_result

    etl, repo, initial = _stack(tmp_path)
    service = LocalCatalystIntelligence(
        initial.db_path, repo, mode="manual", canonical_tickers=("NVDA",),
        model=runtime.LUNA_MODEL, reasoning="max",
    )
    service.initialize()
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 900, available_at=now - timedelta(minutes=2))], as_of=now)
    service.reconcile()
    job = service.request_analysis(900, force=False)
    with sqlite3.connect(repo.path) as db:
        db.execute(
            "UPDATE ai_jobs SET schema_version=?,schema_sha256=? WHERE job_id=?",
            (*runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY, job["job_id"]),
        )
    _finish_job(repo, job["job_id"], catalyst_news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    service.reconcile()
    assert service.feed(as_of=datetime.now(timezone.utc), limit=10)["items"][0]["analysis"] is not None
    assert service.request_analysis(900, force=False)["job_id"] == job["job_id"]
    with sqlite3.connect(repo.path) as db:
        assert db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 1
