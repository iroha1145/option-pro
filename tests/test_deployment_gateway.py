from __future__ import annotations

from collections import deque

import pytest
from fastapi.testclient import TestClient

from app import main
from app.services.request_security import (
    client_ip_from_scope,
    parse_trusted_proxy_cidrs,
)


def _client() -> TestClient:
    return TestClient(main.app, base_url="http://localhost")


def test_public_bind_requires_auth_or_explicit_private_network_opt_in():
    with pytest.raises(RuntimeError, match="non-loopback HOST_BIND"):
        main._validate_public_bind("0.0.0.0", "", False)

    main._validate_public_bind("127.0.0.1", "", False)
    main._validate_public_bind("::1", "", False)
    main._validate_public_bind("0.0.0.0", "strong-token", False)
    main._validate_public_bind("0.0.0.0", "", True)


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

    static = client.get("/static/css/styles.css", headers={"accept-encoding": "gzip"})
    assert static.status_code == 200
    assert "max-age=300" in static.headers["cache-control"]
    assert static.headers["content-encoding"] == "gzip"


def test_gateway_auth_errors_keep_security_headers(monkeypatch):
    monkeypatch.setattr(main, "_APP_AUTH_TOKEN", "test-secret")
    client = _client()

    response = client.get("/api/market/status")

    assert response.status_code == 401
    assert response.json()["error"] == "unauthorized"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"


def test_expensive_provider_routes_use_heavy_rate_limit_bucket():
    assert main._is_heavy_api_path("/api/ai/analyze-alerts")
    assert main._is_heavy_api_path("/api/market/indices")
    assert main._is_heavy_api_path("/api/options/AAPL/chain")
    assert main._is_heavy_api_path("/api/sectors/technology/iv-ranking")
    assert main._is_heavy_api_path("/api/signals/stock/AAPL")
    assert main._is_heavy_api_path("/api/stocks/AAPL/chart")
    assert main._is_heavy_api_path("/api/strength/scan")
    assert not main._is_heavy_api_path("/api/market/status")
    assert not main._is_heavy_api_path("/api/stocks/search")
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
