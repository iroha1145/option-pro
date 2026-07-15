from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import ai, signals
from app.services.ai_jobs import runtime
from app.services.ai_jobs import worker as ai_worker
from app.services.ai_jobs.repository import AIJobRepository


class _CopyableSettings(SimpleNamespace):
    def model_copy(self, *, update):
        values = dict(vars(self))
        values.update(update)
        return _CopyableSettings(**values)


def _settings(path) -> _CopyableSettings:
    return _CopyableSettings(
        openai_api_key=SecretStr("synthetic-test-key"),
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_timeout_seconds=900,
        openai_control_timeout_seconds=30,
        openai_max_retries=0,
        openai_max_concurrency=1,
        openai_daily_max_jobs=4,
        openai_daily_budget_usd=2.0,
        openai_manual_cooldown_seconds=0,
        openai_background_initial_poll_seconds=2,
        openai_background_max_poll_seconds=15,
        openai_background_poll_timeout_seconds=1800,
        openai_job_db_path=path,
        openai_job_lease_seconds=60,
        openai_job_max_age_seconds=86400,
        openai_job_max_queued=200,
    )


def _effective(*, manual: bool, scheduled: bool):
    return SimpleNamespace(
        ai=SimpleNamespace(
            daily_max_jobs=4,
            daily_budget_usd=2.0,
            manual_analysis_cooldown_seconds=0,
            manual_analysis_enabled=manual,
        ),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=scheduled),
    )


def _create_job(
    repository: AIJobRepository,
    *,
    ticker: str,
    source: str,
    priority: int = 50,
    force_retry: bool = False,
):
    version, digest = runtime.schema_identity("earnings_impact")
    return repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": ticker, "name": ticker},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="manual-switch-test-v1",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
        submission_source=source,
        priority=priority,
        force_retry=force_retry,
    )


def _seed_source_legacy_jobs(
    repository: AIJobRepository,
    specs: list[dict],
) -> list[dict]:
    repository.initialize()
    version, digest = runtime.schema_identity("earnings_impact")
    payload_json = repository._canonical_payload({"ticker": "AAPL", "name": "AAPL"})
    rows = []
    with repository._connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        for spec in specs:
            source = spec["source"]
            request_hash = repository._request_hash_source_legacy(
                "earnings_impact",
                payload_json,
                source,
                "gpt-5.6-terra",
                "max",
                "background",
                "manual-switch-test-v1",
                version,
                digest,
            )
            row = repository._insert_job(
                connection,
                job_type="earnings_impact",
                request_hash=request_hash,
                payload_json=payload_json,
                model="gpt-5.6-terra",
                reasoning="max",
                execution_mode="background",
                prompt_version="manual-switch-test-v1",
                schema_version=version,
                schema_sha256=digest,
                submission_source="manual",
                priority=50,
                now=spec["created_at"],
                retry_of_job_id=None,
                execution_number=spec.get("execution_number", 1),
            )
            connection.execute(
                """UPDATE ai_jobs SET status=?,error_code=?,
                          submission_started_at=?,submitted_at=?,
                          openai_response_id=?,completed_at=?,
                          budget_charge_microusd=?
                   WHERE job_id=?""",
                (
                    spec.get("status", "pending"),
                    spec.get("error_code"),
                    spec.get("submission_started_at"),
                    spec.get("submission_started_at"),
                    spec.get("openai_response_id"),
                    spec.get("completed_at"),
                    spec.get("budget_charge_microusd", 0),
                    row["job_id"],
                ),
            )
            rows.append(row)
        connection.execute(
            "DELETE FROM ai_job_schema WHERE version='ai-job-identities-v2'"
        )
        connection.commit()
    return rows


def _completed_earnings_result() -> dict:
    return {
        "output_language": "zh-CN",
        "ticker": "AAPL",
        "summary": "供应链与大型科技股可能出现联动。",
        "expectation": "关注营收、利润率与指引变化。",
        "impacted": [
            {
                "ticker": ticker,
                "name": name,
                "relation": relation,
                "direction": "mixed",
                "reason": "公开业务关系可能形成市场传导。",
            }
            for ticker, name, relation in (
                ("MSFT", "Microsoft", "competitor"),
                ("QCOM", "Qualcomm", "supplier"),
                ("TSM", "TSMC", "supplier"),
                ("XLK", "Technology ETF", "etf"),
            )
        ],
    }


