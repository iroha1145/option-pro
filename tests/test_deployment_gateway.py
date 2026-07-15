from __future__ import annotations

from collections import deque

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.catalysts.focus_config import (
    FocusContextSettings,
    get_focus_context_settings,
)
from app.services.catalysts.repository import CatalystRepository
from app.services.request_security import (
    client_ip_from_scope,
    parse_trusted_proxy_cidrs,
)


def _client() -> TestClient:
    return TestClient(main.app, base_url="http://localhost")


async def _empty_api(scope, receive, send) -> None:
    """Small downstream app for exercising the real ASGI gateway only."""

    await send(
        {
            "type": "http.response.start",
            "status": 204,
            "headers": [],
        }
    )
    await send({"type": "http.response.body", "body": b""})


def _gateway_client() -> TestClient:
    return TestClient(
        main._GatewayMiddleware(_empty_api),
        base_url="http://localhost",
    )


def test_public_bind_requires_auth_or_explicit_private_network_opt_in():
    with pytest.raises(RuntimeError, match="non-loopback HOST_BIND"):
        main._validate_public_bind("0.0.0.0", "", False)

    main._validate_public_bind("127.0.0.1", "", False)
    main._validate_public_bind("::1", "", False)
    main._validate_public_bind("0.0.0.0", "strong-token", False)
    main._validate_public_bind("0.0.0.0", "", True)


def test_public_read_requires_private_routes_to_have_an_auth_token():
    main._validate_public_read_auth(False, "")
    main._validate_public_read_auth(True, "strong-token")

    with pytest.raises(RuntimeError, match="requires APP_AUTH_TOKEN"):
        main._validate_public_read_auth(True, "")


def test_health_and_ready_expose_build_and_frontend_integrity():
    client = _client()

    health = client.get("/health")
    assert health.status_code == 200
    payload = health.json()
    assert payload["status"] == "ok"
    assert payload["app_version"] == main._APP_VERSION
    assert payload["app_commit"] == main._APP_COMMIT
    assert payload["frontend"]["ready"] is True
    assert len(payload["frontend"]["sha256"]) == 64
    assert health.headers["cache-control"] == "private, no-store"

    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["status"] == "ready"


def test_ready_fails_when_required_frontend_files_are_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path)
    client = _client()

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["frontend"]["ready"] is False
    assert set(response.json()["frontend"]["missing"]) == set(main._FRONTEND_REQUIRED_FILES)


