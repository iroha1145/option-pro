"""Bounded, best-effort cache diagnostics; never contains request/response text."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
import fcntl
import json
import os
from pathlib import Path
import re
import queue
import stat
import threading
import tempfile
import time
from typing import Any, Mapping

from app.data_paths import data_dir

MISSING = object()
READ_WAIT_SECONDS = 0.01
MAX_RECORDS = 200
MAX_BYTES = 512_000
TASKS = frozenset({"market_brief", "ai_jobs:unknown", *(
    f"ai_jobs:{name}" for name in (
        "earnings_impact", "option_alerts", "signal_analysis", "news_impact", "market_focus"
    )
)})
REASONS = frozenset({"model_changed", "system_changed", "tools_changed", "messages_changed",
                     "previous_message_not_found", "unavailable"})
MODELS = frozenset({"claude-haiku-5-5", "claude-opus-5-5"})


def field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def diagnostic_field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value[name] if name in value else MISSING
    fields_set = getattr(value, "model_fields_set", None)
    if fields_set is not None and name not in fields_set:
        return MISSING
    return getattr(value, name, MISSING)


def message_id(value: Any) -> str | None:
    return value if isinstance(value, str) and re.fullmatch(r"msg_[A-Za-z0-9_-]{1,120}", value) else None


def count(value: Any) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**12 else None


def normalize(diagnostics: Any) -> dict[str, Any]:
    if diagnostics is None:
        return {"diagnostic_state": "no_difference", "reason": None, "estimated_missed_input_tokens": None}
    if diagnostics is MISSING:
        return {"diagnostic_state": "unknown", "reason": None, "estimated_missed_input_tokens": None}
    reason = diagnostic_field(diagnostics, "cache_miss_reason")
    if reason is MISSING:
        return {"diagnostic_state": "unknown", "reason": None, "estimated_missed_input_tokens": None}
    if reason is None:
        state = "pending"
        return {"diagnostic_state": state, "reason": None, "estimated_missed_input_tokens": None}
    kind = field(reason, "type")
    known = kind if isinstance(kind, str) and kind in REASONS else "unknown"
    return {"diagnostic_state": "reason", "reason": known,
            "estimated_missed_input_tokens": count(field(reason, "cache_missed_input_tokens")) if known.endswith("_changed") else None}


def verdict(record: Mapping[str, Any]) -> str:
    if record.get("previous_message_id") is None:
        return "baseline"
    if record.get("diagnostic_state") == "pending":
        return "pending"
    if record.get("reason") in {"previous_message_not_found", "unavailable", "unknown"} or record.get("diagnostic_state") == "unknown":
        return "comparison_unavailable"
    if record.get("diagnostic_state") == "reason":
        return "prefix_changed"
    if not record.get("usage_complete"):
        return "usage_incomplete"
    cached = record.get("cache_read_input_tokens")
    return "cache_read_observed" if isinstance(cached, int) and cached > 0 else "no_cache_read_observed"


class DiagnosticsStore:
    def __init__(self, root: str | Path | None = None):
        self.root = root

    def path(self) -> Path:
        return data_dir(self.root) / "claude-cache-diagnostics.json"

    def read(self) -> dict[str, Any]:
        """Read only; atomic replacement makes unlocked reads consistent."""
        try:
            path = self.path()
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as handle:
                details = os.fstat(handle.fileno())
                if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_BYTES:
                    return {"records": [], "available": False}
                raw = handle.read(MAX_BYTES + 1)
                if len(raw) > MAX_BYTES:
                    return {"records": [], "available": False}
            payload = json.loads(raw)
            if payload.get("version") != 1 or not isinstance(payload.get("records"), list):
                raise ValueError("invalid diagnostics")
            records = [self._clean_record(row) for row in payload["records"][-MAX_RECORDS:]]
            return {"records": [row for row in records if row is not None], "available": True}
        except Exception:
            return {"records": [], "available": False}

    @staticmethod
    def _clean_record(row: Any) -> dict[str, Any] | None:
        if not isinstance(row, dict) or row.get("task") not in TASKS or row.get("model") not in MODELS or not message_id(row.get("message_id")):
            return None
        timestamp = row.get("timestamp")
        if type(timestamp) not in (int, float) or not 0 <= timestamp <= 10**12:
            return None
        state = row.get("diagnostic_state")
        reason = row.get("reason")
        return {
            "task": row["task"], "model": row["model"], "message_id": row["message_id"],
            "previous_message_id": message_id(row.get("previous_message_id")), "timestamp": timestamp,
            "diagnostic_state": state if state in {"pending", "unknown", "no_difference", "reason"} else "unknown",
            "reason": reason if isinstance(reason, str) and reason in REASONS | {"unknown"} else None,
            "estimated_missed_input_tokens": count(row.get("estimated_missed_input_tokens")),
            "cache_read_input_tokens": count(row.get("cache_read_input_tokens")),
            "cache_creation_input_tokens": count(row.get("cache_creation_input_tokens")),
            "usage_complete": row.get("usage_complete") is True,
        }

    def previous(self, task: str) -> str | None:
        try:
            return next((row["message_id"] for row in reversed(self.read()["records"]) if row["task"] == task), None)
        except Exception:
            return None

    def record(self, *, task: str, model: str, previous: str | None, current: Any,
               diagnostics: Any, usage: Any, complete: bool, started_at: float | None = None) -> None:
        """Do not wait on another writer or propagate storage failure into paid work."""
        temporary = None
        try:
            row = self._clean_record({"task": task, "model": model, "message_id": message_id(current),
                "previous_message_id": message_id(previous), "timestamp": time.time() if started_at is None else started_at,
                **normalize(diagnostics), "cache_read_input_tokens": field(usage, "cache_read_input_tokens"),
                "cache_creation_input_tokens": field(usage, "cache_creation_input_tokens"), "usage_complete": complete})
            if row is None:
                return
            path = self.path()
            # DATA_DIR is owned by runtime setup. Missing/unwritable roots simply disable persistence.
            lock_fd = os.open(path.with_suffix(".lock"), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NONBLOCK | os.O_NOFOLLOW, 0o600)
            with os.fdopen(lock_fd, "a", encoding="utf-8") as lock:
                if not stat.S_ISREG(os.fstat(lock.fileno()).st_mode):
                    return
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                os.fchmod(lock.fileno(), 0o600)
                rows = self.read()["records"]
                existing = next((i for i, item in enumerate(rows) if item["task"] == task and item["message_id"] == row["message_id"]), None)
                if existing is None:
                    rows.append(row)
                else:
                    row["timestamp"] = rows[existing]["timestamp"]
                    rows[existing] = row
                rows.sort(key=lambda item: item["timestamp"])
                payload = {"version": 1, "records": rows[-MAX_RECORDS:]}
                fd, temporary = tempfile.mkstemp(prefix=".claude-cache-", dir=path.parent)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, separators=(",", ":"))
                os.replace(temporary, path)
                temporary = None
        except Exception:
            pass
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass


# One dedicated daemon per process, with a fixed queue. asyncio.run never waits
# for this worker at shutdown. Slow filesystems can lose diagnostics, not business work.
_work: queue.Queue = queue.Queue(maxsize=64)
_worker_guard = threading.Lock()
_worker: threading.Thread | None = None
_memory: OrderedDict = OrderedDict()
_memory_guard = threading.Lock()


def _background_loop() -> None:
    while True:
        operation = _work.get()
        try:
            operation()
        except Exception:
            pass
        finally:
            _work.task_done()


def _submit(operation: Any) -> bool:
    global _worker
    try:
        with _worker_guard:
            if _worker is None or not _worker.is_alive():
                _worker = threading.Thread(target=_background_loop, name="claude-cache-diagnostics", daemon=True)
                _worker.start()
        _work.put_nowait(operation)
        return True
    except Exception:
        return False


class BackgroundDiagnosticsStore:
    """Bounded background I/O gateway for provider event loops."""
    def __init__(self, root: str | Path | None = None):
        try:
            self.store = DiagnosticsStore(data_dir(root))  # no filesystem I/O
        except Exception:
            self.store = None

    async def previous(self, task: str) -> str | None:
        if self.store is None:
            return None
        try:
            key = (str(self.store.path()), task)
        except Exception:
            return None
        def memory_parent() -> str | None:
            with _memory_guard:
                memory = _memory.get(key)
            return memory[1] if memory is not None else None
        result: list[Any] = []
        done = threading.Event()
        def read() -> None:
            try:
                result.append(self.store.read())
            finally:
                done.set()
        if not _submit(read):
            return memory_parent()
        deadline = time.monotonic() + READ_WAIT_SECONDS
        while not done.is_set():
            if time.monotonic() >= deadline:
                # Background I/O may lag behind record(), which already retained
                # this process's latest message. Keep that comparison without
                # making the paid request wait for persistence.
                return memory_parent()
            await asyncio.sleep(0.001)
        rows = [row for row in result[0]["records"] if row["task"] == task] if result else []
        with _memory_guard:
            memory = _memory.get(key)
        candidates = [(row["timestamp"], row["message_id"]) for row in rows]
        if memory is not None:
            candidates.append(memory)
        return max(candidates)[1] if candidates else None

    def record(self, **kwargs: Any) -> None:
        if self.store is None:
            return
        try:
            # Snapshot only normalized fields before enqueueing: never retain SDK
            # objects carrying prompts/content or mutable cumulative usage.
            row = self.store._clean_record({"task": kwargs["task"], "model": kwargs["model"],
                "message_id": message_id(kwargs["current"]), "previous_message_id": message_id(kwargs["previous"]),
                "timestamp": kwargs.get("started_at", time.time()), **normalize(kwargs["diagnostics"]),
                "cache_read_input_tokens": field(kwargs["usage"], "cache_read_input_tokens"),
                "cache_creation_input_tokens": field(kwargs["usage"], "cache_creation_input_tokens"),
                "usage_complete": kwargs["complete"]})
            if row is None:
                return
            key = (str(self.store.path()), row["task"])
            with _memory_guard:
                if key not in _memory or row["timestamp"] >= _memory[key][0]:
                    _memory[key] = (row["timestamp"], row["message_id"])
                    _memory.move_to_end(key)
                while len(_memory) > 256:
                    _memory.popitem(last=False)
            diagnostics = (MISSING if row["diagnostic_state"] == "unknown" else
                None if row["diagnostic_state"] == "no_difference" else
                {"cache_miss_reason": None} if row["diagnostic_state"] == "pending" else
                {"cache_miss_reason": {"type": row["reason"], "cache_missed_input_tokens": row["estimated_missed_input_tokens"]}})
            _submit(lambda: self.store.record(task=row["task"], model=row["model"],
                previous=row["previous_message_id"], current=row["message_id"], diagnostics=diagnostics,
                usage=row, complete=row["usage_complete"], started_at=row["timestamp"]))
        except Exception:
            pass
