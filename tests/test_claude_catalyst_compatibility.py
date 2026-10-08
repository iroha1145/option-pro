from __future__ import annotations

import sqlite3
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from app.access import request_owner_access_context
from app.services.ai_jobs import runtime
from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService
from test_catalyst_local_intelligence import (
    _apply_news, _finish_job, _focus_result, _news_change, _news_result, _stack,
)


@pytest.fixture(autouse=True)
def _owner_context():
    with request_owner_access_context(True):
        yield


def _switch(intelligence, ai, *, legacy=False):
    service = LocalCatalystIntelligence(
        intelligence.db_path, ai, mode="manual", canonical_tickers=("NVDA",),
        **({"model": "gpt-5.6-terra", "reasoning": "max"} if legacy else {}),
    )
    service.initialize()
    return service


def _count(ai):
    with sqlite3.connect(ai.path) as connection:
        return connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]


def _news_stack(tmp_path):
    etl, ai, initial = _stack(tmp_path)
    legacy = _switch(initial, ai, legacy=True)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _apply_news(etl, [_news_change(1, 900, available_at=now - timedelta(minutes=2))], as_of=now)
    legacy.reconcile()
    return etl, ai, legacy, now


def _finish_claude_with_sources(ai, job_id, result, sources):
    owner = f"source-test-{job_id}"
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == job_id
    assert ai.mark_submission_started(job_id, owner, daily_limit=4) == "started"
    usage = {
        "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1,
        "reasoning_tokens": 0, "total_tokens": 2, "cache_creation_input_tokens": 0,
        "cache_creation_5m_input_tokens": 0, "cache_creation_1h_input_tokens": 0,
        "web_search_requests": 1, "web_fetch_requests": 0, "code_execution_requests": 0,
    }
    ai.link_anthropic_message(job_id, owner, "msg_source_test")
    ai.record_provider_result(job_id, owner, {
        "provider": "anthropic", "model": "claude-haiku-5-5", "id": "msg_source_test",
        "output_text": json.dumps(result), "stop_reason": "tool_use",
        "terminal_error": None, "usage": usage, "evidence_sources": sources,
    })
    ai.complete(job_id, owner, result, usage)


def test_defaults_and_explicit_legacy_pair(tmp_path):
    assert (CatalystSettings().model, CatalystSettings().reasoning) == ("claude-haiku-5-5", "xhigh")
    assert CatalystSettings(model="gpt-5.6-terra", reasoning="max").model == "gpt-5.6-terra"
    with pytest.raises(ValidationError):
        CatalystSettings(model="claude-haiku-5-5", reasoning="max")
    _, ai, initial = _stack(tmp_path)
    assert (initial.model, initial.reasoning) == ("claude-haiku-5-5", "xhigh")
    assert _switch(initial, ai, legacy=True).model == "gpt-5.6-terra"


