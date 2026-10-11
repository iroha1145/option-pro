"""Offline stock report contract, provenance, transport and recovery checks."""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from datetime import timedelta
from types import SimpleNamespace

from anthropic.types import Message
import pytest
from pydantic import ValidationError

from app.config import Settings
from app.personal_config import AIConfig, PersonalConfig
from app.services.ai_jobs import claude_provider as provider, repository as storage, runtime, stock_report, worker
from app.services.ai_jobs.models import StockReportAnalysisResult, validate_result, validate_result_cached
from app.services.ai_jobs.repository import AIJobRepository
from test_signal_context import _signal_result


@pytest.fixture(autouse=True)
def official_local_environment(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def settings(path):
    return Settings(
        _env_file=None, openai_job_db_path=path,
        anthropic_api_key="offline-stock-test-key", openai_api_key="",
        openai_model="claude-haiku-5-5", openai_reasoning="xhigh",
        openai_manual_cooldown_seconds=0,
    )


def payload():
    return {"ticker": "AMD", "signals": {}, "scores": {},
            "as_of": "2026-10-11T00:00:00Z", "evidence_as_of": "2026-10-11T00:00:00Z",
            "company_profile": {"profile": {"name": "Advanced Micro Devices"}, "evidence_id": "company:AMD"},
            "context_status": {"company_profile": "available"}}


def result():
    value = _signal_result()
    value["report"] = {
        "version": "stock-report-v1", "headline": "经营资料有限，价格仍在区间内波动",
        "sections": [{"id": key, "title": "资料与观察", "summary": "现有资料不足，仍需核对最新公布内容。",
                      "points": ["现有资料仅支持有限判断。"], "evidence_ids": ["signals"], "source_urls": []}
                     for key in stock_report.REPORT_SECTION_IDS],
        "watch_items": [{"what": "下一期经营现金流", "why": "核对利润与现金的变化是否一致。",
                         "revise_if": "若现金流与利润持续背离，需要重审盈利质量。"}],
        "data_gaps": ["未取得完整财务数据。"],
    }
    return value


def message(value=None, *, message_id="msg_stock", stop="end_turn", sources=False, source_title="AMD company announcement"):
    content = []
    if sources:
        content.extend([
            {"type": "server_tool_use", "id": "srv_stock", "name": "web_search", "input": {"query": "AMD earnings"}},
            {"type": "web_search_tool_result", "tool_use_id": "srv_stock", "content": [
                {"type": "web_search_result", "title": source_title, "url": "https://www.amd.com/en/newsroom/",
                 "encrypted_content": "offline-evidence"}]},
        ])
    content.append({"type": "text", "text": json.dumps(value or result(), ensure_ascii=False)})
    return Message(
        id=message_id, type="message", role="assistant", model=runtime.SONNET_MODEL,
        stop_reason=stop, content=content,
        usage={"input_tokens": 100, "cache_creation_input_tokens": 200,
               "cache_read_input_tokens": 300, "output_tokens": 100,
               "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 200},
               "server_tool_use": {"web_search_requests": int(sources), "web_fetch_requests": 0}},
    )


def create(repo, data=None):
    version, digest = runtime.schema_identity("signal_analysis", model=runtime.SONNET_MODEL)
    row, _ = repo.create_job(
        job_type="signal_analysis", payload=data or payload(), model=runtime.SONNET_MODEL,
        reasoning="xhigh", execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["signal_analysis"],
        schema_version=version, schema_sha256=digest, max_queued=10,
    )
    return row["job_id"]


def test_stock_model_is_task_specific_and_configured_exactly(tmp_path):
    config = settings(tmp_path / "jobs.db")
    assert AIConfig().signal_analysis_model == runtime.SONNET_MODEL
    assert runtime.model_identity_for_job(config, "signal_analysis") == (runtime.SONNET_MODEL, "xhigh")
    assert runtime.model_identity_for_job(config, "earnings_impact") == ("claude-haiku-5-5", "xhigh")
    with pytest.raises(ValidationError):
        AIConfig(signal_analysis_model="claude-haiku-5-5")
    with pytest.raises(ValidationError):
        Settings(_env_file=None, openai_signal_analysis_reasoning="max")
    assert runtime.capability_status(config, job_type="signal_analysis")["provider"] == "anthropic"


def test_stock_system_schema_prefix_is_shared_and_one_hour_cached(tmp_path):
    config = runtime.settings_for_job(settings(tmp_path / "jobs.db"), "signal_analysis")
    first = runtime.prepare_claude(config, "signal_analysis", payload()).params
    second = runtime.prepare_claude(config, "signal_analysis", {**payload(), "ticker": "AAPL"}).params
    assert first["model"] == runtime.SONNET_MODEL
    assert first["output_config"] == {"effort": "xhigh"}
    assert first["thinking"] == {"type": "adaptive"}
    assert first["system"] == second["system"]
    assert first["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "2026-10-11" not in first["system"][0]["text"]
    assert first["messages"] != second["messages"]
    assert first["tool_choice"]["type"] == "auto"
    tools = {tool["name"]: tool for tool in first["tools"]}
    assert tools["web_search"]["max_uses"] == stock_report.TOOL_MAX_USES["web_search"]
    assert tools["web_fetch"]["max_uses"] == stock_report.TOOL_MAX_USES["web_fetch"]
    assert "代码执行最多2次" in first["system"][0]["text"]
    earnings = runtime.prepare_claude(settings(tmp_path / "jobs.db"), "earnings_impact", {"ticker": "AMD"}).params
    assert earnings["system"][0]["cache_control"]["ttl"] == "5m"


def test_large_stock_packet_is_bounded_without_changing_other_jobs():
    data = {**payload(), "company_profile": {"profile": {"business_description": "资" * 25_000}}}
    request = runtime.build_runtime_request("signal_analysis", data, model=runtime.SONNET_MODEL)
    assert len(request.input_text.encode()) > 60_000
    with pytest.raises(ValueError, match="ai_input_too_large"):
        runtime._bounded_untrusted_json(data)
    with pytest.raises(ValueError, match="ai_input_too_large"):
        runtime.build_runtime_request("signal_analysis", {**data, "oversize": "x" * 240_000}, model=runtime.SONNET_MODEL)
    assert runtime.max_payload_bytes_for("news_impact") == 60_000
    assert runtime.max_input_tokens_for("signal_analysis", model=runtime.SONNET_MODEL) >= 240_000


def test_new_reports_are_required_but_old_completed_results_are_readable():
    old = _signal_result()
    assert "report" not in validate_result("signal_analysis", json.dumps(old), payload())
    with pytest.raises(ValidationError):
        StockReportAnalysisResult.model_validate(old)
    with pytest.raises(ValueError, match="stock_report_required"):
        runtime.enforce_new_output_limits({"job_type": "signal_analysis", "schema_version": stock_report.REPORT_SCHEMA_NAME}, json.dumps(old), payload())
    identity = runtime.schema_identity("signal_analysis", model=runtime.SONNET_MODEL)
    assert identity[0] == stock_report.REPORT_SCHEMA_NAME
    assert identity != runtime.schema_identity("signal_analysis", model="claude-haiku-5-5")
    assert not runtime.schema_identity_current("signal_analysis", "signal-analysis-zh-cn-v6", "signal_analysis_zh_cn_v5", "c96f6a0c9cc838df1a72a687983f31526cd0bdfb6155ff3d58e79d287387c18c", current_identity=identity)


@pytest.mark.parametrize("mutation,error", [
    (lambda data: data["report"]["sections"].reverse(), "stock_report_sections_mismatch"),
    (lambda data: data["report"]["sections"][0]["evidence_ids"].append("invented:block"), "stock_report_local_evidence_mismatch"),
    (lambda data: data["report"]["sections"][0]["source_urls"].append("https://127.0.0.1/secret"), "stock_report_source_url_invalid"),
    (lambda data: data["report"]["sections"][0].update(summary="This is entirely English."), "simplified_chinese_text_required"),
])
def test_fixed_report_rejects_invalid_output(mutation, error):
    data = result()
    mutation(data)
    with pytest.raises(ValueError, match=error):
        validate_result("signal_analysis", json.dumps(data, ensure_ascii=False), payload())


def test_report_source_urls_must_bind_actual_successful_tool_receipts():
    data = result()
    data["report"]["sections"][0]["source_urls"] = ["https://www.amd.com/en/newsroom/"]
    receipt = runtime.claude_receipt(message(data, sources=True))
    accepted = runtime.receipt_result(receipt, "signal_analysis", payload())
    assert accepted["report"]["sections"][0]["source_urls"]
    altered = copy.deepcopy(receipt)
    altered["tool_evidence"][0]["status"] = "error"
    with pytest.raises(ValueError, match="stock_report_web_source_mismatch"):
        runtime.receipt_result(altered, "signal_analysis", payload())
    altered = copy.deepcopy(receipt)
    altered["tool_evidence"][0]["content_sha256"] = "invented"
    with pytest.raises(ValueError, match="stock_report_web_source_mismatch"):
        runtime.receipt_result(altered, "signal_analysis", payload())


def test_unavailable_local_block_cannot_be_referenced():
    data = result()
    data["report"]["sections"][0]["evidence_ids"] = ["company:AMD"]
    assert validate_result("signal_analysis", json.dumps(data, ensure_ascii=False), payload())
    with pytest.raises(ValueError, match="stock_report_local_evidence_mismatch"):
        validate_result("signal_analysis", json.dumps(data, ensure_ascii=False), {**payload(), "context_status": {"company_profile": "omitted_size"}})


@pytest.mark.parametrize("state", ["timeout", "not_applicable", "missing", "error", "empty"])
@pytest.mark.parametrize("where", ["context_status", "block"])
def test_placeholder_block_is_not_a_factual_report_source(state, where):
    data = result()
    data["report"]["sections"][1]["evidence_ids"] = ["financials"]
    inputs = payload()
    inputs["financials"] = {"source": "Yahoo/yfinance", "as_of": None, "data_through": None}
    if where == "context_status":
        inputs["context_status"]["financials"] = state
    else:
        inputs["financials"]["status"] = state
    with pytest.raises(ValueError, match="stock_report_local_evidence_mismatch"):
        validate_result("signal_analysis", json.dumps(data, ensure_ascii=False), inputs)


def test_validation_cache_keys_include_actual_external_source_titles():
    calls = []
    def validator(kind, text, data, *, external_source_texts=()):
        calls.append(external_source_texts)
        return {"sources": list(external_source_texts)}
    first = validate_result_cached("signal_analysis", "{}", payload(), validator=validator, external_source_texts=("First company",))
    first["sources"].clear()
    assert validate_result_cached("signal_analysis", "{}", payload(), validator=validator, external_source_texts=("First company",))["sources"]
    validate_result_cached("signal_analysis", "{}", payload(), validator=validator, external_source_texts=("Second company",))
    assert len(calls) == 2


@pytest.mark.parametrize("valid_report", [True, False])
def test_worker_saves_paid_receipt_before_report_validation(tmp_path, monkeypatch, valid_report):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    data = result()
    data["report"]["sections"][0]["source_urls"] = ["https://www.amd.com/en/newsroom/" if valid_report else "https://www.amd.com/invented"]
    calls = []
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        calls.append(prepared)
        await on_message_start("msg_stock")
        return message(data, sources=True)
    monkeypatch.setattr(provider, "stream_message", stream)
    original_complete = repo.complete
    def complete(job, owner, output, usage):
        assert repo.get_provider_result(job)["tool_evidence"]
        return original_complete(job, owner, output, usage)
    monkeypatch.setattr(repo, "complete", complete)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    row = repo.get_job(job_id)
    assert len(calls) == 1
    assert calls[0].params["model"] == runtime.SONNET_MODEL
    assert repo.get_provider_result(job_id)["usage"]["cache_creation_1h_input_tokens"] == 200
    assert row["status"] == ("completed" if valid_report else "failed")
    assert row["budget_charge_microusd"] > 0
    if valid_report:
        public = repo.public(row)
        assert public["result"]["report"]["version"] == "stock-report-v1"
    else:
        assert row["result_json"] is None


def test_pause_turn_stock_conversation_uses_fixed_prefix_and_durable_rounds(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        calls.append(prepared)
        await on_message_start(f"msg_stock_{len(calls)}")
        if len(calls) > 1:
            progress = repo.get_provider_progress(job_id)
            assert len(progress["rounds"]) == 1
            assert progress["rounds"][0]["stop_reason"] == "pause_turn"
        return message(message_id=f"msg_stock_{len(calls)}", stop="pause_turn" if len(calls) == 1 else "end_turn")
    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    assert repo.get_job(job_id)["status"] == "completed"
    assert len(calls) == 2
    assert calls[0].params["system"] == calls[1].params["system"]
    assert "cache_control" not in calls[1].params  # Cache only the stable 1h prefix.
    assert repo.get_provider_result(job_id)["usage"]["cache_creation_1h_input_tokens"] == 400


def test_stock_manual_switch_is_independent_of_catalyst_mode(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    effective = SimpleNamespace(
        ai=SimpleNamespace(daily_max_jobs=0, daily_budget_usd=0, daily_token_limit=100_000_000,
                           manual_analysis_cooldown_seconds=0, manual_analysis_enabled=False, stock_analysis_enabled=True),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=False),
        earnings=SimpleNamespace(scheduled_analysis_enabled=False),
    )
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: effective)
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        await on_message_start("msg_stock")
        return message()
    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_configured_once(repo, settings(repo.path), "offline-worker", personal_config=PersonalConfig()))
    assert repo.get_job(job_id)["status"] == "completed"
    next_id = create(repo, {**payload(), "as_of": "2026-10-11T01:00:00Z"})
    effective.ai.stock_analysis_enabled = False
    asyncio.run(worker.run_configured_once(repo, settings(repo.path), "offline-worker", personal_config=PersonalConfig()))
    assert repo.get_job(next_id)["error_code"] == "manual_analysis_disabled"


def test_saved_stock_receipt_recovers_without_another_paid_request(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    repo.claim_due("old-worker", 60)
    repo.mark_submission_started(job_id, "old-worker", daily_limit=0)
    saved = runtime.claude_receipt(message())
    repo.record_provider_result(job_id, "old-worker", saved)
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE job_id=?",
                           (storage._iso(storage._utcnow() - timedelta(seconds=1)), job_id))
    async def forbidden(*args, **kwargs):
        raise AssertionError("saved paid receipt must never trigger a model request")
    monkeypatch.setattr(provider, "stream_message", forbidden)
    asyncio.run(worker.run_once(AIJobRepository(repo.path), settings(repo.path), "new-worker"))
    row = repo.get_job(job_id)
    assert row["status"] == "completed"
    assert json.loads(row["result_json"])["report"]["version"] == stock_report.REPORT_VERSION
    assert row["usage_cache_creation_1h_input_tokens"] == 200


def test_ambiguous_stock_request_keeps_reservation_and_does_not_retry(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    async def broken(prepared, **kwargs):
        calls.append(prepared)
        raise RuntimeError("offline simulated incomplete stream")
    monkeypatch.setattr(provider, "stream_message", broken)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    row = repo.get_job(job_id)
    assert row["error_code"] == "submission_outcome_unknown"
    assert row["budget_charge_microusd"] == runtime.budget_reservation_microusd("signal_analysis", model=runtime.SONNET_MODEL)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    assert len(calls) == 1


def paid_stock_result(repo, *, foreign_name=False, failed=False):
    job_id = create(repo)
    repo.claim_due("offline-worker", 60)
    repo.mark_submission_started(job_id, "offline-worker", daily_limit=0)
    data = result()
    data["report"]["sections"][0]["source_urls"] = ["https://www.amd.com/en/newsroom/"]
    title = "AMD company announcement"
    if foreign_name:
        # A long compact product name needs source binding under the existing
        # Chinese validator's Latin-character bound. It is synthetic test data.
        data["report"]["sections"][0]["summary"] = "关注HyperSpectralPhotonicsInternationalSystems。"
        title = "Company product HyperSpectralPhotonicsInternationalSystems update"
    receipt = runtime.claude_receipt(message(data, sources=True, source_title=title))
    repo.record_provider_result(job_id, "offline-worker", receipt)
    validated = runtime.receipt_result(receipt, "signal_analysis", payload())
    if failed:
        repo.fail(job_id, "offline-worker", "schema_validation_failed", usage=receipt["usage"])
    else:
        repo.complete(job_id, "offline-worker", validated, receipt["usage"])
    return repo.get_job(job_id), validated


def test_public_report_roundtrip_uses_only_validated_external_titles(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    row, expected = paid_stock_result(repo, foreign_name=True)
    # Show why the source context is required for this completed report.
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_result("signal_analysis", row["result_json"], payload())
    public = repo.public(row)
    assert public["result"] == expected
    assert public["error_code"] is None
    assert public["evidence_sources"] == [{
        "title": "Company product HyperSpectralPhotonicsInternationalSystems update",
        "url": "https://www.amd.com/en/newsroom/", "type": "web_search",
    }]
    assert repo._completed_result_state(row) == "valid"


@pytest.mark.parametrize("damage", ["missing_receipt", "wrong_id", "wrong_model", "failed_tool", "forged_url", "missing_local_block", "non_terminal", "terminal_error"])
def test_public_report_hides_invalid_receipts_and_unbound_sources(tmp_path, damage):
    repo = AIJobRepository(tmp_path / "jobs.db")
    row, _ = paid_stock_result(repo)
    receipt = json.loads(row["provider_result_json"])
    if damage == "missing_receipt":
        row["provider_result_json"] = None
    elif damage == "wrong_id":
        receipt["id"] = "msg_different"
        row["provider_result_json"] = json.dumps(receipt)
    elif damage == "wrong_model":
        receipt["model"] = "claude-haiku-5-5"
        row["provider_result_json"] = json.dumps(receipt)
    elif damage == "failed_tool":
        receipt["tool_evidence"][0]["status"] = "error"
        row["provider_result_json"] = json.dumps(receipt)
    elif damage == "forged_url":
        data = json.loads(row["result_json"])
        data["report"]["sections"][0]["source_urls"] = ["https://www.amd.com/invented"]
        row["result_json"] = json.dumps(data)
    elif damage == "non_terminal":
        receipt["stop_reason"] = "pause_turn"
        row["provider_result_json"] = json.dumps(receipt)
    elif damage == "terminal_error":
        receipt["terminal_error"] = "provider_continuation_limit"
        row["provider_result_json"] = json.dumps(receipt)
    else:
        data = json.loads(row["payload_json"])
        del data["signals"]
        row["payload_json"] = json.dumps(data)
    public = repo.public(row)
    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"
    assert public["evidence_sources"] == []
    assert repo._completed_result_state(row) == "invalid"


def test_failed_report_recovery_reuses_real_sources_and_preserves_accounting(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    row, expected = paid_stock_result(repo, foreign_name=True, failed=True)
    before = {key: value for key, value in row.items() if key.startswith("usage_") or key == "budget_charge_microusd"}
    recovered = repo.recover_schema_validation_failure(row["job_id"], row["anthropic_message_id"], expected)
    assert recovered["status"] == "completed"
    assert repo.public(recovered)["result"] == expected
    assert {key: recovered[key] for key in before} == before
    assert repo.get_provider_result(row["job_id"])["id"] == row["anthropic_message_id"]


def test_failed_report_recovery_cannot_add_a_source_or_replace_output(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    row, expected = paid_stock_result(repo, failed=True)
    forged = copy.deepcopy(expected)
    forged["report"]["sections"][0]["source_urls"] = ["https://www.amd.com/invented"]
    with pytest.raises(RuntimeError, match="ai_job_recovery_result_mismatch"):
        repo.recover_schema_validation_failure(row["job_id"], row["anthropic_message_id"], forged)
    assert repo.get_job(row["job_id"])["status"] == "failed"


@pytest.mark.parametrize("finish", [True, False])
def test_stock_end_turn_nudges_persist_and_stop_at_the_round_limit(tmp_path, monkeypatch, finish):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        calls.append(prepared)
        await on_message_start(f"msg_stock_{len(calls)}")
        if len(calls) > 1:
            progress = repo.get_provider_progress(job_id)
            assert progress["job_type"] == "signal_analysis"
            assert progress["rounds"][0]["continuation_reason"] == "incomplete_final_json"
        if finish and len(calls) == 2:
            return message(message_id=f"msg_stock_{len(calls)}")
        response = message(message_id=f"msg_stock_{len(calls)}")
        return response.model_copy(update={"content": [response.content[0].model_copy(update={"text": "继续核对资料。"})]})
    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    row = repo.get_job(job_id)
    receipt = repo.get_provider_result(job_id)
    assert len(calls) == (2 if finish else runtime.CONVERSATION_MAX_ROUNDS)
    assert len(receipt["rounds"]) == len(calls)
    assert row["usage_cache_creation_1h_input_tokens"] == len(calls) * 200
    assert row["status"] == ("completed" if finish else "failed")
    if finish:
        assert repo.public(row)["result"]["report"]
    else:
        assert row["error_code"] == "provider_continuation_limit"
        asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
        assert len(calls) == runtime.CONVERSATION_MAX_ROUNDS


def test_continued_stock_report_recovers_after_publication_write_failure(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    final = result()
    final["report"]["sections"][0].update(
        summary="关注HyperSpectralPhotonicsInternationalSystems。",
        source_urls=["https://www.amd.com/en/newsroom/"],
    )
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        calls.append(prepared)
        await on_message_start(f"msg_stock_{len(calls)}")
        if len(calls) == 1:
            response = message(message_id="msg_stock_1")
            return response.model_copy(update={"content": [response.content[0].model_copy(update={"text": "正在核对原始公告。"})]})
        return message(final, message_id="msg_stock_2", sources=True,
                       source_title="Company product HyperSpectralPhotonicsInternationalSystems update")
    monkeypatch.setattr(provider, "stream_message", stream)
    monkeypatch.setattr(worker, "_STORAGE_WRITE_RETRY_DELAY_SECONDS", 0)
    def busy(*args, **kwargs):
        assert repo.get_provider_result(job_id)["job_type"] == "signal_analysis"
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(repo, "complete", busy)
    asyncio.run(worker.run_once(repo, settings(repo.path), "old-worker"))
    saved = repo.get_job(job_id)
    assert saved["error_code"] == "local_storage_error"
    assert len(repo.get_provider_result(job_id)["rounds"]) == 2
    charge = saved["budget_charge_microusd"]
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET next_attempt_at=NULL WHERE job_id=?", (job_id,))
    async def forbidden(*args, **kwargs):
        raise AssertionError("saved conversation must not make another paid request")
    monkeypatch.setattr(provider, "stream_message", forbidden)
    restarted = AIJobRepository(repo.path)
    asyncio.run(worker.run_once(restarted, settings(repo.path), "new-worker"))
    recovered = restarted.get_job(job_id)
    assert recovered["status"] == "completed"
    assert restarted.public(recovered)["result"] == final
    assert recovered["budget_charge_microusd"] == charge
    assert recovered["usage_cache_creation_1h_input_tokens"] == 400
    assert len(calls) == 2


def test_known_field_labels_are_normalized_only_in_report_narrative_paths():
    raw = result()
    raw["data_quality"] = 36
    raw["breakdown_risk"] = 30
    raw["data_quality_notes"] = [
        "scores.data_quality为36，breakdown_risk为30，earnings_history截至2026-10-09。",
    ]
    raw["report"]["data_gaps"] = ["earnings_history不可用。"]
    raw["report"]["sections"][0].update(
        id="company", title="company_profile", summary="scores.data_quality为36。",
        points=["price_history截至2026-10-09；原值-3.9%、0.05与1.132898美元。"],
        evidence_ids=["earnings_history"], source_urls=["https://www.amd.com/earnings_history?s=scores.data_quality"],
    )
    original = copy.deepcopy(raw)
    normalized = stock_report.normalize_stock_report_labels(raw)
    assert raw == original
    assert normalized["data_quality_notes"] == ["程序资料质量分为36，向下破位风险为30，历史盈利截至2026-10-09。"]
    assert normalized["report"]["data_gaps"] == ["历史盈利不可用。"]
    assert normalized["report"]["sections"][0]["evidence_ids"] == ["earnings_history"]
    assert normalized["report"]["sections"][0]["source_urls"] == original["report"]["sections"][0]["source_urls"]
    assert normalized["report"]["sections"][0]["points"] == ["价格历史截至2026-10-09；原值-3.9%、0.05与1.132898美元。"]
    assert normalized["data_quality"] == 36 and normalized["breakdown_risk"] == 30
    assert set(raw) == set(normalized)
    assert set(raw["report"]) == set(normalized["report"])
    assert set(raw["report"]["sections"][0]) == set(normalized["report"]["sections"][0])
    assert stock_report.normalize_stock_report_labels(normalized) == normalized


def test_label_normalization_respects_identifier_boundaries_and_urls():
    text = (
        "scores.data_quality=36；scores.data_quality_extra、my_earnings_history、"
        "earnings_history2、other.scores.data_quality、breakdown_risk.extra不改；"
        "链接https://www.amd.com/earnings_history?x=scores.data_quality，"
        "[earnings_history](https://www.amd.com/scores.data_quality)；"
        "earnings_history截至2026-10-09。"
    )
    raw = result()
    raw["data_quality_notes"] = [text]
    normalized = stock_report.normalize_stock_report_labels(raw)["data_quality_notes"][0]
    assert normalized == text.replace("scores.data_quality=36", "程序资料质量分=36").replace("earnings_history截至", "历史盈利截至")
    for value in ("www.amd.com/earnings_history", "ftp://www.amd.com/scores.data_quality"):
        raw["data_quality_notes"] = [value]
        assert stock_report.normalize_stock_report_labels(raw)["data_quality_notes"] == [value]


@pytest.mark.parametrize("text", [
    "scores.data_quality will increase because the company expects strong future earnings.",
    "The company will increase revenue next quarter.",
])
def test_field_mapping_does_not_allow_english_sentences(text):
    raw = result()
    raw["data_quality_notes"] = [text]
    with pytest.raises(ValueError, match="english_prose_not_allowed|simplified_chinese_text_required"):
        validate_result("signal_analysis", json.dumps(raw, ensure_ascii=False), payload())


def test_field_mapping_is_not_applied_to_legacy_technical_analysis():
    raw = _signal_result()
    raw["data_quality_notes"] = ["scores.data_quality为36。"]
    assert stock_report.normalize_stock_report_labels(raw) == raw
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_result("signal_analysis", json.dumps(raw, ensure_ascii=False), payload())


def test_original_paid_v1_receipt_with_field_labels_can_be_recovered(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    # Model/schema metadata from the paid v1 request must survive a v2 prompt.
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET prompt_version=?,schema_sha256=? WHERE job_id=?",
                           ("signal-analysis-stock-report-zh-cn-v1", "fc781f0124a23036908bfcc5d8df06b2088b79cced7356ee3cb2c487c0ed6d09", job_id))
    repo.claim_due("offline-worker", 60)
    repo.mark_submission_started(job_id, "offline-worker", daily_limit=0)
    raw = result()
    raw.update(data_quality=36, breakdown_risk=30)
    raw["data_quality_notes"] = [
        "scores.data_quality为36：顶部与底部评分各仅覆盖40%分项；本字段沿用程序值。",
        "breakdown_risk程序未提供，此处30为对既有信号的定性综合，不是程序评分。",
        "财报历史（earnings_history）不可用；资料获取于2026-10-11，不视为实时状态。",
    ]
    raw["report"]["data_gaps"] = ["财报历史（earnings_history）不可用。"]
    receipt = runtime.claude_receipt(message(raw))
    receipt["usage"]["cache_creation_5m_input_tokens"] = None
    receipt["usage"]["cache_creation_1h_input_tokens"] = None
    before_receipt = copy.deepcopy(receipt)
    repo.record_provider_result(job_id, "offline-worker", receipt)
    repo.fail(job_id, "offline-worker", "schema_validation_failed", usage=receipt["usage"])
    failed = repo.get_job(job_id)
    candidate = runtime.receipt_result(receipt, "signal_analysis", payload())
    assert receipt == before_receipt
    runtime.enforce_new_output_limits(failed, receipt["output_text"], payload())
    restored = repo.recover_schema_validation_failure(job_id, failed["anthropic_message_id"], candidate)
    assert restored["status"] == "completed"
    assert restored["prompt_version"] == "signal-analysis-stock-report-zh-cn-v1"
    assert restored["provider_result_json"] == failed["provider_result_json"]
    assert restored["budget_charge_microusd"] == failed["budget_charge_microusd"]
    assert {k: restored[k] for k in failed if k.startswith("usage_")} == {k: failed[k] for k in failed if k.startswith("usage_")}
    public = repo.public(restored)
    assert public["result"] == candidate
    assert public["result"]["data_quality"] == 36
    assert public["result"]["data_quality_notes"][0].startswith("程序资料质量分为36")
    assert public["result"]["report"]["data_gaps"] == ["财报历史（历史盈利）不可用。"]
    assert public["usage"]["cache_creation_1h_input_tokens"] is None
