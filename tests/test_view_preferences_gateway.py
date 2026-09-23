from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import OwnerAccessRuntime, hash_owner_password
from app.api import accounts as accounts_api
from app.api import view_preferences as view_preferences_api
from app.main import _GatewayMiddleware
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, set_account_store
from app.services.view_preferences import ViewPreferenceStore

HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1"}


@pytest.fixture
def password_gateway(monkeypatch, tmp_path):
    set_account_store(AccountStore(tmp_path / "accounts.db"))
    accounts_api.reset_rate_limits()
    store = ViewPreferenceStore(tmp_path / "view-preferences.json")
    monkeypatch.setattr(view_preferences_api, "get_view_preference_store", lambda: store)
    runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("fixture-owner-password"),
    )
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(accounts_api.router)
    app.include_router(view_preferences_api.router)
    app.add_middleware(_GatewayMiddleware, access_runtime=runtime)
    with TestClient(app, base_url="https://localhost") as client:
        yield client
    set_account_store(None)
    accounts_api.reset_rate_limits()


def test_signed_in_customer_keeps_algorithm_choice_through_the_gateway(
    password_gateway: TestClient,
) -> None:
    registered = password_gateway.post(
        "/api/account/register",
        headers=HEADERS,
        json={"username": "radar-user", "password": "fixture-customer-password"},
    )
    assert registered.status_code == 201

    saved = password_gateway.put(
        "/api/view-preferences",
        headers=HEADERS,
        json={"radar_sort_algorithm": "t1_daily_priority"},
    )
    assert saved.status_code == 200
    assert saved.json()["persisted"] is True

    read = password_gateway.get("/api/view-preferences")
    assert read.status_code == 200
    assert read.json()["principal"].startswith("account:")
    assert read.json()["radar_sort_algorithm"] == "t1_daily_priority"


def test_anonymous_visitor_reads_defaults_and_cannot_save(
    password_gateway: TestClient,
) -> None:
    read = password_gateway.get("/api/view-preferences")
    assert read.status_code == 200
    assert read.json()["principal"] is None
    assert read.json()["persisted"] is False

    write = password_gateway.put(
        "/api/view-preferences",
        headers=HEADERS,
        json={"radar_sort_algorithm": "t1_daily_priority"},
    )
    assert write.status_code == 401
    assert write.json()["detail"]["code"] == "view_preferences_login_required"