@pytest.mark.parametrize("state", ["pending", "queued", "in_progress", "failed", "completed"])
def test_legacy_news_keeps_identity_and_reconcile_does_not_create_jobs(tmp_path, state):
    _, ai, legacy, _ = _news_stack(tmp_path)
    job = legacy.request_analysis(900, force=False)
    if state == "completed":
        _finish_job(ai, job["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    elif state in {"failed", "queued", "in_progress"}:
        owner = "test-owner"
        assert ai.claim_due(owner, lease_seconds=60)["job_id"] == job["job_id"]
        if state in {"queued", "in_progress"}:
            assert ai.mark_submission_started(job["job_id"], owner, daily_limit=4) == "started"
            ai.record_background_response(job["job_id"], owner, "resp_test", state, delay_seconds=30)
        if state == "failed":
            ai.fail(job["job_id"], owner, "provider_failed")
    current = _switch(legacy, ai)
    current.reconcile()
    detail = current.analysis_job(job["job_id"])
    assert detail is not None
    assert detail["status"] == state
    assert (detail["model"], detail["reasoning"]) == ("gpt-5.6-terra", "max")
    assert _count(ai) == 1
    if state in {"pending", "queued", "in_progress", "completed"}:
        assert current.request_analysis(900, force=False)["job_id"] == job["job_id"]
        assert _count(ai) == 1
    if state == "completed":
        feed = current.feed(as_of=datetime.now(timezone.utc), limit=10)
        assert feed["items"][0]["analysis"] is not None


def test_new_news_uses_claude_and_original_provider_schema_hash(tmp_path):
    etl, ai, legacy, now = _news_stack(tmp_path)
    old = legacy.request_analysis(900, force=False)
    old_row = ai.get_job(old["job_id"])
    assert old_row["schema_sha256"] == runtime.schema_identity("news_impact", model="gpt-5.6-terra")[1]
    current = _switch(legacy, ai)
    _apply_news(etl, [_news_change(2, 901, available_at=now - timedelta(seconds=1), title="NVIDIA quarterly earnings beat forecasts")], as_of=now + timedelta(seconds=1))
    current.reconcile()
    new = current.request_analysis(901, force=False)
    assert (new["model"], new["reasoning"]) == ("claude-haiku-5-5", "xhigh")
    assert ai.get_job(new["job_id"])["schema_sha256"] == runtime.schema_identity("news_impact", model="claude-haiku-5-5")[1]


@pytest.mark.parametrize(("field", "value"), [
    ("model", "unknown-model"), ("reasoning", "xhigh"),
    ("schema_sha256", "f" * 64), ("prompt_version", "unknown-prompt"),
])
def test_wrong_legacy_identity_still_rejected(tmp_path, field, value):
    _, ai, legacy, _ = _news_stack(tmp_path)
    job = legacy.request_analysis(900, force=False)
    row = ai.get_job(job["job_id"])
    row[field] = value
    current = _switch(legacy, ai)
    assert not current._has_current_job_identity(row, expected_type="news_impact")
    assert current._compatible_news_public_job(row) is None


def test_completed_legacy_focus_keeps_actual_identity_for_visitors(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    with request_owner_access_context(True):
        news = legacy.request_analysis(900, force=False)
        _finish_job(ai, news["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
        revision = legacy.reconcile()["prepared_revision"]
        cycle = legacy.request_market_focus_cycle(expected_prepared_revision=revision)
        _finish_job(ai, cycle["job_id"], _focus_result(ai, cycle))
        legacy.reconcile()
    current = _switch(legacy, ai)
    current.reconcile()
    with request_owner_access_context(False):
        detail = current.market_focus_cycle(cycle["cycle_id"])
    assert detail is not None and detail["result"] is not None
    assert detail["model"] == "gpt-5.6-terra"
    assert detail["reasoning_effort"] == "max"
    assert _count(ai) == 2


def test_new_focus_cycle_defaults_to_claude_after_legacy_news(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    news = legacy.request_analysis(900, force=False)
    _finish_job(ai, news["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    legacy.reconcile()
    current = _switch(legacy, ai)
    revision = current.reconcile()["prepared_revision"]
    cycle = current.request_market_focus_cycle(expected_prepared_revision=revision)
    job = ai.get_job(cycle["job_id"])
    assert (job["model"], job["reasoning"]) == ("claude-haiku-5-5", "xhigh")
    assert job["schema_sha256"] == runtime.schema_identity("market_focus", model=job["model"])[1]


def test_personal_news_projection_accepts_legacy_but_rejects_wrong_hash(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    job = legacy.request_analysis(900, force=False)
    service = object.__new__(PersonalCatalystService)
    service.settings = CatalystSettings()
    service.ai_repository = ai
    service._ai_store_ready = lambda: True
    assert service._verified_news_job_row(job["job_id"])["model"] == "gpt-5.6-terra"
    with sqlite3.connect(ai.path) as connection:
        connection.execute("UPDATE ai_jobs SET schema_sha256=? WHERE job_id=?", ("f" * 64, job["job_id"]))
    assert service._verified_news_job_row(job["job_id"]) is None


def test_news_result_metadata_stays_legacy_during_claude_reanalysis(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    job = legacy.request_analysis(900, force=False)
    _finish_job(ai, job["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    legacy.reconcile()
    # Simulate a pre-migration link with no saved provider identity.
    with sqlite3.connect(legacy.db_path) as connection:
        connection.execute("UPDATE catalyst_local_analysis_links SET input_context_json=NULL")
    current = _switch(legacy, ai)
    current.reconcile()
    retry = current.request_analysis(900, force=True)
    assert retry["model"] == "claude-haiku-5-5"
    with request_owner_access_context(False):
        feed = current.feed(as_of=datetime.now(timezone.utc), limit=10)
        item = PersonalCatalystService._project_news_item(feed["items"][0])
    assert item["analysis"] is not None
    assert item["analysis_model"] == "gpt-5.6-terra"
    assert item["analysis_reasoning"] == "max"
    assert "analysis_model" not in item["analysis"]


def test_published_news_sources_do_not_follow_new_queued_attempt(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    current = _switch(legacy, ai)
    job = current.request_analysis(900, force=False)
    sources = [{"title": "Original source", "url": "https://www.anthropic.com/news", "type": "web_search"}]
    _finish_claude_with_sources(ai, job["job_id"], _news_result(
        news_id=900, change_sequence=1, content_hash="hash-900-1",
    ), sources)
    current.reconcile()
    current.request_analysis(900, force=True)
    with request_owner_access_context(False):
        item = PersonalCatalystService._project_news_item(current.feed(
            as_of=datetime.now(timezone.utc), limit=10,
        )["items"][0])
    assert item["analysis_sources"] == sources
    assert "evidence_sources" not in item["analysis"]


def test_published_focus_sources_survive_public_projection(tmp_path):
    _, ai, legacy, _ = _news_stack(tmp_path)
    current = _switch(legacy, ai)
    news = current.request_analysis(900, force=False)
    _finish_job(ai, news["job_id"], _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"))
    revision = current.reconcile()["prepared_revision"]
    cycle = current.request_market_focus_cycle(expected_prepared_revision=revision)
    sources = [{"title": "Focus source", "url": "https://www.anthropic.com/docs", "type": "web_search"}]
    _finish_claude_with_sources(ai, cycle["job_id"], _focus_result(ai, cycle), sources)
    current.reconcile()
    service = object.__new__(PersonalCatalystService)
    with request_owner_access_context(False):
        detail = current.market_focus_cycle(cycle["cycle_id"])
        projected = service._project_focus_cycle_for_access(detail, include_owner_state=False)
    assert projected["evidence_sources"] == sources


@pytest.mark.parametrize(("model", "openai", "anthropic", "expected"), [
    ("claude-haiku-5-5", "old-key", "", False),
    ("claude-haiku-5-5", "", "new-key", True),
    ("gpt-5.6-terra", "old-key", "", True),
    ("gpt-5.6-terra", "", "new-key", False),
])
def test_provider_specific_key_gate(model, openai, anthropic, expected):
    service = object.__new__(PersonalCatalystService)
    service.settings = CatalystSettings()
    service.ai_settings = SimpleNamespace(
        openai_model=model, openai_api_key=SecretStr(openai), anthropic_api_key=SecretStr(anthropic),
    )
    service._ai_repository_injected = False
    service._intelligence_injected = False
    assert service._ai_configured() is expected


def test_injected_test_service_without_keys_keeps_existing_fallback():
    service = object.__new__(PersonalCatalystService)
    service.settings = CatalystSettings()
    service.ai_settings = SimpleNamespace()
    service._ai_repository_injected = True
    service._intelligence_injected = False
    assert service._ai_configured() is True
