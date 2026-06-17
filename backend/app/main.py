from __future__ import annotations

import hmac
import json as _json_mod
import os as _os
import time as _time
from collections import deque as _deque
from pathlib import Path

# Import yahoo.py first — it monkey-patches yf.Ticker to use curl_cffi session
# so all downstream yfinance usage dodges Yahoo's rate limiter.
from app.services import yahoo  # noqa: F401

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders

from app.api import ai, earnings, market, options, sectors, signals, stocks, strength

app = FastAPI(
    title="Optix Pro Options Visualization API",
    description="FastAPI backend wrapping Massive.com stock and options market data.",
    version="0.1.0",
)

# CORS: same-origin by default. Override with ALLOWED_ORIGINS only when the
# frontend is hosted on a different trusted origin.
_allowed = _os.environ.get("ALLOWED_ORIGINS", "").strip()
_origins = [o.strip() for o in _allowed.split(",") if o.strip()] if _allowed else []
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Authorization", "X-App-Token"],
    )


_TRUST_PROXY_HEADERS = _os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}
_APP_AUTH_TOKEN = _os.environ.get("APP_AUTH_TOKEN", "").strip()

# Rate limiter state. deque + per-IP buckets, pruned lazily so the dict can't
# grow without bound when many distinct IPs hit the API.
_rl_buckets: dict[str, _deque] = {}
_RL_HEAVY_LIMIT = 30    # max requests / window for heavy endpoints
_RL_LIGHT_LIMIT = 200   # max requests / window for cheap endpoints
_RL_WINDOW = 60         # seconds
_RL_MAX_KEYS = 10_000   # safety valve against IP-churn memory growth
_rl_last_prune = 0.0


def _scope_header(scope, name: bytes) -> str:
    for key, value in scope.get("headers") or []:
        if key == name:
            try:
                return value.decode("latin-1")
            except Exception:
                return ""
    return ""


def _scope_client_ip(scope) -> str:
    if _TRUST_PROXY_HEADERS:
        ip = (
            _scope_header(scope, b"cf-connecting-ip")
            or _scope_header(scope, b"x-forwarded-for").split(",")[0].strip()
        )
        if ip:
            return ip
    client = scope.get("client")
    return client[0] if client else "unknown"


def _scope_token(scope) -> str:
    auth = _scope_header(scope, b"authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return _scope_header(scope, b"x-app-token").strip()


def _cors_headers(scope) -> list[tuple[bytes, bytes]]:
    if not _origins:
        return []
    origin = _scope_header(scope, b"origin")
    if not origin:
        return []
    allow_origin = origin if "*" in _origins or origin in _origins else ""
    if not allow_origin:
        return []
    return [
        (b"access-control-allow-origin", allow_origin.encode("latin-1")),
        (b"access-control-allow-credentials", b"true"),
        (b"vary", b"Origin"),
    ]


def _prune_rl_buckets(now: float) -> None:
    """Drop stale/empty buckets. Called at most once per window."""
    global _rl_last_prune
    if now - _rl_last_prune < _RL_WINDOW and len(_rl_buckets) < _RL_MAX_KEYS:
        return
    _rl_last_prune = now
    cutoff = now - _RL_WINDOW
    for key in [k for k, dq in _rl_buckets.items() if not dq or dq[-1] < cutoff]:
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
    """Single pure-ASGI middleware combining auth, rate limiting and cache headers.

    Replaces three stacked BaseHTTPMiddleware layers (each of which spins up an
    anyio task group per request) with one cheap pass.

    Cache policy:
    - HTML ("/" and *.html): no-store — deploys must show up immediately.
    - /static/*: no-cache (revalidate) — StaticFiles serves ETag/Last-Modified,
      so unchanged JS/CSS answer with 304 instead of a full re-download.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")
        method = scope.get("method", "GET")

        if method != "OPTIONS" and path.startswith("/api/"):
            # ── Optional bearer-token auth ──
            if _APP_AUTH_TOKEN:
                token = _scope_token(scope)
                try:
                    valid = bool(token) and hmac.compare_digest(token, _APP_AUTH_TOKEN)
                except Exception:
                    valid = False
                if not valid:
                    return await _send_json(
                        send, 401,
                        {"error": "unauthorized", "message": "Missing or invalid API token"},
                        extra_headers=_cors_headers(scope),
                    )

            # ── Per-IP rate limit ──
            is_heavy = (
                path.startswith("/api/ai/") or
                path.startswith("/api/strength/scan") or
                "/ai-analysis" in path or
                "/analyze-" in path or
                path.endswith("/unusual") or
                path.endswith("/watchlist")
            )
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
                    send, 429,
                    {"error": "rate_limited", "message": f"Too many requests; try again in {_RL_WINDOW}s"},
                    extra_headers=_cors_headers(scope) + [(b"retry-after", str(_RL_WINDOW).encode())],
                )
            bucket.append(now)

        is_html = path == "/" or path.endswith(".html")
        is_static = path.startswith("/static/")
        if not (is_html or is_static):
            return await self.app(scope, receive, send)

        async def send_with_cache_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.setdefault("headers", []))
                if is_html:
                    headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
                    headers["CDN-Cache-Control"] = "no-store"
                    headers["Cloudflare-CDN-Cache-Control"] = "no-store"
                else:
                    # Allow conditional revalidation (ETag → 304), never stale reuse.
                    headers["Cache-Control"] = "no-cache"
            await send(message)

        return await self.app(scope, receive, send_with_cache_headers)


app.add_middleware(_GatewayMiddleware)

app.include_router(stocks.router)
app.include_router(options.router)
app.include_router(earnings.router)
app.include_router(sectors.router)
app.include_router(market.router)
app.include_router(signals.router)
app.include_router(ai.router)
app.include_router(strength.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


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

if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
else:
    import warnings
    warnings.warn(f"FRONTEND_DIR not found at {FRONTEND_DIR}; static serving disabled")
