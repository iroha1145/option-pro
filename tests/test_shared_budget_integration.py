"""Shared budget configuration and real API preflight, without provider calls."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

import app.config as settings_module
import test_market_brief_api as api_helpers
from app.api import market_brief as api
from app.config import Settings
from app.personal_config import ModelBudgetConfig, PersonalConfig
from app.services.market_brief import BriefStore
from app.services.model_budget import SharedModelBudget
from app.worker.tasks import MarketBriefTask


NOW = datetime(2026, 10, 8, 13, 45, tzinfo=timezone.utc)


@pytest.mark.parametrize("amount", [True, -1, float("nan"), float("inf"), 9.501])
def test_shared_amount_rejects_invalid_configuration(amount):
    with pytest.raises(ValidationError):
        ModelBudgetConfig(daily_budget_usd=amount)
    with pytest.raises(ValidationError):
        Settings(model_daily_budget_usd=amount)


@pytest.mark.parametrize("start", [True, 1, "2026-10-08T13:45:00", NOW.replace(tzinfo=None)])
def test_budget_start_requires_an_explicit_timezone(start):
    with pytest.raises(ValidationError):
        ModelBudgetConfig(accounting_start_at=start)
    with pytest.raises(ValidationError):
        Settings(model_budget_start_at=start)


def test_production_profile_is_the_source_for_both_model_runtimes(monkeypatch, tmp_path):
    start = "2026-10-08T22:45:00+09:00"
    profile = PersonalConfig.model_validate({"model_budget": {
        "daily_budget_usd": 9.5, "accounting_start_at": start,
    }})
    monkeypatch.setattr(settings_module, "_PERSONAL_CONFIG", profile)
    monkeypatch.setenv("MODEL_DAILY_BUDGET_USD", "1")
    monkeypatch.setenv("MODEL_BUDGET_START_AT", "2000-01-01T00:00:00Z")
    settings = Settings(openai_job_db_path=tmp_path / "jobs.db")
    assert settings.model_daily_budget_usd == 9.5
    assert settings.model_budget_start_at == NOW
    run_config = MarketBriefTask("budget-test", settings=settings, personal_config=profile)._run_config()
    assert run_config.shared_daily_budget_usd == 9.5
    assert run_config.shared_budget_start_at == NOW
    assert run_config.budget_path == settings.openai_job_db_path


def _historical_report(root, *, at=NOW, complete=True):
    runs = root / "runs"
    runs.mkdir(parents=True)
    ident = "mb_legacy"
    name = f"2026-10-08-pre_open-{ident}.json"
    stamp = at.isoformat().replace("+00:00", "Z")
    record = {
        "run_id": ident, "model": "claude-opus-5-5", "started_at": stamp,
        "usage_complete": complete, "cost_microusd": 500_000 if complete else None,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "web_search_requests": 0},
    }
    (runs / name).write_text(json.dumps({"record": record}))
    (root / "index.json").write_text(json.dumps({"runs": [
        {"run_id": ident, "started_at": stamp, "file": name},
    ]}))


@pytest.mark.parametrize("complete", [True, False])
def test_opus_api_preflight_imports_the_same_persistent_history(monkeypatch, tmp_path, complete):
    root = tmp_path / "market-brief"
    _historical_report(root, complete=complete)
    settings = Settings(model_daily_budget_usd=9.5, openai_job_db_path=tmp_path / "ai-jobs.db")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "_store", lambda: BriefStore(root))
    snapshot = api._shared_budget(NOW)
    expected = 500_000 if complete else 6_060_000
    assert snapshot["used_microusd"] == expected
    assert SharedModelBudget(settings.openai_job_db_path, 9.5).snapshot(NOW)["used_microusd"] == expected
    assert snapshot["budget_available"] is complete


def test_opus_api_new_window_excludes_previous_report(monkeypatch, tmp_path):
    root = tmp_path / "market-brief"
    _historical_report(root, at=NOW - timedelta(hours=1), complete=False)
    settings = Settings(model_daily_budget_usd=9.5, model_budget_start_at=NOW,
                        openai_job_db_path=tmp_path / "ai-jobs.db")
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "_store", lambda: BriefStore(root))
    snapshot = api._shared_budget(NOW)
    assert snapshot["budget_used_usd"] == 0
    assert snapshot["budget_remaining_usd"] == 9.5
    assert snapshot["budget_reset_at"] == "2026-10-09T00:00:00.000000Z"


def test_owner_preflight_recovers_a_saved_receipt_before_rejecting_new_work(monkeypatch, tmp_path):
    root = tmp_path / "market-brief"
    path = tmp_path / "ai-jobs.db"
    budget = SharedModelBudget(path, 9.5)
    run_id = "mb_20261008_pre_open_00000001"
    budget.reserve_brief_request(run_id, 1, 6_060_000, NOW)
    store = BriefStore(root)
    store.write_request_round(run_id, {
        "round_index": 1, "accounting_complete": True,
        "confirmed_unbilled": False, "cost_microusd": 2_000,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "web_search_requests": 0},
    })
    # Simulate exit after the durable receipt but before its ledger settlement.
    assert budget.snapshot(NOW)["budget_used_usd"] == 6.06
    settings = Settings(model_daily_budget_usd=9.5, openai_job_db_path=path)
    monkeypatch.setattr(api, "get_settings", lambda: settings)
    monkeypatch.setattr(api, "_store", lambda: store)
    snapshot = api._shared_budget(NOW)
    assert snapshot["budget_used_usd"] == 0.002
    assert snapshot["budget_available"] is True
    assert api._shared_budget(NOW)["budget_used_usd"] == 0.002


def test_haiku_capacity_recovers_opus_receipts_in_the_same_shared_account(monkeypatch, tmp_path):
    from app.services.ai_jobs.repository import AIJobRepository
    from app.services.ai_jobs import repository as repository_module
    root = tmp_path / "market-brief"
    path = tmp_path / "ai-jobs.db"
    budget = SharedModelBudget(path, 9.5)
    run_id = "mb_20261008_pre_open_00000002"
    budget.reserve_brief_request(run_id, 1, 9_000_000, NOW)
    BriefStore(root).write_request_round(run_id, {
        "round_index": 1, "accounting_complete": True,
        "confirmed_unbilled": False, "cost_microusd": 2_000,
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                  "web_search_requests": 0},
    })
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    snapshot = AIJobRepository(path).budget_snapshot(
        daily_limit=0, daily_budget_usd=0, shared_daily_budget_usd=9.5,
        max_concurrency=4, model="claude-haiku-5-5", now=NOW,
    )
    assert snapshot["budget_used_usd"] == 0.002
    assert snapshot["dollar_budget_available"] is True


def test_owner_budget_rejection_cannot_queue_a_worker_action(monkeypatch):
    monkeypatch.setattr(api, "_store", lambda: api_helpers.FakeStore(api_helpers.SAMPLE))
    monkeypatch.setattr(api, "_key_configured", lambda: True)
    monkeypatch.setattr(api, "_shared_budget", lambda now: {
        "budget_available": False, "daily_budget_usd": 9.5, "budget_remaining_usd": 0.2,
    })
    monkeypatch.setattr(api, "WorkerStateRepository", lambda *a, **kw: pytest.fail("budget rejection queued a worker action"))
    with TestClient(api_helpers._app(), base_url=api_helpers.ORIGIN) as client:
        anonymous = client.get("/api/market-brief/status")
        assert anonymous.status_code == 401
        api_helpers._login(client)
        response = client.post("/api/market-brief/runs", json={}, headers=api_helpers._action_headers())
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "daily_budget_usd_reached"


def test_owner_status_reports_budget_storage_failure_safely(monkeypatch):
    monkeypatch.setattr(api, "_store", lambda: api_helpers.FakeStore(api_helpers.SAMPLE))
    monkeypatch.setattr(api, "_worker_actions", lambda now: (None, None))
    def unavailable(now):
        raise RuntimeError("sensitive internal accounting failure")
    monkeypatch.setattr(api, "_shared_budget", unavailable)
    with TestClient(api_helpers._app(), base_url=api_helpers.ORIGIN) as client:
        api_helpers._login(client)
        response = client.get("/api/market-brief/status")
    assert response.status_code == 503
    assert response.json()["detail"] == {"code": "shared_budget_unavailable"}
