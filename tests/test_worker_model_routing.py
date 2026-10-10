from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from app.access import request_owner_access_context
from app.services.ai_jobs import repository as repository_module
from app.services.ai_jobs import runtime, worker
from app.services.catalysts import local_intelligence as local
from app.services.catalysts.etl_repository import CatalystEtlRepository
from app.worker.tasks import CatalystSyncTask, FocusTask, _build_local_intelligence
from test_ai_jobs import _settings
from test_catalyst_local_intelligence import _apply_news, _news_change, _news_result
from test_personal_worker import _runtime_settings, _worker_config


@pytest.fixture(autouse=True)
def owner_context():
    with request_owner_access_context(True):
        yield


@pytest.fixture
def setup(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    monkeypatch.setattr(local, "_utc_now", lambda: now)
    monkeypatch.setattr(repository_module, "_utcnow", lambda: now)
    monkeypatch.setattr(local, "macro_conditions_context", lambda: None)
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: _runtime_settings(scheduled=True))
    article_reads = []

    def offline_article(url, **kwargs):
        article_reads.append(url)
        return {"status": "unavailable", "text": "", "source_url": "", "fetched_at": local._iso(now), "reason": "no_matching_article_body", "truncated": False}

    monkeypatch.setattr("app.services.catalysts.article_content.fetch_article", offline_article)
    settings = _settings(tmp_path / "ai-jobs.db")
    vars(settings).update(vars(_worker_config(tmp_path, token="fixture-owner", url="https://macrolens.example")))
    settings.openai_model = "claude-haiku-5-5"
    settings.openai_reasoning = "xhigh"
    settings.openai_news_model = runtime.LUNA_MODEL
    settings.openai_news_reasoning = "max"
    settings.openai_market_focus_model = runtime.SONNET_MODEL
    settings.openai_market_focus_reasoning = "xhigh"
    settings.openai_max_concurrency = 4
    settings.anthropic_api_key = SecretStr("fixture-key-not-used")
    config = SimpleNamespace(
        ai=SimpleNamespace(model="claude-haiku-5-5", reasoning="xhigh"),
        features=SimpleNamespace(catalyst_mode="scheduled"),
        catalyst=SimpleNamespace(sync_seconds=120, focus_seconds=1800, news_source="macrolens"),
    )
    return settings, config, now, article_reads


def build_through_producer(settings, config, producer):
    async def build():
        if producer == "factory":
            return await _build_local_intelligence(config, settings)
        if producer == "focus":
            task = FocusTask("routing-test", enabled=True, settings=settings, personal_config=config)
            assert await task._prepare_personal() == "personal"
            return task._intelligence
        task = CatalystSyncTask("routing-test", settings=settings, personal_config=config)
        return await task._local_intelligence(attempt=True)
    return asyncio.run(build())


def automatic_news(engine, settings, now):
    etl = CatalystEtlRepository(settings.macrolens_cache_db_path)
    etl.initialize()
    _apply_news(etl, [_news_change(1, 900, available_at=now - timedelta(minutes=5), sources=("Reuters", "Bloomberg"))], as_of=now)
    engine.reconcile(allow_scheduled_jobs=False)
    assert engine.run_scheduled(now=now)["queued"] == 1
    with engine.ai_repository._connect() as connection:
        row = dict(connection.execute("SELECT * FROM ai_jobs WHERE job_type='news_impact'").fetchone())
    return row


def automatic_focus_after_fixture_result(engine, news, now):
    repository = engine.ai_repository
    owner = "fixture-result"
    assert repository.claim_due(owner, lease_seconds=60)["job_id"] == news["job_id"]
    # Deterministic local fixture output only: no provider submission, receipt,
    # reservation or fee. This opens the real scheduler's published-news gate.
    repository.complete(news["job_id"], owner, _news_result(news_id=900, change_sequence=1, content_hash="hash-900-1"), {
        "input_tokens": 0, "cached_input_tokens": 0, "output_tokens": 0,
        "reasoning_tokens": 0, "total_tokens": 0,
    })
    engine.reconcile(allow_scheduled_jobs=False)
    assert engine.run_scheduled(now=now)["queued"] == 1
    with repository._connect() as connection:
        return dict(connection.execute("SELECT * FROM ai_jobs WHERE job_type='market_focus'").fetchone())


