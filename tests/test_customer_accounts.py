from __future__ import annotations

import ipaddress
import sqlite3

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.access import request_owner_access_context
from app.api import access as access_api
from app.api import accounts as accounts_api
from app.services.accounts import (
    AccountError,
    AccountStore,
    WATCHLIST_MAX_TICKERS,
    hash_account_password,
    set_account_store,
    verify_account_password,
)

HEADERS = {"Origin": "https://localhost", "X-Optix-Action": "1"}


@pytest.fixture()
def store(tmp_path, monkeypatch: pytest.MonkeyPatch) -> AccountStore:
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
    # base_url https so the HTTPS gate is satisfied the same way production is.
    return TestClient(app, base_url="https://localhost")


def _register(client: TestClient, username: str, password: str):
    return client.post(
        "/api/account/register",
        json={"username": username, "password": password},
        headers=HEADERS,
    )


# ---------------- password handling ----------------


def test_passwords_are_hashed_with_owner_grade_stretching() -> None:
    """Legacy short-password hashes keep their original verification behavior."""

    encoded = hash_account_password("a")
    algorithm, iterations, salt, digest = encoded.split("$")
    assert algorithm == "pbkdf2_sha256"
    assert int(iterations) >= 240_000
    assert salt and digest
    # The stored value is a derivation, never the secret itself.
    assert "a" not in (algorithm, iterations, salt, digest)
    assert verify_account_password("a", encoded) is True
    assert verify_account_password("b", encoded) is False
    # Same salt, same password → same digest (the derivation is deterministic).
    from app.services.accounts import _b64decode as decode

    assert hash_account_password("a", salt=decode(salt)) == encoded


def test_same_password_gets_a_distinct_salt() -> None:
    assert hash_account_password("hunter2") != hash_account_password("hunter2")


@pytest.mark.parametrize("password", ["", "x" * 257, "with\x00null", "line\nbreak"])
def test_unstorable_passwords_are_refused(password: str) -> None:
    with pytest.raises(AccountError):
        hash_account_password(password)


@pytest.mark.parametrize("password", ["1", "x" * 14])
def test_new_accounts_reject_short_passwords_before_hashing(store, monkeypatch, password):
    def forbidden_hash(_password):
        raise AssertionError("short new passwords must be rejected before expensive hashing")

    monkeypatch.setattr("app.services.accounts.hash_account_password", forbidden_hash)
    with pytest.raises(AccountError) as captured:
        store.register("shorty", password)
    assert captured.value.code == "password_too_short"
    assert store.account_count() == 0


@pytest.mark.parametrize("password", ["a short phrase!", "all lowercase password", "春江潮水连海平海上明月共潮生啊", "x" * 256])
def test_registration_accepts_long_phrases_without_character_class_requirements(store, password):
    session = store.register("phrase-user", password)
    assert store.authenticate("phrase-user", password).account == session.account


@pytest.mark.parametrize("password, code", [
    ("x" * 257, "password_too_long"),
    ("passwordpassword", "password_too_common"),
    ("PasswordPassword", "password_too_common"),
    ("123456789012345", "password_too_common"),
    (" " * 15, "password_too_common"),
])
def test_registration_policy_rejects_oversize_and_known_common_passwords(store, password, code):
    with pytest.raises(AccountError) as captured:
        store.register("weak-password", password)
    assert captured.value.code == code
    assert store.account_count() == 0


