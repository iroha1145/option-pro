from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import ai
from app.config import Settings
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import AIJobRepository
from app.services.ai_jobs.security import require_expensive_action
from app.services.ai_jobs.worker import process_job


def _settings(path):
    return SimpleNamespace(
        openai_api_key=SecretStr("test-key"),
        openai_base_url="",
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_timeout_seconds=900,
        openai_control_timeout_seconds=30,
        openai_max_retries=0,
        openai_max_output_tokens=16384,
        openai_max_concurrency=2,
        openai_background_initial_poll_seconds=2,
        openai_background_max_poll_seconds=15,
        openai_background_poll_timeout_seconds=1800,
        openai_job_db_path=path,
        openai_job_lease_seconds=60,
        openai_job_max_age_seconds=86400,
        openai_job_max_queued=200,
    )


def _create_earnings_job(repository: AIJobRepository):
    version, digest = runtime.schema_identity("earnings_impact")
    return repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )


def _earnings_result():
    return {
        "ticker": "AAPL",
        "summary": "供应链与大型科技股可能出现联动。",
        "expectation": "关注营收、利润率与指引。",
        "impacted": [
            {
                "ticker": ticker,
                "name": name,
                "relation": relation,
                "direction": "mixed",
                "reason": "公开业务关系可能形成传导。",
            }
            for ticker, name, relation in [
                ("MSFT", "Microsoft", "competitor"),
                ("QCOM", "Qualcomm", "supplier"),
                ("TSM", "TSMC", "supplier"),
                ("XLK", "Technology ETF", "etf"),
            ]
        ],
    }


def _large_signal_result():
    evidence = "证" * 500
    twelve = [evidence for _ in range(12)]
    ten = [evidence for _ in range(10)]
    return {
        "asset": "AAPL",
        "horizon": "数日到数周",
        "dominant_regime": "趋势与波动交织",
        "trend_bias_confidence": 60,
        "top_risk_confidence": 40,
        "bottom_opportunity_confidence": 50,
        "dip_buy_quality": 55,
        "breakdown_risk": 35,
        "data_quality": 80,
        "final_bias": "range_consolidation",
        "top_evidence": twelve,
        "bottom_evidence": twelve,
        "dip_buy_evidence": twelve,
        "bearish_evidence": twelve,
        "contradictions": twelve,
        "options_flow_read": {
            "net_direction": "mixed",
            "confidence": 50,
            "bullish_flow_evidence": ten,
            "bearish_flow_evidence": ten,
            "unknown_or_neutral_flow": ten,
            "warnings": ten,
        },
        "key_levels": {
            "support": ten,
            "resistance": ten,
            "vwap_levels": ten,
            "options_levels": ten,
        },
        "confirmation_signals": twelve,
        "invalidation_signals": twelve,
        "event_risks": twelve,
        "data_quality_notes": twelve,
        "summary": "结" * 1200,
    }


