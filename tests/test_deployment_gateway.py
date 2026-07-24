from __future__ import annotations

import asyncio
import ipaddress
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest
from fastapi import Depends, FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.access import (
    OWNER_COOKIE_NAME,
    OWNER_SESSION_SECONDS,
    LoginRejected,
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
    require_owner_access,
    require_same_origin_action,
    require_same_origin_json,
)
import app.access as access_module
from app.api import access as access_api
import app.main as main
from app.main import _GatewayMiddleware, _configured_allowed_hosts
from app.personal_config import AccessConfig


PASSWORD = "owner-password-for-tests"


class _PeerAddress:
    def __init__(self, app, address: str) -> None:
        self.app = app
        self.address = address

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope["client"] = (self.address, 50000)
        await self.app(scope, receive, send)


def _runtime(mode: str, *, clock=None) -> OwnerAccessRuntime:
    config = AccessConfig(mode=mode)
    kwargs = {
        "password_hash": hash_owner_password(PASSWORD)
        if mode == "password"
        else "",
    }
    if clock is not None:
        kwargs["clock"] = clock
    return OwnerAccessRuntime(config, **kwargs)


def _test_app(runtime: OwnerAccessRuntime) -> FastAPI:
    app = FastAPI()
    app.state.access_runtime = runtime

    @app.get("/health")
    def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/ready")
    def ready() -> dict[str, bool]:
        return {"ready": True}

    @app.get("/")
    def index() -> dict[str, str]:
        return {"page": "public"}

    @app.get("/owner.html")
    def owner_page() -> dict[str, str]:
        return {"page": "owner"}

    @app.get("/static/app.js")
    def public_script() -> dict[str, bool]:
        return {"public": True}

    @app.get("/api/market/status")
    def public_market_status() -> dict[str, bool]:
        return {"public": True}

    @app.post(
        "/api/catalysts/tickers/batch",
        dependencies=[Depends(require_same_origin_json)],
    )
    def public_batch_query() -> dict[str, bool]:
        return {"public": True}

    @app.get("/login")
    def login_page() -> dict[str, str]:
        return {"page": "login"}

    @app.get(
        "/api/value",
        dependencies=[
            Depends(require_owner_access),
            Depends(require_same_origin_action),
        ],
    )
    def value() -> dict[str, bool]:
        return {"owner": True}

    @app.post(
        "/api/action",
        dependencies=[
            Depends(require_owner_access),
            Depends(require_same_origin_action),
        ],
    )
    def action() -> dict[str, bool]:
        return {"changed": True}

    app.include_router(access_api.router)
    app.add_middleware(_GatewayMiddleware, access_runtime=runtime)
    return app


def _action_headers(origin: str = "https://testserver") -> dict[str, str]:
    return {
        "Origin": origin,
        "X-Optix-Action": "1",
    }


def _login(client: TestClient) -> str:
    response = client.post(
        "/api/access/login",
        json={"password": PASSWORD},
        headers=_action_headers(),
    )
    assert response.status_code == 200
    return response.headers["set-cookie"]


_BODY_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# Login is the anonymous authentication bootstrap and the ticker batch is a
# bounded read query. They still require same-origin JSON, but not an owner
# action header or an existing owner session.
_SAME_ORIGIN_JSON_ONLY_OPERATIONS = {
    ("POST", "/api/access/login"),
    ("POST", "/api/catalysts/tickers/batch"),
}


def _effective_fastapi_routes(app: FastAPI):
    """Yield routes after FastAPI's lazy router includes are applied."""

    for route in app.routes:
        effective_contexts = getattr(route, "effective_route_contexts", None)
        if callable(effective_contexts):
            yield from effective_contexts()
        elif isinstance(route, APIRoute):
            yield route


def _dependency_calls(dependant):
    for dependency in dependant.dependencies:
        yield dependency.call
        yield from _dependency_calls(dependency)


def _real_body_operations():
    return [
        (method, route.path, route)
        for route in _effective_fastapi_routes(main.app)
        for method in sorted(set(route.methods or ()) & _BODY_METHODS)
    ]