def test_manual_routes_fail_closed_when_only_scheduled_analysis_is_enabled(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(
        ai,
        "get_effective_runtime_settings",
        lambda: _effective(manual=False, scheduled=True),
    )
    capability_checked = False

    def capability() -> None:
        nonlocal capability_checked
        capability_checked = True

    monkeypatch.setattr(ai, "_require_runtime_capability", capability)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    earnings = client.post(
        "/api/ai/jobs/earnings-impact",
        json={"ticker": "AAPL", "name": "Apple"},
    )
    options = client.post(
        "/api/ai/jobs/option-alerts",
        json={"ticker": "AAPL", "alerts": [], "underlying_price": 200},
    )

    for response in (earnings, options):
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "manual_analysis_disabled",
            "message": "手动分析已关闭",
        }
    assert capability_checked is False
    assert repository.health()["pending"] == 0


def test_manual_routes_do_not_enqueue_when_personal_mode_is_read_only(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(
        ai,
        "get_personal_config",
        lambda: SimpleNamespace(catalyst_manual_enabled=False),
    )
    monkeypatch.setattr(
        ai,
        "get_effective_runtime_settings",
        lambda: _effective(manual=True, scheduled=True),
    )
    capability_checked = False

    def capability() -> None:
        nonlocal capability_checked
        capability_checked = True

    monkeypatch.setattr(ai, "_require_runtime_capability", capability)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    responses = (
        client.post(
            "/api/ai/jobs/earnings-impact",
            json={"ticker": "AAPL", "name": "Apple"},
        ),
        client.post(
            "/api/ai/jobs/option-alerts",
            json={"ticker": "AAPL", "alerts": [], "underlying_price": 200},
        ),
    )

    for response in responses:
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "read_only_mode",
            "message": "当前为只读模式",
        }
    assert capability_checked is False
    assert repository.health()["pending"] == 0


def test_signal_analysis_checks_manual_switch_before_building_evidence(monkeypatch):
    monkeypatch.setattr(
        ai,
        "get_effective_runtime_settings",
        lambda: _effective(manual=False, scheduled=True),
    )
    provider_called = False

    def signals_provider(_ticker: str):
        nonlocal provider_called
        provider_called = True
        return {}

    monkeypatch.setattr(signals, "compute_stock_signals", signals_provider)

    with pytest.raises(HTTPException) as captured:
        asyncio.run(
            signals.stock_ai_analysis(
                "AAPL",
                signals.SignalAnalysisJobCreateRequest(),
            )
        )

    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == "manual_analysis_disabled"
    assert provider_called is False


@pytest.mark.parametrize(
    ("first_source", "second_source"),
    (("manual", "scheduled"), ("scheduled", "manual")),
)
def test_repository_dedupes_the_same_input_across_sources_and_keeps_first_source(
    tmp_path,
    first_source,
    second_source,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, first_created = _create_job(
        repository,
        ticker="AAPL",
        source=first_source,
    )
    reused, reused_created = _create_job(
        repository,
        ticker="AAPL",
        source=second_source,
    )
    different, different_created = _create_job(
        repository,
        ticker="MSFT",
        source=second_source,
    )

    assert first_created is True
    assert reused_created is False
    assert reused["job_id"] == first["job_id"]
    assert reused["request_hash"] == first["request_hash"]
    assert repository.get_job(first["job_id"])["submission_source"] == first_source
    assert repository.public(reused)["submission_source"] == first_source
    assert different_created is True
    assert different["job_id"] != first["job_id"]
    assert different["submission_source"] == second_source


def test_running_job_is_reused_across_sources_without_a_second_paid_job(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, _ = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
    )
    owner = "cross-source-running-worker"
    assert repository.claim_due(owner, 60)["job_id"] == first["job_id"]
    assert repository.mark_submission_started(
        first["job_id"],
        owner,
        daily_limit=4,
        daily_budget_usd=2.0,
    ) == "started"

    reused, created = _create_job(
        repository,
        ticker="AAPL",
        source="scheduled",
    )

    assert created is False
    assert reused["job_id"] == first["job_id"]
    assert reused["status"] == "in_progress"
    assert reused["submission_source"] == "manual"
    assert repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=2.0,
    )["submitted_jobs"] == 1


