"""Regressions for the 2026-09-25 AI job-core audit (AI-2 to AI-26).

Each test pins the corrected behaviour of one finding. Probes that reproduced
the defects live in the audit archive; the scenarios here are the same ones,
asserting the fixed outcome.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import itertools
import json
import sqlite3
import threading
from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from app.access import request_owner_access_context
from app.api import ai, signals
from app.services.ai_jobs import repository as repo_mod
from app.services.ai_jobs import runtime
from app.services.ai_jobs import worker as ai_worker
from app.services.ai_jobs.models import (
    _TRADITIONAL_CONFLICT_PHRASES,
    _TRADITIONAL_ONLY,
    InvalidJobPayloadError,
    normalize_earnings_analysis_payload,
    validate_result,
    validate_simplified_chinese_text,
)
from app.services.ai_jobs.repository import AIJobRepository, _daily_tokens_used
from app.services.ai_jobs.worker import process_job
from app.tools import recover_ai_schema_results as recovery_tool


@pytest.fixture(autouse=True)
def _owner_request_context():
    with request_owner_access_context(True):
        yield


class _Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock(monkeypatch):
    fake = _Clock()
    monkeypatch.setattr(repo_mod, "_utcnow", fake)
    return fake


class _StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"provider returned {status_code}")
        self.status_code = status_code


def _settings(path, **overrides):
    values = {
        "openai_api_key": SecretStr("test-key"),
        "openai_model": "gpt-5.6-terra",
        "openai_reasoning": "max",
        "openai_execution_mode": "background",
        "openai_timeout_seconds": 900,
        "openai_control_timeout_seconds": 30,
        "openai_max_retries": 0,
        "openai_max_concurrency": 1,
        "openai_background_initial_poll_seconds": 2,
        "openai_background_max_poll_seconds": 15,
        "openai_background_poll_timeout_seconds": 1800,
        "openai_job_db_path": path,
        "openai_job_lease_seconds": 60,
        "openai_job_max_age_seconds": 86400,
        "openai_job_max_queued": 200,
        "openai_daily_max_jobs": 0,
        "openai_daily_budget_usd": 0,
        "openai_daily_token_limit": 10_000_000,
        "openai_manual_cooldown_seconds": 0,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _create(
    repository: AIJobRepository,
    job_type: str = "earnings_impact",
    payload: dict | None = None,
    *,
    source: str = "scheduled",
    priority: int = 70,
    max_queued: int = 500,
):
    version, digest = runtime.schema_identity(job_type)
    row, created = repository.create_job(
        job_type=job_type,
        payload=payload or {"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS[job_type],
        schema_version=version,
        schema_sha256=digest,
        max_queued=max_queued,
        submission_source=source,
        priority=priority,
    )
    assert created is True
    return row


def _earnings_result(ticker: str = "AAPL", count: int = 4) -> dict:
    peers = [
        ("MSFT", "微软", "competitor"),
        ("QCOM", "高通", "supplier"),
        ("TSM", "台积电", "supplier"),
        ("XLK", "科技行业交易所交易基金", "etf"),
    ]
    return {
        "output_language": "zh-CN",
        "ticker": ticker,
        "summary": "供应链与大型科技股可能出现联动。",
        "expectation": "关注营收、利润率与指引。",
        "impacted": [
            {
                "ticker": code,
                "name": name,
                "relation": relation,
                "direction": "mixed",
                "reason": "公开业务关系可能形成传导。",
            }
            for code, name, relation in peers[:count]
        ],
    }


def _usage():
    return SimpleNamespace(
        input_tokens=1000,
        output_tokens=500,
        total_tokens=1500,
        input_tokens_details=SimpleNamespace(cached_tokens=0),
        output_tokens_details=SimpleNamespace(reasoning_tokens=300),
    )


def _token_rows(repository: AIJobRepository):
    with repository._connect() as connection:
        return connection.execute(
            """SELECT job_type,status,error_code,openai_response_id,
                      usage_total_tokens FROM ai_jobs"""
        ).fetchall()


def _lane_free(repository: AIJobRepository, lane: str) -> bool:
    return repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
        lane=lane,
    )["concurrency_available"]


def _release_next_attempt(repository: AIJobRepository, job_id: str) -> None:
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET next_attempt_at=NULL WHERE job_id=?",
            (job_id,),
        )
        connection.commit()


def _age_submission(repository: AIJobRepository, job_id: str, seconds: float):
    aged = (
        datetime.now(timezone.utc) - timedelta(seconds=seconds)
    ).isoformat().replace("+00:00", "Z")
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET submitted_at=?,submission_started_at=?,
                      next_attempt_at=NULL WHERE job_id=?""",
            (aged, aged, job_id),
        )
        connection.commit()


def _submitted_in_progress(repository, settings, monkeypatch, response_id):
    """Create one job and leave it linked to a queued provider response."""

    row = _create(repository)

    async def submit(*_args, **_kwargs):
        return SimpleNamespace(status="queued", id=response_id)

    monkeypatch.setattr(runtime, "submit_background", submit)
    claimed = repository.claim_due("setup", 60)
    asyncio.run(process_job(repository, settings, claimed, "setup"))
    stored = repository.get_job(row["job_id"])
    assert stored["openai_response_id"] == response_id
    assert stored["status"] == "queued"
    return row


# --- AI-2: one shared "can this lane submit" check for gate, claim, snapshot


def _simulate(repository, clock, owner, manual_id, seconds):
    """Mimic the production loop: 0.5 s after work, 2 s when idle."""

    started = clock.now
    verdicts: Counter[str] = Counter()
    manual_claimed_at = None
    while (clock.now - started).total_seconds() < seconds:
        job = repository.claim_due(owner, 60)
        if job is None:
            verdicts["idle"] += 1
            clock.advance(2.0)
            continue
        if job.get("openai_response_id"):
            repository.record_background_response(
                job["job_id"],
                owner,
                job["openai_response_id"],
                "in_progress",
                delay_seconds=15,
            )
            verdicts["poll"] += 1
        else:
            if job["job_id"] == manual_id and manual_claimed_at is None:
                manual_claimed_at = (clock.now - started).total_seconds()
            verdict = repository.mark_submission_started(
                job["job_id"],
                owner,
                daily_token_limit=100_000_000,
            )
            verdicts[verdict] += 1
            if verdict == "started":
                response_id = f"resp_{job['job_id'][-8:]}"
                repository.link_background_response(
                    job["job_id"],
                    owner,
                    response_id,
                )
                repository.record_background_response(
                    job["job_id"],
                    owner,
                    response_id,
                    "in_progress",
                    delay_seconds=15,
                )
        clock.advance(0.5)
    return verdicts, manual_claimed_at


def _backlog(repository, count=8):
    return [
        _create(repository, payload={"ticker": f"B{index:03d}", "name": "b"})
        for index in range(count)
    ]


def _manual_focus(repository):
    return _create(
        repository,
        "market_focus",
        {"cycle_id": "cycle-m1", "as_of": "2026-09-25T15:00:00Z"},
        source="manual",
        priority=60,
    )


