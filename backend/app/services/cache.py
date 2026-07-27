from __future__ import annotations

import asyncio
import json
import sys
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.services import cache_metrics


def _estimate_size(value: Any) -> int:
    """Approximate the retained bytes of a cached value.

    Entry counts alone let one 4MB earnings payload weigh the same as a tiny
    status dict; the byte budget needs a real (if approximate) size. JSON
    encoding is exact for the JSON-ish payloads this cache mostly holds and
    runs only on writes; DataFrame-like values report their buffer usage via
    duck typing; everything else falls back to a shallow floor.
    """

    if value is None:
        return 64
    if isinstance(value, (str, bytes, bytearray)):
        return len(value) + 64
    usage = getattr(value, "memory_usage", None)
    if callable(usage):
        try:
            return int(usage(deep=True).sum()) + 256
        except Exception:
            pass
    if isinstance(value, (dict, list, tuple, int, float, bool)):
        try:
            return len(json.dumps(value, default=str, ensure_ascii=False)) + 64
        except Exception:
            pass
        if isinstance(value, dict):
            return 512 + 128 * len(value)
        if isinstance(value, (list, tuple)):
            return 256 + 64 * len(value)
    try:
        return int(sys.getsizeof(value))
    except Exception:
        return 512


class TTLCache:
    """Small async-friendly in-memory TTL cache.

    This is intentionally process-local and dependency-free for the MVP. It is
    safe for FastAPI's single event-loop access pattern; multi-worker Docker
    deployments will each have their own cache.

    Capacity is bounded two ways: an entry cap and a byte budget (large
    payloads and small status objects must not weigh the same). Values above
    the per-item cap are returned to the caller but not retained.
    """

    # Purge expired entries (and their locks) once the store grows past this.
    _PURGE_THRESHOLD = 256
    _MAX_ENTRIES = 512
    _MAX_TOTAL_BYTES = 64 * 1024 * 1024
    _MAX_ITEM_BYTES = 16 * 1024 * 1024

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._sizes: dict[str, int] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_users: dict[str, int] = {}

    def _reserve_lock(self, key: str) -> asyncio.Lock:
        lock = self._locks.setdefault(key, asyncio.Lock())
        self._lock_users[key] = self._lock_users.get(key, 0) + 1
        return lock

    def _release_lock(self, key: str, lock: asyncio.Lock) -> None:
        remaining = self._lock_users.get(key, 1) - 1
        if remaining > 0:
            self._lock_users[key] = remaining
            return
        self._lock_users.pop(key, None)
        if key not in self._store and self._locks.get(key) is lock:
            self._locks.pop(key, None)

    def _drop(self, key: str) -> None:
        self._store.pop(key, None)
        self._sizes.pop(key, None)

    def _total_bytes(self) -> int:
        return sum(self._sizes.values())

    def _publish_gauges(self) -> None:
        cache_metrics.set_gauge("ttl_cache.entries", float(len(self._store)))
        cache_metrics.set_gauge("ttl_cache.bytes", float(self._total_bytes()))
        if self._sizes:
            largest_key = max(self._sizes, key=self._sizes.__getitem__)
            cache_metrics.set_gauge(
                "ttl_cache.largest_item_bytes", float(self._sizes[largest_key])
            )

    def _maybe_purge(self, incoming_bytes: int = 0) -> None:
        """Keep the store and lock map from growing without bound.

        Expired entries are otherwise only removed when their key is read
        again, and locks were never removed at all.
        """
        over_budget = self._total_bytes() + incoming_bytes > self._MAX_TOTAL_BYTES
        if (
            len(self._store) < self._PURGE_THRESHOLD
            and len(self._locks) < self._PURGE_THRESHOLD
            and not over_budget
        ):
            return
        now = time.time()
        for key in [k for k, (expires_at, _) in self._store.items() if expires_at <= now]:
            self._drop(key)
        # Expiry alone is not a bound: a stream of distinct, still-fresh query
        # keys can otherwise grow the process indefinitely. Leave room for the
        # value about to be inserted and evict the soonest-expiring entries —
        # both when the entry cap is hit and when the byte budget is.
        if (
            len(self._store) >= self._MAX_ENTRIES
            or self._total_bytes() + incoming_bytes > self._MAX_TOTAL_BYTES
        ):
            for key, _ in sorted(
                self._store.items(),
                key=lambda item: item[1][0],
            ):
                if (
                    len(self._store) < self._MAX_ENTRIES
                    and self._total_bytes() + incoming_bytes
                    <= self._MAX_TOTAL_BYTES
                ):
                    break
                self._drop(key)
                cache_metrics.incr("ttl_cache.evicted")
        # Drop locks that no longer guard a live cache entry and aren't held.
        for key in [k for k in self._locks if k not in self._store and self._lock_users.get(k, 0) == 0]:
            self._locks.pop(key, None)

    def get(self, key: str) -> Any | None:
        entry = self.get_with_expiry(key)
        if entry is None:
            return None
        return entry[1]

    def get_with_expiry(self, key: str) -> tuple[float, Any] | None:
        entry = self._store.get(key)
        if not entry:
            cache_metrics.incr("ttl_cache.miss")
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            self._drop(key)
            cache_metrics.incr("ttl_cache.expired")
            return None
        cache_metrics.incr("ttl_cache.hit")
        return expires_at, value

    def set(self, key: str, value: Any, ttl: int, *, size_hint: int | None = None) -> Any:
        size = int(size_hint) if size_hint is not None else _estimate_size(value)
        if size > self._MAX_ITEM_BYTES:
            # Serve the value but do not retain something this large.
            cache_metrics.incr("ttl_cache.rejected_oversize")
            self._drop(key)
            return value
        self._maybe_purge(size)
        self._store[key] = (time.time() + ttl, value)
        self._sizes[key] = size
        self._publish_gauges()
        return value

    async def get_or_set(self, key: str, ttl: int, producer: Callable[[], Awaitable[Any]]) -> Any:
        value, _, _ = await self.get_or_set_with_meta(key, ttl, producer)
        return value

    async def get_or_set_with_meta(
        self,
        key: str,
        ttl: int,
        producer: Callable[[], Awaitable[Any]],
    ) -> tuple[Any, bool, float]:
        cached = self.get_with_expiry(key)
        if cached is not None:
            expires_at, value = cached
            return value, True, expires_at

        lock = self._reserve_lock(key)
        try:
            async with lock:
                cached = self.get_with_expiry(key)
                if cached is not None:
                    expires_at, value = cached
                    cache_metrics.incr("ttl_cache.singleflight_wait")
                    return value, True, expires_at

                value = await producer()
                expires_at = time.time() + ttl
                self.set(key, value, ttl)
                return value, False, expires_at
        finally:
            self._release_lock(key, lock)

    def clear(self) -> None:
        self._store.clear()
        self._sizes.clear()
        for key in [k for k in self._locks if self._lock_users.get(k, 0) == 0]:
            self._locks.pop(key, None)

    def stats(self) -> dict[str, Any]:
        largest = None
        if self._sizes:
            largest_key = max(self._sizes, key=self._sizes.__getitem__)
            # Key names can embed query params; expose only a coarse prefix.
            largest = {
                "key_prefix": largest_key.split(":", 1)[0],
                "bytes": self._sizes[largest_key],
            }
        return {
            "entries": len(self._store),
            "bytes": self._total_bytes(),
            "largest_item": largest,
        }


cache = TTLCache()
