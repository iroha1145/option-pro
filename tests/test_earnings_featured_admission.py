from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import date
from types import SimpleNamespace

import pytest
from anthropic.types import Message
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import request_owner_access_context
from app.api import accounts as account_api, ai, earnings
from app.config import Settings
from app.personal_config import get_personal_config
from app.services import accounts
from app.services.ai_jobs import claude_provider, repository as storage, runtime, worker
from app.services.ai_jobs.models import normalize_earnings_analysis_payload
from app.services.ai_jobs.repository import AIJobRepository
from app.services.earnings_analysis_scope import is_featured_company
from app.worker.tasks import EarningsAnalysisTask


REPORT_DATE = "2026-10-15"
HEADERS = {"Origin": "http://localhost", "X-Optix-Action": "1"}


def settings(path):
    return Settings(
        _env_file=None, anthropic_api_key="test-only-claude", anthropic_base_url="",
        openai_api_key="test-only-openai", openai_base_url="",
        openai_job_db_path=path, openai_manual_cooldown_seconds=0,
        model_budget_enforce_limit=False,
    )


def effective():
    return SimpleNamespace(
        ai=SimpleNamespace(manual_analysis_enabled=True),
        earnings=SimpleNamespace(scheduled_analysis_enabled=True, lookahead_days=5),
    )


def row(ticker="SMALL", **values):
    return {
        "ticker": ticker, "name": ticker, "sector": "", "earnings_date": REPORT_DATE,
        "days_until": 4, "year": 2026, "quarter": 3, "eps_estimate": 1.0,
        **values,
    }


def payload(ticker="SMALL", **values):
    return normalize_earnings_analysis_payload(
        {key: value for key, value in row(ticker, **values).items() if key != "days_until"},
        analysis_stage="pre_release",
    )


def create_job(repo, ticker="SMALL", *, verified=False):
    data = payload(ticker)
    version, digest = runtime.schema_identity("earnings_impact", model=runtime.SONNET_EARNINGS_MODEL)
    return repo.create_job(
        job_type="earnings_impact", payload=data,
        model=runtime.SONNET_EARNINGS_MODEL, reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version, schema_sha256=digest, max_queued=200,
        earnings_featured_verified=verified,
    )


