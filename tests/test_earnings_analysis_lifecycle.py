from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr
import pytest
from starlette.requests import Request

from app.access import request_owner_access_context
from app.api import ai, earnings
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import normalize_earnings_analysis_payload
from app.services.ai_jobs.repository import AIJobRepository
from app.worker.tasks import EarningsAnalysisTask


def _result(ticker: str) -> dict:
    return {
        "output_language": "zh-CN",
        "ticker": ticker,
        "summary": "实际每股收益高于预期，收入数据仍需结合管理层说明。",
        "expectation": "财报已经发布，应以实际值与预期值的差异为准。",
        "impacted": [
            {
                "ticker": code,
                "name": name,
                "relation": "competitor",
                "direction": "mixed",
                "reason": "同业估值可能受到已发布财报的比较影响。",
            }
            for code, name in (
                ("MSFT", "微软"),
                ("QCOM", "高通"),
                ("TSM", "台积电"),
                ("XLK", "科技行业交易所交易基金"),
            )
        ],
    }


def _settings(path):
    return SimpleNamespace(
        openai_api_key=SecretStr("test-key"),
        openai_model="gpt-5.6-terra",
        openai_reasoning="max",
        openai_execution_mode="background",
        openai_job_db_path=path,
        openai_job_max_queued=200,
    )


def _create(repository: AIJobRepository, payload: dict, *, force=False):
    version, digest = runtime.schema_identity("earnings_impact")
    return repository.create_job(
        job_type="earnings_impact",
        payload=payload,
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version="earnings-impact-zh-cn-v5",
        schema_version=version,
        schema_sha256=digest,
        max_queued=200,
        force_retry=force,
    )


def _seed_active_jobs(repository: AIJobRepository, count: int) -> None:
    version, digest = runtime.schema_identity("option_alerts")
    for index in range(count):
        repository.create_job(
            job_type="option_alerts",
            payload={"ticker": f"Q{index:04d}"},
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version="queue-capacity-seed-v1",
            schema_version=version,
            schema_sha256=digest,
            max_queued=count + 1,
            submission_source="scheduled",
            priority=10,
        )


def _active_job_count(repository: AIJobRepository) -> int:
    with repository._connect() as connection:
        return int(
            connection.execute(
                """SELECT COUNT(*) FROM ai_jobs
                   WHERE status IN ('pending','queued','in_progress')"""
            ).fetchone()[0]
        )


def _complete_pre_release_analysis(
    repository: AIJobRepository,
    ticker: str,
    *,
    report_date: str,
    year: int | None = None,
    quarter: int | None = None,
) -> dict:
    payload = normalize_earnings_analysis_payload(
        {
            "ticker": ticker,
            "name": ticker,
            "earnings_date": report_date,
            "year": year,
            "quarter": quarter,
            "eps_estimate": 1.0,
            "release_status": "scheduled",
        },
        analysis_stage="pre_release",
    )
    row, created = _create(repository, payload)
    assert created is True
    owner = f"pre-release-{ticker}"
    claimed = repository.claim_due(owner, 60)
    assert claimed is not None and claimed["job_id"] == row["job_id"]
    repository.complete(row["job_id"], owner, _result(ticker), {})
    completed = repository.get_job(row["job_id"])
    assert completed is not None
    return completed


def _final_payload(
    ticker: str = "AAPL",
    *,
    report_date: str = "2026-07-23",
    year: int = 2026,
    quarter: int = 2,
) -> dict:
    return normalize_earnings_analysis_payload(
        {
            "ticker": ticker,
            "name": ticker,
            "earnings_date": report_date,
            "year": year,
            "quarter": quarter,
            "eps_estimate": 1.4,
            "eps_actual": 1.6,
            "revenue_estimate": 90_000_000_000,
            "revenue_actual": 92_000_000_000,
            "release_status": "released",
        },
        analysis_stage="post_release_final",
    )


def test_final_requires_comparable_actual_and_estimate() -> None:
    with pytest.raises(
        ValueError,
        match="final_earnings_analysis_requires_comparable_actuals",
    ):
        normalize_earnings_analysis_payload(
            {
                "ticker": "AAPL",
                "earnings_date": "2026-07-23",
                "eps_actual": 1.6,
            },
            analysis_stage="post_release_final",
        )


