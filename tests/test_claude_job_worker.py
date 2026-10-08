from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from anthropic import RateLimitError
from anthropic.types import Message, Usage
import httpx2

from app.config import Settings
from app.services.ai_jobs import claude_provider, repository as storage, runtime, worker
from app.services.ai_jobs.repository import AIJobRepository


def settings(path):
    return Settings(
        openai_job_db_path=path,
        anthropic_api_key="test-claude-key",
        openai_api_key="test-legacy-key",
        openai_model="claude-haiku-5-5",
        openai_reasoning="xhigh",
        openai_manual_cooldown_seconds=0,
    )


_V2_CLAUDE_EARNINGS_DIGEST = "90a0d7b406e0e84af3d618e715fe071404f26fe8a004cfcf5b6450ccd8407e8b"


def create_job(repo, *, schema_digest=None, payload=None):
    version, digest = runtime.schema_identity("earnings_impact")
    created, _ = repo.create_job(
        job_type="earnings_impact", payload=payload or {"ticker": "AAPL", "name": "Apple"},
        model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version, schema_sha256=schema_digest or digest, max_queued=10,
    )
    return created["job_id"]


def message(*, text=None, stop_reason="end_turn"):
    result = {
        "output_language": "zh-CN", "ticker": "AAPL",
        "summary": "供应链可能出现联动。", "expectation": "关注营收与指引。",
        "impacted": [{
            "ticker": "QCOM", "name": "高通", "relation": "supplier",
            "direction": "mixed", "reason": "公开业务关系可能形成传导。",
        }],
    }
    return Message(
        id="msg_claude_test", type="message", role="assistant",
        model="claude-haiku-5-5", stop_reason=stop_reason, stop_sequence=None,
        content=[{"type": "text", "text": json.dumps(result, ensure_ascii=False) if text is None else text}],
        usage=Usage(
            input_tokens=100, cache_creation_input_tokens=200,
            cache_read_input_tokens=300, output_tokens=100,
            cache_creation={"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 0},
            output_tokens_details={"thinking_tokens": 50},
        ),
    )


def install_stream(monkeypatch, response=None):
    calls = []

    async def stream(prepared, *, on_message_start=None):
        calls.append(prepared)
        if on_message_start:
            await on_message_start("msg_claude_test")
        return response if response is not None else message()

    monkeypatch.setattr(claude_provider, "stream_message", stream)
    return calls


def test_completed_claude_receipt_precedes_publication(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    calls = install_stream(monkeypatch)
    complete = repo.complete

    def checked_complete(job_id, owner, result, usage):
        assert repo.get_provider_result(job_id)["usage"]["total_tokens"] == 700
        assert repo.get_job(job_id)["usage_total_tokens"] == 700
        return complete(job_id, owner, result, usage)

    monkeypatch.setattr(repo, "complete", checked_complete)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "owner")) == 1
    row = repo.get_job(ident)
    assert row["status"] == "completed"
    assert row["openai_response_id"] is None
    assert row["anthropic_message_id"] == "msg_claude_test"
    assert row["budget_charge_microusd"] == 88
    assert len(calls) == 1


@pytest.mark.parametrize("valid_chinese", [True, False])
def test_native_tool_json_goes_through_original_business_validation(tmp_path, monkeypatch, valid_chinese):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    original = message()
    result = json.loads(original.content[0].text)
    if not valid_chinese:
        result["summary"] = "Only English analysis."
    payload = original.model_dump()
    payload["content"] = [
        {"type": "text", "text": "Checking public facts before analysis."},
        {"type": "server_tool_use", "id": "srv_search", "name": "web_search", "input": {"query": "Apple"}},
        {"type": "web_search_tool_result", "tool_use_id": "srv_search", "content": [{
            "type": "web_search_result", "title": "Apple newsroom",
            "url": "https://www.apple.com/newsroom/", "encrypted_content": "opaque",
        }]},
        {"type": "text", "text": json.dumps(result, ensure_ascii=False)},
    ]
    payload["usage"]["server_tool_use"] = {"web_search_requests": 1, "web_fetch_requests": 0}
    calls = install_stream(monkeypatch, Message.model_validate(payload))
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    row = repo.get_job(ident)
    assert len(calls) == 1
    assert calls[0].params["output_config"] == {"effort": "xhigh"}
    assert "最终回答只输出一个完整JSON对象" in calls[0].params["system"][0]["text"]
    receipt = repo.get_provider_result(ident)
    assert json.loads(receipt["output_text"]) == result
    assert receipt["stop_reason"] == "end_turn"
    assert receipt["evidence_sources"] == [{
        "type": "web_search", "title": "Apple newsroom", "url": "https://www.apple.com/newsroom/",
    }]
    if valid_chinese:
        assert row["status"] == "completed"
        assert json.loads(row["result_json"])["summary"] == result["summary"]
    else:
        assert row["error_code"] == "schema_validation_failed"
        assert row["result_json"] is None