def test_private_network_startup_is_fail_closed_for_public_bindings() -> None:
    runtime = _runtime("private_network")
    runtime.validate_startup("localhost")
    runtime.validate_startup("127.0.0.1")
    runtime.validate_startup("100.64.10.20")

    for host in ("0.0.0.0", "::", "8.8.8.8", "203.0.113.8", "example.com"):
        with pytest.raises(RuntimeError):
            runtime.validate_startup(host)

    with pytest.raises(RuntimeError, match="TRUST_PROXY_HEADERS=false"):
        runtime.validate_startup(
            "127.0.0.1",
            trust_proxy_headers=True,
            trusted_proxy_cidrs="127.0.0.1/32",
        )

    with pytest.raises(RuntimeError, match="IP literals"):
        runtime.validate_startup(
            "127.0.0.1",
            allowed_hosts="option.example.com",
        )


def test_password_startup_requires_a_valid_password_hash() -> None:
    with pytest.raises(RuntimeError, match="APP_PASSWORD_HASH"):
        OwnerAccessRuntime(
            AccessConfig(mode="password"),
            password_hash="",
        ).validate_startup("0.0.0.0")

    boundary = _runtime("password").validate_startup(
        "127.0.0.1",
        allowed_hosts="option.example.com",
        trust_proxy_headers=True,
        trusted_proxy_cidrs="127.0.0.1/32,172.18.0.0/16",
    )
    assert "option.example.com" in boundary.allowed_hosts


def test_password_proxy_boundary_rejects_missing_or_public_trust_ranges() -> None:
    runtime = _runtime("password")
    with pytest.raises(RuntimeError, match="DNS ALLOWED_HOSTS"):
        runtime.validate_startup(
            "0.0.0.0",
            allowed_hosts="option.example.com",
            trust_proxy_headers=False,
        )
    with pytest.raises(RuntimeError, match="TRUSTED_PROXY_CIDRS"):
        runtime.validate_startup(
            "127.0.0.1",
            allowed_hosts="option.example.com",
            trust_proxy_headers=True,
        )
    with pytest.raises(RuntimeError, match="actual private"):
        runtime.validate_startup(
            "127.0.0.1",
            allowed_hosts="option.example.com",
            trust_proxy_headers=True,
            trusted_proxy_cidrs="0.0.0.0/0",
        )


def test_password_local_or_ip_hosts_can_use_direct_https_without_proxy_headers() -> None:
    runtime = _runtime("password")
    for host_bind, allowed_hosts in (
        ("127.0.0.1", "localhost,127.0.0.1"),
        ("10.20.30.40", "10.20.30.40"),
    ):
        boundary = runtime.validate_startup(
            host_bind,
            allowed_hosts=allowed_hosts,
            trust_proxy_headers=False,
        )
        assert boundary.access_mode == "password"
        assert boundary.trusted_proxy_cidrs == ()


def test_private_network_uses_request_source_and_never_needs_a_browser_token() -> None:
    app = _test_app(_runtime("private_network"))
    with TestClient(_PeerAddress(app, "10.20.30.40")) as private_client:
        assert private_client.get("/api/value").status_code == 200
        response = private_client.post(
            "/api/action",
            json={},
            headers={
                "Origin": "http://testserver",
                "X-Optix-Action": "1",
            },
        )
        assert response.status_code == 200

    with TestClient(_PeerAddress(app, "8.8.8.8")) as public_client:
        assert public_client.get("/api/value").status_code == 403
        assert public_client.get("/").status_code == 403


def test_health_and_ready_are_public_in_both_access_modes() -> None:
    for mode in ("private_network", "password"):
        with TestClient(
            _PeerAddress(_test_app(_runtime(mode)), "8.8.8.8"),
            base_url="https://testserver",
        ) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/ready").status_code == 200
            assert client.get("/api/value").status_code in {401, 403}


def test_password_mode_serves_public_reads_and_protects_owner_surfaces() -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/api/market/status").status_code == 200
        status = client.get("/api/access/status")
        assert status.status_code == 200
        assert status.json() == {"access_mode": "password", "logged_in": False}

        owner_page = client.get("/owner.html")
        assert owner_page.status_code == 303
        assert owner_page.headers["location"] == "/login"
        assert client.get("/api/value").status_code == 401
        assert client.get("/api/ai/status").status_code == 401
        assert client.get("/api/runtime-settings").status_code == 401
        assert client.get("/api/worker/status").status_code == 401
        assert client.post("/api/market/status", json={}).status_code == 401
        assert client.get("/login").status_code == 200

        public_batch = client.post(
            "/api/catalysts/tickers/batch",
            json={"tickers": ["NVDA"]},
            headers=_action_headers(),
        )
        assert public_batch.status_code == 200
        cross_site_batch = client.post(
            "/api/catalysts/tickers/batch",
            json={"tickers": ["NVDA"]},
            headers=_action_headers("https://evil.example"),
        )
        assert cross_site_batch.status_code == 403


