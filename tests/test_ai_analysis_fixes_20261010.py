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
from app.tools import recover_ai_schema_results as recovery
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


def _luna_receipt(urls, **fields):
    return runtime.openai_receipt(luna_response(
        calls=[dict(type="web_search_call", id="ws_1", status="completed",
                    action=dict(type="search", sources=[dict(url=url, title="核验来源") for url in urls]))],
        text=json.dumps({
            **_news_result(), "news_id": 1, "change_sequence": 1, "content_hash": "hash-1", **fields,
        }, ensure_ascii=False),
    ))


def test_bracketed_markdown_citations_are_stripped_whatever_their_label():
    url = "https://www.sec.gov/Archives/edgar/data/1137774/d11107dex991.htm"
    receipt = _luna_receipt(
        [url],
        summary_zh=f"暂停期限延长至2027年1月31日。([sec.gov]({url}))",
        causal_summary=f"监管命令要求整改。 ([www.sec.gov]({url})) 可能限制新单销售。",
    )
    result = runtime.receipt_result(receipt, "news_impact", luna_payload())
    assert result["summary_zh"] == "暂停期限延长至2027年1月31日。"
    assert result["causal_summary"] == "监管命令要求整改。可能限制新单销售。"
    # 2026-10-10 第三轮：括号里的 Markdown 引用不论标签和链接是否对得上都整段剥掉（原先标签
    # 对不上链接时留下「（ec.gov）」被拒）。
    mislabeled = _luna_receipt([url], key_factors=[f"命令已披露（[ec.gov]({url})）"])
    assert runtime.receipt_result(mislabeled, "news_impact", luna_payload())["key_factors"] == ["命令已披露"]


def test_bare_urls_in_brackets_are_stripped_whether_or_not_retrieved():
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
    # 2026-10-10 第三轮：没有联网取回过的网址也剥掉，不再拒绝。
    assert runtime.receipt_result(
        receipt_for("公司提交了整改计划（https://www.sec.gov/other.htm）。"),
        "news_impact", luna_payload(),
    )["summary_zh"] == "公司提交了整改计划。"


_RETRIEVED = [
    "https://www.globenewswire.com/news-release/2026/10/09/1/0/en/a.html",
    "https://www.tradingview.com/news/a/",
    "https://www.sec.gov/Archives/edgar/data/1/a.htm",
    "https://www.cnbc.com/2026/10/09/a.html",
    "https://investor.kimberly-clark.com/news/a",
]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("公司公布融资安排。(globenewswire.com)", "公司公布融资安排。"),
        ("公司概述1.25亿美元额度 (tradingview.com)", "公司概述1.25亿美元额度"),
        ("会议延期（sec.gov、cnbc.com）至10月19日。", "会议延期至10月19日。"),
        ("资料来自投资者关系网站（来源：investor.kimberly-clark.com）。", "资料来自投资者关系网站。"),
    ],
)
def test_bracketed_domains_of_retrieved_sites_leave_the_published_text(text, expected):
    receipt = _luna_receipt(_RETRIEVED, summary_zh=text)
    assert runtime.receipt_result(receipt, "news_impact", luna_payload())["summary_zh"] == expected


@pytest.mark.parametrize(
    "text",
    [
        "公司公布融资安排。(globenewswire.com)",
        "会议延期（sec.gov、othersite.com）至10月19日。",
    ],
)
def test_bracketed_domains_without_a_retrieved_site_stay_for_the_language_gate(text):
    receipt = _luna_receipt(_RETRIEVED[2:], summary_zh=text)
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        runtime.receipt_result(receipt, "news_impact", luna_payload())
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _news_field(text)
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        _focus_field(text)


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
    ("text", "changes", "published"),
    [
        ("输入article标记为可用，已按正文分析。", {"article_status": "available", "article": _ARTICLE},
         "输入新闻正文标记为可用，已按正文分析。"),
        ("输入article字段缺失，只能依据标题判断。", {}, "输入新闻正文字段缺失，只能依据标题判断。"),
        ("输入的source为美国财经资讯网站。", {}, "输入的来源为美国财经资讯网站。"),
        ("summary只有代码标记，未提供实质信息。", {}, "摘要只有代码标记，未提供实质信息。"),
        ("article_reason为http_403，正文不可用。", {"article_status": "unavailable", "article_reason": "http_403"},
         "正文缺失原因为状态码403，正文不可用。"),
        ("正文因unsupported_encoding无法解析。", {"article_status": "unavailable", "article_reason": "unsupported_encoding"},
         "正文因编码不支持无法解析。"),
        ("insufficient_context设为真，affected_stocks留空。", {}, "证据不足设为真，受影响个股留空。"),
    ],
)
def test_exact_payload_field_names_are_published_in_chinese(text, changes, published):
    assert _news_field(text, field="uncertainty_notes", **changes) == published