@pytest.mark.parametrize("stage", ["before_message", "after_message"])
def test_ambiguous_stream_failure_is_never_automatically_retried(tmp_path, monkeypatch, stage):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    calls = []

    async def stream(prepared, *, on_message_start=None):
        calls.append(1)
        if stage == "after_message":
            await on_message_start("msg_claude_test")
        raise ConnectionError("stream disconnected")

    monkeypatch.setattr(claude_provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    row = repo.get_job(ident)
    assert row["error_code"] == "submission_outcome_unknown"
    assert row["usage_total_tokens"] is None
    assert row["budget_charge_microusd"] > 0
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "owner2")) == 0
    assert len(calls) == 1


def test_definite_http_rejection_records_zero_usage(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)

    async def rejected(*args, **kwargs):
        response = httpx2.Response(429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
        raise RateLimitError("limited", response=response, body={"error": {"type": "rate_limit_error"}})

    monkeypatch.setattr(claude_provider, "stream_message", rejected)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    row = repo.get_job(ident)
    assert row["error_code"] == "provider_rate_limited"
    assert row["usage_total_tokens"] == 0
    assert row["budget_charge_microusd"] == 0


@pytest.mark.parametrize("action", ["cancel", "timeout"])
def test_active_stream_cancellation_and_timeout_keep_unknown_charge(tmp_path, monkeypatch, action):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    closed = []
    config = settings(repo.path)
    monkeypatch.setattr(worker, "_CLAUDE_CANCEL_CHECK_SECONDS", 0.001)
    if action == "timeout":
        config = config.model_copy(update={"openai_background_poll_timeout_seconds": 0.01})

    async def stream(prepared, *, on_message_start=None):
        await on_message_start("msg_claude_test")
        if action == "cancel":
            repo.request_cancel(ident)
        try:
            await asyncio.Future()
        finally:
            closed.append(True)

    monkeypatch.setattr(claude_provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, config, "owner"))
    row = repo.get_job(ident)
    assert row["error_code"] == "submission_outcome_unknown"
    assert row["usage_total_tokens"] is None
    assert row["budget_charge_microusd"] > 0
    assert closed == [True]


@pytest.mark.parametrize("saved_stop_reason", ["end_turn", "tool_use"])
def test_restart_recovers_paid_receipt_without_network(tmp_path, monkeypatch, saved_stop_reason):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo, schema_digest=_V2_CLAUDE_EARNINGS_DIGEST)
    repo.claim_due("old-owner", 60)
    repo.mark_submission_started(ident, "old-owner", daily_limit=0)
    saved = runtime.claude_receipt(message())
    saved["stop_reason"] = saved_stop_reason
    repo.record_provider_result(ident, "old-owner", saved)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE ai_jobs SET lease_expires_at=? WHERE job_id=?",
            (storage._iso(storage._utcnow() - timedelta(seconds=1)), ident),
        )

    async def forbidden(*args, **kwargs):
        raise AssertionError("paid receipt must not trigger another request")

    monkeypatch.setattr(claude_provider, "stream_message", forbidden)
    asyncio.run(worker.run_once(AIJobRepository(repo.path), settings(repo.path), "new-owner"))
    assert repo.get_job(ident)["status"] == "completed"
    assert repo.get_job(ident)["usage_total_tokens"] == 700
    assert repo.get_job(ident)["schema_sha256"] == _V2_CLAUDE_EARNINGS_DIGEST