def test_existing_short_password_account_can_still_log_in(client, store):
    account = store.register("legacy-user", "original-long-password").account
    # Model a row created before the registration policy changed. Do not weaken
    # the production registration path merely to create this legacy fixture.
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "UPDATE accounts SET password_hash=? WHERE user_id=?",
            (hash_account_password("1"), account.user_id),
        )
    response = client.post(
        "/api/access/login",
        json={"username": "legacy-user", "password": "1"},
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["account"]["username"] == "legacy-user"
    assert client.get("/api/account/me").json()["logged_in"] is True


@pytest.mark.parametrize("password, code", [
    ("1", "password_too_short"),
    ("passwordpassword", "password_too_common"),
])
def test_registration_http_returns_clear_password_policy_error(client, store, password, code):
    response = _register(client, "new-user", password)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == code
    assert response.json()["detail"]["message"]
    assert accounts_api.ACCOUNT_COOKIE_NAME not in response.cookies
    assert store.account_count() == 0


# ---------------- registration ----------------


def test_register_signs_in_and_sets_an_httponly_cookie(client: TestClient) -> None:
    response = _register(client, "alice", "fixture-password-for-tests")
    assert response.status_code == 201
    assert response.json()["username"] == "alice"
    cookie = response.headers["set-cookie"]
    assert accounts_api.ACCOUNT_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "Secure" in cookie
    assert "SameSite=strict" in cookie
    identity = client.get("/api/account/me").json()
    assert identity["logged_in"] is True
    assert identity["username"] == "alice"


def test_usernames_are_unique_case_and_width_insensitively(client: TestClient) -> None:
    assert _register(client, "Alice", "fixture-password-for-tests").status_code == 201
    clash = _register(client, "alice", "another-fixture-password")
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "username_taken"
    # Full-width characters normalise to the same key.
    assert _register(client, "ａlice", "another-fixture-password").status_code == 409


@pytest.mark.parametrize("username", ["admin", "Admin", "ADMIN", "owner", "root"])
def test_reserved_usernames_cannot_be_registered(
    client: TestClient,
    username: str,
) -> None:
    response = _register(client, username, "fixture-password-for-tests")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "username_reserved"


def test_registration_is_rate_limited_per_client(client: TestClient) -> None:
    for index in range(5):
        assert _register(client, f"user{index}", "fixture-password-for-tests").status_code == 201
    blocked = _register(client, "user5", "fixture-password-for-tests")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "registration_rate_limited"


def test_account_cap_closes_registration(tmp_path) -> None:
    small = AccountStore(tmp_path / "accounts.db", max_accounts=1)
    small.register("first", "fixture-password-for-tests")
    with pytest.raises(AccountError) as excinfo:
        small.register("second", "fixture-password-for-tests")
    assert excinfo.value.code == "registration_closed"


# ---------------- sign-in ----------------


def test_customer_signs_in_through_the_shared_login_endpoint(
    client: TestClient,
    store: AccountStore,
) -> None:
    store.register("bob", "fixture-password-for-tests")
    response = client.post(
        "/api/access/login",
        json={"username": "bob", "password": "fixture-password-for-tests"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    body = response.json()
    # A customer session must never claim owner access.
    assert body["logged_in"] is False
    assert body["account"] == {"logged_in": True, "username": "bob"}
    assert accounts_api.ACCOUNT_COOKIE_NAME in response.headers["set-cookie"]
    assert client.get("/api/account/me").json()["username"] == "bob"


def test_customer_session_never_grants_owner_access(
    client: TestClient,
    store: AccountStore,
) -> None:
    store.register("carol", "fixture-password-for-tests")
    client.post(
        "/api/access/login",
        json={"username": "carol", "password": "fixture-password-for-tests"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    from app.access import OWNER_COOKIE_NAME

    assert client.get("/api/account/me").json()["username"] == "carol"
    assert accounts_api.ACCOUNT_COOKIE_NAME in client.cookies
    # The owner cookie is what every owner-gated route reads; signing in as a
    # customer must never mint one.
    assert OWNER_COOKIE_NAME not in client.cookies


def test_wrong_password_and_unknown_user_are_indistinguishable(
    client: TestClient,
    store: AccountStore,
) -> None:
    store.register("dave", "fixture-password-for-tests")
    wrong = client.post(
        "/api/access/login",
        json={"username": "dave", "password": "nope"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    missing = client.post(
        "/api/access/login",
        json={"username": "nobody", "password": "nope"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json() == missing.json()


def test_repeated_failures_trigger_a_cooldown(
    client: TestClient,
    store: AccountStore,
) -> None:
    store.register("erin", "fixture-password-for-tests")
    for _ in range(10):
        client.post(
            "/api/access/login",
            json={"username": "erin", "password": "wrong"},
            headers={**HEADERS, "Content-Type": "application/json"},
        )
    blocked = client.post(
        "/api/access/login",
        json={"username": "erin", "password": "fixture-password-for-tests"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "login_cooldown"


def test_own_account_login_cannot_reset_guesses_against_another_account(
    client: TestClient,
    store: AccountStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store.register("target", "correct-password")
    store.register("self", "known-password-for-self")
    clock = [1000.0]
    monkeypatch.setattr(accounts_api.time, "time", lambda: clock[0])

    def login(username: str, password: str):
        return client.post(
            "/api/access/login",
            json={"username": username, "password": password},
            headers=HEADERS,
        )

    for _ in range(9):
        assert login("target", "wrong").status_code == 401
    assert login("self", "known-password-for-self").status_code == 200
    assert login("target", "wrong").status_code == 401
    assert login("target", "correct-password").status_code == 429

    # The failed attempts still expire; users are not permanently locked out.
    clock[0] += accounts_api._LOGIN_FAILURE_WINDOW_SECONDS + 1
    assert login("target", "correct-password").status_code == 200


def test_logout_revokes_the_session(client: TestClient, store: AccountStore) -> None:
    store.register("frank", "fixture-password-for-tests")
    client.post(
        "/api/access/login",
        json={"username": "frank", "password": "fixture-password-for-tests"},
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert client.get("/api/account/me").json()["logged_in"] is True
    assert client.post("/api/account/logout", headers=HEADERS).status_code == 200
    assert client.get("/api/account/me").json()["logged_in"] is False


def test_revoked_token_stops_resolving(store: AccountStore) -> None:
    session = store.register("grace", "fixture-password-for-tests")
    assert store.resolve_session(session.token) is not None
    store.revoke_session(session.token)
    assert store.resolve_session(session.token) is None


def test_expired_session_is_rejected(tmp_path) -> None:
    now = [1_000.0]
    aging = AccountStore(tmp_path / "accounts.db", clock=lambda: now[0])
    session = aging.register("heidi", "fixture-password-for-tests")
    assert aging.resolve_session(session.token) is not None
    now[0] += 31 * 24 * 60 * 60
    assert aging.resolve_session(session.token) is None


# ---------------- watchlist ----------------


def test_batch_edit_is_atomic_and_preserves_concurrent_membership(client, store):
    _register(client, "batch-editor", "fixture-password-for-tests")
    client.put("/api/account/watchlist", json={"tickers": ["AAPL", "NVDA"]}, headers=HEADERS)
    # Another page adds AMD after the editor opened its original draft.
    client.post("/api/account/watchlist", json={"ticker": "AMD"}, headers=HEADERS)
    response = client.patch("/api/account/watchlist", json={"add": ["msft", "ＭＳＦＴ"], "remove": ["NVDA"]}, headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["tickers"] == ["AAPL", "AMD", "MSFT"]
    bad = client.patch("/api/account/watchlist", json={"add": ["SPY", "BAD!"], "remove": ["AAPL"]}, headers=HEADERS)
    assert bad.status_code == 400
    assert client.get("/api/account/watchlist").json()["tickers"] == ["AAPL", "AMD", "MSFT"]
    empty = client.patch("/api/account/watchlist", json={"remove": ["AAPL", "AMD", "MSFT"]}, headers=HEADERS)
    assert empty.json()["tickers"] == []
    assert client.get("/api/account/watchlist").json()["tickers"] == []


def test_batch_cap_rolls_back_removals_and_retains_other_accounts(store):
    first = store.register("first-editor", "fixture-password-for-tests").account.user_id
    second = store.register("second-editor", "fixture-password-for-tests").account.user_id
    original = [f"T{i}" for i in range(WATCHLIST_MAX_TICKERS)]
    store.replace_watchlist(first, original)
    store.add_ticker(second, "AAPL")
    with pytest.raises(AccountError, match="watchlist_full"):
        store.edit_watchlist(first, add=["AAPL", "MSFT"], remove=["T0"])
    assert store.watchlist(first) == original
    assert store.edit_watchlist(first, add=["NVDA"], remove=["T0"])[-1] == "NVDA"
    assert store.watchlist(second) == ["AAPL"]


def test_batch_edit_requires_account_and_same_origin_json(client):
    assert client.patch("/api/account/watchlist", json={"add": ["AAPL"]}, headers=HEADERS).status_code == 401
    _register(client, "origin-editor", "fixture-password-for-tests")
    assert client.patch("/api/account/watchlist", json={"add": ["AAPL"]}, headers={**HEADERS, "Origin": "https://elsewhere.test"}).status_code == 403
    assert client.get("/api/account/watchlist").json()["tickers"] == []


def test_owner_batch_can_delete_last_ticker_without_reseeding(owner_client):
    _owner_login(owner_client)
    assert owner_client.patch("/api/account/watchlist", json={"add": ["AAPL"]}, headers=HEADERS).json()["tickers"] == ["AAPL"]
    assert owner_client.delete("/api/account/watchlist/AAPL", headers=HEADERS).json()["tickers"] == []
    assert owner_client.get("/api/account/watchlist").json()["tickers"] == []


def test_watchlist_requires_a_session(client: TestClient) -> None:
    assert client.get("/api/account/watchlist").status_code == 401


def test_watchlist_round_trips_and_keeps_order(client: TestClient) -> None:
    _register(client, "ivan", "fixture-password-for-tests")
    add = client.post(
        "/api/account/watchlist",
        json={"ticker": "nvda"},
        headers=HEADERS,
    )
    assert add.status_code == 200
    assert add.json()["tickers"] == ["NVDA"]
    client.post("/api/account/watchlist", json={"ticker": "AAPL"}, headers=HEADERS)
    assert client.get("/api/account/watchlist").json()["tickers"] == ["NVDA", "AAPL"]
    # Adding twice is a no-op rather than an error.
    again = client.post(
        "/api/account/watchlist",
        json={"ticker": "NVDA"},
        headers=HEADERS,
    )
    assert again.json()["tickers"] == ["NVDA", "AAPL"]
    removed = client.delete("/api/account/watchlist/NVDA", headers=HEADERS)
    assert removed.json()["tickers"] == ["AAPL"]


def test_watchlist_rejects_malformed_tickers(client: TestClient) -> None:
    _register(client, "judy", "fixture-password-for-tests")
    response = client.post(
        "/api/account/watchlist",
        json={"ticker": "not a ticker"},
        headers=HEADERS,
    )
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "invalid_ticker"


def test_watchlist_is_capped(store: AccountStore) -> None:
    session = store.register("ken", "fixture-password-for-tests")
    for index in range(WATCHLIST_MAX_TICKERS):
        store.add_ticker(session.account.user_id, f"T{index:04d}")
    with pytest.raises(AccountError) as excinfo:
        store.add_ticker(session.account.user_id, "OVER")
    assert excinfo.value.code == "watchlist_full"


def test_watchlists_are_isolated_between_accounts(store: AccountStore) -> None:
    first = store.register("leo", "fixture-password-for-tests")
    second = store.register("mia", "fixture-password-for-tests")
    store.add_ticker(first.account.user_id, "NVDA")
    store.add_ticker(second.account.user_id, "TSLA")
    assert store.watchlist(first.account.user_id) == ["NVDA"]
    assert store.watchlist(second.account.user_id) == ["TSLA"]
    # Removing another account's ticker cannot touch it.
    store.remove_ticker(second.account.user_id, "NVDA")
    assert store.watchlist(first.account.user_id) == ["NVDA"]


def test_replace_watchlist_deduplicates_and_normalises(store: AccountStore) -> None:
    session = store.register("nina", "fixture-password-for-tests")
    result = store.replace_watchlist(
        session.account.user_id,
        ["msft", "MSFT", " aapl ", "^GSPC"],
    )
    assert result == ["MSFT", "AAPL", "^GSPC"]
    assert store.watchlist(session.account.user_id) == ["MSFT", "AAPL", "^GSPC"]


# ---------------- owner watchlist ----------------
#
# The owner authenticates through APP_PASSWORD_HASH and never holds an account
# cookie, so before these routes accepted an owner session the owner -- the only
# account on a personal deployment -- was the one user who could not keep a
# watchlist, and the UI hid the controls rather than showing buttons that 401.

OWNER_PASSWORD = "owner-password-for-watchlist-tests"


@pytest.fixture()
def owner_client(store: AccountStore) -> TestClient:
    from app.access import AccessConfig, OwnerAccessRuntime, hash_owner_password

    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password(OWNER_PASSWORD),
    )
    app.include_router(accounts_api.router)
    app.include_router(access_api.router)
    return TestClient(app, base_url="https://localhost")


def _owner_login(client: TestClient) -> None:
    response = client.post(
        "/api/access/login",
        json={"password": OWNER_PASSWORD},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text


def test_owner_session_can_keep_a_watchlist(owner_client: TestClient) -> None:
    assert owner_client.get("/api/account/watchlist").status_code == 401

    _owner_login(owner_client)
    empty = owner_client.get("/api/account/watchlist")
    assert empty.status_code == 200
    assert empty.json()["tickers"] == []

    added = owner_client.post(
        "/api/account/watchlist",
        json={"ticker": "nvda"},
        headers=HEADERS,
    )
    assert added.status_code == 200
    assert added.json()["tickers"] == ["NVDA"]
    assert owner_client.get("/api/account/watchlist").json()["tickers"] == ["NVDA"]

    removed = owner_client.delete("/api/account/watchlist/NVDA", headers=HEADERS)
    assert removed.status_code == 200
    assert removed.json()["tickers"] == []


def test_owner_watchlist_is_not_a_customer_identity(owner_client: TestClient) -> None:
    """Provisioning the owner's row must not make the owner look like a customer."""

    _owner_login(owner_client)
    owner_client.post(
        "/api/account/watchlist",
        json={"ticker": "MSFT"},
        headers=HEADERS,
    )
    me = owner_client.get("/api/account/me")
    assert me.status_code == 200
    assert me.json() == {"logged_in": False, "username": None}


def test_owner_and_customer_watchlists_stay_separate(
    owner_client: TestClient,
    store: AccountStore,
) -> None:
    _owner_login(owner_client)
    owner_client.post(
        "/api/account/watchlist",
        json={"ticker": "OWNR"},
        headers=HEADERS,
    )

    # A customer cookie on the same client wins over the owner session, so the
    # list a signed-in customer edits is always the one they can see.
    assert _register(owner_client, "dana", "fixture-password-for-dana").status_code == 201
    customer = owner_client.get("/api/account/watchlist")
    assert customer.status_code == 200
    assert customer.json()["tickers"] == []

    added = owner_client.post(
        "/api/account/watchlist",
        json={"ticker": "CUST"},
        headers=HEADERS,
    )
    assert added.json()["tickers"] == ["CUST"]

    # The owner's own list is untouched by anything the customer did.
    owner_client.cookies.delete(accounts_api.ACCOUNT_COOKIE_NAME)
    assert owner_client.get("/api/account/watchlist").json()["tickers"] == ["OWNR"]


def test_owner_row_cannot_be_claimed_by_registration(
    owner_client: TestClient,
    store: AccountStore,
) -> None:
    """The reserved username is the DB-level guarantee, not just a code check."""

    _owner_login(owner_client)
    owner_client.get("/api/account/watchlist")  # provisions the owner row

    rejected = _register(owner_client, "admin", "pw-attempt")
    assert rejected.status_code == 400
    assert rejected.json()["detail"]["code"] == "username_reserved"


def test_ensure_owner_account_is_idempotent(store: AccountStore) -> None:
    first = store.ensure_owner_account()
    second = store.ensure_owner_account()
    assert first == second
    store.add_ticker(first.user_id, "AAPL")
    assert store.watchlist(second.user_id) == ["AAPL"]


# ---------------- rate-limit keying and capacity (audit P1-11) ----------------


class _PeerAddress:
    """Give the ASGI scope a real peer address, as a proxy deployment has."""

    def __init__(self, app, address: str) -> None:
        self.app = app
        self.address = address

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope["client"] = (self.address, 50000)
        await self.app(scope, receive, send)


def test_rate_limit_key_follows_the_trusted_proxy_chain(
    store: AccountStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behind a proxy, two visitors must not share one bucket.

    ``request.client.host`` is the proxy container, so keying on it gave every
    visitor on the deployment a single bucket: five sign-ups from one person
    closed registration for everyone. Owner login already resolves the address
    through the trusted-proxy allowlist; both systems now agree.
    """

    from app import access as access_module

    monkeypatch.setattr(access_module, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(
        access_module,
        "TRUSTED_PROXY_NETWORKS",
        (ipaddress.ip_network("127.0.0.1/32"),),
    )

    app = FastAPI()

    @app.middleware("http")
    async def visitor_access(request, call_next):
        with request_owner_access_context(False):
            return await call_next(request)

    app.include_router(accounts_api.router)
    proxied = TestClient(
        _PeerAddress(app, "127.0.0.1"),
        base_url="https://localhost",
    )

    def register_as(address: str, name: str):
        return proxied.post(
            "/api/account/register",
            json={"username": name, "password": "fixture-password-for-tests"},
            headers={**HEADERS, "Content-Type": "application/json", "X-Forwarded-For": address},
        )

    for index in range(5):
        assert register_as("203.0.113.9", f"first{index}").status_code == 201
    assert register_as("203.0.113.9", "first5").status_code == 429
    # A different visitor arrives through the same proxy and must be unaffected.
    assert register_as("198.51.100.4", "second0").status_code == 201


def test_bucket_overflow_evicts_instead_of_clearing_every_cooldown() -> None:
    """Reaching capacity must not reset everyone's failure count.

    ``bucket.clear()`` at the threshold meant an attacker could fill the table
    and have the next insert wipe every active cooldown, including their own.
    """

    now = 1_000_000.0
    bucket: dict[str, tuple[int, float, float]] = {}
    # One live cooldown, plus enough long-expired entries to reach capacity.
    bucket["victim"] = (10, now, now + accounts_api._LOGIN_COOLDOWN_SECONDS)
    stale_start = now - accounts_api._LOGIN_FAILURE_WINDOW_SECONDS - 1
    for index in range(accounts_api._RATE_BUCKET_LIMIT):
        bucket[f"stale{index}"] = (1, stale_start - index, 0.0)

    accounts_api._prune(bucket, now)

    assert "victim" in bucket, "an active cooldown must survive pruning"
    assert bucket["victim"] == (10, now, now + accounts_api._LOGIN_COOLDOWN_SECONDS)
    assert len(bucket) < accounts_api._RATE_BUCKET_LIMIT


def test_bucket_overflow_of_live_entries_drops_the_oldest_only() -> None:
    now = 1_000_000.0
    bucket: dict[str, list[float]] = {}
    for index in range(accounts_api._RATE_BUCKET_LIMIT):
        # All within the registration window, so nothing is expired and
        # eviction has to choose which entry to drop.
        bucket[f"live{index}"] = [now - index * 0.5]

    accounts_api._prune(bucket, now)

    assert len(bucket) == accounts_api._RATE_BUCKET_LIMIT - 1
    assert "live0" in bucket, "the newest entry must be kept"
    oldest = f"live{accounts_api._RATE_BUCKET_LIMIT - 1}"
    assert oldest not in bucket, "eviction must start from the oldest entry"
