"""One bounded, same-origin quote stream per page; provider keys stay private."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections import Counter
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.access import (
    _canonical_origin,
    _canonical_request_origin,
    current_request_is_owner,
    get_access_runtime,
    request_is_owner_session,
    request_uses_https,
)
from app.config import get_settings
from app.services.request_security import (
    TRUSTED_PROXY_NETWORKS,
    TRUST_PROXY_HEADERS,
    client_ip_from_scope,
)


router = APIRouter(prefix="/api/quotes", tags=["quotes"])
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:[.-][A-Z0-9]{1,4})?$")
_MAX_REQUEST_SYMBOLS = 200
_MAX_CONNECTIONS_PER_IP = 8
_HEARTBEAT_SECONDS = 15.0


def _settings(request: Request | None = None):
    if request is not None:
        configured = getattr(request.app.state, "quote_settings", None)
        if configured is not None:
            return configured
    return get_settings()


def realtime_visible(request: Request | None = None, *, signals: bool = False) -> bool:
    """Gate both live prices and their derived signals, including direct reads."""

    settings = _settings(request)
    flag = "quotes_signals_enabled" if signals else "quotes_enabled"
    if not getattr(settings, flag, False) or not str(settings.finnhub_api_key or "").strip():
        return False
    if getattr(settings, "quotes_public_enabled", False):
        return True
    return (
        request_is_owner_session(request)
        if request is not None
        else current_request_is_owner()
    )


def _parse_symbols(value: str) -> list[str]:
    if not value.strip():
        return []
    parts = value.split(",")
    if len(parts) > _MAX_REQUEST_SYMBOLS:
        raise HTTPException(422, detail={"code": "quote_symbol_limit", "message": "Too many symbols"})
    symbols: list[str] = []
    for raw in parts:
        symbol = raw.strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise HTTPException(422, detail={"code": "invalid_quote_symbol", "message": "Invalid stock symbol"})
        if symbol not in symbols:
            symbols.append(symbol)
    return symbols


def _same_origin_stream(request: Request) -> None:
    """EventSource cannot send the mutation header; check browser origin here."""

    fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    origin = request.headers.get("origin")
    expected = _canonical_request_origin(
        "https" if request_uses_https(request) else "http",
        request.headers.get("host", ""),
    )
    if (
        fetch_site not in {"", "same-origin", "none"}
        or (origin is not None and (_canonical_origin(origin) is None or _canonical_origin(origin) != expected))
    ):
        raise HTTPException(403, detail={"code": "same_origin_required", "message": "Same-origin request required"})


def _status(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    enabled = bool(getattr(settings, "quotes_enabled", False))
    configured = bool(str(settings.finnhub_api_key or "").strip())
    return {
        "enabled": enabled,
        "configured": configured,
        "public_enabled": bool(getattr(settings, "quotes_public_enabled", False)),
        "signals_enabled": bool(getattr(settings, "quotes_signals_enabled", False)),
        "allowed": realtime_visible(request),
        "connected": False,
        "connection_status": "disabled" if not enabled else "unconfigured" if not configured else "stopped",
        "max_symbols": int(getattr(settings, "quotes_max_symbols", 50)),
    }


def _public_status(value: dict[str, Any], request: Request) -> dict[str, Any]:
    # A shared pool may include other visitors' research interests. Publish
    # capacity counts only, never the global symbol list or client identities.
    result = {
        key: item for key, item in value.items()
        if key in {
            "enabled", "configured", "public_enabled", "signals_enabled", "connected",
            "connection_status", "max_symbols", "allocated_symbols", "subscribed_count",
            "last_message_at", "last_error", "session", "market_session", "reconnect_count", "resync_required", "signals_resync_required",
        }
    }
    allocated = result.get("allocated_symbols")
    if isinstance(allocated, (list, tuple, set, dict)):
        result["allocated_symbols"] = len(allocated)
    result.update({
        "allowed": realtime_visible(request),
        "signals_enabled": bool(getattr(_settings(request), "quotes_signals_enabled", False)),
    })
    return result


@router.get("")
async def quotes_snapshot(
    request: Request,
    symbols: str = Query(default="", max_length=3200),
    focus: str = Query(default="", max_length=3200),
):
    requested = _parse_symbols(symbols)
    _parse_symbols(focus)
    status = _status(request)
    hub = getattr(request.app.state, "quote_hub", None)
    if not status["allowed"] or hub is None:
        return JSONResponse({"quotes": [], "status": status}, headers={"Cache-Control": "no-store"})
    # This endpoint is a cache read, not a hidden temporary subscription. The
    # stream owns its lifetime; snapshot refreshes cannot accumulate interests.
    payload = dict(await hub.snapshot(requested))
    payload["status"] = _public_status(dict(payload.get("status") or status), request)
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def _stream_authorized(request: Request) -> bool:
    settings = _settings(request)
    if not settings.quotes_enabled:
        return False
    if settings.quotes_public_enabled:
        return True
    # Do not reuse request.state.owner_access after session expiration.
    runtime = getattr(request.app.state, "access_runtime", None) or get_access_runtime()
    return runtime.request_is_owner(request)


def _encode_event(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':'))}\n\n"


class _QuoteStreamingResponse(StreamingResponse):
    def __init__(self, *args, cleanup: Callable[[], None], **kwargs):
        super().__init__(*args, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            # A disconnect can interrupt response headers before body() has
            # ever started, in which case its generator finally cannot run.
            self._cleanup()


@router.get("/stream")
async def quotes_stream(
    request: Request,
    symbols: str = Query(default="", max_length=3200),
    focus: str = Query(default="", max_length=3200),
):
    _same_origin_stream(request)
    requested = _parse_symbols(symbols)
    focused = _parse_symbols(focus)
    if any(symbol not in requested for symbol in focused):
        raise HTTPException(422, detail={"code": "invalid_quote_focus", "message": "Focus must be in requested symbols"})
    if not realtime_visible(request):
        raise HTTPException(403, detail={"code": "quotes_not_available", "message": "Real-time quotes are not available for this session"})
    hub = getattr(request.app.state, "quote_hub", None)
    if hub is None:
        raise HTTPException(503, detail={"code": "quotes_starting", "message": "Real-time quotes are starting"}, headers={"Retry-After": "5"})

    active = getattr(request.app.state, "quote_stream_clients", None)
    if active is None:
        active = Counter()
        request.app.state.quote_stream_clients = active
    peer = client_ip_from_scope(request.scope, enabled=TRUST_PROXY_HEADERS, networks=TRUSTED_PROXY_NETWORKS)
    if active[peer] >= _MAX_CONNECTIONS_PER_IP:
        raise HTTPException(429, detail={"code": "quote_connection_limit", "message": "Too many quote connections"}, headers={"Retry-After": "30"})
    active[peer] += 1
    try:
        client_id = await hub.subscribe(requested, focus=focused)
    except BaseException as error:
        active[peer] -= 1
        if active[peer] <= 0:
            active.pop(peer, None)
        if isinstance(error, ValueError):
            raise HTTPException(429, detail={"code": "quote_capacity", "message": "Quote capacity is temporarily full"}, headers={"Retry-After": "30"}) from None
        raise

    released = False

    def release():
        nonlocal released
        if released:
            return
        released = True
        try:
            hub.unsubscribe(client_id)
        finally:
            active[peer] -= 1
            if active[peer] <= 0:
                active.pop(peer, None)

    async def body():
        iterator = hub.events(client_id)
        pending = None
        try:
            yield "retry: 5000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                if not _stream_authorized(request):
                    yield _encode_event("status", {**_status(request), "allowed": False})
                    break
                if pending is None:
                    pending = asyncio.create_task(anext(iterator))
                ready, _ = await asyncio.wait({pending}, timeout=_HEARTBEAT_SECONDS)
                if not ready:
                    yield ": heartbeat\n\n"
                    continue
                try:
                    item = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                if not _stream_authorized(request):
                    yield _encode_event("status", {**_status(request), "allowed": False})
                    break
                event = item.get("event")
                data = dict(item.get("data") or {})
                if event == "status":
                    data = _public_status(data, request)
                elif event == "quotes" and isinstance(data.get("status"), dict):
                    data["status"] = _public_status(data["status"], request)
                if event in {"quotes", "status", "radar"}:
                    yield _encode_event(event, data)
        finally:
            try:
                if pending is not None:
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
                        await pending
                with contextlib.suppress(RuntimeError):
                    await iterator.aclose()
            finally:
                release()

    return _QuoteStreamingResponse(body(), cleanup=release, media_type="text/event-stream", headers={
        "Cache-Control": "no-store, no-transform",
        "X-Accel-Buffering": "no",
    })