def test_completed_result_is_reused_across_sources_with_its_original_source(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, _ = _create_job(
        repository,
        ticker="AAPL",
        source="scheduled",
    )
    owner = "cross-source-completed-worker"
    assert repository.claim_due(owner, 60)["job_id"] == first["job_id"]
    assert repository.mark_submission_started(
        first["job_id"],
        owner,
        daily_limit=4,
        daily_budget_usd=2.0,
    ) == "started"
    completed_result = _completed_earnings_result()
    repository.complete(
        first["job_id"],
        owner,
        completed_result,
        {
            "input_tokens": 10,
            "cached_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_tokens": 0,
            "total_tokens": 15,
        },
    )

    reused, created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
    )

    assert created is False
    assert reused["job_id"] == first["job_id"]
    assert reused["status"] == "completed"
    assert reused["submission_source"] == "scheduled"
    public = repository.public(reused, cached=True)
    assert public["cached"] is True
    assert public["result"] == completed_result
    assert public["submission_source"] == "scheduled"
    assert repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=2.0,
    )["submitted_jobs"] == 1


@pytest.mark.parametrize(
    ("first_source", "other_source"),
    (("manual", "scheduled"), ("scheduled", "manual")),
)
def test_force_retry_creates_only_one_cross_source_retry_after_a_safe_failure(
    tmp_path,
    first_source,
    other_source,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, _ = _create_job(
        repository,
        ticker="AAPL",
        source=first_source,
    )
    owner = f"{first_source}-failed-worker"
    assert repository.claim_due(owner, 60)["job_id"] == first["job_id"]
    repository.fail(first["job_id"], owner, "provider_failed")

    retried, retry_created = _create_job(
        repository,
        ticker="AAPL",
        source=other_source,
        force_retry=True,
    )
    repeated, repeated_created = _create_job(
        repository,
        ticker="AAPL",
        source=other_source,
        force_retry=True,
    )

    assert retry_created is True
    assert retried["job_id"] != first["job_id"]
    assert retried["retry_of_job_id"] == first["job_id"]
    assert retried["execution_number"] == 2
    assert retried["submission_source"] == other_source
    assert repository.get_job(first["job_id"])["submission_source"] == first_source
    assert repeated_created is False
    assert repeated["job_id"] == retried["job_id"]
    assert repeated["submission_source"] == other_source


def test_identity_v2_restores_sources_and_seals_duplicate_unpaid_jobs(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {"source": "manual", "created_at": "2026-07-15T00:00:00Z"},
            {"source": "scheduled", "created_at": "2026-07-15T00:00:01Z"},
        ],
    )

    repository.initialize()
    manual = repository.get_job(seeded[0]["job_id"])
    scheduled = repository.get_job(seeded[1]["job_id"])

    assert manual["submission_source"] == "manual"
    assert scheduled["submission_source"] == "scheduled"
    assert manual["status"] == "pending"
    assert scheduled["status"] == "cancelled"
    assert scheduled["error_code"] == "duplicate_request_migrated"
    owner = "identity-v2-worker"
    assert repository.claim_due(owner, 60)["job_id"] == manual["job_id"]
    assert repository.mark_submission_started(
        manual["job_id"], owner, daily_limit=4, daily_budget_usd=2.0
    ) == "started"
    repository.fail(manual["job_id"], owner, "provider_failed")
    assert repository.claim_due(owner, 60) is None


