from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.api import ai
from app.config import Settings
from app.services.ai_jobs import runtime, worker as ai_worker
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import AIJobRepository
from app.services.ai_jobs.worker import health_payload, process_job
from app.tools import recover_ai_schema_results as recovery_tool


def _settings(path):
    return SimpleNamespace(
        openai_api_key=SecretStr("test-key"),
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_timeout_seconds=900,
        openai_control_timeout_seconds=30,
        openai_max_retries=0,
        openai_max_output_tokens=16384,
        openai_max_concurrency=1,
        openai_background_initial_poll_seconds=2,
        openai_background_max_poll_seconds=15,
        openai_background_poll_timeout_seconds=1800,
        openai_job_db_path=path,
        openai_job_lease_seconds=60,
        openai_job_max_age_seconds=86400,
        openai_job_max_queued=200,
        openai_daily_max_jobs=4,
        openai_daily_budget_usd=2.0,
        openai_manual_cooldown_seconds=0,
    )


def _create_earnings_job(
    repository: AIJobRepository,
    *,
    submission_source: str = "manual",
    force_retry: bool = False,
):
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
        submission_source=submission_source,
        force_retry=force_retry,
    )


def _create_budget_job(
    repository: AIJobRepository,
    job_type: str,
    suffix: str,
):
    payloads = {
        "earnings_impact": {"ticker": suffix, "name": "Budget test"},
        "option_alerts": {
            "ticker": suffix,
            "alerts": [],
            "underlying_price": 100,
        },
        "signal_analysis": {
            "ticker": suffix,
            "signals": {},
            "scores": {},
            "as_of": "2026-07-16T00:00:00Z",
        },
        "news_impact": {
            "news_id": int(suffix[-1], 36) + 1,
            "change_sequence": 1,
            "content_hash": suffix.lower() * 8,
            "allowed_tickers": [suffix],
        },
        "market_focus": {
            "cycle_id": f"cycle-{suffix}",
            "as_of": "2026-07-16T00:00:00Z",
            "input_hash": suffix.lower() * 8,
            "allowed_event_group_ids": [],
            "allowed_tickers": [suffix],
        },
    }
    version, digest = runtime.schema_identity(job_type)
    return repository.create_job(
        job_type=job_type,
        payload=payloads[job_type],
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version=f"budget-{job_type}-{suffix}",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
    )


def _earnings_result():
    return {
        "output_language": "zh-CN",
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
                ("MSFT", "微软", "competitor"),
                ("QCOM", "高通", "supplier"),
                ("TSM", "台积电", "supplier"),
                ("XLK", "科技类交易所交易基金", "etf"),
            ]
        ],
    }


@pytest.mark.parametrize(
    "name",
    ["Microsoft公司", "微软（Microsoft）"],
)
def test_earnings_company_names_accept_registered_aliases_in_chinese(name):
    result = _earnings_result()
    result["impacted"][0]["name"] = name
    validated = validate_result(
        "earnings_impact",
        json.dumps(result, ensure_ascii=False),
        {"ticker": "AAPL"},
    )

    assert validated["impacted"][0]["name"] == name


def test_earnings_company_names_reject_english_prose():
    result = _earnings_result()
    result["impacted"][0]["name"] = "Markets rally after strong earnings"

    with pytest.raises(ValidationError, match="company_registered_name"):
        validate_result(
            "earnings_impact",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AAPL"},
        )


def test_earnings_reason_can_reference_its_structured_impacted_ticker():
    result = _earnings_result()
    result["impacted"][0]["reason"] = (
        "MSFT作为竞争对手，其产品进展可能影响行业定价。"
    )

    validated = validate_result(
        "earnings_impact",
        json.dumps(result, ensure_ascii=False),
        {"ticker": "AAPL"},
    )

    assert validated["impacted"][0]["reason"].startswith("MSFT作为")


def test_earnings_reason_rejects_unbound_uppercase_word():
    result = _earnings_result()
    result["impacted"][0]["reason"] = (
        "PANIC作为竞争对手，其产品进展可能影响行业定价。"
    )

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "earnings_impact",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AAPL"},
        )


def test_earnings_reason_cannot_borrow_another_impacted_ticker():
    result = _earnings_result()
    result["impacted"][0]["reason"] = (
        "QCOM作为供应商，其产品进展可能影响行业定价。"
    )

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "earnings_impact",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AAPL"},
        )


@pytest.mark.parametrize(
    "reason",
    [
        "受EIA库存数据影响，相关业务预期可能出现变化。",
        "FOMC决议可能改变融资成本与市场风险偏好。",
        "GDP数据变化可能影响行业需求预期。",
    ],
)
def test_earnings_reason_accepts_contextual_economic_initialisms(reason):
    result = _earnings_result()
    result["impacted"][0]["reason"] = reason

    validated = validate_result(
        "earnings_impact",
        json.dumps(result, ensure_ascii=False),
        {"ticker": "AAPL"},
    )

    assert validated["impacted"][0]["reason"] == reason


def test_earnings_tickers_cannot_form_an_english_sentence():
    result = _earnings_result()
    result["impacted"].append({**result["impacted"][0]})
    fake_tickers = ("MARKETS", "RALLY", "AFTER", "STRONG", "EARNINGS")
    for item, ticker in zip(result["impacted"], fake_tickers, strict=True):
        item["ticker"] = ticker
    result["impacted"][0]["reason"] = (
        "MARKETS RALLY AFTER STRONG EARNINGS，行业表现需要继续观察。"
    )

    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "earnings_impact",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "AAPL"},
        )