def test_publish_storage_failure_keeps_receipt_for_local_retry(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    calls = install_stream(monkeypatch)
    complete = repo.complete
    monkeypatch.setattr(worker, "_STORAGE_WRITE_RETRY_DELAY_SECONDS", 0)

    def busy(*args, **kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(repo, "complete", busy)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    assert repo.get_provider_result(ident) is not None
    assert repo.get_job(ident)["error_code"] == "local_storage_error"
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET next_attempt_at=NULL WHERE job_id=?", (ident,))
    monkeypatch.setattr(repo, "complete", complete)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    assert repo.get_job(ident)["status"] == "completed"
    assert len(calls) == 1


@pytest.mark.parametrize("response,code", [
    (message(text=""), "provider_empty_response"),
    (message(text="not-json"), "schema_validation_failed"),
    (message(stop_reason="max_tokens"), "provider_incomplete_max_output_tokens"),
])
def test_unusable_terminal_answer_is_saved_and_charged(tmp_path, monkeypatch, response, code):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    install_stream(monkeypatch, response)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    row = repo.get_job(ident)
    assert row["error_code"] == code
    assert row["usage_total_tokens"] == 700
    assert repo.get_provider_result(ident) is not None


def test_legacy_retrieve_uses_legacy_key_under_claude_settings(tmp_path, monkeypatch):
    calls = []

    class LegacyClient:
        def __init__(self, **kwargs):
            calls.append(kwargs)

    import openai

    monkeypatch.setattr(openai, "AsyncOpenAI", LegacyClient)
    monkeypatch.setattr(runtime, "_CLIENT", None)
    runtime._client(settings(tmp_path / "jobs.db"))
    assert calls[0]["api_key"] == "test-legacy-key"
    assert calls[0]["base_url"] == "https://api.openai.com/v1"
    assert calls[0]["max_retries"] == 0


def test_claude_billing_preserves_actual_usage_above_reservation():
    usage = {
        "input_tokens": 1000, "cached_input_tokens": 0,
        "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
        "cache_creation_1h_input_tokens": 0, "output_tokens": 1000,
        "total_tokens": 2000, "reasoning_tokens": None,
    }
    assert runtime.settled_usage_cost_microusd(
        "earnings_impact", usage, fallback_microusd=1,
        model="claude-haiku-5-5",
    ) == 600


@pytest.mark.parametrize("saved_stop_reason", ["end_turn", "tool_use"])
def test_paid_claude_schema_recovery_is_local_and_preserves_accounting(tmp_path, monkeypatch, saved_stop_reason):
    from app.tools import recover_ai_schema_results

    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo, schema_digest=_V2_CLAUDE_EARNINGS_DIGEST)
    repo.claim_due("owner", 60)
    repo.mark_submission_started(ident, "owner", daily_limit=0)
    saved = runtime.claude_receipt(message())
    saved["stop_reason"] = saved_stop_reason
    repo.record_provider_result(ident, "owner", saved)
    repo.fail(ident, "owner", "schema_validation_failed")
    charge = repo.get_job(ident)["budget_charge_microusd"]
    monkeypatch.setattr(recover_ai_schema_results, "get_settings", lambda: settings(repo.path))

    async def forbidden(*args, **kwargs):
        raise AssertionError("local receipt recovery must not contact a provider")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(claude_provider, "stream_message", forbidden)
    output = asyncio.run(recover_ai_schema_results.recover([ident], apply=True))
    assert output[0]["status"] == "recovered"
    row = repo.get_job(ident)
    assert row["status"] == "completed"
    assert row["budget_charge_microusd"] == charge
    assert row["usage_total_tokens"] == 700


@pytest.mark.parametrize("entrypoint", ["manual", "scheduled"])
def test_rollback_model_jobs_reach_the_legacy_provider(tmp_path, monkeypatch, entrypoint):
    from app.api import ai
    from app.worker.tasks import EarningsAnalysisTask

    repo = AIJobRepository(tmp_path / "jobs.db")
    config = settings(repo.path).model_copy(update={
        "openai_model": "gpt-5.6-terra", "openai_reasoning": "max",
        "openai_max_concurrency": 1,
    })
    monkeypatch.setattr(ai, "get_settings", lambda: config)
    monkeypatch.setattr(ai, "_job_repository", lambda: repo)
    monkeypatch.setattr(ai, "_require_earnings_manual_analysis_enabled", lambda: None)
    if entrypoint == "manual":
        row, _ = ai._create_job("earnings_impact", {"ticker": "AAPL", "name": "Apple"})
    else:
        effective = SimpleNamespace(
            ai=SimpleNamespace(manual_analysis_enabled=True),
            earnings=SimpleNamespace(scheduled_analysis_enabled=True, lookahead_days=5),
        )
        task = EarningsAnalysisTask(
            "rollback", settings=config, repository=repo,
            runtime_settings_reader=lambda: effective,
            today=lambda: date(2026, 10, 8),
            builder=lambda _: {
                "data_limited": False, "source_status": "active",
                "earnings": [{
                    "ticker": "AAPL", "name": "Apple", "days_until": 0,
                    "earnings_date": "2026-10-08",
                }],
            },
        )
        result = asyncio.run(task())
        assert result.details["queued"] == 1
        row = repo.latest_for_ticker("earnings_impact", "AAPL")

    sent = []
    monkeypatch.setattr(runtime, "prepare_background", lambda *args: object())

    async def submit(*args, **kwargs):
        sent.append(1)
        return SimpleNamespace(id="resp_rollback", status="in_progress")

    monkeypatch.setattr(runtime, "submit_background", submit)
    assert asyncio.run(worker.run_once(repo, config, "rollback-worker")) == 1
    saved = repo.get_job(row["job_id"])
    assert sent == [1]
    assert saved["openai_response_id"] == "resp_rollback"
    assert saved["status"] == "in_progress"
    assert saved["error_code"] is None


@pytest.mark.parametrize("concurrency", [3, 4])
@pytest.mark.parametrize("cancel_group", [False, True])
def test_configured_claude_slots_stream_together_and_drain_on_cancel(
    tmp_path, monkeypatch, concurrency, cancel_group,
):
    repo = AIJobRepository(tmp_path / "jobs.db")
    config = settings(repo.path).model_copy(update={"openai_max_concurrency": concurrency})
    version, digest = runtime.schema_identity("earnings_impact")
    ids = []
    for ticker in ["AAPL", "MSFT", "NVDA", "AMD", "TSM"]:
        row, _ = repo.create_job(
            job_type="earnings_impact", payload={"ticker": ticker, "name": ticker},
            model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
            prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
            schema_version=version, schema_sha256=digest, max_queued=10,
            submission_source="scheduled",
        )
        ids.append(row["job_id"])
    effective = SimpleNamespace(
        ai=SimpleNamespace(
            daily_max_jobs=0, daily_budget_usd=0, daily_token_limit=100_000_000,
            manual_analysis_cooldown_seconds=0, manual_analysis_enabled=True,
        ),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=True),
        earnings=SimpleNamespace(scheduled_analysis_enabled=True),
    )
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: effective)
    monkeypatch.setattr(runtime, "prepare_claude", lambda _settings, _kind, payload: payload)

    async def scenario():
        started = asyncio.Event()
        release = asyncio.Event()
        calls = []
        active = 0
        peak = 0

        async def stream(payload, *, on_message_start=None):
            nonlocal active, peak
            ticker = payload["ticker"]
            calls.append(ticker)
            active += 1
            peak = max(peak, active)
            try:
                await on_message_start(f"msg_{ticker}")
                if len(calls) == concurrency:
                    started.set()
                await release.wait()
                result = json.loads(message().content[0].text)
                result["ticker"] = ticker
                return message(text=json.dumps(result, ensure_ascii=False)).model_copy(
                    update={"id": f"msg_{ticker}"},
                )
            finally:
                active -= 1

        monkeypatch.setattr(claude_provider, "stream_message", stream)
        task = asyncio.create_task(worker.run_configured_once(
            repo, config, "pool", personal_config=SimpleNamespace(
                features=SimpleNamespace(catalyst_mode="scheduled"),
            ),
        ))
        try:
            await asyncio.wait_for(started.wait(), timeout=5)
            assert peak == concurrency
            if cancel_group:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                release.set()
                assert await asyncio.wait_for(task, timeout=5) == (concurrency, "enabled")
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        assert active == 0
        assert len(calls) == concurrency

    asyncio.run(scenario())
    rows = [repo.get_job(ident) for ident in ids]
    if cancel_group:
        assert sum(row["error_code"] == "submission_outcome_unknown" for row in rows) == concurrency
        assert repo.claim_due("later", 60, max_concurrency=concurrency) is None
    else:
        assert sum(row["status"] == "completed" for row in rows) == concurrency
        assert repo.claim_due("later", 60, max_concurrency=concurrency) is not None


