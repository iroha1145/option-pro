from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from app.services.ai_jobs.models import AIJobPublic, validate_result


_SCHEMA_VERSION = "ai-jobs-v3"
_SOURCE_SCHEMA_VERSION = "ai-job-sources-v1"
_IDENTITY_MIGRATION_VERSION = "ai-job-identities-v2"
_IDENTITY_MIGRATION_CHECKSUM = hashlib.sha256(
    b"restore-source-aware-hashes-and-seal-unsubmitted-duplicates-v2"
).hexdigest()
_DUPLICATE_MIGRATION_ERROR = "duplicate_request_migrated"
_MAX_REQUEST_JSON_BYTES = 64 * 1024
_MAX_RESULT_JSON_BYTES = 1024 * 1024
_TERMINAL = {
    "completed",
    "failed",
    "cancelled",
    "insufficient_context",
    "budget_blocked",
}
_SCHEMA_REGISTRY_SQL = """
CREATE TABLE IF NOT EXISTS ai_job_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
"""
_AI_JOBS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_jobs (
    job_id TEXT PRIMARY KEY,
    job_type TEXT NOT NULL CHECK(job_type IN (
        'earnings_impact','option_alerts','signal_analysis',
        'news_impact','market_focus'
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
    execution_mode TEXT NOT NULL CHECK(execution_mode='background'),
    legacy_execution_mode TEXT,
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
    budget_charge_microusd INTEGER NOT NULL DEFAULT 0
        CHECK(budget_charge_microusd >= 0),
    cancel_requested_at TEXT,
    lease_owner TEXT,
    lease_expires_at TEXT,
    retry_of_job_id TEXT REFERENCES ai_jobs(job_id) ON DELETE SET NULL,
    execution_number INTEGER NOT NULL DEFAULT 1 CHECK(execution_number >= 1),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(
        job_type,request_hash,model,reasoning,prompt_version,schema_version,
        execution_number
    )
);
"""
_AI_JOBS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ai_jobs_due
ON ai_jobs(status, next_attempt_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_ticker
ON ai_jobs(job_type, json_extract(payload_json, '$.ticker'), completed_at DESC);
"""
_AI_JOBS_INDEX_STATEMENTS = (
    """CREATE INDEX IF NOT EXISTS idx_ai_jobs_due
       ON ai_jobs(status,next_attempt_at,priority DESC,created_at)""",
    """CREATE INDEX IF NOT EXISTS idx_ai_jobs_ticker
       ON ai_jobs(job_type,json_extract(payload_json,'$.ticker'),completed_at DESC)""",
)
_SCHEMA_SQL = _SCHEMA_REGISTRY_SQL + _AI_JOBS_TABLE_SQL + _AI_JOBS_INDEX_SQL
_SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()
_AI_JOB_SOURCES_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_job_sources (
    job_id TEXT PRIMARY KEY,
    submission_source TEXT NOT NULL
        CHECK(submission_source IN ('manual','scheduled')),
    created_at TEXT NOT NULL
);
"""
_SOURCE_SCHEMA_CHECKSUM = hashlib.sha256(
    _AI_JOB_SOURCES_TABLE_SQL.encode("utf-8")
).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _task_budget_reservation_microusd(job_type: str) -> int:
    from app.services.ai_jobs.runtime import budget_reservation_microusd

    return budget_reservation_microusd(job_type)


def _task_token_reservation(job_type: str) -> int:
    from app.services.ai_jobs.runtime import token_reservation

    return token_reservation(job_type)


def _minimum_task_token_reservation() -> int:
    from app.services.ai_jobs.runtime import minimum_token_reservation

    return minimum_token_reservation()


def _settled_budget_charge_microusd(
    job_type: str,
    usage: dict[str, int | None],
    *,
    fallback_microusd: int,
) -> int:
    from app.services.ai_jobs.runtime import settled_usage_cost_microusd

    return settled_usage_cost_microusd(
        job_type,
        usage,
        fallback_microusd=fallback_microusd,
    )


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
            connection.execute(_SCHEMA_REGISTRY_SQL)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            existing_table = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='ai_jobs'"
            ).fetchone()
            if existing_table is None:
                connection.execute(_AI_JOBS_TABLE_SQL)
                self._ensure_indexes(connection)
            else:
                table_sql = str(existing_table["sql"] or "")
                if (
                    "'news_impact'" not in table_sql
                    or "execution_number" not in table_sql
                ):
                    self._migrate_v2(connection)
                else:
                    columns = {
                        str(row["name"])
                        for row in connection.execute(
                            "PRAGMA table_info(ai_jobs)"
                        ).fetchall()
                    }
                    if "budget_charge_microusd" not in columns:
                        connection.execute(
                            """ALTER TABLE ai_jobs
                               ADD COLUMN budget_charge_microusd INTEGER
                               NOT NULL DEFAULT 0
                               CHECK(budget_charge_microusd >= 0)"""
                        )
                    self._ensure_indexes(connection)
            connection.execute(_AI_JOB_SOURCES_TABLE_SQL)
            source_schema = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_SOURCE_SCHEMA_VERSION,),
            ).fetchone()
            if (
                source_schema is not None
                and source_schema["checksum"] != _SOURCE_SCHEMA_CHECKSUM
            ):
                raise RuntimeError("ai_job_source_schema_checksum_mismatch")
            connection.execute(
                """INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at)
                   VALUES(?,?,?)""",
                (_SOURCE_SCHEMA_VERSION, _SOURCE_SCHEMA_CHECKSUM, _iso()),
            )
            identity_migration = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_IDENTITY_MIGRATION_VERSION,),
            ).fetchone()
            if (
                identity_migration is not None
                and identity_migration["checksum"]
                != _IDENTITY_MIGRATION_CHECKSUM
            ):
                raise RuntimeError("ai_job_identity_migration_checksum_mismatch")
            if identity_migration is None:
                self._migrate_cross_source_identities(connection)
                connection.execute(
                    """INSERT INTO ai_job_schema(version,checksum,applied_at)
                       VALUES(?,?,?)""",
                    (
                        _IDENTITY_MIGRATION_VERSION,
                        _IDENTITY_MIGRATION_CHECKSUM,
                        _iso(),
                    ),
                )
            # Rows from before source-aware identities cannot be classified.
            # Keep those conservative: only the manual switch may release them.
            connection.execute(
                """INSERT OR IGNORE INTO ai_job_sources(
                       job_id,submission_source,created_at
                   )
                   SELECT job_id,'manual',created_at FROM ai_jobs"""
            )
            missing_charges = connection.execute(
                """SELECT job_id,job_type,error_code,
                          budget_charge_microusd,
                          usage_input_tokens,usage_cached_input_tokens,
                          usage_output_tokens,usage_reasoning_tokens,
                          usage_total_tokens
                   FROM ai_jobs
                   WHERE submission_started_at IS NOT NULL
                     AND budget_charge_microusd=0"""
            ).fetchall()
            for missing in missing_charges:
                reservation = _task_budget_reservation_microusd(
                    str(missing["job_type"])
                )
                usage = {
                    "input_tokens": missing["usage_input_tokens"],
                    "cached_input_tokens": missing["usage_cached_input_tokens"],
                    "output_tokens": missing["usage_output_tokens"],
                    "reasoning_tokens": missing["usage_reasoning_tokens"],
                    "total_tokens": missing["usage_total_tokens"],
                }
                has_terminal_usage = (
                    missing["usage_input_tokens"] is not None
                    and missing["usage_output_tokens"] is not None
                    and missing["error_code"] != "submission_outcome_unknown"
                )
                charge = (
                    _settled_budget_charge_microusd(
                        str(missing["job_type"]),
                        usage,
                        fallback_microusd=reservation,
                    )
                    if has_terminal_usage
                    else reservation
                )
                connection.execute(
                    """UPDATE ai_jobs SET budget_charge_microusd=?
                       WHERE job_id=? AND budget_charge_microusd=0""",
                    (charge, missing["job_id"]),
                )
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
    def _ensure_indexes(connection: sqlite3.Connection) -> None:
        for statement in _AI_JOBS_INDEX_STATEMENTS:
            connection.execute(statement)

    @staticmethod
    def _migrate_v2(connection: sqlite3.Connection) -> None:
        """Add the two local task types and append-only retry lineage."""

        connection.execute("DROP INDEX IF EXISTS idx_ai_jobs_due")
        connection.execute("DROP INDEX IF EXISTS idx_ai_jobs_ticker")
        connection.execute("ALTER TABLE ai_jobs RENAME TO ai_jobs_v1")
        connection.execute(_AI_JOBS_TABLE_SQL)
        connection.execute(
            """
            INSERT INTO ai_jobs(
                job_id,job_type,request_hash,payload_json,status,priority,
                model,reasoning,execution_mode,legacy_execution_mode,
                prompt_version,schema_version,
                schema_sha256,openai_response_id,submission_started_at,
                submitted_at,last_polled_at,completed_at,attempt_count,
                poll_count,next_attempt_at,error_code,result_json,
                usage_input_tokens,usage_cached_input_tokens,
                usage_output_tokens,usage_reasoning_tokens,usage_total_tokens,
                cancel_requested_at,lease_owner,lease_expires_at,
                retry_of_job_id,execution_number,created_at,updated_at
            )
            SELECT
                job_id,job_type,request_hash,payload_json,
                CASE
                    WHEN execution_mode='background'
                      OR status NOT IN ('pending','queued','in_progress')
                    THEN status ELSE 'failed'
                END,
                priority,model,reasoning,'background',
                CASE WHEN execution_mode='background' THEN NULL ELSE execution_mode END,
                prompt_version,schema_version,
                schema_sha256,openai_response_id,submission_started_at,
                submitted_at,last_polled_at,
                CASE
                    WHEN execution_mode<>'background'
                      AND status IN ('pending','queued','in_progress')
                    THEN COALESCE(completed_at,updated_at) ELSE completed_at
                END,
                attempt_count,poll_count,
                CASE WHEN execution_mode='background' THEN next_attempt_at ELSE NULL END,
                CASE
                    WHEN execution_mode<>'background'
                      AND status IN ('pending','queued','in_progress')
                    THEN 'legacy_execution_mode_disabled' ELSE error_code
                END,
                result_json,
                usage_input_tokens,usage_cached_input_tokens,
                usage_output_tokens,usage_reasoning_tokens,usage_total_tokens,
                cancel_requested_at,
                CASE WHEN execution_mode='background' THEN lease_owner ELSE NULL END,
                CASE WHEN execution_mode='background' THEN lease_expires_at ELSE NULL END,
                NULL,1,created_at,updated_at
            FROM ai_jobs_v1
            """
        )
        connection.execute("DROP TABLE ai_jobs_v1")
        AIJobRepository._ensure_indexes(connection)

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
        schema_sha256: str,
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
                schema_sha256,
            ]
        )
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

    @classmethod
    def _identity_hashes_for_row(
        cls,
        row: dict[str, Any],
    ) -> tuple[str, str, str, str, str]:
        job_type = str(row["job_type"])
        payload_json = str(row["payload_json"])
        model = str(row["model"])
        reasoning = str(row["reasoning"])
        execution_mode = str(row["execution_mode"])
        prompt_version = str(row["prompt_version"])
        schema_version = str(row["schema_version"])
        schema_sha256 = str(row["schema_sha256"])
        current = cls._request_hash(
            job_type,
            payload_json,
            model,
            reasoning,
            execution_mode,
            prompt_version,
            schema_version,
            schema_sha256,
        )
        manual = cls._request_hash_source_legacy(
            job_type,
            payload_json,
            "manual",
            model,
            reasoning,
            execution_mode,
            prompt_version,
            schema_version,
            schema_sha256,
        )
        scheduled = cls._request_hash_source_legacy(
            job_type,
            payload_json,
            "scheduled",
            model,
            reasoning,
            execution_mode,
            prompt_version,
            schema_version,
            schema_sha256,
        )
        execution_legacy = cls._request_hash_execution_legacy(
            job_type,
            payload_json,
            model,
            reasoning,
            execution_mode,
            prompt_version,
            schema_version,
        )
        legacy = cls._request_hash_legacy(
            job_type,
            payload_json,
            model,
            reasoning,
            prompt_version,
            schema_version,
        )
        return current, manual, scheduled, execution_legacy, legacy

    @classmethod
    def _migrate_cross_source_identities(
        cls,
        connection: sqlite3.Connection,
    ) -> None:
        """Restore exact old sources and seal only unpaid duplicate queue rows."""

        rows = [
            dict(row)
            for row in connection.execute(
                """SELECT j.*,s.submission_source
                   FROM ai_jobs AS j
                   LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id"""
            ).fetchall()
        ]
        groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
        for row in rows:
            current, manual, scheduled, execution_legacy, legacy = (
                cls._identity_hashes_for_row(row)
            )
            stored_hash = str(row["request_hash"])
            existing_source = row.get("submission_source")
            source = (
                "scheduled"
                if stored_hash == scheduled
                else "manual"
                if stored_hash == manual
                else existing_source
                if existing_source in {"manual", "scheduled"}
                else "manual"
            )
            connection.execute(
                """INSERT INTO ai_job_sources(
                       job_id,submission_source,created_at
                   ) VALUES(?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET
                       submission_source=excluded.submission_source""",
                (row["job_id"], source, row["created_at"]),
            )
            if stored_hash not in {
                current,
                manual,
                scheduled,
                execution_legacy,
                legacy,
            }:
                continue
            key = (
                str(row["job_type"]),
                current,
                str(row["model"]),
                str(row["reasoning"]),
                str(row["execution_mode"]),
                str(row["prompt_version"]),
                str(row["schema_version"]),
                str(row["schema_sha256"]),
            )
            groups.setdefault(key, []).append(row)

        now = _iso()
        active_statuses = {"pending", "queued", "in_progress"}
        for identity_rows in groups.values():
            if len(identity_rows) < 2:
                continue
            max_execution = max(int(row["execution_number"]) for row in identity_rows)
            group_has_paid_or_settled_work = any(
                row.get("submission_started_at") is not None
                or row.get("openai_response_id") is not None
                or row.get("error_code") == "submission_outcome_unknown"
                or row["status"] in {"completed", "insufficient_context"}
                for row in identity_rows
            )
            by_execution: dict[int, list[dict[str, Any]]] = {}
            for row in identity_rows:
                by_execution.setdefault(int(row["execution_number"]), []).append(row)
            for execution_number, execution_rows in by_execution.items():
                unpaid_active = [
                    row
                    for row in execution_rows
                    if row["status"] in active_statuses
                    and row.get("submission_started_at") is None
                    and row.get("openai_response_id") is None
                ]
                if not unpaid_active:
                    continue
                keep_job_id: str | None = None
                if (
                    not group_has_paid_or_settled_work
                    and execution_number == max_execution
                ):
                    keep_job_id = str(
                        min(
                            unpaid_active,
                            key=lambda row: (str(row["created_at"]), str(row["job_id"])),
                        )["job_id"]
                    )
                for duplicate in unpaid_active:
                    if str(duplicate["job_id"]) == keep_job_id:
                        continue
                    connection.execute(
                        """UPDATE ai_jobs
                           SET status='cancelled',
                               error_code=?,completed_at=COALESCE(completed_at,?),
                               next_attempt_at=NULL,lease_owner=NULL,
                               lease_expires_at=NULL,updated_at=?
                           WHERE job_id=?
                             AND status IN ('pending','queued','in_progress')
                             AND submission_started_at IS NULL
                             AND openai_response_id IS NULL""",
                        (
                            _DUPLICATE_MIGRATION_ERROR,
                            now,
                            now,
                            duplicate["job_id"],
                        ),
                    )

    @staticmethod
    def _select_identity_row(
        rows: list[dict[str, Any]],
        *,
        current_hash: str,
    ) -> dict[str, Any]:
        usable = [
            row
            for row in rows
            if row.get("error_code") != _DUPLICATE_MIGRATION_ERROR
        ]
        if not usable:
            usable = rows
        max_execution = max(int(row["execution_number"]) for row in usable)
        latest = [
            row for row in usable if int(row["execution_number"]) == max_execution
        ]
        active = {"pending", "queued", "in_progress"}
        buckets = (
            [row for row in latest if row.get("error_code") == "submission_outcome_unknown"],
            [
                row
                for row in latest
                if row["status"] in active
                and (
                    row.get("submission_started_at") is not None
                    or row.get("openai_response_id") is not None
                )
            ],
            [row for row in latest if row["status"] == "completed"],
            [row for row in latest if row["status"] in active],
            [row for row in latest if row["status"] == "insufficient_context"],
            latest,
        )
        selected = next(bucket for bucket in buckets if bucket)
        if all(row["status"] in active for row in selected):
            return min(
                selected,
                key=lambda row: (
                    str(row["created_at"]),
                    0 if row["request_hash"] == current_hash else 1,
                    str(row["job_id"]),
                ),
            )
        return max(
            selected,
            key=lambda row: (
                str(row.get("completed_at") or row.get("updated_at") or row["created_at"]),
                1 if row["request_hash"] == current_hash else 0,
                str(row["job_id"]),
            ),
        )

    @classmethod
    def _completed_result_state(cls, row: dict[str, Any]) -> str:
        if row.get("status") != "completed":
            return "not_completed"
        raw_result = row.get("result_json")
        if not isinstance(raw_result, str) or not raw_result:
            return "unknown"
        try:
            public = cls.public(row)
        except (KeyError, TypeError, ValueError):
            return "unknown"
        if public.get("result") is not None:
            return "valid"
        if public.get("error_code") == "legacy_output_hidden":
            return "invalid"
        return "unknown"

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
        submission_source: str = "manual",
        priority: int = 50,
        force_retry: bool = False,
    ) -> tuple[dict[str, Any], bool]:
        if execution_mode != "background":
            raise ValueError("background_execution_required")
        if submission_source not in {"manual", "scheduled"}:
            raise ValueError("invalid_submission_source")
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
            schema_sha256,
        )
        source_legacy_hashes = tuple(
            self._request_hash_source_legacy(
                job_type,
                payload_json,
                source,
                model,
                reasoning,
                execution_mode,
                prompt_version,
                schema_version,
                schema_sha256,
            )
            for source in ("manual", "scheduled")
        )
        execution_legacy_hash = self._request_hash_execution_legacy(
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
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            matches = [
                dict(row)
                for row in connection.execute(
                """
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type=?
                  AND j.request_hash IN (?,?,?,?,?)
                  AND j.model=?
                  AND j.reasoning=? AND j.execution_mode=?
                  AND j.prompt_version=? AND j.schema_version=?
                  AND j.schema_sha256=?
                """,
                (
                    job_type,
                    request_hash,
                    *source_legacy_hashes,
                    execution_legacy_hash,
                    legacy_request_hash,
                    model,
                    reasoning,
                    execution_mode,
                    prompt_version,
                    schema_version,
                    schema_sha256,
                ),
            ).fetchall()
            ]
            if matches:
                global_max_execution = max(
                    int(row["execution_number"]) for row in matches
                )
                existing = self._select_identity_row(
                    matches,
                    current_hash=request_hash,
                )
                if existing["request_hash"] != request_hash:
                    try:
                        connection.execute(
                            """UPDATE ai_jobs SET request_hash=?,updated_at=?
                               WHERE job_id=?""",
                            (request_hash, now, existing["job_id"]),
                        )
                    except sqlite3.IntegrityError:
                        # Another preserved row already owns the canonical hash
                        # for this execution. Selection still considers both.
                        pass
                    else:
                        refreshed = connection.execute(
                            """SELECT j.*,s.submission_source FROM ai_jobs AS j
                               JOIN ai_job_sources AS s ON s.job_id=j.job_id
                               WHERE j.job_id=?""",
                            (existing["job_id"],),
                        ).fetchone()
                        if refreshed is not None:
                            existing = dict(refreshed)
                meaningful_matches = [
                    row
                    for row in matches
                    if row.get("error_code") != _DUPLICATE_MIGRATION_ERROR
                ]
                meaningful_latest: list[dict[str, Any]] = []
                if meaningful_matches:
                    meaningful_max_execution = max(
                        int(row["execution_number"])
                        for row in meaningful_matches
                    )
                    meaningful_latest = [
                        row
                        for row in meaningful_matches
                        if int(row["execution_number"])
                        == meaningful_max_execution
                    ]
                unknown_exists = any(
                    row.get("error_code") == "submission_outcome_unknown"
                    for row in matches
                )
                completed_result_states = {
                    str(row["job_id"]): self._completed_result_state(row)
                    for row in matches
                    if row["status"] == "completed"
                }
                settled_result_exists = any(
                    row["status"] == "insufficient_context"
                    or (
                        row["status"] == "completed"
                        and completed_result_states.get(
                            str(row["job_id"]),
                            "unknown",
                        )
                        != "invalid"
                    )
                    for row in matches
                )
                unresolved_submission_exists = any(
                    row["status"] in {"pending", "queued", "in_progress"}
                    and (
                        row.get("submission_started_at") is not None
                        or row.get("openai_response_id") is not None
                    )
                    for row in matches
                )
                retryable_latest = bool(meaningful_latest) and all(
                    (
                        row["status"]
                        in {"failed", "cancelled", "budget_blocked"}
                        and row.get("error_code")
                        not in {
                            "submission_outcome_unknown",
                            _DUPLICATE_MIGRATION_ERROR,
                        }
                    )
                    or (
                        row["status"] == "completed"
                        and completed_result_states.get(
                            str(row["job_id"]),
                            "unknown",
                        )
                        == "invalid"
                    )
                    for row in meaningful_latest
                )
                if (
                    force_retry
                    and not unknown_exists
                    and not settled_result_exists
                    and not unresolved_submission_exists
                    and retryable_latest
                ):
                    active = int(
                        connection.execute(
                            """SELECT COUNT(*) FROM ai_jobs
                               WHERE status IN ('pending','queued','in_progress')"""
                        ).fetchone()[0]
                    )
                    if active >= max_queued:
                        connection.rollback()
                        raise RuntimeError("ai_job_queue_full")
                    retry_parent = self._select_identity_row(
                        meaningful_latest,
                        current_hash=request_hash,
                    )
                    row = self._insert_job(
                        connection,
                        job_type=job_type,
                        request_hash=request_hash,
                        payload_json=payload_json,
                        model=model,
                        reasoning=reasoning,
                        execution_mode=execution_mode,
                        prompt_version=prompt_version,
                        schema_version=schema_version,
                        schema_sha256=schema_sha256,
                        submission_source=submission_source,
                        priority=priority,
                        now=now,
                        retry_of_job_id=str(retry_parent["job_id"]),
                        execution_number=global_max_execution + 1,
                    )
                    connection.commit()
                    return row, True
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
            row = self._insert_job(
                connection,
                job_type=job_type,
                request_hash=request_hash,
                payload_json=payload_json,
                model=model,
                reasoning=reasoning,
                execution_mode=execution_mode,
                prompt_version=prompt_version,
                schema_version=schema_version,
                schema_sha256=schema_sha256,
                submission_source=submission_source,
                priority=priority,
                now=now,
                retry_of_job_id=None,
                execution_number=1,
            )
            connection.commit()
            return row, True

    @staticmethod
    def _request_hash_source_legacy(
        job_type: str,
        payload_json: str,
        submission_source: str,
        model: str,
        reasoning: str,
        execution_mode: str,
        prompt_version: str,
        schema_version: str,
        schema_sha256: str,
    ) -> str:
        """Return the former source-aware identity for lazy compatibility."""

        envelope = "\n".join(
            [
                job_type,
                payload_json,
                submission_source,
                model,
                reasoning,
                execution_mode,
                prompt_version,
                schema_version,
                schema_sha256,
            ]
        )
        return hashlib.sha256(envelope.encode("utf-8")).hexdigest()

    @staticmethod
    def _insert_job(
        connection: sqlite3.Connection,
        *,
        job_type: str,
        request_hash: str,
        payload_json: str,
        model: str,
        reasoning: str,
        execution_mode: str,
        prompt_version: str,
        schema_version: str,
        schema_sha256: str,
        submission_source: str,
        priority: int,
        now: str,
        retry_of_job_id: str | None,
        execution_number: int,
    ) -> dict[str, Any]:
        job_id = "aij_" + uuid.uuid4().hex
        connection.execute(
            """
            INSERT INTO ai_jobs(
                job_id,job_type,request_hash,payload_json,status,priority,
                model,reasoning,execution_mode,prompt_version,schema_version,
                schema_sha256,next_attempt_at,retry_of_job_id,execution_number,
                created_at,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
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
                retry_of_job_id,
                execution_number,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO ai_job_sources(
                   job_id,submission_source,created_at
               ) VALUES(?,?,?)""",
            (job_id, submission_source, now),
        )
        row = connection.execute(
            """SELECT j.*,s.submission_source FROM ai_jobs AS j
               JOIN ai_job_sources AS s ON s.job_id=j.job_id
               WHERE j.job_id=?""",
            (job_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("ai_job_insert_failed")
        return dict(row)

    @staticmethod
    def _request_hash_execution_legacy(
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
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            return dict(row) if row else None

    def latest_completed(self, job_type: str, ticker: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type=? AND j.status='completed'
                  AND upper(json_extract(j.payload_json, '$.ticker'))=?
                  AND json_extract(j.result_json, '$.output_language')='zh-CN'
                ORDER BY j.completed_at DESC, j.created_at DESC
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
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (row["job_id"],),
            ).fetchone()
            connection.commit()
            return dict(claimed)

    def mark_submission_started(
        self,
        job_id: str,
        owner: str,
        *,
        daily_limit: int = 4,
        daily_budget_usd: float = 2.0,
        daily_token_limit: int = 10_000_000,
        cooldown_seconds: int = 0,
        unknown_submission_hold_seconds: int = 86400,
    ) -> str:
        """Atomically enforce concurrency, cooldown, and daily Token usage.

        An unknown submission remains terminal and is never retried. It reserves
        the single paid slot only for the configured response recovery window.
        The former count and dollar arguments remain API-compatible but no
        longer block work; zero represents their disabled state.
        """

        del daily_limit, daily_budget_usd
        if not 102_400 <= int(daily_token_limit) <= 100_000_000:
            raise ValueError("daily_token_limit is invalid")
        now_dt = _utcnow()
        now = _iso(now_dt)
        unknown_submission_cutoff = _iso(
            now_dt
            - timedelta(
                seconds=max(1, int(unknown_submission_hold_seconds))
            )
        )
        day_start = _iso(now_dt.replace(hour=0, minute=0, second=0, microsecond=0))
        day_end = _iso(
            now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        token_limit = int(daily_token_limit)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT job_type,status,lease_owner FROM ai_jobs WHERE job_id=?",
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "pending"
                or row["lease_owner"] != owner
            ):
                connection.rollback()
                raise RuntimeError("ai_job_not_submittable")
            reservation_microusd = _task_budget_reservation_microusd(
                str(row["job_type"])
            )
            token_reservation = _task_token_reservation(str(row["job_type"]))
            in_flight = int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM ai_jobs
                    WHERE job_id<>? AND submission_started_at IS NOT NULL
                      AND (
                        status IN ('queued','in_progress')
                        OR (
                          error_code='submission_outcome_unknown'
                          AND submission_started_at>=?
                        )
                      )
                    """,
                    (job_id, unknown_submission_cutoff),
                ).fetchone()[0]
            )
            if in_flight:
                connection.execute(
                    """
                    UPDATE ai_jobs
                    SET next_attempt_at=?,error_code='global_concurrency_limit',
                        lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='pending'
                    """,
                    (_iso(now_dt + timedelta(seconds=2)), now, job_id, owner),
                )
                connection.commit()
                return "concurrency_limit"
            if int(cooldown_seconds) > 0:
                latest_terminal = connection.execute(
                    """SELECT COALESCE(completed_at,submission_started_at)
                       FROM ai_jobs
                       WHERE job_id<>? AND submission_started_at IS NOT NULL
                         AND status IN ('completed','failed','cancelled',
                                        'insufficient_context','budget_blocked')
                       ORDER BY COALESCE(completed_at,submission_started_at) DESC
                       LIMIT 1""",
                    (job_id,),
                ).fetchone()
                latest_at = (
                    _parse_time(latest_terminal[0])
                    if latest_terminal is not None
                    else None
                )
                cooldown_until = (
                    latest_at + timedelta(seconds=int(cooldown_seconds))
                    if latest_at is not None
                    else None
                )
                if cooldown_until is not None and cooldown_until > now_dt:
                    connection.execute(
                        """UPDATE ai_jobs
                           SET next_attempt_at=?,error_code='analysis_cooldown_active',
                               lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                           WHERE job_id=? AND lease_owner=? AND status='pending'""",
                        (_iso(cooldown_until), now, job_id, owner),
                    )
                    connection.commit()
                    return "cooldown"
            token_rows = connection.execute(
                """SELECT job_type,usage_total_tokens FROM ai_jobs
                   WHERE submission_started_at>=? AND submission_started_at<?""",
                (day_start, day_end),
            ).fetchall()
            tokens_used = sum(
                int(item["usage_total_tokens"])
                if item["usage_total_tokens"] is not None
                else _task_token_reservation(str(item["job_type"]))
                for item in token_rows
            )
            if tokens_used + token_reservation > token_limit:
                connection.execute(
                    """
                    UPDATE ai_jobs
                    SET status='budget_blocked',
                        error_code='daily_token_limit_reached',completed_at=?,
                        next_attempt_at=NULL,lease_owner=NULL,
                        lease_expires_at=NULL,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='pending'
                    """,
                    (now, now, job_id, owner),
                )
                connection.commit()
                return "daily_token_limit"
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET submission_started_at=COALESCE(submission_started_at,?),
                    submitted_at=COALESCE(submitted_at,?),
                    budget_charge_microusd=CASE
                        WHEN budget_charge_microusd=0 THEN ?
                        ELSE budget_charge_microusd END,
                    status='in_progress',
                    attempt_count=attempt_count+1,
                    updated_at=?
                WHERE job_id=? AND lease_owner=? AND openai_response_id IS NULL
                  AND status='pending' AND cancel_requested_at IS NULL
                """,
                (now, now, reservation_microusd, now, job_id, owner),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_not_submittable")
            return "started"

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
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT job_type,budget_charge_microusd FROM ai_jobs
                   WHERE job_id=? AND lease_owner=?""",
                (job_id, owner),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise RuntimeError("ai_job_completion_rejected")
            budget_charge_microusd = _settled_budget_charge_microusd(
                str(current["job_type"]),
                usage,
                fallback_microusd=int(current["budget_charge_microusd"] or 0),
            )
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='completed', result_json=?, completed_at=?,
                    usage_input_tokens=?, usage_cached_input_tokens=?,
                    usage_output_tokens=?, usage_reasoning_tokens=?,
                    usage_total_tokens=?, budget_charge_microusd=?, error_code=NULL,
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
                    budget_charge_microusd,
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

    def fail(
        self,
        job_id: str,
        owner: str,
        error_code: str,
        *,
        usage: dict[str, int | None] | None = None,
    ) -> None:
        now = _iso()
        safe_code = error_code[:120]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT job_type,budget_charge_microusd,openai_response_id
                   FROM ai_jobs
                   WHERE job_id=? AND lease_owner=?""",
                (job_id, owner),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise RuntimeError("ai_job_lease_lost")
            usage_values = dict(usage or {})
            has_reported_usage = any(
                usage_values.get(field) is not None
                for field in (
                    "input_tokens",
                    "cached_input_tokens",
                    "output_tokens",
                    "reasoning_tokens",
                    "total_tokens",
                )
            )
            confirmed_without_usage = bool(
                not has_reported_usage
                and current["openai_response_id"] is None
                and safe_code != "submission_outcome_unknown"
            )
            if confirmed_without_usage:
                # A local failure or an explicit provider rejection without a
                # durable response identity did not start recoverable model
                # work. Record a settled zero instead of holding the task's
                # worst-case Token reservation until the UTC day changes.
                usage_values.update(
                    {
                        "input_tokens": 0,
                        "cached_input_tokens": 0,
                        "output_tokens": 0,
                        "reasoning_tokens": 0,
                        "total_tokens": 0,
                    }
                )
            budget_charge_microusd = int(
                current["budget_charge_microusd"] or 0
            )
            if confirmed_without_usage:
                budget_charge_microusd = 0
            elif usage is not None:
                budget_charge_microusd = _settled_budget_charge_microusd(
                    str(current["job_type"]),
                    usage_values,
                    fallback_microusd=budget_charge_microusd,
                )
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='failed', error_code=?, completed_at=?,
                    usage_input_tokens=COALESCE(?,usage_input_tokens),
                    usage_cached_input_tokens=COALESCE(?,usage_cached_input_tokens),
                    usage_output_tokens=COALESCE(?,usage_output_tokens),
                    usage_reasoning_tokens=COALESCE(?,usage_reasoning_tokens),
                    usage_total_tokens=COALESCE(?,usage_total_tokens),
                    budget_charge_microusd=?,
                    next_attempt_at=NULL, lease_owner=NULL, lease_expires_at=NULL,
                    updated_at=?
                WHERE job_id=? AND lease_owner=?
                """,
                (
                    safe_code,
                    now,
                    usage_values.get("input_tokens"),
                    usage_values.get("cached_input_tokens"),
                    usage_values.get("output_tokens"),
                    usage_values.get("reasoning_tokens"),
                    usage_values.get("total_tokens"),
                    budget_charge_microusd,
                    now,
                    job_id,
                    owner,
                ),
            ).rowcount
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_lease_lost")

    def mark_cancelled(
        self,
        job_id: str,
        owner: str | None = None,
        *,
        usage: dict[str, int | None] | None = None,
    ) -> None:
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            select_sql = """SELECT job_type,budget_charge_microusd FROM ai_jobs
                            WHERE job_id=?
                              AND status IN ('pending','queued','in_progress')"""
            select_params: list[Any] = [job_id]
            if owner is not None:
                select_sql += " AND lease_owner=?"
                select_params.append(owner)
            current = connection.execute(
                select_sql,
                tuple(select_params),
            ).fetchone()
            if current is None:
                connection.commit()
                return
            usage_values = usage or {}
            budget_charge_microusd = int(
                current["budget_charge_microusd"] or 0
            )
            if usage is not None:
                budget_charge_microusd = _settled_budget_charge_microusd(
                    str(current["job_type"]),
                    usage_values,
                    fallback_microusd=budget_charge_microusd,
                )
            update_sql = """
                UPDATE ai_jobs
                SET status='cancelled', completed_at=?,
                    usage_input_tokens=COALESCE(?,usage_input_tokens),
                    usage_cached_input_tokens=COALESCE(?,usage_cached_input_tokens),
                    usage_output_tokens=COALESCE(?,usage_output_tokens),
                    usage_reasoning_tokens=COALESCE(?,usage_reasoning_tokens),
                    usage_total_tokens=COALESCE(?,usage_total_tokens),
                    budget_charge_microusd=?,
                    next_attempt_at=NULL,lease_owner=NULL,lease_expires_at=NULL,
                    updated_at=?
                WHERE job_id=? AND status IN ('pending','queued','in_progress')
            """
            update_params: list[Any] = [
                now,
                usage_values.get("input_tokens"),
                usage_values.get("cached_input_tokens"),
                usage_values.get("output_tokens"),
                usage_values.get("reasoning_tokens"),
                usage_values.get("total_tokens"),
                budget_charge_microusd,
                now,
                job_id,
            ]
            if owner is not None:
                update_sql += " AND lease_owner=?"
                update_params.append(owner)
            connection.execute(update_sql, tuple(update_params))
            connection.commit()

    def request_cancel(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        now = _iso()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
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
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            connection.commit()
            return dict(updated)

    @staticmethod
    def public(row: dict[str, Any], *, cached: bool = False) -> dict[str, Any]:
        try:
            payload = json.loads(row["payload_json"])
        except (KeyError, TypeError, json.JSONDecodeError):
            payload = {}
        result = json.loads(row["result_json"]) if row.get("result_json") else None
        legacy_output_hidden = False
        if result is not None:
            try:
                result = validate_result(
                    str(row["job_type"]),
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    payload,
                )
            except (TypeError, ValueError):
                legacy_output_hidden = True
        if legacy_output_hidden:
            result = None
        public = AIJobPublic(
            job_id=row["job_id"],
            job_type=row["job_type"],
            status=row["status"],
            model=row["model"],
            reasoning=row["reasoning"],
            submitted_at=row.get("submitted_at"),
            updated_at=row["updated_at"],
            completed_at=row.get("completed_at"),
            error_code=(
                row.get("error_code")
                or ("legacy_output_hidden" if legacy_output_hidden else None)
            ),
            retry_after=None,
            result=result,
            cached=cached,
            cancellable=(
                row["status"] in {"pending", "queued", "in_progress"}
                and not bool(row.get("cancel_requested_at"))
            ),
            cancel_requested=(
                bool(row.get("cancel_requested_at"))
                and row["status"] in {"pending", "queued", "in_progress"}
            ),
            analysis_revision=(
                int(payload["analysis_revision"])
                if isinstance(payload.get("analysis_revision"), int)
                and payload["analysis_revision"] >= 1
                else None
            ),
            cycle_revision=(
                int(payload["cycle_revision"])
                if isinstance(payload.get("cycle_revision"), int)
                and payload["cycle_revision"] >= 1
                else None
            ),
            budget_charge_usd=(
                max(0, int(row.get("budget_charge_microusd") or 0)) / 1_000_000
            ),
            usage={
                "input_tokens": row.get("usage_input_tokens"),
                "cached_input_tokens": row.get("usage_cached_input_tokens"),
                "output_tokens": row.get("usage_output_tokens"),
                "reasoning_tokens": row.get("usage_reasoning_tokens"),
                "total_tokens": row.get("usage_total_tokens"),
            },
        )
        payload = public.model_dump(mode="json")
        payload["submission_source"] = (
            row.get("submission_source")
            if row.get("submission_source") in {"manual", "scheduled"}
            else "manual"
        )
        return payload

    def budget_snapshot(
        self,
        *,
        daily_limit: int,
        daily_budget_usd: float,
        daily_token_limit: int = 10_000_000,
        cooldown_seconds: int = 0,
        unknown_submission_hold_seconds: int = 86400,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Return a secret-free, point-in-time view of paid task capacity.

        Use the same bounded quarantine as ``mark_submission_started`` so the
        owner UI and the worker agree about whether the paid slot is available.
        """

        self.initialize()
        observed = now or _utcnow()
        unknown_submission_cutoff = _iso(
            observed
            - timedelta(
                seconds=max(1, int(unknown_submission_hold_seconds))
            )
        )
        day_start_dt = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_dt = day_start_dt + timedelta(days=1)
        with self._connect() as connection:
            totals = connection.execute(
                """
                SELECT COUNT(*) AS submitted_jobs,
                       COALESCE(SUM(budget_charge_microusd),0) AS charged,
                       COALESCE(SUM(usage_total_tokens),0) AS total_tokens
                FROM ai_jobs
                WHERE submission_started_at>=? AND submission_started_at<?
                """,
                (_iso(day_start_dt), _iso(day_end_dt)),
            ).fetchone()
            token_rows = connection.execute(
                """SELECT job_type,usage_total_tokens FROM ai_jobs
                   WHERE submission_started_at>=? AND submission_started_at<?""",
                (_iso(day_start_dt), _iso(day_end_dt)),
            ).fetchall()
            active = connection.execute(
                """
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.submission_started_at IS NOT NULL
                  AND (
                    j.status IN ('queued','in_progress')
                    OR (
                      j.error_code='submission_outcome_unknown'
                      AND j.submission_started_at>=?
                    )
                  )
                ORDER BY j.created_at LIMIT 1
                """,
                (unknown_submission_cutoff,),
            ).fetchone()
            latest_paid = connection.execute(
                """
                SELECT COALESCE(completed_at,submission_started_at) AS activity_at
                FROM ai_jobs
                WHERE submission_started_at IS NOT NULL
                  AND status IN ('completed','failed','cancelled',
                                 'insufficient_context','budget_blocked')
                ORDER BY COALESCE(completed_at,submission_started_at) DESC
                LIMIT 1
                """
            ).fetchone()
        submitted_jobs = int(totals["submitted_jobs"] if totals else 0)
        charged_microusd = int(totals["charged"] if totals else 0)
        del daily_limit, daily_budget_usd
        token_limit = int(daily_token_limit)
        if not 102_400 <= token_limit <= 100_000_000:
            raise ValueError("daily_token_limit is invalid")
        token_budget_used = sum(
            int(item["usage_total_tokens"])
            if item["usage_total_tokens"] is not None
            else _task_token_reservation(str(item["job_type"]))
            for item in token_rows
        )
        cooldown_until: datetime | None = None
        if latest_paid is not None and int(cooldown_seconds) > 0:
            activity_at = _parse_time(latest_paid["activity_at"])
            if activity_at is not None:
                candidate = activity_at + timedelta(seconds=int(cooldown_seconds))
                if candidate > observed:
                    cooldown_until = candidate
        active_public = self.public(dict(active)) if active is not None else None
        token_budget_available = (
            token_budget_used + _minimum_task_token_reservation()
            <= token_limit
        )
        return {
            "daily_max_jobs": 0,
            "daily_budget_usd": 0.0,
            "daily_token_limit": token_limit,
            "submitted_jobs": submitted_jobs,
            "budget_used_usd": charged_microusd / 1_000_000,
            "budget_remaining_usd": None,
            "usage_total_tokens": int(totals["total_tokens"] if totals else 0),
            "token_budget_used_tokens": token_budget_used,
            "token_budget_remaining_tokens": max(
                0,
                token_limit - token_budget_used,
            ),
            "token_budget_available": token_budget_available,
            "budget_available": token_budget_available,
            "job_limit_available": True,
            "dollar_budget_available": True,
            "concurrency_available": active is None,
            "active_job": active_public,
            "cooldown_until": _iso(cooldown_until) if cooldown_until else None,
            "cooldown_complete": cooldown_until is None,
        }

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
                submission_unknown = connection.execute(
                    """
                    SELECT COUNT(*) FROM ai_jobs
                    WHERE submission_started_at IS NOT NULL
                      AND error_code='submission_outcome_unknown'
                    """
                ).fetchone()[0]
            return {
                "healthy": True,
                "status": "ready",
                "schema_version": _SCHEMA_VERSION,
                "pending": pending,
                "submission_unknown": submission_unknown,
            }
        except Exception:
            return {
                "healthy": False,
                "status": "database_unavailable",
                "schema_version": _SCHEMA_VERSION,
                "pending": None,
                "submission_unknown": None,
            }