def _large_signal_result():
    evidence = "证" * 500
    twelve = [evidence for _ in range(12)]
    ten = [evidence for _ in range(10)]
    return {
        "output_language": "zh-CN",
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


def test_ai_job_heartbeat_survives_a_saturated_default_executor(monkeypatch):
    monkeypatch.setattr(
        ai_worker,
        "_MIN_LEASE_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="saturated-ai-default",
            )
        )
        blocker_started = threading.Event()
        blocker_release = threading.Event()

        def occupy_default_executor() -> None:
            blocker_started.set()
            blocker_release.wait()

        blocked = loop.run_in_executor(None, occupy_default_executor)
        for _ in range(100):
            if blocker_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert blocker_started.is_set()

        class ImmediateHeartbeatStop:
            def __init__(self) -> None:
                self.stopped = False

            def is_set(self) -> bool:
                return self.stopped

            async def wait(self) -> None:
                raise asyncio.TimeoutError

        stop = ImmediateHeartbeatStop()
        renewed_on: list[str] = []

        class Repository:
            def renew_lease(
                self,
                job_id: str,
                owner: str,
                lease_seconds: float,
            ) -> bool:
                assert (job_id, owner, lease_seconds) == ("job-1", "owner-1", 0.06)
                renewed_on.append(threading.current_thread().name)
                stop.stopped = True
                return True

        try:
            await asyncio.wait_for(
                ai_worker._lease_heartbeat(
                    Repository(),  # type: ignore[arg-type]
                    "job-1",
                    "owner-1",
                    0.06,  # type: ignore[arg-type]
                    stop,  # type: ignore[arg-type]
                ),
                timeout=1,
            )
        finally:
            blocker_release.set()
            await blocked

        assert renewed_on
        assert all(
            thread_name.startswith("ai-job-heartbeat")
            for thread_name in renewed_on
        )

    asyncio.run(scenario())


