"""Bound expensive catalyst reads and their waiting queue on the shared host."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from weakref import WeakKeyDictionary

import anyio
from starlette.responses import JSONResponse

from app.services.catalysts.hotspot_reads import (
    HOTSPOT_READ_WORKERS,
    hotspot_read_pool_context,
)

_CATALYST_READ_PATHS = frozenset({
    "/api/catalysts/feed",
    "/api/catalysts/hotspots",
})
_CATALYST_READ_CONCURRENCY = 1
_CATALYST_READ_WAITING = 96
_HOTSPOT_READ_WAITING = 64


@dataclass
class _ReadGate:
    limiter: anyio.CapacityLimiter
    capacity: int
    entered: int = 0


class CatalystReadAdmission:
    """Share one execution slot and 96 waiters for feed and pool fallback.

    An available hotspot pool has its own eight slots and 64 waiters. Gateway
    checks run before admission, and each event loop owns its own gates.
    """

    def __init__(self, app):
        self.app = app
        self._limiters = WeakKeyDictionary()
        self._hotspot_limiters = WeakKeyDictionary()

    async def __call__(self, scope, receive, send):
        if (
            scope["type"] != "http"
            or scope.get("method") != "GET"
            or scope.get("path") not in _CATALYST_READ_PATHS
        ):
            return await self.app(scope, receive, send)

        loop = asyncio.get_running_loop()
        application = scope.get("app")
        pool = getattr(getattr(application, "state", None), "hotspot_read_pool", None)
        if pool is not None and not pool.available:
            pool = None
        process_read = pool is not None and scope.get("path") == "/api/catalysts/hotspots"
        limiters = self._hotspot_limiters if process_read else self._limiters
        gate = limiters.get(loop)
        if gate is None:
            slots = HOTSPOT_READ_WORKERS if process_read else _CATALYST_READ_CONCURRENCY
            waiting = _HOTSPOT_READ_WAITING if process_read else _CATALYST_READ_WAITING
            gate = _ReadGate(anyio.CapacityLimiter(slots), slots + waiting)
            limiters[loop] = gate
        # Check and increment before the first await: all requests on this
        # event loop observe one bound, including tasks waiting for a slot.
        if gate.entered >= gate.capacity:
            response = JSONResponse(
                {"error": "rate_limited", "message": "Too many requests; try again in 1s"},
                status_code=429, headers={"Retry-After": "1"},
            )
            return await response(scope, receive, send)
        gate.entered += 1
        try:
            async with gate.limiter:
                # A running synchronous worker cannot be cancelled. Keep its
                # slot until the inner request finishes, then propagate caller
                # cancellation. The child inherits gateway ownership context.
                with hotspot_read_pool_context(pool if process_read else None):
                    task = asyncio.create_task(self.app(scope, receive, send))
                try:
                    return await asyncio.shield(task)
                except asyncio.CancelledError:
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            continue
                        except BaseException:
                            break
                    if not task.cancelled():
                        task.exception()
                    raise
        finally:
            gate.entered -= 1