def test_claude_prompt_json_changes_only_earnings_identity_and_keeps_prepare_policies(tmp_path):
    # Pinned to the production v2 contract before the provider encoding fix.
    expected = {
        ("earnings_impact", "claude-haiku-5-5"): "90a0d7b406e0e84af3d618e715fe071404f26fe8a004cfcf5b6450ccd8407e8b",
        ("news_impact", "claude-haiku-5-5"): "68b3095ba0f47e559a5a7edd3daf6b091350546961ce398368684143bbb76a4a",
        ("earnings_impact", "gpt-5.6-terra"): "efcf4a6d24e87c8bfcb8620183338d7ddd927a8df9290b1a8ee7f601a05e9265",
        ("news_impact", "gpt-5.6-terra"): "d0e6936d8749cc96ed7fa8b3bf07bc64bd4cc1f5fb70d18ec0b0fbe3c35576fe",
    }
    for (job_type, model), digest in expected.items():
        identity = runtime.schema_identity(job_type, model=model)
        if (job_type, model) == ("earnings_impact", "claude-haiku-5-5"):
            assert identity[1] != digest
        else:
            assert identity[1] == digest
        assert runtime.schema_identity_current(job_type, runtime.PROMPT_VERSIONS[job_type], *identity, model=model)
    prepared = runtime.prepare_claude(settings(tmp_path / "jobs.db"), "earnings_impact", {
        "ticker": "PENG", "name": "PENG", "analysis_stage": "post_release_final",
        "eps_actual": 1.0, "eps_estimate": 0.7844,
        "revenue_actual": 566690000.0, "revenue_estimate": 524737643.0,
    })
    assert prepared.params["output_config"] == {"effort": "xhigh"}
    schema = json.loads(prepared.params["system"][0]["text"].split(claude_provider._PROMPT_JSON_INSTRUCTIONS)[1])
    assert "pattern" not in schema["properties"]["summary"]
    assert schema["properties"]["output_language"]["enum"] == ["zh-CN"]
    assert prepared.params["model"] == "claude-haiku-5-5"
    assert prepared.params["output_config"]["effort"] == "xhigh"
    assert prepared.params["max_tokens"] == 65536
    assert prepared.params["thinking"] == {"type": "adaptive"}
    assert prepared.params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert prepared.params["tools"] == runtime.claude_tools_for("earnings_impact", {})


