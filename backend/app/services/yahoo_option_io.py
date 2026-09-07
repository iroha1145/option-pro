"""Process-local Yahoo options I/O budget.

The slot is held until the blocking provider call finishes. Client cancel,
route timeout, or waiter timeout must not release the slot early — that
would let abandoned threads break the real outbound cap.

This budget is per process. A multi-worker deployment multiplies it by
the worker count; do not treat one process's coalescing as cluster-wide.
"""

from __future__ import annotations

from collections.abc import Callable
import threading
from typing import Any, TypeVar

from app.config import get_settings


T = TypeVar("T")


class YahooOptionBusy(RuntimeError):
    """Queue is full or the wait for a slot expired."""


class YahooOptionTimeout(TimeoutError):
    """The waiter gave up; the provider thread may still hold a slot."""


class YahooOptionIO:
    def __init__(
        self,
        *,
        max_in_flight: int = 3,
        max_queue: int = 8,
        queue_wait_seconds: float = 8.0,
        call_timeout_seconds: float = 20.0,
    ) -> None:
        if max_in_flight < 1:
            raise ValueError("max_in_flight must be >= 1")
        if max_queue < 1:
            raise ValueError("max_queue must be >= 1")
        self.max_in_flight = max_in_flight
        self.max_queue = max_queue
        self.queue_wait_seconds = queue_wait_seconds
        self.call_timeout_seconds = call_timeout_seconds
        self._sem = threading.Semaphore(max_in_flight)
        self._lock = threading.Lock()
        self._queued = 0
        self._in_flight = 0
        self._peak_in_flight = 0
        self._started = 0
        self._completed = 0
        self._rejected = 0
        self._timed_out_waiters = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "queued": self._queued,
                "in_flight": self._in_flight,
                "peak_in_flight": self._peak_in_flight,
                "started": self._started,
                "completed": self._completed,
                "rejected": self._rejected,
                "timed_out_waiters": self._timed_out_waiters,
                "max_in_flight": self.max_in_flight,
                "max_queue": self.max_queue,
            }

    def run(self, fn: Callable[[], T]) -> T:
        with self._lock:
            if self._queued >= self.max_queue:
                self._rejected += 1
                raise YahooOptionBusy("yahoo options queue is full")
            self._queued += 1
        acquired = False
        try:
            acquired = self._sem.acquire(timeout=self.queue_wait_seconds)
        finally:
            with self._lock:
                self._queued -= 1
                if not acquired:
                    self._rejected += 1
        if not acquired:
            raise YahooOptionBusy("yahoo options concurrency budget is busy")

        with self._lock:
            self._in_flight += 1
            self._started += 1
            if self._in_flight > self._peak_in_flight:
                self._peak_in_flight = self._in_flight

        box: dict[str, Any] = {}

        def worker() -> None:
            try:
                box["result"] = fn()
            except Exception as exc:
                box["error"] = exc
            finally:
                with self._lock:
                    self._in_flight -= 1
                    self._completed += 1
                self._sem.release()

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=self.call_timeout_seconds)
        if thread.is_alive():
            with self._lock:
                self._timed_out_waiters += 1
            raise YahooOptionTimeout("yahoo options provider call timed out")
        if "error" in box:
            raise box["error"]
        return box["result"]


_io: YahooOptionIO | None = None
_io_lock = threading.Lock()


def _settings_io() -> YahooOptionIO:
    settings = get_settings()
    return YahooOptionIO(
        max_in_flight=int(getattr(settings, "yahoo_option_max_in_flight", 3)),
        max_queue=int(getattr(settings, "yahoo_option_max_queue", 8)),
        queue_wait_seconds=float(getattr(settings, "yahoo_option_queue_wait_seconds", 8.0)),
        call_timeout_seconds=float(getattr(settings, "yahoo_option_call_timeout_seconds", 20.0)),
    )


def get_yahoo_option_io() -> YahooOptionIO:
    global _io
    with _io_lock:
        if _io is None:
            _io = _settings_io()
        return _io


def reset_yahoo_option_io(instance: YahooOptionIO | None = None) -> YahooOptionIO:
    """Test helper. Production callers should not rebuild the process budget."""
    global _io
    with _io_lock:
        _io = instance if instance is not None else _settings_io()
        return _io


def run_yahoo_option_io(fn: Callable[[], T]) -> T:
    return get_yahoo_option_io().run(fn)