@pytest.mark.parametrize(
    "settled_spec",
    [
        {
            "status": "completed",
            "submission_started_at": "2026-07-15T00:00:00Z",
            "openai_response_id": "resp_completed_before_pending",
            "completed_at": "2026-07-15T00:00:01Z",
            "budget_charge_microusd": 1_000,
        },
        {
            "status": "in_progress",
            "submission_started_at": "2026-07-15T00:00:00Z",
            "openai_response_id": "resp_submitted_before_pending",
            "budget_charge_microusd": 709_120,
        },
    ],
)
def test_identity_v2_seals_newer_unpaid_job_after_paid_or_settled_work(
    tmp_path,
    settled_spec,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:00Z",
                "execution_number": 1,
                **settled_spec,
            },
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:02Z",
                "execution_number": 2,
            },
        ],
    )

    repository.initialize()
    settled = repository.get_job(seeded[0]["job_id"])
    duplicate = repository.get_job(seeded[1]["job_id"])

    assert settled["status"] == settled_spec["status"]
    assert settled["submission_started_at"] is not None
    assert duplicate["status"] == "cancelled"
    assert duplicate["error_code"] == "duplicate_request_migrated"


def test_completed_legacy_duplicate_blocks_retry_of_older_failure(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:00Z",
                "status": "failed",
                "error_code": "provider_failed",
                "completed_at": "2026-07-15T00:00:02Z",
            },
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:01Z",
                "status": "completed",
                "submission_started_at": "2026-07-15T00:00:01Z",
                "openai_response_id": "resp_completed_legacy",
                "completed_at": "2026-07-15T00:00:03Z",
                "budget_charge_microusd": 1_000,
            },
        ],
    )
    repository.initialize()

    reused, created = _create_job(repository, ticker="AAPL", source="manual")
    retried, retry_created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        force_retry=True,
    )

    assert created is False
    assert reused["job_id"] == seeded[1]["job_id"]
    assert reused["status"] == "completed"
    assert retry_created is False
    assert retried["job_id"] == seeded[1]["job_id"]
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2


def test_completed_history_blocks_retry_of_newer_legacy_failure(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:00Z",
                "execution_number": 1,
                "status": "completed",
                "submission_started_at": "2026-07-15T00:00:00Z",
                "openai_response_id": "resp_completed_history",
                "completed_at": "2026-07-15T00:00:01Z",
                "budget_charge_microusd": 1_000,
            },
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:02Z",
                "execution_number": 2,
                "status": "failed",
                "error_code": "provider_failed",
                "completed_at": "2026-07-15T00:00:03Z",
            },
        ],
    )
    repository.initialize()

    blocked, created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        force_retry=True,
    )

    assert created is False
    assert blocked["job_id"] == seeded[1]["job_id"]
    assert blocked["execution_number"] == 2
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2


def test_safe_submitted_failure_retries_after_newer_pending_is_migrated(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:00Z",
                "execution_number": 1,
                "status": "failed",
                "error_code": "provider_failed",
                "submission_started_at": "2026-07-15T00:00:00Z",
                "openai_response_id": "resp_safe_failed_history",
                "completed_at": "2026-07-15T00:00:01Z",
                "budget_charge_microusd": 1_000,
            },
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:02Z",
                "execution_number": 2,
            },
        ],
    )
    repository.initialize()
    migrated = repository.get_job(seeded[1]["job_id"])

    retried, created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        force_retry=True,
    )

    assert migrated["status"] == "cancelled"
    assert migrated["error_code"] == "duplicate_request_migrated"
    assert created is True
    assert retried["execution_number"] == 3
    assert retried["retry_of_job_id"] == seeded[0]["job_id"]


def test_unknown_legacy_execution_blocks_retry_of_newer_safe_failure(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:00Z",
                "execution_number": 1,
                "status": "failed",
                "error_code": "submission_outcome_unknown",
                "submission_started_at": "2026-07-15T00:00:00Z",
                "completed_at": "2026-07-15T00:00:01Z",
                "budget_charge_microusd": 709_120,
            },
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:02Z",
                "execution_number": 2,
                "status": "failed",
                "error_code": "provider_failed",
                "completed_at": "2026-07-15T00:00:03Z",
            },
        ],
    )
    repository.initialize()

    blocked, created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        force_retry=True,
    )

    assert created is False
    assert blocked["job_id"] == seeded[1]["job_id"]
    assert blocked["execution_number"] == 2
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2