def test_frontend_manifest_checks_every_baked_asset(monkeypatch, tmp_path):
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / main._FRONTEND_MANIFEST_NAME).write_text(
        "./index.html\n./static/js/missing.js\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(main, "_FRONTEND_MANIFEST_REQUIRED", True)

    integrity = main._frontend_integrity()

    assert integrity["ready"] is False
    assert integrity["manifest"] is True
    assert integrity["required_files"] == 2
    assert integrity["missing"] == ["static/js/missing.js"]


def test_gateway_adds_security_cache_and_compression_headers():
    client = _client()

    root = client.get("/", headers={"accept-encoding": "gzip"})
    assert root.status_code == 200
    assert "no-store" in root.headers["cache-control"]
    assert "frame-ancestors 'none'" in root.headers["content-security-policy"]
    assert "fonts.googleapis.com" not in root.headers["content-security-policy"]
    assert "financialmodelingprep.com" not in root.headers["content-security-policy"]
    assert root.headers["x-content-type-options"] == "nosniff"
    assert root.headers["x-frame-options"] == "DENY"
    assert root.headers["x-app-commit"] == main._APP_COMMIT
    assert root.headers["content-encoding"] == "gzip"

    static = client.get("/static/css/optix-deck.css", headers={"accept-encoding": "gzip"})
    assert static.status_code == 200
    assert "max-age=300" in static.headers["cache-control"]
    assert static.headers["content-encoding"] == "gzip"


def test_gateway_auth_errors_keep_security_headers(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", False)
    client = _client()

    response = client.get("/api/market/status")

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


def test_public_read_mode_serves_display_data_without_exposing_actions(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    client = _client()

    public = client.get("/api/market/status")

    assert public.status_code == 200
    assert main._is_public_read_api("GET", "/api/stocks/watchlist")
    assert main._is_public_read_api("GET", "/api/breakouts/events/evt_123")
    assert main._is_public_read_api("GET", "/api/catalysts/market-focus-cycles/latest")
    assert main._is_public_read_api("POST", "/api/catalysts/tickers/batch")

    assert not main._is_public_read_api("GET", "/api/ai/jobs/job_123456")
    assert not main._is_public_read_api(
        "GET", "/api/catalysts/analysis-jobs/job_123456"
    )
    assert not main._is_public_read_api("POST", "/api/ai/jobs/earnings-impact")
    assert not main._is_public_read_api("POST", "/api/catalysts/refresh")
    assert not main._is_public_read_api("GET", "/api/stocks/future-private-route")
    assert not main._is_public_read_api("GET", "/api/catalysts/future-private-route")

    paid = client.post("/api/ai/jobs/earnings-impact", json={})
    refresh = client.post("/api/catalysts/refresh", json={})
    job = client.get("/api/ai/jobs/job_123456")

    assert paid.status_code == 401
    assert refresh.status_code == 401
    assert job.status_code == 401


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/market/indices"),
        ("GET", "/api/stocks/AAPL/chart"),
        ("GET", "/api/stocks/^GSPC"),
        ("GET", "/api/stocks/^GSPC/chart"),
        ("GET", "/api/stocks/^GSPC/signals"),
        ("GET", "/api/signals/stock/BRK.B"),
        ("GET", "/api/signals/stock/^GSPC"),
        ("GET", "/api/breakouts/events/evt_123"),
        ("GET", "/api/breakouts/tickers/AMD"),
        ("GET", "/api/breakouts/tickers/ABCDEFGHIJKLMNO"),
        ("GET", "/api/sectors/technology/iv-ranking"),
        ("GET", "/api/options/AAPL/chain"),
        ("GET", "/api/catalysts/news/133996"),
        ("GET", "/api/catalysts/tickers/TSLA"),
        (
            "GET",
            "/api/catalysts/market-focus-cycles/"
            "mfc_0123456789abcdef0123456789abcdef",
        ),
        ("POST", "/api/catalysts/tickers/batch"),
    ],
)
def test_public_read_allowlist_uses_exact_route_shapes(method, path):
    assert main._is_public_read_api(method, path)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/market/future-admin"),
        ("GET", "/api/stocks/AAPL/chart/extra"),
        ("GET", "/api/stocks/AAPL%2Fchart"),
        ("GET", "/api/stocks/aapl/chart"),
        ("GET", "/api/stocks/future-private-route"),
        ("GET", "/api/breakouts/tickers/ABCDEFGHIJKLMNOP"),
        ("GET", "/api/stocks//watchlist"),
        ("GET", "/api/stocks/watchlist/"),
        ("GET", "/API/stocks/watchlist"),
        ("GET", "/api/strength/stocks/NVDA"),
        ("GET", "/api/sectors/technology/heatmap"),
        ("GET", "/api/ai/status"),
        ("GET", "/api/ai/earnings-impact/META"),
        ("GET", "/api/ai/jobs/job_123456"),
        ("GET", "/api/catalysts/feed/extra"),
        ("GET", "/api/catalysts/analysis-jobs/job_123456"),
        ("GET", "/api/integrations/macrolens/v1/focus-context"),
        ("POST", "/api/catalysts/refresh"),
        ("PUT", "/api/stocks/watchlist"),
        ("PATCH", "/api/catalysts/news/133996"),
        ("DELETE", "/api/catalysts/news/133996"),
    ],
)
def test_public_read_allowlist_fails_closed(method, path):
    assert not main._is_public_read_api(method, path)


