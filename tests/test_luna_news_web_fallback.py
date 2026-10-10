from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace as NS

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from app.tools import recover_ai_schema_results as recovery
from test_ai_jobs import _settings
from test_catalyst_local_intelligence import _news_result


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context
    with request_owner_access_context(True):
        yield


def payload():
    return dict(news_id=1, change_sequence=1, content_hash="hash-1", title="NVIDIA launches chip",
                summary="NVIDIA announces a new chip.", source="Reuters", source_url="https://www.reuters.com/article/test",
                allowed_tickers=["NVDA"], article_status="unavailable")


def response(*, calls=None, text=None):
    return NS(id="resp_news_web", model=runtime.LUNA_MODEL, status="completed",
              output=[] if calls is None else calls,
              output_text=json.dumps(_news_result(news_id=1, change_sequence=1, content_hash="hash-1")) if text is None else text,
              usage=NS(input_tokens=100, output_tokens=50, total_tokens=150,
                       input_tokens_details=NS(cached_tokens=20), output_tokens_details=NS(reasoning_tokens=10)))


def search(status="completed", action="search"):
    return dict(type="web_search_call", id="ws_1", status=status,
                action=dict(type=action, sources=[dict(url="https://www.nvidia.com/en-us/about-nvidia/", title="公司公告")]))


def settings(path):
    result = _settings(path)
    result.openai_model = runtime.LUNA_MODEL
    return result


def started(tmp_path, *, legacy=False):
    repo = AIJobRepository(tmp_path / "news.db")
    version, digest = runtime.LEGACY_LUNA_NEWS_IDENTITY if legacy else runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL)
    job, _ = repo.create_job(job_type="news_impact", payload=payload(), model=runtime.LUNA_MODEL,
                            reasoning="max", execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["news_impact"],
                            schema_version=version, schema_sha256=digest, max_queued=10)
    claimed = repo.claim_due("owner", 600)
    assert repo.mark_submission_started(job["job_id"], "owner", daily_limit=100, daily_budget_usd=100,
                                        daily_token_limit=10_000_000, cooldown_seconds=0) == "started"
    repo.link_background_response(job["job_id"], "owner", "resp_news_web")
    return repo, repo.get_job(job["job_id"])


def test_request_is_model_aware_and_bounded(tmp_path):
    params = runtime._create_params(settings(tmp_path / "x"), "news_impact", payload())
    assert params["model"] == runtime.LUNA_MODEL
    assert params["reasoning"] == {"effort": "max"}
    assert params["tools"] == [{"type": "web_search", "search_context_size": "low", "external_web_access": True}]
    assert params["tool_choice"] == "required"
    assert params["max_tool_calls"] == 1
    assert params["include"] == ["web_search_call.action.sources"]
    assert "不浏览网页" not in params["instructions"]
    assert not runtime.build_runtime_request("news_impact", payload(), model=runtime.OFFICIAL_CLAUDE_MODEL).use_web_search
    assert not runtime.build_runtime_request("market_focus", {}, model=runtime.SONNET_MODEL).use_web_search


def test_search_open_and_unknown_accounting():
    open_call = dict(type="web_search_call", id="ws_2", status="completed", action=dict(type="open_page", url="https://www.nvidia.com/"))
    usage = runtime.response_usage(response(calls=[search(), open_call]))
    assert usage["web_search_requests"] == 1
    assert usage["web_fetch_requests"] == 1
    assert usage["cached_input_tokens"] == 20
    assert runtime.settled_usage_cost_microusd("news_impact", usage, model=runtime.LUNA_MODEL, fallback_microusd=90000) == 10081
    assert runtime.response_usage(response())["web_search_requests"] == 0
    for call in [search(status="failed"), search(action="new_unknown_action")]:
        usage = runtime.response_usage(response(calls=[call]))
        assert usage["web_search_requests"] is None
        assert runtime.settled_usage_cost_microusd("news_impact", usage, model=runtime.LUNA_MODEL, fallback_microusd=90000) == 90000


def test_generated_url_never_becomes_evidence_and_no_sources_is_safe():
    receipt = runtime.openai_receipt(response(text='{"url":"https://fake.example.com/"}'))
    assert receipt["evidence_sources"] == []
    result = runtime.receipt_result(receipt, "news_impact", payload())
    assert result["insufficient_context"] is True
    assert result["affected_stocks"] == []
    assert result["overall_sentiment"] == 0
    assert result["content_hash"] == "hash-1"
    assert runtime.openai_receipt(response(calls=[search(status="failed")]))["evidence_sources"] == []


def test_paid_receipt_publication_and_local_recovery(tmp_path, monkeypatch):
    repo, job = started(tmp_path)
    receipt = runtime.openai_receipt(response(calls=[search()]))
    repo.record_openai_result(job["job_id"], "owner", receipt)
    assert repo.get_provider_result(job["job_id"]) == receipt
    repo.fail(job["job_id"], "owner", "schema_validation_failed", usage=receipt["usage"])
    charge = repo.get_job(job["job_id"])["budget_charge_microusd"]
    monkeypatch.setattr(recovery, "get_settings", lambda: settings(repo.path))
    async def forbidden(*args, **kwargs):
        raise AssertionError("Paid response must be reused locally")
    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(runtime, "submit_background", forbidden)
    assert asyncio.run(recovery.recover([job["job_id"]], apply=True))[0]["status"] == "recovered"
    row = repo.get_job(job["job_id"])
    public = repo.public(row)
    assert row["budget_charge_microusd"] == charge == 10081
    assert row["openai_response_id"] == "resp_news_web"
    assert row["anthropic_message_id"] is None
    assert public["evidence_sources"] == receipt["evidence_sources"]
    assert public["usage"]["web_search_requests"] == 1
    assert "另行联网检索" in public["result"]["uncertainty_notes"][-1]