def test_ai_job_heartbeat_survives_a_blocked_event_loop(monkeypatch):
    monkeypatch.setattr(
        ai_worker,
        "_MIN_LEASE_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )

    async def scenario() -> None:
        stop = asyncio.Event()
        renewed_on: list[str] = []

        class Repository:
            def renew_lease(
                self,
                job_id: str,
                owner: str,
                lease_seconds: float,
            ) -> bool:
                assert (job_id, owner, lease_seconds) == ("job-1", "owner-1", 0.06)
                renewed_on.append(threading.current_thread().name)
                return True

        heartbeat = asyncio.create_task(
            ai_worker._lease_heartbeat(
                Repository(),  # type: ignore[arg-type]
                "job-1",
                "owner-1",
                0.06,  # type: ignore[arg-type]
                stop,
                maximum_loop_stall_seconds=0.3,
            )
        )
        for _ in range(100):
            if renewed_on:
                break
            await asyncio.sleep(0.01)
        assert renewed_on

        threading.Event().wait(0.2)
        stop.set()
        await asyncio.wait_for(heartbeat, timeout=1)

        assert len(renewed_on) >= 5
        assert all(name.startswith("ai-job-heartbeat") for name in renewed_on)

    asyncio.run(scenario())


def test_ai_job_heartbeat_start_failure_blocks_provider_submission(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    row, created = _create_earnings_job(repository)
    assert created is True
    owner = "heartbeat-start-failure"
    job = repository.claim_due(owner, lease_seconds=60)
    assert job is not None and job["job_id"] == row["job_id"]

    original_start = threading.Thread.start

    def fail_heartbeat_start(thread):
        if thread.name == "ai-job-heartbeat":
            raise RuntimeError("simulated heartbeat thread failure")
        return original_start(thread)

    def provider_must_not_start(*_args, **_kwargs):
        raise AssertionError("provider preparation ran without a heartbeat")

    monkeypatch.setattr(threading.Thread, "start", fail_heartbeat_start)
    monkeypatch.setattr(ai_worker.runtime, "prepare_background", provider_must_not_start)

    asyncio.run(process_job(repository, _settings(repository.path), job, owner))

    stored = repository.get_job(row["job_id"])
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error_code"] == "ai_job_heartbeat_unavailable"


def test_ai_job_heartbeat_stops_without_blocking_the_event_loop_during_renewal(
    monkeypatch,
):
    async def scenario() -> None:
        renewal_started = threading.Event()
        renewal_release = threading.Event()
        stop_holder: list[asyncio.Event] = []
        cancellation_observed = False

        async def heartbeat_during_renewal(
            repository,
            job_id,
            owner,
            lease_seconds,
            stop,
            started=None,
            maximum_loop_stall_seconds=None,
            lease_lost=None,
        ) -> None:
            nonlocal cancellation_observed
            assert (job_id, owner, lease_seconds) == ("job-1", "owner-1", 60)
            assert maximum_loop_stall_seconds == (
                ai_worker.BREAKOUT_TASK_TIMEOUT_SECONDS + lease_seconds
            )
            stop_holder.append(stop)
            if started is not None:
                started.set()
            loop = asyncio.get_running_loop()

            def renew_lease() -> None:
                renewal_started.set()
                renewal_release.wait(timeout=0.5)

            with ThreadPoolExecutor(max_workers=1) as executor:
                try:
                    await loop.run_in_executor(executor, renew_lease)
                except asyncio.CancelledError:
                    cancellation_observed = True
                    raise

        async def cancel_after_renewal_starts(settings, response_id):
            assert response_id == "response-1"
            while not renewal_started.is_set():
                await asyncio.sleep(0)
            return object()

        async def finish_response(*args, **kwargs) -> None:
            return None

        monkeypatch.setattr(ai_worker, "_lease_heartbeat", heartbeat_during_renewal)
        monkeypatch.setattr(ai_worker.runtime, "cancel", cancel_after_renewal_starts)
        monkeypatch.setattr(ai_worker, "_finish_response", finish_response)

        process = asyncio.create_task(
            ai_worker.process_job(
                object(),  # type: ignore[arg-type]
                SimpleNamespace(
                    openai_job_lease_seconds=60,
                    openai_job_max_age_seconds=900,
                ),
                {
                    "job_id": "job-1",
                    "cancel_requested_at": "2026-07-18T00:00:00Z",
                    "openai_response_id": "response-1",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                },
                "owner-1",
            )
        )

        while not stop_holder or not stop_holder[0].is_set():
            await asyncio.sleep(0)
        renewal_release.set()
        await asyncio.wait_for(process, timeout=1)
        assert not cancellation_observed

    asyncio.run(scenario())


def test_ai_job_midflight_lease_loss_cancels_unknown_submission(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(
        ai_worker,
        "_MIN_LEASE_HEARTBEAT_INTERVAL_SECONDS",
        0.01,
    )
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    row, created = _create_earnings_job(repository)
    assert created is True
    owner = "midflight-lease-loss"
    job = repository.claim_due(owner, lease_seconds=60)
    assert job is not None and job["job_id"] == row["job_id"]
    provider_started = threading.Event()
    provider_cancelled = asyncio.Event()

    def renew_lease(*_args, **_kwargs):
        return not provider_started.is_set()

    async def submit_background(*_args, **_kwargs):
        provider_started.set()
        try:
            await asyncio.Future()
        finally:
            provider_cancelled.set()

    monkeypatch.setattr(repository, "renew_lease", renew_lease)
    monkeypatch.setattr(runtime, "runtime_configuration_valid", lambda _settings: True)
    monkeypatch.setattr(runtime, "prepare_background", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(runtime, "submit_background", submit_background)
    settings = _settings(repository.path)
    settings.openai_job_lease_seconds = 1

    async def scenario():
        await asyncio.wait_for(
            process_job(repository, settings, job, owner),
            timeout=2,
        )
        await asyncio.wait_for(provider_cancelled.wait(), timeout=1)

    asyncio.run(scenario())

    stored = repository.get_job(row["job_id"])
    assert stored is not None
    assert stored["status"] == "failed"
    assert stored["error_code"] == "submission_outcome_unknown"


def test_existing_ai_response_is_deferred_when_heartbeat_cannot_start(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    row, created = _create_earnings_job(repository)
    assert created is True
    setup_owner = "heartbeat-existing-setup"
    claimed = repository.claim_due(setup_owner, lease_seconds=60)
    assert claimed is not None
    assert repository.mark_submission_started(
        row["job_id"],
        setup_owner,
        daily_limit=4,
    ) == "started"
    repository.link_background_response(
        row["job_id"],
        setup_owner,
        "resp_heartbeat_existing",
    )
    repository.record_background_response(
        row["job_id"],
        setup_owner,
        "resp_heartbeat_existing",
        "queued",
        delay_seconds=1,
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET next_attempt_at='1970-01-01T00:00:00Z' "
            "WHERE job_id=?",
            (row["job_id"],),
        )
        connection.commit()
    owner = "heartbeat-existing-owner"
    job = repository.claim_due(owner, lease_seconds=60)
    assert job is not None

    original_start = threading.Thread.start

    def fail_heartbeat_start(thread):
        if thread.name == "ai-job-heartbeat":
            raise RuntimeError("simulated heartbeat thread failure")
        return original_start(thread)

    async def retrieve_must_not_run(*_args, **_kwargs):
        raise AssertionError("provider polling ran without a heartbeat")

    monkeypatch.setattr(threading.Thread, "start", fail_heartbeat_start)
    monkeypatch.setattr(runtime, "retrieve", retrieve_must_not_run)

    asyncio.run(process_job(repository, _settings(repository.path), job, owner))

    stored = repository.get_job(row["job_id"])
    assert stored is not None
    assert stored["status"] in {"queued", "in_progress"}
    assert stored["openai_response_id"] == "resp_heartbeat_existing"
    assert stored["error_code"] == "ai_job_heartbeat_unavailable"
    assert stored["lease_owner"] is None


def test_standalone_worker_reads_fresh_runtime_controls_each_iteration(
    tmp_path,
    monkeypatch,
):
    class CopyableSettings(SimpleNamespace):
        def model_copy(self, *, update):
            values = dict(vars(self))
            values.update(update)
            return CopyableSettings(**values)

    settings = CopyableSettings(**vars(_settings(tmp_path / "ai-jobs.db")))
    effective = SimpleNamespace(
        ai=SimpleNamespace(
            daily_max_jobs=3,
            daily_budget_usd=1.25,
            daily_token_limit=9_000_000,
            manual_analysis_cooldown_seconds=45,
            manual_analysis_enabled=True,
        ),
        catalyst=SimpleNamespace(scheduled_analysis_enabled=False),
    )
    seen = []

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: effective,
    )

    async def capture(
        _repository,
        worker_settings,
        _owner,
        *,
        allow_new_submissions,
        new_submission_block_reason,
        manual_analysis_enabled,
        scheduled_analysis_enabled,
    ):
        seen.append(
            (
                worker_settings.openai_daily_max_jobs,
                worker_settings.openai_daily_budget_usd,
                worker_settings.openai_daily_token_limit,
                worker_settings.openai_manual_cooldown_seconds,
                allow_new_submissions,
                new_submission_block_reason,
                manual_analysis_enabled,
                scheduled_analysis_enabled,
            )
        )
        return 0

    monkeypatch.setattr(ai_worker, "run_once", capture)
    repository = AIJobRepository(settings.openai_job_db_path)

    first = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "standalone")
    )
    effective.ai.daily_budget_usd = 1.0
    effective.ai.daily_token_limit = 8_000_000
    second = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "standalone")
    )
    effective.ai.manual_analysis_enabled = False
    third = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "standalone")
    )

    assert first == (0, "enabled")
    assert second == (0, "enabled")
    assert third == (0, "analysis_disabled")
    assert seen == [
        (3, 1.25, 9_000_000, 45, True, "analysis_disabled", True, False),
        (3, 1.0, 8_000_000, 45, True, "analysis_disabled", True, False),
        (3, 1.0, 8_000_000, 45, True, "analysis_disabled", False, False),
    ]


