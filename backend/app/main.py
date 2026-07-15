from __future__ import annotations

import hashlib
import json as _json_mod
import os as _os
import time as _time
from collections import deque as _deque
from pathlib import Path

from app.runtime_environment import load_runtime_environment

# This must run before request-security and service modules inspect os.environ.
load_runtime_environment()

# Import yahoo.py first — it monkey-patches yf.Ticker to use curl_cffi session
# so all downstream yfinance usage dodges Yahoo's rate limiter.
from app.services import yahoo  # noqa: F401

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request as StarletteRequest

from app.access import (
    OwnerAccessRuntime,
    get_access_runtime,
    require_owner_access,
    require_same_origin_action,
)
from app.deployment_boundary import normalize_allowed_hosts
from app.api import (
    access,
    ai,
    breakouts,
    catalysts,
    earnings,
    integrations,
    market,
    options,
    sectors,
    settings,
    signals,
    stocks,
    strength,
    worker_actions,
)
from app.services.request_security import (
    TRUSTED_PROXY_NETWORKS as _TRUSTED_PROXY_NETWORKS,
    TRUST_PROXY_HEADERS as _TRUST_PROXY_HEADERS,
    client_ip_from_scope,
    request_is_https,
)


_TRUTHY_VALUES = {"1", "true", "yes"}
_APP_VERSION = _os.environ.get("APP_VERSION", "").strip() or "dev"
_APP_COMMIT = _os.environ.get("APP_COMMIT", "").strip() or "unknown"
_HOST_BIND = _os.environ.get("HOST_BIND", "127.0.0.1").strip() or "127.0.0.1"
_FRONTEND_MANIFEST_REQUIRED = (
    _os.environ.get("FRONTEND_MANIFEST_REQUIRED", "").strip().lower() in _TRUTHY_VALUES
)
_FRONTEND_MANIFEST_PATH = _os.environ.get("FRONTEND_MANIFEST_PATH", "").strip()


def _configured_allowed_hosts(host_bind: str, raw: str) -> list[str]:
    return list(normalize_allowed_hosts(host_bind, raw))


_ACCESS_RUNTIME = get_access_runtime()
_DEPLOYMENT_BOUNDARY = _ACCESS_RUNTIME.validate_startup(
    _HOST_BIND,
    allowed_hosts=_os.environ.get("ALLOWED_HOSTS", ""),
    trust_proxy_headers=_os.environ.get("TRUST_PROXY_HEADERS", "false"),
    trusted_proxy_cidrs=_os.environ.get("TRUSTED_PROXY_CIDRS", ""),
)
_ALLOWED_HOSTS = list(_DEPLOYMENT_BOUNDARY.allowed_hosts)


app = FastAPI(
    title="Optix Pro Options Visualization API",
    description="Personal stock, options, signal, and market-data API.",
    version=_APP_VERSION,
)
app.state.access_runtime = _ACCESS_RUNTIME

# Rate limiter state. deque + per-IP buckets, pruned lazily so the dict can't
# grow without bound when many distinct IPs hit the API.
_rl_buckets: dict[str, _deque] = {}
_RL_HEAVY_LIMIT = 30    # max requests / window for heavy endpoints
_RL_LIGHT_LIMIT = 200   # max requests / window for cheap endpoints
_RL_WINDOW = 60         # seconds
_RL_MAX_KEYS = 10_000   # safety valve against IP-churn memory growth
_rl_last_prune = 0.0

_HEAVY_API_PREFIXES = (
    "/api/ai/",
    "/api/earnings/",
    "/api/options/",
    "/api/sectors/",
    "/api/signals/",
    "/api/strength/",
    "/api/breakouts/",
)
_LIGHT_API_PATHS = {
    "/api/ai/status",
    "/api/market/status",
    "/api/strength/profiles",
    "/api/breakouts/current",
    "/api/breakouts/status",
}
_HTML_CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'"
)
_LEGACY_SERVICE_PATHS = {"/api/integrations/macrolens/v1/focus-context"}
_PUBLIC_ACCESS_PATHS = {
    "/health",
    "/ready",
}
_PASSWORD_ENTRY_PATHS = {
    "/login.html",
    "/api/access/login",
    "/static/js/login.js",
    "/static/favicon.svg",
}


