"""Two bounded, disposable spawn workers for local hotspot reads only.

HTTP ownership and availability checks stay in the web process. A worker is
leased for exactly one read, with no internal submission queue. Only primitive
configuration and paths cross the pipe; no service, session or connection does.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
import multiprocessing as mp
from pathlib import Path
import sqlite3
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

#: 个人部署的 hotspots 读者只有 owner 与几位朋友：2 个 spawn 子进程足够覆盖
#: 并发（原值 8 的每个子进程都重新 import 大模块，常驻数百 MB）。池满时第
#: 三个并发读在 read() 处如实报 hotspot_read_workers_busy，由服务层落成
#: cache_unavailable 载荷，下一次轮询自愈。
HOTSPOT_READ_WORKERS = 2
_START_TIMEOUT_SECONDS = 20.0
_READ_TIMEOUT_SECONDS = 30.0
_STOP_TIMEOUT_SECONDS = .5
_CURRENT_POOL: ContextVar[HotspotReadPool | None] = ContextVar("hotspot_read_pool", default=None)


@contextmanager
def hotspot_read_pool_context(pool):
    token = _CURRENT_POOL.set(pool)
    try:
        yield
    finally:
        _CURRENT_POOL.reset(token)


def current_hotspot_read_pool():
    return _CURRENT_POOL.get()


@dataclass(frozen=True)
class HotspotRead:
    db_path: str
    ai_db_path: str
    mode: str
    canonical_tickers: tuple[str, ...]
    model: str
    reasoning: str
    focus_model: str
    focus_reasoning: str
    max_queued: int
    manual_refresh_cooldown_seconds: int
    scheduled_skip_template_commentary: bool
    limit: int
    now: datetime | None
    owner_context: bool
    include_owner_state: bool


def _read_hotspots(request: HotspotRead):
    from app.access import request_owner_access_context
    from .local_intelligence import LocalCatalystIntelligence
    from .personal_service import PersonalCatalystService, _HOTSPOT_PROJECTION_SCAN_LIMIT

    # The path-only object intentionally has no initialize, budget or job-write
    # methods. Hotspot receipts use Local's existing mode=ro snapshot readers.
    repository = SimpleNamespace(path=Path(request.ai_db_path))
    intelligence = LocalCatalystIntelligence(
        request.db_path, repository, mode=request.mode,
        canonical_tickers=request.canonical_tickers,
        model=request.model, reasoning=request.reasoning,
        news_model=request.model, news_reasoning=request.reasoning,
        focus_model=request.focus_model, focus_reasoning=request.focus_reasoning,
        max_queued=request.max_queued,
        manual_refresh_cooldown_seconds=request.manual_refresh_cooldown_seconds,
        scheduled_skip_template_commentary=request.scheduled_skip_template_commentary,
        _read_only=True,
    )
    with request_owner_access_context(request.owner_context):
        payload = intelligence.hotspots(
            limit=max(request.limit, _HOTSPOT_PROJECTION_SCAN_LIMIT),
            now=request.now, published_only=not request.include_owner_state,
        )
        projected = PersonalCatalystService._project_hotspots(
            payload, include_owner_state=request.include_owner_state,
        )
        projected["items"] = list(projected.get("items") or [])[:request.limit]
        return projected


def _worker_main(pipe):
    # This module does not import app.main or instantiate the HTTP application.
    # Fail closed even if a future refactor accidentally reaches a provider or
    # opens an ordinary SQLite connection from this dedicated read process.
    allowed_paths: set[str] = set()
    def audit(event, values):
        if event == "socket.connect":
            raise RuntimeError("hotspot_read_network_forbidden")
        if event == "sqlite3.connect":
            uri = urlsplit(str(values[0]))
            if (uri.scheme != "file" or parse_qs(uri.query).get("mode") != ["ro"]
                    or str(Path(unquote(uri.path)).resolve()) not in allowed_paths):
                raise sqlite3.OperationalError("hotspot_read_requires_read_only_store")
    sys.addaudithook(audit)
    try:
        # Import prewarming is independent of the presence/health of data files.
        from . import local_intelligence, personal_service  # noqa: F401
        from .errors import CatalystError
        pipe.send({"ready": True})
        while True:
            request = pipe.recv()
            if request is None:
                break
            allowed_paths.clear()
            allowed_paths.update(str(Path(path).resolve()) for path in
                                 (request.db_path, request.ai_db_path))
            try:
                reply = {"result": _read_hotspots(request)}
            except (sqlite3.Error, OSError):
                reply = {"error": "store"}
            except CatalystError as error:
                reply = {"error": "catalyst", "fields": {
                    "code": error.code, "message": error.message,
                    "retryable": error.retryable,
                    "retry_after_seconds": error.retry_after_seconds,
                    "counts_for_circuit": error.counts_for_circuit,
                    "resync_from": error.resync_from,
                }}
            except Exception as error:
                # No provider bodies, paths, secrets or arbitrary exception
                # objects cross the error protocol.
                reply = {"error": "store" if personal_service.PersonalCatalystService._is_local_store_error(error)
                         else "unexpected"}
            finally:
                allowed_paths.clear()
            pipe.send(reply)
    except (EOFError, BrokenPipeError):
        pass
    finally:
        pipe.close()


@dataclass
class _Worker:
    process: object
    pipe: object
    leased: bool = False


def _stop(worker):
    if worker is None:
        return
    process = worker.process
    if process.is_alive():
        process.terminate()
    process.join(_STOP_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        process.join(_STOP_TIMEOUT_SECONDS)
    worker.pipe.close()


class HotspotReadPool:
    def __init__(self):
        self._context = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._workers: list[_Worker | None] = [None] * HOTSPOT_READ_WORKERS
        self._closed = False
        self._started = False

    @property
    def available(self):
        # Called by the ASGI event loop. Never wait for a recovery/shutdown
        # thread's join lock here; read() validates this snapshot under lock.
        return self._started and not self._closed

    def _spawn(self):
        parent, child = self._context.Pipe()
        process = self._context.Process(target=_worker_main, args=(child,),
                                        name="catalyst-hotspot-read", daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        return _Worker(process, parent)

    @staticmethod
    def _ready(worker, timeout):
        if not worker.pipe.poll(max(0, timeout)) or worker.pipe.recv() != {"ready": True}:
            raise sqlite3.OperationalError("hotspot_read_worker_unavailable")

    def start(self):
        # Lifespan calls start/close outside the event loop. No data is opened.
        with self._lock:
            if self._closed or self._started:
                return
            try:
                deadline = time.monotonic() + _START_TIMEOUT_SECONDS
                for index in range(HOTSPOT_READ_WORKERS):
                    self._workers[index] = self._spawn()
                for worker in self._workers:
                    self._ready(worker, deadline - time.monotonic())
                self._started = True
            except BaseException:
                for worker in self._workers:
                    _stop(worker)
                self._workers = [None] * HOTSPOT_READ_WORKERS
                raise

    def read(self, request: HotspotRead):
        # No waiting futures or unbounded queue. The pool itself is the
        # concurrency bound; direct over-submission fails locally.
        with self._lock:
            if self._closed or not self._started:
                raise sqlite3.OperationalError("hotspot_read_worker_unavailable")
            index = next((i for i, worker in enumerate(self._workers)
                          if worker is None or not worker.leased), None)
            if index is None:
                raise sqlite3.OperationalError("hotspot_read_workers_busy")
            worker = self._workers[index]
            newly_spawned = False
            try:
                if worker is None or not worker.process.is_alive():
                    _stop(worker)
                    worker = self._spawn()
                    self._workers[index] = worker
                    newly_spawned = True
            except BaseException:
                _stop(worker)
                self._workers[index] = None
                raise sqlite3.OperationalError("hotspot_read_worker_unavailable") from None
            worker.leased = True
        if newly_spawned:
            try:
                self._ready(worker, _START_TIMEOUT_SECONDS)
            except BaseException:
                with self._lock:
                    _stop(worker)
                    self._workers[index] = None
                raise sqlite3.OperationalError("hotspot_read_worker_unavailable") from None
        expired = threading.Event()
        def expire():
            expired.set()
            # Killing the writer unblocks even recv() after a partial frame;
            # poll alone would not bound that case. Only this lease owns it.
            try:
                worker.process.kill()
            except (OSError, ValueError):
                pass
        timer = threading.Timer(_READ_TIMEOUT_SECONDS, expire)
        timer.daemon = True
        timer.start()
        broken = False
        try:
            worker.pipe.send(request)
            reply = worker.pipe.recv()
            if expired.is_set():
                raise EOFError
        except (EOFError, OSError, ValueError):
            broken = True
            raise sqlite3.OperationalError("hotspot_read_worker_unavailable") from None
        finally:
            timer.cancel()
            timer.join()
            with self._lock:
                if broken or self._closed:
                    _stop(worker)
                    self._workers[index] = None
                else:
                    worker.leased = False
        if reply.get("error") == "store":
            raise sqlite3.OperationalError("hotspot_read_store_unavailable")
        if reply.get("error") == "catalyst":
            from .errors import CatalystError
            raise CatalystError(**reply["fields"])
        if "result" not in reply:
            raise RuntimeError("hotspot_read_failed")
        return reply["result"]

    def close(self):
        with self._lock:
            self._closed = True
            # Terminate all first, then bounded joins. A timed-out or cancelled
            # HTTP caller cannot leave its owned child running after shutdown.
            for worker in self._workers:
                if worker is not None and worker.process.is_alive():
                    worker.process.terminate()
            for index, worker in enumerate(self._workers):
                _stop(worker)
                self._workers[index] = None