def response(ticker="SMALL"):
    result = {
        "output_language": "zh-CN", "ticker": ticker,
        "summary": "经营变化可能传导至供应链。", "expectation": "关注收入预期和管理层指引。",
        "impacted": [{"ticker": "QCOM", "name": "高通", "relation": "supplier",
                      "direction": "mixed", "reason": "供应链需求可能受到影响。"}],
    }
    return Message(
        id="msg_featured_test", type="message", role="assistant", model=runtime.SONNET_EARNINGS_MODEL,
        stop_reason="end_turn", content=[{"type": "text", "text": json.dumps(result, ensure_ascii=False)}],
        usage={"input_tokens": 100, "output_tokens": 20, "cache_creation_input_tokens": 0,
               "cache_read_input_tokens": 0,
               "cache_creation": {"ephemeral_5m_input_tokens": 0, "ephemeral_1h_input_tokens": 0},
               "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0}},
    )


@pytest.fixture
def api_context(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    store = accounts.AccountStore(tmp_path / "accounts.db")
    monkeypatch.setattr(accounts, "_store", store)
    monkeypatch.setattr(ai, "get_settings", lambda: settings(repo.path))
    monkeypatch.setattr(ai, "_job_repository", lambda: repo)
    monkeypatch.setattr(ai, "get_effective_runtime_settings", effective)
    monkeypatch.setattr(ai, "_public_earnings_recent", {})
    snapshot_rows = [row(), row("AAPL"), row("LARGE", market_cap=20_000_000_000)]

    async def snapshot(_observed=None):
        return {"data_limited": False, "source_status": "active", "earnings": snapshot_rows}

    monkeypatch.setattr(earnings, "_read_current_upcoming_earnings_snapshot", snapshot)
    # Simulate the gateway's already-validated owner decision. Real customer
    # cookies still use the account store's signed-in session resolution.
    monkeypatch.setattr(account_api, "request_is_owner_session", lambda request: request.headers.get("x-test-owner") == "1")
    app = FastAPI()

    @app.middleware("http")
    async def owner_context(request, call_next):
        with request_owner_access_context(request.headers.get("x-test-owner") == "1"):
            return await call_next(request)

    app.include_router(ai.router)
    with TestClient(app, base_url="http://localhost") as client:
        yield client, repo, store, snapshot_rows


def post(client, ticker="SMALL", **kwargs):
    return client.post(
        f"/api/ai/earnings-impact/{ticker}/reports/{REPORT_DATE}?year=2026&quarter=3",
        json={"confirm": True}, headers=HEADERS, **kwargs,
    )


@pytest.mark.parametrize("ticker", ["AAPL", "LARGE"])
def test_public_featured_uses_sonnet_and_stores_private_admission_metadata(api_context, ticker):
    client, repo, _store, _rows = api_context
    result = post(client, ticker)
    assert result.status_code == 202
    saved = repo.latest_for_ticker("earnings_impact", ticker)
    assert (saved["model"], saved["reasoning"]) == (runtime.SONNET_EARNINGS_MODEL, "xhigh")
    assert saved["earnings_featured_verified"] == 1
    assert "earnings_featured_verified" not in saved["payload_json"]
    assert not {"earnings_featured_verified", "job_id", "usage", "budget_charge_usd"} & result.json().keys()
    assert result.json()["model"] == runtime.SONNET_EARNINGS_MODEL


def test_nonfeatured_and_forged_eligibility_cannot_enqueue(api_context):
    client, repo, _store, _rows = api_context
    rejected = post(client)
    assert rejected.status_code == 409
    assert rejected.json()["detail"]["code"] == "earnings_company_not_featured"
    forged = client.post(
        f"/api/ai/earnings-impact/SMALL/reports/{REPORT_DATE}",
        json={"confirm": True, "earnings_featured_verified": True, "public_featured": True},
        headers=HEADERS,
    )
    assert forged.status_code == 422
    assert repo.health()["pending"] == 0


def test_watchlist_eligibility_is_current_account_only_and_keeps_deduplication(api_context):
    client, repo, store, _rows = api_context
    alice = store.register("alice-scope", "test-passphrase-123!")
    bob = store.register("bob-scope", "test-passphrase-123!")
    store.replace_watchlist(alice.account.user_id, ["SMALL"])
    store.ensure_owner_account()
    store.replace_watchlist(accounts.OWNER_USER_ID, ["SMALL"])
    client.cookies.set(account_api.ACCOUNT_COOKIE_NAME, bob.token)
    # Owner access cannot borrow the owner's selections while a customer
    # account is active in the same browser.
    denied = client.post(
        f"/api/ai/earnings-impact/SMALL/reports/{REPORT_DATE}", json={"confirm": True},
        headers={**HEADERS, "x-test-owner": "1"},
    )
    assert denied.status_code == 409
    client.cookies.set(account_api.ACCOUNT_COOKIE_NAME, alice.token)
    assert post(client).status_code == 202
    first = repo.latest_for_ticker("earnings_impact", "SMALL")
    store.replace_watchlist(bob.account.user_id, ["SMALL"])
    client.cookies.set(account_api.ACCOUNT_COOKIE_NAME, bob.token)
    assert post(client).status_code == 202
    second = repo.latest_for_ticker("earnings_impact", "SMALL")
    assert second["job_id"] == first["job_id"]
    assert second["request_hash"] == first["request_hash"]
    assert repo.health()["pending"] == 1
    prepared = runtime.prepare_claude(runtime.settings_for_job(settings(repo.path), "earnings_impact"), "earnings_impact", json.loads(second["payload_json"]))
    serialized = json.dumps(prepared.params, ensure_ascii=False)
    for private in (alice.account.user_id, bob.account.user_id, "earnings_featured_verified"):
        assert private not in serialized
        assert private not in json.dumps(repo.public(second))


def test_deduplication_can_verify_existing_unsent_job_without_rewriting_identity(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    old, created = create_job(repo)
    assert created and old["earnings_featured_verified"] == 0
    admitted, created = create_job(repo, verified=True)
    assert not created
    assert admitted["earnings_featured_verified"] == 1
    assert {key: value for key, value in admitted.items() if key != "earnings_featured_verified"} == {
        key: value for key, value in old.items() if key != "earnings_featured_verified"
    }
    again, created = create_job(repo)
    assert not created and again["earnings_featured_verified"] == 1


@pytest.mark.parametrize("manual", [False, True])
def test_bulk_analysis_excludes_nonfeatured_and_other_accounts_watchlists(api_context, manual):
    _client, repo, store, _rows = api_context
    store.ensure_owner_account()
    store.replace_watchlist(accounts.OWNER_USER_ID, ["OWN"])
    customer = store.register("customer-scope", "test-passphrase-123!")
    store.replace_watchlist(customer.account.user_id, ["OTHER"])
    task = EarningsAnalysisTask(
        "featured-test", settings=settings(repo.path), repository=repo,
        builder=lambda _today: {
            "data_limited": False, "source_status": "active",
            "earnings": [row("AAPL"), row("LARGE", market_cap=20_000_000_000), row("OWN"), row("OTHER"), row()],
        },
        runtime_settings_reader=effective, today=lambda: date(2026, 10, 11),
    )
    result = asyncio.run(task._run(manual=manual))
    assert result.details["queued"] == 3
    assert result.details["skipped_nonfeatured"] == 2
    for ticker in ("AAPL", "LARGE", "OWN"):
        saved = repo.latest_for_ticker("earnings_impact", ticker)
        assert saved["model"] == runtime.SONNET_EARNINGS_MODEL
        assert saved["earnings_featured_verified"] == 1
    assert repo.latest_for_ticker("earnings_impact", "OTHER") is None
    assert repo.latest_for_ticker("earnings_impact", "SMALL") is None


def test_unsubmitted_nonfeatured_job_never_prepares_or_calls_provider(api_context, monkeypatch):
    _client, repo, _store, _rows = api_context
    job, _ = create_job(repo)

    def forbidden(*_args, **_kwargs):
        pytest.fail("an ineligible job must not prepare or submit a paid request")

    monkeypatch.setattr(runtime, "prepare_claude", forbidden)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "worker")) == 1
    saved = repo.get_job(job["job_id"])
    assert saved["error_code"] == "earnings_company_not_featured"
    assert saved["submission_started_at"] is None
    assert saved["attempt_count"] == 0
    assert saved["budget_charge_microusd"] == 0


@pytest.mark.parametrize("eligibility", ["public", "owner", "admitted"])
def test_verified_or_legacy_featured_job_reaches_provider(api_context, monkeypatch, eligibility):
    _client, repo, store, _rows = api_context
    ticker = "AAPL" if eligibility == "public" else "SMALL"
    if eligibility == "owner":
        store.ensure_owner_account()
        store.replace_watchlist(accounts.OWNER_USER_ID, [ticker])
    job, _ = create_job(repo, ticker, verified=eligibility == "admitted")
    calls = []

    async def stream(prepared, *, on_message_start, on_progress):
        calls.append(prepared)
        reply = response(ticker)
        await on_message_start(reply.id)
        return reply

    monkeypatch.setattr(claude_provider, "stream_earnings", stream)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "worker")) == 1
    saved = repo.get_job(job["job_id"])
    assert saved["status"] == "completed", saved["error_code"]
    assert len(calls) == 1
    assert saved["attempt_count"] == 1
    assert saved["budget_charge_microusd"] == 400
    assert repo.get_provider_result(job["job_id"])["id"] == "msg_featured_test"