def test_password_login_sets_strict_server_only_cookie_and_unlocks_owner_routes() -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        set_cookie = _login(client).lower()
        assert "httponly" in set_cookie
        assert "secure" in set_cookie
        assert "samesite=strict" in set_cookie
        assert "path=/" in set_cookie
        assert f"max-age={OWNER_SESSION_SECONDS}" in set_cookie
        assert client.get("/api/value").status_code == 200
        status = client.get("/api/access/status")
        assert status.status_code == 200
        assert status.json() == {"access_mode": "password", "logged_in": True}


def test_a_new_owner_login_invalidates_the_previous_session() -> None:
    app = _test_app(_runtime("password"))
    with (
        TestClient(app, base_url="https://testserver") as first,
        TestClient(app, base_url="https://testserver") as second,
    ):
        _login(first)
        assert first.get("/api/value").status_code == 200
        _login(second)
        assert second.get("/api/value").status_code == 200
        assert first.get("/api/value").status_code == 401


def test_logout_invalidates_the_owner_session() -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        _login(client)
        response = client.post(
            "/api/access/logout",
            json={},
            headers=_action_headers(),
        )
        assert response.status_code == 200
        assert "max-age=0" in response.headers["set-cookie"].lower()
        assert client.get("/api/value").status_code == 401


def test_owner_session_expires_in_memory() -> None:
    now = [1_000.0]
    runtime = _runtime("password", clock=lambda: now[0])
    result = runtime.login(PASSWORD, client_key="127.0.0.1")
    assert runtime.session_valid(result.session_token)

    now[0] += OWNER_SESSION_SECONDS + 1
    assert not runtime.session_valid(result.session_token)


def test_repeated_login_failures_enter_a_bounded_cooldown() -> None:
    runtime = _runtime("password")
    for _ in range(5):
        with pytest.raises(LoginRejected, match="invalid_owner_password"):
            runtime.login("wrong-password", client_key="127.0.0.1")

    with pytest.raises(LoginRejected) as rejected:
        runtime.login(PASSWORD, client_key="127.0.0.1")
    assert rejected.value.code == "login_cooldown"
    assert rejected.value.retry_after is not None


def test_parallel_login_from_one_source_runs_only_one_password_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime("password")
    entered = Event()
    release = Event()
    calls = 0

    def slow_rejection(_password: str, _encoded_hash: str) -> bool:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return False

    monkeypatch.setattr(access_module, "verify_owner_password", slow_rejection)
    with ThreadPoolExecutor(max_workers=1) as executor:
        first = executor.submit(
            runtime.login,
            "wrong-password",
            client_key="127.0.0.1",
        )
        assert entered.wait(timeout=2)
        with pytest.raises(LoginRejected) as concurrent_rejection:
            runtime.login("wrong-password", client_key="127.0.0.1")
        assert concurrent_rejection.value.code == "login_cooldown"
        assert concurrent_rejection.value.retry_after == 1
        release.set()
        with pytest.raises(LoginRejected) as first_rejection:
            first.result(timeout=2)
        assert first_rejection.value.code == "invalid_owner_password"
    assert calls == 1


@pytest.mark.parametrize(
    ("headers", "use_json", "expected"),
    [
        ({"Origin": "https://evil.example", "X-Optix-Action": "1"}, True, 403),
        ({"Origin": "https://testserver"}, True, 403),
        ({"Origin": "https://testserver", "X-Optix-Action": "0"}, True, 403),
        ({"Origin": "https://testserver", "X-Optix-Action": "1"}, False, 415),
    ],
)
def test_state_changes_require_origin_host_json_and_custom_header(
    headers: dict[str, str],
    use_json: bool,
    expected: int,
) -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        _login(client)
        response = client.post(
            "/api/action",
            headers=headers,
            **({"json": {}} if use_json else {"content": "value=1"}),
        )
        assert response.status_code == expected


