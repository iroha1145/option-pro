"""Account-wide Finnhub REST budget shared by API and worker processes.

Reservations are committed before making an upstream call. SQLite serializes
contenders; waiting happens only after the transaction and connection close.
Only a hash of the API key is persisted. A storage failure fails closed so it
cannot silently turn a deployment issue into an unbounded upstream bill.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
import sqlite3
import time
from typing import Callable, TypeVar

from app.data_paths import get_data_paths


MAX_PER_MINUTE = 60
MAX_PER_SECOND = 30
_STORAGE_BUSY_RETRY_SECONDS = 2.0
_T = TypeVar("_T")


def default_budget_path() -> Path:
    return get_data_paths().root / "finnhub-budget.sqlite"


def _key_id(api_key: str) -> str:
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=0.1, isolation_level=None)
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS finnhub_requests "
            "(key_id TEXT NOT NULL, requested_at REAL NOT NULL)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS finnhub_requests_key_time "
            "ON finnhub_requests(key_id, requested_at)"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS finnhub_cooldowns "
            "(key_id TEXT PRIMARY KEY, until_at REAL NOT NULL)"
        )
        return connection
    except Exception:
        connection.close()
        raise


def _with_busy_retry(operation: Callable[[], _T]) -> _T:
    """Retry transient counter contention, never an exhausted account window.

    A zero quota-wait timeout still needs to read/commit the shared counter.
    In particular, concurrent cold-start schema writes may exceed one SQLite
    busy timeout without indicating that any provider quota has been spent.
    Each operation closes its connection before this bounded wait begins.
    """
    deadline = time.perf_counter() + _STORAGE_BUSY_RETRY_SECONDS
    while True:
        try:
            return operation()
        except sqlite3.Error as exc:
            code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
            remaining = deadline - time.perf_counter()
            if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or remaining <= 0:
                raise
            time.sleep(min(0.025, remaining))


def _reserve_once(api_key: str, db_path: Path | str | None) -> tuple[bool, float]:
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(Path(db_path) if db_path else default_budget_path())
        connection.execute("BEGIN IMMEDIATE")
        now = time.time()
        key_id = _key_id(api_key)
        connection.execute("DELETE FROM finnhub_requests WHERE requested_at <= ?", (now - 60,))
        connection.execute("DELETE FROM finnhub_cooldowns WHERE until_at <= ?", (now,))
        cooldown = connection.execute(
            "SELECT until_at FROM finnhub_cooldowns WHERE key_id = ?", (key_id,)
        ).fetchone()
        requests = [row[0] for row in connection.execute(
            "SELECT requested_at FROM finnhub_requests WHERE key_id = ? ORDER BY requested_at",
            (key_id,),
        ).fetchall()]
        delay = max(0.0, cooldown[0] - now) if cooldown else 0.0
        if len(requests) >= MAX_PER_MINUTE:
            delay = max(delay, requests[-MAX_PER_MINUTE] + 60 - now)
        second = [value for value in requests if value > now - 1]
        if len(second) >= MAX_PER_SECOND:
            delay = max(delay, second[-MAX_PER_SECOND] + 1 - now)
        if delay <= 0:
            connection.execute(
                "INSERT INTO finnhub_requests(key_id, requested_at) VALUES (?, ?)",
                (key_id, now),
            )
        connection.execute("COMMIT")
        return delay <= 0, max(0.01, delay)
    finally:
        if connection is not None:
            connection.close()


def _try_reserve(api_key: str, db_path: Path | str | None) -> tuple[bool, float]:
    if not api_key:
        return False, 60.0
    try:
        return _with_busy_retry(lambda: _reserve_once(api_key, db_path))
    except (OSError, sqlite3.Error):
        # Non-transient failures and exhausted storage retries still fail
        # closed. Never expose paths, credentials, or exception text.
        return False, 0.25


def _timeout_seconds(timeout: float) -> float:
    value = float(timeout)
    return min(120.0, max(0.0, value)) if math.isfinite(value) else 0.0


def reserve_finnhub_request(
    api_key: str, *, timeout: float = 0.0, db_path: Path | str | None = None
) -> bool:
    """Reserve one REST call; ``timeout`` controls waiting for account quota.

    Shared-counter contention has its own short, bounded retry; it never
    grants a reservation before an atomic transaction has committed.
    """
    deadline = time.monotonic() + _timeout_seconds(timeout)
    while True:
        reserved, delay = _try_reserve(api_key, db_path)
        if reserved:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(delay, remaining, 1.0))


async def async_reserve_finnhub_request(
    api_key: str, *, timeout: float = 0.0, db_path: Path | str | None = None
) -> bool:
    """Async reservation with cancellable waits and no SQLite work on the loop."""
    deadline = time.monotonic() + _timeout_seconds(timeout)
    while True:
        reserved, delay = await asyncio.to_thread(_try_reserve, api_key, db_path)
        if reserved:
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(delay, remaining, 1.0))


def mark_finnhub_rate_limited(
    api_key: str,
    *,
    retry_after: float | str = 60.0,
    db_path: Path | str | None = None,
) -> None:
    """Publish a 429 cooldown to every process using this account."""
    if not api_key:
        return
    try:
        duration = float(retry_after)
    except (ValueError, TypeError):
        duration = 60.0
    if not math.isfinite(duration):
        duration = 60.0
    duration = min(3600.0, max(1.0, duration))
    def record_cooldown() -> None:
        connection = _connect(Path(db_path) if db_path else default_budget_path())
        try:
            connection.execute(
                "INSERT INTO finnhub_cooldowns(key_id, until_at) VALUES (?, ?) "
                "ON CONFLICT(key_id) DO UPDATE SET until_at = MAX(until_at, excluded.until_at)",
                (_key_id(api_key), time.time() + duration),
            )
        finally:
            connection.close()

    try:
        _with_busy_retry(record_cooldown)
    except (OSError, sqlite3.Error):
        pass


__all__ = [
    "async_reserve_finnhub_request",
    "default_budget_path",
    "mark_finnhub_rate_limited",
    "reserve_finnhub_request",
]