@pytest.mark.parametrize(
    ("text", "changes"),
    [
        ("股票代码allowed_tickers上涨", {}),
        ("输入my_article_status为不可用", {"article_status": "unavailable"}),
        ("输入article_status_extra为不可用", {"article_status": "unavailable"}),
        ("正文因http_404无法读取", {"article_status": "unavailable", "article_reason": "http_403"}),
        ("The article is unavailable and stocks are falling", {}),
    ],
)
def test_field_name_rule_stays_exact_and_outside_security_context(text, changes):
    with pytest.raises(ValueError):
        _news_field(text, field="uncertainty_notes", **changes)


def test_a_status_value_that_differs_from_the_input_is_left_untranslated():
    # 2026-10-10 口径变更：状态值对不上本条输入时照旧不翻译；剩下的「unavailable」按词条
    # 原样发布，不再拒绝（原先在上面的拒绝清单里）。
    assert _news_field(
        "正文article_status为unavailable", field="uncertainty_notes", article_status="not_requested",
    ) == "正文正文状态为unavailable"


def test_non_news_text_gets_no_field_name_exemption():
    # 2026-10-10 口径变更：热点照旧不翻译字段名；「article」按词条原样发布，不再拒绝。
    assert _focus_field("输入article标记为可用。") == "输入article标记为可用。"


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
        # 2026-10-10 口径变更：状态码对不上输入时照旧不翻译；「HTTP 401」按词条原样发布，
        # 不再拒绝。
        assert _news_field(
            "原始链接因HTTP 401无法读取。", field="uncertainty_notes", **changes,
        ) == "原始链接因HTTP 401无法读取。"


def test_semicolon_ends_the_security_prefix_for_a_source_bound_name():
    title = "XMax (XMAX) to acquire Hexa Creation, expanding AI infrastructure strategy"
    text = "拟收购Hexa Creation全部已发行及流通股份；Hexa Creation聚焦功率半导体。"
    assert _news_field(text, field="headline_summary", title=title) == text
    assert _news_field("股票；TSLA上涨", title=title) == "股票；TSLA上涨"
    # 2026-10-10 第三轮：分号后的「TSLA」没有证券标记；「Hexa Creation」不是代码样词元，接
    # 股价也按词条发布（原先这两句在拒绝清单里）。
    for published in ("股票代码；TSLA", "流通股份，Hexa Creation股价上涨"):
        assert _news_field(published, title=title) == published


def test_macro_statistic_movement_names_the_statistic_not_a_stock():
    title = "Pantheon sees September CPI rising 0.6% as gasoline prices jump"
    text = "潘森宏观经济学预计：汽油价格跳涨将推动美国9月CPI上涨0.6%"
    assert _news_field(text, field="title_zh", title=title) == text
    assert _news_field("美国9月PPI下跌0.2%", field="title_zh") == "美国9月PPI下跌0.2%"
    for text in ("CPI股价上涨", "股票代码CPI上涨"):
        assert _news_field(text, field="title_zh", title=title) == text


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
    ["证券代码700股价上涨", "股票代码600519股价下跌"],
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
    ["A股价上涨", "A公司宣布回购", "股票代码SOFR加1%", "IT股价上涨", "URL股票下跌", "TSLA上涨"],
)
def test_single_letters_and_initialisms_do_not_guess_stock_identity(text):
    assert _news_field(text) == text


@pytest.mark.parametrize("text", ["Galaxy 18 Pro销量预期下调。", "iPhone 18 Pro Deluxe销量下调。"])
def test_unlisted_product_names_are_terms_after_the_2026_10_10_policy(text):
    # 2026-10-10 口径变更：不在名单里的产品线和档位，不在证券语境时按名称放行（原先在上面的
    # 红线测试里，但它们没有证券语境）。
    assert _news_field(text) == text


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


# --- B. Luna searches only when the article body is missing ------------------


def test_luna_request_tools_follow_each_tasks_article():
    settings = luna_settings(Path("/nonexistent/news.db"))
    without = luna_payload()
    with_article = {**luna_payload(), "article_status": "available", "article": _ARTICLE}

    searching = runtime._create_params(settings, "news_impact", without)
    assert searching["tools"][0]["type"] == "web_search"
    assert searching["tool_choice"] == "required"
    assert searching["max_tool_calls"] == 1
    assert "必须先联网搜索原始事件" in searching["instructions"]
    assert "不得附加网址、Markdown链接或括号中的网站域名" in searching["instructions"]

    plain = runtime._create_params(settings, "news_impact", with_article)
    assert not {"tools", "tool_choice", "max_tool_calls", "include"} & set(plain)
    assert "不浏览网页" in plain["instructions"]
    assert "联网搜索" not in plain["instructions"]
    assert "不得照抄输入或输出的字段名与状态值" in plain["instructions"]
    assert plain["max_output_tokens"] == searching["max_output_tokens"] == 65_536

    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL, payload=without) == runtime.LUNA_WEB_NEWS_IDENTITY
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL, payload=with_article) == runtime.LUNA_ARTICLE_NEWS_IDENTITY
    assert runtime.max_tool_calls_for("news_impact", model=runtime.LUNA_MODEL, payload=without) == 1
    assert runtime.max_tool_calls_for("news_impact", model=runtime.LUNA_MODEL, payload=with_article) == 0
    assert runtime.budget_reservation_microusd(
        "news_impact", model=runtime.LUNA_MODEL, payload=with_article,
    ) < runtime.budget_reservation_microusd(
        "news_impact", model=runtime.LUNA_MODEL, payload=without,
    )