def test_password_proxy_accepts_default_https_port_for_login_and_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access_module, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(
        access_module,
        "TRUSTED_PROXY_NETWORKS",
        (ipaddress.ip_network("127.0.0.1/32"),),
    )
    app = _PeerAddress(_test_app(_runtime("password")), "127.0.0.1")
    headers = {
        "Host": "option.example.com:443",
        "Origin": "https://option.example.com",
        "X-Forwarded-Proto": "https",
        "X-Optix-Action": "1",
    }
    with TestClient(app, base_url="http://option.example.com") as client:
        login = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers=headers,
        )
        assert login.status_code == 200
        session = login.cookies.get(OWNER_COOKIE_NAME)
        assert session
        action = client.post(
            "/api/action",
            json={},
            headers={
                **headers,
                "Cookie": f"{OWNER_COOKIE_NAME}={session}",
            },
        )
        assert action.status_code == 200


def test_private_http_action_accepts_the_explicit_default_port() -> None:
    app = _PeerAddress(_test_app(_runtime("private_network")), "10.20.30.40")
    with TestClient(app, base_url="http://testserver") as client:
        response = client.post(
            "/api/action",
            json={},
            headers={
                "Host": "testserver:80",
                "Origin": "http://testserver",
                "X-Optix-Action": "1",
            },
        )
    assert response.status_code == 200


def test_same_origin_keeps_non_default_ports_distinct() -> None:
    app = _test_app(_runtime("password"))
    with TestClient(app, base_url="https://testserver:8443") as client:
        accepted = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={
                "Host": "testserver:8443",
                "Origin": "https://testserver:8443",
                "X-Optix-Action": "1",
            },
        )
        rejected = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={
                "Host": "testserver:8443",
                "Origin": "https://testserver",
                "X-Optix-Action": "1",
            },
        )
    assert accepted.status_code == 200
    assert rejected.status_code == 403


@pytest.mark.parametrize(
    "authority",
    [
        "testserver:",
        "testserver:99999",
        "user@testserver",
        "testserver/path",
        "testserver\\path",
        "testserver?query",
        "testserver#fragment",
        "testserver,evil.example",
        "2001:db8::1",
        "[2001:db8::1",
        "[v1.foo]:443",
    ],
)
def test_same_origin_rejects_malformed_host_authorities(authority: str) -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        response = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={
                "Host": authority,
                "Origin": "https://testserver",
                "X-Optix-Action": "1",
            },
        )
    assert response.status_code == 403


@pytest.mark.parametrize(
    "origin",
    [
        "https://testserver?",
        "https://testserver#",
        "https://testserver:",
        "https://testserver:99999",
        "https://user@testserver",
        "https://testserver/path",
        "https://testserver\\path",
        "https://[v1.foo]",
    ],
)
def test_same_origin_rejects_malformed_origins(origin: str) -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        response = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={
                "Host": "testserver",
                "Origin": origin,
                "X-Optix-Action": "1",
            },
        )
    assert response.status_code == 403


@pytest.mark.parametrize("duplicate", ["host", "origin"])
def test_same_origin_rejects_duplicate_authority_headers(duplicate: str) -> None:
    headers = [
        ("Host", "testserver"),
        ("Origin", "https://testserver"),
        ("X-Optix-Action", "1"),
    ]
    if duplicate == "host":
        headers.insert(1, ("Host", "testserver"))
    else:
        headers.insert(2, ("Origin", "https://testserver"))
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        response = client.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers=headers,
        )
    assert response.status_code == 403


def test_proxy_scheme_and_forwarded_host_remain_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(access_module, "TRUST_PROXY_HEADERS", True)
    monkeypatch.setattr(
        access_module,
        "TRUSTED_PROXY_NETWORKS",
        (ipaddress.ip_network("127.0.0.1/32"),),
    )
    headers = {
        "Host": "backend:2000",
        "Origin": "https://option.example.com",
        "X-Forwarded-Host": "option.example.com",
        "X-Forwarded-Proto": "https",
        "X-Optix-Action": "1",
    }
    trusted = TestClient(
        _PeerAddress(_test_app(_runtime("password")), "127.0.0.1"),
        base_url="http://backend:2000",
    )
    untrusted = TestClient(
        _PeerAddress(_test_app(_runtime("password")), "8.8.8.8"),
        base_url="http://option.example.com",
    )
    try:
        assert trusted.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers=headers,
        ).status_code == 403
        assert untrusted.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={**headers, "Host": "option.example.com:443"},
        ).status_code == 403
    finally:
        trusted.close()
        untrusted.close()


