"""Exercise resource admission through the actual gateway, without providers."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import deque

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OWNER_COOKIE_NAME,
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
)
from app.api import accounts, options
from app import main
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, DRAWINGS_PER_RANGE_MAX, set_account_store


@pytest.fixture(autouse=True)
def isolated_state(tmp_path):
    main._rl_buckets.clear()
    options.cache.clear()
    options._option_failure_cache.clear()
    accounts.reset_rate_limits()
    set_account_store(AccountStore(tmp_path / "accounts.db"))
    yield
    main._rl_buckets.clear()
    options.cache.clear()
    options._option_failure_cache.clear()
    accounts.reset_rate_limits()
    set_account_store(None)


def _runtime(*, visitor_live_pulls: bool = False) -> OwnerAccessRuntime:
    return OwnerAccessRuntime(
        AccessConfig(mode="password", visitor_live_pulls=visitor_live_pulls),
        password_hash=hash_owner_password("gateway-security-test-password"),
    )


async def _raw_request(app, *, chunks, headers=(), path="/api/account/register"):
    remaining = deque(chunks)
    reads = 0
    sent = []

    async def receive():
        nonlocal reads
        reads += 1
        if not remaining:
            raise AssertionError("gateway must stop receiving after rejection or final chunk")
        return remaining.popleft()

    async def send(message):
        sent.append(message)

    await app(
        {
            "type": "http", "http_version": "1.1", "method": "POST",
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"host", b"testserver"), *headers],
            "scheme": "https", "server": ("testserver", 443),
            "client": ("203.0.113.5", 45000),
        },
        receive,
        send,
    )
    return sent, reads


@pytest.mark.parametrize("path", ["/api/account/register", "/api/account/register/"])
def test_oversized_declared_registration_is_rejected_without_reading_or_parsing(path):
    async def forbidden(*_args):
        raise AssertionError("oversized credentials reached the router")

    sent, reads = asyncio.run(_raw_request(
        main._GatewayMiddleware(forbidden, access_runtime=_runtime()),
        path=path,
        headers=[(b"content-length", str(main._MAX_CREDENTIAL_BODY_BYTES + 1).encode())],
        chunks=[],
    ))
    assert reads == 0
    assert sent[0]["status"] == 413
    assert dict(sent[0]["headers"])[b"cache-control"] == b"private, no-store"


@pytest.mark.parametrize("declared", [None, b"10"])
def test_chunked_or_underdeclared_registration_stops_at_limit(declared):
    async def forbidden(*_args):
        raise AssertionError("oversized credentials reached JSON parsing")

    sent, reads = asyncio.run(_raw_request(
        main._GatewayMiddleware(forbidden, access_runtime=_runtime()),
        headers=[] if declared is None else [(b"content-length", declared)],
        chunks=[
            {"type": "http.request", "body": b"x" * main._MAX_CREDENTIAL_BODY_BYTES, "more_body": True},
            {"type": "http.request", "body": b"SECRET", "more_body": True},
            {"type": "http.request", "body": b"unread", "more_body": False},
        ],
    ))
    assert reads == 2
    assert sent[0]["status"] == 413
    assert b"SECRET" not in sent[1]["body"]


@pytest.mark.parametrize("lengths", [[b"-1"], [b"bogus"], [b"1", b"2"], [b"1, 2"]])
def test_malformed_lengths_rejected_without_body_read(lengths):
    async def forbidden(*_args):
        raise AssertionError("invalid request reached router")

    sent, reads = asyncio.run(_raw_request(
        main._GatewayMiddleware(forbidden, access_runtime=_runtime()),
        headers=[(b"content-length", value) for value in lengths],
        chunks=[],
    ))
    assert reads == 0
    assert sent[0]["status"] == 400


def test_other_api_routes_have_a_finite_body_limit(monkeypatch):
    monkeypatch.setattr(main, "_MAX_API_BODY_BYTES", 128)

    async def forbidden(*_args):
        raise AssertionError("large drawing body reached router")

    sent, reads = asyncio.run(_raw_request(
        main._GatewayMiddleware(forbidden, access_runtime=_runtime()),
        path="/api/account/chart-drawings/replace",
        chunks=[{"type": "http.request", "body": b"x" * 129, "more_body": False}],
    ))
    assert reads == 1
    assert sent[0]["status"] == 413


def test_valid_streamed_registration_still_creates_an_account():
    runtime = _runtime()
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(accounts.router)
    gateway = main._GatewayMiddleware(app, access_runtime=runtime)
    payload = json.dumps({"username": "friend", "password": "safe-password-for-tests"}).encode()
    sent, reads = asyncio.run(_raw_request(
        gateway,
        headers=[
            (b"origin", b"https://testserver"),
            (b"x-optix-action", b"1"),
            (b"content-type", b"application/json"),
        ],
        chunks=[
            {"type": "http.request", "body": payload[:10], "more_body": True},
            {"type": "http.request", "body": payload[10:], "more_body": False},
        ],
    ))
    assert reads == 2
    assert sent[0]["status"] == 201
    assert accounts.get_account_store().authenticate("friend", "safe-password-for-tests").account.username == "friend"


def test_gateway_accepts_a_full_500_item_drawing_replacement():
    runtime = _runtime()
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(accounts.router)
    app.add_middleware(main._GatewayMiddleware, access_runtime=runtime)
    session = accounts.get_account_store().register("drawing-friend", "drawing-password-for-tests")
    drawings = [
        {
            "schemaVersion": 1, "id": str(uuid.uuid4()), "ticker": "NVDA",
            "range": "1d", "adjustment": "raw", "kind": "text",
            "anchors": [{"time": "2026-07-06T13:30:00Z", "barKey": "2026-07-06", "price": 120.5}],
            "style": {"color": "#2E46E0", "width": 2, "dash": "solid"},
            "text": "中文笔记" * 60,
        }
        for _ in range(DRAWINGS_PER_RANGE_MAX)
    ]
    # Escaped CJK characters cost more wire bytes than the browser's usual
    # UTF-8 encoding; both encodings must fit with maximum-length notes.
    body = json.dumps({"schemaVersion": 1, "expected_scope_revision": 0, "drawings": drawings}).encode()
    with TestClient(app, base_url="https://testserver") as client:
        client.cookies.set(accounts.ACCOUNT_COOKIE_NAME, session.token)
        response = client.post(
            "/api/account/chart-drawings/replace?ticker=NVDA&range=1d&adjustment=raw",
            content=body,
            headers={
                "Origin": "https://testserver", "X-Optix-Action": "1",
                "Content-Type": "application/json",
            },
        )
        assert response.status_code == 200, response.text
        saved = client.get("/api/account/chart-drawings?ticker=NVDA&range=1d&adjustment=raw")
        assert saved.status_code == 200
        assert len(saved.json()["drawings"]) == DRAWINGS_PER_RANGE_MAX


def _option_app(runtime):
    app = FastAPI()
    app.state.access_runtime = runtime
    app.include_router(options.router, dependencies=[Depends(require_public_read_or_owner_access)])
    app.add_middleware(main._GatewayMiddleware, access_runtime=runtime)
    return app


@pytest.mark.parametrize("account_cookie", [None, "forged-session"])
def test_anonymous_cold_options_never_contact_provider(monkeypatch, account_cookie):
    calls = []

    def forbidden(*args):
        calls.append(args)
        raise AssertionError("anonymous cold read contacted Yahoo")

    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", forbidden)
    monkeypatch.setattr(options.yahoo, "get_option_chain", forbidden)
    with TestClient(_option_app(_runtime()), base_url="https://testserver") as client:
        if account_cookie:
            client.cookies.set(accounts.ACCOUNT_COOKIE_NAME, account_cookie)
        for path in ["/api/options/AAPL/expirations", "/api/options/AAPL/chain?expiration=2030-08-16"]:
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["detail"]["code"] == "public_snapshot_unavailable"
    assert calls == []


@pytest.mark.parametrize("visitor_live_pulls", [False, True])
def test_authorized_option_cold_reads_populate_cache_for_visitors(monkeypatch, visitor_live_pulls):
    calls = []

    def expirations(symbol):
        calls.append((symbol, "expirations"))
        return {"expirations": ["2030-08-16"]}

    def chain(symbol, expiration):
        calls.append((symbol, "chain"))
        return {"ticker": symbol, "expiration": expiration, "calls": [], "puts": []}

    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", expirations)
    monkeypatch.setattr(options.yahoo, "get_option_chain", chain)
    runtime = _runtime(visitor_live_pulls=visitor_live_pulls)
    with TestClient(_option_app(runtime), base_url="https://testserver") as client:
        if not visitor_live_pulls:
            session = runtime.login("gateway-security-test-password", client_key="test-owner")
            client.cookies.set(OWNER_COOKIE_NAME, session.session_token)
        assert client.get("/api/options/AAPL/chain?expiration=2030-08-16").status_code == 200
        client.cookies.clear()
        assert client.get("/api/options/AAPL/expirations").status_code == 200
        assert client.get("/api/options/AAPL/chain?expiration=2030-08-16").status_code == 200
    assert calls == [("AAPL", "expirations"), ("AAPL", "chain")]


def test_visitor_can_read_fresh_chain_after_earlier_expiration_list_expires(monkeypatch):
    now = [1000.0]
    calls = []
    monkeypatch.setattr(options.time, "time", lambda: now[0])

    def expirations(symbol):
        calls.append((symbol, "expirations"))
        return {"expirations": ["2030-08-16"]}

    def chain(symbol, expiration):
        calls.append((symbol, "chain"))
        return {
            "ticker": symbol, "expiration": expiration,
            "underlying_price": float("nan"), "calls": [], "puts": [],
        }

    monkeypatch.setattr(options.yahoo, "get_expirations_snapshot", expirations)
    monkeypatch.setattr(options.yahoo, "get_option_chain", chain)
    runtime = _runtime()
    with TestClient(_option_app(runtime), base_url="https://testserver") as client:
        session = runtime.login("gateway-security-test-password", client_key="test-owner")
        client.cookies.set(OWNER_COOKIE_NAME, session.session_token)
        assert client.get("/api/options/AAPL/expirations").status_code == 200
        # The chain is loaded ten minutes after its expiration list. Their
        # independent TTLs leave a five-minute interval with only a fresh chain.
        now[0] += 600
        assert client.get("/api/options/AAPL/chain?expiration=2030-08-16").status_code == 200
        now[0] += 360
        client.cookies.clear()
        assert options.cache.get("options:expirations:AAPL") is None
        assert options.cache.get("options:chain:AAPL:2030-08-16") is not None

        response = client.get("/api/options/AAPL/chain?expiration=2030-08-16")
        assert response.status_code == 200
        assert response.json()["provider"] == "Yahoo/yfinance"
        assert response.json()["underlying_price"] is None
        assert calls == [("AAPL", "expirations"), ("AAPL", "chain")]

        # Once the chain itself expires, the anonymous cold-query gate still
        # applies; this fix cannot keep serving an expired result or call Yahoo.
        now[0] += 241
        unavailable = client.get("/api/options/AAPL/chain?expiration=2030-08-16")
        assert unavailable.status_code == 503
        assert unavailable.json()["detail"]["code"] == "public_snapshot_unavailable"
        assert calls == [("AAPL", "expirations"), ("AAPL", "chain")]
