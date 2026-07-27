"""Conditional GET (ETag/304) plus serialized-bytes reuse for snapshot reads.

Endpoints that serve a byte-stable payload (worker snapshots and their fixed
stale decorations) pay three per-request costs today: FastAPI's
``jsonable_encoder`` tree walk, ``json.dumps``, and gzip. For the earnings
snapshot that is ~700ms of pure re-encoding of bytes that have not changed
since the worker published them.

``respond_with_snapshot`` collapses all three:

- The serialized JSON (and its gzip form, and the strong ETag over the exact
  bytes) is cached under a caller-supplied version key that uniquely
  determines the payload bytes — typically ``(resource, scope,
  snapshot saved_at, schema, params)``. The cache has a byte budget and a
  per-item cap, not just an entry count.
- ``If-None-Match`` gets a 304 with no body.
- Responses are returned as plain ``Response`` objects so FastAPI never runs
  ``jsonable_encoder`` on the payload.

Identity scoping: the *caller* must fold the principal scope into the version
key (and the payloads themselves already differ per scope). Bodies are served
with ``Cache-Control: private`` + ``Vary: Cookie`` so a browser never replays
one principal's body to another after login/logout.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Mapping

from fastapi import Request, Response

from app.services import cache_metrics

_GZIP_MIN_BYTES = 1024
_GZIP_LEVEL = 5

# Small responses are cheap to re-encode; only bodies at least this large are
# worth holding in the bytes cache. Everything still gets an ETag.
_STORE_MIN_BYTES = 8 * 1024
_MAX_ITEM_BYTES = 12 * 1024 * 1024
_MAX_TOTAL_BYTES = 48 * 1024 * 1024
_MAX_ENTRIES = 32


class _SerializedStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[str, bytes, bytes | None]] = (
            OrderedDict()
        )

    def _total_bytes_locked(self) -> int:
        total = 0
        for _, body, packed in self._entries.values():
            total += len(body) + (len(packed) if packed else 0)
        return total

    def get(self, key: str) -> tuple[str, bytes, bytes | None] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                self._entries.move_to_end(key)
            return entry

    def put(self, key: str, etag: str, body: bytes, packed: bytes | None) -> None:
        size = len(body) + (len(packed) if packed else 0)
        if size > _MAX_ITEM_BYTES:
            cache_metrics.incr("response_bytes.rejected_oversize")
            return
        with self._lock:
            self._entries[key] = (etag, body, packed)
            self._entries.move_to_end(key)
            while len(self._entries) > _MAX_ENTRIES or (
                len(self._entries) > 1
                and self._total_bytes_locked() > _MAX_TOTAL_BYTES
            ):
                self._entries.popitem(last=False)
                cache_metrics.incr("response_bytes.evicted")
            cache_metrics.set_gauge(
                "response_bytes.bytes", float(self._total_bytes_locked())
            )
            cache_metrics.set_gauge(
                "response_bytes.entries", float(len(self._entries))
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


_store = _SerializedStore()


def reset_serialized_response_cache() -> None:
    """Test hook."""

    _store.clear()


def _encode_payload(payload: Any) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _etag_for(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


def _if_none_match_hits(header_value: str | None, etag: str) -> bool:
    if not header_value:
        return False
    if header_value.strip() == "*":
        return True
    bare = etag.strip('"')
    for candidate in header_value.split(","):
        token = candidate.strip()
        if token.startswith("W/"):
            token = token[2:].strip()
        if token.strip('"') == bare:
            return True
    return False


def _accepts_gzip(request: Request) -> bool:
    return "gzip" in request.headers.get("accept-encoding", "")


def snapshot_version_key(*parts: Any) -> str:
    """Stable string key from version parts (no payload contents)."""

    return "|".join(repr(part) for part in parts)


def respond_with_snapshot(
    request: Request,
    payload: Any,
    *,
    version_key: str | None,
    cache_control: str,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Serve a byte-stable JSON payload with ETag/304 support.

    ``version_key`` must uniquely determine the payload bytes; pass None for
    dynamic payloads (they still get an ETag for 304s, just no bytes reuse).
    """

    started = time.perf_counter()
    cached = _store.get(version_key) if version_key is not None else None
    if cached is not None:
        etag, body, packed = cached
        cache_state = "bytes-hit"
        cache_metrics.incr("response_bytes.hit")
    else:
        body = _encode_payload(payload)
        etag = _etag_for(body)
        packed = None
        if len(body) >= _GZIP_MIN_BYTES:
            packed = gzip.compress(body, compresslevel=_GZIP_LEVEL)
            if len(packed) >= len(body):
                packed = None
        cache_state = "bytes-miss"
        cache_metrics.incr("response_bytes.miss")
        if version_key is not None and len(body) >= _STORE_MIN_BYTES:
            _store.put(version_key, etag, body, packed)
    encode_ms = (time.perf_counter() - started) * 1000
    cache_metrics.observe_ms("response_bytes.encode_ms", encode_ms)

    headers: dict[str, str] = {
        "ETag": etag,
        "Cache-Control": cache_control,
        "Vary": "Cookie, Accept-Encoding",
        # Never let a shared cache hold these bodies, even if a proxy is
        # configured loosely: identity-scoped research data stays private.
        "CDN-Cache-Control": "no-store",
        "Server-Timing": f"enc;dur={encode_ms:.1f}",
    }
    if extra_headers:
        headers.update(extra_headers)

    if _if_none_match_hits(request.headers.get("if-none-match"), etag):
        cache_metrics.incr("response_bytes.not_modified")
        headers["X-Optix-Cache"] = "304"
        return Response(status_code=304, headers=headers)

    headers["X-Optix-Cache"] = cache_state
    if packed is not None and _accepts_gzip(request):
        headers["Content-Encoding"] = "gzip"
        return Response(
            content=packed,
            media_type="application/json",
            headers=headers,
        )
    return Response(
        content=body,
        media_type="application/json",
        headers=headers,
    )