def test_origin_helpers_normalize_default_ports_dns_and_ipv6() -> None:
    assert access_module._canonical_origin("https://OPTION.example") == (
        "https",
        "option.example",
        443,
    )
    assert access_module._canonical_request_origin(
        "https",
        "option.example:443",
    ) == ("https", "option.example", 443)
    assert access_module._canonical_origin(
        "https://option.example:443"
    ) == access_module._canonical_request_origin("https", "option.example")
    assert access_module._canonical_origin("https://[2001:0db8::1]") == (
        "https",
        "2001:db8::1",
        443,
    )
    assert access_module._canonical_request_origin(
        "https",
        "[2001:db8::1]:443",
    ) == ("https", "2001:db8::1", 443)
    assert access_module._canonical_origin(
        "https://bücher.example"
    ) == access_module._canonical_request_origin(
        "https",
        "xn--bcher-kva.example:443",
    )
    assert access_module._canonical_origin(
        "https://faß.de"
    ) == access_module._canonical_request_origin(
        "https",
        "xn--fa-hia.de:443",
    )
    assert access_module._canonical_origin("https://faß.de") != (
        "https",
        "fass.de",
        443,
    )
    assert access_module._canonical_request_origin(
        "https",
        "option.example\x00:443",
    ) is None


def test_production_host_validation_accepts_bracketed_ipv6_loopback() -> None:
    assert "::1" in main._ALLOWED_HOSTS

    async def request(application, host: str) -> tuple[int, bytes]:
        sent: list[dict] = []
        received = False

        async def receive() -> dict:
            nonlocal received
            if not received:
                received = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            sent.append(message)

        await application(
            {
                "type": "http",
                "asgi": {"version": "3.0"},
                "http_version": "1.1",
                "method": "GET",
                "scheme": "http",
                "path": "/health",
                "raw_path": b"/health",
                "query_string": b"",
                "root_path": "",
                "headers": [(b"host", host.encode("ascii"))],
                "client": ("::1", 50000),
                "server": ("::1", 2000),
            },
            receive,
            send,
        )
        status_code = next(
            message["status"]
            for message in sent
            if message["type"] == "http.response.start"
        )
        body = b"".join(
            message.get("body", b"")
            for message in sent
            if message["type"] == "http.response.body"
        )
        return status_code, body

    accepted_status, _accepted_body = asyncio.run(
        request(main.app, "[::1]:2000")
    )
    rejected_status, rejected_body = asyncio.run(
        request(main.app, "[::2]:2000")
    )
    assert accepted_status == 200
    assert rejected_status == 400
    assert rejected_body == b"Invalid host header"

    async def accepted_app(_scope, _receive, send) -> None:
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = main._ExactTrustedHostMiddleware(
        accepted_app,
        allowed_hosts=["2001:0db8:0:0:0:0:0:1", "faß.de"],
    )
    assert middleware.allowed_hosts == frozenset(
        {"2001:db8::1", "xn--fa-hia.de"}
    )
    assert asyncio.run(request(middleware, "[2001:db8::1]:443"))[0] == 204
    assert asyncio.run(request(middleware, "xn--fa-hia.de:443"))[0] == 204
    assert asyncio.run(request(middleware, "fass.de:443"))[0] == 400


def test_password_login_itself_requires_https_and_same_origin_json() -> None:
    app = _test_app(_runtime("password"))
    with TestClient(app, base_url="http://testserver") as insecure:
        response = insecure.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={"Origin": "http://testserver", "X-Optix-Action": "1"},
        )
        assert response.status_code == 426

    with TestClient(app, base_url="https://testserver") as secure:
        assert secure.post(
            "/api/access/login",
            json={"password": PASSWORD},
            headers={"Origin": "https://evil.example", "X-Optix-Action": "1"},
        ).status_code == 403