def _scope_header(scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            try:
                return value.decode("latin-1")
            except Exception:
                return ""
    return ""


def _scope_client_ip(scope) -> str:
    return client_ip_from_scope(
        scope,
        enabled=_TRUST_PROXY_HEADERS,
        networks=_TRUSTED_PROXY_NETWORKS,
    )


def _scope_is_https(scope) -> bool:
    return request_is_https(
        scope,
        enabled=_TRUST_PROXY_HEADERS,
        networks=_TRUSTED_PROXY_NETWORKS,
    )


def _raw_path_has_unsafe_escape(scope) -> bool:
    """Keep the one-release service compatibility path exact."""

    raw_path = scope.get("raw_path", b"")
    if isinstance(raw_path, str):
        raw_path = raw_path.encode("latin-1", errors="ignore")
    lowered = bytes(raw_path).lower()
    return b"\\" in lowered or any(
        marker in lowered for marker in (b"%2f", b"%5c", b"%25")
    )


def _is_heavy_api_path(path: str, method: str = "GET") -> bool:
    normalized_method = method.upper()
    if normalized_method == "GET" and (
        path.startswith("/api/ai/jobs/")
        or path.startswith("/api/catalysts/")
        or path == "/api/catalysts/status"
        or path == "/api/catalysts/feed"
        or path == "/api/catalysts/calendar"
    ):
        return False
    if normalized_method in {"POST", "PUT", "PATCH", "DELETE"} and (
        path.startswith("/api/ai/")
        or path.startswith("/api/catalysts/")
    ):
        return True
    if path in _LIGHT_API_PATHS:
        return False
    if path.endswith("/logo"):
        return False
    return (
        path == "/api/market/indices"
        or path.startswith(_HEAVY_API_PREFIXES)
        or path.startswith("/api/stocks/")
    )


def _prune_rl_buckets(now: float) -> None:
    """Drop stale buckets periodically and enforce the hard key limit."""
    global _rl_last_prune
    if now - _rl_last_prune < _RL_WINDOW and len(_rl_buckets) < _RL_MAX_KEYS:
        return
    _rl_last_prune = now
    cutoff = now - _RL_WINDOW
    for key in [k for k, dq in _rl_buckets.items() if not dq or dq[-1] < cutoff]:
        _rl_buckets.pop(key, None)
    # A burst from many distinct addresses can leave every bucket fresh. Keep
    # one slot free for the current request instead of relying on expiry alone.
    if len(_rl_buckets) >= _RL_MAX_KEYS:
        remove_count = len(_rl_buckets) - _RL_MAX_KEYS + 1
        oldest = sorted(
            _rl_buckets,
            key=lambda key: _rl_buckets[key][-1] if _rl_buckets[key] else float("-inf"),
        )[:remove_count]
        for key in oldest:
            _rl_buckets.pop(key, None)


async def _send_json(send, status: int, payload: dict, extra_headers: list | None = None) -> None:
    body = _json_mod.dumps(payload).encode("utf-8")
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ] + (extra_headers or [])
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


class _GatewayMiddleware:
    """Pure-ASGI gateway for auth, limits, cache policy, and security headers.

    Replaces three stacked BaseHTTPMiddleware layers (each of which spins up an
    anyio task group per request) with one cheap pass.

    Cache policy:
    - HTML ("/" and *.html): no-store — deploys must show up immediately.
    - /static/*: five-minute browser cache plus normal ETag revalidation.
    - API and health metadata: private/no-store.
    """

    def __init__(
        self,
        app,
        access_runtime: OwnerAccessRuntime | None = None,
    ):
        self.app = app
        self.access_runtime = access_runtime or _ACCESS_RUNTIME

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")
        is_html = path == "/" or path.endswith(".html")
        is_static = path.startswith("/static/")

        async def send_with_response_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                headers["Referrer-Policy"] = "no-referrer"
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=(), payment=(), usb=()"
                )
                headers["Cross-Origin-Opener-Policy"] = "same-origin"
                headers["X-XSS-Protection"] = "0"
                headers["X-App-Version"] = _APP_VERSION
                headers["X-App-Commit"] = _APP_COMMIT
                if _scope_is_https(scope):
                    headers["Strict-Transport-Security"] = "max-age=31536000"

                if is_html:
                    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    headers["CDN-Cache-Control"] = "no-store"
                    headers["Cloudflare-CDN-Cache-Control"] = "no-store"
                    headers["Content-Security-Policy"] = _HTML_CSP
                elif is_static:
                    headers["Cache-Control"] = "public, max-age=300, stale-while-revalidate=60"
                elif path.startswith("/api/") or path in {"/health", "/ready"}:
                    headers["Cache-Control"] = "private, no-store"
            await send(message)

        unsafe_raw_path = _raw_path_has_unsafe_escape(scope)
        legacy_service_request = bool(
            not unsafe_raw_path
            and method == "GET"
            and path in _LEGACY_SERVICE_PATHS
        )
        publicly_available = bool(
            path in _PUBLIC_ACCESS_PATHS
            or (
                self.access_runtime.mode == "password"
                and path in _PASSWORD_ENTRY_PATHS
            )
        )
        owner_request = StarletteRequest(scope, receive=receive)
        owner_access = self.access_runtime.request_is_owner(owner_request)
        scope.setdefault("state", {})["owner_access"] = owner_access

        if (
            method != "OPTIONS"
            and not publicly_available
            and not legacy_service_request
            and not owner_access
        ):
            if self.access_runtime.mode == "password" and is_html:
                response = RedirectResponse("/login.html", status_code=303)
                return await response(scope, receive, send_with_response_headers)
            code = (
                "owner_login_required"
                if self.access_runtime.mode == "password"
                else "private_network_required"
            )
            return await _send_json(
                send_with_response_headers,
                401 if self.access_runtime.mode == "password" else 403,
                {"error": code, "message": "Owner access is required"},
            )

        if method != "OPTIONS" and path.startswith("/api/"):
            # ── Per-IP rate limit ──
            is_heavy = _is_heavy_api_path(path, method)
            limit = _RL_HEAVY_LIMIT if is_heavy else _RL_LIGHT_LIMIT
            key = f"{_scope_client_ip(scope)}:{'h' if is_heavy else 'l'}"
            now = _time.time()
            _prune_rl_buckets(now)
            bucket = _rl_buckets.get(key)
            if bucket is None:
                bucket = _rl_buckets[key] = _deque()
            cutoff = now - _RL_WINDOW
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                return await _send_json(
                    send_with_response_headers, 429,
                    {"error": "rate_limited", "message": f"Too many requests; try again in {_RL_WINDOW}s"},
                    extra_headers=[(b"retry-after", str(_RL_WINDOW).encode())],
                )
            bucket.append(now)

        return await self.app(scope, receive, send_with_response_headers)


