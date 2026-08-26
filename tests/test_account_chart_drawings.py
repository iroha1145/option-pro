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

# 绘图功能上线前，开发库里 drawing_id 是全局主键；初始化时要能就地换掉。
_OLD_DRAWINGS_TABLE = """
CREATE TABLE IF NOT EXISTS account_chart_drawings (
    drawing_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL
        REFERENCES accounts(user_id) ON DELETE CASCADE,
    ticker TEXT NOT NULL,
    chart_range TEXT NOT NULL,
    adjustment TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    revision INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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


def test_legacy_global_drawing_id_primary_key_is_rebuilt(tmp_path) -> None:
    path = tmp_path / "legacy-pk.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(_OLD_SCHEMA)
        connection.executescript(_OLD_DRAWINGS_TABLE)
        connection.execute(
            """INSERT INTO accounts
                   (user_id, username, username_key, password_hash, created_at)
               VALUES ('usr_legacy', 'legacy', 'legacy', 'x', '2026-01-01T00:00:00+00:00')"""
        )
        connection.commit()
    upgraded = AccountStore(path)
    upgraded.initialize()
    with sqlite3.connect(path) as connection:
        key_columns = sorted(
            (int(row[5]), str(row[1]))
            for row in connection.execute("PRAGMA table_info(account_chart_drawings)")
            if int(row[5]) > 0
        )
        accounts_left = connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0]
    assert [name for _, name in key_columns] == ["user_id", "drawing_id"]
    assert accounts_left == 1


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
    assert stolen.json()["detail"]["code"] == "drawing_not_found"
    # 删除是幂等的，所以这里是 200；但 DELETE 带着 user_id 条件，删掉的是「自己
    # 名下这个编号」（不存在），原主人的行必须一动不动。
    removed = client.delete(
        f"/api/account/chart-drawings/{first['id']}",
        headers=HEADERS,
    )
    assert removed.status_code == 200
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


def test_account_cap_on_create(
    monkeypatch: pytest.MonkeyPatch, store: AccountStore
) -> None:
    monkeypatch.setattr(accounts_mod, "DRAWINGS_PER_ACCOUNT_MAX", 3)
    session = store.register("nate", "pw")
    for _ in range(3):
        store.create_drawing(session.account.user_id, _drawing())
    with pytest.raises(AccountError) as excinfo:
        store.create_drawing(session.account.user_id, _drawing())
    assert excinfo.value.code == "drawings_full"


def test_create_replay_returns_the_stored_row(client: TestClient) -> None:
    """重放创建必须是成功：报 409 的话客户端 outbox 就永远卡在这一条上。"""

    _register(client, "xena", "pw")
    body = _drawing()
    first = _create(client, body)
    assert first.status_code == 201
    replay = _create(client, body)
    assert replay.status_code == 201, replay.text
    assert replay.json() == first.json()
    # 重放不是隐式更新：正文变了也只返回已存的那一版。
    changed = _create(client, {**body, "locked": True})
    assert changed.status_code == 201
    assert changed.json()["locked"] is False
    assert changed.json()["revision"] == 1
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert len(listed.json()["drawings"]) == 1


def test_delete_is_idempotent(client: TestClient) -> None:
    """没有墓碑表，重放的删除和多设备重复删除都得算成功。"""

    _register(client, "yuri", "pw")
    body = _drawing()
    assert _create(client, body).status_code == 201
    for _ in range(3):
        removed = client.delete(
            f"/api/account/chart-drawings/{body['id']}",
            headers=HEADERS,
        )
        assert removed.status_code == 200
        assert removed.json() == {"ok": True}
    never_existed = client.delete(
        f"/api/account/chart-drawings/{uuid.uuid4()}",
        headers=HEADERS,
    )
    assert never_existed.status_code == 200


def test_moving_a_drawing_to_another_scope_is_rejected(client: TestClient) -> None:
    _register(client, "wade", "pw")
    body = _drawing()
    assert _create(client, body).status_code == 201
    for moved in ({"ticker": "AAPL"}, {"range": "1w"}):
        response = client.put(
            f"/api/account/chart-drawings/{body['id']}",
            json={**body, **moved, "revision": 1},
            headers=HEADERS,
        )
        assert response.status_code == 400, response.text
        assert response.json()["detail"]["code"] == "scope_mismatch"
    listed = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1d", "adjustment": "raw"},
    )
    assert listed.json()["drawings"][0]["revision"] == 1


def test_extreme_offset_time_is_invalid_not_a_crash(client: TestClient) -> None:
    """格式合法但换算到 UTC 会越界的时间：astimezone 抛 OverflowError，不能漏成 500。"""

    _register(client, "vera", "pw")
    for stamp in ("0001-01-01T00:00:00+00:01", "9999-12-31T23:59:59-00:01"):
        response = _create(
            client,
            _drawing(
                anchors=[
                    {"time": stamp, "barKey": "bar-0", "price": 120.5},
                ]
            ),
        )
        assert response.status_code == 400, (stamp, response.text)
        assert response.json()["detail"]["code"] == "invalid_time"


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


def test_owner_account_cannot_be_deleted(store: AccountStore) -> None:
    """owner 那行是 owner 全部个人数据的外键锚点，删掉等于清空。"""

    owner = store.ensure_owner_account()
    store.create_drawing(owner.user_id, _drawing(ticker="MSFT"))
    with pytest.raises(AccountError) as excinfo:
        store.delete_account(OWNER_USER_ID)
    assert excinfo.value.code == "account_delete_forbidden"
    assert len(store.list_drawings(OWNER_USER_ID, "MSFT", "1d")) == 1


def test_same_id_in_two_accounts_stays_isolated(store: AccountStore) -> None:
    """编号空间按账户隔离：同一个 id 两边各存一份，谁也读不到、改不到、删不掉对方。"""

    first = store.register("omar", "pw")
    second = store.register("pia", "pw")
    body = _drawing()
    mine = store.create_drawing(first.account.user_id, body)
    theirs = store.create_drawing(
        second.account.user_id, {**body, "ticker": "AAPL"}
    )
    assert mine["id"] == theirs["id"] == body["id"]
    assert store.get_drawing(first.account.user_id, body["id"])["ticker"] == "NVDA"
    assert store.get_drawing(second.account.user_id, body["id"])["ticker"] == "AAPL"

    # B 的更新只落在 B 自己那条上。
    store.update_drawing(
        second.account.user_id,
        body["id"],
        {**body, "ticker": "AAPL", "locked": True},
        expected_revision=1,
    )
    assert store.get_drawing(first.account.user_id, body["id"])["locked"] is False
    assert store.get_drawing(first.account.user_id, body["id"])["revision"] == 1

    # B 的删除同样只删掉 B 自己那条。
    store.delete_drawing(second.account.user_id, body["id"])
    assert store.get_drawing(second.account.user_id, body["id"]) is None
    assert store.get_drawing(first.account.user_id, body["id"]) is not None


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


def test_replace_keeps_id_another_account_already_holds(
    store: AccountStore, client: TestClient
) -> None:
    """别人占着同一个编号不再是冲突：主键带 user_id，两条行本来就不同。"""

    other = store.register("other-owner", "pw")
    shared = _drawing()
    store.create_drawing(other.account.user_id, shared)
    _register(client, "uma", "pw")
    response = _replace(client, [shared])
    assert response.status_code == 200, response.text
    saved = response.json()["drawings"]
    assert len(saved) == 1
    assert saved[0]["id"] == shared["id"]
    leftover = store.get_drawing(other.account.user_id, shared["id"])
    assert leftover is not None


def test_replace_mints_id_when_the_same_account_holds_it_elsewhere(
    client: TestClient,
) -> None:
    """本账户在别的周期占着这个编号才需要改发新号，否则插入会撞主键。"""

    _register(client, "vince", "pw")
    weekly = _drawing(range="1w")
    assert _create(client, weekly).status_code == 201
    response = _replace(client, [{**weekly, "range": "1d"}])
    assert response.status_code == 200, response.text
    minted = response.json()["drawings"]
    assert len(minted) == 1
    assert minted[0]["id"] != weekly["id"]
    still_weekly = client.get(
        "/api/account/chart-drawings",
        params={"ticker": "NVDA", "range": "1w", "adjustment": "raw"},
    )
    assert still_weekly.json()["drawings"][0]["id"] == weekly["id"]


def test_account_cap_counts_other_scopes_on_replace(
    monkeypatch: pytest.MonkeyPatch, store: AccountStore
) -> None:
    """``other_count + len(batch)`` 是这条路径上最绕的配额算式，钉住它的边界。"""

    monkeypatch.setattr(accounts_mod, "DRAWINGS_PER_ACCOUNT_MAX", 3)
    session = store.register("abe", "pw")
    user_id = session.account.user_id
    for _ in range(2):
        store.create_drawing(user_id, _drawing(ticker="AAPL"))
    kept = store.create_drawing(user_id, _drawing())
    with pytest.raises(AccountError) as excinfo:
        store.replace_drawings_in_scope(
            user_id, "NVDA", "1d", "raw", [_drawing(), _drawing()]
        )
    assert excinfo.value.code == "drawings_full"
    # 配额检查在 DELETE 之前，本作用域原有的行不能被这次失败吞掉。
    survivors = [row["id"] for row in store.list_drawings(user_id, "NVDA", "1d")]
    assert survivors == [kept["id"]]
    # 边界：2（其他作用域）+ 1 == 3，正好放得下。
    replaced = store.replace_drawings_in_scope(
        user_id, "NVDA", "1d", "raw", [_drawing()]
    )
    assert len(replaced) == 1
    assert replaced[0]["id"] != kept["id"]


def test_quota_409_carries_its_own_code(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
) -> None:
    """配额满和版本冲突都是 409，客户端只能靠 code 区分——绝不能都当成冲突。"""

    monkeypatch.setattr(accounts_mod, "DRAWINGS_PER_ACCOUNT_MAX", 1)
    _register(client, "bree", "pw")
    assert _create(client).status_code == 201
    full = _create(client)
    assert full.status_code == 409
    assert full.json()["detail"]["code"] == "drawings_full"
    batch = _replace(client, [_drawing(), _drawing()])
    assert batch.status_code == 409
    assert batch.json()["detail"]["code"] == "drawings_full"