def test_password_login_rejects_large_body_before_json_parsing() -> None:
    sentinel = "oversized-login-body-sentinel"
    body = (f'{{"password":"{sentinel}' + ("x" * 5000) + '"}').encode()
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        response = client.post(
            "/api/access/login",
            content=body,
            headers={
                **_action_headers(),
                "Content-Type": "application/json",
            },
        )
    assert response.status_code == 413
    assert sentinel not in response.text


def test_production_validation_errors_never_echo_submitted_password() -> None:
    submitted_password = "password-response-sentinel-" + ("x" * 1024)
    client = TestClient(
        _PeerAddress(main.app, "127.0.0.1"),
        base_url="http://localhost",
    )
    try:
        main._rl_buckets.clear()
        response = client.post(
            "/api/access/login",
            json={"password": submitted_password},
            headers={
                "Origin": "http://localhost",
                "X-Optix-Action": "1",
            },
        )
        assert response.status_code == 422
        assert submitted_password not in response.text
        assert "password-response-sentinel" not in response.text
        assert all(
            set(error) == {"type", "loc", "msg"}
            for error in response.json()["detail"]
        )

        field_sentinel = "owner-password-as-extra-field-sentinel"
        main._rl_buckets.clear()
        extra_field = client.post(
            "/api/access/login",
            json={"password": "x", field_sentinel: 1},
            headers={
                "Origin": "http://localhost",
                "X-Optix-Action": "1",
            },
        )
        assert extra_field.status_code == 422
        assert field_sentinel not in extra_field.text
        assert extra_field.json()["detail"] == [
            {
                "type": "request_validation_failed",
                "loc": ["request"],
                "msg": "Invalid request",
            }
        ]
    finally:
        client.close()


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/", True),
        ("HEAD", "/index.html", True),
        ("GET", "/static/js/deck-app.js", True),
        ("GET", "/api/access/status", True),
        ("GET", "/api/stocks", False),
        ("GET", "/api/stocks/watchlist", True),
        ("GET", "/api/stocks/NVDA", True),
        ("HEAD", "/api/stocks/NVDA/chart", True),
        ("GET", "/api/options/NVDA/chain", True),
        ("GET", "/api/earnings/upcoming", True),
        ("GET", "/api/sectors/technology/heatmap", True),
        ("GET", "/api/market/status", True),
        ("GET", "/api/signals/market", True),
        ("GET", "/api/catalysts/feed", True),
        ("GET", "/api/catalysts/news/101", True),
        ("GET", "/api/catalysts/market-focus-cycles/latest", True),
        ("GET", "/api/catalysts/market-focus-cycles/mfc_" + "a" * 32, False),
        ("GET", "/api/catalysts/analysis-jobs/aij_test", False),
        ("GET", "/api/catalysts/refresh/refresh_test", False),
        ("GET", "/api/strength/scan", True),
        ("GET", "/api/breakouts/current", True),
        ("POST", "/api/catalysts/tickers/batch", True),
        # SPA(BrowserRouter):无扩展名路径回退到 index.html 壳,匿名可读
        ("GET", "/index.html/extra", True),
        ("GET", "/staticity/js/deck-app.js", False),
        ("GET", "/api/access/status/extra", False),
        ("GET", "/api/stocks-private", False),
        ("GET", "/api/options2/NVDA", False),
        ("GET", "/api/earnings-private", False),
        ("GET", "/api/sectors2", False),
        ("GET", "/api/marketplace", False),
        ("GET", "/api/signals-private", False),
        ("GET", "/api/catalysts-admin", False),
        ("GET", "/api/strengthened", False),
        ("GET", "/api/breakouts2", False),
        ("GET", "/api/ai/status", False),
        ("GET", "/api/runtime-settings", False),
        ("POST", "/api/stocks", False),
        ("POST", "/api/catalysts/tickers/batch/", False),
        ("POST", "/api/catalysts/tickers/batch/extra", False),
    ],
)
def test_public_read_paths_match_only_exact_paths_or_path_segments(
    method: str,
    path: str,
    expected: bool,
) -> None:
    assert main._is_public_read_request(path, method) is expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/catalysts/status", False),
        ("GET", "/api/catalysts/hotspots/status", False),
        ("GET", "/api/catalysts/feed", False),
        ("GET", "/api/catalysts/news/101", False),
        ("GET", "/api/catalysts/calendar", False),
        ("GET", "/api/catalysts/market-focus-cycles/latest", False),
        ("GET", "/api/catalysts/market-focus-cycles/mfc_" + "a" * 32, True),
        ("POST", "/api/catalysts/tickers/batch", True),
    ],
)
def test_public_catalyst_reads_do_not_consume_the_provider_work_budget(
    method: str,
    path: str,
    expected: bool,
) -> None:
    assert main._is_heavy_api_path(path, method) is expected


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/api/stocks/search", True),
        ("GET", "/api/stocks/watchlist", True),
        ("GET", "/api/stocks/AAOI", True),
        ("GET", "/api/stocks/AAOI/chart", True),
        ("GET", "/api/stocks/AAOI/signals", True),
        ("GET", "/api/options/AAOI/expirations", True),
        ("GET", "/api/options/AAOI/chain", True),
        ("GET", "/api/signals/stock/AAOI", True),
        ("GET", "/api/strength/stocks/AAOI", True),
        ("GET", "/api/strength/scan", True),
        ("GET", "/api/breakouts/tickers/AAOI", True),
        ("GET", "/api/options/unusual", True),
        ("GET", "/api/earnings/upcoming", True),
        ("GET", "/api/signals/market", True),
        ("GET", "/api/catalysts/feed", True),
        ("POST", "/api/stocks/AAOI", False),
        ("POST", "/api/ai/jobs/signal-analysis", False),
    ],
)
def test_cache_protected_stock_drawer_reads_have_a_separate_finite_bucket(
    method: str,
    path: str,
    expected: bool,
) -> None:
    assert main._is_cached_market_read_path(path, method) is expected
    if expected:
        assert main._is_heavy_api_path(path, method) is False