def test_post_release_prompt_requires_actual_vs_estimate_comparison() -> None:
    prepared = runtime.build_runtime_request(
        "earnings_impact",
        _final_payload(),
    )
    assert "逐项比较输入中的actual与estimate" in prepared.instructions
    assert "不得继续使用“假设超预期”" in prepared.instructions
    assert '"eps_actual":1.6' in prepared.input_text
    assert '"eps_estimate":1.4' in prepared.input_text


def test_completed_final_locks_exact_report_but_not_new_quarter(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    payload = _final_payload()
    row, created = _create(repository, payload)
    assert created is True
    same, same_created = _create(repository, payload)
    assert same_created is False
    assert same["job_id"] == row["job_id"]
    manual_payload = normalize_earnings_analysis_payload(
        {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "analysis_stage",
                "analysis_phase",
                "report_id",
                "input_hash",
            }
        },
        analysis_stage="post_release_manual",
    )
    with pytest.raises(
        RuntimeError,
        match="earnings_finalization_in_progress",
    ):
        _create(repository, manual_payload)
    claimed = repository.claim_due("final-worker", 60)
    assert claimed is not None
    repository.complete(claimed["job_id"], "final-worker", _result("AAPL"), {})

    published = repository.public(repository.get_job(row["job_id"]))
    assert published["_analysis_stage"] == "post_release_final"
    assert published["_report_id"] == payload["report_id"]
    assert published["_locked"] is True
    assert published["_final"] is True

    with pytest.raises(RuntimeError, match="earnings_analysis_locked"):
        _create(repository, payload)
    with pytest.raises(RuntimeError, match="earnings_analysis_locked"):
        version, digest = runtime.schema_identity("earnings_impact")
        repository.create_job(
            job_type="earnings_impact",
            payload=payload,
            model="gpt-5.6-terra",
            reasoning="max",
            execution_mode="background",
            prompt_version="future-prompt-version",
            schema_version=version,
            schema_sha256=digest,
            max_queued=200,
        )

    next_quarter = _final_payload(
        report_date="2026-10-23",
        quarter=3,
    )
    _next, next_created = _create(repository, next_quarter)
    assert next_created is True


def test_latest_for_report_treats_stage_less_job_as_pre_release(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    payload = normalize_earnings_analysis_payload(
        {
            "ticker": "AAPL",
            "name": "Apple",
            "earnings_date": "2026-07-23",
            "year": 2026,
            "quarter": 2,
            "eps_estimate": 1.4,
        },
        analysis_stage="pre_release",
    )
    report_id = payload["report_id"]
    payload.pop("analysis_stage")
    payload.pop("analysis_phase")
    legacy, created = _create(repository, payload)
    assert created is True
    claimed = repository.claim_due("legacy-pre-release", 60)
    assert claimed is not None and claimed["job_id"] == legacy["job_id"]
    repository.complete(
        legacy["job_id"],
        "legacy-pre-release",
        _result("AAPL"),
        {},
    )

    preliminary = repository.latest_for_report(
        "AAPL",
        report_id,
        analysis_stage="pre_release",
        status="completed",
    )
    final = repository.latest_for_report(
        "AAPL",
        report_id,
        analysis_stage="post_release_final",
    )

    assert preliminary is not None and preliminary["job_id"] == legacy["job_id"]
    assert final is None

    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )
    task = EarningsAnalysisTask(
        "legacy-pre-release-final",
        settings=_settings(repository.path),
        repository=repository,
        builder=lambda _today: {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "earnings_date": "2026-07-23",
                    "days_until": -1,
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    "release_status": "released",
                }
            ],
        },
        runtime_settings_reader=lambda: SimpleNamespace(
            ai=SimpleNamespace(manual_analysis_enabled=True),
            earnings=SimpleNamespace(
                scheduled_analysis_enabled=True,
                lookahead_days=5,
            ),
        ),
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )
    scheduled = asyncio.run(task())
    latest = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert scheduled.details["queued"] == 1
    assert latest is not None
    assert json.loads(latest["payload_json"])["analysis_stage"] == (
        "post_release_final"
    )


def test_final_failure_does_not_lock_and_exact_retry_is_allowed(tmp_path) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    payload = _final_payload()
    first, _ = _create(repository, payload)
    claimed = repository.claim_due("failed-final", 60)
    assert claimed is not None
    repository.fail(claimed["job_id"], "failed-final", "provider_failed")

    failed = repository.public(repository.get_job(first["job_id"]))
    assert failed["_locked"] is False
    retry, created = _create(repository, payload, force=True)
    assert created is True
    assert retry["retry_of_job_id"] == first["job_id"]