def test_completed_v2_earnings_result_remains_public_and_readable_by_report_api(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import ai

    repo = AIJobRepository(tmp_path / "jobs.db")
    payload = {
        "ticker": "AAPL", "name": "Apple", "analysis_stage": "post_release_final",
        "earnings_date": "2026-10-08", "year": 2026, "quarter": 4,
        "report_id": "earnings:AAPL:2026-10-08:2026:q4",
    }
    ident = create_job(repo, schema_digest=_V2_CLAUDE_EARNINGS_DIGEST, payload=payload)
    repo.claim_due("owner", 60)
    repo.mark_submission_started(ident, "owner", daily_limit=0)
    receipt = runtime.claude_receipt(message())
    repo.record_provider_result(ident, "owner", receipt)
    result = runtime.validate_result("earnings_impact", receipt["output_text"], payload)
    repo.complete(ident, "owner", result, receipt["usage"])
    row = repo.get_job(ident)
    assert row["schema_sha256"] == _V2_CLAUDE_EARNINGS_DIGEST
    assert repo.public(row)["result"] == result
    monkeypatch.setattr(ai, "_job_repository", lambda: repo)
    app = FastAPI()
    app.include_router(ai.router)
    with TestClient(app) as client:
        response = client.get("/api/ai/earnings-impact/AAPL/reports/2026-10-08", params={"year": 2026, "quarter": 4})
    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    assert response.json()["result"] == result


def test_unsubmitted_v2_earnings_is_rejected_without_paid_submission(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo, schema_digest=_V2_CLAUDE_EARNINGS_DIGEST)

    def forbidden(*args, **kwargs):
        raise AssertionError("retired request identity must not start paid work")

    monkeypatch.setattr(repo, "mark_submission_started", forbidden)
    monkeypatch.setattr(claude_provider, "stream_message", forbidden)
    asyncio.run(worker.run_once(repo, settings(repo.path), "owner"))
    row = repo.get_job(ident)
    assert row["status"] == "failed"
    assert row["error_code"] == "runtime_configuration_changed"
    assert row["submission_started_at"] is None
    assert row["anthropic_message_id"] is None
    assert (row["budget_charge_microusd"] or 0) == 0
    assert row["usage_total_tokens"] == 0
    assert repo.get_provider_result(ident) is None


@pytest.mark.parametrize("analysis_stage", ["pre_release", "post_release_final"])
def test_prompt_json_fixed_cached_prefix_fits_existing_input_and_reservation_bounds(tmp_path, analysis_stage):
    payload = {"ticker": "PENG", "name": "PENG", "analysis_stage": analysis_stage}
    prepared = runtime.prepare_claude(settings(tmp_path / "jobs.db"), "earnings_impact", payload)
    fixed = runtime.prepare_claude(settings(tmp_path / "jobs.db"), "earnings_impact", {"ticker": "NEOG", "name": "NEOG", "analysis_stage": analysis_stage})
    assert prepared.params["system"] == fixed.params["system"]
    assert prepared.params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    # UTF-8 prompt/tool bytes bound token counts. Replace the actual bounded
    # payload with the maximum accepted 60KB, leaving the fixed framing intact.
    request = runtime.build_runtime_request("earnings_impact", payload)
    payload_bytes = runtime.untrusted_json_size(payload)
    fixed_input_bytes = len(request.input_text.encode("utf-8")) - payload_bytes
    system_bytes = len(prepared.params["system"][0]["text"].encode("utf-8"))
    tool_bytes = len(json.dumps(prepared.params["tools"], ensure_ascii=False).encode("utf-8"))
    maximum_input_bytes = system_bytes + fixed_input_bytes + tool_bytes + runtime._MAX_UNTRUSTED_JSON_BYTES
    bound = runtime.max_input_tokens_for("earnings_impact", model="claude-haiku-5-5")
    assert maximum_input_bytes < bound
    assert bound + prepared.params["max_tokens"] < runtime.CLAUDE_TOOL_TOKEN_RESERVATION
    assert runtime.token_reservation("earnings_impact", model="claude-haiku-5-5") == 1_000_000
    assert "PENG" not in prepared.params["system"][0]["text"]


@pytest.mark.parametrize(("job_type", "payload", "v2_digest"), [
    ("option_alerts", {"ticker": "AAPL", "alerts": [], "underlying_price": 100},
     "2817085fe7f12590b30e2b0a5631952dda6ff31216b5a7468ec237af39efcdcd"),
    ("signal_analysis", {"ticker": "AAPL", "signals": {}, "scores": {}, "as_of": "2026-10-08T00:00:00Z"},
     "c96f6a0c9cc838df1a72a687983f31526cd0bdfb6155ff3d58e79d287387c18c"),
    ("news_impact", {"news_id": 1, "change_sequence": 1, "content_hash": "a" * 64, "allowed_tickers": []},
     "68b3095ba0f47e559a5a7edd3daf6b091350546961ce398368684143bbb76a4a"),
])
def test_other_claude_job_types_keep_native_json_and_v2_identity(tmp_path, job_type, payload, v2_digest):
    config = settings(tmp_path / "jobs.db")
    request = runtime.build_runtime_request(job_type, payload)
    expected = claude_provider.prepare_message(
        config, instructions=runtime.claude_instructions(request.instructions),
        input_text=request.input_text, schema=request.schema,
        max_tokens=runtime.max_output_tokens_for(job_type, model="claude-haiku-5-5"),
        tools=runtime.claude_tools_for(job_type, payload),
    )
    actual = runtime.prepare_claude(config, job_type, payload)
    assert actual.params == expected.params
    assert actual.params["output_config"]["format"]["type"] == "json_schema"
    assert claude_provider._PROMPT_JSON_INSTRUCTIONS not in actual.params["system"][0]["text"]
    assert runtime.schema_identity(job_type, model="claude-haiku-5-5")[1] == v2_digest


def test_claim_failure_does_not_cancel_paid_sibling_streams(tmp_path, monkeypatch):
    import threading

    repo = AIJobRepository(tmp_path / "jobs.db")
    config = settings(repo.path).model_copy(update={"openai_max_concurrency": 4})
    ids = [create_job(repo, payload={"ticker": ticker, "name": ticker}) for ticker in ("AAPL", "MSFT", "NVDA")]
    effective = SimpleNamespace(
        ai=SimpleNamespace(daily_max_jobs=0, daily_budget_usd=0, daily_token_limit=100_000_000,
                           manual_analysis_cooldown_seconds=0, manual_analysis_enabled=True),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=True),
        earnings=SimpleNamespace(scheduled_analysis_enabled=True),
    )
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: effective)
    monkeypatch.setattr(runtime, "prepare_claude", lambda _settings, _kind, payload: payload)
    streams_started = threading.Event()
    claim_failed = threading.Event()
    original_claim = repo.claim_due
    failure = RuntimeError("isolated claim failure")

    def claim(owner, *args, **kwargs):
        if owner.endswith(":slot-0"):
            if not streams_started.wait(5):
                raise AssertionError("paid sibling streams did not start")
            claim_failed.set()
            raise failure
        return original_claim(owner, *args, **kwargs)

    monkeypatch.setattr(repo, "claim_due", claim)

    async def scenario():
        release = asyncio.Event()
        started, cancelled, finished = [], [], []

        async def stream(payload, *, on_message_start=None):
            ticker = payload["ticker"]
            await on_message_start(f"msg_{ticker}")
            started.append(ticker)
            if len(started) == 3:
                streams_started.set()
            try:
                await release.wait()
                result = json.loads(message().content[0].text)
                result["ticker"] = ticker
                return message(text=json.dumps(result, ensure_ascii=False)).model_copy(update={"id": f"msg_{ticker}"})
            except asyncio.CancelledError:
                cancelled.append(ticker)
                raise
            finally:
                finished.append(ticker)

        monkeypatch.setattr(claude_provider, "stream_message", stream)
        task = asyncio.create_task(worker.run_configured_once(
            repo, config, "isolation", personal_config=SimpleNamespace(features=SimpleNamespace(catalyst_mode="scheduled")),
        ))
        try:
            assert await asyncio.to_thread(claim_failed.wait, 5)
            # Give the failed claim's future time to reach the pool coordinator.
            await asyncio.sleep(0.05)
            assert not task.done(), "the round must wait for independent paid siblings"
            assert not cancelled
            release.set()
            with pytest.raises(RuntimeError) as caught:
                await asyncio.wait_for(task, timeout=5)
            assert caught.value is failure
            assert sorted(finished) == sorted(started)
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    for identifier in ids:
        row = repo.get_job(identifier)
        assert row["status"] == "completed" and row["error_code"] is None
        assert row["usage_total_tokens"] == 700
        assert row["provider_result_json"] is not None