def test_stock_drawer_read_bucket_is_bounded_and_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = FastAPI()

    @app.get("/api/stocks/AAOI")
    def stock() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/earnings/upcoming")
    def earnings() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/api/runtime-settings")
    def runtime_settings() -> dict[str, bool]:
        return {"ok": True}

    app.add_middleware(
        _GatewayMiddleware,
        access_runtime=_runtime("private_network"),
    )
    monkeypatch.setattr(main, "_RL_MARKET_READ_LIMIT", 3)
    main._rl_buckets.clear()
    try:
        with TestClient(
            _PeerAddress(app, "127.0.0.1"),
            base_url="https://testserver",
        ) as client:
            assert [
                client.get("/api/stocks/AAOI").status_code,
                client.get("/api/earnings/upcoming").status_code,
                client.get("/api/stocks/AAOI").status_code,
            ] == [
                200,
                200,
                200,
            ]
            # Owner-only light reads do not share the public UI-read bucket.
            assert client.get("/api/runtime-settings").status_code == 200
            limited = client.get("/api/earnings/upcoming")
            assert limited.status_code == 429
            assert limited.json()["error"] == "rate_limited"
            assert limited.headers["retry-after"] == str(main._RL_WINDOW)
    finally:
        main._rl_buckets.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/staticity/js/deck-app.js"),
        ("GET", "/api/access/status/extra"),
        ("GET", "/api/stocks-private"),
        ("GET", "/api/ai/status"),
        ("POST", "/api/catalysts/tickers/batch/"),
    ],
)
def test_password_gateway_rejects_public_path_lookalikes(
    method: str,
    path: str,
) -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
    ) as client:
        response = client.request(
            method,
            path,
            json={} if method == "POST" else None,
        )
    assert response.status_code == 401
    assert response.json()["error"] == "owner_login_required"


def test_anonymous_requests_cannot_reach_any_owner_state_changing_route() -> None:
    operations = [
        (method, template)
        for method, template, _route in _real_body_operations()
        if (method, template) not in _SAME_ORIGIN_JSON_ONLY_OPERATIONS
    ]
    assert len(operations) >= 15
    assert ("PUT", "/api/runtime-settings") in operations
    assert ("POST", "/api/ai/jobs/earnings-impact") in operations
    assert ("POST", "/api/catalysts/refresh") in operations

    for mode, address, expected_status, expected_error in (
        ("password", "8.8.8.8", 401, "owner_login_required"),
        ("private_network", "8.8.8.8", 403, "private_network_required"),
    ):
        gateway = _GatewayMiddleware(FastAPI(), access_runtime=_runtime(mode))
        with TestClient(
            _PeerAddress(gateway, address),
            base_url="https://testserver",
        ) as client:
            for method, template in operations:
                path = re.sub(r"\{[^}]+\}", "anonymous-test", template)
                for headers in ({}, _action_headers()):
                    response = client.request(
                        method,
                        path,
                        json={},
                        headers=headers,
                    )
                    assert response.status_code == expected_status, (
                        mode,
                        method,
                        template,
                        headers,
                        response.text,
                    )
                    assert response.json()["error"] == expected_error


