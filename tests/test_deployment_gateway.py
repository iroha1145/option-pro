from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OWNER_SESSION_SECONDS,
    LoginRejected,
    OwnerAccessRuntime,
    hash_owner_password,
    require_owner_access,
    require_same_origin_action,
)
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
        return {"page": "owner"}

    @app.get("/login.html")
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


def test_password_mode_redirects_pages_and_protects_all_other_apis() -> None:
    with TestClient(
        _test_app(_runtime("password")),
        base_url="https://testserver",
        follow_redirects=False,
    ) as client:
        page = client.get("/")
        assert page.status_code == 303
        assert page.headers["location"] == "/login.html"
        assert client.get("/api/value").status_code == 401
        assert client.get("/api/access/status").status_code == 401
        assert client.get("/login.html").status_code == 200


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
    with pytest.raises(RuntimeError):
        _configured_allowed_hosts("127.0.0.1", "*.example.com")
