"""Real gateway, account cookies, durable jobs, worker, refreshed report."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (OwnerAccessRuntime, hash_owner_password,
                        require_public_read_or_owner_access, require_same_origin_action)
from app.api import ai, signals
from app.api.accounts import ACCOUNT_COOKIE_NAME
from app.main import _GatewayMiddleware, _rl_buckets
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, set_account_store
from app.services.ai_jobs import claude_provider, worker
from app.services.ai_jobs.repository import AIJobRepository
from app.services.runtime_settings import RuntimeSettings
from test_stock_ai_model import settings, message


HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1", "Sec-Fetch-Site": "same-origin"}


@pytest.fixture
def app_state(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    store = AccountStore(tmp_path / "accounts.db")
    set_account_store(store)
    repo = AIJobRepository(tmp_path / "jobs.db")
    config = settings(repo.path)
    effective = RuntimeSettings()
    monkeypatch.setattr(ai, "_job_repository", lambda: repo)
    monkeypatch.setattr(signals, "_job_repository", lambda: repo)
    monkeypatch.setattr(ai, "get_settings", lambda: config)
    monkeypatch.setattr(signals, "get_settings", lambda: config)
    monkeypatch.setattr(ai, "get_effective_runtime_settings", lambda: effective)
    monkeypatch.setattr(signals, "get_effective_runtime_settings", lambda: effective)
    async def evidence(_ticker):
        return {"rsi14": {"value": 55}}, None, None
    async def context(_ticker):
        return {"blocks": {}, "status": {}, "context_tickers": []}
    monkeypatch.setattr(signals, "_owner_stock_signal_evidence", evidence)
    monkeypatch.setattr(signals, "build_signal_context", context)
    monkeypatch.setattr(signals, "compute_stock_scores", lambda _data: {})
    app = FastAPI()
    access = OwnerAccessRuntime(AccessConfig(mode="password"), password_hash=hash_owner_password("test-owner-stock-password"))
    app.state.access_runtime = access
    app.include_router(signals.router, dependencies=[Depends(require_public_read_or_owner_access)])
    app.include_router(ai.router, dependencies=[Depends(require_public_read_or_owner_access), Depends(require_same_origin_action)])
    app.add_middleware(_GatewayMiddleware, access_runtime=access)
    _rl_buckets.clear()
    yield SimpleNamespace(app=app, store=store, repo=repo, settings=config, effective=effective, access=access)
    set_account_store(None)
    _rl_buckets.clear()


def client_for(state, username=None):
    client = TestClient(state.app, base_url="https://localhost")
    account = None
    if username:
        session = state.store.register(username, "stock-analysis-test-password")
        account = session.account
        client.cookies.set(ACCOUNT_COOKIE_NAME, session.token)
    return client, account


def test_customer_enqueue_worker_persist_reload_and_global_account_limit(app_state, monkeypatch):
    state = app_state
    client, account = client_for(state, "stock-reader")
    initial = client.get("/api/signals/stock/AMD/ai-analysis")
    assert initial.status_code == 200
    assert initial.json()["can_generate"] is True
    assert "no-store" in initial.headers["cache-control"]
    accepted = client.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS)
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["quota"]["remaining"] == 0 and body["cancellable"] is False
    assert body["model"] == "claude-sonnet-5-5" and body["reasoning"] == "xhigh"
    stored = state.repo.get_job(body["job_id"])
    assert str(account.user_id) not in stored["payload_json"]
    assert client.post("/api/signals/stock/MSFT/ai-analysis", json={}, headers=HEADERS).status_code == 429
    calls = []
    async def stream(prepared, *, on_message_start=None, tool_counts=None):
        calls.append(prepared)
        await on_message_start("msg_stock")
        return message()
    monkeypatch.setattr(claude_provider, "stream_message", stream)
    asyncio.run(worker.run_once(state.repo, state.settings, "http-stock-worker"))
    assert len(calls) == 1
    # Replace the repository object to prove the refresh reads durable state.
    restarted = AIJobRepository(state.repo.path)
    monkeypatch.setattr(signals, "_job_repository", lambda: restarted)
    monkeypatch.setattr(ai, "_job_repository", lambda: restarted)
    refreshed = client.get("/api/signals/stock/AMD/ai-analysis").json()
    assert refreshed["active_job"] is None
    assert refreshed["latest_job"]["status"] == "completed"
    assert refreshed["latest_job"]["result"]["report"]["version"] == "stock-report-v1"
    assert refreshed["latest_job"]["usage"]["cache_creation_1h_input_tokens"] == 200
    assert client.get(accepted.headers["location"]).json()["result"]["report"]
    second, second_account = client_for(state, "stock-reader-two")
    assert second.get("/api/signals/stock/AMD/ai-analysis").json()["latest_job"]["job_id"] == body["job_id"]
    reuse = second.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS)
    assert reuse.status_code == 202 and reuse.json()["job_id"] == body["job_id"]
    assert restarted.stock_analysis_quota(str(second_account.user_id))["remaining"] == 1
    assert len(calls) == 1


def test_anonymous_forged_cookie_and_other_workflows_are_denied(app_state):
    anonymous, _ = client_for(app_state)
    for method in ["get", "post"]:
        response = getattr(anonymous, method)("/api/signals/stock/AMD/ai-analysis", **({"json": {}, "headers": HEADERS} if method == "post" else {}))
        assert response.status_code == 401
    anonymous.cookies.set(ACCOUNT_COOKIE_NAME, "forged-session")
    assert anonymous.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS).status_code == 401
    customer, _ = client_for(app_state, "scope-reader")
    assert customer.post("/api/signals/stock/AMD/ai-analysis", json={"force": True}, headers=HEADERS).status_code == 403
    queued = customer.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS).json()
    assert customer.post(f"/api/ai/jobs/{queued['job_id']}/cancel", json={"confirm": True}, headers=HEADERS).status_code == 401
    assert customer.post("/api/ai/jobs/option-alerts", json={}, headers=HEADERS).status_code == 401
    # A real opaque job id never grants access to other job types.
    with app_state.repo._connect() as connection:
        connection.execute("UPDATE ai_jobs SET job_type='news_impact' WHERE job_id=?", (queued["job_id"],))
        connection.commit()
    assert customer.get(f"/api/ai/jobs/{queued['job_id']}").status_code == 404


@pytest.mark.parametrize("path,method", [
    ("/api/signals/stock/AMD/ai-analysis", "delete"),
    ("/api/signals/stock/AMD/ai-analysis/extra", "post"),
    ("/api/signals/stock/AMD%2fai-analysis", "post"),
    ("/api/signals/stock/AMD%252fai-analysis", "post"),
    ("/api/signals/stock/AMD/ai-analysis/cancel", "post"),
])
def test_account_route_allowlist_is_exact(app_state, path, method):
    client, _ = client_for(app_state, "route-reader")
    response = getattr(client, method)(path, **({"json": {}, "headers": HEADERS} if method == "post" else {"headers": HEADERS}))
    assert response.status_code in {401, 403, 404, 405}
    assert app_state.repo.stock_analysis_quota(str(app_state.store.authenticate("route-reader", "stock-analysis-test-password").account.user_id))["remaining"] == 1


def test_cross_origin_and_stale_preflight_do_not_spend(app_state, monkeypatch):
    client, account = client_for(app_state, "preflight-reader")
    refused = client.post("/api/signals/stock/AMD/ai-analysis", json={}, headers={**HEADERS, "Origin": "https://elsewhere.example"})
    assert refused.status_code == 403
    async def stale(_ticker):
        return {}, {"fresh": False, "saved_at": 1_700_000_000}, "saved"
    monkeypatch.setattr(signals, "_owner_stock_signal_evidence", stale)
    assert client.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS).status_code == 409
    assert app_state.repo.stock_analysis_quota(str(account.user_id))["remaining"] == 1
    disabled = app_state.effective.model_copy(update={
        "ai": app_state.effective.ai.model_copy(update={"stock_analysis_enabled": False}),
    })
    monkeypatch.setattr(ai, "get_effective_runtime_settings", lambda: disabled)
    monkeypatch.setattr(signals, "get_effective_runtime_settings", lambda: disabled)
    assert client.get("/api/signals/stock/AMD/ai-analysis").json()["enabled"] is False
    assert client.post("/api/signals/stock/AMD/ai-analysis", json={}, headers=HEADERS).status_code == 409
