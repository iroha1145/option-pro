"""Durable, catalog-bounded sector IV demand shared by HTTP and the worker.

Only the worker calls providers. SQLite transactions serialize requests and
claims across processes; lease tokens fence publication after a worker restart.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3
import time
from typing import Any, Callable
import uuid

from app.services.sectors import SECTORS

MIN_REFRESH_SECONDS = 300
LEASE_SECONDS = 600
QUEUE_TIMEOUT_SECONDS = 1800
SCAN_TIMEOUT_SECONDS = 180
MAX_STALE_SECONDS = 7 * 86400


def refresh_interval(now: float) -> int:
    from app.services.realtime_quotes import market_session

    return 900 if market_session(datetime.fromtimestamp(now, timezone.utc)) == "regular" else 21600


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


class SectorIVRefreshStore:
    def __init__(self, path: Path, *, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock

    @contextmanager
    def transaction(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        connection.row_factory = sqlite3.Row
        try:
            # Acquire the write reservation before initializing the schema.
            # Switching journal mode on concurrent cold opens can raise BUSY
            # without honoring SQLite's busy timeout. This bounded 24-row
            # queue uses the default durable rollback journal instead.
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""CREATE TABLE IF NOT EXISTS sector_refresh (
                sector_id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'idle',
                requested_at REAL, started_at REAL, completed_at REAL,
                next_allowed_at REAL NOT NULL DEFAULT 0,
                next_refresh_at REAL NOT NULL DEFAULT 0,
                failures INTEGER NOT NULL DEFAULT 0,
                error_code TEXT, lease_token TEXT, lease_until REAL,
                priority INTEGER NOT NULL DEFAULT 0
            )""")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _row(self, connection, sector_id: str, now: float):
        if sector_id not in SECTORS:
            raise ValueError("Unknown sector")
        connection.execute("INSERT OR IGNORE INTO sector_refresh(sector_id) VALUES (?)", (sector_id,))
        connection.execute("""UPDATE sector_refresh SET status='failed',
            error_code='worker_interrupted', lease_token=NULL, lease_until=NULL,
            next_allowed_at=?, next_refresh_at=?
            WHERE sector_id=? AND status='running' AND lease_until<=?""",
            (now, now, sector_id, now))
        connection.execute("""UPDATE sector_refresh SET status='failed',
            error_code='worker_unavailable', next_allowed_at=?, next_refresh_at=?
            WHERE sector_id=? AND status='queued' AND requested_at<=?""",
            (now + MIN_REFRESH_SECONDS, now + MIN_REFRESH_SECONDS,
             sector_id, now - QUEUE_TIMEOUT_SECONDS))
        return connection.execute("SELECT * FROM sector_refresh WHERE sector_id=?", (sector_id,)).fetchone()

    @staticmethod
    def _state(row, now: float, *, cooldown: bool = False) -> dict[str, Any]:
        status = 'cooldown' if cooldown else row['status']
        retry_at = row['next_allowed_at'] if status in {'cooldown', 'failed'} else 0
        return {
            'status': status,
            'retry_after_seconds': max(0, math.ceil(retry_at - now)),
            'requested_at': _iso(row['requested_at']),
            'started_at': _iso(row['started_at']),
            'completed_at': _iso(row['completed_at']),
            'next_refresh_at': _iso(row['next_refresh_at']),
            'error_code': row['error_code'],
        }

    def status(self, sector_id: str) -> dict[str, Any]:
        now = self.clock()
        with self.transaction() as connection:
            return self._state(self._row(connection, sector_id, now), now)

    def request(self, sector_id: str) -> dict[str, Any]:
        now = self.clock()
        with self.transaction() as connection:
            row = self._row(connection, sector_id, now)
            if row['status'] in {'queued', 'running'}:
                if row['status'] == 'queued':
                    connection.execute("UPDATE sector_refresh SET priority=1 WHERE sector_id=?", (sector_id,))
                return self._state(row, now)
            if row['next_allowed_at'] > now:
                return self._state(row, now, cooldown=row['status'] != 'failed')
            connection.execute("""UPDATE sector_refresh SET status='queued', requested_at=?,
                next_refresh_at=?, priority=1, error_code=NULL WHERE sector_id=?""", (now, now, sector_id))
            return self._state(self._row(connection, sector_id, now), now)

    def schedule_due(self) -> None:
        now = self.clock()
        interval = refresh_interval(now)
        with self.transaction() as connection:
            for sector_id in SECTORS:
                row = self._row(connection, sector_id, now)
                if row['status'] in {'queued', 'running'} or row['next_allowed_at'] > now:
                    continue
                # Recompute when the market opens: a weekend six-hour deadline
                # must not delay the first regular-session update.
                due = (row['completed_at'] or 0) + interval if row['status'] == 'idle' else row['next_refresh_at']
                if due > now:
                    connection.execute("UPDATE sector_refresh SET next_refresh_at=? WHERE sector_id=?", (due, sector_id))
                    continue
                connection.execute("""UPDATE sector_refresh SET status='queued', requested_at=?,
                    next_refresh_at=?, priority=0, error_code=NULL WHERE sector_id=?""", (now, now, sector_id))

    def claim(self) -> tuple[str, str] | None:
        now = self.clock()
        with self.transaction() as connection:
            row = connection.execute("""SELECT sector_id FROM sector_refresh
                WHERE status='queued' AND next_allowed_at<=?
                ORDER BY priority DESC, requested_at, sector_id LIMIT 1""", (now,)).fetchone()
            if row is None:
                return None
            sector_id = row['sector_id']
            if sector_id not in SECTORS:
                connection.execute("DELETE FROM sector_refresh WHERE sector_id=?", (sector_id,))
                return None
            token = uuid.uuid4().hex
            connection.execute("""UPDATE sector_refresh SET status='running', started_at=?,
                next_allowed_at=?, lease_token=?, lease_until=? WHERE sector_id=?""",
                (now, now + MIN_REFRESH_SECONDS, token, now + LEASE_SECONDS, sector_id))
            return sector_id, token

    def finish(self, sector_id: str, token: str, *, publish: Callable[[], None] | None = None,
               error_code: str | None = None) -> bool:
        now = self.clock()
        with self.transaction() as connection:
            row = self._row(connection, sector_id, now)
            if row['status'] != 'running' or row['lease_token'] != token:
                return False
            if error_code is None:
                if publish is not None:
                    publish()  # Atomic file replacement, fenced by the DB transaction.
                connection.execute("""UPDATE sector_refresh SET status='idle', completed_at=?,
                    failures=0, error_code=NULL, next_refresh_at=?, lease_token=NULL,
                    lease_until=NULL WHERE sector_id=?""", (now, now + refresh_interval(now), sector_id))
            else:
                failures = row['failures'] + 1
                retry = now + min(3600, MIN_REFRESH_SECONDS * 2 ** min(failures - 1, 4))
                connection.execute("""UPDATE sector_refresh SET status='failed', failures=?,
                    error_code=?, next_allowed_at=?, next_refresh_at=?, lease_token=NULL,
                    lease_until=NULL WHERE sector_id=?""", (failures, error_code, retry, retry, sector_id))
            return True


