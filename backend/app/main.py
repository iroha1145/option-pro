from __future__ import annotations

import hashlib
import json as _json_mod
import os as _os
import re as _re
import threading
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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers, MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.gzip import GZipMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import PlainTextResponse, Response

from app.access import (
    OwnerAccessRuntime,
    canonical_request_host,
    get_access_runtime,
    is_public_earnings_ai_read_path,
    is_public_earnings_impact_action_path,
    is_public_stock_pull_path,
    request_has_account_session,
    request_owner_access_context,
    require_public_read_or_owner_access,
    require_same_origin_action,
)
from app.deployment_boundary import canonicalize_hostname, normalize_allowed_hosts
from app.api import (
    access,
    accounts,
    ai,
    breakouts,
    catalysts,
    diagnostics,
    earnings,
    macro_conditions,
    market,
    options,
    runtime_settings,
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


class _ExactTrustedHostMiddleware:
    """Validate one explicit Host authority, including bracketed IPv6."""

    def __init__(self, app, *, allowed_hosts: list[str]) -> None:
        self.app = app
        normalized = {canonicalize_hostname(host) for host in allowed_hosts}
        if not normalized or None in normalized:
            raise RuntimeError("allowed hosts are not canonical")
        self.allowed_hosts = frozenset(normalized)

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        values = Headers(scope=scope).getlist("host")
        hostname = canonical_request_host(values[0]) if len(values) == 1 else None
        if hostname not in self.allowed_hosts:
            response = PlainTextResponse("Invalid host header", status_code=400)
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


app = FastAPI(
    title="Optix Pro Options Visualization API",
    description="Personal stock, options, signal, and market-data API.",
    version=_APP_VERSION,
    # Documentation routes stay off in this deployment (audit P3-7). The gateway
    # treats extension-less GETs as SPA documents, so /docs and /redoc would load
    # their shell while /openapi.json stayed behind the access gate -- a half-open
    # page that exposes an entry point and delivers nothing.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.state.access_runtime = _ACCESS_RUNTIME


@app.exception_handler(RequestValidationError)
async def _redacted_request_validation_error(
    _request: StarletteRequest,
    _exc: RequestValidationError,
) -> JSONResponse:
    """Keep submitted values out of every validation response."""

    return JSONResponse(
        status_code=422,
        content={
            "detail": [
                {
                    "type": "request_validation_failed",
                    "loc": ["request"],
                    "msg": "Invalid request",
                }
            ]
        },
        headers={"Cache-Control": "no-store"},
    )

# Rate limiter state. deque + per-IP buckets, pruned lazily so the dict can't
# grow without bound when many distinct IPs hit the API.
_rl_buckets: dict[str, _deque] = {}
_RL_HEAVY_LIMIT = 30    # max requests / window for heavy endpoints
_RL_LIGHT_LIMIT = 200   # max requests / window for cheap endpoints
_RL_MARKET_READ_LIMIT = 200  # independently bounded cache-protected market reads
_RL_WINDOW = 60         # seconds
_RL_MAX_KEYS = 10_000   # safety valve against IP-churn memory growth
_rl_last_prune = 0.0

_HEAVY_API_PREFIXES = (
    "/api/ai/",
    "/api/earnings/",
    "/api/options/",
    "/api/sectors/",
    "/api/signals/",
    "/api/catalysts/",
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
_CACHED_MARKET_READ_PATHS = {
    "/api/stocks/watchlist",
    "/api/stocks/search",
    "/api/strength/scan",
}
_CACHED_MARKET_READ_PATTERNS = tuple(
    _re.compile(pattern, _re.IGNORECASE)
    for pattern in (
        r"^/api/stocks/[^/]+$",
        r"^/api/stocks/[^/]+/(?:signals|chart|technical)$",
        r"^/api/options/[^/]+/(?:expirations|chain)$",
        r"^/api/signals/stock/[^/]+$",
        r"^/api/strength/stocks/[^/]+$",
        r"^/api/breakouts/tickers/[^/]+$",
    )
)
_PROVIDER_WORK_READ_PATTERNS = tuple(
    _re.compile(pattern, _re.IGNORECASE)
    for pattern in (
        r"^/api/options/[^/]+/(?:expirations|chain)$",
        r"^/api/sectors/[^/]+/(?:iv-ranking|heatmap)$",
    )
)
_HTML_CSP = (
    "default-src 'self'; base-uri 'none'; object-src 'none'; frame-ancestors 'none'; "
    "script-src 'self'; style-src 'self' 'unsafe-inline'; font-src 'self'; "
    "img-src 'self' data:; connect-src 'self'; form-action 'self'"
)
_PUBLIC_ACCESS_PATHS = {
    "/health",
    "/ready",
}
_PUBLIC_DOCUMENT_PATHS = {
    "/",
    "/index.html",
}
# SPA(BrowserRouter)文档路径:无扩展名的 GET 路径由 index.html 壳接管。
_SPA_EXCLUDED_PREFIXES = ("/api/", "/static/", "/assets/")
# 构建产物根目录的公共静态资产(手绘插画/Logo),登录前也需可加载。
_ROOT_PUBLIC_ASSETS = frozenset({
    "/logo.svg",
    "/login-motif.svg",
    "/texture-dots.svg",
    "/empty-scan.svg",
    "/empty-radar.svg",
    "/empty-news.svg",
    "/empty-watchlist.svg",
    "/empty-chart.svg",
})


def _is_spa_document_path(path: str) -> bool:
    if not path.startswith("/") or path in _PUBLIC_ACCESS_PATHS:
        return False
    if any(path.startswith(prefix) for prefix in _SPA_EXCLUDED_PREFIXES):
        return False
    return "." not in path.rsplit("/", 1)[-1]
_PUBLIC_READ_API_PATHS = {
    "/api/access/status",
    "/api/stocks/watchlist",
    "/api/stocks/search",
    "/api/options/unusual",
    "/api/earnings/upcoming",
    "/api/sectors",
    "/api/market/indices",
    "/api/market/status",
    # CTA 趋势资金代理估算：只读 worker 快照（指纹 ETag），无供应商工作。
    "/api/market/cta",
    # Optix 宏观环境 reads are bounded SQLite snapshot reads: no provider work,
    # no worker action, no model spend. The refresh POST is deliberately absent.
    "/api/macro/conditions",
    "/api/macro/conditions/history",
    "/api/signals/market",
    "/api/catalysts/status",
    "/api/catalysts/feed",
    "/api/catalysts/calendar",
    "/api/catalysts/hotspots/status",
    "/api/catalysts/hotspots",
    "/api/catalysts/market-focus-cycles/latest",
    "/api/strength/scan",
    "/api/strength/sectors",
    "/api/strength/market",
    "/api/strength/profiles",
    "/api/breakouts/current",
    "/api/breakouts/events",
    "/api/breakouts/status",
}
_PUBLIC_READ_API_PATTERNS = tuple(
    _re.compile(pattern, _re.IGNORECASE)
    for pattern in (
        r"^/api/stocks/[^/]+$",
        r"^/api/stocks/[^/]+/(?:signals|logo|chart|technical)$",
        r"^/api/options/[^/]+/(?:expirations|chain)$",
        r"^/api/sectors/[^/]+/(?:iv-ranking|heatmap)$",
        r"^/api/signals/stock/[^/]+$",
        r"^/api/macro/conditions/modules/[a-z_]{1,32}$",
        r"^/api/macro/conditions/factors/[a-z0-9_]{1,64}/history$",
        r"^/api/catalysts/news/[1-9][0-9]*$",
        r"^/api/catalysts/tickers/(?!batch$)[A-Z0-9][A-Z0-9.-]{0,19}$",
        r"^/api/strength/stocks/[^/]+$",
        r"^/api/breakouts/events/[^/]+$",
        r"^/api/breakouts/tickers/[^/]+$",
    )
)
_PUBLIC_READ_POST_PATHS = {
    # This endpoint is a bounded, same-origin batch query. It does not refresh
    # providers, write application state, or enqueue model work.
    "/api/catalysts/tickers/batch",
}
_PASSWORD_ENTRY_PATHS = {
    "/login",
    "/api/access/login",
    "/logo.svg",
    "/login-motif.svg",
}
# 登录页所需的构建资产(文件名带内容 hash,按前缀放行)。
_PASSWORD_ENTRY_PREFIXES = ("/assets/",)


_ACCOUNT_API_PREFIX = "/api/account"


def _is_account_api_request(path: str, method: str) -> bool:
    """Customer account surface — it authenticates itself, so the owner gate
    does not apply.

    Sign-up and sign-in have to be reachable before any session exists, and the
    watchlist routes resolve the caller's own account from its cookie and can
    only ever touch that account's rows. Same-origin checks and per-IP rate
    limits still apply on every route here; none of them read owner state or
    spend provider budget.
    """

    if path != _ACCOUNT_API_PREFIX and not path.startswith(
        _ACCOUNT_API_PREFIX + "/"
    ):
        return False
    return method.upper() in {"GET", "HEAD", "POST", "PUT", "DELETE"}


def _is_public_read_api_path(path: str) -> bool:
    return (
        path in _PUBLIC_READ_API_PATHS
        or is_public_earnings_ai_read_path(path)
        or any(
        pattern.fullmatch(path) is not None
        for pattern in _PUBLIC_READ_API_PATTERNS
        )
    )


def _is_public_read_request(
    path: str,
    method: str,
    *,
    visitor_ai_actions: bool = False,
    visitor_live_pulls: bool = False,
) -> bool:
    """Return whether password-mode visitors may use this bounded surface.

    The two keyword flags mirror ``AccessConfig``: the earnings-impact
    submission and the manual stock pull are visitor-reachable only when the
    owner opted in via config; by default visitors stay read-only.
    """

    normalized_method = method.upper()
    if _is_account_api_request(path, normalized_method):
        return True
    if normalized_method in {"GET", "HEAD"}:
        return bool(
            path in _PUBLIC_DOCUMENT_PATHS
            or _is_spa_document_path(path)
            or path.startswith("/static/")
            or path.startswith("/assets/")
            or path in _ROOT_PUBLIC_ASSETS
            or _is_public_read_api_path(path)
        )
    return normalized_method == "POST" and (
        path in _PUBLIC_READ_POST_PATHS
        or (visitor_ai_actions and is_public_earnings_impact_action_path(path))
        or (visitor_live_pulls and is_public_stock_pull_path(path))
    )


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


def _is_heavy_api_path(path: str, method: str = "GET") -> bool:
    normalized_method = method.upper()
    if _is_cached_market_read_path(path, normalized_method):
        return False
    if normalized_method == "GET" and (
        path.startswith("/api/ai/jobs/")
        or path == "/api/catalysts/status"
        or path == "/api/catalysts/hotspots/status"
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


def _is_cached_market_read_path(path: str, method: str = "GET") -> bool:
    """Identify bounded UI reads already protected by server-side snapshots.

    A stock drawer fans out across quote, chart, signal, option and event
    snapshots, while every public research page loads several persisted read
    endpoints together. Keeping those reads in their own finite bucket prevents
    normal navigation from consuming the stricter mutation/provider-work
    budget. State changes and private provider-work endpoints remain in their
    original buckets.
    """

    if method.upper() not in {"GET", "HEAD"}:
        return False
    if any(
        pattern.fullmatch(path) is not None
        for pattern in _PROVIDER_WORK_READ_PATTERNS
    ):
        return False
    return (
        _is_public_read_api_path(path)
        or path in _CACHED_MARKET_READ_PATHS
        or any(
            pattern.fullmatch(path) is not None
            for pattern in _CACHED_MARKET_READ_PATTERNS
        )
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
    - /assets/*: immutable one-year cache — Vite emits content-hashed
      filenames there, so a new deploy always changes the URL.
    - other static (unhashed logos/illustrations): five-minute browser cache
      plus normal ETag revalidation.
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
        is_html = path == "/" or path.endswith(".html") or _is_spa_document_path(path)
        is_static = (
            path.startswith("/static/")
            or path.startswith("/assets/")
            or path in _ROOT_PUBLIC_ASSETS
        )

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
                    if path.startswith("/assets/"):
                        # Content-hashed build artifacts never change in place.
                        headers["Cache-Control"] = (
                            "public, max-age=31536000, immutable"
                        )
                    else:
                        headers["Cache-Control"] = (
                            "public, max-age=300, stale-while-revalidate=60"
                        )
                elif path.startswith("/api/") or path in {"/health", "/ready"}:
                    # Default-deny caching, but let snapshot endpoints opt into
                    # conditional caching (ETag + private max-age) explicitly.
                    # Anything that never sets its own header stays no-store.
                    if "cache-control" not in headers:
                        headers["Cache-Control"] = "private, no-store"
            await send(message)

        publicly_available = bool(
            path in _PUBLIC_ACCESS_PATHS
            or (
                self.access_runtime.mode == "password"
                and (
                    path in _PASSWORD_ENTRY_PATHS
                    or path.startswith(_PASSWORD_ENTRY_PREFIXES)
                    or _is_public_read_request(
                        path,
                        method,
                        visitor_ai_actions=self.access_runtime.visitor_ai_actions,
                        visitor_live_pulls=self.access_runtime.visitor_live_pulls,
                    )
                )
            )
        )
        owner_request = StarletteRequest(scope, receive=receive)
        owner_access = self.access_runtime.request_is_owner(owner_request)
        scope.setdefault("state", {})["owner_access"] = owner_access

        is_stock_pull_post = (
            self.access_runtime.mode == "password"
            and method == "POST"
            and is_public_stock_pull_path(path)
        )
        if (
            not publicly_available
            and not owner_access
            and is_stock_pull_post
            and request_has_account_session(owner_request)
        ):
            # 手动拉取对登录客户开放（仅匿名保持只读）。账号 cookie 只在
            # 「即将被拒的拉取 POST」上才解析，不给全站请求加数据库查找。
            publicly_available = True

        if (
            method != "OPTIONS"
            and not publicly_available
            and not owner_access
        ):
            if self.access_runtime.mode == "password" and is_html:
                response = RedirectResponse("/login", status_code=303)
                return await response(scope, receive, send_with_response_headers)
            if is_stock_pull_post:
                # 匿名点拉取：如实要求登录，而不是抛 owner 门。
                return await _send_json(
                    send_with_response_headers,
                    401,
                    {"error": "account_login_required", "message": "请先登录"},
                )
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
            is_market_read = _is_cached_market_read_path(path, method)
            is_heavy = not is_market_read and _is_heavy_api_path(path, method)
            if is_market_read:
                limit = _RL_MARKET_READ_LIMIT
                bucket_kind = "m"
            elif is_heavy:
                limit = _RL_HEAVY_LIMIT
                bucket_kind = "h"
            else:
                limit = _RL_LIGHT_LIMIT
                bucket_kind = "l"
            key = f"{_scope_client_ip(scope)}:{bucket_kind}"
            now = _time.time()
            _prune_rl_buckets(now)
            bucket = _rl_buckets.get(key)
            if bucket is None:
                bucket = _rl_buckets[key] = _deque()
            cutoff = now - _RL_WINDOW
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= limit:
                # Retry-After 报真实等待而不是整窗常量：滚动窗口里最早的那批
                # 条目老化出窗就有名额，通常只差几秒。恒发 60 会让守规矩的
                # 客户端（绘图 outbox 按 Retry-After 排自动重放）把任务多押
                # 五十几秒，也让「{n} 秒后重试」的文案对用户撒谎。
                oldest_blocking = bucket[len(bucket) - limit]
                retry_after = max(1, int(oldest_blocking + _RL_WINDOW - now) + 1)
                return await _send_json(
                    send_with_response_headers, 429,
                    {"error": "rate_limited", "message": f"Too many requests; try again in {retry_after}s"},
                    extra_headers=[(b"retry-after", str(retry_after).encode())],
                )
            bucket.append(now)

        with request_owner_access_context(owner_access):
            return await self.app(scope, receive, send_with_response_headers)


app.add_middleware(_GatewayMiddleware, access_runtime=_ACCESS_RUNTIME)
app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=5)
app.add_middleware(_ExactTrustedHostMiddleware, allowed_hosts=_ALLOWED_HOSTS)

# The gateway gives protected HTML requests a friendly redirect. Public-data
# routers keep their GET endpoints anonymous in password mode and put owner
# guards directly on every state-changing route. Operational and AI routers
# retain a router-wide owner boundary.
_OWNER_DEPENDENCIES = [
    Depends(require_same_origin_action),
]
_PUBLIC_READ_DEPENDENCIES = [
    Depends(require_public_read_or_owner_access),
]
app.include_router(stocks.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(options.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(earnings.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(sectors.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(market.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
# Macro reads are public research content; the refresh route carries its own
# owner and same-origin dependencies on top of this router-wide gate.
app.include_router(macro_conditions.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(signals.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(ai.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(catalysts.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(strength.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(breakouts.router, dependencies=_PUBLIC_READ_DEPENDENCIES)
app.include_router(worker_actions.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(diagnostics.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(runtime_settings.router, dependencies=_OWNER_DEPENDENCIES)
app.include_router(access.router)
# No owner dependency: every account route authenticates the caller from its
# own cookie and can only reach that caller's rows.
app.include_router(accounts.router)
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
    # Vite 构建产物:JS/CSS 文件名带内容 hash,由构建期 manifest 全量覆盖;
    # 这里只钉稳定名文件作为无 manifest 环境的兜底。
    "index.html",
    "logo.svg",
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


def _frontend_manifest_signature() -> tuple:
    """Cheap stamp that changes whenever the built frontend is replaced.

    Two stat() calls, versus reading and hashing every file in the manifest.
    A deploy rewrites both the manifest and index.html, so a changed stamp is
    the signal to recompute; an unchanged stamp means the bytes on disk are the
    ones we already hashed.
    """

    manifest_path = (
        Path(_FRONTEND_MANIFEST_PATH)
        if _FRONTEND_MANIFEST_PATH
        else FRONTEND_DIR / _FRONTEND_MANIFEST_NAME
    )
    stamps: list[tuple] = []
    for path in (manifest_path, FRONTEND_DIR / "index.html"):
        # The full path, not just the name: FRONTEND_DIR is monkeypatched in
        # tests, and two different directories that are both empty would
        # otherwise produce the same stamp and serve each other's result.
        key = str(path)
        try:
            stat = path.stat()
        except OSError:
            stamps.append((key, None))
        else:
            stamps.append((key, stat.st_mtime_ns, stat.st_size))
    return tuple(stamps)


_frontend_integrity_lock = threading.Lock()
_frontend_integrity_cache: tuple[tuple, dict] | None = None


def _compute_frontend_integrity() -> dict:
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


def _frontend_integrity() -> dict:
    """Integrity of the built frontend, recomputed only when it changes.

    ``/health`` and ``/ready`` are public and unauthenticated, and the API rate
    limit only covers ``/api``. Hashing every built file per request turned a
    probe that should be nearly free into a disk and CPU amplifier that any
    anonymous caller could drive, and that gets slower exactly as the frontend
    grows. The result is now keyed on the manifest stamp.
    """

    global _frontend_integrity_cache

    signature = _frontend_manifest_signature()
    cached = _frontend_integrity_cache
    if cached is not None and cached[0] == signature:
        return dict(cached[1])
    with _frontend_integrity_lock:
        cached = _frontend_integrity_cache
        if cached is not None and cached[0] == signature:
            return dict(cached[1])
        payload = _compute_frontend_integrity()
        _frontend_integrity_cache = (signature, payload)
    return dict(payload)


def reset_frontend_integrity_cache() -> None:
    """Test hook: force the next probe to re-read the built files."""

    global _frontend_integrity_cache
    with _frontend_integrity_lock:
        _frontend_integrity_cache = None


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


class _SPAStaticFiles(StaticFiles):
    """Serve the built SPA: unknown extension-less paths fall back to index.html.

    BrowserRouter 路由(/watchlist、/market、/stock/NVDA…)在磁盘上没有对应
    文件;带扩展名的缺失路径(资产)仍如实返回 404。

    index.html 在发出时注入 <meta name="x-app-commit">:前端注册表在
    发出任何网络请求之前就知道自己属于哪个部署,冷启动恢复 IndexedDB
    记录时的版本检查才真正生效(审计 P2-02)。注入发生在响应期而不是
    构建期——构建先于提交,把 git sha 编进产物会打破提交产物的 CI
    字节闸门。文档路径本就 no-store,不需要文件级校验器。
    """

    _index_cache: tuple[tuple[int, int, str], bytes] | None = None

    def _index_response(self) -> Response:
        index_path = Path(self.directory) / "index.html"
        try:
            stat = index_path.stat()
        except OSError:
            raise StarletteHTTPException(status_code=404)
        identity = (stat.st_mtime_ns, stat.st_size, _APP_COMMIT)
        cached = self._index_cache
        if cached is None or cached[0] != identity:
            raw = index_path.read_bytes()
            if _APP_COMMIT != "unknown":
                raw = raw.replace(
                    b"<head>",
                    b'<head><meta name="x-app-commit" content="'
                    + _APP_COMMIT.encode("ascii", "ignore")
                    + b'">',
                    1,
                )
            cached = (identity, raw)
            self._index_cache = cached
        return Response(content=cached[1], media_type="text/html")

    async def get_response(self, path: str, scope):
        if path in ("", ".", "index.html"):
            return self._index_response()
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and _is_spa_document_path("/" + path):
                return self._index_response()
            raise
        if response.status_code == 404 and _is_spa_document_path("/" + path):
            return self._index_response()
        return response


if FRONTEND_DIR.exists():
    app.mount("/", _SPAStaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    import warnings
    warnings.warn(f"FRONTEND_DIR not found at {FRONTEND_DIR}; static serving disabled")
