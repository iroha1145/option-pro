from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.services.ai_jobs.models import AIJobPublic


_SCHEMA_VERSION = "ai-jobs-v1"
_MAX_REQUEST_JSON_BYTES = 64 * 1024
_MAX_RESULT_JSON_BYTES = 1024 * 1024
_TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "insufficient_context",
    "budget_blocked",
}
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_job_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ai_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL CHECK(job_type IN (
        'earnings_impact','option_alerts','signal_analysis'
    )),
    request_hash TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK(json_valid(payload_json)),
    status TEXT NOT NULL CHECK(status IN (
        'pending','queued','in_progress','completed','failed','cancelled',
        'insufficient_context','budget_blocked'
    )),
    priority INTEGER NOT NULL DEFAULT 50,
    model TEXT NOT NULL,
    reasoning TEXT NOT NULL,
    execution_mode TEXT NOT NULL CHECK(execution_mode IN ('background','worker_sync')),
    prompt_version TEXT NOT NULL,
    schema_version TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL,
    openai_response_id TEXT,
    submission_started_at TEXT,
    submitted_at TEXT,
    last_polled_at TEXT,
    completed_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    poll_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    error_code TEXT,
    result_json TEXT CHECK(result_json IS NULL OR json_valid(result_json)),
    usage_input_tokens INTEGER,
    usage_cached_input_tokens INTEGER,
    usage_output_tokens INTEGER,
    usage_reasoning_tokens INTEGER,
    usage_total_tokens INTEGER,
    cancel_requested_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(job_type, request_hash, model, prompt_version, schema_version)
);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_due
ON ai_jobs(status, next_attempt_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_ticker
ON ai_jobs(job_type, json_extract(payload_json, '$.ticker'), completed_at DESC);
"""
_SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class AIJobRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA_SQL)
            row = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_SCHEMA_VERSION,),
            ).fetchone()
            if row and row["checksum"] != _SCHEMA_CHECKSUM:
                raise RuntimeError("ai_job_schema_checksum_mismatch")
            connection.execute(
                """
                INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at)
                VALUES(?,?,?)
                """,
                (_SCHEMA_VERSION, _SCHEMA_CHECKSUM, _iso()),
            )
            connection.commit()

    @staticmethod
    def _canonical_json(
        payload: dict[str, Any],
        *,
        max_bytes: int,
        error_code: str,
    ) -> str:
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError(error_code)
        return raw

    @classmethod
    def _canonical_payload(cls, payload: dict[str, Any]) -> str:
        return cls._canonical_json(
            payload,
            max_bytes=_MAX_REQUEST_JSON_BYTES,
            error_code="ai_job_payload_too_large",
        )

    @classmethod
    def _canonical_result(cls, result: dict[str, Any]) -> str:
        return cls._canonical_json(
            result,
            max_bytes=_MAX_RESULT_JSON_BYTES,
            error_code="ai_job_result_too_large",
        )

    @staticmethod
    def _request_hash(
        job_type: str,
        payload_json: str,
        model: str,
        reasoning: str,
        execution_mode: str,
        prompt_version: str,
        schema_version: str,
    ) -> str:
        envelope = "\n".join(
            [
                job_type,
                payload_json,
                model,
                reasoning,
                execution_mode,
                prompt_version,
                schema_version,
            ]
        )
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

    def create_job(
        self,
        *,
        job_type: str,
        payload: dict[str, Any],
        model: str,
        reasoning: str,
        execution_mode: str,
        prompt_version: str,
        schema_version: str,
        schema_sha256: str,
        max_queued: int,
        priority: int = 50,
        force_retry: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        self.initialize()
        payload_json = self._canonical_payload(payload)
        request_hash = self._request_hash(
            job_type,
            payload_json,
            model,
            reasoning,
            execution_mode,
            prompt_version,
            schema_version,
        )
        legacy_request_hash = self._request_hash_legacy(
            job_type,
            payload_json,
            model,
            reasoning,
            prompt_version,
            schema_version,
        )
        now = _iso()
        job_id = "aij_" + uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT * FROM ai_jobs
                WHERE job_type=? AND request_hash IN (?,?) AND model=?
                  AND reasoning=? AND execution_mode=?
                  AND prompt_version=? AND schema_version=?
                ORDER BY CASE WHEN request_hash=? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (
                    job_type,
                    request_hash,
                    legacy_request_hash,
                    model,
                    reasoning,
                    execution_mode,
                    prompt_version,
                    schema_version,
                    request_hash,
                ),
            ).fetchone()
            if existing:
                if existing["request_hash"] != request_hash:
                    connection.execute(
                        "UPDATE ai_jobs SET request_hash=?,updated_at=? WHERE job_id=?",
                        (request_hash, now, existing["job_id"]),
                    )
                    existing = connection.execute(
                        "SELECT * FROM ai_jobs WHERE job_id=?",
                        (existing["job_id"],),
                    ).fetchone()
                if (
                    force_retry
                    and existing["status"] in {"failed", "cancelled"}
                    and existing["error_code"] != "submission_outcome_unknown"
                ):
                    connection.execute(
                        """
                        UPDATE ai_jobs
                        SET status='pending', openai_response_id=NULL,
                            submission_started_at=NULL, submitted_at=NULL,
                            last_polled_at=NULL, completed_at=NULL,
                            attempt_count=0, poll_count=0, next_attempt_at=?,
                            error_code=NULL, result_json=NULL,
                            usage_input_tokens=NULL,
                            usage_cached_input_tokens=NULL,
                            usage_output_tokens=NULL,
                            usage_reasoning_tokens=NULL,
                            usage_total_tokens=NULL,
                            cancel_requested_at=NULL,
                            lease_owner=NULL, lease_expires_at=NULL,
                            updated_at=?
                        WHERE job_id=?
                        """,
                        (now, now, existing["job_id"]),
                    )
                    retried = connection.execute(
                        "SELECT * FROM ai_jobs WHERE job_id=?",
                        (existing["job_id"],),
                    ).fetchone()
                    connection.commit()
                    return dict(retried), True
                connection.commit()
                return dict(existing), False
            active = connection.execute(
                """
                SELECT COUNT(*) AS count FROM ai_jobs
                WHERE status IN ('pending','queued','in_progress')
                """
            ).fetchone()["count"]
            if active >= max_queued:
                connection.rollback()
                raise RuntimeError("ai_job_queue_full")
            connection.execute(
                """
                INSERT INTO ai_jobs(
                    job_id,job_type,request_hash,payload_json,status,priority,
                    model,reasoning,execution_mode,prompt_version,schema_version,
                    schema_sha256,next_attempt_at,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    job_type,
                    request_hash,
                    payload_json,
                    "pending",
                    max(0, min(int(priority), 100)),
                    model,
                    reasoning,
                    execution_mode,
                    prompt_version,
                    schema_version,
                    schema_sha256,
                    now,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            connection.commit()
            return dict(row), True

    @staticmethod
    def _request_hash_legacy(
        job_type: str,
        payload_json: str,
        model: str,
        reasoning: str,
        prompt_version: str,
        schema_version: str,
    ) -> str:
        envelope = "\n".join(
            [
                job_type,
                payload_json,
                model,
                reasoning,
                prompt_version,
                schema_version,
            ]
        )
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def latest_completed(self, job_type: str, ticker: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM ai_jobs
                WHERE job_type=? AND status='completed'
                  AND upper(json_extract(payload_json, '$.ticker'))=?
                ORDER BY completed_at DESC, created_at DESC
                LIMIT 1
                """,
                (job_type, ticker.upper()),
            ).fetchone()
            return dict(row) if row else None

    def claim_due(self, owner: str, lease_seconds: int) -> dict[str, Any] | None:
        self.initialize()
        now_dt = _utcnow()
        now = _iso(now_dt)
        lease_expires = _iso(now_dt + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT * FROM ai_jobs
                WHERE status IN ('pending','queued','in_progress')
                  AND (next_attempt_at IS NULL OR next_attempt_at<=?)
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                ORDER BY
                  CASE WHEN cancel_requested_at IS NOT NULL THEN 0 ELSE 1 END,
                  priority DESC,
                  created_at
                LIMIT 1
                """,
                (now, now),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET lease_owner=?, lease_expires_at=?, updated_at=?
                WHERE job_id=?
                  AND (lease_expires_at IS NULL OR lease_expires_at<=?)
                """,
                (owner, lease_expires, now, row["job_id"], now),
            ).rowcount
            if updated != 1:
                connection.rollback()
                return None
            claimed = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?",
                (row["job_id"],),
            ).fetchone()
            connection.commit()
            return dict(claimed)

    def mark_submission_started(self, job_id: str, owner: str) -> None:
        now = _iso()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET submission_started_at=COALESCE(submission_started_at,?),
                    submitted_at=COALESCE(submitted_at,?),
                    status='in_progress',
                    attempt_count=attempt_count+1,
                    updated_at=?
                WHERE job_id=? AND lease_owner=? AND openai_response_id IS NULL
                  AND status='pending' AND cancel_requested_at IS NULL
                """,
                (now, now, now, job_id, owner),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_not_submittable")

    def link_background_response(
        self,
        job_id: str,
        owner: str,
        response_id: str,
    ) -> None:
        """Persist the upstream identity before interpreting a terminal result."""

        now = _iso()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET openai_response_id=?, updated_at=?
                WHERE job_id=? AND lease_owner=?
                  AND openai_response_id IS NULL
                  AND status='in_progress'
                """,
                (response_id, now, job_id, owner),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_response_link_rejected")

    def renew_lease(self, job_id: str, owner: str, lease_seconds: int) -> bool:
        now_dt = _utcnow()
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET lease_expires_at=?, updated_at=?
                WHERE job_id=? AND lease_owner=?
                  AND status IN ('pending','queued','in_progress')
                """,
                (
                    _iso(now_dt + timedelta(seconds=lease_seconds)),
                    _iso(now_dt),
                    job_id,
                    owner,
                ),
            ).rowcount
            connection.commit()
            return updated == 1

    def record_background_response(
        self,
        job_id: str,
        owner: str,
        response_id: str,
        status: str,
        *,
        delay_seconds: float,
        error_code: str | None = None,
    ) -> None:
        now_dt = _utcnow()
        now = _iso(now_dt)
        public_status = "queued" if status == "queued" else "in_progress"
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET openai_response_id=?, status=?, last_polled_at=?,
                    poll_count=poll_count+1, next_attempt_at=?,
                    error_code=?,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE job_id=? AND lease_owner=?
                """,
                (
                    response_id,
                    public_status,
                    now,
                    _iso(now_dt + timedelta(seconds=max(1.0, delay_seconds))),
                    error_code[:120] if error_code else None,
                    now,
                    job_id,
                    owner,
                ),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_lease_lost")

    def complete(
        self,
        job_id: str,
        owner: str,
        result: dict[str, Any],
        usage: dict[str, int | None],
    ) -> None:
        now = _iso()
        result_json = self._canonical_result(result)
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='completed', result_json=?, completed_at=?,
                    usage_input_tokens=?, usage_cached_input_tokens=?,
                    usage_output_tokens=?, usage_reasoning_tokens=?,
                    usage_total_tokens=?, error_code=NULL,
                    next_attempt_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=?
                WHERE job_id=? AND lease_owner=?
                """,
                (
                    result_json,
                    now,
                    usage.get("input_tokens"),
                    usage.get("cached_input_tokens"),
                    usage.get("output_tokens"),
                    usage.get("reasoning_tokens"),
                    usage.get("total_tokens"),
                    now,
                    job_id,
                    owner,
                ),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_completion_rejected")

    def defer(
        self,
        job_id: str,
        owner: str,
        *,
        delay_seconds: float,
        error_code: str | None = None,
    ) -> None:
        now_dt = _utcnow()
        now = _iso(now_dt)
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET next_attempt_at=?, last_polled_at=?,
                    poll_count=poll_count+1, error_code=?,
                    lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                WHERE job_id=? AND lease_owner=?
                """,
                (
                    _iso(now_dt + timedelta(seconds=max(1.0, delay_seconds))),
                    now,
                    error_code[:120] if error_code else None,
                    now,
                    job_id,
                    owner,
                ),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_lease_lost")

    def fail(self, job_id: str, owner: str, error_code: str) -> None:
        now = _iso()
        safe_code = error_code[:120]
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='failed', error_code=?, completed_at=?,
                    next_attempt_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=?
                WHERE job_id=? AND lease_owner=?
                """,
                (safe_code, now, now, job_id, owner),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_lease_lost")

    def mark_cancelled(self, job_id: str, owner: str | None = None) -> None:
        now = _iso()
        sql = """
            UPDATE ai_jobs
            SET status='cancelled', completed_at=?, next_attempt_at=NULL,
                lease_owner=NULL, lease_expires_at=NULL, updated_at=?
            WHERE job_id=? AND status IN ('pending','queued','in_progress')
        """
        params: list[Any] = [now, now, job_id]
        if owner is not None:
            sql += " AND lease_owner=?"
            params.append(owner)
        with self._connect() as connection:
            connection.execute(sql, tuple(params))
            connection.commit()

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            if row["status"] in _TERMINAL:
                connection.commit()
                return dict(row)
            if row["status"] == "pending" and not row["openai_response_id"]:
                connection.execute(
                    """
                    UPDATE ai_jobs SET status='cancelled', cancel_requested_at=?,
                        completed_at=?, next_attempt_at=NULL,
                        lease_owner=NULL, lease_expires_at=NULL, updated_at=?
                    WHERE job_id=?
                    """,
                    (now, now, now, job_id),
                )
            else:
                connection.execute(
                    """
                    UPDATE ai_jobs SET cancel_requested_at=?,
                        next_attempt_at=?, updated_at=?
                    WHERE job_id=?
                    """,
                    (now, now, now, job_id),
                )
            updated = connection.execute(
                "SELECT * FROM ai_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            connection.commit()
            return dict(updated)

    @staticmethod
    def public(row: dict[str, Any], *, cached: bool = False) -> dict[str, Any]:
        result = json.loads(row["result_json"]) if row.get("result_json") else None
        public = AIJobPublic(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            model=row["model"],
            reasoning=row["reasoning"],
            submitted_at=row.get("submitted_at"),
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
            error_code=row.get("error_code"),
            retry_after=None,
            result=result,
            cached=cached,
            cancellable=row["status"] in {"pending", "queued", "in_progress"},
        )
        return public.model_dump(mode="json")

    def health(self) -> dict[str, Any]:
        try:
            self.initialize()
            with self._connect() as connection:
                connection.execute("SELECT 1").fetchone()
                pending = connection.execute(
                    """
                    SELECT COUNT(*) FROM ai_jobs
                    WHERE status IN ('pending','queued','in_progress')
                    """
                ).fetchone()[0]
            return {
                "healthy": True,
                "status": "ready",
                "schema_version": _SCHEMA_VERSION,
                "pending": pending,
            }
        except Exception:
            return {
                "healthy": False,
                "status": "database_unavailable",
                "schema_version": _SCHEMA_VERSION,
                "pending": None,
            }