def assert_submission_identity(job, settings):
    selected = runtime.settings_for_job(settings, job["job_type"])
    expected = runtime.schema_identity(job["job_type"], model=selected.openai_model)
    assert (job["model"], job["reasoning"]) == (selected.openai_model, selected.openai_reasoning)
    assert (job["schema_version"], job["schema_sha256"]) == expected
    assert runtime.schema_identity_current(job["job_type"], job["prompt_version"], job["schema_version"], job["schema_sha256"], current_identity=expected)
    assert runtime.runtime_configuration_valid(selected)
    assert job["execution_mode"] == runtime.OFFICIAL_EXECUTION_MODE
    assert job["status"] == "pending"
    assert job["submission_started_at"] is None
    assert job["budget_charge_microusd"] == 0


@pytest.mark.parametrize("producer", ["factory", "focus", "sync"])
def test_real_worker_factories_queue_each_model_with_matching_runtime_contract(setup, producer):
    settings, config, now, article_reads = setup
    before = copy.deepcopy(vars(settings))
    engine = build_through_producer(settings, config, producer)
    assert engine.mode == "scheduled"
    assert (engine.model, engine.reasoning) == (runtime.LUNA_MODEL, "max")
    assert (engine.focus_model, engine.focus_reasoning) == (runtime.SONNET_MODEL, "xhigh")
    assert engine.focus_verification_enabled is True
    news = automatic_news(engine, settings, now)
    assert_submission_identity(news, settings)
    focus = automatic_focus_after_fixture_result(engine, news, now)
    assert_submission_identity(focus, settings)
    payload = json.loads(focus["payload_json"])
    assert payload["verification_version"] == "web-evidence-v1"
    assert payload["input_schema_version"] == "market-focus-input-v3"
    assert focus["schema_version"] == "market_focus_verified_zh_cn_v2"
    assert len(payload["events"]) == 1
    assert article_reads  # All article acquisition used the offline fixture.
    assert vars(settings) == before
    assert runtime.model_identity_for_job(settings, "earnings_impact") == (config.ai.model, config.ai.reasoning)
    with engine.ai_repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2
        assert connection.execute("SELECT SUM(budget_charge_microusd) FROM ai_jobs").fetchone()[0] == 0
    # A second normal scheduler read reuses pending work, never duplicates it.
    engine.run_scheduled(now=now)
    with engine.ai_repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2


@pytest.mark.parametrize("job_type", ["news_impact", "market_focus"])
def test_actual_worker_guard_accepts_automatic_job_then_stops_before_provider(setup, monkeypatch, job_type):
    settings, config, now, _ = setup
    engine = build_through_producer(settings, config, "factory")
    news = automatic_news(engine, settings, now)
    job = news if job_type == "news_impact" else automatic_focus_after_fixture_result(engine, news, now)
    calls = []

    def stop_before_provider(selected, kind, payload):
        calls.append((kind, selected.openai_model, selected.openai_reasoning))
        raise RuntimeError("test_stop_before_provider_request")

    monkeypatch.setattr(runtime, "prepare_background", stop_before_provider)
    monkeypatch.setattr(runtime, "prepare_claude", stop_before_provider)
    owner = "routing-guard-test"
    claimed = engine.ai_repository.claim_due(owner, lease_seconds=60)
    assert claimed["job_id"] == job["job_id"]
    asyncio.run(worker.process_job(engine.ai_repository, settings, claimed, owner))
    assert calls == [(job_type, job["model"], job["reasoning"])]
    after = engine.ai_repository.get_job(job["job_id"])
    assert after["error_code"] != "runtime_configuration_changed"
    assert after["submission_started_at"] is None
    assert after["openai_response_id"] is None and after["anthropic_message_id"] is None
    assert after["budget_charge_microusd"] == 0


def test_legacy_injected_settings_keep_config_defaults_without_mutation(tmp_path, monkeypatch):
    monkeypatch.setattr("app.services.runtime_settings.get_effective_runtime_settings", lambda: _runtime_settings())
    settings = _worker_config(tmp_path)
    before = copy.deepcopy(vars(settings))
    config = SimpleNamespace(ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"), features=SimpleNamespace(catalyst_mode="manual"))
    options = {}

    class Factory:
        def __init__(self, path, repository, **kwargs):
            options.update(kwargs)

        def initialize(self):
            pass

    asyncio.run(_build_local_intelligence(config, settings, factory=Factory))
    assert (options["model"], options["reasoning"]) == ("gpt-5.6-terra", "max")
    assert (options["news_model"], options["news_reasoning"]) == ("gpt-5.6-terra", "max")
    assert (options["focus_model"], options["focus_reasoning"]) == ("gpt-5.6-terra", "max")
    assert vars(settings) == before


def test_explicit_invalid_route_effort_is_not_replaced_by_global_default(setup):
    settings, config, _, _ = setup
    settings.openai_market_focus_reasoning = "max"
    with pytest.raises(ValueError, match="runtime_configuration_invalid"):
        asyncio.run(_build_local_intelligence(config, settings))