def test_runtime_settings_failure_defers_unsubmitted_job_without_provider_call(
    tmp_path,
    monkeypatch,
):
    from app.services.runtime_settings import RuntimeSettingsStorageError

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    settings = _settings(repository.path)
    submit_calls = 0

    def unreadable():
        raise RuntimeSettingsStorageError("invalid runtime document")

    async def unexpected_submit(*_args, **_kwargs):
        nonlocal submit_calls
        submit_calls += 1
        raise AssertionError("provider submission must stay closed")

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        unreadable,
    )
    monkeypatch.setattr(runtime, "submit_background", unexpected_submit)

    result = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "fail-closed")
    )

    stored = repository.get_job(job["job_id"])
    assert result == (1, "runtime_settings_unavailable")
    assert submit_calls == 0
    assert stored["status"] == "pending"
    assert stored["submission_started_at"] is None
    assert stored["openai_response_id"] is None
    assert stored["lease_owner"] is None
    assert stored["error_code"] == "runtime_settings_unavailable"


@pytest.mark.parametrize("cancel_requested", [False, True])
def test_runtime_settings_failure_finishes_submitted_and_cancelled_jobs(
    tmp_path,
    monkeypatch,
    cancel_requested,
):
    from app.services.runtime_settings import RuntimeSettingsStorageError

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    settings = _settings(repository.path)
    setup_owner = "setup-owner"
    claimed = repository.claim_due(setup_owner, 60)
    assert claimed is not None
    assert repository.mark_submission_started(
        job["job_id"],
        setup_owner,
        daily_limit=4,
    ) == "started"
    repository.link_background_response(
        job["job_id"],
        setup_owner,
        "resp_existing",
    )
    repository.record_background_response(
        job["job_id"],
        setup_owner,
        "resp_existing",
        "queued",
        delay_seconds=1,
    )
    if cancel_requested:
        repository.request_cancel(job["job_id"])
    else:
        with repository._connect() as connection:
            connection.execute(
                "UPDATE ai_jobs SET next_attempt_at='1970-01-01T00:00:00Z' "
                "WHERE job_id=?",
                (job["job_id"],),
            )
            connection.commit()

    calls = {"submit": 0, "retrieve": 0, "cancel": 0}

    def unreadable():
        raise RuntimeSettingsStorageError("invalid runtime document")

    async def unexpected_submit(*_args, **_kwargs):
        calls["submit"] += 1
        raise AssertionError("provider submission must stay closed")

    async def retrieve(*_args, **_kwargs):
        calls["retrieve"] += 1
        return SimpleNamespace(
            status="completed",
            id="resp_existing",
            output_text=json.dumps(_earnings_result()),
            usage=None,
        )

    async def cancel(*_args, **_kwargs):
        calls["cancel"] += 1
        return SimpleNamespace(status="cancelled", id="resp_existing")

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        unreadable,
    )
    monkeypatch.setattr(runtime, "submit_background", unexpected_submit)
    monkeypatch.setattr(runtime, "retrieve", retrieve)
    monkeypatch.setattr(runtime, "cancel", cancel)

    result = asyncio.run(
        ai_worker.run_configured_once(repository, settings, "drain-owner")
    )

    stored = repository.get_job(job["job_id"])
    assert result == (1, "runtime_settings_unavailable")
    assert calls["submit"] == 0
    if cancel_requested:
        assert stored["status"] == "cancelled"
        assert calls == {"submit": 0, "retrieve": 0, "cancel": 1}
    else:
        assert stored["status"] == "completed"
        assert calls == {"submit": 0, "retrieve": 1, "cancel": 0}


def test_token_budget_blocks_before_provider_submission(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    job, created = _create_budget_job(repository, "market_focus", "T0000001")
    assert created is True
    claimed = repository.claim_due("budget-owner", lease_seconds=60)
    assert claimed is not None and claimed["job_id"] == job["job_id"]

    reservation = runtime.token_reservation("market_focus")
    token_limit = reservation - 1
    outcome = repository.mark_submission_started(
        job["job_id"],
        "budget-owner",
        daily_limit=4,
        daily_token_limit=token_limit,
    )
    stored = repository.get_job(job["job_id"])
    snapshot = repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=0,
        daily_token_limit=token_limit,
    )

    assert outcome == "daily_token_limit"
    assert stored is not None and stored["status"] == "budget_blocked"
    assert stored["error_code"] == "daily_token_limit_reached"
    assert stored["submission_started_at"] is None
    # The remaining balance can still fit a smaller task even though this
    # market-focus request is correctly blocked by its larger reservation.
    assert snapshot["token_budget_available"] is True
    assert snapshot["budget_available"] is True
    assert snapshot["token_budget_remaining_tokens"] == token_limit


