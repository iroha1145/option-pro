"""Personal chart-drawing store and /api/account/chart-drawings HTTP contract."""

from __future__ import annotations

import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import (
    AccessConfig,
    OwnerAccessRuntime,
    hash_owner_password,
    request_owner_access_context,
)
from app.api import access as access_api
from app.api import accounts as accounts_api
from app.services import accounts as accounts_mod
from app.services.accounts import (
    AccountError,
    AccountStore,
    DRAWINGS_PER_RANGE_MAX,
    OWNER_USER_ID,
    set_account_store,
)

HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1"}
OWNER_PASSWORD = "owner-password-for-drawing-tests"

_OLD_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    user_id TEXT PRIMARY KEY,
    username TEXT NOT NULL,
    username_key TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS account_sessions (
    token_sha256 TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS account_watchlist (
    user_id TEXT NOT NULL REFERENCES accounts(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    position INTEGER NOT NULL,
    added_at TEXT NOT NULL,
    PRIMARY KEY (user_id, ticker)
);
"""


@pytest.fixture()
def store(tmp_path) -> AccountStore:
    created = AccountStore(tmp_path / "accounts.db")
    set_account_store(created)
    accounts_api.reset_rate_limits()
    yield created
    set_account_store(None)
    accounts_api.reset_rate_limits()


@pytest.fixture()
def client(store: AccountStore) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(accounts_api.router)
    app.include_router(access_api.router)
    return TestClient(app, base_url="https://localhost")


@pytest.fixture()
def owner_client(store: AccountStore) -> TestClient:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password(OWNER_PASSWORD),
    )
    app.include_router(accounts_api.router)
    app.include_router(access_api.router)
    return TestClient(app, base_url="https://localhost")


def _register(client: TestClient, username: str, password: str):
    return client.post(
        "/api/account/register",
        json={"username": username, "password": password},
        headers=HEADERS,
    )


def _owner_login(client: TestClient) -> None:
    response = client.post(
        "/api/access/login",
        json={"password": OWNER_PASSWORD},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text


def _drawing(**overrides):
    body = {
        "schemaVersion": 1,
        "id": str(uuid.uuid4()),
        "ticker": "NVDA",
        "range": "1d",
        "adjustment": "raw",
        "kind": "horizontal",
        "anchors": [
            {
                "time": "2026-07-06T13:30:00Z",
                "barKey": "2026-07-06",
                "price": 120.5,
            }
        ],
        "style": {"color": "#2E46E0", "width": 2, "dash": "solid"},
        "locked": False,
        "hidden": False,
        "zOrder": 0,
    }
    body.update(overrides)
    return body


def _create(client: TestClient, body: dict | None = None):
    return client.post(
        "/api/account/chart-drawings",
        json=body or _drawing(),
        headers=HEADERS,
    )


def test_initialize_is_idempotent_and_enables_wal_fk(store: AccountStore) -> None:
    store.initialize()
    store.initialize()
    with sqlite3.connect(store.path) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        assert "account_chart_drawings" in names
    # New connections from the store always set WAL + FK.
    with store._connect() as connection:
        journal = connection.execute("PRAGMA journal_mode").fetchone()[0]
        fks = connection.execute("PRAGMA foreign_keys").fetchone()[0]
        timeout = connection.execute("PRAGMA busy_timeout").fetchone()[0]
    assert str(journal).lower() == "wal"
    assert int(fks) == 1
    assert int(timeout) >= 10000


def test_old_database_gains_drawings_table_in_place(tmp_path) -> None:
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(_OLD_SCHEMA)
        connection.execute(
            """INSERT INTO accounts
                   (user_id, username, username_key, password_hash, created_at)
               VALUES ('usr_legacy', 'legacy', 'legacy', 'x', '2026-01-01T00:00:00+00:00')"""
        )
        connection.commit()
    upgraded = AccountStore(path)
    upgraded.initialize()
    with sqlite3.connect(path) as connection:
        names = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        count = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert "account_chart_drawings" in names
    assert count == 1


def test_guest_cannot_read_or_write_drawings(client: TestClient) -> None:
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert listed.status_code == 401
    created = _create(client)
    assert created.status_code == 401


def test_mutating_requests_require_same_origin(client: TestClient) -> None:
    _register(client, "alice", "pw")
    body = _drawing()
    missing = client.post("/api/account/chart-drawings", json=body)
    assert missing.status_code == 403
    assert missing.json()["detail"]["code"] == "same_origin_required"
    form = client.post(
        "/api/account/chart-drawings",
        data="kind=horizontal",
        headers={**HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert form.status_code == 415


def test_crud_round_trip_and_cache_control(client: TestClient) -> None:
    _register(client, "bob", "pw")
    body = _drawing()
    created = _create(client, body)
    assert created.status_code == 201
    assert created.headers.get("cache-control") == "no-store"
    payload = created.json()
    assert payload["id"] == body["id"]
    assert payload["kind"] == "horizontal"
    assert payload["revision"] == 1
    assert payload["anchors"][0]["price"] == 120.5
    assert payload["anchors"][0]["time"].startswith("2026-07-06")
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert listed.status_code == 200
    assert listed.headers.get("cache-control") == "no-store"
    assert listed.json()["drawings"][0]["id"] == body["id"]
    assert listed.json()["max_per_range"] == DRAWINGS_PER_RANGE_MAX

    updated = client.put(
        f"/api/account/chart-drawings/{body['id']}",
        json={
            **body,
            "revision": 1,
            "style": {"color": "#E5484D", "width": 3, "dash": "dashed"},
        },
        headers=HEADERS,
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2
    assert updated.json()["style"]["color"] == "#E5484D"
    deleted = client.delete(
        f"/api/account/chart-drawings/{body['id']}",
        headers=HEADERS,
    )
    assert deleted.status_code == 200
    empty = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert empty.json()["drawings"] == []


def test_stale_revision_returns_409_twice(client: TestClient) -> None:
    _register(client, "carol", "pw")
    body = _drawing()
    created = _create(client, body)
    assert created.json()["revision"] == 1
    first = client.put(
        f"/api/account/chart-drawings/{body['id']}",
        json={**body, "revision": 1, "locked": True},
        headers=HEADERS,
    )
    assert first.status_code == 200
    assert first.json()["revision"] == 2
    stale = client.put(
        f"/api/account/chart-drawings/{body['id']}",
        json={**body, "revision": 1, "locked": False},
        headers=HEADERS,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "revision_conflict"
    # Current server body is still revision 2; a second stale write still 409.
    again = client.put(
        f"/api/account/chart-drawings/{body['id']}",
        json={**body, "revision": 1},
        headers=HEADERS,
    )
    assert again.status_code == 409
    current = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert current.json()["drawings"][0]["revision"] == 2
    assert current.json()["drawings"][0]["locked"] is True


def test_scoped_bulk_clear_does_not_touch_other_ranges(client: TestClient) -> None:
    _register(client, "dana", "pw")
    daily = _drawing()
    weekly = _drawing(range="1w", ticker="NVDA")
    other = _drawing(ticker="AAPL")
    assert _create(client, daily).status_code == 201
    assert _create(client, weekly).status_code == 201
    assert _create(client, other).status_code == 201
    cleared = client.delete(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
        headers=HEADERS,
    )
    assert cleared.status_code == 200
    assert cleared.json()["deleted"] == 1
    nvda_week = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1w", "adjustment": "raw"},
    )
    aapl = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "AAPL", "range": "1d", "adjustment": "raw"},
    )
    assert len(nvda_week.json()["drawings"]) == 1
    assert len(aapl.json()["drawings"]) == 1


def test_customer_drawings_are_isolated(client: TestClient, store: AccountStore) -> None:
    _register(client, "erin", "pw")
    first = _drawing()
    assert _create(client, first).status_code == 201
    client.post("/api/account/logout", headers=HEADERS)
    _register(client, "frank", "pw")
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert listed.json()["drawings"] == []
    stolen = client.put(
        f"/api/account/chart-drawings/{first['id']}",
        json={**first, "revision": 1},
        headers=HEADERS,
    )
    assert stolen.status_code == 404
    removed = client.delete(
        f"/api/account/chart-drawings/{first['id']}",
        headers=HEADERS,
    )
    assert removed.status_code == 404
    # Original owner still has the row.
    erin = store.authenticate("erin", "pw")
    assert store.get_drawing(erin.account.user_id, first["id"]) is not None


def test_owner_drawings_are_isolated_from_customers(
    owner_client: TestClient,
    store: AccountStore,
) -> None:
    _owner_login(owner_client)
    owned = _drawing(ticker="MSFT")
    created = _create(owner_client, owned)
    assert created.status_code == 201
    assert _register(owner_client, "gina", "pw-for-gina").status_code == 201
    listed = owner_client.get(
        "/api/account/chart-drawings",
        params={"ticker": "MSFT", "range": "1d", "adjustment": "raw"},
    )
    assert listed.json()["drawings"] == []
    owner_client.cookies.delete(accounts_api.ACCOUNT_COOKIE_NAME)
    still = owner_client.get(
        "/api/account/chart-drawings",
        params={"ticker": "MSFT", "range": "1d", "adjustment": "raw"},
    )
    assert still.json()["drawings"][0]["id"] == owned["id"]
    assert store.list_drawings(OWNER_USER_ID, "MSFT", "1d")[0]["id"] == owned["id"]


def test_cookie_wins_over_owner_session(owner_client: TestClient) -> None:
    _owner_login(owner_client)
    owner_body = _drawing(ticker="OWNR")
    assert _create(owner_client, owner_body).status_code == 201
    assert _register(owner_client, "hank", "pw-for-hank").status_code == 201
    customer_body = _drawing(ticker="CUST")
    assert _create(owner_client, customer_body).status_code == 201
    customer_list = owner_client.get(
        "/api/account/chart-drawings",
        params={"ticker": "CUST", "range": "1d", "adjustment": "raw"},
    )
    assert customer_list.json()["drawings"][0]["id"] == customer_body["id"]
    owner_view = owner_client.get(
        "/api/account/chart-drawings",
        params={"ticker": "OWNR", "range": "1d", "adjustment": "raw"},
    )
    assert owner_view.json()["drawings"] == []


def test_extra_forbid_and_illegal_fields(client: TestClient) -> None:
    _register(client, "ivy", "pw")
    extra = _drawing()
    extra["option"] = {"series": []}
    response = _create(client, extra)
    assert response.status_code == 422

    bad_ticker = _create(client, _drawing(ticker="not a ticker"))
    assert bad_ticker.status_code == 400
    assert bad_ticker.json()["detail"]["code"] == "invalid_ticker"

    bad_range = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "2d", "adjustment": "raw"},
    )
    assert bad_range.status_code == 422

    bad_kind = _drawing(kind="pitchfork")
    assert _create(client, bad_kind).status_code == 422

    two_anchors = _drawing(
        kind="horizontal",
        anchors=[
            {"time": "2026-07-06T13:30:00Z", "barKey": "2026-07-06", "price": 10},
            {"time": "2026-07-07T13:30:00Z", "barKey": "2026-07-07", "price": 11},
        ],
    )
    assert _create(client, two_anchors).status_code == 400
    assert _create(client, two_anchors).json()["detail"]["code"] == "invalid_anchors"


def test_nan_inf_and_nonpositive_prices_are_rejected(
    client: TestClient, store: AccountStore
) -> None:
    from app.services.accounts import validate_drawing_payload

    session = store.register("jude-store", "pw")
    for price in (float("nan"), float("inf"), float("-inf"), -1.0, 0.0):
        with pytest.raises(AccountError) as excinfo:
            store.create_drawing(
                session.account.user_id,
                _drawing(
                    anchors=[
                        {
                            "time": "2026-07-06T13:30:00Z",
                            "barKey": "2026-07-06",
                            "price": price,
                        }
                    ]
                ),
            )
        assert excinfo.value.code == "invalid_price"
        with pytest.raises(AccountError):
            validate_drawing_payload(
                _drawing(
                    anchors=[
                        {
                            "time": "2026-07-06T13:30:00Z",
                            "barKey": "2026-07-06",
                            "price": price,
                        }
                    ]
                )
            )
    _register(client, "jude", "pw")
    for price in (-1.0, 0.0):
        response = _create(
            client,
            _drawing(
                anchors=[
                    {
                        "time": "2026-07-06T13:30:00Z",
                        "barKey": "2026-07-06",
                        "price": price,
                    }
                ]
            ),
        )
        assert response.status_code in {400, 422}, price


def test_overlong_text_and_illegal_color(client: TestClient) -> None:
    _register(client, "kara", "pw")
    long_text = _drawing(kind="text", text="字" * 241)
    assert _create(client, long_text).status_code == 422
    html = _drawing(kind="text", text="<script>alert(1)</script>")
    rejected = _create(client, html)
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "invalid_text"
    color = _drawing(style={"color": "red", "width": 2, "dash": "solid"})
    assert _create(client, color).status_code == 400
    css = _drawing(style={"color": "url(https://evil)", "width": 2, "dash": "solid"})
    assert _create(client, css).status_code in {400, 422}


def test_per_range_cap(store: AccountStore) -> None:
    session = store.register("leo", "pw")
    for index in range(DRAWINGS_PER_RANGE_MAX):
        store.create_drawing(
            session.account.user_id,
            _drawing(id=str(uuid.uuid4()), zOrder=index),
        )
    with pytest.raises(AccountError) as excinfo:
        store.create_drawing(session.account.user_id, _drawing())
    assert excinfo.value.code == "drawings_range_full"


def test_payload_size_cap(monkeypatch: pytest.MonkeyPatch, store: AccountStore) -> None:
    monkeypatch.setattr(accounts_mod, "DRAWING_PAYLOAD_MAX_BYTES", 80)
    session = store.register("mia", "pw")
    with pytest.raises(AccountError) as excinfo:
        store.create_drawing(
            session.account.user_id,
            _drawing(kind="text", text="a" * 40),
        )
    assert excinfo.value.code == "payload_too_large"


def test_account_delete_cascades_drawings(store: AccountStore) -> None:
    session = store.register("nina", "pw")
    created = store.create_drawing(session.account.user_id, _drawing())
    store.delete_account(session.account.user_id)
    with store._connect() as connection:
        leftover = connection.execute(
            "SELECT COUNT(*) FROM account_chart_drawings WHERE drawing_id=?",
            (created["id"],),
        ).fetchone()[0]
    assert leftover == 0


def test_cannot_reuse_another_users_id(store: AccountStore) -> None:
    first = store.register("omar", "pw")
    second = store.register("pia", "pw")
    body = _drawing()
    store.create_drawing(first.account.user_id, body)
    with pytest.raises(AccountError) as excinfo:
        store.create_drawing(second.account.user_id, body)
    assert excinfo.value.code == "drawing_forbidden"


def test_create_then_get_body_twice(client: TestClient) -> None:
    """Verification step 7: POST then GET, twice, asserting the body."""

    _register(client, "quin", "pw")
    for _ in range(2):
        body = _drawing()
        created = _create(client, body)
        assert created.status_code == 201
        assert created.json()["id"] == body["id"]
        assert created.json()["kind"] == "horizontal"
        assert created.json()["revision"] == 1
        assert created.json()["anchors"][0]["price"] == 120.5
        listed = client.get(
            "/api/account/chart-drawings",
            params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
        )
        match = next(item for item in listed.json()["drawings"] if item["id"] == body["id"])
        assert match["anchors"][0]["time"].startswith("2026-07-06")
        assert match["revision"] == 1
        stale = client.put(
            f"/api/account/chart-drawings/{body['id']}",
            json={**body, "revision": 99},
            headers=HEADERS,
        )
        assert stale.status_code == 409


def _replace(client: TestClient, drawings: list, ticker="NVDA", chart_range="1d"):
    return client.post(
        "/api/account/chart-drawings/replace",
        params={"ticker": ticker, "range": chart_range, "adjustment": "raw"},
        json={"schemaVersion": 1, "drawings": drawings},
        headers=HEADERS,
    )


def test_replace_current_scope_is_transactional(client: TestClient) -> None:
    _register(client, "rhea", "pw")
    original = _drawing()
    assert _create(client, original).status_code == 201
    replacement = _drawing(kind="segment", anchors=[
        {"time": "2026-07-06T13:30:00Z", "barKey": "2026-07-06", "price": 10},
        {"time": "2026-07-07T13:30:00Z", "barKey": "2026-07-07", "price": 12},
    ])
    replaced = _replace(client, [replacement])
    assert replaced.status_code == 200, replaced.text
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    ids = [row["id"] for row in listed.json()["drawings"]]
    kinds = [row["kind"] for row in listed.json()["drawings"]]
    assert original["id"] not in ids
    assert kinds == ["segment"]
    assert listed.json()["drawings"][0]["anchors"][1]["price"] == 12

    empty = _replace(client, [])
    assert empty.status_code == 200
    after_empty = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert after_empty.json()["drawings"] == []


def test_replace_partial_invalid_leaves_previous_set(client: TestClient) -> None:
    _register(client, "seth", "pw")
    original = _drawing()
    assert _create(client, original).status_code == 201
    bad = _drawing(
        kind="horizontal",
        anchors=[
            {"time": "2026-07-06T13:30:00Z", "barKey": "2026-07-06", "price": 10},
            {"time": "2026-07-07T13:30:00Z", "barKey": "2026-07-07", "price": 11},
        ],
    )
    response = _replace(client, [bad])
    assert response.status_code in {400, 422}
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert listed.json()["drawings"][0]["id"] == original["id"]


def test_replace_mints_id_when_another_account_holds_it(
    store: AccountStore, client: TestClient
) -> None:
    other = store.register("other-owner", "pw")
    stolen = _drawing()
    store.create_drawing(other.account.user_id, stolen)
    _register(client, "uma", "pw")
    response = _replace(client, [stolen])
    assert response.status_code == 200, response.text
    minted = response.json()["drawings"]
    assert len(minted) == 1
    assert minted[0]["id"] != stolen["id"]
    leftover = store.get_drawing(other.account.user_id, stolen["id"])
    assert leftover is not None