def _pool_controls(tmp_path, monkeypatch):
    config = settings(tmp_path / "pool.db").model_copy(update={"openai_max_concurrency": 4})
    effective = SimpleNamespace(
        ai=SimpleNamespace(daily_max_jobs=0, daily_budget_usd=0, daily_token_limit=100_000_000,
                           manual_analysis_cooldown_seconds=0, manual_analysis_enabled=True),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=True),
        earnings=SimpleNamespace(scheduled_analysis_enabled=True),
    )
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: effective)
    return config, SimpleNamespace(features=SimpleNamespace(catalyst_mode="scheduled"))


def test_multiple_slot_errors_are_logged_individually_after_other_slots_finish(tmp_path, monkeypatch, caplog):
    config, personal = _pool_controls(tmp_path, monkeypatch)
    errors = [RuntimeError("first slot failure"), ValueError("second slot failure")]

    async def scenario():
        started, completed = [], []
        all_started, release = asyncio.Event(), asyncio.Event()

        async def run_once(repo, settings, owner, **kwargs):
            index = int(owner.rsplit("-", 1)[1])
            started.append(index)
            if len(started) == 4:
                all_started.set()
            await all_started.wait()
            if index < 2:
                raise errors[index]
            await release.wait()
            completed.append(index)
            return 1

        monkeypatch.setattr(worker, "run_once", run_once)
        task = asyncio.create_task(worker.run_configured_once(None, config, "pool", personal_config=personal))
        try:
            await all_started.wait()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(RuntimeError) as caught:
                await task
            assert caught.value is errors[0]
            assert sorted(completed) == [2, 3]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    logged = [item.exc_info[1] for item in caplog.records if item.exc_info and "AI job slot failed" in item.message]
    assert logged == errors