def test_unknown_hold_does_not_starve_the_manual_lane(tmp_path, clock):
    repository = AIJobRepository(tmp_path / "ai.db")
    owner = "lane-worker"
    stuck = _create(repository, payload={"ticker": "STUCK", "name": "s"})
    assert repository.claim_due(owner, 60)["job_id"] == stuck["job_id"]
    assert repository.mark_submission_started(stuck["job_id"], owner) == "started"
    repository.fail(stuck["job_id"], owner, "submission_outcome_unknown")
    _backlog(repository)
    clock.advance(1)
    manual = _manual_focus(repository)

    verdicts, manual_claimed_at = _simulate(
        repository,
        clock,
        owner,
        manual["job_id"],
        seconds=1000,
    )

    assert manual_claimed_at == 0.0
    assert verdicts["concurrency_limit"] == 0
    # The unknown hold still protects the scheduled lane for its 900 s window,
    # after which exactly one backlog job takes the freed slot.
    assert verdicts["started"] == 2
    with repository._connect() as connection:
        churned = connection.execute(
            "SELECT COUNT(*) FROM ai_jobs "
            "WHERE error_code='global_concurrency_limit'"
        ).fetchone()[0]
    assert churned == 0


def test_backlog_is_not_reclaimed_while_its_lane_is_in_flight(tmp_path, clock):
    repository = AIJobRepository(tmp_path / "ai.db")
    owner = "lane-worker"
    running = _create(repository, payload={"ticker": "RUN", "name": "r"})
    assert repository.claim_due(owner, 60)["job_id"] == running["job_id"]
    assert repository.mark_submission_started(running["job_id"], owner) == "started"
    repository.link_background_response(running["job_id"], owner, "resp_running")
    repository.record_background_response(
        running["job_id"],
        owner,
        "resp_running",
        "in_progress",
        delay_seconds=15,
    )
    _backlog(repository)
    clock.advance(1)
    manual = _manual_focus(repository)

    verdicts, manual_claimed_at = _simulate(
        repository,
        clock,
        owner,
        manual["job_id"],
        seconds=600,
    )

    assert manual_claimed_at == 0.0
    assert verdicts["concurrency_limit"] == 0
    assert verdicts["started"] == 1
    assert verdicts["idle"] > verdicts["poll"]


def test_claim_skips_both_lanes_during_the_credit_hold(tmp_path, clock):
    repository = AIJobRepository(tmp_path / "ai.db")
    exhausted = _create(repository, payload={"ticker": "EMPTY", "name": "e"})
    manual = _create(
        repository,
        payload={"ticker": "MAN", "name": "m"},
        source="manual",
        priority=80,
    )
    assert repository.claim_due("w1", 60)["job_id"] == manual["job_id"]
    assert repository.claim_due("w2", 60)["job_id"] == exhausted["job_id"]
    assert repository.mark_submission_started(exhausted["job_id"], "w2") == "started"
    repository.link_background_response(exhausted["job_id"], "w2", "resp_empty")
    repository.fail(exhausted["job_id"], "w2", "provider_credit_exhausted")
    failed_at = clock.now

    # AI-18：账户级暂停，推迟而不是终态，不消耗重试次数。
    assert (
        repository.mark_submission_started(manual["job_id"], "w1")
        == "provider_credit_exhausted_hold"
    )
    held = repository.get_job(manual["job_id"])
    assert held["status"] == "pending"
    assert held["error_code"] == "provider_credit_exhausted_hold"
    assert held["attempt_count"] == 0
    assert held["submission_started_at"] is None
    assert repo_mod._parse_time(held["next_attempt_at"]) == failed_at + timedelta(
        seconds=600
    )
    _create(repository, payload={"ticker": "SCH", "name": "s"})
    _release_next_attempt(repository, manual["job_id"])
    assert repository.claim_due("w3", 60) is None

    clock.advance(601)
    assert repository.claim_due("w3", 60)["job_id"] == manual["job_id"]
    assert repository.mark_submission_started(manual["job_id"], "w3") == "started"


# --- AI-16: the manual cooldown only follows the manual lane


def test_manual_cooldown_only_follows_the_manual_lane(tmp_path, clock):
    repository = AIJobRepository(tmp_path / "ai.db")
    owner = "cooldown-worker"
    usage = {"input_tokens": 10, "output_tokens": 10, "total_tokens": 20}
    batch = _create(repository, payload={"ticker": "BATCH", "name": "b"})
    assert repository.claim_due(owner, 60)["job_id"] == batch["job_id"]
    assert repository.mark_submission_started(batch["job_id"], owner) == "started"
    repository.link_background_response(batch["job_id"], owner, "resp_batch")
    repository.complete(batch["job_id"], owner, {"output_language": "zh-CN"}, usage)

    clock.advance(1)
    manual = _manual_focus(repository)
    assert repository.claim_due(owner, 60, cooldown_seconds=30)["job_id"] == (
        manual["job_id"]
    )
    assert repository.mark_submission_started(
        manual["job_id"],
        owner,
        cooldown_seconds=30,
    ) == "started"
    repository.link_background_response(manual["job_id"], owner, "resp_manual")
    repository.complete(manual["job_id"], owner, {"output_language": "zh-CN"}, usage)

    clock.advance(1)
    second_manual = _create(
        repository,
        payload={"ticker": "M2", "name": "m"},
        source="manual",
        priority=80,
    )
    scheduled = _create(repository, payload={"ticker": "S2", "name": "s"})
    manual_view = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
        cooldown_seconds=30,
        lane="manual",
    )
    scheduled_view = repository.budget_snapshot(
        daily_limit=0,
        daily_budget_usd=0,
        cooldown_seconds=30,
        lane="scheduled",
    )
    assert manual_view["cooldown_complete"] is False
    assert scheduled_view["cooldown_complete"] is True
    # The manual job cools down; the scheduled lane does not wait for it.
    claimed = repository.claim_due(owner, 60, cooldown_seconds=30)
    assert claimed["job_id"] == scheduled["job_id"]
    assert repository.mark_submission_started(
        scheduled["job_id"],
        owner,
        cooldown_seconds=30,
    ) == "started"
    assert repository.claim_due(owner, 60, cooldown_seconds=30) is None

    clock.advance(30)
    assert repository.claim_due(owner, 60, cooldown_seconds=30)["job_id"] == (
        second_manual["job_id"]
    )