def test_worker_resumes_persisted_receipt_without_provider(tmp_path, monkeypatch):
    repo, job = started(tmp_path)
    receipt = runtime.openai_receipt(response(calls=[search()]))
    repo.record_openai_result(job["job_id"], "owner", receipt)
    async def forbidden(*args, **kwargs):
        raise AssertionError("Should finish saved receipt")
    monkeypatch.setattr(runtime, "retrieve", forbidden)
    asyncio.run(worker.process_job(repo, settings(repo.path), repo.get_job(job["job_id"]), "owner"))
    assert repo.get_job(job["job_id"])["status"] == "completed"


def test_legacy_luna_identity_remains_readable():
    version, digest = runtime.LEGACY_LUNA_NEWS_IDENTITY
    assert runtime.schema_identity_current("news_impact", runtime.PROMPT_VERSIONS["news_impact"], version, digest, model=runtime.LUNA_MODEL)
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL) != (version, digest)


@pytest.mark.parametrize("with_sources", [True, False])
def test_terminal_worker_saves_evidence_before_result(tmp_path, monkeypatch, with_sources):
    repo, job = started(tmp_path)
    original_complete = repo.complete
    def checked_complete(job_id, owner, result, usage):
        assert repo.get_provider_result(job_id) is not None
        assert bool(result["insufficient_context"]) is not with_sources
        return original_complete(job_id, owner, result, usage)
    monkeypatch.setattr(repo, "complete", checked_complete)
    asyncio.run(worker._finish_response(repo, settings(repo.path), job, "owner", response(calls=[search()] if with_sources else [])))
    row = repo.get_job(job["job_id"])
    assert row["status"] == "completed"
    assert repo.public(row)["usage"]["web_search_requests"] == int(with_sources)


def test_legacy_terminal_plain_response_is_not_rewritten(tmp_path):
    repo, job = started(tmp_path, legacy=True)
    asyncio.run(worker._finish_response(repo, settings(repo.path), job, "owner", response()))
    row = repo.get_job(job["job_id"])
    assert row["status"] == "completed"
    assert row["provider_result_json"] is None
    assert not json.loads(row["result_json"])["insufficient_context"]


def test_pending_legacy_request_with_new_web_tools_saves_evidence(tmp_path):
    repo, job = started(tmp_path, legacy=True)
    current_response = response(calls=[search()])
    current_response.tools = [NS(type="web_search")]
    asyncio.run(worker._finish_response(repo, settings(repo.path), job, "owner", current_response))
    assert repo.get_provider_result(job["job_id"])["usage"]["web_search_requests"] == 1


def test_historical_luna_projection_does_not_create_new_paid_job(tmp_path):
    import sqlite3
    from datetime import datetime, timedelta, timezone
    from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
    from test_catalyst_local_intelligence import _stack, _apply_news, _news_change, _finish_job
    etl, repo, initial = _stack(tmp_path)
    service = LocalCatalystIntelligence(initial.db_path, repo, mode="manual", canonical_tickers=("NVDA",), model=runtime.LUNA_MODEL, reasoning="max")
    service.initialize()
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 900, available_at=now - timedelta(minutes=2))], as_of=now)
    service.reconcile()
    job = service.request_analysis(900, force=False)
    with sqlite3.connect(repo.path) as db:
        db.execute("UPDATE ai_jobs SET schema_version=?,schema_sha256=? WHERE job_id=?", (*runtime.LEGACY_LUNA_NEWS_IDENTITY, job["job_id"]))
    _finish_job(repo, job["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    service.reconcile()
    assert service.feed(as_of=datetime.now(timezone.utc), limit=10)["items"][0]["analysis"] is not None
    assert service.request_analysis(900, force=False)["job_id"] == job["job_id"]
    with sqlite3.connect(repo.path) as db:
        assert db.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 1


def test_legacy_allowlist_does_not_bypass_future_policy(monkeypatch):
    monkeypatch.setitem(runtime.AI_TASK_MAX_OUTPUT_TOKENS, "news_impact", 16384)
    version, digest = runtime.LEGACY_LUNA_NEWS_IDENTITY
    assert not runtime.schema_identity_current("news_impact", runtime.PROMPT_VERSIONS["news_impact"], version, digest, model=runtime.LUNA_MODEL)


def test_citations_require_matching_successful_tool_sources():
    result = response(calls=[search(), {"type": "message", "content": [{"annotations": [
        {"type": "url_citation", "url": "https://invented.source.com/"},
    ]}]}])
    receipt = runtime.openai_receipt(result)
    assert all(source["url"] != "https://invented.source.com/" for source in receipt["evidence_sources"])
    receipt["evidence_sources"].append({"title": "伪造来源", "url": "https://invented.source.com/", "type": "web_search"})
    with pytest.raises(ValueError, match="provider_sources_invalid"):
        AIJobRepository._provider_receipt_json(receipt)


def test_available_body_never_searches(tmp_path):
    data = payload()
    data.update(article_status="available", article={"status": "available", "text": "英伟达公布新芯片。", "source_url": data["source_url"],
                "fetched_at": "2026-10-09T00:00:00Z", "truncated": False})
    params = runtime._create_params(settings(tmp_path / "x"), "news_impact", data)
    for key in ("tools", "tool_choice", "max_tool_calls", "include"):
        assert key not in params
    assert "不浏览网页" in params["instructions"] and "联网搜索" not in params["instructions"]
    assert runtime.max_tool_calls_for("news_impact", model=runtime.LUNA_MODEL, payload=data) == 0
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL, payload=data) == runtime.LUNA_ARTICLE_NEWS_IDENTITY