@pytest.mark.parametrize("interruption", ["repeated_cancel", "deadline"])
def test_external_pool_interruption_drains_every_slot_before_returning(tmp_path, monkeypatch, interruption):
    config, personal = _pool_controls(tmp_path, monkeypatch)

    async def scenario():
        started, closing, closed = [], [], []
        all_started, cleanup_started = asyncio.Event(), asyncio.Event()

        async def run_once(repo, settings, owner, **kwargs):
            started.append(owner)
            if len(started) == 4:
                all_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                closing.append(owner)
                if len(closing) == 4:
                    cleanup_started.set()
                # Model the asynchronous stream close and durable accounting.
                await asyncio.sleep(0.04)
                closed.append(owner)

        monkeypatch.setattr(worker, "run_once", run_once)
        task = asyncio.create_task(worker.run_configured_once(None, config, "pool", personal_config=personal))
        try:
            await asyncio.wait_for(all_started.wait(), timeout=2)
            if interruption == "deadline":
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(task, timeout=0.01)
            else:
                task.cancel()
                await asyncio.wait_for(cleanup_started.wait(), timeout=2)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            assert sorted(closed) == sorted(started)
            assert len(closed) == 4
            assert not [item for item in asyncio.all_tasks() if item is not asyncio.current_task() and not item.done()]
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_slot_self_cancellation_reports_error_without_cancelling_parent_or_siblings(tmp_path, monkeypatch):
    config, personal = _pool_controls(tmp_path, monkeypatch)

    async def scenario():
        started, completed = [], []
        all_started, release = asyncio.Event(), asyncio.Event()

        async def run_once(repo, settings, owner, **kwargs):
            index = int(owner.rsplit("-", 1)[1])
            started.append(index)
            if len(started) == 4:
                all_started.set()
            await all_started.wait()
            if index == 0:
                asyncio.current_task().cancel("slot-local cancellation")
                await asyncio.sleep(0)
            await release.wait()
            completed.append(index)
            return 1

        monkeypatch.setattr(worker, "run_once", run_once)
        task = asyncio.create_task(worker.run_configured_once(None, config, "pool", personal_config=personal))
        try:
            await all_started.wait()
            await asyncio.sleep(0.01)
            assert not task.done()
            release.set()
            with pytest.raises(RuntimeError, match="ai_job_slot_cancelled") as caught:
                await task
            assert isinstance(caught.value.__cause__, asyncio.CancelledError)
            assert not task.cancelled()
            assert sorted(completed) == [1, 2, 3]
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


