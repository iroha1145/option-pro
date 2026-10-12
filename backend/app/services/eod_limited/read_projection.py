"""Bounded, single-flight derived views of immutable published batches."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import sys
import threading
import weakref
from typing import Any


class Projection(dict):
    """Rows are immutable by contract; request metadata lives in the outer copy."""

    ticker_rows: dict[str, dict[str, Any]]


class ProjectionCache:
    def __init__(self, max_entries: int = 12, max_bytes: int = 64 * 1024 * 1024):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lock = threading.RLock()
        self._batch: weakref.ReferenceType | None = None
        self._entries: OrderedDict[str, tuple[Projection, int]] = OrderedDict()

    def _discard(self, reference):
        with self._lock:
            if self._batch is reference:
                self._entries.clear()
                self._batch = None

    def read(self, batch: dict, key: str, build: Callable[[], dict]) -> Projection:
        # read_batch returns the same object only for the exact opened file
        # identity. A weak reference cannot alias a reused id and does not
        # keep a superseded nine-variant batch alive after publish invalidation.
        # The lock also coalesces concurrent cold projection/validation work.
        try:
            reference = weakref.ref(batch, self._discard)
        except TypeError:
            # Plain dict test/ad-hoc readers have no verifiable lifetime token.
            result = Projection(build())
            result.ticker_rows = {}
            for row in result.get("observation_rows") or result.get("rows") or []:
                result.ticker_rows.setdefault(row["ticker"], row)
            return result
        with self._lock:
            if self._batch is None or self._batch() is not batch:
                self._entries.clear()
                self._batch = reference
            cached = self._entries.get(key)
            if cached is None:
                payload = Projection(build())
                payload.ticker_rows = {}
                for row in payload.get("observation_rows") or payload.get("rows") or []:
                    payload.ticker_rows.setdefault(row["ticker"], row)
                size = _size((key, payload, payload.ticker_rows), set())
                if size <= self.max_bytes:
                    self._entries[key] = (payload, size)
                    while len(self._entries) > self.max_entries or sum(v[1] for v in self._entries.values()) > self.max_bytes:
                        self._entries.popitem(last=False)
            else:
                payload = cached[0]
                self._entries.move_to_end(key)
            result = Projection(payload)
            result.ticker_rows = payload.ticker_rows
            return result


def _size(value: Any, seen: set[int]) -> int:
    identity = id(value)
    if identity in seen:
        return 0
    seen.add(identity)
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_size(k, seen) + _size(v, seen) for k, v in value.items())
    elif isinstance(value, (list, tuple)):
        size += sum(_size(v, seen) for v in value)
    return size


projections = ProjectionCache()