def test_other_models_and_job_types_never_take_the_luna_search_path():
    with_article = {**luna_payload(), "article_status": "available", "article": _ARTICLE}
    for model in (runtime.OFFICIAL_OPENAI_MODEL, runtime.OFFICIAL_CLAUDE_MODEL, runtime.SONNET_MODEL):
        assert not runtime.task_uses_web_search("news_impact", luna_payload(), model=model)
    assert not runtime.task_uses_web_search("market_focus", {}, model=runtime.LUNA_MODEL)
    assert not runtime.task_uses_web_search("news_impact", with_article, model=runtime.LUNA_MODEL)


@pytest.mark.parametrize(
    "stored_identity",
    [runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY, runtime.LEGACY_LUNA_NEWS_IDENTITY],
    ids=["always-web", "legacy-plain"],
)
def test_queued_job_with_an_article_is_submitted_without_search(tmp_path, monkeypatch, stored_identity):
    repo = AIJobRepository(tmp_path / "news.db")
    payload = {**luna_payload(), "article_status": "available", "article": _ARTICLE}
    job_id = _pending_luna_job(repo, payload, stored_identity)
    (params,) = _submit_pending(repo, monkeypatch)
    row = repo.get_job(job_id)
    assert row["error_code"] != "runtime_configuration_changed"
    assert row["openai_response_id"] == "resp_policy_transition"
    assert not {"tools", "tool_choice", "max_tool_calls", "include"} & set(params)
    assert row["budget_charge_microusd"] == runtime.budget_reservation_microusd(
        "news_impact", model=runtime.LUNA_MODEL, payload=payload,
    )


def test_both_current_luna_variants_are_current_for_reads_and_submission():
    prompt = runtime.PROMPT_VERSIONS["news_impact"]
    web = runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL)
    for stored in (runtime.LUNA_WEB_NEWS_IDENTITY, runtime.LUNA_ARTICLE_NEWS_IDENTITY):
        # Readers pass the model's default variant; the article variant is
        # still current.
        assert runtime.schema_identity_current(
            "news_impact", prompt, *stored, current_identity=web, model=runtime.LUNA_MODEL,
        )
    assert not runtime.schema_identity_current(
        "news_impact", prompt, *runtime.LUNA_ARTICLE_NEWS_IDENTITY, model=runtime.OFFICIAL_OPENAI_MODEL,
    )


# --- D. Paid results are recovered locally ------------------------------------


def _failed_receipt_row(repo, sample):
    job_id = _pending_luna_job(repo, sample["payload"], runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY)
    assert repo.claim_due("owner", 60)["job_id"] == job_id
    assert repo.mark_submission_started(job_id, "owner", max_concurrency=4) == "started"
    repo.link_background_response(job_id, "owner", sample["receipt"]["id"])
    repo.record_openai_result(job_id, "owner", sample["receipt"])
    repo.fail(job_id, "owner", "schema_validation_failed", usage=sample["receipt"]["usage"],
              detail=sample["production_error"])
    return job_id


