from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from app.services import signals


def _clear_signal_cache_state() -> None:
    signals._cache.clear()
    if hasattr(signals, "_key_locks"):
        signals._key_locks.clear()
    if hasattr(signals, "_key_lock_users"):
        signals._key_lock_users.clear()


def test_signal_cache_purges_expired_entries_and_stays_bounded():
    _clear_signal_cache_state()
    expired = datetime.now(timezone.utc) - timedelta(seconds=1)
    for index in range(signals._CACHE_MAX_ENTRIES + 100):
        signals._cache[f"expired:{index}"] = (expired, {"value": index})

    value = signals._cached("fresh", 60, lambda: {"value": 42})

    assert value == {"value": 42}
    assert list(signals._cache) == ["fresh"]


def test_signal_cache_evicts_oldest_live_entry_at_capacity():
    _clear_signal_cache_state()
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    for index in range(signals._CACHE_MAX_ENTRIES):
        signals._cache[f"live:{index}"] = (future, index)

    signals._cached("newest", 60, lambda: 999)

    assert len(signals._cache) == signals._CACHE_MAX_ENTRIES
    assert "live:0" not in signals._cache
    assert signals._cache["newest"][1] == 999


def test_signal_cache_coalesces_concurrent_cold_loads():
    _clear_signal_cache_state()
    calls = 0
    calls_lock = threading.Lock()
    start = threading.Barrier(8)

    def loader():
        nonlocal calls
        with calls_lock:
            calls += 1
        time.sleep(0.05)
        return {"value": 42}

    def run(_index: int):
        start.wait()
        return signals._cached("single-flight", 60, loader)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(run, range(8)))

    assert calls == 1
    assert all(result.get("value") == 42 for result in results)
    assert sum("_cached" not in result for result in results) == 1


def test_signal_cache_failed_unique_loads_do_not_retain_key_locks():
    _clear_signal_cache_state()

    def fail():
        raise RuntimeError("provider down")

    for index in range(20):
        with pytest.raises(RuntimeError, match="provider down"):
            signals._cached(f"failure:{index}", 60, fail)

    assert signals._key_locks == {}
    assert signals._key_lock_users == {}