def test_retention_deletes_only_strictly_older_terminal_rows_and_sources(
    tmp_path,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    now = datetime(2026, 8, 22, 12, tzinfo=timezone.utc)
    cutoff = now - timedelta(days=30)

    old, _ = _create(repository, _final_payload("AAPL"))
    claimed = repository.claim_due("old-final", 60)
    repository.complete(claimed["job_id"], "old-final", _result("AAPL"), {})

    exact_payload = normalize_earnings_analysis_payload(
        {"ticker": "MSFT", "earnings_date": "2026-07-22"},
        analysis_stage="pre_release",
    )
    exact, _ = _create(repository, exact_payload)
    repository.request_cancel(exact["job_id"])

    active_payload = normalize_earnings_analysis_payload(
        {"ticker": "NVDA", "earnings_date": "2026-07-22"},
        analysis_stage="pre_release",
    )
    active, _ = _create(repository, active_payload)

    old_stamp = (cutoff - timedelta(microseconds=1)).isoformat().replace(
        "+00:00", "Z"
    )
    exact_stamp = cutoff.isoformat().replace("+00:00", "Z")
    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs SET completed_at=?,updated_at=?,created_at=?
               WHERE job_id=?""",
            (old_stamp, old_stamp, old_stamp, old["job_id"]),
        )
        connection.execute(
            """UPDATE ai_jobs SET completed_at=?,updated_at=?,created_at=?
               WHERE job_id=?""",
            (exact_stamp, exact_stamp, exact_stamp, exact["job_id"]),
        )
        connection.execute(
            """UPDATE ai_jobs SET updated_at=?,created_at=? WHERE job_id=?""",
            (old_stamp, old_stamp, active["job_id"]),
        )
        connection.commit()

    assert repository.prune_earnings_retention(now=now) == 1
    assert repository.get_job(old["job_id"]) is None
    assert repository.get_job(exact["job_id"]) is not None
    assert repository.get_job(active["job_id"]) is not None
    with repository._connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM ai_job_sources WHERE job_id=?",
            (old["job_id"],),
        ).fetchone() is None
        assert connection.execute(
            "SELECT 1 FROM ai_earnings_final_locks WHERE job_id=?",
            (old["job_id"],),
        ).fetchone() is None


def test_report_action_uses_server_snapshot_and_hides_job_capabilities(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(repository.path))
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)

    async def snapshot():
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "sector": "Technology",
                    "earnings_date": "2026-07-23",
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    "revenue_estimate": 90_000_000_000,
                    "revenue_actual": 92_000_000_000,
                    "market_cap": 3_000_000_000_000,
                    "release_status": "released",
                }
            ],
        }

    monkeypatch.setattr(
        earnings,
        "_read_current_upcoming_earnings_snapshot",
        snapshot,
    )
    app = FastAPI()

    @app.middleware("http")
    async def bind_visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")
    response = client.post(
        "/api/ai/earnings-impact/AAPL/reports/2026-07-23"
        "?year=2026&quarter=2",
        json={"confirm": True},
        headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
    )

    assert response.status_code == 202
    assert set(response.json()).isdisjoint(
        {"job_id", "usage", "budget_charge_usd", "cancellable"}
    )
    stored = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert stored["priority"] == 75
    payload = json.loads(stored["payload_json"])
    assert payload["eps_actual"] == 1.6
    assert payload["eps_estimate"] == 1.4
    assert payload["market_cap"] == 3_000_000_000_000
    assert payload["analysis_stage"] == "post_release_manual"

    tampered = client.post(
        "/api/ai/earnings-impact/AAPL/reports/2026-07-23"
        "?year=2026&quarter=2",
        json={"confirm": True, "eps_actual": 999},
        headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
    )
    assert tampered.status_code == 422


def test_manual_earnings_jobs_keep_reserved_queue_capacity(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    captured: dict = {}
    real_create = repository.create_job

    def create_job(**kwargs):
        captured.update(kwargs)
        return real_create(**kwargs)

    monkeypatch.setattr(repository, "create_job", create_job)
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(repository.path))
    monkeypatch.setattr(ai, "_require_earnings_manual_analysis_enabled", lambda: None)

    ai._create_job(
        "earnings_impact",
        _final_payload(),
        priority=runtime.EARNINGS_VISITOR_PRIORITY,
    )

    assert captured["max_queued"] == (
        200 + runtime.EARNINGS_MANUAL_QUEUE_RESERVE
    )
    assert captured["priority"] == runtime.EARNINGS_VISITOR_PRIORITY == 75
    assert captured["submission_source"] == "manual"


def test_queue_reserves_are_tiered_and_keep_a_hard_total_cap(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        runtime.EARNINGS_FINAL_PRIORITY
        > runtime.EARNINGS_OWNER_PRIORITY
        > runtime.EARNINGS_VISITOR_PRIORITY
        > runtime.EARNINGS_PRE_RELEASE_PRIORITY
    )
    assert (
        runtime.EARNINGS_FINAL_QUEUE_RESERVE
        > runtime.EARNINGS_MANUAL_QUEUE_RESERVE
        > 0
    )
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(repository.path)
    effective = SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(
            scheduled_analysis_enabled=True,
            lookahead_days=30,
        ),
    )
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: settings)
    monkeypatch.setattr(
        ai,
        "_require_earnings_manual_analysis_enabled",
        lambda: None,
    )
    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )
    final_capacity = (
        runtime.EARNINGS_FINAL_QUEUE_RESERVE
        - runtime.EARNINGS_MANUAL_QUEUE_RESERVE
    )
    for index in range(final_capacity + 1):
        _complete_pre_release_analysis(
            repository,
            f"F{index:03d}",
            report_date="2026-07-23",
        )
    _seed_active_jobs(repository, settings.openai_job_max_queued)

    async def pre_release_builder(_today):
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "PRE",
                    "name": "Pre release",
                    "earnings_date": "2026-07-25",
                    "days_until": 1,
                    "eps_estimate": 1.0,
                }
            ],
        }

    pre_release_task = EarningsAnalysisTask(
        "scheduled-pre-release-cap",
        settings=settings,
        repository=repository,
        builder=pre_release_builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )
    pre_release = asyncio.run(pre_release_task())

    assert pre_release.status == "degraded"
    assert pre_release.error_code == "ai_job_queue_full"
    assert pre_release.details["queued"] == 0
    assert _active_job_count(repository) == settings.openai_job_max_queued

    for index in range(runtime.EARNINGS_MANUAL_QUEUE_RESERVE):
        payload = normalize_earnings_analysis_payload(
            {
                "ticker": f"M{index:03d}",
                "earnings_date": "2026-07-23",
                "eps_estimate": 1.0,
                "eps_actual": 1.1,
            },
            analysis_stage="post_release_manual",
        )
        _row, created = ai._create_job(
            "earnings_impact",
            payload,
            priority=runtime.EARNINGS_VISITOR_PRIORITY,
        )
        assert created is True

    manual_cap = (
        settings.openai_job_max_queued
        + runtime.EARNINGS_MANUAL_QUEUE_RESERVE
    )
    assert _active_job_count(repository) == manual_cap
    overflow_payload = normalize_earnings_analysis_payload(
        {
            "ticker": "M999",
            "earnings_date": "2026-07-23",
            "eps_estimate": 1.0,
            "eps_actual": 1.1,
        },
        analysis_stage="post_release_manual",
    )
    with pytest.raises(HTTPException) as blocked:
        ai._create_job(
            "earnings_impact",
            overflow_payload,
            priority=runtime.EARNINGS_VISITOR_PRIORITY,
        )
    assert getattr(blocked.value, "status_code", None) == 429
    assert _active_job_count(repository) == manual_cap

    async def final_builder(_today):
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": f"F{index:03d}",
                    "name": f"Final {index}",
                    "earnings_date": "2026-07-23",
                    "days_until": -1,
                    "eps_estimate": 1.0,
                    "eps_actual": 1.2,
                    "release_status": "released",
                }
                for index in range(final_capacity + 1)
            ],
        }

    final_task = EarningsAnalysisTask(
        "scheduled-final-cap",
        settings=settings,
        repository=repository,
        builder=final_builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )
    final_result = asyncio.run(final_task())
    final_cap = (
        settings.openai_job_max_queued
        + runtime.EARNINGS_FINAL_QUEUE_RESERVE
    )

    assert final_result.status == "degraded"
    assert final_result.error_code == "ai_job_queue_full"
    assert final_result.details["queued"] == final_capacity
    assert _active_job_count(repository) == final_cap
    with repository._connect() as connection:
        priority_counts = {
            int(row["priority"]): int(row["count"])
            for row in connection.execute(
                """SELECT priority,COUNT(*) AS count FROM ai_jobs
                   WHERE status IN ('pending','queued','in_progress')
                   GROUP BY priority"""
            ).fetchall()
        }
    assert priority_counts[runtime.EARNINGS_VISITOR_PRIORITY] == (
        runtime.EARNINGS_MANUAL_QUEUE_RESERVE
    )
    assert priority_counts[runtime.EARNINGS_FINAL_PRIORITY] == final_capacity
    claimed = repository.claim_due("priority-contract-worker", 60)
    assert claimed is not None
    assert claimed["priority"] == runtime.EARNINGS_FINAL_PRIORITY == 90


def test_saturated_base_queue_processes_same_day_final_before_pre_release(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(repository.path)
    effective = SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(
            scheduled_analysis_enabled=True,
            lookahead_days=30,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )
    _complete_pre_release_analysis(
        repository,
        "ZZZ",
        report_date="2026-07-24",
    )
    _seed_active_jobs(repository, settings.openai_job_max_queued)

    async def mixed_builder(_today):
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAA",
                    "name": "Unreleased first alphabetically",
                    "earnings_date": "2026-07-24",
                    "days_until": 0,
                    "eps_estimate": 1.0,
                },
                {
                    "ticker": "ZZZ",
                    "name": "Released final",
                    "earnings_date": "2026-07-24",
                    "days_until": 0,
                    "eps_estimate": 1.0,
                    "eps_actual": 1.2,
                    "release_status": "released",
                },
            ],
        }

    task = EarningsAnalysisTask(
        "same-day-final-first",
        settings=settings,
        repository=repository,
        builder=mixed_builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )
    result = asyncio.run(task())

    assert result.status == "degraded"
    assert result.error_code == "ai_job_queue_full"
    assert result.details["queued"] == 1
    assert repository.latest_for_ticker("earnings_impact", "AAA") is None
    final = repository.latest_for_ticker("earnings_impact", "ZZZ")
    assert final is not None
    assert final["priority"] == runtime.EARNINGS_FINAL_PRIORITY
    assert json.loads(final["payload_json"])["analysis_stage"] == (
        "post_release_final"
    )
    assert _active_job_count(repository) == (
        settings.openai_job_max_queued + 1
    )


@pytest.mark.parametrize(
    ("terminal_status", "error_code"),
    (
        ("failed", "provider_failed"),
        ("cancelled", "cancelled_by_user"),
        ("budget_blocked", "daily_token_limit_reached"),
    ),
)
def test_public_report_action_retries_retryable_terminal_job(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
    error_code: str,
) -> None:
    ai._public_earnings_recent.clear()
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(repository.path))
    monkeypatch.setattr(ai, "_require_runtime_capability", lambda: None)

    async def snapshot():
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "sector": "Technology",
                    "earnings_date": "2026-07-23",
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    "revenue_estimate": 90_000_000_000,
                    "revenue_actual": 92_000_000_000,
                    "release_status": "released",
                }
            ],
        }

    monkeypatch.setattr(
        earnings,
        "_read_current_upcoming_earnings_snapshot",
        snapshot,
    )
    app = FastAPI()

    @app.middleware("http")
    async def bind_retry_visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")
    path = (
        "/api/ai/earnings-impact/AAPL/reports/2026-07-23"
        "?year=2026&quarter=2"
    )
    headers = {"Origin": "http://localhost", "X-Optix-Action": "1"}

    first_response = client.post(path, json={"confirm": True}, headers=headers)
    assert first_response.status_code == 202
    first = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert first is not None

    with repository._connect() as connection:
        connection.execute(
            """UPDATE ai_jobs
               SET status=?,error_code=?,completed_at=CURRENT_TIMESTAMP,
                   updated_at=CURRENT_TIMESTAMP
               WHERE job_id=?""",
            (terminal_status, error_code, first["job_id"]),
        )
        connection.commit()

    retry_response = client.post(path, json={"confirm": True}, headers=headers)

    assert retry_response.status_code == 202
    retry = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert retry is not None
    assert retry["job_id"] != first["job_id"]
    assert retry["retry_of_job_id"] == first["job_id"]
    assert retry["execution_number"] == first["execution_number"] + 1
    assert retry["status"] == "pending"
    assert sum(map(len, ai._public_earnings_recent.values())) == 2
    ai._public_earnings_recent.clear()


def test_visitor_rate_limit_counts_unique_reports_but_reuses_exact_task() -> None:
    ai._public_earnings_recent.clear()
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "https",
            "path": "/api/ai/earnings-impact/AAPL/reports/2026-07-23",
            "headers": [],
            "client": ("203.0.113.70", 50000),
            "server": ("testserver", 443),
            "query_string": b"",
        }
    )
    payloads = [
        _final_payload(ticker, report_date=f"2026-07-{day:02d}")
        for ticker, day in (("AAPL", 23), ("MSFT", 24), ("NVDA", 25), ("META", 26))
    ]
    try:
        with request_owner_access_context(False):
            ai._reserve_public_earnings_submission(
                request,
                payloads[0],
                force_retry=False,
            )
            ai._reserve_public_earnings_submission(
                request,
                payloads[0],
                force_retry=False,
            )
            ai._reserve_public_earnings_submission(
                request,
                payloads[1],
                force_retry=False,
            )
            ai._reserve_public_earnings_submission(
                request,
                payloads[2],
                force_retry=False,
            )
            with pytest.raises(Exception) as blocked:
                ai._reserve_public_earnings_submission(
                    request,
                    payloads[3],
                    force_retry=False,
                )
        assert getattr(blocked.value, "status_code", None) == 429
        assert blocked.value.detail["code"] == "earnings_analysis_rate_limited"
    finally:
        ai._public_earnings_recent.clear()


def test_scheduled_actuals_without_pre_release_require_manual_analysis(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(repository.path)
    effective = SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(
            scheduled_analysis_enabled=True,
            lookahead_days=5,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )

    async def builder(_today):
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "earnings_date": "2026-07-23",
                    "days_until": -1,
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    "release_status": "released",
                }
            ],
        }

    task = EarningsAnalysisTask(
        "scheduled-final-without-pre-release",
        settings=settings,
        repository=repository,
        builder=builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )

    scheduled = asyncio.run(task())
    assert scheduled.details["queued"] == 0
    assert scheduled.details["skipped_final_without_pre_release"] == 1
    assert repository.latest_for_ticker("earnings_impact", "AAPL") is None

    manual = asyncio.run(task.run_for_actions([{"request_id": "manual"}]))
    assert manual.details["queued"] == 1
    stored = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert stored is not None
    assert json.loads(stored["payload_json"])["analysis_stage"] == (
        "post_release_manual"
    )


def test_scheduled_actuals_queue_final_and_lock_after_success(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(repository.path)
    effective = SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(
            scheduled_analysis_enabled=True,
            lookahead_days=30,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )

    async def builder(_today):
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "earnings_date": "2026-07-23",
                    "days_until": -1,
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    "release_status": "released",
                }
            ],
        }

    task = EarningsAnalysisTask(
        "scheduled-final",
        settings=settings,
        repository=repository,
        builder=builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: datetime(2026, 7, 24, tzinfo=timezone.utc).date(),
    )
    pre_payload = normalize_earnings_analysis_payload(
        {
            "ticker": "AAPL",
            "name": "Apple",
            "earnings_date": "2026-07-23",
            "year": 2026,
            "quarter": 2,
            "eps_estimate": 1.4,
            "release_status": "scheduled",
        },
        analysis_stage="pre_release",
    )
    pre, _ = _create(repository, pre_payload)
    claimed_pre = repository.claim_due("pre-release-worker", 60)
    repository.complete(
        claimed_pre["job_id"],
        "pre-release-worker",
        _result("AAPL"),
        {},
    )
    assert repository.public(repository.get_job(pre["job_id"]))["_locked"] is False
    later_pre_payload = normalize_earnings_analysis_payload(
        {
            "ticker": "AAPL",
            "name": "Apple",
            "earnings_date": "2026-07-23",
            "year": 2026,
            "quarter": 2,
            "eps_estimate": 1.45,
            "release_status": "scheduled",
        },
        analysis_stage="pre_release",
    )
    later_pre, later_created = _create(repository, later_pre_payload)
    assert later_created is True
    claimed_later = repository.claim_due("later-pre-release-worker", 60)
    assert claimed_later is not None
    repository.fail(
        claimed_later["job_id"],
        "later-pre-release-worker",
        "provider_failed",
    )
    assert claimed_later["job_id"] == later_pre["job_id"]

    first = asyncio.run(task())
    assert first.details["queued"] == 1
    row = repository.latest_for_ticker("earnings_impact", "AAPL")
    payload = json.loads(row["payload_json"])
    assert payload["analysis_stage"] == "post_release_final"
    assert row["priority"] == runtime.EARNINGS_FINAL_PRIORITY == 90

    claimed = repository.claim_due("scheduled-final-worker", 60)
    repository.complete(
        claimed["job_id"],
        "scheduled-final-worker",
        _result("AAPL"),
        {},
    )
    second = asyncio.run(task())
    assert second.details["queued"] == 0
    assert second.details["existing"] == 1
    final = repository.public(repository.get_job(row["job_id"]))
    assert final["_locked"] is True
    assert final["_final"] is True


def test_blocked_final_is_not_requeued_on_every_scheduled_cycle(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A budget-blocked finalization must not be duplicated each cycle.

    Production enqueued 3,330 earnings jobs in one day — up to 36 identical
    rows for a single report — because the "a row already exists" guard only
    covered the pre-release stage. Every scheduled pass re-queued each
    released report, and once the daily token budget was gone the duplicates
    piled up as budget_blocked rows.

    Payload deduplication cannot save this: ``market_cap`` rides along in the
    analysis payload and moves with the price, so every cycle hashes to a
    brand new job. The builder below drifts it the same way.
    """

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    settings = _settings(repository.path)
    effective = SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(
            scheduled_analysis_enabled=True,
            lookahead_days=30,
        ),
    )
    monkeypatch.setattr(
        runtime,
        "capability_status",
        lambda _settings: {"supported": True, "status": "supported"},
    )

    scans = {"count": 0}

    async def builder(_today):
        scans["count"] += 1
        return {
            "data_limited": False,
            "source_status": "active",
            "earnings": [
                {
                    "ticker": "AAPL",
                    "name": "Apple",
                    "earnings_date": "2026-07-23",
                    "days_until": -1,
                    "year": 2026,
                    "quarter": 2,
                    "eps_estimate": 1.4,
                    "eps_actual": 1.6,
                    # Market cap moves with the price on every scan, so each
                    # cycle would otherwise hash to a different job.
                    "market_cap": 3_000_000_000_000 + scans["count"],
                    "release_status": "released",
                }
            ],
        }

    now = datetime(2026, 7, 24, 9, 0, tzinfo=timezone.utc)
    task = EarningsAnalysisTask(
        "blocked-final",
        settings=settings,
        repository=repository,
        builder=builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: now.date(),
        now=lambda: now,
    )

    pre, _ = _create(
        repository,
        normalize_earnings_analysis_payload(
            {
                "ticker": "AAPL",
                "name": "Apple",
                "earnings_date": "2026-07-23",
                "year": 2026,
                "quarter": 2,
                "eps_estimate": 1.4,
                "release_status": "scheduled",
            },
            analysis_stage="pre_release",
        ),
    )
    claimed_pre = repository.claim_due("pre-release-worker", 60)
    repository.complete(
        claimed_pre["job_id"],
        "pre-release-worker",
        _result("AAPL"),
        {},
    )

    first = asyncio.run(task())
    assert first.details["queued"] == 1
    final_row = repository.latest_for_ticker("earnings_impact", "AAPL")
    assert json.loads(final_row["payload_json"])["analysis_stage"] == (
        "post_release_final"
    )

    # The daily token budget runs out while the finalization is queued.
    with repository._connect() as connection:  # noqa: SLF001 - test fixture
        connection.execute(
            """UPDATE ai_jobs
                   SET status='budget_blocked',
                       error_code='daily_token_limit_reached',
                       updated_at=?, completed_at=?
                 WHERE job_id=?""",
            (now.isoformat(), now.isoformat(), final_row["job_id"]),
        )
        connection.commit()

    for _ in range(3):
        again = asyncio.run(task())
        assert again.details["queued"] == 0
        assert again.details["existing"] == 1

    with repository._connect() as connection:  # noqa: SLF001 - test fixture
        total = connection.execute(
            "SELECT COUNT(*) FROM ai_jobs WHERE job_type='earnings_impact'",
        ).fetchone()[0]
    assert total == 2  # the pre-release run plus exactly one finalization

    # A new UTC day still lets the blocked finalization retry.
    next_day = now + timedelta(days=1)
    tomorrow = EarningsAnalysisTask(
        "blocked-final-next-day",
        settings=settings,
        repository=repository,
        builder=builder,
        runtime_settings_reader=lambda: effective,
        today=lambda: next_day.date(),
        now=lambda: next_day,
    )
    assert asyncio.run(tomorrow()).details["queued"] == 1