def test_terra_runtime_defaults_are_explicit(monkeypatch):
    for name in (
        "OPENAI_MODEL",
        "OPENAI_REASONING",
        "OPENAI_TIMEOUT_SECONDS",
        "OPTION_PRO_AI_MAX_OUTPUT_TOKENS",
        "OPENAI_MAX_OUTPUT_TOKENS",
        "OPENAI_EXECUTION_MODE",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = Settings(_env_file=None)
    assert settings.openai_model == "gpt-5.6-terra"
    assert settings.openai_reasoning == "max"
    assert settings.openai_timeout_seconds == 900
    assert settings.openai_max_output_tokens == 32768
    assert settings.openai_execution_mode == "background"


def test_custom_provider_requires_explicit_capability_attestation(tmp_path):
    settings = _settings(tmp_path / "ai-jobs.db")
    settings.openai_base_url = "https://proxy.example/v1"
    settings.openai_custom_capabilities_confirmed = False
    status = runtime.capability_status(settings)
    assert status["supported"] is False
    assert status["reason"] == "custom_base_url_not_attested"


def test_job_dedupe_is_persistent_and_public_shape_hides_response_id(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, created = _create_earnings_job(repository)
    second, created_again = _create_earnings_job(repository)
    assert created is True
    assert created_again is False
    assert first["job_id"] == second["job_id"]
    public = repository.public(first)
    assert "openai_response_id" not in public
    assert public["model"] == "gpt-5.6-terra"
    assert public["reasoning"] == "max"


def test_job_identity_separates_execution_modes_and_migrates_legacy_hash(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    background, created = _create_earnings_job(repository)
    assert created is True

    version, digest = runtime.schema_identity("earnings_impact")
    worker_sync, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="worker_sync",
        prompt_version="earnings-impact-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    assert created is True
    assert worker_sync["job_id"] != background["job_id"]

    payload_json = repository._canonical_payload({"ticker": "AAPL", "name": "Apple"})
    legacy_hash = repository._request_hash_legacy(
        "earnings_impact",
        payload_json,
        "gpt-5.6-terra",
        "max",
        "earnings-impact-v2",
        version,
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET request_hash=? WHERE job_id=?",
            (legacy_hash, background["job_id"]),
        )
        connection.commit()
    migrated, created = _create_earnings_job(repository)
    assert created is False
    assert migrated["job_id"] == background["job_id"]
    assert migrated["request_hash"] != legacy_hash


def test_structured_result_rejects_fences_and_unknown_fields():
    valid = _earnings_result()
    fence = chr(96) * 3
    with pytest.raises(ValidationError):
        validate_result(
            "earnings_impact",
            fence + "json\n" + json.dumps(valid) + "\n" + fence,
            {"ticker": "AAPL"},
        )
    with pytest.raises(ValidationError):
        validate_result(
            "earnings_impact",
            json.dumps({**valid, "unexpected": True}),
            {"ticker": "AAPL"},
        )


def test_background_job_resumes_existing_response_without_resubmit(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-one"
    calls = {"submit": 0, "retrieve": 0}

    async def fake_submit(*_args, **_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(status="queued", id="resp_test")

    async def fake_retrieve(*_args, **_kwargs):
        calls["retrieve"] += 1
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=50,
            total_tokens=150,
            input_tokens_details=SimpleNamespace(cached_tokens=20),
            output_tokens_details=SimpleNamespace(reasoning_tokens=10),
        )
        return SimpleNamespace(
            status="completed",
            id="resp_test",
            output_text=json.dumps(_earnings_result()),
            usage=usage,
        )

    monkeypatch.setattr(runtime, "submit_background", fake_submit)
    monkeypatch.setattr(runtime, "retrieve", fake_retrieve)

    async def scenario():
        claimed = repository.claim_due(owner, 60)
        await process_job(repository, settings, claimed, owner)
        queued = repository.get_job(row["job_id"])
        assert queued["status"] == "queued"
        assert queued["openai_response_id"] == "resp_test"
        assert "openai_response_id" not in repository.public(queued)

        with repository._connect() as connection:
            connection.execute(
                "UPDATE ai_jobs SET next_attempt_at='1970-01-01T00:00:00Z' WHERE job_id=?",
                (row["job_id"],),
            )
            connection.commit()
        resumed = repository.claim_due(owner, 60)
        await process_job(repository, settings, resumed, owner)

    asyncio.run(scenario())

    completed = repository.get_job(row["job_id"])
    assert completed["status"] == "completed"
    assert calls == {"submit": 1, "retrieve": 1}
    assert repository.public(completed)["result"]["ticker"] == "AAPL"


def test_direct_background_completion_links_response_before_publish(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-direct-complete"

    async def fake_submit(*_args, **_kwargs):
        return SimpleNamespace(
            status="completed",
            id="resp_direct_complete",
            output_text=json.dumps(_earnings_result()),
            usage=None,
        )

    monkeypatch.setattr(runtime, "submit_background", fake_submit)
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    completed = repository.get_job(row["job_id"])
    assert completed["status"] == "completed"
    assert completed["openai_response_id"] == "resp_direct_complete"
    public = repository.public(completed)
    assert public["result"]["ticker"] == "AAPL"
    assert public["cached"] is False
    assert repository.public(completed, cached=True)["cached"] is True


def test_background_link_failure_is_not_retryable_as_a_known_submission(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-link-failure"
    submit_calls = 0

    async def fake_submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        return SimpleNamespace(status="queued", id="resp_not_persisted")

    def reject_link(*_args, **_kwargs):
        raise RuntimeError("ai_job_response_link_rejected")

    monkeypatch.setattr(runtime, "submit_background", fake_submit)
    monkeypatch.setattr(repository, "link_background_response", reject_link)
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(row["job_id"])
    assert failed["status"] == "failed"
    assert failed["openai_response_id"] is None
    assert failed["error_code"] == "submission_outcome_unknown"
    blocked, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-v2",
        schema_version=runtime.schema_identity("earnings_impact")[0],
        schema_sha256=runtime.schema_identity("earnings_impact")[1],
        max_queued=200,
        force_retry=True,
    )
    assert created is False
    assert submit_calls == 1
    assert blocked["status"] == "failed"
    assert blocked["error_code"] == "submission_outcome_unknown"


def test_pending_job_fails_safely_when_frozen_runtime_configuration_changed(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    settings.openai_reasoning = "high"
    calls = 0

    async def fake_submit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(status="queued", id="should_not_submit")

    monkeypatch.setattr(runtime, "submit_background", fake_submit)
    owner = "worker-config-drift"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    failed = repository.get_job(row["job_id"])
    assert calls == 0
    assert failed["status"] == "failed"
    assert failed["error_code"] == "runtime_configuration_changed"


def test_elapsed_poll_window_keeps_the_existing_background_response(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-poll-window"

    async def fake_retrieve(*_args, **_kwargs):
        return SimpleNamespace(status="in_progress", id="resp_existing")

    monkeypatch.setattr(runtime, "retrieve", fake_retrieve)
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_existing',
                submission_started_at='2020-01-01T00:00:00Z',
                submitted_at='2020-01-01T00:00:00Z',
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (row["job_id"],),
        )
        connection.commit()

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    deferred = repository.get_job(row["job_id"])
    assert deferred["status"] == "in_progress"
    assert deferred["openai_response_id"] == "resp_existing"
    assert deferred["error_code"] == "poll_window_elapsed"


def test_legacy_get_never_creates_paid_analysis(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    response = client.get("/api/ai/earnings-impact/AAPL")

    assert response.status_code == 409
    assert response.json()["status"] == "analysis_required"
    assert repository.health()["pending"] == 0


def test_job_post_is_fast_local_and_idempotent(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)

    app = FastAPI()
    app.include_router(ai.router)
    app.dependency_overrides[require_expensive_action] = lambda: None
    client = TestClient(app, base_url="http://localhost")
    body = {
        "ticker": "AAPL",
        "name": "Apple",
        "sector": "Technology",
        "earnings_date": "2026-07-30",
    }

    first = client.post("/api/ai/jobs/earnings-impact", json=body)
    second = client.post("/api/ai/jobs/earnings-impact", json=body)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["job_id"] == second.json()["job_id"]
    assert first.json()["status"] == "pending"
    assert second.json()["cached"] is False


def test_option_alert_failed_job_requires_explicit_force_to_requeue(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)

    app = FastAPI()
    app.include_router(ai.router)
    app.dependency_overrides[require_expensive_action] = lambda: None
    client = TestClient(app, base_url="http://localhost")
    body = {"ticker": "AAPL", "alerts": [], "underlying_price": 200}

    first = client.post("/api/ai/jobs/option-alerts", json=body)
    owner = "worker-option-retry"
    claimed = repository.claim_due(owner, 60)
    repository.fail(claimed["job_id"], owner, "worker_interrupted")

    reused = client.post("/api/ai/jobs/option-alerts", json=body)
    retried = client.post(
        "/api/ai/jobs/option-alerts",
        json={**body, "force": True},
    )

    assert first.status_code == 202
    assert reused.json()["status"] == "failed"
    assert retried.status_code == 202
    assert retried.json()["job_id"] == first.json()["job_id"]
    assert retried.json()["status"] == "pending"


def test_large_valid_signal_result_is_persisted_separately_from_request_limit(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("signal_analysis")
    payload = {
        "ticker": "AAPL",
        "signals": {},
        "scores": {},
        "as_of": "2026-07-12",
    }
    row, _ = repository.create_job(
        job_type="signal_analysis",
        payload=payload,
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="signal-analysis-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    result = validate_result(
        "signal_analysis",
        json.dumps(_large_signal_result(), ensure_ascii=False),
        payload,
    )
    assert len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > 64 * 1024

    owner = "worker-large-result"
    claimed = repository.claim_due(owner, 60)
    repository.complete(claimed["job_id"], owner, result, {})

    completed = repository.public(repository.get_job(row["job_id"]))
    assert completed["status"] == "completed"
    assert completed["result"]["asset"] == "AAPL"


def test_paid_job_creation_is_disabled_without_app_auth(monkeypatch, tmp_path):
    monkeypatch.delenv("APP_AUTH_TOKEN", raising=False)
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    response = client.post(
        "/api/ai/jobs/earnings-impact",
        json={"ticker": "AAPL"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "capability_disabled"
    assert repository.health()["pending"] == 0


def test_pending_cancel_is_idempotent(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    first = repository.request_cancel(row["job_id"])
    second = repository.request_cancel(row["job_id"])
    assert first["status"] == "cancelled"
    assert second["status"] == "cancelled"


def test_claimed_pending_cancel_cannot_be_resurrected_or_submitted(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-cancel-race"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None

    first = repository.request_cancel(row["job_id"])
    second = repository.request_cancel(row["job_id"])
    calls = 0

    async def forbidden_submit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("cancelled pending job reached the provider")

    monkeypatch.setattr(runtime, "submit_background", forbidden_submit)
    asyncio.run(process_job(repository, settings, claimed, owner))

    final = repository.get_job(row["job_id"])
    assert first["status"] == "cancelled"
    assert second["status"] == "cancelled"
    assert final["status"] == "cancelled"
    assert final["submission_started_at"] is None
    assert final["lease_owner"] is None
    assert calls == 0


def test_explicit_retry_requeues_safe_terminal_job_but_not_unknown_submission(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    owner = "worker-retry"
    claimed = repository.claim_due(owner, 60)
    repository.fail(row["job_id"], owner, "provider_failed")

    retried, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-v2",
        schema_version=runtime.schema_identity("earnings_impact")[0],
        schema_sha256=runtime.schema_identity("earnings_impact")[1],
        max_queued=200,
        force_retry=True,
    )
    assert created is True
    assert retried["job_id"] == row["job_id"]
    assert retried["status"] == "pending"

    claimed = repository.claim_due(owner, 60)
    repository.fail(row["job_id"], owner, "submission_outcome_unknown")
    blocked, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-v2",
        schema_version=runtime.schema_identity("earnings_impact")[0],
        schema_sha256=runtime.schema_identity("earnings_impact")[1],
        max_queued=200,
        force_retry=True,
    )
    assert created is False
    assert blocked["status"] == "failed"
    assert blocked["error_code"] == "submission_outcome_unknown"


def test_interrupted_worker_sync_job_can_be_explicitly_retried(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")
    row, _ = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="worker_sync",
        prompt_version="earnings-impact-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', submission_started_at=created_at,
                submitted_at=created_at, lease_owner=NULL,
                lease_expires_at='1970-01-01T00:00:00Z',
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (row["job_id"],),
        )
        connection.commit()

    owner = "worker-sync-recovery"
    claimed = repository.claim_due(owner, 60)
    settings = _settings(tmp_path / "ai-jobs.db")
    settings.openai_execution_mode = "worker_sync"
    asyncio.run(process_job(repository, settings, claimed, owner))
    interrupted = repository.get_job(row["job_id"])
    assert interrupted["status"] == "failed"
    assert interrupted["error_code"] == "worker_interrupted"

    retried, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="worker_sync",
        prompt_version="earnings-impact-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
        force_retry=True,
    )
    assert created is True
    assert retried["job_id"] == row["job_id"]
    assert retried["status"] == "pending"


def test_background_cancel_waits_for_provider_terminal_state(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-cancel"
    calls = 0

    async def fake_cancel(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            status="in_progress" if calls == 1 else "cancelled",
            id="resp_cancel",
        )

    monkeypatch.setattr(runtime, "cancel", fake_cancel)
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_cancel',
                submission_started_at=?, submitted_at=?,
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (row["created_at"], row["created_at"], row["job_id"]),
        )
        connection.commit()
    repository.request_cancel(row["job_id"])

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))
    waiting = repository.get_job(row["job_id"])
    assert waiting["status"] == "in_progress"
    assert waiting["cancel_requested_at"] is not None
    assert waiting["openai_response_id"] == "resp_cancel"

    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET next_attempt_at='1970-01-01T00:00:00Z' WHERE job_id=?",
            (row["job_id"],),
        )
        connection.commit()
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))
    assert repository.get_job(row["job_id"])["status"] == "cancelled"


def test_cancel_race_keeps_provider_completed_result(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-cancel-completed"

    async def fake_cancel(*_args, **_kwargs):
        return SimpleNamespace(
            status="completed",
            id="resp_cancel_completed",
            output_text=json.dumps(_earnings_result()),
            usage=None,
        )

    monkeypatch.setattr(runtime, "cancel", fake_cancel)
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_cancel_completed',
                submission_started_at=?, submitted_at=?,
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (row["created_at"], row["created_at"], row["job_id"]),
        )
        connection.commit()
    repository.request_cancel(row["job_id"])

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    completed = repository.get_job(row["job_id"])
    assert completed["status"] == "completed"
    assert repository.public(completed)["result"]["ticker"] == "AAPL"


def test_background_cancel_transport_error_is_deferred(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-cancel-error"

    async def fake_cancel(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(runtime, "cancel", fake_cancel)
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_cancel_error',
                submission_started_at=?, submitted_at=?,
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (row["created_at"], row["created_at"], row["job_id"]),
        )
        connection.commit()
    repository.request_cancel(row["job_id"])

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    deferred = repository.get_job(row["job_id"])
    assert deferred["status"] == "in_progress"
    assert deferred["error_code"] == "provider_cancel_deferred"
    assert deferred["openai_response_id"] == "resp_cancel_error"
