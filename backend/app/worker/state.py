from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping


SCHEMA_VERSION = "optix-worker-v2"
LOCK_NAME = "optix-worker"
_MAX_DETAILS_BYTES = 16 * 1024
_ACTION_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,200}$")
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS optix_worker_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS optix_worker_lock (
    lock_name TEXT PRIMARY KEY,
    owner_id TEXT,
    fencing_token INTEGER NOT NULL DEFAULT 0,
    heartbeat_at TEXT,
    expires_at TEXT
);
CREATE TABLE IF NOT EXISTS worker_task_status (
    task_name TEXT PRIMARY KEY,
    enabled INTEGER NOT NULL CHECK(enabled IN (0,1)),
    status TEXT NOT NULL,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    last_started_at TEXT,
    last_completed_at TEXT,
    last_success_at TEXT,
    next_run_at TEXT,
    error_code TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_action_requests (
    request_id TEXT PRIMARY KEY,
    action_type TEXT NOT NULL,
    task_name TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('queued','running','completed','failed')),
    cooldown_seconds REAL NOT NULL DEFAULT 0 CHECK(cooldown_seconds >= 0),
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    cooldown_until TEXT,
    owner_id TEXT,
    fencing_token INTEGER,
    error_code TEXT,
    details_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL,
    UNIQUE(action_type,idempotency_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_worker_action_active
ON worker_action_requests(action_type)
WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_worker_action_task_status
ON worker_action_requests(task_name,status,requested_at);
"""
SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()


class WorkerAlreadyRunning(RuntimeError):
    pass


class WorkerLeaseLost(RuntimeError):
    pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("worker timestamps must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(parsed)


def _details_json(details: Mapping[str, Any] | None) -> str:
    raw = json.dumps(
        dict(details or {}),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(raw.encode("utf-8")) > _MAX_DETAILS_BYTES:
        raise ValueError("worker task details are too large")
    return raw


class WorkerStateRepository:
    """Own the unified process lease and bounded per-task status."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    @contextmanager
    def _connect(self, *, read_only: bool = False) -> Iterator[sqlite3.Connection]:
        if read_only:
            uri = self.path.resolve().as_uri() + "?mode=ro"
            connection = sqlite3.connect(uri, uri=True, timeout=2.0)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self, *, now: datetime | None = None) -> None:
        observed = _as_utc(now or utc_now())
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA_SQL)
            row = connection.execute(
                "SELECT checksum FROM optix_worker_schema WHERE version=?",
                (SCHEMA_VERSION,),
            ).fetchone()
            if row is not None and row["checksum"] != SCHEMA_CHECKSUM:
                raise RuntimeError("optix_worker_schema_checksum_mismatch")
            connection.execute(
                """
                INSERT OR IGNORE INTO optix_worker_schema(version,checksum,applied_at)
                VALUES(?,?,?)
                """,
                (SCHEMA_VERSION, SCHEMA_CHECKSUM, _iso(observed)),
            )
            connection.commit()

    @staticmethod
    def _live(row: sqlite3.Row | None, observed: datetime) -> bool:
        return bool(
            row is not None
            and row["owner_id"]
            and (_parse(row["expires_at"]) or observed) > observed
        )

    def acquire(
        self,
        owner_id: str,
        *,
        lease_seconds: float,
        force: bool = False,
        now: datetime | None = None,
    ) -> int | None:
        if not owner_id or len(owner_id) > 200:
            raise ValueError("worker owner id is invalid")
        if lease_seconds <= 0:
            raise ValueError("worker lease must be positive")
        observed = _as_utc(now or utc_now())
        expires = observed + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM optix_worker_lock WHERE lock_name=?",
                (LOCK_NAME,),
            ).fetchone()
            if self._live(row, observed) and not force:
                connection.rollback()
                return None
            token = int(row["fencing_token"] if row is not None else 0) + 1
            connection.execute(
                """
                INSERT INTO optix_worker_lock(
                    lock_name,owner_id,fencing_token,heartbeat_at,expires_at
                ) VALUES(?,?,?,?,?)
                ON CONFLICT(lock_name) DO UPDATE SET
                    owner_id=excluded.owner_id,
                    fencing_token=excluded.fencing_token,
                    heartbeat_at=excluded.heartbeat_at,
                    expires_at=excluded.expires_at
                """,
                (LOCK_NAME, owner_id, token, _iso(observed), _iso(expires)),
            )
            connection.commit()
            return token

    def renew(
        self,
        owner_id: str,
        fencing_token: int,
        *,
        lease_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        observed = _as_utc(now or utc_now())
        expires = observed + timedelta(seconds=lease_seconds)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM optix_worker_lock WHERE lock_name=?",
                (LOCK_NAME,),
            ).fetchone()
            if (
                not self._live(row, observed)
                or row["owner_id"] != owner_id
                or int(row["fencing_token"]) != int(fencing_token)
            ):
                connection.rollback()
                return False
            connection.execute(
                """
                UPDATE optix_worker_lock SET heartbeat_at=?,expires_at=?
                WHERE lock_name=? AND owner_id=? AND fencing_token=?
                """,
                (_iso(observed), _iso(expires), LOCK_NAME, owner_id, fencing_token),
            )
            connection.commit()
            return True

    def release(
        self,
        owner_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> bool:
        observed = _as_utc(now or utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE optix_worker_lock
                SET owner_id=NULL,heartbeat_at=?,expires_at=?
                WHERE lock_name=? AND owner_id=? AND fencing_token=?
                """,
                (_iso(observed), _iso(observed), LOCK_NAME, owner_id, fencing_token),
            )
            connection.commit()
            return cursor.rowcount == 1

    @staticmethod
    def _assert_fence(
        connection: sqlite3.Connection,
        owner_id: str,
        fencing_token: int,
        observed: datetime,
    ) -> None:
        row = connection.execute(
            "SELECT * FROM optix_worker_lock WHERE lock_name=?",
            (LOCK_NAME,),
        ).fetchone()
        if (
            not WorkerStateRepository._live(row, observed)
            or row["owner_id"] != owner_id
            or int(row["fencing_token"]) != int(fencing_token)
        ):
            raise WorkerLeaseLost("unified worker lease was lost")

    def recover_interrupted(
        self,
        owner_id: str,
        fencing_token: int,
        *,
        now: datetime | None = None,
    ) -> int:
        observed = _as_utc(now or utc_now())
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_fence(connection, owner_id, fencing_token, observed)
            cursor = connection.execute(
                """
                UPDATE worker_task_status
                SET status='interrupted',error_code='worker_restarted',updated_at=?
                WHERE status IN ('starting','running','stopping')
                """,
                (_iso(observed),),
            )
            connection.execute(
                """
                UPDATE worker_action_requests
                SET status='queued',started_at=NULL,owner_id=NULL,fencing_token=NULL,
                    error_code='worker_restarted',updated_at=?
                WHERE status='running'
                """,
                (_iso(observed),),
            )
            connection.commit()
            return cursor.rowcount

    @staticmethod
    def _action_item(row: sqlite3.Row) -> dict[str, Any]:
        item = dict(row)
        item["cooldown_seconds"] = float(item["cooldown_seconds"])
        try:
            item["details"] = json.loads(item.pop("details_json"))
        except (TypeError, json.JSONDecodeError):
            item["details"] = {}
            item.pop("details_json", None)
        item.pop("owner_id", None)
        item.pop("fencing_token", None)
        return item

    def request_action(
        self,
        action_type: str,
        task_name: str,
        idempotency_key: str,
        *,
        cooldown_seconds: float = 0.0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        if not _ACTION_NAME.fullmatch(action_type):
            raise ValueError("worker action type is invalid")
        if not _ACTION_NAME.fullmatch(task_name):
            raise ValueError("worker action task is invalid")
        if not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise ValueError("worker action idempotency key is invalid")
        if not 0 <= float(cooldown_seconds) <= 86_400:
            raise ValueError("worker action cooldown is invalid")
        observed = _as_utc(now or utc_now())
        observed_text = _iso(observed)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM worker_action_requests
                WHERE action_type=? AND idempotency_key=?
                """,
                (action_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                connection.rollback()
                item = self._action_item(existing)
                item.update({"reused": True, "reason": "idempotent"})
                return item
            active = connection.execute(
                """
                SELECT * FROM worker_action_requests
                WHERE action_type=? AND status IN ('queued','running')
                ORDER BY requested_at DESC LIMIT 1
                """,
                (action_type,),
            ).fetchone()
            if active is not None:
                connection.rollback()
                item = self._action_item(active)
                item.update({"reused": True, "reason": "already_running"})
                return item
            recent = connection.execute(
                """
                SELECT * FROM worker_action_requests
                WHERE action_type=? AND status='completed'
                ORDER BY completed_at DESC LIMIT 1
                """,
                (action_type,),
            ).fetchone()
            if recent is not None:
                cooldown_until = _parse(recent["cooldown_until"])
                if cooldown_until is not None and cooldown_until > observed:
                    connection.rollback()
                    item = self._action_item(recent)
                    item.update({"reused": True, "reason": "cooldown"})
                    return item
            request_id = f"act_{uuid.uuid4().hex}"
            connection.execute(
                """
                INSERT INTO worker_action_requests(
                    request_id,action_type,task_name,idempotency_key,status,
                    cooldown_seconds,requested_at,details_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                """,
                (
                    request_id,
                    action_type,
                    task_name,
                    idempotency_key,
                    "queued",
                    float(cooldown_seconds),
                    observed_text,
                    "{}",
                    observed_text,
                ),
            )
            row = connection.execute(
                "SELECT * FROM worker_action_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
            connection.commit()
        item = self._action_item(row)
        item.update({"reused": False, "reason": "queued"})
        return item

    def has_pending_actions(self, task_name: str) -> bool:
        if not self.path.is_file():
            return False
        with self._connect(read_only=True) as connection:
            row = connection.execute(
                """
                SELECT 1 FROM worker_action_requests
                WHERE task_name=? AND status='queued' LIMIT 1
                """,
                (task_name,),
            ).fetchone()
        return row is not None

    def claim_actions(
        self,
        owner_id: str,
        fencing_token: int,
        task_name: str,
        *,
        now: datetime | None = None,
    ) -> list[dict[str, Any]]:
        observed = _as_utc(now or utc_now())
        observed_text = _iso(observed)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_fence(connection, owner_id, fencing_token, observed)
            rows = connection.execute(
                """
                SELECT * FROM worker_action_requests
                WHERE task_name=? AND status='queued'
                ORDER BY requested_at,request_id LIMIT 32
                """,
                (task_name,),
            ).fetchall()
            if rows:
                request_ids = [row["request_id"] for row in rows]
                placeholders = ",".join("?" for _ in request_ids)
                connection.execute(
                    f"""
                    UPDATE worker_action_requests
                    SET status='running',started_at=?,owner_id=?,fencing_token=?,
                        error_code=NULL,updated_at=?
                    WHERE request_id IN ({placeholders}) AND status='queued'
                    """,
                    (
                        observed_text,
                        owner_id,
                        int(fencing_token),
                        observed_text,
                        *request_ids,
                    ),
                )
            connection.commit()
        claimed = [self._action_item(row) for row in rows]
        for item in claimed:
            item["status"] = "running"
            item["started_at"] = observed_text
        return claimed

    def finish_actions(
        self,
        owner_id: str,
        fencing_token: int,
        request_ids: list[str],
        *,
        succeeded: bool,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> int:
        if not request_ids:
            return 0
        if error_code is not None and not _ACTION_NAME.fullmatch(error_code):
            raise ValueError("worker action error code is invalid")
        observed = _as_utc(now or utc_now())
        observed_text = _iso(observed)
        body = _details_json(details)
        placeholders = ",".join("?" for _ in request_ids)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_fence(connection, owner_id, fencing_token, observed)
            cursor = connection.execute(
                f"""
                UPDATE worker_action_requests
                SET status=?,completed_at=?,
                    cooldown_until=CASE
                        WHEN ? THEN strftime(
                            '%Y-%m-%dT%H:%M:%fZ',
                            ?,
                            '+' || cooldown_seconds || ' seconds'
                        )
                        ELSE NULL
                    END,
                    error_code=?,details_json=?,updated_at=?
                WHERE request_id IN ({placeholders})
                  AND status='running' AND owner_id=? AND fencing_token=?
                """,
                (
                    "completed" if succeeded else "failed",
                    observed_text,
                    int(succeeded),
                    observed_text,
                    None if succeeded else error_code,
                    body,
                    observed_text,
                    *request_ids,
                    owner_id,
                    int(fencing_token),
                ),
            )
            connection.commit()
            return cursor.rowcount

    def action_requests(
        self,
        *,
        action_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        bounded_limit = max(1, min(int(limit), 200))
        query = "SELECT * FROM worker_action_requests"
        parameters: list[Any] = []
        if action_type is not None:
            if not _ACTION_NAME.fullmatch(action_type):
                raise ValueError("worker action type is invalid")
            query += " WHERE action_type=?"
            parameters.append(action_type)
        query += " ORDER BY requested_at DESC,request_id DESC LIMIT ?"
        parameters.append(bounded_limit)
        with self._connect(read_only=True) as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._action_item(row) for row in rows]

    def action_request(self, request_id: str) -> dict[str, Any] | None:
        """Return one manual action without exposing worker fencing fields."""

        if not re.fullmatch(r"act_[0-9a-f]{32}", request_id):
            raise ValueError("worker action request id is invalid")
        if not self.path.is_file():
            return None
        with self._connect(read_only=True) as connection:
            row = connection.execute(
                "SELECT * FROM worker_action_requests WHERE request_id=?",
                (request_id,),
            ).fetchone()
        return self._action_item(row) if row is not None else None

    def record_task(
        self,
        owner_id: str,
        fencing_token: int,
        task_name: str,
        *,
        enabled: bool,
        status: str,
        consecutive_failures: int = 0,
        last_started_at: datetime | None = None,
        last_completed_at: datetime | None = None,
        last_success_at: datetime | None = None,
        next_run_at: datetime | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        if not task_name or len(task_name) > 80:
            raise ValueError("worker task name is invalid")
        if error_code is not None and len(error_code) > 120:
            raise ValueError("worker task error code is too long")
        observed = _as_utc(now or utc_now())
        body = _details_json(details)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self._assert_fence(connection, owner_id, fencing_token, observed)
            connection.execute(
                """
                INSERT INTO worker_task_status(
                    task_name,enabled,status,consecutive_failures,last_started_at,
                    last_completed_at,last_success_at,next_run_at,error_code,
                    details_json,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(task_name) DO UPDATE SET
                    enabled=excluded.enabled,
                    status=excluded.status,
                    consecutive_failures=excluded.consecutive_failures,
                    last_started_at=COALESCE(excluded.last_started_at,worker_task_status.last_started_at),
                    last_completed_at=COALESCE(excluded.last_completed_at,worker_task_status.last_completed_at),
                    last_success_at=COALESCE(excluded.last_success_at,worker_task_status.last_success_at),
                    next_run_at=excluded.next_run_at,
                    error_code=excluded.error_code,
                    details_json=excluded.details_json,
                    updated_at=excluded.updated_at
                """,
                (
                    task_name,
                    int(enabled),
                    status,
                    max(0, int(consecutive_failures)),
                    _iso(last_started_at) if last_started_at else None,
                    _iso(last_completed_at) if last_completed_at else None,
                    _iso(last_success_at) if last_success_at else None,
                    _iso(next_run_at) if next_run_at else None,
                    error_code,
                    body,
                    _iso(observed),
                ),
            )
            connection.commit()

    def task_states(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        with self._connect(read_only=True) as connection:
            rows = connection.execute(
                "SELECT * FROM worker_task_status ORDER BY task_name"
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["enabled"] = bool(item["enabled"])
            try:
                item["details"] = json.loads(item.pop("details_json"))
            except (TypeError, json.JSONDecodeError):
                item["details"] = {}
                item.pop("details_json", None)
            output.append(item)
        return output

    def health(
        self,
        *,
        heartbeat_stale_seconds: float = 120.0,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        observed = _as_utc(now or utc_now())
        if not self.path.is_file():
            return {
                "healthy": False,
                "status": "not_started",
                "schema_version": SCHEMA_VERSION,
                "lock_live": False,
                "tasks": [],
                "actions": [],
            }
        try:
            with self._connect(read_only=True) as connection:
                schema = connection.execute(
                    "SELECT checksum FROM optix_worker_schema WHERE version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                lock = connection.execute(
                    "SELECT * FROM optix_worker_lock WHERE lock_name=?",
                    (LOCK_NAME,),
                ).fetchone()
        except sqlite3.Error:
            return {
                "healthy": False,
                "status": "unavailable",
                "schema_version": SCHEMA_VERSION,
                "lock_live": False,
                "tasks": [],
                "actions": [],
            }
        try:
            checksum_ok = bool(schema and schema["checksum"] == SCHEMA_CHECKSUM)
            heartbeat = _parse(lock["heartbeat_at"]) if lock is not None else None
            lock_live = self._live(lock, observed)
            tasks = self.task_states()
            actions = self.action_requests(limit=50)
        except (sqlite3.Error, TypeError, ValueError):
            return {
                "healthy": False,
                "status": "unavailable",
                "schema_version": SCHEMA_VERSION,
                "lock_live": False,
                "tasks": [],
                "actions": [],
            }
        heartbeat_age = (
            max(0.0, (observed - heartbeat).total_seconds())
            if heartbeat is not None
            else None
        )
        heartbeat_fresh = bool(
            heartbeat_age is not None and heartbeat_age <= heartbeat_stale_seconds
        )
        degraded = any(
            task["enabled"] and task["status"] in {"degraded", "failed", "interrupted"}
            for task in tasks
        )
        healthy = bool(checksum_ok and lock_live and heartbeat_fresh)
        return {
            "healthy": healthy,
            "status": "degraded" if healthy and degraded else "ok" if healthy else "unhealthy",
            "schema_version": SCHEMA_VERSION,
            "schema_checksum_valid": checksum_ok,
            "lock_live": lock_live,
            "heartbeat_at": _iso(heartbeat) if heartbeat else None,
            "heartbeat_age_seconds": heartbeat_age,
            "tasks": tasks,
            "actions": actions,
        }