def test_failed_finalization_does_not_hide_the_completed_analysis(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The report endpoint must not present a dead end over a real result.

    A finalization that never produced a result — budget blocked, or failed
    terminally because the scheduled switch was off while it sat in the queue
    — is the newest row for the report. Reporting that as the report's state
    hid the completed pre-release analysis and left the card showing a raw
    "scheduled_analysis_disabled" failure with a retry that changed nothing.
    """

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(repository.path))

    pre, _ = _create(
        repository,
        normalize_earnings_analysis_payload(
            {
                "ticker": "AAPL",
                "name": "Apple",
                "earnings_date": "2026-07-23",
                "year": 2026,
                "quarter": 2,
                "eps_estimate": 1.4,
                "release_status": "scheduled",
            },
            analysis_stage="pre_release",
        ),
    )
    claimed_pre = repository.claim_due("pre-release-worker", 60)
    repository.complete(
        claimed_pre["job_id"],
        "pre-release-worker",
        _result("AAPL"),
        {},
    )

    final, _ = _create(repository, _final_payload())
    claimed_final = repository.claim_due("final-worker", 60)
    repository.fail(
        claimed_final["job_id"],
        "final-worker",
        "scheduled_analysis_disabled",
    )

    newest = repository.latest_for_report(
        "AAPL",
        json.loads(final["payload_json"])["report_id"],
    )
    assert newest["job_id"] == final["job_id"]
    assert newest["status"] == "failed"

    app = FastAPI()

    @app.middleware("http")
    async def bind_visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")
    response = client.get(
        "/api/ai/earnings-impact/AAPL/reports/2026-07-23?year=2026&quarter=2",
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["error_code"] is None
    assert body["result"] is not None
    assert body["result"]["ticker"] == "AAPL"
    assert body["_analysis_stage"] == "pre_release"
    # The preliminary analysis is not the locked final one.
    assert body["_final"] is False
    assert body["_finalization_in_progress"] is False


def test_report_lookup_can_reuse_analyses_written_before_report_ids(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Analyses predating report ids must stay reachable for one report.

    Four production reports were stuck because their completed analysis was
    written before report ids were bound to the payload. A strict report-id
    match hid them, so the card showed a failure over a real result and the
    scheduler kept skipping the finalization for want of a preliminary run.
    """

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    monkeypatch.setattr(ai, "_job_repository", lambda: repository)
    monkeypatch.setattr(ai, "get_settings", lambda: _settings(repository.path))

    legacy_payload = normalize_earnings_analysis_payload(
        {
            "ticker": "AAPL",
            "name": "Apple",
            "earnings_date": "2026-07-23",
            "eps_estimate": 1.4,
            "release_status": "scheduled",
        },
        analysis_stage="pre_release",
    )
    legacy_payload.pop("report_id", None)  # written before report-id binding
    legacy, _ = _create(repository, legacy_payload)
    claimed = repository.claim_due("legacy-worker", 60)
    repository.complete(claimed["job_id"], "legacy-worker", _result("AAPL"), {})

    report_id = str(_final_payload()["report_id"])
    # Strict matching still refuses the legacy row: enqueue decisions must not
    # widen, or a report id typo would silently reuse an unrelated analysis.
    assert repository.latest_for_report("AAPL", report_id) is None
    reused = repository.latest_for_report(
        "AAPL",
        report_id,
        analysis_stage="pre_release",
        status="completed",
        legacy_report_date="2026-07-23",
    )
    assert reused is not None
    assert reused["job_id"] == legacy["job_id"]
    # A different earnings date must not borrow it.
    assert repository.latest_for_report(
        "AAPL",
        report_id,
        status="completed",
        legacy_report_date="2026-07-24",
    ) is None

    final, _ = _create(repository, _final_payload())
    claimed_final = repository.claim_due("final-worker", 60)
    repository.fail(
        claimed_final["job_id"],
        "final-worker",
        "scheduled_analysis_disabled",
    )

    app = FastAPI()

    @app.middleware("http")
    async def bind_visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(ai.router)
    client = TestClient(app, base_url="http://localhost")
    response = client.get(
        "/api/ai/earnings-impact/AAPL/reports/2026-07-23?year=2026&quarter=2",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "completed"
    assert body["result"]["ticker"] == "AAPL"