def test_recovery_tool_republishes_production_receipts_without_provider_calls(tmp_path, monkeypatch, capsys):
    repo = AIJobRepository(tmp_path / "news.db")
    job_ids = [_failed_receipt_row(repo, sample) for sample in FIXTURES[:3]]
    before = {job_id: repo.get_job(job_id) for job_id in job_ids}

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must reuse the stored receipt")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(runtime, "submit_background", forbidden)
    monkeypatch.setattr(recovery, "get_settings", lambda: luna_settings(repo.path))

    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    assert recovery.main(["--failed-since", since, "--job-type", "news_impact"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in dry_run] == ["validated"] * 3
    assert all(repo.get_job(job_id)["status"] == "failed" for job_id in job_ids)

    assert recovery.main(["--failed-since", since, "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert {item["job_id"] for item in applied} == set(job_ids)
    assert {item["status"] for item in applied} == {"recovered"}
    for job_id, sample in zip(job_ids, FIXTURES[:3]):
        row = repo.get_job(job_id)
        assert row["status"] == "completed" and row["error_code"] is None and row["error_detail"] is None
        result = json.loads(row["result_json"])
        assert result == validate_result("news_impact", row["result_json"], sample["payload"])
        for key in ("budget_charge_microusd", "usage_output_tokens", "provider_result_json", "openai_response_id"):
            assert row[key] == before[job_id][key]
    assert recovery.main(["--failed-since", since, "--apply"]) == 1
    assert json.loads(capsys.readouterr().out) == []


def test_bulk_selection_skips_rows_that_would_need_a_provider_retrieve(tmp_path):
    repo = AIJobRepository(tmp_path / "news.db")
    with_receipt = _failed_receipt_row(repo, FIXTURES[0])
    job_id = _pending_luna_job(repo, {**luna_payload(), "news_id": 2}, runtime.LUNA_WEB_NEWS_IDENTITY)
    assert repo.claim_due("owner-2", 60)["job_id"] == job_id
    assert repo.mark_submission_started(job_id, "owner-2", max_concurrency=4) == "started"
    repo.link_background_response(job_id, "owner-2", "resp_without_receipt")
    repo.fail(job_id, "owner-2", "schema_validation_failed")
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert repo.recoverable_receipt_failures(since=since) == [with_receipt]
    assert repo.recoverable_receipt_failures(since=since, job_types=["market_focus"]) == []
    assert repo.recoverable_receipt_failures(since=datetime.now(timezone.utc) + timedelta(minutes=1)) == []


# --- E. Failed hotspot cycles carry their reason for the owner ----------------


def test_failed_focus_cycle_shows_the_validation_detail_to_the_owner_only(tmp_path, monkeypatch):
    from app.access import request_owner_access_context
    from app.services.catalysts import local_intelligence as local_module
    from test_catalyst_local_intelligence import _apply_news, _news_change, _stack

    etl, ai, intelligence = _stack(tmp_path)
    now = datetime(2030, 7, 16, 19, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    _apply_news(etl, [_news_change(1, 221, available_at=now - timedelta(minutes=10))], as_of=now - timedelta(minutes=9))
    revision = intelligence.reconcile()["prepared_revision"]
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    detail = "3 validation errors for VerifiedMarketFocusResult\nheadline_summary\n  Value error, unbound_numeric_security_code"
    claimed = ai.claim_due("owner", lease_seconds=60)
    assert claimed["job_id"] == cycle["job_id"]
    ai.fail(cycle["job_id"], "owner", "schema_validation_failed", detail=detail)
    intelligence.reconcile()

    owner_view = intelligence.latest_market_focus_cycle(now=now + timedelta(minutes=1))["cycle"]
    assert owner_view["status"] == "failed"
    assert owner_view["error_code"] == "schema_validation_failed"
    assert owner_view["error_detail"] == detail
    with request_owner_access_context(False):
        assert intelligence.market_focus_cycle(cycle["cycle_id"]) is None
        visitor = intelligence.latest_market_focus_cycle(now=now + timedelta(minutes=1))
    assert visitor["cycle"] is None


def test_failed_focus_cycle_is_published_once_its_paid_job_is_recovered(tmp_path, monkeypatch):
    from app.services.catalysts import local_intelligence as local_module
    from test_catalyst_local_intelligence import _apply_news, _focus_result, _news_change, _stack

    etl, ai, intelligence = _stack(tmp_path)
    now = datetime(2030, 7, 16, 19, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(local_module, "_utc_now", lambda: now)
    _apply_news(etl, [_news_change(1, 221, available_at=now - timedelta(minutes=10))], as_of=now - timedelta(minutes=9))
    revision = intelligence.reconcile()["prepared_revision"]
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    assert ai.claim_due("owner", lease_seconds=60)["job_id"] == cycle["job_id"]
    ai.fail(cycle["job_id"], "owner", "schema_validation_failed", detail="unbound_numeric_security_code")
    intelligence.reconcile()
    assert intelligence.latest_market_focus_cycle(now=now + timedelta(minutes=1))["cycle"]["status"] == "failed"

    # What recover_schema_validation_failure writes for the paid job.
    with sqlite3.connect(ai.path) as db:
        db.execute(
            """UPDATE ai_jobs SET status='completed',result_json=?,error_code=NULL,
                   error_detail=NULL,completed_at=? WHERE job_id=?""",
            (
                json.dumps(_focus_result(ai, cycle), ensure_ascii=False),
                (now - timedelta(seconds=30)).isoformat().replace("+00:00", "Z"),
                cycle["job_id"],
            ),
        )
    intelligence.reconcile()
    latest = intelligence.latest_market_focus_cycle(now=now + timedelta(minutes=1))["cycle"]
    assert latest["status"] == "completed"
    assert latest["error_code"] is None
    assert latest["result"] is not None