def test_every_real_body_route_declares_the_required_same_origin_dependency() -> None:
    operations = _real_body_operations()
    assert len(operations) >= 17
    assert {
        (method, path)
        for method, path, _route in operations
        if (method, path) in _SAME_ORIGIN_JSON_ONLY_OPERATIONS
    } == _SAME_ORIGIN_JSON_ONLY_OPERATIONS

    missing: list[tuple[str, str, str]] = []
    for method, path, route in operations:
        calls = set(_dependency_calls(route.dependant))
        expected_dependency = (
            require_same_origin_json
            if (method, path) in _SAME_ORIGIN_JSON_ONLY_OPERATIONS
            else require_same_origin_action
        )
        if expected_dependency not in calls:
            missing.append((method, path, expected_dependency.__name__))

    assert missing == []


def test_every_public_data_route_keeps_the_mode_aware_router_boundary() -> None:
    public_router_prefixes = (
        "/api/stocks",
        "/api/options",
        "/api/earnings",
        "/api/sectors",
        "/api/market",
        "/api/signals",
        "/api/catalysts",
        "/api/strength",
        "/api/breakouts",
    )
    public_routes = [
        route
        for route in _effective_fastapi_routes(main.app)
        if route.path.startswith(public_router_prefixes)
    ]
    assert public_routes

    missing = [
        route.path
        for route in public_routes
        if require_public_read_or_owner_access
        not in set(_dependency_calls(route.dependant))
    ]
    assert missing == []


def test_every_real_mutating_route_rejects_cross_site_and_form_requests() -> None:
    schema = main.app.openapi()
    mutations = [
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
        if method in {"post", "put", "patch", "delete"}
    ]
    assert len(mutations) >= 17
    client = TestClient(
        _PeerAddress(main.app, "127.0.0.1"),
        base_url="http://localhost",
    )
    try:
        for method, template in mutations:
            path = re.sub(r"\{[^}]+\}", "boundary-test", template)
            main._rl_buckets.clear()
            cross_site = client.request(
                method,
                path,
                json={},
                headers={
                    "Origin": "http://evil.example",
                    "X-Optix-Action": "1",
                },
            )
            assert cross_site.status_code == 403, (method, template, cross_site.text)

            main._rl_buckets.clear()
            missing_header = client.request(
                method,
                path,
                json={},
                headers={"Origin": "http://localhost"},
            )
            assert missing_header.status_code == 403, (
                method,
                template,
                missing_header.text,
            )

            main._rl_buckets.clear()
            form = client.request(
                method,
                path,
                data={"value": "1"},
                headers={
                    "Origin": "http://localhost",
                    "X-Optix-Action": "1",
                },
            )
            assert form.status_code == 415, (method, template, form.text)
    finally:
        client.close()


def test_frontend_integrity_and_host_validation_remain_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    monkeypatch.setattr(main, "FRONTEND_DIR", frontend)
    integrity = main._frontend_integrity()
    assert integrity["ready"] is False
    assert integrity["missing"]

    assert "example.com" in _configured_allowed_hosts(
        "127.0.0.1", "example.com"
    )
    internationalized = _configured_allowed_hosts("127.0.0.1", "faß.de")
    assert "xn--fa-hia.de" in internationalized
    assert "fass.de" not in internationalized
    assert "2001:db8::1" in _configured_allowed_hosts(
        "127.0.0.1",
        "2001:0db8:0:0:0:0:0:1",
    )
    with pytest.raises(RuntimeError):
        _configured_allowed_hosts("127.0.0.1", "*.example.com")
    with pytest.raises(RuntimeError):
        _configured_allowed_hosts("127.0.0.1", "[[::1]]")
