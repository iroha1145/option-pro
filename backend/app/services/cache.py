from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class TTLCache:
    """Small async-friendly in-memory TTL cache.

    This is intentionally process-local and dependency-free for the MVP. It is
    safe for FastAPI's single event-loop access pattern; multi-worker Docker
    deployments will each have their own cache.
    """

    # Purge expired entries (and their locks) once the store grows past this.
    _PURGE_THRESHOLD = 256
    _MAX_ENTRIES = 512

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
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

    def _maybe_purge(self) -> None:
        """Keep the store and lock map from growing without bound.

        Expired entries are otherwise only removed when their key is read
        again, and locks were never removed at all.
        """
        if len(self._store) < self._PURGE_THRESHOLD and len(self._locks) < self._PURGE_THRESHOLD:
            return
        now = time.time()
        for key in [k for k, (expires_at, _) in self._store.items() if expires_at <= now]:
            self._store.pop(key, None)
        # Expiry alone is not a bound: a stream of distinct, still-fresh query
        # keys can otherwise grow the process indefinitely. Leave room for the
        # value about to be inserted and evict the soonest-expiring entries.
        if len(self._store) >= self._MAX_ENTRIES:
            remove_count = len(self._store) - self._MAX_ENTRIES + 1
            for key, _ in sorted(
                self._store.items(),
                key=lambda item: item[1][0],
            )[:remove_count]:
                self._store.pop(key, None)
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
            return None
        expires_at, value = entry
        if expires_at <= time.time():
            self._store.pop(key, None)
            return None
        return expires_at, value

    def set(self, key: str, value: Any, ttl: int) -> Any:
        self._maybe_purge()
        self._store[key] = (time.time() + ttl, value)
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
                    return value, True, expires_at

                value = await producer()
                self._maybe_purge()
                expires_at = time.time() + ttl
                self._store[key] = (expires_at, value)
                return value, False, expires_at
        finally:
            self._release_lock(key, lock)

    def clear(self) -> None:
        self._store.clear()
        for key in [k for k in self._locks if self._lock_users.get(k, 0) == 0]:
            self._locks.pop(key, None)


cache = TTLCache()