def default_store() -> SectorIVRefreshStore:
    # Keep queue and its snapshots on the same durable data volume.
    from app.api import sectors

    return SectorIVRefreshStore(sectors._SECTOR_IV_SNAPSHOT_DIR / 'refresh.sqlite', clock=time.time)


def _usable_worker_payload(sector_id: str, payload: dict[str, Any], now: float) -> dict[str, Any]:
    from app.api import sectors

    rows = []
    for item in payload.get("rankings", []):
        source_at = sectors._source_timestamp(item.get("as_of"))
        if source_at is None or not -60 <= now - source_at <= MAX_STALE_SECONDS:
            continue
        rows.append({
            "ticker": item["ticker"], "name": item.get("name"),
            "price": item.get("price"), "price_provider": item.get("price_provider"),
            "iv": item["atm_iv_percent"] / 100,
            "_stale": bool(item.get("_stale")) or now - source_at >= refresh_interval(now),
            "as_of": item["as_of"], "provider": "Yahoo/yfinance",
        })
    if len(rows) == len(payload.get("rankings", [])):
        if any(row["_stale"] for row in rows):
            return {**payload, "_stale": True, "source_status": "stale"}
        return payload
    return sectors._rank_iv_rows(sector_id, rows)


async def run_refresh_batch(*, store: SectorIVRefreshStore | None = None,
                            schedule: bool = True, limit: int = 4) -> dict[str, int]:
    from app.api import sectors

    store = store or default_store()
    if schedule:
        await asyncio.to_thread(store.schedule_due)
    outcome = {'completed': 0, 'failed': 0}
    for _ in range(limit):
        claim = await asyncio.to_thread(store.claim)
        if claim is None:
            break
        sector_id, token = claim
        try:
            payload = await asyncio.wait_for(sectors._iv_ranking_payload(sector_id), SCAN_TIMEOUT_SECONDS)
            payload = _usable_worker_payload(sector_id, payload, store.clock())
            source_at = sectors._source_timestamp(payload.get('as_of'))
            if not payload.get('rankings') or source_at is None or store.clock() - source_at > MAX_STALE_SECONDS:
                raise ValueError('sector_iv_no_data')
            saved_at = store.clock()
            published = await asyncio.to_thread(
                store.finish, sector_id, token,
                publish=lambda: sectors._write_sector_iv_snapshot(
                    sector_id, payload, saved_at=saved_at, snapshot_origin='worker'),
            )
            outcome['completed'] += int(published)
        except asyncio.CancelledError:
            await asyncio.to_thread(store.finish, sector_id, token, error_code='worker_interrupted')
            raise
        except Exception as exc:
            code = 'sector_iv_timeout' if isinstance(exc, TimeoutError) else 'sector_iv_refresh_failed'
            await asyncio.to_thread(store.finish, sector_id, token, error_code=code)
            outcome['failed'] += 1
    return outcome