def test_high_output_task_is_blocked_before_provider_call(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_budget_job(repository, "market_focus", "A0000001")
    settings = _settings(repository.path)
    reservation = runtime.token_reservation("market_focus")
    settings.openai_daily_token_limit = reservation - 1
    settings.openai_manual_cooldown_seconds = 0
    calls = {"prepare": 0, "submit": 0}

    def prepare(*_args, **_kwargs):
        calls["prepare"] += 1
        return object()

    async def submit(*_args, **_kwargs):
        calls["submit"] += 1
        raise AssertionError("budget-blocked task reached the provider")

    monkeypatch.setattr(runtime, "prepare_background", prepare)
    monkeypatch.setattr(runtime, "submit_background", submit)
    owner = "high-output-budget-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    stored = repository.get_job(job["job_id"])
    assert runtime.max_output_tokens_for("market_focus") == 49_152
    assert calls == {"prepare": 1, "submit": 0}
    assert stored["status"] == "budget_blocked"
    assert stored["error_code"] == "daily_token_limit_reached"
    assert stored["submission_started_at"] is None
    assert stored["budget_charge_microusd"] == 0


def test_completed_job_replaces_reservation_with_usage_cost(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    owner = "settlement-owner"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None
    assert repository.mark_submission_started(
        job["job_id"], owner, daily_limit=4
    ) == "started"
    reserved = repository.get_job(job["job_id"])
    assert reserved["budget_charge_microusd"] == (
        runtime.budget_reservation_microusd("earnings_impact")
    )
    usage = {
        "input_tokens": 100,
        "cached_input_tokens": 20,
        "output_tokens": 50,
        "reasoning_tokens": 10,
        "total_tokens": 150,
    }
    repository.complete(job["job_id"], owner, _earnings_result(), usage)

    completed = repository.get_job(job["job_id"])
    expected = runtime.settled_usage_cost_microusd(
        "earnings_impact",
        usage,
        fallback_microusd=reserved["budget_charge_microusd"],
    )
    assert expected == 1_005
    assert completed["budget_charge_microusd"] == expected
    assert completed["usage_total_tokens"] == 150


def test_completed_charge_cannot_exceed_the_original_reservation(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    owner = "settlement-cap-owner"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None
    assert repository.mark_submission_started(
        job["job_id"], owner, daily_limit=4
    ) == "started"
    reservation = runtime.budget_reservation_microusd("earnings_impact")
    repository.complete(
        job["job_id"],
        owner,
        _earnings_result(),
        {
            "input_tokens": 1_050_000,
            "cached_input_tokens": 0,
            "output_tokens": 128_000,
            "reasoning_tokens": 128_000,
            "total_tokens": 1_178_000,
        },
    )

    completed = repository.get_job(job["job_id"])
    assert completed["budget_charge_microusd"] == reservation


@pytest.mark.parametrize(
    ("status", "refusal", "expected_status", "expected_error"),
    [
        ("completed", "不能处理", "failed", "provider_refusal"),
        ("failed", None, "failed", "provider_failed"),
        ("incomplete", None, "failed", "provider_incomplete"),
        ("cancelled", None, "cancelled", None),
    ],
)
def test_provider_terminal_non_success_persists_usage(
    tmp_path,
    monkeypatch,
    status,
    refusal,
    expected_status,
    expected_error,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    settings = _settings(repository.path)
    usage = SimpleNamespace(
        input_tokens=100,
        output_tokens=50,
        total_tokens=150,
        input_tokens_details=SimpleNamespace(cached_tokens=20),
        output_tokens_details=SimpleNamespace(reasoning_tokens=10),
    )
    response = SimpleNamespace(
        status=status,
        id=f"resp_{status}",
        refusal=refusal,
        usage=usage,
    )

    monkeypatch.setattr(runtime, "prepare_background", lambda *_args, **_kwargs: object())

    async def submit(*_args, **_kwargs):
        return response

    monkeypatch.setattr(runtime, "submit_background", submit)
    owner = f"terminal-{status}-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    stored = repository.get_job(job["job_id"])
    assert stored["status"] == expected_status
    assert stored["error_code"] == expected_error
    assert stored["usage_input_tokens"] == 100
    assert stored["usage_cached_input_tokens"] == 20
    assert stored["usage_output_tokens"] == 50
    assert stored["usage_reasoning_tokens"] == 10
    assert stored["usage_total_tokens"] == 150
    assert stored["budget_charge_microusd"] == 1_005


def test_terminal_response_without_usage_keeps_full_reservation(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    job, _ = _create_earnings_job(repository)
    settings = _settings(repository.path)
    response = SimpleNamespace(status="failed", id="resp_no_usage", usage=None)
    monkeypatch.setattr(runtime, "prepare_background", lambda *_args, **_kwargs: object())

    async def submit(*_args, **_kwargs):
        return response

    monkeypatch.setattr(runtime, "submit_background", submit)
    owner = "terminal-no-usage-owner"
    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    stored = repository.get_job(job["job_id"])
    assert stored["status"] == "failed"
    assert stored["usage_input_tokens"] is None
    assert stored["usage_output_tokens"] is None
    assert stored["budget_charge_microusd"] == (
        runtime.budget_reservation_microusd("earnings_impact")
    )
    snapshot = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
    )
    assert snapshot["token_budget_used_tokens"] == runtime.token_reservation(
        "earnings_impact"
    )


def test_used_tokens_plus_every_task_reservation_never_exceeds_cap(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    seed, _ = _create_budget_job(repository, "earnings_impact", "B0000001")
    used_tokens = 100_000
    token_limit = (
        used_tokens + runtime.minimum_token_reservation() - 1
    )
    owner = "budget-seed-owner"
    claimed = repository.claim_due(owner, 60)
    assert repository.mark_submission_started(
        seed["job_id"],
        owner,
        daily_limit=4,
        daily_token_limit=token_limit,
    ) == "started"
    repository.complete(
        seed["job_id"],
        owner,
        _earnings_result(),
        {
            "input_tokens": 68_000,
            "cached_input_tokens": 0,
            "output_tokens": 32_000,
            "reasoning_tokens": 0,
            "total_tokens": used_tokens,
        },
    )

    for index, job_type in enumerate(runtime.AI_TASK_MAX_OUTPUT_TOKENS, start=2):
        suffix = f"B{index:07d}"
        job, _ = _create_budget_job(repository, job_type, suffix)
        owner = f"budget-owner-{job_type}"
        claimed = repository.claim_due(owner, 60)
        assert claimed is not None and claimed["job_id"] == job["job_id"]
        assert used_tokens + runtime.token_reservation(job_type) > token_limit
        assert repository.mark_submission_started(
            job["job_id"],
            owner,
            daily_token_limit=token_limit,
        ) == "daily_token_limit"
        assert repository.get_job(job["job_id"])["submission_started_at"] is None

    snapshot = repository.budget_snapshot(
        daily_limit=4,
        daily_budget_usd=0,
        daily_token_limit=token_limit,
    )
    assert snapshot["token_budget_used_tokens"] == used_tokens
    assert snapshot["token_budget_available"] is False


def test_cancel_requested_is_visible_while_provider_job_is_active(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    job, created = _create_earnings_job(repository)
    assert created is True
    claimed = repository.claim_due("cancel-owner", lease_seconds=60)
    assert claimed is not None
    assert repository.mark_submission_started(
        job["job_id"], "cancel-owner", daily_limit=4
    ) == "started"

    cancelled = repository.request_cancel(job["job_id"])
    assert cancelled is not None
    public = repository.public(cancelled)
    assert public["status"] == "in_progress"
    assert public["cancel_requested"] is True
    assert public["cancellable"] is False


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


def test_runtime_capability_rejects_non_official_configuration(tmp_path):
    settings = _settings(tmp_path / "ai-jobs.db")
    settings.openai_model = "unsupported-model"
    status = runtime.capability_status(settings)
    assert status["supported"] is False
    assert status["status"] == "runtime_configuration_invalid"


def test_worker_health_reports_official_responses_sdk_without_a_key(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    settings = _settings(repository.path)
    settings.openai_api_key = SecretStr("")

    status = health_payload(repository, settings)

    assert status["status"] == "disabled"
    assert status["sdk_capability_supported"] is True
    assert all(
        status["methods"].get(name) is True
        for name in ("create", "retrieve", "cancel")
    )


def test_all_paid_job_prompt_versions_invalidate_legacy_english_cache():
    assert ai._PROMPT_VERSIONS == {
        "earnings_impact": "earnings-impact-zh-cn-v4",
        "option_alerts": "option-alerts-zh-cn-v4",
        "signal_analysis": "signal-analysis-zh-cn-v4",
        "news_impact": "news-impact-zh-cn-v6",
        "market_focus": "market-focus-zh-cn-v4",
    }


def test_client_is_fixed_to_the_official_openai_base_url(
    tmp_path,
    monkeypatch,
):
    captured: dict = {}
    sentinel = object()

    def fake_client(**kwargs):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setitem(
        sys.modules,
        "openai",
        SimpleNamespace(AsyncOpenAI=fake_client),
    )
    monkeypatch.setattr(runtime, "_CLIENT", None)
    monkeypatch.setattr(runtime, "_CLIENT_SIGNATURE", None)
    settings = _settings(tmp_path / "ai-jobs.db")

    assert runtime._client(settings) is sentinel
    assert captured["base_url"] == "https://api.openai.com/v1"
    assert captured["api_key"] == "test-key"
    assert captured["max_retries"] == 0


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


def test_job_dedupe_precedes_saturated_queue_capacity_check(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    first, created = _create_earnings_job(repository)
    version, digest = runtime.schema_identity("earnings_impact")

    duplicate, created_again = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-v2",
        schema_version=version,
        schema_sha256=digest,
        max_queued=1,
    )

    assert created is True
    assert created_again is False
    assert duplicate["job_id"] == first["job_id"]
    with repository._connect() as connection:
        assert connection.execute(
            """SELECT COUNT(*) FROM ai_jobs
               WHERE status IN ('pending','queued','in_progress')"""
        ).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1


def test_job_identity_rejects_sync_mode_and_migrates_legacy_hash(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    background, created = _create_earnings_job(repository)
    assert created is True

    version, digest = runtime.schema_identity("earnings_impact")
    with pytest.raises(ValueError, match="background_execution_required"):
        repository.create_job(
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

    source_legacy_hash = repository._request_hash_source_legacy(
        "earnings_impact",
        payload_json,
        "manual",
        "gpt-5.6-terra",
        "max",
        "background",
        "earnings-impact-v2",
        version,
        digest,
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET request_hash=? WHERE job_id=?",
            (source_legacy_hash, background["job_id"]),
        )
        connection.commit()
    cross_source, created = _create_earnings_job(
        repository,
        submission_source="scheduled",
    )
    assert created is False
    assert cross_source["job_id"] == background["job_id"]
    assert cross_source["request_hash"] != source_legacy_hash
    assert cross_source["submission_source"] == "manual"


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
    snapshot = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
    )
    assert snapshot["token_budget_used_tokens"] == runtime.token_reservation(
        "earnings_impact"
    )


def test_paid_schema_failure_can_be_recovered_without_changing_usage(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    failed_at = "2026-07-19T12:34:56Z"
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET status='failed',
                      error_code='schema_validation_failed',
                      openai_response_id='resp_paid_valid_result',
                      submission_started_at='2026-07-19T12:30:00Z',
                      completed_at=?,usage_total_tokens=4321,
                      budget_charge_microusd=765432
               WHERE job_id=?""",
            (failed_at, row["job_id"]),
        )
        connection.commit()

    recovered = repository.recover_schema_validation_failure(
        row["job_id"],
        "resp_paid_valid_result",
        _earnings_result(),
    )

    assert recovered["status"] == "completed"
    assert recovered["error_code"] is None
    assert recovered["completed_at"] == failed_at
    assert recovered["usage_total_tokens"] == 4321
    assert recovered["budget_charge_microusd"] == 765432
    assert recovered["attempt_count"] == 0
    assert repository.public(recovered)["result"]["ticker"] == "AAPL"


def test_paid_schema_failure_recovery_rejects_wrong_response_or_invalid_result(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET status='failed',
                      error_code='schema_validation_failed',
                      openai_response_id='resp_paid_valid_result'
               WHERE job_id=?""",
            (row["job_id"],),
        )
        connection.commit()

    with pytest.raises(RuntimeError, match="ai_job_recovery_rejected"):
        repository.recover_schema_validation_failure(
            row["job_id"],
            "resp_different",
            _earnings_result(),
        )

    invalid = _earnings_result()
    invalid["summary"] = "Markets rally after strong earnings"
    with pytest.raises(ValidationError):
        repository.recover_schema_validation_failure(
            row["job_id"],
            "resp_paid_valid_result",
            invalid,
        )
    assert repository.get_job(row["job_id"])["status"] == "failed"


def test_recovery_tool_is_dry_run_by_default_and_never_resubmits(
    monkeypatch,
    tmp_path,
):
    database = tmp_path / "ai-jobs.db"
    repository = AIJobRepository(database)
    row, _ = _create_earnings_job(repository)
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET status='failed',
                      error_code='schema_validation_failed',
                      openai_response_id='resp_paid_valid_result'
               WHERE job_id=?""",
            (row["job_id"],),
        )
        connection.commit()
    calls = {"retrieve": 0}

    async def retrieve(_settings, response_id):
        calls["retrieve"] += 1
        assert response_id == "resp_paid_valid_result"
        return SimpleNamespace(
            id=response_id,
            status="completed",
            output_text=json.dumps(_earnings_result()),
            error=None,
            incomplete_details=None,
        )

    monkeypatch.setattr(
        recovery_tool,
        "get_settings",
        lambda: SimpleNamespace(openai_job_db_path=database),
    )
    monkeypatch.setattr(runtime, "retrieve", retrieve)

    validated = asyncio.run(recovery_tool.recover([row["job_id"]], apply=False))
    assert validated == [{"job_id": row["job_id"], "status": "validated"}]
    assert repository.get_job(row["job_id"])["status"] == "failed"

    recovered = asyncio.run(recovery_tool.recover([row["job_id"]], apply=True))
    assert recovered[0]["status"] == "recovered"
    assert repository.get_job(row["job_id"])["status"] == "completed"
    assert calls == {"retrieve": 2}


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
        submission_source="scheduled",
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
    submitted_at = (
        datetime.now(timezone.utc) - timedelta(hours=1)
    ).isoformat().replace("+00:00", "Z")
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_existing',
                submission_started_at=?, submitted_at=?,
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (submitted_at, submitted_at, row["job_id"]),
        )
        connection.commit()

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    deferred = repository.get_job(row["job_id"])
    assert deferred["status"] == "in_progress"
    assert deferred["openai_response_id"] == "resp_existing"
    assert deferred["error_code"] == "poll_window_elapsed"


def test_response_expiry_uses_submission_time_not_queue_creation_time(
    monkeypatch,
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    settings = _settings(tmp_path / "ai-jobs.db")
    owner = "worker-old-queue"
    retrieved = 0

    async def fake_retrieve(*_args, **_kwargs):
        nonlocal retrieved
        retrieved += 1
        return SimpleNamespace(status="in_progress", id="resp_recent")

    monkeypatch.setattr(runtime, "retrieve", fake_retrieve)
    submitted_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    with repository._connect() as connection:
        connection.execute(
            """
            UPDATE ai_jobs
            SET status='in_progress', openai_response_id='resp_recent',
                created_at='2020-01-01T00:00:00Z',
                submission_started_at=?, submitted_at=?,
                next_attempt_at='1970-01-01T00:00:00Z'
            WHERE job_id=?
            """,
            (submitted_at, submitted_at, row["job_id"]),
        )
        connection.commit()

    claimed = repository.claim_due(owner, 60)
    asyncio.run(process_job(repository, settings, claimed, owner))

    waiting = repository.get_job(row["job_id"])
    assert retrieved == 1
    assert waiting["status"] == "in_progress"
    assert waiting["error_code"] is None


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


def test_cached_get_hides_a_forged_zh_cn_legacy_result(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    owner = "legacy-cache-owner"
    claimed = repository.claim_due(owner, 60)
    legacy = _earnings_result()
    legacy["summary"] = "Markets rally after strong earnings"
    repository.complete(claimed["job_id"], owner, legacy, {})
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    response = client.get("/api/ai/earnings-impact/AAPL")

    assert response.status_code == 409
    assert response.json()["status"] == "analysis_required"
    assert "Markets rally" not in response.text
    assert repository.get_job(row["job_id"])["result_json"] is not None


def test_job_post_is_fast_local_and_idempotent(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)

    app = FastAPI()
    app.include_router(ai.router)
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
    stored = repository.get_job(first.json()["job_id"])
    assert stored["prompt_version"] == "earnings-impact-zh-cn-v4"


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
    assert retried.json()["job_id"] != first.json()["job_id"]
    assert retried.json()["status"] == "pending"
    assert repository.get_job(first.json()["job_id"])["status"] == "failed"
    retried_row = repository.get_job(retried.json()["job_id"])
    assert retried_row["retry_of_job_id"] == first.json()["job_id"]


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


def test_paid_job_route_has_no_extra_action_capability(monkeypatch, tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    response = client.post(
        "/api/ai/jobs/earnings-impact",
        json={"ticker": "AAPL"},
    )

    assert response.status_code == 202
    assert repository.health()["pending"] == 1


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


def test_daily_token_limit_is_reserved_before_provider_submission(
    monkeypatch,
    tmp_path,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")

    def create(ticker: str):
        return repository.create_job(
            job_type="earnings_impact",
            payload={"ticker": ticker, "name": ticker},
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version="earnings-impact-v2",
            schema_version=version,
            schema_sha256=digest,
            max_queued=200,
        )[0]

    row = create("USED")
    seed_owner = "worker-seed"
    claimed = repository.claim_due(seed_owner, 60)
    assert claimed is not None and claimed["job_id"] == row["job_id"]
    assert repository.mark_submission_started(
        row["job_id"],
        seed_owner,
        daily_token_limit=200_000,
    ) == "started"
    repository.link_background_response(
        row["job_id"],
        seed_owner,
        "resp_token_seed",
    )
    repository.fail(row["job_id"], seed_owner, "fixture_terminal")

    blocked = create("LIMIT")
    owner = "worker-limit"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None and claimed["job_id"] == blocked["job_id"]
    calls = 0

    async def forbidden_submit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("daily limit reached the provider")

    monkeypatch.setattr(runtime, "submit_background", forbidden_submit)
    settings = _settings(tmp_path / "ai-jobs.db")
    settings.openai_daily_token_limit = 200_000
    asyncio.run(process_job(repository, settings, claimed, owner))

    final = repository.get_job(blocked["job_id"])
    assert final["status"] == "budget_blocked"
    assert final["error_code"] == "daily_token_limit_reached"
    assert final["submission_started_at"] is None
    assert calls == 0


def test_concurrent_token_reservations_cannot_cross_the_limit(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")

    def create(ticker: str) -> dict:
        return repository.create_job(
            job_type="earnings_impact",
            payload={"ticker": ticker, "name": ticker},
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version="earnings-impact-v2",
            schema_version=version,
            schema_sha256=digest,
            max_queued=200,
        )[0]

    rows = [create("RACE1"), create("RACE2")]
    owners = ["race-owner-1", "race-owner-2"]
    claimed_rows = [repository.claim_due(owner, 60) for owner in owners]
    assert all(row is not None for row in claimed_rows)

    with ThreadPoolExecutor(max_workers=2) as executor:
        decisions = list(
            executor.map(
                lambda pair: repository.mark_submission_started(
                    pair[0]["job_id"],
                    pair[1],
                    daily_token_limit=204_799,
                ),
                zip(claimed_rows, owners, strict=True),
            )
        )

    assert sorted(decisions) == ["concurrency_limit", "started"]
    started_index = decisions.index("started")
    deferred_index = decisions.index("concurrency_limit")
    repository.link_background_response(
        rows[started_index]["job_id"],
        owners[started_index],
        "resp_concurrent_token_reservation",
    )
    repository.fail(
        rows[started_index]["job_id"],
        owners[started_index],
        "fixture_terminal",
    )
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET next_attempt_at=NULL WHERE job_id=?",
            (rows[deferred_index]["job_id"],),
        )
        connection.commit()
    deferred = repository.claim_due(owners[deferred_index], 60)
    assert deferred is not None
    assert repository.mark_submission_started(
        deferred["job_id"],
        owners[deferred_index],
        daily_token_limit=204_799,
    ) == "daily_token_limit"
    assert repository.get_job(deferred["job_id"])["status"] == "budget_blocked"


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
    assert retried["job_id"] != row["job_id"]
    assert retried["retry_of_job_id"] == row["job_id"]
    assert retried["execution_number"] == 2
    assert retried["status"] == "pending"

    claimed = repository.claim_due(owner, 60)
    repository.fail(retried["job_id"], owner, "submission_outcome_unknown")
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


def test_explicit_retry_requeues_completed_job_with_hidden_invalid_result(
    tmp_path,
):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    owner = "worker-invalid-completed-retry"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None and claimed["job_id"] == row["job_id"]
    assert repository.mark_submission_started(
        row["job_id"],
        owner,
        daily_limit=4,
    ) == "started"
    invalid = _earnings_result()
    invalid["summary"] = "Markets rally after stronger earnings"
    repository.complete(row["job_id"], owner, invalid, {})

    hidden = repository.public(repository.get_job(row["job_id"]))
    assert hidden["status"] == "completed"
    assert hidden["result"] is None
    assert hidden["error_code"] == "legacy_output_hidden"

    retried, created = _create_earnings_job(
        repository,
        force_retry=True,
    )
    assert created is True
    assert retried["status"] == "pending"
    assert retried["retry_of_job_id"] == row["job_id"]
    assert retried["execution_number"] == 2


def test_explicit_retry_keeps_a_valid_completed_result_settled(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    row, _ = _create_earnings_job(repository)
    owner = "worker-valid-completed-retry"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None and claimed["job_id"] == row["job_id"]
    assert repository.mark_submission_started(
        row["job_id"],
        owner,
        daily_limit=4,
    ) == "started"
    repository.complete(row["job_id"], owner, _earnings_result(), {})

    settled, created = _create_earnings_job(
        repository,
        force_retry=True,
    )
    assert created is False
    assert settled["job_id"] == row["job_id"]
    assert settled["status"] == "completed"


def test_explicit_retry_respects_the_queue_capacity_limit(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")

    def create(ticker: str, *, force_retry: bool = False):
        return repository.create_job(
            job_type="earnings_impact",
            payload={"ticker": ticker, "name": ticker},
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version="earnings-impact-v2",
            schema_version=version,
            schema_sha256=digest,
            max_queued=1,
            force_retry=force_retry,
        )

    terminal, _ = create("AAPL")
    owner = "worker-queue-capacity"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None and claimed["job_id"] == terminal["job_id"]
    repository.fail(terminal["job_id"], owner, "provider_failed")
    active, _ = create("MSFT")

    with pytest.raises(RuntimeError, match="ai_job_queue_full"):
        create("AAPL", force_retry=True)

    assert repository.get_job(terminal["job_id"])["status"] == "failed"
    assert repository.get_job(active["job_id"])["status"] == "pending"
    with repository._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 2


def test_worker_sync_job_creation_is_sealed(tmp_path):
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")
    with pytest.raises(ValueError, match="background_execution_required"):
        repository.create_job(
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
