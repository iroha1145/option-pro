"""Per-key thread lock bookkeeping for the independent synchronous caches."""
from __future__ import annotations

import threading
from collections.abc import Mapping
from typing import Any


def acquire_key_lock(
    key: str,
    cache_lock: threading.RLock,
    locks: dict[str, threading.Lock],
    users: dict[str, int],
) -> threading.Lock:
    with cache_lock:
        key_lock = locks.get(key)
        if key_lock is None:
            key_lock = threading.Lock()
            locks[key] = key_lock
        users[key] = users.get(key, 0) + 1
    key_lock.acquire()
    return key_lock


def release_key_lock(
    key: str,
    key_lock: threading.Lock,
    cache_lock: threading.RLock,
    locks: dict[str, threading.Lock],
    users: dict[str, int],
    cache: Mapping[str, Any],
) -> None:
    key_lock.release()
    with cache_lock:
        remaining = users.get(key, 1) - 1
        if remaining > 0:
            users[key] = remaining
            return
        users.pop(key, None)
        if key not in cache and locks.get(key) is key_lock:
            locks.pop(key, None)
