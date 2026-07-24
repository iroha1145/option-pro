from __future__ import annotations

import json
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import FastAPI
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

    first = asyncio.run(task())
    assert first.details["queued"] == 1
    row = repository.latest_for_ticker("earnings_impact", "AAPL")
    payload = json.loads(row["payload_json"])
    assert payload["analysis_stage"] == "post_release_final"

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