def test_legacy_retry_uses_global_execution_number(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seeded = _seed_source_legacy_jobs(
        repository,
        [
            {
                "source": "manual",
                "created_at": "2026-07-15T00:00:00Z",
                "execution_number": 1,
                "status": "failed",
                "error_code": "provider_failed",
                "completed_at": "2026-07-15T00:00:01Z",
            },
            {
                "source": "scheduled",
                "created_at": "2026-07-15T00:00:02Z",
                "execution_number": 3,
                "status": "failed",
                "error_code": "provider_failed",
                "completed_at": "2026-07-15T00:00:03Z",
            },
        ],
    )
    repository.initialize()

    retried, created = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        force_retry=True,
    )

    assert created is True
    assert retried["execution_number"] == 4
    assert retried["retry_of_job_id"] == seeded[1]["job_id"]


def test_scheduled_switch_never_releases_a_manual_queue_entry(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    manual, _ = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        priority=80,
    )
    settings = _settings(repository.path)
    effective = _effective(manual=False, scheduled=True)
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: effective,
    )
    provider_calls = 0

    async def forbidden_submission(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider submission must not run")

    monkeypatch.setattr(runtime, "submit_background", forbidden_submission)

    processed, state = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "manual-off-worker")
    )
    blocked = repository.get_job(manual["job_id"])

    assert (processed, state) == (1, "analysis_disabled")
    assert blocked["status"] == "failed"
    assert blocked["error_code"] == "manual_analysis_disabled"
    assert blocked["submission_started_at"] is None
    assert provider_calls == 0

    effective.ai.manual_analysis_enabled = True
    assert asyncio.run(
        ai_worker.run_configured_once(repository, settings, "manual-restored-worker")
    ) == (0, "enabled")
    assert provider_calls == 0


def test_both_switches_terminally_block_each_source_and_recovery_stays_empty(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    manual, _ = _create_job(
        repository,
        ticker="AAPL",
        source="manual",
        priority=80,
    )
    scheduled, _ = _create_job(
        repository,
        ticker="MSFT",
        source="scheduled",
        priority=70,
    )
    settings = _settings(repository.path)
    effective = _effective(manual=False, scheduled=False)
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: effective,
    )
    provider_calls = 0

    async def forbidden_submission(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider submission must not run")

    monkeypatch.setattr(runtime, "submit_background", forbidden_submission)

    first = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "all-off-worker")
    )
    second = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "all-off-worker")
    )

    assert first == (1, "analysis_disabled")
    assert second == (1, "analysis_disabled")
    assert repository.get_job(manual["job_id"])["error_code"] == (
        "manual_analysis_disabled"
    )
    assert repository.get_job(scheduled["job_id"])["error_code"] == (
        "scheduled_analysis_disabled"
    )
    effective.ai.manual_analysis_enabled = True
    effective.catalyst.scheduled_analysis_enabled = True
    assert asyncio.run(
        ai_worker.run_configured_once(repository, settings, "all-restored-worker")
    ) == (0, "enabled")
    assert provider_calls == 0


def test_scheduled_source_passes_its_own_switch_without_provider_submission(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    scheduled, _ = _create_job(
        repository,
        ticker="MSFT",
        source="scheduled",
    )
    settings = _settings(repository.path)
    prepared = 0
    provider_calls = 0

    def prepare(*_args, **_kwargs):
        nonlocal prepared
        prepared += 1
        return {"synthetic": True}

    def stop_before_submission(job_id, owner, **_kwargs):
        repository.defer(
            job_id,
            owner,
            delay_seconds=60,
            error_code="synthetic_preflight_stop",
        )
        return "concurrency_limit"

    async def forbidden_submission(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("provider submission must not run")

    monkeypatch.setattr(runtime, "prepare_background", prepare)
    monkeypatch.setattr(repository, "mark_submission_started", stop_before_submission)
    monkeypatch.setattr(runtime, "submit_background", forbidden_submission)
    claimed = repository.claim_due("scheduled-worker", 60)

    asyncio.run(
        ai_worker.process_job(
            repository,
            settings,
            claimed,
            "scheduled-worker",
            manual_analysis_enabled=False,
            scheduled_analysis_enabled=True,
        )
    )

    row = repository.get_job(scheduled["job_id"])
    assert prepared == 1
    assert provider_calls == 0
    assert row["status"] == "pending"
    assert row["error_code"] == "synthetic_preflight_stop"