def test_parent_cancel_preserves_prior_slot_error_without_logging_cancelled_siblings(tmp_path, monkeypatch, caplog):
    config, personal = _pool_controls(tmp_path, monkeypatch)
    failure = RuntimeError("claim failed before parent cancellation")

    async def scenario():
        started, closed = [], []
        all_started, failed = asyncio.Event(), asyncio.Event()

        async def run_once(repo, settings, owner, **kwargs):
            index = int(owner.rsplit("-", 1)[1])
            started.append(index)
            if len(started) == 4:
                all_started.set()
            await all_started.wait()
            if index == 0:
                failed.set()
                raise failure
            try:
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.append(index)

        monkeypatch.setattr(worker, "run_once", run_once)
        task = asyncio.create_task(worker.run_configured_once(None, config, "pool", personal_config=personal))
        await asyncio.wait_for(failed.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert sorted(closed) == [1, 2, 3]

    asyncio.run(scenario())
    logged = [item.exc_info[1] for item in caplog.records if item.exc_info and "AI job slot failed" in item.message]
    assert logged == [failure]


@pytest.mark.parametrize("model,reasoning", [("claude-haiku-5-5", "xhigh"), ("gpt-5.6-terra", "max")])
@pytest.mark.parametrize("outcome", ["normal", "self_cancel", "parent_cancel"])
def test_single_slot_preserves_owner_and_distinguishes_cancel_sources(tmp_path, monkeypatch, model, reasoning, outcome):
    config, personal = _pool_controls(tmp_path, monkeypatch)
    config = config.model_copy(update={"openai_max_concurrency": 1, "openai_model": model, "openai_reasoning": reasoning})

    async def scenario():
        started = asyncio.Event()
        owners, closed = [], []

        async def run_once(repo, settings, owner, **kwargs):
            owners.append(owner)
            started.set()
            try:
                if outcome == "normal":
                    return 1
                if outcome == "self_cancel":
                    asyncio.current_task().cancel("single slot cancellation")
                    await asyncio.sleep(0)
                await asyncio.Event().wait()
            finally:
                await asyncio.sleep(0.01)
                closed.append(owner)

        monkeypatch.setattr(worker, "run_once", run_once)
        task = asyncio.create_task(worker.run_configured_once(None, config, "unchanged-owner", personal_config=personal))
        await asyncio.wait_for(started.wait(), timeout=2)
        if outcome == "parent_cancel":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        elif outcome == "self_cancel":
            with pytest.raises(RuntimeError, match="ai_job_slot_cancelled") as caught:
                await task
            assert isinstance(caught.value.__cause__, asyncio.CancelledError)
            assert not task.cancelled()
        else:
            assert await task == (1, "enabled")
        assert owners == closed == ["unchanged-owner"]

    asyncio.run(scenario())
