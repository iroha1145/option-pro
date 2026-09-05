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

from app.data_paths import get_data_paths


MAX_PER_MINUTE = 60
MAX_PER_SECOND = 30


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


def _try_reserve(api_key: str, db_path: Path | str | None) -> tuple[bool, float]:
    if not api_key:
        return False, 60.0
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
    except (OSError, sqlite3.Error):
        # Deliberately do not log exception text: paths or an upstream caller's
        # credentials must never reach a public error response.
        return False, 0.25
    finally:
        if connection is not None:
            connection.close()


def _timeout_seconds(timeout: float) -> float:
    value = float(timeout)
    return min(120.0, max(0.0, value)) if math.isfinite(value) else 0.0


def reserve_finnhub_request(
    api_key: str, *, timeout: float = 0.0, db_path: Path | str | None = None
) -> bool:
    """Reserve one REST call; return False when unavailable within ``timeout``."""
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
    connection: sqlite3.Connection | None = None
    try:
        connection = _connect(Path(db_path) if db_path else default_budget_path())
        connection.execute(
            "INSERT INTO finnhub_cooldowns(key_id, until_at) VALUES (?, ?) "
            "ON CONFLICT(key_id) DO UPDATE SET until_at = MAX(until_at, excluded.until_at)",
            (_key_id(api_key), time.time() + duration),
        )
    except (OSError, sqlite3.Error):
        pass
    finally:
        if connection is not None:
            connection.close()


__all__ = [
    "async_reserve_finnhub_request",
    "default_budget_path",
    "mark_finnhub_rate_limited",
    "reserve_finnhub_request",
]