def test_nonfeatured_paid_receipt_still_completes_and_remains_publicly_readable(api_context, monkeypatch):
    client, repo, _store, _rows = api_context
    job, _ = create_job(repo)
    receipt = runtime.claude_receipt(response())
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            """UPDATE ai_jobs SET status='in_progress',submission_started_at=?,submitted_at=?,
               anthropic_message_id=?,provider_result_json=?,budget_charge_microusd=? WHERE job_id=?""",
            (storage._iso(), storage._iso(), receipt["id"], json.dumps(receipt), 4_513_216, job["job_id"]),
        )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("a persisted paid receipt must never generate another request")

    monkeypatch.setattr(claude_provider, "stream_earnings", forbidden)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "worker")) == 1
    saved = repo.get_job(job["job_id"])
    assert saved["status"] == "completed"
    assert saved["earnings_featured_verified"] == 0
    assert saved["budget_charge_microusd"] == 400
    historical = client.get(f"/api/ai/earnings-impact/SMALL/reports/{REPORT_DATE}?year=2026&quarter=3")
    assert historical.status_code == 200
    assert historical.json()["result"]["ticker"] == "SMALL"
    assert historical.json()["model"] == runtime.SONNET_EARNINGS_MODEL
    assert post(client).status_code == 409


def test_nonfeatured_unknown_submission_is_not_reclassified_or_resubmitted(api_context, monkeypatch):
    _client, repo, _store, _rows = api_context
    job, _ = create_job(repo)
    with sqlite3.connect(repo.path) as connection:
        connection.execute(
            "UPDATE ai_jobs SET status='in_progress',submission_started_at=?,budget_charge_microusd=4513216 WHERE job_id=?",
            (storage._iso(), job["job_id"]),
        )

    def forbidden(*_args, **_kwargs):
        pytest.fail("unknown submitted work must not be prepared again")

    monkeypatch.setattr(runtime, "prepare_claude", forbidden)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "worker")) == 1
    saved = repo.get_job(job["job_id"])
    assert saved["error_code"] == "submission_outcome_unknown"
    assert saved["budget_charge_microusd"] == 4_513_216


def test_featured_threshold_and_unknown_market_cap():
    threshold = get_personal_config().earnings.featured_market_cap_usd
    assert is_featured_company(row(market_cap=threshold))
    assert not is_featured_company(row(market_cap=threshold - 1))
    assert not is_featured_company(row(market_cap=None))
    assert not is_featured_company(row(market_cap=float("nan")))
    assert is_featured_company(row(market_cap=None), personal_tickers=["small"])
