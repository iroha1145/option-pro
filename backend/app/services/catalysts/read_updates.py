"""Bounded, public, content-free invalidations for the local Catalyst read cache.

A notification says only that the SQLite cache may have changed. It contains no
news, job IDs, settings, tokens, filesystem paths or account data. Clients still
fetch through the existing permission-checked endpoints. No SQL connection and
no external request is kept alive by a subscriber.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import AsyncIterator

from fastapi import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.types import Receive, Scope, Send

_CHECK_SECONDS = 15.0
_MAX_SECONDS = 180.0
_MAX_STREAMS = 32
_active_streams = 0


def cache_revision(path: Path) -> str:
    """Include WAL commits and inode swaps; never create an absent database.

    Checkpointing may cause a harmless extra invalidation. File metadata is a
    notification hint, not a replacement for low-frequency content revalidation.
    SHM is intentionally excluded: readers can modify SHM without new content.
    """
    parts: list[tuple[int, int, int] | None] = []
    for file in (path, Path(str(path) + "-wal")):
        try:
            stat = file.stat()
            parts.append((stat.st_ino, stat.st_size, stat.st_mtime_ns))
        except OSError:
            parts.append(None)
    payload = json.dumps(parts, separators=(",", ":")).encode("ascii")
    return hashlib.sha256(payload).hexdigest()[:24]


def update_frame(revision: str) -> str:
    return "event: catalyst-update\ndata: " + json.dumps({"revision": revision}) + "\n\n"


async def event_frames(
    request: Request, path: Path, *, interval: float = _CHECK_SECONDS,
    lifetime: float = _MAX_SECONDS,
) -> AsyncIterator[str]:
    last_revision = None
    started = time.monotonic()
    yield "retry: 10000\n\n"
    while time.monotonic() - started < lifetime:
        if await request.is_disconnected():
            return
        revision = cache_revision(path)
        if revision != last_revision:
            yield update_frame(revision)
            last_revision = revision
        else:
            yield ": keep-alive\n\n"
        await asyncio.sleep(interval)


class _LimitedUpdates(StreamingResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        global _active_streams
        # Reserve only when ASGI starts the response; abandoned response objects
        # cannot leak slots. Also releases on cancellation, send failure, shutdown.
        if _active_streams >= _MAX_STREAMS:
            await JSONResponse({"detail": "Too many Catalyst update streams"},
                status_code=429, headers={"Retry-After": "60", "Cache-Control": "no-store"})(scope, receive, send)
            return
        _active_streams += 1
        try:
            await super().__call__(scope, receive, send)
        finally:
            _active_streams -= 1


def catalyst_update_response(request: Request, path: Path) -> StreamingResponse:
    return _LimitedUpdates(
        event_frames(request, path), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Vary": "Cookie"},
    )