def test_run_once_passes_the_lane_controls_to_the_claim(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    captured = {}

    def claim_due(owner, lease_seconds, **kwargs):
        captured.update(kwargs, owner=owner, lease_seconds=lease_seconds)
        return None

    monkeypatch.setattr(repository, "claim_due", claim_due)
    settings = _settings(
        repository.path,
        openai_manual_cooldown_seconds=45,
        openai_job_max_age_seconds=7200,
    )
    assert asyncio.run(ai_worker.run_once(repository, settings, "w")) == 0
    assert captured == {
        "owner": "w",
        "lease_seconds": 60,
        "cooldown_seconds": 45,
        "unknown_submission_hold_seconds": 7200,
    }


def test_no_response_hold_window_is_one_shared_constant():
    for method in (
        AIJobRepository.mark_submission_started,
        AIJobRepository.budget_snapshot,
    ):
        assert "unknown_submission_no_response_hold_seconds" not in (
            inspect.signature(method).parameters
        )
    assert repo_mod._UNKNOWN_NO_RESPONSE_HOLD_SECONDS == 900


# --- AI-3: local SQLite failures are not provider failures


def _completed_submit(calls):
    async def submit(*_args, **_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(
            status="completed",
            id="resp_paid_ok",
            output_text=json.dumps(_earnings_result(), ensure_ascii=False),
            usage=_usage(),
        )

    return submit


def test_transient_busy_on_complete_is_retried_and_keeps_the_paid_result(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    calls = Counter()
    original_complete = repository.complete

    def flaky_complete(*args, **kwargs):
        calls["complete"] += 1
        if calls["complete"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return original_complete(*args, **kwargs)

    monkeypatch.setattr(runtime, "submit_background", _completed_submit(calls))
    monkeypatch.setattr(repository, "complete", flaky_complete)
    monkeypatch.setattr(ai_worker, "_STORAGE_WRITE_RETRY_DELAY_SECONDS", 0)
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, _settings(repository.path), claimed, "w1"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "completed"
    assert stored["usage_total_tokens"] == 1500
    assert calls == {"submit": 1, "complete": 2}


def test_persistent_busy_on_complete_defers_and_recovers_without_resubmit(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    settings = _settings(repository.path)
    calls = Counter()
    original_complete = repository.complete

    def busy_complete(*_args, **_kwargs):
        calls["complete"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runtime, "submit_background", _completed_submit(calls))
    monkeypatch.setattr(repository, "complete", busy_complete)
    monkeypatch.setattr(ai_worker, "_STORAGE_WRITE_RETRY_DELAY_SECONDS", 0)
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, settings, claimed, "w1"))

    deferred = repository.get_job(row["job_id"])
    assert deferred["status"] == "in_progress"
    assert deferred["error_code"] == "local_storage_error"
    assert deferred["openai_response_id"] == "resp_paid_ok"
    assert _lane_free(repository, "scheduled") is False
    assert _daily_tokens_used(_token_rows(repository)) == runtime.token_reservation(
        "earnings_impact"
    )

    async def retrieve(_settings, response_id):
        calls["retrieve"] += 1
        assert response_id == "resp_paid_ok"
        return SimpleNamespace(
            status="completed",
            id=response_id,
            output_text=json.dumps(_earnings_result(), ensure_ascii=False),
            usage=_usage(),
        )

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    monkeypatch.setattr(repository, "complete", original_complete)
    _release_next_attempt(repository, row["job_id"])
    reclaimed = repository.claim_due("w2", 60)
    asyncio.run(process_job(repository, settings, reclaimed, "w2"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "completed"
    assert calls["submit"] == 1
    assert calls["retrieve"] == 1


def test_busy_poll_record_keeps_the_lane_and_the_reservation(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    calls = Counter()

    async def submit(*_args, **_kwargs):
        calls["submit"] += 1
        return SimpleNamespace(status="queued", id="resp_running")

    def busy_record(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runtime, "submit_background", submit)
    monkeypatch.setattr(repository, "record_background_response", busy_record)
    monkeypatch.setattr(ai_worker, "_STORAGE_WRITE_RETRY_DELAY_SECONDS", 0)
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, _settings(repository.path), claimed, "w1"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "in_progress"
    assert stored["error_code"] == "local_storage_error"
    assert stored["openai_response_id"] == "resp_running"
    assert _lane_free(repository, "scheduled") is False
    assert _daily_tokens_used(_token_rows(repository)) == runtime.token_reservation(
        "earnings_impact"
    )
    assert calls["submit"] == 1


def test_busy_submission_gate_defers_a_job_that_was_never_sent(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository, source="manual")
    calls = Counter()

    async def submit(*_args, **_kwargs):
        calls["submit"] += 1
        raise AssertionError("an unsent job must not reach the provider")

    def busy_gate(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runtime, "submit_background", submit)
    monkeypatch.setattr(repository, "mark_submission_started", busy_gate)
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, _settings(repository.path), claimed, "w1"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "pending"
    assert stored["error_code"] == "local_storage_error"
    assert stored["submission_started_at"] is None
    assert stored["attempt_count"] == 0
    assert calls["submit"] == 0


def test_storage_failure_after_the_gate_returns_the_unsent_job_to_the_queue(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    calls = Counter()

    async def submit(*_args, **_kwargs):
        calls["submit"] += 1
        raise AssertionError("an unsent job must not reach the provider")

    def busy_read(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(runtime, "submit_background", submit)
    monkeypatch.setattr(repository, "get_job", busy_read)
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, _settings(repository.path), claimed, "w1"))

    stored = AIJobRepository(repository.path).get_job(row["job_id"])
    assert stored["status"] == "pending"
    assert stored["error_code"] == "local_storage_error"
    assert stored["submission_started_at"] is None
    assert stored["budget_charge_microusd"] == 0
    assert stored["attempt_count"] == 0
    assert calls["submit"] == 0
    # Not mistaken for an unknown submission: the lane stays free.
    assert _lane_free(AIJobRepository(repository.path), "scheduled") is True


def test_unsent_defer_requires_the_lease(tmp_path):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    with pytest.raises(RuntimeError, match="ai_job_lease_lost"):
        repository.defer_unsent_submission(
            row["job_id"],
            "not-the-owner",
            delay_seconds=2,
            error_code="local_storage_error",
        )


@pytest.mark.parametrize(
    ("submitted", "response_id", "expected"),
    [
        (False, None, "local_storage_error"),
        (True, "resp_known", "local_storage_error"),
        (True, None, "submission_outcome_unknown"),
    ],
)
def test_local_database_errors_never_map_to_provider_unavailable(
    submitted,
    response_id,
    expected,
):
    code = ai_worker._public_error(
        sqlite3.OperationalError("database is locked"),
        submitted=submitted,
        response_id=response_id,
    )
    assert code == expected


@pytest.mark.parametrize("error_code", ["provider_unavailable", "local_storage_error"])
def test_recovery_tool_reclaims_paid_results_lost_to_local_writes(
    tmp_path,
    monkeypatch,
    error_code,
):
    database = tmp_path / "ai.db"
    repository = AIJobRepository(database)
    recoverable = _create(repository, payload={"ticker": "AAPL", "name": "Apple"})
    unlinked = _create(repository, payload={"ticker": "MSFT", "name": "Microsoft"})
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET status='failed',error_code=?,
                      openai_response_id='resp_lost_locally'
               WHERE job_id=?""",
            (error_code, recoverable["job_id"]),
        )
        connection.execute(
            "UPDATE ai_jobs SET status='failed',error_code=? WHERE job_id=?",
            (error_code, unlinked["job_id"]),
        )
        connection.commit()

    async def retrieve(_settings, response_id):
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

    results = asyncio.run(
        recovery_tool.recover(
            [recoverable["job_id"], unlinked["job_id"]],
            apply=True,
        )
    )

    assert [item["status"] for item in results] == ["recovered", "not_recoverable"]
    assert repository.get_job(recoverable["job_id"])["status"] == "completed"


# --- AI-4: definitive retrieve/cancel errors settle; transient ones defer


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [
        (404, "failed", "provider_response_expired"),
        (401, "failed", "provider_auth_failed"),
        (403, "failed", "provider_auth_failed"),
        (400, "failed", "provider_request_rejected"),
        (429, "queued", "provider_poll_deferred"),
        (503, "queued", "provider_poll_deferred"),
    ],
)
def test_retrieve_errors_are_routed_by_status_code(
    tmp_path,
    monkeypatch,
    status_code,
    expected_status,
    expected_error,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_lost")

    async def retrieve(*_args, **_kwargs):
        raise _StatusError(status_code)

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    _release_next_attempt(repository, row["job_id"])
    claimed = repository.claim_due("w1", 60)
    asyncio.run(process_job(repository, settings, claimed, "w1"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == expected_status
    assert stored["error_code"] == expected_error
    # A definitive error frees the lane at once instead of after 24 hours.
    assert _lane_free(repository, "scheduled") is (expected_status == "failed")


def test_network_errors_on_retrieve_stay_deferred(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_net")

    async def retrieve(*_args, **_kwargs):
        raise OSError("network unavailable")

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    _release_next_attempt(repository, row["job_id"])
    asyncio.run(
        process_job(repository, settings, repository.claim_due("w1", 60), "w1")
    )
    assert repository.get_job(row["job_id"])["error_code"] == "provider_poll_deferred"


@pytest.mark.parametrize(
    ("cancel_outcome", "expected_error"),
    [
        ("cancelled", "provider_poll_timeout_cancelled"),
        ("unreachable", "provider_poll_timeout"),
    ],
)
def test_persistent_retrieve_failure_is_bound_by_the_poll_window(
    tmp_path,
    monkeypatch,
    cancel_outcome,
    expected_error,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_stall")
    calls = Counter()

    async def retrieve(*_args, **_kwargs):
        calls["retrieve"] += 1
        raise _StatusError(503)

    async def cancel(_settings, response_id):
        calls["cancel"] += 1
        if cancel_outcome == "unreachable":
            raise _StatusError(503)
        return SimpleNamespace(status="cancelled", id=response_id, usage=None)

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    monkeypatch.setattr(runtime, "cancel", cancel)
    _age_submission(repository, row["job_id"], seconds=2 * 3600)
    asyncio.run(
        process_job(repository, settings, repository.claim_due("w1", 60), "w1")
    )

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "failed"
    assert stored["error_code"] == expected_error
    assert calls == {"retrieve": 1, "cancel": 1}
    assert _lane_free(repository, "scheduled") is True


def test_job_past_max_age_is_retrieved_once_before_it_expires(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_old")
    calls = Counter()

    async def retrieve(_settings, response_id):
        calls["retrieve"] += 1
        return SimpleNamespace(
            status="completed",
            id=response_id,
            output_text=json.dumps(_earnings_result(), ensure_ascii=False),
            usage=_usage(),
        )

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    _age_submission(repository, row["job_id"], seconds=25 * 3600)
    asyncio.run(
        process_job(repository, settings, repository.claim_due("w1", 60), "w1")
    )

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "completed"
    assert calls["retrieve"] == 1


@pytest.mark.parametrize("outcome", ["error", "still_running"])
def test_job_past_max_age_expires_when_the_final_retrieve_cannot_settle(
    tmp_path,
    monkeypatch,
    outcome,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_gone")

    async def retrieve(_settings, response_id):
        if outcome == "error":
            raise OSError("network unavailable")
        return SimpleNamespace(status="in_progress", id=response_id)

    monkeypatch.setattr(runtime, "retrieve", retrieve)
    _age_submission(repository, row["job_id"], seconds=25 * 3600)
    asyncio.run(
        process_job(repository, settings, repository.claim_due("w1", 60), "w1")
    )

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "failed"
    assert stored["error_code"] == "provider_response_expired"


@pytest.mark.parametrize(
    ("status_code", "expected_status", "expected_error"),
    [
        (404, "failed", "provider_response_expired"),
        (401, "failed", "provider_auth_failed"),
        (503, "queued", "provider_cancel_deferred"),
    ],
)
def test_cancel_errors_are_routed_by_status_code(
    tmp_path,
    monkeypatch,
    status_code,
    expected_status,
    expected_error,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path)
    row = _submitted_in_progress(repository, settings, monkeypatch, "resp_cancel")

    async def cancel(*_args, **_kwargs):
        raise _StatusError(status_code)

    monkeypatch.setattr(runtime, "cancel", cancel)
    repository.request_cancel(row["job_id"])
    asyncio.run(
        process_job(repository, settings, repository.claim_due("w1", 60), "w1")
    )

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == expected_status
    assert stored["error_code"] == expected_error


# --- AI-5: manual submissions keep a reserve above a full scheduled queue


def test_manual_jobs_are_admitted_while_scheduled_news_fills_the_queue(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    settings = _settings(repository.path, openai_job_max_queued=5)
    for index in range(4):
        _create(
            repository,
            "news_impact",
            {
                "news_id": index + 1,
                "change_sequence": 1,
                "content_hash": f"h{index}",
                "allowed_tickers": ["AAPL"],
            },
            max_queued=5,
        )
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(ai, "_require_manual_analysis_enabled", lambda: None)

    admitted = [
        ai._create_job(
            "signal_analysis",
            {"ticker": ticker, "signals": {}, "scores": {}},
        )[1]
        for ticker in ("AMD", "NVDA")
    ]
    admitted.append(
        ai._create_job(
            "option_alerts",
            {"ticker": "AMD", "alerts": [], "underlying_price": 1},
        )[1]
    )
    assert admitted == [True, True, True]

    # The reserve is still a hard cap: base 5 + manual reserve 20.
    for index in range(runtime.MANUAL_QUEUE_RESERVE - 2):
        ai._create_job(
            "signal_analysis",
            {"ticker": f"R{index:03d}", "signals": {}, "scores": {}},
        )
    with pytest.raises(HTTPException) as refused:
        ai._create_job(
            "signal_analysis",
            {"ticker": "OVER", "signals": {}, "scores": {}},
        )
    assert refused.value.status_code == 429
    assert runtime.MANUAL_QUEUE_RESERVE >= runtime.EARNINGS_MANUAL_QUEUE_RESERVE


# --- AI-6: the validator accepts standard option/indicator/finance terms

LEGAL_ANALYSIS_TEXTS = [
    "Call成交量放大，但缺少主动方。",
    "Put/Call比率上升。",
    "隐含波动率（IV）处于高位。",
    "IV百分位偏高。",
    "成交量与OI比值偏高。",
    "未平仓合约（OI）增加。",
    "ATM期权成交放大。",
    "OTM看涨期权成交集中。",
    "ITM期权持仓增加。",
    "Gamma敞口较大。",
    "Delta中性。",
    "Vega风险上升。",
    "Theta损耗加快。",
    "看跌看涨比（PCR）偏高。",
    "RSI超买，短线有回调压力。",
    "MACD金叉，趋势延续。",
    "ATR扩大，波动加剧。",
    "首席执行官（CEO）表示需求强劲。",
    "CFO离职。",
    "CTO离职。",
    "标普500指数（SPX）下跌。",
    "美元指数（DXY）走强。",
    "纳斯达克100指数（NDX）走弱。",
    "恐慌指数（VIX）上涨。",
    "Non-GAAP每股收益为2.1美元。",
    "公司在10-K文件中披露了风险。",
    "10-Q显示库存上升。",
    "8-K文件显示高管变动。",
    "ChatGPT用户数继续增长。",
    "CoWoS产能依然紧张。",
    "YTD涨幅已达30%。",
]


@pytest.mark.parametrize("text", LEGAL_ANALYSIS_TEXTS)
def test_standard_market_terms_pass_the_chinese_validator(text):
    assert validate_simplified_chinese_text(text, None, allowed_codes=("AMD",))


@pytest.mark.parametrize(
    ("text", "codes"),
    [
        ("Delta股价上涨。", ("AMD",)),
        ("Call股价上涨。", ("AMD",)),
        ("股票代码IV走强。", ("AMD",)),
        ("标普500指数（SPX）股价上涨。", ("AMD",)),
        ("英伟达（NVDA）股价上涨。", ("AAPL",)),
        ("特斯拉（Tesla）股价上涨。", ("TSLA",)),
        ("SPY走弱，大盘承压。", ("AMD",)),
    ],
)
def test_security_context_still_requires_ticker_binding(text, codes):
    with pytest.raises(ValueError, match="english_prose_not_allowed"):
        validate_simplified_chinese_text(text, None, allowed_codes=codes)


def test_prose_rejection_names_the_rejected_fragment():
    with pytest.raises(ValueError, match=r"english_prose_not_allowed: 'Foobar'"):
        validate_simplified_chinese_text("公司发布Foobar更新。", None)
    with pytest.raises(
        ValidationError,
        match=r"english_prose_not_allowed: 'Apple Beats Estimates'",
    ):
        validate_result(
            "earnings_impact",
            json.dumps(
                {**_earnings_result(), "summary": "市场消息：Apple Beats Estimates"},
                ensure_ascii=False,
            ),
            {"ticker": "AAPL"},
        )


def _option_alert_result(summary: str) -> str:
    return json.dumps(
        {
            "output_language": "zh-CN",
            "confidence": "low",
            "direction": "unknown",
            "direction_status": "unavailable_without_trade_side",
            "summary": summary,
            "analysis": "缺少成交主动方，无法判断方向。",
            "key_strikes": ["120行权价"],
            "risk_note": "仅供信息参考。",
        },
        ensure_ascii=False,
    )


def test_option_alert_strings_are_source_texts():
    payload = {
        "ticker": "AMD",
        "alerts": [
            {
                "type": "call",
                "strike": 120,
                "volume": 5000,
                "moneyness": "otm",
                "reasons": ["Sweep"],
            }
        ],
        "underlying_price": 118,
        "expiration": "2026-10-16",
    }
    summary = "出现Sweep成交，但缺少主动方。"
    validated = validate_result("option_alerts", _option_alert_result(summary), payload)
    assert validated["summary"] == summary
    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "option_alerts",
            _option_alert_result(summary),
            {**payload, "alerts": []},
        )
    for wording in ("Call成交量放大，但缺少主动方。", "Put/Call比率上升。"):
        validate_result("option_alerts", _option_alert_result(wording), payload)


def test_common_simplified_finance_vocabulary_is_not_flagged_traditional():
    words = (
        "显著 著名 干扰 后续 只有 台积电 里程碑 面临 发展 了解 周期 占比 布局 系列 "
        "关系 联系 松绑 其余 复苏 云计算 范围 采购 制造 板块 钟摆 冲击 准则 几乎 表现 "
        "征收 占据 周边 复合 获得 获批 历史 汇率 汇总 必须 游戏 于是 并购 凭借 尽管 "
        "适合 系统 纤维 苏伊士 丑闻 卷入 厂商 广告 干预 回升 回购 合并 签署 协议 医药 "
        "药物 试验 临床 批准 标准 涨幅 跌幅 波动 趋势 支撑 阻力 均线 成交量 期权 行权价 "
        "到期 隐含波动率 看涨 看跌 认购 认沽 背离 超买 超卖 营收 利润 毛利率 指引 预期 "
        "市值 估值 市盈率 股息 回撤 杠杆 对冲 仓位 风险 敞口 流动性 利率 通胀 就业 降息 "
        "加息 美联储 财政部 国债 收益率 美元 欧元 日元 原油 黄金 白银 铜 天然气 芯片 "
        "半导体 数据中心 人工智能 算力 存储 内存 闪存 游戏机 电动车 电池 锂 光伏 风电 "
        "电网 航空 航天 国防 军工 银行 保险 券商 地产 零售 消费 餐饮 旅游 酒店 物流 快递 "
        "制药 生物 医疗 器械 保健 化工 钢铁 有机 余额 冬季 周末 盘前 盘后 收盘 开盘 复牌 "
        "停牌 暂停 恢复 升级 降级 评级 上调 下调 维持 目标价 分析师 机构 基金 仓储 制裁 "
        "关税 出口管制 谈判 选举 政策 监管 诉讼 调查 罚款 和解 裁员 招聘 罢工 供应链 "
        "库存 订单 交付 产能 扩产 投产"
    ).split()
    hits = {
        word: [char for char in word if char in _TRADITIONAL_ONLY]
        + [phrase for phrase in _TRADITIONAL_CONFLICT_PHRASES if phrase in word]
        for word in words
    }
    assert {word: found for word, found in hits.items() if found} == {}


@pytest.mark.parametrize(
    ("job_type", "identity"),
    [
        (
            "option_alerts",
            (
                "option_alerts_zh_cn_v4",
                "24e832d8625a5298abfbc29f7bf839bcd17c66f9b307723b036ceeae50a3ab6f",
            ),
        ),
        (
            "signal_analysis",
            (
                "signal_analysis_zh_cn_v5",
                "2574c44d0eb91112ff8118014dfeba2db028e3b4dd9c47771d245763e9cdc233",
            ),
        ),
        (
            "news_impact",
            (
                "news_impact_zh_cn_v6",
                "e35f6bc0b8d55cf0c8343e5fde84223b565ada5204be51bfdb956bfac652e3aa",
            ),
        ),
        (
            "market_focus",
            (
                "market_focus_zh_cn_v5",
                "6c3d008c66678f7729b5f69afcaa011597376fc7025431e2fd8a1afd67509d39",
            ),
        ),
    ],
)
def test_validator_changes_keep_queued_task_identities(job_type, identity):
    """The whitelist lives in the validator, not the prompt: pending jobs of
    the other four types must not flip to runtime_configuration_changed."""

    assert runtime.schema_identity(job_type) == identity


# --- AI-7: earnings impact binds its inputs and accepts short lists


def test_earnings_payload_names_are_source_texts():
    result = _earnings_result()
    result["summary"] = "Micron Technology本季度业绩超预期。"
    result["impacted"][0]["reason"] = "Micron Technology扩产可能带动设备需求。"
    payload = {"ticker": "MU", "name": "Micron Technology", "sector": "Technology"}
    result["ticker"] = "MU"

    validated = validate_result(
        "earnings_impact",
        json.dumps(result, ensure_ascii=False),
        payload,
    )

    assert validated["summary"].startswith("Micron Technology")
    with pytest.raises(ValidationError, match="english_prose_not_allowed"):
        validate_result(
            "earnings_impact",
            json.dumps(result, ensure_ascii=False),
            {"ticker": "MU"},
        )


@pytest.mark.parametrize("count", [1, 3])
def test_earnings_impact_accepts_fewer_than_four_companies(count):
    validated = validate_result(
        "earnings_impact",
        json.dumps(_earnings_result(count=count), ensure_ascii=False),
        {"ticker": "AAPL"},
    )
    assert len(validated["impacted"]) == count


def test_earnings_impact_still_needs_one_company_besides_itself():
    only_itself = _earnings_result(count=1)
    only_itself["impacted"][0].update(ticker="AAPL", name="苹果")
    with pytest.raises(ValueError, match="earnings_impacted_count_invalid"):
        validate_result(
            "earnings_impact",
            json.dumps(only_itself, ensure_ascii=False),
            {"ticker": "AAPL"},
        )
    with pytest.raises(ValidationError):
        validate_result(
            "earnings_impact",
            json.dumps(_earnings_result(count=0), ensure_ascii=False),
            {"ticker": "AAPL"},
        )


def test_earnings_contract_change_moves_the_schema_identity():
    schema_name, digest = runtime.schema_identity("earnings_impact")
    schema = json.loads(runtime._validation_schema_json("earnings_impact"))
    instructions = runtime.build_runtime_request("earnings_impact", {}).instructions

    assert schema_name == "earnings_impact_zh_cn_v5"
    assert digest != "65c6fcf4ec95e4fff55b5767f43a1a593492edcc05366c9da42161f0390a5f5b"
    assert schema["properties"]["impacted"]["minItems"] == 1
    assert runtime.PROMPT_VERSIONS["earnings_impact"] == "earnings-impact-zh-cn-v6"
    assert "列出1至8家" in instructions and "不得编造" in instructions


def test_old_identity_jobs_fail_retryably_without_reaching_the_provider(
    tmp_path,
    monkeypatch,
):
    from app.worker.tasks import EarningsAnalysisTask

    repository = AIJobRepository(tmp_path / "ai.db")
    row, _ = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v5",
        schema_version="earnings_impact_zh_cn_v4",
        schema_sha256="65c6fcf4ec95e4fff55b5767f43a1a593492edcc05366c9da42161f0390a5f5b",
        max_queued=200,
        submission_source="scheduled",
    )

    async def submit(*_args, **_kwargs):
        raise AssertionError("a stale identity must not be submitted")

    monkeypatch.setattr(runtime, "submit_background", submit)
    asyncio.run(
        process_job(
            repository,
            _settings(repository.path),
            repository.claim_due("w1", 60),
            "w1",
        )
    )

    stored = repository.get_job(row["job_id"])
    assert stored["error_code"] == "runtime_configuration_changed"
    assert stored["usage_total_tokens"] == 0
    assert "runtime_configuration_changed" in runtime.SCHEDULED_TRANSIENT_AI_ERRORS
    assert EarningsAnalysisTask._scheduled_retry_allowed(stored) is True
    assert "provider_incomplete" not in runtime.SCHEDULED_TRANSIENT_AI_ERRORS


# --- AI-15 / AI-25 / AI-26: signal analysis entry point


def _signal_stubs(monkeypatch, repository, counter):
    monkeypatch.setattr(signals, "_require_manual_analysis_enabled", lambda: None)
    monkeypatch.setattr(signals, "_require_runtime_capability", lambda: None)
    monkeypatch.setattr(
        signals,
        "read_stock_pull_resource",
        lambda _symbol, _resource: {
            "payload": {"rsi14": {"value": 55.0, "label": "RSI(14)"}},
            "saved_at": 1_700_000_000.0,
            "fresh": True,
        },
    )
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})

    async def dynamic_context(_symbol):
        await asyncio.sleep(0.05)
        return {
            "blocks": {"market_context": {"observed": next(counter)}},
            "status": {key: "ok" for key in signals.CONTEXT_BLOCK_KEYS},
            "context_tickers": [],
        }

    monkeypatch.setattr(signals, "build_signal_context", dynamic_context)

    def create_job(job_type, payload, *, force_retry=False):
        version, digest = runtime.schema_identity(job_type)
        return repository.create_job(
            job_type=job_type,
            payload=payload,
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version=runtime.PROMPT_VERSIONS[job_type],
            schema_version=version,
            schema_sha256=digest,
            max_queued=200,
            submission_source="manual",
            priority=80,
            force_retry=force_retry,
        )

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(signals, "_job_repository", lambda: repository)


def _active_signal_jobs(repository: AIJobRepository) -> int:
    with repository._connect() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM ai_jobs WHERE job_type='signal_analysis' "
            "AND status IN ('pending','queued','in_progress')"
        ).fetchone()[0]


def test_concurrent_signal_requests_for_one_ticker_create_one_job(
    tmp_path,
    monkeypatch,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    _signal_stubs(monkeypatch, repository, itertools.count())

    async def both():
        request = signals.SignalAnalysisJobCreateRequest()
        return await asyncio.gather(
            signals.stock_ai_analysis("AMD", request),
            signals.stock_ai_analysis("AMD", request),
        )

    first, second = asyncio.run(both())

    assert first.status_code == second.status_code == 202
    assert first.headers["Location"] == second.headers["Location"]
    assert _active_signal_jobs(repository) == 1

    forced = asyncio.run(
        signals.stock_ai_analysis(
            "AMD",
            signals.SignalAnalysisJobCreateRequest(force=True),
        )
    )
    assert forced.headers["Location"] != first.headers["Location"]
    assert _active_signal_jobs(repository) == 2


def test_signal_repository_calls_run_off_the_event_loop(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    _signal_stubs(monkeypatch, repository, itertools.count())
    loop_threads: list[int] = []
    call_threads: dict[str, int] = {}
    real_create = signals._create_job
    real_active = repository.active_for_ticker

    def create_job(*args, **kwargs):
        call_threads["create_job"] = threading.get_ident()
        return real_create(*args, **kwargs)

    def active_for_ticker(*args, **kwargs):
        call_threads["active_for_ticker"] = threading.get_ident()
        return real_active(*args, **kwargs)

    monkeypatch.setattr(signals, "_create_job", create_job)
    monkeypatch.setattr(repository, "active_for_ticker", active_for_ticker)

    async def run():
        loop_threads.append(threading.get_ident())
        return await signals.stock_ai_analysis(
            "AMD",
            signals.SignalAnalysisJobCreateRequest(),
        )

    assert asyncio.run(run()).status_code == 202
    assert set(call_threads) == {"create_job", "active_for_ticker"}
    assert loop_threads[0] not in set(call_threads.values())


def test_dropping_recent_news_also_drops_its_context_tickers():
    context = {
        "blocks": {"recent_news": {"items": [{"title": "新闻" * 20_000}]}},
        "status": {"recent_news": "ok"},
        "context_tickers": ["NVDA"],
    }
    oversized = signals._signal_analysis_payload("AMD", {}, {}, context=context)
    assert "recent_news" not in oversized
    assert "context_tickers" not in oversized
    assert oversized["context_status"]["recent_news"] == "omitted_size"

    context["blocks"]["recent_news"] = {"items": [{"title": "英伟达新品发布"}]}
    fitting = signals._signal_analysis_payload("AMD", {}, {}, context=context)
    assert fitting["context_tickers"] == ["NVDA"]


# --- AI-17: a cancel request cannot erase an unknown submission


def test_cancel_after_a_mid_submission_crash_stays_unknown(tmp_path, clock):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    settings = _settings(repository.path)
    assert repository.claim_due("dead-worker", 60)["job_id"] == row["job_id"]
    assert repository.mark_submission_started(row["job_id"], "dead-worker") == "started"
    repository.request_cancel(row["job_id"])
    clock.advance(61)
    reclaimed = repository.claim_due("new-worker", 60)
    assert reclaimed["job_id"] == row["job_id"]
    asyncio.run(process_job(repository, settings, reclaimed, "new-worker"))

    stored = repository.get_job(row["job_id"])
    assert stored["status"] == "failed"
    assert stored["error_code"] == "submission_outcome_unknown"
    assert _daily_tokens_used(_token_rows(repository)) == runtime.token_reservation(
        "earnings_impact"
    )
    assert _lane_free(repository, "scheduled") is False


# --- AI-19 / AI-20: one release rule for both ledgers


def test_credit_exhausted_failures_settle_both_ledgers_to_zero(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)

    async def submit(*_args, **_kwargs):
        return SimpleNamespace(
            status="failed",
            id="resp_credit",
            error=SimpleNamespace(
                code="credit_balance_exhausted",
                message="balance exhausted",
            ),
            usage=None,
        )

    monkeypatch.setattr(runtime, "submit_background", submit)
    asyncio.run(
        process_job(
            repository,
            _settings(repository.path),
            repository.claim_due("w1", 60),
            "w1",
        )
    )

    stored = repository.get_job(row["job_id"])
    snapshot = repository.budget_snapshot(daily_limit=0, daily_budget_usd=0)
    assert stored["error_code"] == "provider_credit_exhausted"
    assert stored["budget_charge_microusd"] == 0
    assert snapshot["token_budget_used_tokens"] == 0
    assert snapshot["budget_used_usd"] == 0
    # The zero charge survives the backfill that every initialize() runs.
    repository.initialize()
    assert repository.get_job(row["job_id"])["budget_charge_microusd"] == 0


@pytest.mark.parametrize(
    "error_code",
    ["provider_unavailable", "provider_response_expired", "provider_auth_failed"],
)
def test_unconfirmed_failures_with_a_response_id_keep_both_reservations(
    tmp_path,
    error_code,
):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    assert repository.claim_due("w1", 60)["job_id"] == row["job_id"]
    assert repository.mark_submission_started(row["job_id"], "w1") == "started"
    repository.link_background_response(row["job_id"], "w1", "resp_maybe_billed")
    repository.fail(row["job_id"], "w1", error_code)

    stored = repository.get_job(row["job_id"])
    snapshot = repository.budget_snapshot(daily_limit=0, daily_budget_usd=0)
    assert stored["budget_charge_microusd"] == runtime.budget_reservation_microusd(
        "earnings_impact"
    )
    assert snapshot["token_budget_used_tokens"] == runtime.token_reservation(
        "earnings_impact"
    )


def test_release_rule_is_a_whitelist():
    release = repo_mod._reservation_released
    assert release("failed", "provider_credit_exhausted", "resp") is True
    assert release("failed", "provider_failed", "resp") is True
    assert release("failed", "provider_unavailable", None) is True
    assert release("failed", "provider_unavailable", "resp") is False
    assert release("cancelled", None, "resp") is False
    assert release("cancelled", None, None) is True
    assert release("failed", "submission_outcome_unknown", None) is False
    assert release("failed", "provider_poll_timeout", None) is False
    assert release("completed", None, None) is False


# --- AI-21: one payload size limit


def test_payload_between_the_old_limits_is_refused_at_enqueue(tmp_path):
    repository = AIJobRepository(tmp_path / "ai.db")
    payload = {
        "ticker": "AMD",
        "signals": {"note": "x" * 50_000 + ">" * 2_500},
        "scores": {},
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(canonical) < 60_000
    with pytest.raises(ValueError, match="^ai_job_payload_too_large$"):
        _create(repository, "signal_analysis", payload, source="manual")
    with pytest.raises(ValueError, match="ai_input_too_large"):
        runtime._bounded_untrusted_json(payload)


# --- AI-22: error codes point at the right party


def test_error_codes_distinguish_payload_output_and_binding_failures():
    assert ai_worker._public_error(
        ValueError("signal_ticker_mismatch"),
        submitted=True,
        response_id="r",
    ) == "signal_ticker_mismatch"
    assert ai_worker._public_error(
        InvalidJobPayloadError("news_id_invalid"),
        submitted=False,
        response_id=None,
    ) == "invalid_job_payload"
    assert ai_worker._public_error(
        ValueError("1 validation error"),
        submitted=True,
        response_id="r",
    ) == "schema_validation_failed"


def test_invalid_queued_payload_fails_as_invalid_job_payload(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(
        repository,
        "news_impact",
        {"news_id": 0, "change_sequence": 1, "content_hash": "h", "allowed_tickers": []},
    )

    async def submit(*_args, **_kwargs):
        raise AssertionError("an invalid payload must not be submitted")

    monkeypatch.setattr(runtime, "submit_background", submit)
    asyncio.run(
        process_job(
            repository,
            _settings(repository.path),
            repository.claim_due("w1", 60),
            "w1",
        )
    )
    stored = repository.get_job(row["job_id"])
    assert stored["error_code"] == "invalid_job_payload"
    assert stored["error_detail"] == "news_id_invalid"
    assert stored["submission_started_at"] is None


# --- AI-23 / AI-24: API edges


@pytest.mark.parametrize(
    ("row", "owner", "expected"),
    [
        ({"status": "failed", "error_code": "schema_validation_failed"}, False, False),
        (
            {"status": "failed", "error_code": "provider_incomplete_max_output_tokens"},
            False,
            False,
        ),
        ({"status": "failed", "error_code": "provider_failed"}, False, True),
        ({"status": "failed", "error_code": "schema_validation_failed"}, True, True),
        ({"status": "cancelled", "error_code": "cancelled_by_user"}, False, True),
        ({"status": "failed", "error_code": "submission_outcome_unknown"}, True, False),
    ],
)
def test_visitor_retries_only_transient_failures(row, owner, expected):
    assert ai._earnings_report_force_retry(row, owner=owner) is expected


def test_cancel_endpoint_honours_confirm_false(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    app = FastAPI()
    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")

    refused = client.post(f"/api/ai/jobs/{row['job_id']}/cancel", json={"confirm": False})
    assert refused.status_code == 400
    assert refused.json()["detail"]["code"] == "confirmation_required"
    assert repository.get_job(row["job_id"])["status"] == "pending"

    accepted = client.post(f"/api/ai/jobs/{row['job_id']}/cancel", json={"confirm": True})
    assert accepted.status_code == 200
    assert accepted.json()["status"] == "cancelled"


def test_create_job_returns_the_decorated_earnings_row(tmp_path):
    repository = AIJobRepository(tmp_path / "ai.db")
    final_payload = normalize_earnings_analysis_payload(
        {
            "ticker": "AAPL",
            "name": "Apple",
            "earnings_date": "2026-07-23",
            "eps_estimate": 1.4,
            "eps_actual": 1.6,
        },
        analysis_stage="post_release_final",
    )
    version, digest = runtime.schema_identity("earnings_impact")

    def create():
        return repository.create_job(
            job_type="earnings_impact",
            payload=final_payload,
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
            schema_version=version,
            schema_sha256=digest,
            max_queued=200,
            submission_source="scheduled",
            priority=runtime.EARNINGS_FINAL_PRIORITY,
        )

    created_row, created = create()
    reused_row, reused = create()

    assert (created, reused) == (True, False)
    for row in (created_row, reused_row):
        assert row["earnings_finalization_in_progress"] == 1
        assert repository.public(row)["_finalization_in_progress"] is True
    stored = repository.get_job(created_row["job_id"])
    assert stored["earnings_final_locked"] == created_row["earnings_final_locked"]


# --- Schema registry: checksums are pinned to their versions


def test_schema_checksums_are_pinned_to_their_versions():
    def digest(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    observed = {
        repo_mod._SCHEMA_VERSION: digest(
            repo_mod._SCHEMA_REGISTRY_SQL
            + repo_mod._AI_JOBS_TABLE_SQL
            + repo_mod._AI_JOBS_INDEX_SQL
        ),
        repo_mod._SOURCE_SCHEMA_VERSION: digest(repo_mod._AI_JOB_SOURCES_TABLE_SQL),
        repo_mod._BATCH_SCHEMA_VERSION: digest(
            repo_mod._AI_JOB_BATCH_MEMBERS_TABLE_SQL
        ),
        repo_mod._EARNINGS_LOCK_SCHEMA_VERSION: digest(
            repo_mod._EARNINGS_FINAL_LOCKS_TABLE_SQL
        ),
    }
    assert observed == {
        "ai-jobs-v4": (
            "b436ff83ab65b48ca267a6ac87f51a426dcc54c26341d1fb16f6fe72d5a06ed6"
        ),
        "ai-job-sources-v1": (
            "1dc4fa74c9dfdad32936cd896d2422d18d771700b52d4779144bdfa2ac843feb"
        ),
        "ai-job-batch-members-v1": (
            "ad0fe9d62541faf14b048a3a11f47495a3fd53cfe18ebb2a762aa3f3a7f9de24"
        ),
        "ai-earnings-final-locks-v1": (
            "adbaf0f8bfe306da3445d55aa5f94f6b67693fe7510e9eb6f419c6294ada87e3"
        ),
    }, (
        "改了建表文本必须升版本：registry 按版本保存建表文本的校验和，版本不变"
        "而文本变了，已有库初始化时会抛 *_checksum_mismatch，ai_jobs/catalyst/"
        "focus 任务整体停摆（2026-08-08 事故）。升版本后在这里登记新的校验和。"
    )
    assert observed[repo_mod._SCHEMA_VERSION] == repo_mod._SCHEMA_CHECKSUM


# --- Retention: settled news/focus history is pruned


def test_prune_scheduled_history_removes_only_old_settled_news_and_focus(
    tmp_path,
    clock,
):
    repository = AIJobRepository(tmp_path / "ai.db")

    def news(news_id):
        return _create(
            repository,
            "news_impact",
            {
                "news_id": news_id,
                "change_sequence": 1,
                "content_hash": f"h{news_id}",
                "allowed_tickers": ["AAPL"],
            },
        )

    old_news = news(1)
    old_focus = _create(
        repository,
        "market_focus",
        {"cycle_id": "c-old", "as_of": "2026-09-01T00:00:00Z"},
    )
    old_active = news(2)
    old_earnings = _create(repository)
    old_created_settled_today = news(3)
    clock.advance(40 * 86400)
    recent_news = news(4)
    settled = [old_news, old_focus, old_earnings, recent_news, old_created_settled_today]
    stamp = repo_mod._iso(clock.now - timedelta(days=39))
    with repository._connect() as connection:
        for row in settled:
            connection.execute(
                "UPDATE ai_jobs SET status='completed',completed_at=? WHERE job_id=?",
                (stamp, row["job_id"]),
            )
        connection.execute(
            "UPDATE ai_jobs SET completed_at=? WHERE job_id=?",
            (repo_mod._iso(clock.now), old_created_settled_today["job_id"]),
        )
        connection.commit()

    removed = repository.prune_scheduled_history(retain_days=30)

    assert removed == 2
    remaining = {
        row["job_id"]
        for row in _token_rows_with_ids(repository)
    }
    assert remaining == {
        old_active["job_id"],
        old_earnings["job_id"],
        old_created_settled_today["job_id"],
        recent_news["job_id"],
    }
    with repository._connect() as connection:
        orphans = connection.execute(
            """SELECT
                 (SELECT COUNT(*) FROM ai_job_sources
                  WHERE job_id NOT IN (SELECT job_id FROM ai_jobs)),
                 (SELECT COUNT(*) FROM ai_job_batch_members
                  WHERE job_id NOT IN (SELECT job_id FROM ai_jobs))"""
        ).fetchone()
    assert tuple(orphans) == (0, 0)
    with pytest.raises(ValueError, match="invalid_scheduled_history_retain_days"):
        repository.prune_scheduled_history(retain_days=0)


def _token_rows_with_ids(repository: AIJobRepository):
    with repository._connect() as connection:
        return connection.execute("SELECT job_id FROM ai_jobs").fetchall()


# --- Diagnostics instead of silent suppression


def test_hidden_legacy_result_leaves_a_diagnostic(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    row = _create(repository)
    legacy = {**_earnings_result(), "summary": "Markets rally after earnings"}
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET status='completed',result_json=? WHERE job_id=?",
            (json.dumps(legacy), row["job_id"]),
        )
        connection.commit()
    recorded = []
    monkeypatch.setattr(
        repo_mod,
        "record_fallback_failure",
        lambda stage, error, **_kwargs: recorded.append(stage),
    )

    public = repository.public(repository.get_job(row["job_id"]))

    assert public["result"] is None
    assert public["error_code"] == "legacy_output_hidden"
    assert recorded == ["ai_job_result_hidden"]


def test_failure_that_cannot_be_persisted_leaves_a_diagnostic(tmp_path, monkeypatch):
    repository = AIJobRepository(tmp_path / "ai.db")
    _create(repository)
    recorded = []

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("ai_sdk_unavailable")

    def lost_lease(*_args, **_kwargs):
        raise RuntimeError("ai_job_lease_lost")

    monkeypatch.setattr(runtime, "prepare_background", unavailable)
    monkeypatch.setattr(repository, "fail", lost_lease)
    monkeypatch.setattr(
        ai_worker,
        "record_fallback_failure",
        lambda stage, error, **_kwargs: recorded.append(stage),
    )
    asyncio.run(
        process_job(
            repository,
            _settings(repository.path),
            repository.claim_due("w1", 60),
            "w1",
        )
    )
    assert recorded == ["ai_job_failure_persist"]