def test_public_read_still_uses_normal_rate_limit_without_auth_failures(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    monkeypatch.setattr(main, "_RL_LIGHT_LIMIT", 1)
    monkeypatch.setattr(main, "_RL_WINDOW", 60)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()
    client = _client()

    first = client.get("/api/market/status")
    limited = client.get("/api/market/status")

    assert first.status_code == 200
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert main._auth_fail_buckets == {}
    main._rl_buckets.clear()


def test_public_watchlist_disallows_custom_provider_batches_without_token(
    monkeypatch,
):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()
    client = _client()

    anonymous = client.get("/api/stocks/watchlist?tickers=AAPL")
    authenticated = client.get(
        "/api/stocks/watchlist?tickers=",
        headers={"authorization": "Bearer test-secret"},
    )

    assert anonymous.status_code == 403
    assert anonymous.json()["detail"] == (
        "Custom watchlist queries require app authentication"
    )
    assert authenticated.status_code == 400
    main._rl_buckets.clear()


@pytest.mark.parametrize(
    ("path", "detail"),
    [
        (
            "/api/earnings/upcoming?refresh=true",
            "Earnings refresh requires app authentication",
        ),
        (
            "/api/options/unusual?type=call",
            "Custom unusual-options scans require app authentication",
        ),
        (
            "/api/options/unusual?min_vol_oi=1.01",
            "Custom unusual-options scans require app authentication",
        ),
    ],
)
def test_public_read_disallows_provider_cache_bypass_parameters(
    monkeypatch,
    path,
    detail,
):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()

    response = _client().get(path)

    assert response.status_code == 403
    assert response.json()["detail"] == detail
    main._rl_buckets.clear()


def test_public_index_route_works_with_literal_and_encoded_caret(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()
    client = _gateway_client()

    literal = client.get("/api/signals/stock/^GSPC")
    encoded = client.get("/api/signals/stock/%5EGSPC")

    assert literal.status_code == 204
    assert encoded.status_code == 204
    assert main._auth_fail_buckets == {}
    main._rl_buckets.clear()


@pytest.mark.parametrize(
    "path",
    [
        "/api/stocks/AAPL%2Fchart",
        "/api/stocks/AAPL%2fchart",
        "/api/stocks/AAPL%252Fchart",
        "/api/stocks/AAPL%5Cchart",
        "/api/stocks/AAPL\\chart",
    ],
)
def test_public_read_rejects_ambiguous_encoded_route_separators(
    monkeypatch,
    path,
):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()

    response = _gateway_client().get(path)

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()


@pytest.mark.parametrize(
    ("method", "path", "expected_status"),
    [
        ("GET", "/api/market/status", 204),
        ("POST", "/api/market/status", 401),
        ("PUT", "/api/market/status", 401),
        ("PATCH", "/api/market/status", 401),
        ("DELETE", "/api/market/status", 401),
        ("HEAD", "/api/market/status", 401),
        ("POST", "/api/catalysts/tickers/batch", 204),
        ("GET", "/api/catalysts/tickers/batch", 401),
    ],
)
def test_public_read_gateway_keeps_method_boundaries(
    monkeypatch,
    method,
    path,
    expected_status,
):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()

    response = _gateway_client().request(method, path)

    assert response.status_code == expected_status
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()


def test_public_heavy_route_still_uses_heavy_rate_limit(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    monkeypatch.setattr(main, "_RL_HEAVY_LIMIT", 1)
    monkeypatch.setattr(main, "_RL_LIGHT_LIMIT", 100)
    monkeypatch.setattr(main, "_RL_WINDOW", 60)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()
    client = _gateway_client()

    first = client.get("/api/signals/stock/%5EGSPC")
    limited = client.get("/api/signals/stock/%5EGSPC")

    assert first.status_code == 204
    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"
    assert main._auth_fail_buckets == {}
    main._rl_buckets.clear()


def test_public_mode_does_not_replace_macrolens_focus_hmac(
    monkeypatch,
    tmp_path,
):
    cache_path = tmp_path / "focus.db"
    CatalystRepository(cache_path).initialize()
    settings = FocusContextSettings(
        _env_file=None,
        MACROLENS_CACHE_DB_PATH=cache_path,
        MACROLENS_FOCUS_KEY_ID="focus-read",
        MACROLENS_FOCUS_SECRET="focus-secret-0123456789abcdef-0001",
        MACROLENS_FOCUS_ALLOWED_CIDRS="127.0.0.0/8",
    )
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    monkeypatch.setattr(main, "_PUBLIC_READ_API_ENABLED", True)
    main._auth_fail_buckets.clear()
    main._rl_buckets.clear()
    main.app.dependency_overrides[get_focus_context_settings] = lambda: settings
    try:
        client = TestClient(
            main.app,
            base_url="https://localhost",
            client=("127.0.0.1", 50000),
        )
        unsigned = client.get(
            "/api/integrations/macrolens/v1/focus-context"
        )
        browser_token_only = client.get(
            "/api/integrations/macrolens/v1/focus-context",
            headers={"authorization": "Bearer test-secret"},
        )
    finally:
        main.app.dependency_overrides.pop(get_focus_context_settings, None)

    for response in (unsigned, browser_token_only):
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "focus_invalid_signature"
    main._rl_buckets.clear()


def test_expensive_provider_routes_use_heavy_rate_limit_bucket():
    assert main._is_heavy_api_path("/api/ai/analyze-alerts")
    assert main._is_heavy_api_path("/api/market/indices")
    assert main._is_heavy_api_path("/api/options/AAPL/chain")
    assert main._is_heavy_api_path("/api/sectors/technology/iv-ranking")
    assert main._is_heavy_api_path("/api/signals/stock/AAPL")
    assert main._is_heavy_api_path("/api/stocks/AAPL/chart")
    assert main._is_heavy_api_path("/api/strength/scan")
    assert not main._is_heavy_api_path("/api/market/status")
    assert main._is_heavy_api_path("/api/stocks/search")
    assert not main._is_heavy_api_path("/api/strength/profiles")


def test_rate_limit_bucket_map_has_a_hard_capacity(monkeypatch):
    monkeypatch.setattr(main, "_RL_MAX_KEYS", 3)
    monkeypatch.setattr(main, "_RL_WINDOW", 60)
    monkeypatch.setattr(main, "_rl_last_prune", 0.0)
    main._rl_buckets.clear()
    now = 1_000.0
    main._rl_buckets.update({
        "oldest": deque([now - 3]),
        "middle": deque([now - 2]),
        "newest": deque([now - 1]),
    })

    main._prune_rl_buckets(now)

    assert len(main._rl_buckets) == 2
    assert "oldest" not in main._rl_buckets


def test_proxy_headers_are_used_only_from_trusted_peers() -> None:
    networks = parse_trusted_proxy_cidrs("10.0.0.0/8,2001:db8::/32")
    direct_scope = {
        "client": ("198.51.100.7", 443),
        "headers": [(b"x-forwarded-for", b"203.0.113.9")],
    }
    trusted_scope = {
        "client": ("10.0.0.5", 443),
        "headers": [
            (b"x-forwarded-for", b"203.0.113.9, 10.0.0.4"),
        ],
    }

    assert (
        client_ip_from_scope(direct_scope, enabled=True, networks=networks)
        == "198.51.100.7"
    )
    assert (
        client_ip_from_scope(trusted_scope, enabled=True, networks=networks)
        == "203.0.113.9"
    )


def test_generic_trusted_proxy_does_not_accept_a_spoofed_cloudflare_header() -> None:
    networks = parse_trusted_proxy_cidrs("10.0.0.0/8")
    scope = {
        "client": ("10.0.0.5", 443),
        "headers": [
            (b"cf-connecting-ip", b"192.0.2.77"),
            (b"x-forwarded-for", b"203.0.113.9, 10.0.0.4"),
        ],
    }

    assert (
        client_ip_from_scope(scope, enabled=True, networks=networks)
        == "203.0.113.9"
    )


def test_invalid_host_is_rejected() -> None:
    response = _client().get("/health", headers={"host": "evil.example"})
    assert response.status_code == 400


def test_allowed_hosts_accept_explicit_domains_and_reject_wildcards() -> None:
    assert "option.example.com" in main._configured_allowed_hosts(
        "127.0.0.1", "option.example.com"
    )
    with pytest.raises(RuntimeError, match="explicit host names"):
        main._configured_allowed_hosts("127.0.0.1", "*.example.com")


def test_failed_authentication_has_an_independent_rate_limit(monkeypatch) -> None:
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "valid-secret")
    monkeypatch.setattr(main, "_AUTH_FAIL_LIMIT", 2)
    monkeypatch.setattr(main, "_auth_fail_last_prune", 0.0)
    main._auth_fail_buckets.clear()
    client = _client()

    assert client.get("/api/market/status").status_code == 401
    assert client.get("/api/market/status").status_code == 401
    limited = client.get("/api/market/status")
    assert limited.status_code == 429
    assert limited.json()["error"] == "auth_rate_limited"

    valid = client.get(
        "/api/market/status",
        headers={"authorization": "Bearer valid-secret"},
    )
    assert valid.status_code == 200