app.add_middleware(_GatewayMiddleware, access_runtime=_ACCESS_RUNTIME)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

# The gateway gives HTML requests a friendly redirect. Router dependencies are
# the single API boundary and remain in force when routers are mounted by tools
# that do not use the production gateway.
_OWNER_DEPENDENCIES = [
    Depends(require_owner_access),
    Depends(require_same_origin_action),
]
app.include_router(stocks.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(options.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(earnings.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(sectors.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(market.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(signals.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(ai.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(catalysts.router, dependencies=_OWNER_DEPENDENCIES)
# This one read-only route is the time-boxed service compatibility adapter.
app.include_router(integrations.router)
app.include_router(strength.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(breakouts.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(worker_actions.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(access.router)
app.include_router(settings.router)

# Docker-compose runs from /app/backend; local runs may be from repo root.
# Allow override via FRONTEND_DIR env var for unusual deployments.
_env_dir = _os.environ.get("FRONTEND_DIR")
if _env_dir:
    FRONTEND_DIR = Path(_env_dir).resolve()
else:
    # Try a few candidate paths
    _here = Path(__file__).resolve()
    _candidates = [
        _here.parents[2] / "frontend",  # /app/frontend (docker)
        _here.parents[3] / "frontend",  # /repo/frontend (local from backend/)
    ]
    FRONTEND_DIR = next((c for c in _candidates if c.exists()), _candidates[0])


_FRONTEND_REQUIRED_FILES = (
    "index.html",
    "login.html",
    "static/favicon.svg",
    "static/css/optix-deck.css",
    "static/css/optix-catalysts.css",
    "static/js/theme-init.js",
    "static/js/login.js",
    "static/js/deck-api.js",
    "static/js/deck-ai-jobs.js",
    "static/js/deck-catalysts.js",
    "static/js/deck-app.js",
)
_FRONTEND_MANIFEST_NAME = ".integrity-manifest"


def _frontend_file_list() -> tuple[tuple[str, ...], list[str], bool]:
    manifest_path = (
        Path(_FRONTEND_MANIFEST_PATH)
        if _FRONTEND_MANIFEST_PATH
        else FRONTEND_DIR / _FRONTEND_MANIFEST_NAME
    )
    manifest_label = manifest_path.name
    if not manifest_path.is_file():
        if _FRONTEND_MANIFEST_REQUIRED:
            return (), [manifest_label], False
        return _FRONTEND_REQUIRED_FILES, [], False

    try:
        raw_entries = manifest_path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return (), [manifest_label], True

    entries: list[str] = []
    for raw_entry in raw_entries:
        relative_path = raw_entry.strip().removeprefix("./")
        path_parts = Path(relative_path).parts
        if (
            not relative_path
            or relative_path.startswith("/")
            or ".." in path_parts
            or relative_path == _FRONTEND_MANIFEST_NAME
        ):
            return (), [manifest_label], True
        if relative_path not in entries:
            entries.append(relative_path)
    if not entries:
        return (), [manifest_label], True
    return tuple(entries), [], True


def _frontend_integrity() -> dict:
    digest = hashlib.sha256()
    required_files, missing, uses_manifest = _frontend_file_list()
    for relative_path in required_files:
        candidate = FRONTEND_DIR / relative_path
        try:
            content = candidate.read_bytes()
        except OSError:
            missing.append(relative_path)
            continue
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
    ready = not missing
    return {
        "ready": ready,
        "required_files": len(required_files),
        "manifest": uses_manifest,
        "missing": missing,
        "sha256": digest.hexdigest() if ready else None,
    }


def _runtime_payload(status: str, frontend: dict) -> dict:
    return {
        "status": status,
        "app_version": _APP_VERSION,
        "app_commit": _APP_COMMIT,
        "frontend": frontend,
    }


@app.get("/health")
async def health():
    frontend = _frontend_integrity()
    status = "ok" if frontend["ready"] else "degraded"
    return _runtime_payload(status, frontend)


@app.get("/ready")
async def ready():
    frontend = _frontend_integrity()
    status = "ready" if frontend["ready"] else "not_ready"
    return JSONResponse(
        _runtime_payload(status, frontend),
        status_code=200 if frontend["ready"] else 503,
    )


if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    import warnings
    warnings.warn(f"FRONTEND_DIR not found at {FRONTEND_DIR}; static serving disabled")
