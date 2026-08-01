"""Fingerprint-keyed cache for parsed-and-validated snapshot files.

Worker processes publish snapshots with atomic ``os.replace``; the API process
previously re-read and re-validated the whole document on every request (the
public-home file alone cost ~440ms per read at 4MB). Publishing swaps the
inode, so ``(st_ino, st_mtime_ns, st_size)`` of the opened file is a exact
content fingerprint: cache the validated parse under that identity and every
request after the first is a stat call.

Safety properties, in the order they matter:

- The cached identity comes from ``fstat`` on the file descriptor actually
  read (borrowed from ``stock_pull_snapshot``), so a publish racing the read
  can never file the new bytes under the old identity.
- A parse/validation failure is negatively cached **per identity**: a corrupt
  file is not re-parsed on every request, but the moment the worker publishes
  a replacement (identity changes) the negative entry is void. Unavailability
  therefore never outlives the condition that caused it.
- Concurrent cold reads share one parse via a per-path lock (single flight).
- Callers must treat returned documents as immutable; entry points that need
  to decorate payloads copy before writing (they already did).
"""

from __future__ import annotations

import os
import stat as stat_module
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any

from app.services import cache_metrics

_Identity = tuple[int, int, int]


def _regular_file_identity(value: os.stat_result) -> _Identity | None:
    if not stat_module.S_ISREG(value.st_mode):
        return None
    return (int(value.st_ino), int(value.st_mtime_ns), int(value.st_size))


def _path_file_identity(path: Path) -> _Identity | None:
    try:
        return _regular_file_identity(os.stat(path, follow_symlinks=False))
    except OSError:
        return None


class FingerprintedFileCache:
    """Cache ``loader(bytes)`` results keyed by the source file's identity."""

    def __init__(
        self,
        name: str,
        *,
        max_paths: int = 8,
        max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self._name = name
        self._max_paths = max_paths
        self._max_bytes = max_bytes
        self._lock = threading.Lock()
        # path -> (identity, validated_at, raw_size, value)
        self._entries: OrderedDict[str, tuple[_Identity, float, int, Any]] = (
            OrderedDict()
        )
        # path -> identity of the last read that produced no usable document
        self._failures: OrderedDict[str, _Identity] = OrderedDict()
        self._path_locks: dict[str, threading.Lock] = {}

    def _metric(self, event: str) -> None:
        cache_metrics.incr(f"snapshot_cache.{self._name}.{event}")

    def _total_bytes_locked(self) -> int:
        return sum(entry[2] for entry in self._entries.values())

    def _store_locked(
        self, key: str, identity: _Identity, validated_at: float, raw_size: int, value: Any
    ) -> None:
        self._failures.pop(key, None)
        self._entries[key] = (identity, validated_at, raw_size, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_paths or (
            len(self._entries) > 1 and self._total_bytes_locked() > self._max_bytes
        ):
            self._entries.popitem(last=False)
            self._metric("evicted")
        self._prune_path_locks_locked()
        cache_metrics.set_gauge(
            f"snapshot_cache.{self._name}.bytes", float(self._total_bytes_locked())
        )
        cache_metrics.set_gauge(
            f"snapshot_cache.{self._name}.entries", float(len(self._entries))
        )

    # Concurrent threads observe time.time() microseconds apart, and lock
    # acquisition order is not arrival order — a strict `now >= validated_at`
    # check would make the loser of that race re-parse. Frozen-clock tests
    # rewind by minutes-to-years, so a small skew allowance separates the two.
    _CLOCK_SKEW_ALLOWANCE_SECONDS = 30.0

    def _cached_locked(
        self, key: str, identity: _Identity, now: float
    ) -> tuple[bool, Any]:
        cached = self._entries.get(key)
        if cached is not None and cached[0] == identity:
            # Validation is monotonic in `now` (checks only reject timestamps
            # from the future). A caller evaluating a *substantially earlier*
            # clock than the one the entry was validated with — frozen-clock
            # tests — must bypass the cache rather than trust a laxer
            # validation.
            if now >= cached[1] - self._CLOCK_SKEW_ALLOWANCE_SECONDS:
                self._entries.move_to_end(key)
                return True, cached[3]
            return False, None
        if cached is not None:
            self._entries.pop(key, None)
        failed = self._failures.get(key)
        if failed is not None:
            if failed == identity:
                return True, None
            self._failures.pop(key, None)
        return False, None

    def read(
        self,
        path: Path,
        loader: Callable[[bytes], Any],
        *,
        now: float | None = None,
        max_bytes: int | None = None,
    ) -> Any:
        """Return the validated document for ``path`` (None when unusable).

        ``loader`` receives the raw file bytes and returns the validated
        document, or None / raises ValueError when the content is unusable.
        """

        current = time.time() if now is None else float(now)
        key = os.path.abspath(os.fspath(path))
        identity = _path_file_identity(path)
        if identity is None:
            self._metric("miss_no_file")
            return None

        with self._lock:
            hit, value = self._cached_locked(key, identity, current)
            if hit:
                self._metric("hit" if value is not None else "negative_hit")
                return value
            path_lock = self._path_locks.setdefault(key, threading.Lock())

        with path_lock:
            identity = _path_file_identity(path)
            if identity is None:
                self._metric("miss_no_file")
                return None
            with self._lock:
                hit, value = self._cached_locked(key, identity, current)
                if hit:
                    self._metric("hit_after_wait" if value is not None else "negative_hit")
                    return value

            started = time.perf_counter()
            try:
                fd = os.open(
                    key, os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
                )
            except OSError:
                self._metric("miss_no_file")
                return None
            try:
                handle = os.fdopen(fd, "rb")
            except OSError:
                os.close(fd)
                self._metric("miss_no_file")
                return None
            with handle:
                opened = _regular_file_identity(os.fstat(handle.fileno()))
                if opened is None:
                    self._metric("miss_not_regular")
                    return None
                limit = max_bytes if max_bytes is not None else self._max_bytes
                raw = handle.read(limit + 1)

            oversized = len(raw) > limit
            value = None
            if not oversized:
                try:
                    value = loader(raw)
                except (ValueError, TypeError):
                    value = None
            elapsed_ms = (time.perf_counter() - started) * 1000
            cache_metrics.observe_ms(f"snapshot_cache.{self._name}.parse_ms", elapsed_ms)
            self._metric("parsed")

            with self._lock:
                if value is None:
                    self._failures[key] = opened
                    self._failures.move_to_end(key)
                    while len(self._failures) > self._max_paths:
                        self._failures.popitem(last=False)
                else:
                    self._store_locked(key, opened, current, len(raw), value)
            return value

    def _prune_path_locks_locked(self) -> None:
        """回收不再对应任何缓存态且未被持有的路径锁。

        参数变体（strength 快照最多 24 个路径）长期运行会让锁表只增不减
        （审计缓存卫生项）。持有中的锁跳过：极端竞态下同路径可能短暂出现
        两把锁 → 两个线程各解析一次——loader 是纯函数，重复解析良性。
        """
        for key in [
            k
            for k, lock in self._path_locks.items()
            if k not in self._entries
            and k not in self._failures
            and not lock.locked()
        ]:
            self._path_locks.pop(key, None)

    def invalidate(self, path: Path | None = None) -> None:
        with self._lock:
            if path is None:
                self._entries.clear()
                self._failures.clear()
                self._prune_path_locks_locked()
                return
            key = os.path.abspath(os.fspath(path))
            self._entries.pop(key, None)
            self._failures.pop(key, None)
            self._prune_path_locks_locked()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._total_bytes_locked(),
                "failures": len(self._failures),
            }
