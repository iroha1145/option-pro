"""手动拉取的准入矩阵：owner / 登录客户 / 匿名。

需求（2026-08-07）：拉取不再是 owner 专属——登录客户即可发起；只有匿名
保持只读快照。visitor_live_pulls 开关仍可额外放开匿名（IP 限额）。
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
)
from app.api import accounts as accounts_api
from app.api import stocks
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, set_account_store

HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1"}


@pytest.fixture()
def store(tmp_path):
    created = AccountStore(tmp_path / "accounts.db")
    set_account_store(created)
    accounts_api.reset_rate_limits()
    yield created
    set_account_store(None)
    accounts_api.reset_rate_limits()


@pytest.fixture()
def pull_calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str | None]]:
    calls: list[tuple[str, str | None]] = []

    async def fake_pull(symbol: str, *, public_client_id: str | None = None):
        calls.append((symbol, public_client_id))
        return {"ticker": symbol, "status": "completed"}

    monkeypatch.setattr(stocks, "_coalesced_stock_pull", fake_pull)
    return calls


def _client(store: AccountStore, *, visitor_live_pulls: bool = False) -> TestClient:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password", visitor_live_pulls=visitor_live_pulls),
        password_hash=hash_owner_password("pull-access-test-password"),
    )
    app.include_router(
        stocks.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )
    app.include_router(accounts_api.router)
    return TestClient(app, base_url="https://localhost")


def test_anonymous_pull_requires_login_not_owner(
    store: AccountStore,
    pull_calls: list,
) -> None:
    client = _client(store)
    response = client.post("/api/stocks/AAOI/pull", json={}, headers=HEADERS)
    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "account_login_required"
    assert pull_calls == []


def test_signed_in_customer_can_pull_with_account_scoped_budget(
    store: AccountStore,
    pull_calls: list,
) -> None:
    client = _client(store)
    registered = client.post(
        "/api/account/register",
        json={"username": "carol", "password": "pw"},
        headers=HEADERS,
    )
    assert registered.status_code == 201

    response = client.post("/api/stocks/AAOI/pull", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["ticker"] == "AAOI"
    assert len(pull_calls) == 1
    symbol, budget_key = pull_calls[0]
    assert symbol == "AAOI"
    # 登录客户按账号限额（换 IP 不重置），不是 owner（None）也不是 IP。
    assert budget_key is not None and budget_key.startswith("acct:")


def test_visitor_live_pulls_flag_still_admits_anonymous_with_ip_budget(
    store: AccountStore,
    pull_calls: list,
) -> None:
    client = _client(store, visitor_live_pulls=True)
    response = client.post("/api/stocks/AAOI/pull", json={}, headers=HEADERS)
    assert response.status_code == 200
    assert len(pull_calls) == 1
    _, budget_key = pull_calls[0]
    assert budget_key is not None and not budget_key.startswith("acct:")
