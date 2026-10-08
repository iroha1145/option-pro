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


def create_job(repo):
    version, digest = runtime.schema_identity("earnings_impact")
    created, _ = repo.create_job(
        job_type="earnings_impact", payload={"ticker": "AAPL", "name": "Apple"},
        model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version, schema_sha256=digest, max_queued=10,
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


def test_restart_recovers_paid_receipt_without_network(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    repo.claim_due("old-owner", 60)
    repo.mark_submission_started(ident, "old-owner", daily_limit=0)
    repo.record_provider_result(ident, "old-owner", runtime.claude_receipt(message()))
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


def test_paid_claude_schema_recovery_is_local_and_preserves_accounting(tmp_path, monkeypatch):
    from app.tools import recover_ai_schema_results

    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = create_job(repo)
    repo.claim_due("owner", 60)
    repo.mark_submission_started(ident, "owner", daily_limit=0)
    repo.record_provider_result(ident, "owner", runtime.claude_receipt(message()))
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
