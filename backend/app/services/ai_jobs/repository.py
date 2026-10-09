from __future__ import annotations

import hashlib
import ipaddress
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping
from urllib.parse import quote, urlsplit

from app.failure_diagnostics import record_fallback_failure
from app.json_validation import canonical_json_text
from app.services.model_budget import (
    SharedModelBudget,
    JOB_MODELS,
    can_reserve_in_transaction,
    initialize_schema as initialize_model_budget_schema,
    totals_in_transaction as model_budget_totals,
    usd_to_microusd,
)
from app.services.ai_jobs.models import (
    AIJobPublic,
    earnings_report_id,
    validate_result,
    validate_result_cached,
)


# 版本号必须随 schema 形状（建表文本）一起升级：registry 按版本存建表文本的
# 校验和，形状变了版本不变会在老库上撞出 ai_job_schema_checksum_mismatch，
# ai_jobs/catalyst/focus 三个任务整体停摆（2026-08-08 生产事故：加 error_detail
# 列没升版本）。老版本行保留作历史；回滚安全——旧代码只查自己版本的行。
_SCHEMA_VERSION = "ai-jobs-v5"
_SOURCE_SCHEMA_VERSION = "ai-job-sources-v1"
_BATCH_SCHEMA_VERSION = "ai-job-batch-members-v1"
_EARNINGS_LOCK_SCHEMA_VERSION = "ai-earnings-final-locks-v1"
_IDENTITY_MIGRATION_VERSION = "ai-job-identities-v2"
_IDENTITY_MIGRATION_CHECKSUM = hashlib.sha256(
    b"restore-source-aware-hashes-and-seal-unsubmitted-duplicates-v2"
).hexdigest()
_DUPLICATE_MIGRATION_ERROR = "duplicate_request_migrated"
_MAX_RESULT_JSON_BYTES = 1024 * 1024
_SUBMISSION_LANES = ("manual", "scheduled")
# 没有 response id 的结果未知永远无法对账，只在上游可能仍在执行的短窗口内
# 占住本车道的付费槽。闸门、认领与快照共用这一个值。
_UNKNOWN_NO_RESPONSE_HOLD_SECONDS = 900
# 余额耗尽是账户级状态：最近一次 provider_credit_exhausted 之后暂停所有新
# 提交，任务留在队列里不消耗重试次数；窗口结束后放一单探测是否已充值。
_CREDIT_EXHAUSTED_HOLD_SECONDS = 600
_CREDIT_EXHAUSTED_HOLD_ERROR = "provider_credit_exhausted_hold"
# 供应商以终态响应确认失败、且不会再计费的错误码（2026-08-14：余额耗尽的
# 瞬时失败零计费）。取回或取消时的 404、401/403、400 不在其中：它们只说明
# 本地拿不到响应，上游可能已经跑完并计费。
_PROVIDER_CONFIRMED_UNBILLED_ERRORS = frozenset(
    {"provider_credit_exhausted", "provider_failed"}
)
# 无论有无 response id 都保留满额预留：结果未知（可能已计费未对账）与取消
# 未获确认的轮询超时（上游可能仍在运行计费）。
_RESERVATION_HOLDING_ERRORS = frozenset(
    {"submission_outcome_unknown", "provider_poll_timeout"}
)
# 已拿到 response id、本地却没能落下结果的失败：恢复工具可以重新取回付费
# 结果而不重新提交。
RECOVERABLE_FAILURE_CODES = frozenset(
    {"schema_validation_failed", "provider_unavailable", "local_storage_error"}
)
_SCHEDULED_HISTORY_JOB_TYPES = ("news_impact", "market_focus")
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
_AI_JOBS_V4_TABLE_SQL = """
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
    error_detail TEXT,
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
_CLAUDE_MODEL = "claude-haiku-5-5"


def _uses_claude(model: Any) -> bool:
    from app.services.ai_jobs.runtime import uses_claude

    return uses_claude(model)


_CLAUDE_COLUMNS = {
    "anthropic_message_id": "TEXT",
    "provider_result_json": "TEXT CHECK(provider_result_json IS NULL OR json_valid(provider_result_json))",
    "usage_cache_creation_input_tokens": "INTEGER",
    "usage_cache_creation_5m_input_tokens": "INTEGER",
    "usage_cache_creation_1h_input_tokens": "INTEGER",
}
_CACHE_USAGE_FIELDS = (
    "cache_creation_input_tokens",
    "cache_creation_5m_input_tokens",
    "cache_creation_1h_input_tokens",
)
_TOOL_USAGE_FIELDS = (
    "web_search_requests", "web_fetch_requests", "code_execution_requests",
)
_AI_JOBS_TABLE_SQL = _AI_JOBS_V4_TABLE_SQL.replace(
    "    openai_response_id TEXT,",
    "    openai_response_id TEXT,\n" + "".join(
        f"    {name} {definition},\n" for name, definition in _CLAUDE_COLUMNS.items()
    ).rstrip("\n"),
)
_AI_JOBS_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_ai_jobs_due
ON ai_jobs(status, next_attempt_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_ticker
ON ai_jobs(job_type, json_extract(payload_json, '$.ticker'), completed_at DESC);
"""


def _statements(script: str) -> tuple[str, ...]:
    """Split a checksummed schema script into the statements that apply it.

    The version/checksum rule above hashes the script text, so the executed
    statements must come from that text: a hand-kept second copy could change
    the real shape without moving the checksum.
    """

    return tuple(part.strip() for part in script.split(";") if part.strip())


_AI_JOBS_INDEX_STATEMENTS = _statements(_AI_JOBS_INDEX_SQL)
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
_AI_JOB_BATCH_MEMBERS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_job_batch_members (
    job_id TEXT PRIMARY KEY REFERENCES ai_jobs(job_id) ON DELETE CASCADE,
    batch_id TEXT NOT NULL,
    position INTEGER NOT NULL CHECK(position >= 1),
    created_at TEXT NOT NULL,
    UNIQUE(batch_id, position)
);
CREATE INDEX IF NOT EXISTS idx_ai_job_batch_members_batch
ON ai_job_batch_members(batch_id, position);
"""
_AI_JOB_BATCH_MEMBERS_STATEMENTS = _statements(_AI_JOB_BATCH_MEMBERS_TABLE_SQL)
_BATCH_SCHEMA_CHECKSUM = hashlib.sha256(
    _AI_JOB_BATCH_MEMBERS_TABLE_SQL.encode("utf-8")
).hexdigest()
_EARNINGS_FINAL_LOCKS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_earnings_final_locks (
    report_id TEXT PRIMARY KEY,
    ticker TEXT NOT NULL,
    earnings_date TEXT NOT NULL,
    report_year INTEGER,
    report_quarter INTEGER,
    job_id TEXT NOT NULL UNIQUE
        REFERENCES ai_jobs(job_id) ON DELETE CASCADE,
    locked_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_earnings_final_locks_ticker
ON ai_earnings_final_locks(ticker, earnings_date DESC);
"""
_EARNINGS_FINAL_LOCK_STATEMENTS = _statements(_EARNINGS_FINAL_LOCKS_TABLE_SQL)
_EARNINGS_LOCK_SCHEMA_CHECKSUM = hashlib.sha256(
    _EARNINGS_FINAL_LOCKS_TABLE_SQL.encode("utf-8")
).hexdigest()


_PROVIDER_PROGRESS_SCHEMA_VERSION = "ai-job-provider-progress-v1"
_PROVIDER_PROGRESS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS ai_job_provider_progress (
    job_id TEXT PRIMARY KEY REFERENCES ai_jobs(job_id) ON DELETE CASCADE,
    progress_json TEXT NOT NULL CHECK(json_valid(progress_json)),
    updated_at TEXT NOT NULL
);
"""
_PROVIDER_PROGRESS_SCHEMA_CHECKSUM = hashlib.sha256(
    _PROVIDER_PROGRESS_SCHEMA_SQL.encode("utf-8")
).hexdigest()


def _public_evidence_url(value: Any) -> bool:
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)):
        return False
    try:
        parts = urlsplit(value)
        if (parts.scheme.lower() not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None):
            return False
        parts.port  # Validate malformed/out-of-range ports without network access.
        host = parts.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            return bool(
                len(host) <= 253 and len(labels) >= 2 and not labels[-1].isdigit()
                and labels[-1] not in {
                    "localhost", "local", "internal", "test", "invalid",
                    "example", "onion", "home", "lan",
                }
                and all(
                    label and len(label) <= 63
                    and not label.startswith("-") and not label.endswith("-")
                    and all(char.isascii() and (char.isalnum() or char == "-") for char in label)
                    for label in labels
                )
            )
        return bool(address.is_global and not address.is_multicast
                    and not address.is_reserved and not getattr(address, "ipv4_mapped", None))
    except (ValueError, UnicodeError):
        return False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utcnow()).isoformat().replace("+00:00", "Z")


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _task_budget_reservation_microusd(job_type: str, *, model: str | None = None) -> int:
    from app.services.ai_jobs.runtime import budget_reservation_microusd

    return budget_reservation_microusd(job_type, model=model)


def _task_token_reservation(job_type: str, *, model: str | None = None) -> int:
    from app.services.ai_jobs.runtime import token_reservation

    return token_reservation(job_type, model=model)


def _minimum_task_token_reservation(*, model: str | None = None) -> int:
    from app.services.ai_jobs.runtime import minimum_token_reservation

    return minimum_token_reservation(model=model)


def _bounded_provider_input(payload: dict[str, Any]) -> str:
    from app.services.ai_jobs.runtime import _bounded_untrusted_json

    return _bounded_untrusted_json(payload)


def _reservation_released(
    status: Any,
    error_code: Any,
    response_id: Any,
    model: Any = None,
    submission_started_at: Any = None,
) -> bool:
    """Whether a terminal row without reported usage is known to cost nothing.

    Token 账与美元账共用这一条白名单：只有从未拿到上游响应身份的终态，或
    供应商以终态响应确认未计费的失败才释放预留。旧的黑名单写法会把「写库
    失败记成 provider_unavailable、上游其实还在跑」的行一起释放。
    """

    if str(status or "") not in {"failed", "cancelled"}:
        return False
    if _uses_claude(model) and submission_started_at:
        return False
    code = str(error_code or "")
    if code in _RESERVATION_HOLDING_ERRORS:
        return False
    return not response_id or code in _PROVIDER_CONFIRMED_UNBILLED_ERRORS


def _confirmed_unbilled_claude_row(row: Mapping[str, Any]) -> bool:
    """Recognize the worker's durable, explicit no-model-work rejection evidence.

    An error code alone never proves zero cost. Require the complete zero usage
    projection, the exact definitive HTTP rejection detail, and no response id.
    """
    return bool(
        _uses_claude(row["model"]) and row["status"] == "failed"
        and row["openai_response_id"] is None and row["anthropic_message_id"] is None
        and row["provider_result_json"] is None
        and row["error_code"] in {
            "provider_auth_failed", "provider_rate_limited",
            "provider_request_rejected", "provider_credit_exhausted",
        }
        and row["error_detail"] in {
            f"Claude rejected request (HTTP {status})"
            for status in (400, 401, 403, 404, 413, 422, 429)
        }
        and all(row["usage_" + field] == 0 for field in (
            "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens",
            "total_tokens", *_CACHE_USAGE_FIELDS,
        ))
    )


def _daily_tokens_used(token_rows: Iterable[Mapping[str, Any]]) -> int:
    """当日 token 账：已结算按实际用量，无用量的行按 _reservation_released。

    余额耗尽等零计费失败若按满额预留计入，失败风暴会吃光全天预算（2026-08-14
    生产：94 个 provider_failed 把 10M 账本记到 9.97M，实际结算 0，全线误报
    「今日 Token 预算已用完」）。completed 无用量（计费了、数额未知）、在途与
    待重试行保留满额预留，防超支方向不放松。
    """

    total = 0
    for item in token_rows:
        usage = item["usage_total_tokens"]
        if usage is not None:
            total += int(usage)
            continue
        if not _reservation_released(
            item["status"],
            item["error_code"],
            item["openai_response_id"],
            item["model"],
            item["submission_started_at"],
        ):
            total += _task_token_reservation(
                str(item["job_type"]), model=str(item["model"])
            )
    return total


def _settled_budget_charge_microusd(
    job_type: str,
    usage: dict[str, int | None],
    *,
    fallback_microusd: int,
    model: str | None = None,
) -> int:
    from app.services.ai_jobs.runtime import settled_usage_cost_microusd

    return settled_usage_cost_microusd(
        job_type,
        usage,
        fallback_microusd=fallback_microusd,
        model=model,
    )


# Owner-facing focus cycles are explicit, rate-limited actions (30s trigger
# cooldown, prepared-revision optimistic locking, and the single paid slot).
# They must not be starved by the bulk analysis backlog: in earnings season
# the scheduled earnings/news queue legitimately rides the max_queued ceiling
# for hours, and a depth-based rejection would make the focus trigger answer
# "queue full" exactly when the owner most wants a market read.
_QUEUE_LIMIT_EXEMPT_JOB_TYPES = frozenset({"market_focus"})


_MISSING_CHARGES_SQL = """SELECT job_id,job_type,model,submission_started_at,status,error_code,openai_response_id,
          budget_charge_microusd,error_detail,anthropic_message_id,provider_result_json,
          usage_input_tokens,usage_cached_input_tokens,
          usage_output_tokens,usage_reasoning_tokens,
          usage_total_tokens,usage_cache_creation_input_tokens,
          usage_cache_creation_5m_input_tokens,usage_cache_creation_1h_input_tokens
   FROM ai_jobs
   WHERE submission_started_at IS NOT NULL
     AND budget_charge_microusd=0"""


def _backfilled_budget_charge(missing: Mapping[str, Any]) -> int | None:
    """Charge to backfill for a started job recorded at zero, or None to keep it."""

    reservation = _task_budget_reservation_microusd(
        str(missing["job_type"]), model=str(missing["model"])
    )
    usage = {
        "input_tokens": missing["usage_input_tokens"],
        "cached_input_tokens": missing["usage_cached_input_tokens"],
        "output_tokens": missing["usage_output_tokens"],
        "reasoning_tokens": missing["usage_reasoning_tokens"],
        "total_tokens": missing["usage_total_tokens"],
        **{field: missing["usage_" + field] for field in _CACHE_USAGE_FIELDS},
    }
    # The receipt carries tool counters that have no separate SQL
    # columns. Recalculate from that durable evidence, not an
    # incomplete token-only projection after process restart.
    if missing["provider_result_json"]:
        try:
            receipt = json.loads(missing["provider_result_json"])
            receipt_usage = receipt.get("usage") if isinstance(receipt, dict) else None
            usage = dict(receipt_usage) if isinstance(receipt_usage, dict) else {}
        except (TypeError, ValueError):
            usage = {}
    elif _confirmed_unbilled_claude_row(missing):
        # The worker persisted a definitive HTTP rejection plus
        # explicit zero counts before clearing its lease. It has
        # no message id/receipt and must remain free after restart.
        return None
    has_terminal_usage = (
        usage.get("input_tokens") is not None
        and usage.get("output_tokens") is not None
        and missing["error_code"] != "submission_outcome_unknown"
    )
    if not has_terminal_usage and _reservation_released(
        missing["status"],
        missing["error_code"],
        missing["openai_response_id"],
        missing["model"],
        missing["submission_started_at"],
    ):
        # 规则确认零计费的行本来就记 0；每次初始化都会走到这里，
        # 不能把它们回填成满额预留。
        return None
    return (
        _settled_budget_charge_microusd(
            str(missing["job_type"]),
            usage,
            fallback_microusd=reservation,
            model=str(missing["model"]),
        )
        if has_terminal_usage
        else reservation
    )


@dataclass(frozen=True)
class _InitializationBackfill:
    missing_sources: bool
    charge_job_ids: list[str]


# Schema cookie (``PRAGMA schema_version``) of each path this process has
# fully initialized. Request handlers build a fresh repository per request;
# each used to repeat the whole schema transaction. Any schema change made
# out of band moves the cookie and brings the full checks back.
_READY_SCHEMAS: dict[str, int | None] = {}
_READY_SCHEMAS_LOCK = threading.Lock()


class AIJobRepository:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._initialize_lock = threading.Lock()
        self._initialized = False

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
        """Run the durable schema and recovery checks explicitly.

        Explicit callers may use this again after changing a database out of
        band. Normal repository operations use ``ensure_initialized`` so the
        worker does not acquire a schema write lock on every queue poll.
        """
        with self._initialize_lock:
            self._initialized = False
            with _READY_SCHEMAS_LOCK:
                self._initialize_database()
                _READY_SCHEMAS[str(self.path.resolve())] = self._schema_cookie()
            self._initialized = True

    def ensure_initialized(self) -> None:
        """Initialize this database once per process, including across threads."""
        with self._initialize_lock:
            if self._initialized:
                return
            key = str(self.path.resolve())
            with _READY_SCHEMAS_LOCK:
                cookie = self._schema_cookie()
                if cookie is None or _READY_SCHEMAS.get(key) != cookie:
                    self._initialize_database()
                    _READY_SCHEMAS[key] = self._schema_cookie()
            self._initialized = True

    def _schema_cookie(self) -> int | None:
        if not self.path.is_file():
            return None
        try:
            with self._connect_read_only() as connection:
                return int(connection.execute("PRAGMA schema_version").fetchone()[0])
        except sqlite3.Error:
            return None

    def _plan_initialization_backfill(self) -> _InitializationBackfill | None:
        """Read which legacy rows still need a backfill, before the write lock.

        Both backfills used to scan the whole job table inside the schema
        write transaction on every new repository instance, holding the
        ai-jobs.db write lock for seconds. ``None`` keeps the full in-lock
        backfill (new or unreadable stores).
        """

        if not self.path.is_file():
            return None
        try:
            with self._connect_read_only() as connection:
                missing_sources = connection.execute(
                    """SELECT 1 FROM ai_jobs AS j
                       WHERE NOT EXISTS (
                           SELECT 1 FROM ai_job_sources AS s WHERE s.job_id=j.job_id
                       )
                       LIMIT 1"""
                ).fetchone() is not None
                charge_job_ids = [
                    str(row["job_id"])
                    for row in connection.execute(_MISSING_CHARGES_SQL)
                    if _backfilled_budget_charge(row) is not None
                ]
        except sqlite3.Error:
            return None
        return _InitializationBackfill(
            missing_sources=missing_sources,
            charge_job_ids=charge_job_ids,
        )

    def _initialize_database(self) -> None:
        backfill = self._plan_initialization_backfill()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(_SCHEMA_REGISTRY_SQL)
            connection.commit()
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(_AI_JOBS_TABLE_SQL)
            self._ensure_indexes(connection)
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(ai_jobs)")
            }
            for name, definition in _CLAUDE_COLUMNS.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE ai_jobs ADD COLUMN {name} {definition}")
            progress_schema = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_PROVIDER_PROGRESS_SCHEMA_VERSION,),
            ).fetchone()
            if progress_schema is not None and progress_schema["checksum"] != _PROVIDER_PROGRESS_SCHEMA_CHECKSUM:
                raise RuntimeError("ai_job_provider_progress_schema_checksum_mismatch")
            connection.execute(_PROVIDER_PROGRESS_SCHEMA_SQL)
            connection.execute(
                "INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at) VALUES(?,?,?)",
                (_PROVIDER_PROGRESS_SCHEMA_VERSION, _PROVIDER_PROGRESS_SCHEMA_CHECKSUM, _iso()),
            )
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
            for statement in _AI_JOB_BATCH_MEMBERS_STATEMENTS:
                connection.execute(statement)
            batch_schema = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_BATCH_SCHEMA_VERSION,),
            ).fetchone()
            if (
                batch_schema is not None
                and batch_schema["checksum"] != _BATCH_SCHEMA_CHECKSUM
            ):
                raise RuntimeError("ai_job_batch_schema_checksum_mismatch")
            connection.execute(
                """INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at)
                   VALUES(?,?,?)""",
                (_BATCH_SCHEMA_VERSION, _BATCH_SCHEMA_CHECKSUM, _iso()),
            )
            for statement in _EARNINGS_FINAL_LOCK_STATEMENTS:
                connection.execute(statement)
            earnings_lock_schema = connection.execute(
                "SELECT checksum FROM ai_job_schema WHERE version=?",
                (_EARNINGS_LOCK_SCHEMA_VERSION,),
            ).fetchone()
            if (
                earnings_lock_schema is not None
                and earnings_lock_schema["checksum"]
                != _EARNINGS_LOCK_SCHEMA_CHECKSUM
            ):
                raise RuntimeError("ai_earnings_lock_schema_checksum_mismatch")
            connection.execute(
                """INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at)
                   VALUES(?,?,?)""",
                (
                    _EARNINGS_LOCK_SCHEMA_VERSION,
                    _EARNINGS_LOCK_SCHEMA_CHECKSUM,
                    _iso(),
                ),
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
                # The one-shot rewrite is retired; new stores still record its
                # row so their registry matches production.
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
            if backfill is None or backfill.missing_sources:
                connection.execute(
                    """INSERT OR IGNORE INTO ai_job_sources(
                           job_id,submission_source,created_at
                       )
                       SELECT job_id,'manual',created_at FROM ai_jobs"""
                )
            if backfill is None:
                missing_charges = connection.execute(
                    _MISSING_CHARGES_SQL
                ).fetchall()
            else:
                missing_charges = []
                for offset in range(0, len(backfill.charge_job_ids), 500):
                    chunk = backfill.charge_job_ids[offset : offset + 500]
                    missing_charges.extend(
                        connection.execute(
                            _MISSING_CHARGES_SQL
                            + f" AND job_id IN ({','.join('?' for _ in chunk)})",
                            chunk,
                        ).fetchall()
                    )
            for missing in missing_charges:
                charge = _backfilled_budget_charge(missing)
                if charge is None:
                    continue
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
            initialize_model_budget_schema(connection)
            connection.commit()

    @staticmethod
    def _ensure_indexes(connection: sqlite3.Connection) -> None:
        for statement in _AI_JOBS_INDEX_STATEMENTS:
            connection.execute(statement)

    @staticmethod
    def _canonical_json(
        payload: dict[str, Any],
        *,
        max_bytes: int,
        error_code: str,
    ) -> str:
        raw = canonical_json_text(payload)
        if len(raw.encode("utf-8")) > max_bytes:
            raise ValueError(error_code)
        return raw

    @staticmethod
    def _canonical_payload(payload: dict[str, Any]) -> str:
        # 入队直接用提交时的口径（转义 <> 之后 60,000 字节）。两个上限不一致
        # 时，夹在中间的 payload 能入队、提交时却终态 ai_input_too_large，
        # 调用方按 ai_job_payload_too_large 做的降级也接不住（2026-09-25 审计）。
        try:
            _bounded_provider_input(payload)
        except ValueError as exc:
            if str(exc) != "ai_input_too_large":
                raise
            raise ValueError("ai_job_payload_too_large") from exc
        return canonical_json_text(payload)

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

    @staticmethod
    def _earnings_identity(payload: dict[str, Any]) -> tuple[str, str, str]:
        if not isinstance(payload, dict):
            return "", "", ""
        ticker = str(payload.get("ticker") or "").strip().upper()
        report_date = str(payload.get("earnings_date") or "").strip()
        report_id = str(payload.get("report_id") or "").strip()
        if not report_id and ticker:
            report_id = earnings_report_id(payload)
        return report_id, ticker, report_date

    @staticmethod
    def _final_stage(payload: dict[str, Any]) -> bool:
        return str(
            payload.get("analysis_stage")
            or payload.get("analysis_phase")
            or ""
        ) == "post_release_final"

    @classmethod
    def _decorate_earnings_row(
        cls,
        connection: sqlite3.Connection,
        row: sqlite3.Row | dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        decorated = dict(row)
        if decorated.get("job_type") != "earnings_impact":
            return decorated
        try:
            payload = json.loads(str(decorated.get("payload_json") or "{}"))
        except (TypeError, json.JSONDecodeError):
            payload = {}
        report_id, _ticker, _report_date = cls._earnings_identity(payload)
        if not report_id:
            decorated["earnings_final_locked"] = 0
            decorated["earnings_finalization_in_progress"] = 0
            return decorated
        decorated["earnings_final_locked"] = int(
            connection.execute(
                """SELECT EXISTS(
                       SELECT 1 FROM ai_earnings_final_locks WHERE report_id=?
                   )""",
                (report_id,),
            ).fetchone()[0]
        )
        decorated["earnings_finalization_in_progress"] = int(
            connection.execute(
                """SELECT EXISTS(
                       SELECT 1 FROM ai_jobs
                       WHERE job_type='earnings_impact'
                         AND status IN ('pending','queued','in_progress')
                         AND json_extract(payload_json,'$.report_id')=?
                         AND COALESCE(
                               json_extract(payload_json,'$.analysis_stage'),
                               json_extract(payload_json,'$.analysis_phase')
                             )='post_release_final'
                   )""",
                (report_id,),
            ).fetchone()[0]
        )
        return decorated

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
        batch_id: str | None = None,
        batch_position: int | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if execution_mode != "background":
            raise ValueError("background_execution_required")
        if submission_source not in {"manual", "scheduled"}:
            raise ValueError("invalid_submission_source")
        if batch_id is not None:
            if (
                job_type != "news_impact"
                or not isinstance(batch_id, str)
                or not batch_id.strip()
                or len(batch_id) > 96
                or isinstance(batch_position, bool)
                or not isinstance(batch_position, int)
                or batch_position < 1
            ):
                raise ValueError("invalid_ai_job_batch")
        elif batch_position is not None:
            raise ValueError("invalid_ai_job_batch")
        self.ensure_initialized()
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
            if job_type == "earnings_impact":
                report_id, _ticker, _report_date = self._earnings_identity(payload)
                if report_id:
                    locked = bool(
                        connection.execute(
                            """SELECT 1 FROM ai_earnings_final_locks
                               WHERE report_id=?""",
                            (report_id,),
                        ).fetchone()
                    )
                    if locked:
                        connection.rollback()
                        raise RuntimeError("earnings_analysis_locked")
                    final_active = bool(
                        connection.execute(
                            """SELECT 1 FROM ai_jobs
                               WHERE job_type='earnings_impact'
                                 AND status IN ('pending','queued','in_progress')
                                 AND json_extract(payload_json,'$.report_id')=?
                                 AND COALESCE(
                                       json_extract(payload_json,'$.analysis_stage'),
                                       json_extract(payload_json,'$.analysis_phase')
                                     )='post_release_final'
                               LIMIT 1""",
                            (report_id,),
                        ).fetchone()
                    )
                    exact_final_active = bool(
                        self._final_stage(payload)
                        and any(
                            row["status"] in {"pending", "queued", "in_progress"}
                            for row in matches
                        )
                    )
                    if final_active and not exact_final_active:
                        connection.rollback()
                        raise RuntimeError(
                            "earnings_finalization_in_progress"
                        )
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
                    if (
                        active >= max_queued
                        and job_type not in _QUEUE_LIMIT_EXEMPT_JOB_TYPES
                    ):
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
                        batch_id=batch_id,
                        batch_position=batch_position,
                    )
                    decorated = self._decorate_earnings_row(connection, row)
                    connection.commit()
                    return decorated, True
                # 与 get_job 同一口径补上终稿锁与「终稿进行中」：202 响应直接
                # 用这一行生成报告状态（2026-09-25 审计）。
                decorated = self._decorate_earnings_row(connection, existing)
                connection.commit()
                return decorated, False
            active = connection.execute(
                """
                SELECT COUNT(*) AS count FROM ai_jobs
                WHERE status IN ('pending','queued','in_progress')
                """
            ).fetchone()["count"]
            if active >= max_queued and job_type not in _QUEUE_LIMIT_EXEMPT_JOB_TYPES:
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
                batch_id=batch_id,
                batch_position=batch_position,
            )
            decorated = self._decorate_earnings_row(connection, row)
            connection.commit()
            return decorated, True

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
        batch_id: str | None = None,
        batch_position: int | None = None,
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
        if job_type == "news_impact":
            resolved_batch_id = batch_id or ("aib_" + uuid.uuid4().hex)
            resolved_position = batch_position or 1
            connection.execute(
                """INSERT INTO ai_job_batch_members(
                       job_id,batch_id,position,created_at
                   ) VALUES(?,?,?,?)""",
                (job_id, resolved_batch_id, resolved_position, now),
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
        self.ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            return self._decorate_earnings_row(connection, row)

    def latest_completed(
        self,
        job_type: str,
        ticker: str,
        *,
        report_id: str | None = None,
    ) -> dict[str, Any] | None:
        self.ensure_initialized()
        with self._connect() as connection:
            report_filter = ""
            parameters: list[Any] = [job_type, ticker.upper()]
            if job_type == "earnings_impact":
                parameters.append(_iso(_utcnow() - timedelta(days=30)))
                report_filter += (
                    " AND COALESCE(j.completed_at,j.updated_at,j.created_at)>=?"
                )
                if report_id:
                    report_filter += (
                        " AND json_extract(j.payload_json,'$.report_id')=?"
                    )
                    parameters.append(report_id)
            row = connection.execute(
                f"""
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type=? AND j.status='completed'
                  AND upper(json_extract(j.payload_json, '$.ticker'))=?
                  AND json_extract(j.result_json, '$.output_language')='zh-CN'
                  {report_filter}
                ORDER BY j.completed_at DESC, j.created_at DESC
                LIMIT 1
                """,
                tuple(parameters),
            ).fetchone()
            return self._decorate_earnings_row(connection, row)

    def latest_for_ticker(self, job_type: str, ticker: str) -> dict[str, Any] | None:
        """Return the latest durable job for a ticker, regardless of state."""

        self.ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type=?
                  AND upper(json_extract(j.payload_json, '$.ticker'))=?
                ORDER BY j.created_at DESC,j.job_id DESC
                LIMIT 1
                """,
                (job_type, ticker.upper()),
            ).fetchone()
            return self._decorate_earnings_row(connection, row)

    def active_for_ticker(self, job_type: str, ticker: str) -> dict[str, Any] | None:
        """Return the newest still-running job for a ticker, if any.

        证据包含动态上下文块后，同一票的重复提交几乎必然产生新的
        request_hash，仓库级按哈希去重不再能挡住重复付费。端点用这个查询
        实现按票单飞：已有在跑任务时直接返回它，除非调用方显式 force。
        """

        self.ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type=?
                  AND j.status IN ('pending','queued','in_progress')
                  AND upper(json_extract(j.payload_json, '$.ticker'))=?
                ORDER BY j.created_at DESC,j.job_id DESC
                LIMIT 1
                """,
                (job_type, ticker.upper()),
            ).fetchone()
            return dict(row) if row is not None else None

    def active_scheduled_earnings_pre_release_count(self) -> int:
        """Count only active automatic preliminary earnings analyses."""

        self.ensure_initialized()
        with self._connect() as connection:
            return int(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM ai_jobs AS j
                    JOIN ai_job_sources AS s ON s.job_id=j.job_id
                    WHERE j.job_type='earnings_impact'
                      AND j.status IN ('pending','queued','in_progress')
                      AND s.submission_source='scheduled'
                      AND COALESCE(
                            json_extract(j.payload_json,'$.analysis_stage'),
                            json_extract(j.payload_json,'$.analysis_phase'),
                            'pre_release'
                          )='pre_release'
                    """
                ).fetchone()[0]
            )

    def latest_for_report(
        self,
        ticker: str,
        report_id: str,
        *,
        analysis_stage: str | None = None,
        status: str | None = None,
        legacy_report_date: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Find the newest job for one report.

        ``legacy_report_date`` also matches analyses written before report ids
        were bound to the payload. Those rows carry the earnings date but no
        report id, so a strict match hides them: the reader loses an analysis
        that already exists and the scheduler pays to produce it again. Pass it
        only when the goal is to reuse existing work, never when deciding
        whether new work may be enqueued.
        """

        self.ensure_initialized()
        with self._connect() as connection:
            stage_filter = ""
            status_filter = ""
            report_filter = "AND json_extract(j.payload_json,'$.report_id')=?"
            parameters: list[Any] = [
                ticker.upper(),
                report_id,
            ]
            if legacy_report_date:
                report_filter = """
                  AND (
                        json_extract(j.payload_json,'$.report_id')=?
                        OR (
                          json_extract(j.payload_json,'$.report_id') IS NULL
                          AND json_extract(j.payload_json,'$.earnings_date')=?
                        )
                      )
                """
                parameters.append(legacy_report_date)
            observed = (now or _utcnow())
            if observed.tzinfo is None or observed.utcoffset() is None:
                observed = observed.replace(tzinfo=timezone.utc)
            parameters.append(_iso(observed.astimezone(timezone.utc) - timedelta(days=30)))
            if analysis_stage:
                if analysis_stage == "pre_release":
                    # Jobs written before earnings stages were introduced are
                    # pre-release analyses. Keep them discoverable so a
                    # released report can be finalized without paying for the
                    # same preliminary analysis again.
                    stage_filter = """
                      AND COALESCE(
                            json_extract(j.payload_json,'$.analysis_stage'),
                            json_extract(j.payload_json,'$.analysis_phase'),
                            'pre_release'
                          )=?
                    """
                else:
                    stage_filter = """
                      AND COALESCE(
                            json_extract(j.payload_json,'$.analysis_stage'),
                            json_extract(j.payload_json,'$.analysis_phase')
                          )=?
                    """
                parameters.append(analysis_stage)
            if status:
                status_filter = " AND j.status=?"
                parameters.append(status)
            row = connection.execute(
                f"""
                SELECT j.*,s.submission_source FROM ai_jobs AS j
                JOIN ai_job_sources AS s ON s.job_id=j.job_id
                WHERE j.job_type='earnings_impact'
                  AND upper(json_extract(j.payload_json,'$.ticker'))=?
                  {report_filter}
                  AND COALESCE(j.completed_at,j.updated_at,j.created_at)>=?
                  {stage_filter}
                  {status_filter}
                ORDER BY j.created_at DESC,j.job_id DESC
                LIMIT 1
                """,
                tuple(parameters),
            ).fetchone()
            return self._decorate_earnings_row(connection, row)

    def prune_earnings_retention(
        self,
        *,
        now: datetime | None = None,
        retention_days: int = 30,
    ) -> int:
        """Delete terminal earnings inputs and outputs strictly before cutoff."""

        if isinstance(retention_days, bool) or retention_days < 1:
            raise ValueError("invalid_earnings_retention_days")
        self.ensure_initialized()
        observed = (now or _utcnow()).astimezone(timezone.utc)
        cutoff = _iso(observed - timedelta(days=retention_days))
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired_ids = [
                str(row["job_id"])
                for row in connection.execute(
                    """SELECT job_id FROM ai_jobs
                       WHERE job_type='earnings_impact'
                         AND status IN (
                           'completed','failed','cancelled',
                           'insufficient_context','budget_blocked'
                         )
                         AND COALESCE(completed_at,updated_at,created_at)<?""",
                    (cutoff,),
                ).fetchall()
            ]
            if not expired_ids:
                connection.commit()
                return 0
            placeholders = ",".join("?" for _ in expired_ids)
            params = tuple(expired_ids)
            connection.execute(
                f"DELETE FROM ai_job_sources WHERE job_id IN ({placeholders})",
                params,
            )
            connection.execute(
                f"DELETE FROM ai_earnings_final_locks "
                f"WHERE job_id IN ({placeholders})",
                params,
            )
            connection.execute(
                f"DELETE FROM ai_jobs WHERE job_id IN ({placeholders})",
                params,
            )
            connection.commit()
            return len(expired_ids)

    def prune_scheduled_history(
        self,
        *,
        retain_days: int,
        now: datetime | None = None,
    ) -> int:
        """Delete settled news and focus jobs older than ``retain_days``.

        新闻与焦点任务此前从不清理，reconcile 每轮把它们全量读进内存并重校验，
        开销随历史线性增长且全程持写锁（2026-09-25 审计）。只删终态行；创建与
        完成时间都要早于截止点，当天才结算的积压任务仍留在当日 token 账里。
        """

        if (
            isinstance(retain_days, bool)
            or not isinstance(retain_days, int)
            or retain_days < 1
        ):
            raise ValueError("invalid_scheduled_history_retain_days")
        self.ensure_initialized()
        observed = (now or _utcnow()).astimezone(timezone.utc)
        cutoff = _iso(observed - timedelta(days=retain_days))
        job_types = list(_SCHEDULED_HISTORY_JOB_TYPES)
        statuses = sorted(_TERMINAL)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired_ids = [
                str(row["job_id"])
                for row in connection.execute(
                    f"""SELECT job_id FROM ai_jobs
                        WHERE job_type IN ({",".join("?" for _ in job_types)})
                          AND status IN ({",".join("?" for _ in statuses)})
                          AND created_at<?
                          AND COALESCE(completed_at,updated_at,created_at)<?""",
                    (*job_types, *statuses, cutoff, cutoff),
                ).fetchall()
            ]
            # 首次清理可能是上万行，分批绑定参数，避开 SQLite 的变量个数上限。
            for offset in range(0, len(expired_ids), 500):
                chunk = expired_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in chunk)
                for table in (
                    "ai_job_sources",
                    "ai_job_batch_members",
                    "ai_jobs",
                ):
                    connection.execute(
                        f"DELETE FROM {table} WHERE job_id IN ({placeholders})",
                        chunk,
                    )
            connection.commit()
            return len(expired_ids)

    @staticmethod
    def _lane_occupants(
        connection: sqlite3.Connection,
        *,
        lane: str | None,
        now_dt: datetime,
        unknown_submission_hold_seconds: int,
        exclude_job_id: str | None = None,
    ) -> list[sqlite3.Row]:
        """Paid occupants of a lane, or all lanes when None.

        闸门、认领与快照三处共用这一份「车道被占」：已开始提交且仍在上游运行，
        或放行窗口内的 submission_outcome_unknown。三处手抄时认领漏了 unknown
        窗口，被挡住的后台任务每秒被认领、推迟两次，同一窗口里手动任务饿
        15 分钟（2026-09-25 审计）。缺来源记录的行按 scheduled 保守归类。
        """

        parameters = {
            "lane": lane,
            "exclude_job_id": exclude_job_id,
            "unknown_cutoff": _iso(
                now_dt
                - timedelta(seconds=max(1, int(unknown_submission_hold_seconds)))
            ),
            "unknown_no_response_cutoff": _iso(
                now_dt - timedelta(seconds=_UNKNOWN_NO_RESPONSE_HOLD_SECONDS)
            ),
        }
        lane_filter = """
              AND (:lane IS NULL
                   OR COALESCE(s.submission_source,'scheduled')=:lane)
              AND (:exclude_job_id IS NULL OR j.job_id<>:exclude_job_id)
        """
        in_flight = connection.execute(
            f"""
            SELECT j.*,s.submission_source FROM ai_jobs AS j
            LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id
            WHERE j.status IN ('pending','queued','in_progress')
              AND j.submission_started_at IS NOT NULL
              {lane_filter}
            ORDER BY j.created_at
            """,
            parameters,
        ).fetchall()
        # unknown 只由 fail() 写入，status 必为 failed；限定 status 让查询走
        # idx_ai_jobs_due，认领时不必扫描全表的 payload。
        unknown = connection.execute(
            f"""
            SELECT j.*,s.submission_source FROM ai_jobs AS j
            LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id
            WHERE j.status='failed'
              AND j.error_code='submission_outcome_unknown'
              AND j.submission_started_at IS NOT NULL
              AND (
                (j.openai_response_id IS NOT NULL
                 AND j.submission_started_at>=:unknown_cutoff)
                OR j.submission_started_at>=:unknown_no_response_cutoff
              )
              {lane_filter}
            ORDER BY j.created_at
            """,
            parameters,
        ).fetchall()
        return [*in_flight, *unknown]

    @classmethod
    def _lane_occupant(cls, connection: sqlite3.Connection, **kwargs: Any) -> sqlite3.Row | None:
        occupants = cls._lane_occupants(connection, **kwargs)
        return occupants[0] if occupants else None

    @staticmethod
    def _concurrency_limit(model: str | None, max_concurrency: int) -> int:
        if type(max_concurrency) is not int or not 1 <= max_concurrency <= 4:
            raise ValueError("max_concurrency is invalid")
        return max_concurrency

    def _submission_capacity(
        self, connection: sqlite3.Connection, *, model: str | None, lane: str | None,
        max_concurrency: int, now_dt: datetime, unknown_submission_hold_seconds: int,
        exclude_job_id: str | None = None,
    ) -> tuple[list[sqlite3.Row], int]:
        limit = self._concurrency_limit(model, max_concurrency)
        occupants = self._lane_occupants(
            connection, lane=None,
            now_dt=now_dt, unknown_submission_hold_seconds=unknown_submission_hold_seconds,
            exclude_job_id=exclude_job_id,
        )
        return occupants, limit

    @staticmethod
    def _provider_slot_available(model: str | None, occupants: list[sqlite3.Row]) -> bool:
        # Both OpenAI models share one provider slot inside the global cap.
        # Claude jobs may use the remaining slots, including across lanes.
        return model is None or _uses_claude(model) or not any(
            not _uses_claude(row["model"]) for row in occupants
        )

    @staticmethod
    def _manual_cooldown_until(
        connection: sqlite3.Connection,
        *,
        now_dt: datetime,
        cooldown_seconds: int,
        exclude_job_id: str | None = None,
    ) -> datetime | None:
        """End of the manual lane's cooldown after its latest paid job.

        冷却只约束手动来源、只看手动车道：后台批任务刚结束不能让 owner 的点击
        等 30 秒，后台车道每单也不该多等 30 秒（2026-09-25 审计）。
        """

        if int(cooldown_seconds) <= 0:
            return None
        row = connection.execute(
            """
            SELECT MAX(COALESCE(j.completed_at,j.submission_started_at))
            FROM ai_job_sources AS s
            CROSS JOIN ai_jobs AS j ON j.job_id=s.job_id
            WHERE s.submission_source='manual'
              AND j.submission_started_at IS NOT NULL
              AND j.status IN ('completed','failed','cancelled',
                               'insufficient_context','budget_blocked')
              AND (?1 IS NULL OR j.job_id<>?1)
            """,
            (exclude_job_id,),
        ).fetchone()
        latest_at = _parse_time(row[0]) if row is not None else None
        if latest_at is None:
            return None
        cooldown_until = latest_at + timedelta(seconds=int(cooldown_seconds))
        return cooldown_until if cooldown_until > now_dt else None

    @staticmethod
    def _credit_hold_until(
        connection: sqlite3.Connection,
        *,
        now_dt: datetime,
    ) -> datetime | None:
        row = connection.execute(
            """SELECT MAX(completed_at) FROM ai_jobs
               WHERE status='failed' AND error_code='provider_credit_exhausted'
                 AND completed_at>=?""",
            (
                _iso(
                    now_dt
                    - timedelta(seconds=_CREDIT_EXHAUSTED_HOLD_SECONDS)
                ),
            ),
        ).fetchone()
        failed_at = _parse_time(row[0]) if row is not None else None
        if failed_at is None:
            return None
        hold_until = failed_at + timedelta(seconds=_CREDIT_EXHAUSTED_HOLD_SECONDS)
        return hold_until if hold_until > now_dt else None

    def _lane_submission_block(
        self,
        connection: sqlite3.Connection,
        *,
        lane: str,
        now_dt: datetime,
        cooldown_seconds: int,
        unknown_submission_hold_seconds: int,
        exclude_job_id: str | None = None,
        check_credit_hold: bool = True,
        model: str | None = None,
        max_concurrency: int = 1,
    ) -> tuple[str, datetime | None] | None:
        """Why the model and lane cannot start another paid submission.

        The credit hold is account-wide; a caller that already checked it in
        the same transaction may skip the scan with ``check_credit_hold``.
        """

        if check_credit_hold:
            credit_hold_until = self._credit_hold_until(connection, now_dt=now_dt)
            if credit_hold_until is not None:
                return _CREDIT_EXHAUSTED_HOLD_ERROR, credit_hold_until
        occupants, limit = self._submission_capacity(
            connection, model=model, lane=lane, max_concurrency=max_concurrency,
            now_dt=now_dt, unknown_submission_hold_seconds=unknown_submission_hold_seconds,
            exclude_job_id=exclude_job_id,
        )
        if len(occupants) >= limit or not self._provider_slot_available(model, occupants):
            return "concurrency_limit", None
        if lane == "manual":
            cooldown_until = self._manual_cooldown_until(
                connection,
                now_dt=now_dt,
                cooldown_seconds=cooldown_seconds,
                exclude_job_id=exclude_job_id,
            )
            if cooldown_until is not None:
                return "cooldown", cooldown_until
        return None

    @staticmethod
    def _next_due_candidate(
        connection: sqlite3.Connection,
        *,
        now: str,
        blocked_scopes: set[tuple[str | None, str | None]],
    ) -> sqlite3.Row | None:
        filters: list[str] = []
        parameters: list[str] = []
        for model, lane in sorted(blocked_scopes, key=lambda scope: (scope[0] or "", scope[1] or "")):
            parts: list[str] = []
            if model is not None:
                parts.append("j.model=?")
                parameters.append(model)
            if lane is not None:
                parts.append("COALESCE(s.submission_source,'scheduled')=?")
                parameters.append(lane)
            filters.append("(" + " AND ".join(parts) + ")")
        lane_filter = ""
        if filters:
            lane_filter = """
              AND (
                j.submission_started_at IS NOT NULL
                OR j.openai_response_id IS NOT NULL
                OR j.cancel_requested_at IS NOT NULL
                OR NOT (""" + " OR ".join(filters) + ") )"
        return connection.execute(
            f"""
            SELECT j.job_id,j.model,j.submission_started_at,j.openai_response_id,
                   j.cancel_requested_at,
                   COALESCE(s.submission_source,'scheduled') AS lane
            FROM ai_jobs AS j
            LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id
            WHERE j.status IN ('pending','queued','in_progress')
              AND (j.next_attempt_at IS NULL OR j.next_attempt_at<=?)
              AND (j.lease_expires_at IS NULL OR j.lease_expires_at<=?)
              {lane_filter}
            ORDER BY
              CASE WHEN j.cancel_requested_at IS NOT NULL THEN 0 ELSE 1 END,
              CASE
                WHEN j.openai_response_id IS NOT NULL THEN 0
                WHEN j.submission_started_at IS NOT NULL THEN 1
                ELSE 2
              END,
              j.priority DESC,
              j.created_at
            LIMIT 1
            """,
            (now, now, *parameters),
        ).fetchone()

    @contextmanager
    def _connect_read_only(self) -> Iterator[sqlite3.Connection]:
        uri = self.path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    def active_queue_signature(self) -> tuple[int, str | None] | None:
        """Changes when an active job is added, updated, cancelled or settled.

        The worker polls this to wake its idle AI loop; it is read-only and
        served by ``idx_ai_jobs_due``.
        """

        if not self.path.is_file():
            return None
        with self._connect_read_only() as connection:
            row = connection.execute(
                """SELECT COUNT(*),MAX(updated_at) FROM ai_jobs
                   WHERE status IN ('pending','queued','in_progress')"""
            ).fetchone()
        return int(row[0]), row[1]

    def next_due_delay(self, *, now: datetime | None = None) -> float | None:
        """Seconds until the earliest active job is due and unleased.

        ``0.0`` means a job is due now; ``None`` means nothing is active.
        """

        if not self.path.is_file():
            return None
        observed = now or _utcnow()
        with self._connect_read_only() as connection:
            rows = connection.execute(
                """SELECT next_attempt_at,lease_expires_at FROM ai_jobs
                   WHERE status IN ('pending','queued','in_progress')"""
            ).fetchall()
        delays: list[float] = []
        for row in rows:
            moments = [
                moment
                for moment in (
                    _parse_time(row["next_attempt_at"]),
                    _parse_time(row["lease_expires_at"]),
                )
                if moment is not None
            ]
            delays.append(
                max(0.0, (max(moments) - observed).total_seconds())
                if moments
                else 0.0
            )
        return min(delays) if delays else None

    def claim_due(
        self,
        owner: str,
        lease_seconds: int,
        *,
        cooldown_seconds: int = 0,
        unknown_submission_hold_seconds: int = 86400,
        max_concurrency: int = 1,
    ) -> dict[str, Any] | None:
        self._concurrency_limit(_CLAUDE_MODEL, max_concurrency)
        self.ensure_initialized()
        now_dt = _utcnow()
        now = _iso(now_dt)
        lease_expires = _iso(now_dt + timedelta(seconds=lease_seconds))
        with self._connect() as connection:
            # 空队列不拿写锁：先用只读查询看有没有到期且未被租走的任务。worker
            # 空闲时每轮都会走到这里，原先每次都开一个空的 BEGIN IMMEDIATE，
            # 与后端入队、取消抢 ai-jobs.db 的写锁。
            if self._next_due_candidate(
                connection,
                now=now,
                blocked_scopes=set(),
            ) is None:
                return None
            connection.execute("BEGIN IMMEDIATE")
            # 不可提交车道上尚未提交的任务直接跳过，不再「认领、构造请求、过
            # 闸门、推迟两秒」地空转：那样每秒两个写事务，还让被挡住的车道
            # 抢走另一条空闲车道的认领（2026-09-25 审计）。已提交、已有响应
            # 或待取消的任务不受车道限制，照常认领。认领不到就返回 None，
            # worker 走空闲间隔。
            blocked_scopes: set[tuple[str | None, str | None]] = set()
            row: sqlite3.Row | None = None
            while True:
                candidate = self._next_due_candidate(
                    connection,
                    now=now,
                    blocked_scopes=blocked_scopes,
                )
                if candidate is None or (
                    candidate["submission_started_at"] is not None
                    or candidate["openai_response_id"] is not None
                    or candidate["cancel_requested_at"] is not None
                ):
                    row = candidate
                    break
                lane = str(candidate["lane"])
                block = self._lane_submission_block(
                    connection,
                    lane=lane,
                    now_dt=now_dt,
                    cooldown_seconds=cooldown_seconds,
                    unknown_submission_hold_seconds=(
                        unknown_submission_hold_seconds
                    ),
                    check_credit_hold=not blocked_scopes,
                    model=str(candidate["model"]),
                    max_concurrency=max_concurrency,
                )
                if block is None:
                    row = candidate
                    break
                # 候选按「已提交、已有响应」优先排序：排在最前的是未提交任务，
                # 说明此刻没有要轮询的任务。欠费暂停对两条车道都生效，直接收手。
                if block[0] == _CREDIT_EXHAUSTED_HOLD_ERROR:
                    break
                if block[0] == "cooldown":
                    blocked_scopes.add((None, lane))
                else:
                    blocked_scopes.add((str(candidate["model"]), None))
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
        shared_daily_budget_usd: float = 0,
        shared_budget_start_at: datetime | None = None,
        shared_budget_enforce_limit: bool = True,
        daily_token_limit: int = 10_000_000,
        cooldown_seconds: int = 0,
        unknown_submission_hold_seconds: int = 86400,
        max_concurrency: int = 1,
    ) -> str:
        """Atomically enforce paid concurrency, holds, and daily token usage.

        Claude shares one limit across manual and scheduled work, including
        running legacy jobs. OpenAI retains one paid slot per source lane.
        Unknown submissions retain capacity for the existing recovery window;
        they are terminal and never retried. Claiming and capacity snapshots
        use the same occupancy predicate. Cooldown still affects manual work.

        The former count and dollar arguments remain API-compatible but do not
        block work. The separately configured shared dollar budget replaces
        the token gate only for Haiku. Both models reserve against indexed
        charges under this same SQLite write transaction.
        """

        del daily_limit, daily_budget_usd
        shared_limit = usd_to_microusd(shared_daily_budget_usd)
        self._concurrency_limit(_CLAUDE_MODEL, max_concurrency)
        if not 102_400 <= int(daily_token_limit) <= 100_000_000:
            raise ValueError("daily_token_limit is invalid")
        now_dt = _utcnow()
        now = _iso(now_dt)
        day_start = _iso(now_dt.replace(hour=0, minute=0, second=0, microsecond=0))
        day_end = _iso(
            now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
            + timedelta(days=1)
        )
        token_limit = int(daily_token_limit)
        if shared_limit > 0:
            SharedModelBudget(
                self.path, shared_daily_budget_usd, self.path.parent / "market-brief",
                accounting_start_at=shared_budget_start_at,
                enforce_limit=shared_budget_enforce_limit,
            ).bootstrap_brief_history(now_dt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT j.job_type,j.model,j.status,j.lease_owner,j.submission_started_at,
                          COALESCE(s.submission_source,'scheduled') AS lane
                   FROM ai_jobs AS j
                   LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            if (
                row is None
                or row["status"] != "pending"
                or row["lease_owner"] != owner
                or row["submission_started_at"] is not None
            ):
                connection.rollback()
                raise RuntimeError("ai_job_not_submittable")
            lane = str(row["lane"])
            reservation_microusd = _task_budget_reservation_microusd(
                str(row["job_type"]), model=str(row["model"])
            )
            token_reservation = _task_token_reservation(
                str(row["job_type"]), model=str(row["model"])
            )
            block = self._lane_submission_block(
                connection,
                lane=lane,
                now_dt=now_dt,
                cooldown_seconds=cooldown_seconds,
                unknown_submission_hold_seconds=unknown_submission_hold_seconds,
                exclude_job_id=job_id,
                model=str(row["model"]),
                max_concurrency=max_concurrency,
            )
            if block is not None:
                verdict, retry_at = block
                # 推迟而不是终态：任务留在队列里，不消耗调度器的重试次数。
                error_code = {
                    "concurrency_limit": "global_concurrency_limit",
                    "cooldown": "analysis_cooldown_active",
                }.get(verdict, verdict)
                connection.execute(
                    """
                    UPDATE ai_jobs
                    SET next_attempt_at=?,error_code=?,
                        lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                    WHERE job_id=? AND lease_owner=? AND status='pending'
                    """,
                    (
                        _iso(retry_at or now_dt + timedelta(seconds=2)),
                        error_code,
                        now,
                        job_id,
                        owner,
                    ),
                )
                connection.commit()
                return verdict
            shared_budget_enabled = shared_limit > 0 and row["model"] in JOB_MODELS
            if shared_budget_enabled and not can_reserve_in_transaction(
                connection, daily_budget_microusd=shared_limit,
                reservation_microusd=reservation_microusd, now=now_dt,
                accounting_start_at=shared_budget_start_at,
                enforce_limit=shared_budget_enforce_limit,
            ):
                connection.execute(
                    """UPDATE ai_jobs SET status='budget_blocked',
                           error_code='daily_budget_usd_reached',completed_at=?,
                           next_attempt_at=NULL,lease_owner=NULL,lease_expires_at=NULL,
                           updated_at=? WHERE job_id=? AND lease_owner=? AND status='pending'""",
                    (now, now, job_id, owner),
                )
                connection.commit()
                return "daily_budget_usd_reached"
            token_rows = connection.execute(
                """SELECT job_type,model,submission_started_at,status,error_code,openai_response_id,
                          usage_total_tokens
                   FROM ai_jobs
                   WHERE submission_started_at>=? AND submission_started_at<?""",
                (day_start, day_end),
            ).fetchall()
            tokens_used = _daily_tokens_used(token_rows)
            if not shared_budget_enabled and tokens_used + token_reservation > token_limit:
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

    @staticmethod
    def _provider_receipt_json(receipt: dict[str, Any]) -> str:
        fields = {"provider", "model", "id", "output_text", "stop_reason", "terminal_error", "usage"}
        if (not isinstance(receipt, dict) or not fields <= set(receipt)
                or set(receipt) - fields - {"evidence_sources", "tool_evidence", "tool_evidence_version", "request_ids", "rounds", "openai_web_calls"}):
            raise ValueError("ai_job_provider_receipt_invalid")
        if not ((receipt["provider"] == "anthropic" and _uses_claude(receipt["model"]))
                or (receipt["provider"] == "openai" and receipt["model"] == "gpt-5.6-luna")):
            raise ValueError("ai_job_provider_receipt_invalid")
        if receipt["provider"] == "openai":
            calls = receipt.get("openai_web_calls")
            if not isinstance(calls, list) or len(calls) > 100:
                raise ValueError("ai_job_provider_web_calls_invalid")
            for call in calls:
                if (not isinstance(call, dict) or set(call) != {"id", "action", "status", "sources"}
                        or not isinstance(call["id"], str) or len(call["id"]) > 256
                        or not isinstance(call["sources"], list) or len(call["sources"]) > 50):
                    raise ValueError("ai_job_provider_web_calls_invalid")
            verified = [source for call in calls if call["status"] == "completed"
                        and call["action"] in {"search", "open_page", "find_in_page"}
                        for source in call["sources"]]
            if any(source not in verified for source in receipt.get("evidence_sources", [])):
                raise ValueError("ai_job_provider_sources_invalid")
        elif "openai_web_calls" in receipt:
            raise ValueError("ai_job_provider_receipt_invalid")
        if not isinstance(receipt["id"], str) or not 1 <= len(receipt["id"]) <= 256:
            raise ValueError("ai_job_provider_receipt_invalid")
        if not isinstance(receipt["output_text"], str):
            raise ValueError("ai_job_provider_receipt_invalid")
        if not isinstance(receipt["stop_reason"], str) or not 1 <= len(receipt["stop_reason"]) <= 120:
            raise ValueError("ai_job_provider_receipt_invalid")
        error = receipt["terminal_error"]
        if error is not None and (not isinstance(error, str) or not 1 <= len(error) <= 120):
            raise ValueError("ai_job_provider_receipt_invalid")
        sources = receipt.get("evidence_sources", [])
        if not isinstance(sources, list) or len(sources) > 10:
            raise ValueError("ai_job_provider_sources_invalid")
        for source in sources:
            if (not isinstance(source, dict) or set(source) != {"title", "url", "type"}
                    or not isinstance(source["type"], str)
                    or source["type"] not in {"web_search", "web_fetch"}
                    or not isinstance(source["title"], str) or len(source["title"]) > 512
                    or any(ord(char) < 32 for char in source["title"])
                    or not _public_evidence_url(source["url"])):
                raise ValueError("ai_job_provider_sources_invalid")
        if "tool_evidence" in receipt or "tool_evidence_version" in receipt:
            evidence = receipt.get("tool_evidence")
            if (receipt.get("tool_evidence_version") != "v1"
                    or not isinstance(evidence, list) or len(evidence) > 512):
                raise ValueError("ai_job_provider_tool_evidence_invalid")
            evidence_fields = {"tool_use_id", "tool_name", "status", "url", "title", "content_sha256"}
            for item in evidence:
                if (not isinstance(item, dict) or set(item) != evidence_fields
                        or not isinstance(item["tool_use_id"], str) or not 1 <= len(item["tool_use_id"]) <= 256
                        or not isinstance(item["tool_name"], str)
                        or item["tool_name"] not in {"web_search", "web_fetch"}
                        or item["status"] != "success"
                        or not _public_evidence_url(item["url"])
                        or not isinstance(item["title"], str) or len(item["title"]) > 512
                        or any(ord(char) < 32 for char in item["title"])
                        or not isinstance(item["content_sha256"], str)
                        or len(item["content_sha256"]) != 64
                        or any(char not in "0123456789abcdef" for char in item["content_sha256"])):
                    raise ValueError("ai_job_provider_tool_evidence_invalid")
        usage = receipt["usage"]
        usage_fields = {"input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens", *_CACHE_USAGE_FIELDS}
        if (not isinstance(usage, dict) or not usage_fields <= set(usage)
                or set(usage) - usage_fields - set(_TOOL_USAGE_FIELDS)):
            raise ValueError("ai_job_provider_usage_invalid")
        for field, value in usage.items():
            if value is None:
                continue
            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                raise ValueError("ai_job_provider_usage_invalid")
        creation = usage["cache_creation_input_tokens"] or 0
        writes = (usage["cache_creation_5m_input_tokens"] or 0) + (usage["cache_creation_1h_input_tokens"] or 0)
        input_tokens, output_tokens, total_tokens = (
            usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]
        )
        complete_totals = input_tokens is not None and output_tokens is not None
        if ((input_tokens is not None and (usage["cached_input_tokens"] or 0) + creation > input_tokens)
                or (usage["cache_creation_input_tokens"] is not None and writes > creation)
                or (not complete_totals and total_tokens is not None)
                or (complete_totals and total_tokens is not None and total_tokens != input_tokens + output_tokens)
                or (output_tokens is not None and (usage["reasoning_tokens"] or 0) > output_tokens)):
            raise ValueError("ai_job_provider_usage_invalid")
        if "request_ids" in receipt or "rounds" in receipt:
            AIJobRepository._validate_provider_rounds(receipt, complete=True)
        value = canonical_json_text({**receipt, "evidence_sources": sources})
        if len(value.encode("utf-8")) > 2 * _MAX_RESULT_JSON_BYTES:
            raise ValueError("ai_job_provider_receipt_too_large")
        return value

    @staticmethod
    def _validate_provider_rounds(value: dict[str, Any], *, complete: bool) -> None:
        from app.services.ai_jobs.claude_provider import sum_round_usage

        request_ids, rounds = value.get("request_ids"), value.get("rounds")
        if (not isinstance(request_ids, list) or not 1 <= len(request_ids) <= 4
                or any(not isinstance(item, str) or not 1 <= len(item) <= 256 for item in request_ids)
                or len(set(request_ids)) != len(request_ids) or request_ids[0] != value["id"]
                or not isinstance(rounds, list) or len(rounds) > len(request_ids)
                or len(request_ids) - len(rounds) > (0 if complete else 1)):
            raise ValueError("ai_job_provider_rounds_invalid")
        for index, item in enumerate(rounds):
            if (not isinstance(item, dict) or set(item) != {"id", "stop_reason", "usage"}
                    or item["id"] != request_ids[index]
                    or (index < len(request_ids) - 1 and item["stop_reason"] != "pause_turn")):
                raise ValueError("ai_job_provider_rounds_invalid")
            # Reuse the same usage shape/invariants as a terminal receipt, but
            # never include nested rounds or untrusted output in this check.
            AIJobRepository._provider_receipt_json({
                "provider": "anthropic", "model": value["model"], "id": item["id"],
                "output_text": "", "stop_reason": item["stop_reason"],
                "terminal_error": None, "usage": item["usage"],
            })
        if value.get("usage" if complete else "confirmed_usage") != sum_round_usage(rounds):
            raise ValueError("ai_job_provider_round_usage_mismatch")
        if complete and (not rounds or value["stop_reason"] != rounds[-1]["stop_reason"]):
            raise ValueError("ai_job_provider_rounds_invalid")

    @staticmethod
    def _provider_progress_json(progress: dict[str, Any]) -> str:
        fields = {"provider", "model", "id", "request_ids", "rounds", "confirmed_usage"}
        if (not isinstance(progress, dict) or set(progress) != fields
                or progress["provider"] != "anthropic" or not _uses_claude(progress["model"])):
            raise ValueError("ai_job_provider_progress_invalid")
        AIJobRepository._validate_provider_rounds(progress, complete=False)
        value = canonical_json_text(progress)
        if len(value.encode("utf-8")) > 2 * _MAX_RESULT_JSON_BYTES:
            raise ValueError("ai_job_provider_progress_too_large")
        return value

    @staticmethod
    def _progress_extends(previous: dict[str, Any], current: dict[str, Any]) -> bool:
        return (all(previous[key] == current[key] for key in ("provider", "model", "id"))
                and current["request_ids"][:len(previous["request_ids"])] == previous["request_ids"]
                and current["rounds"][:len(previous["rounds"])] == previous["rounds"])

    def record_provider_progress(
        self, job_id: str, owner: str, progress: dict[str, Any], *,
        will_continue: bool = False, shared_daily_budget_usd: float = 0,
        shared_budget_enforce_limit: bool = True,
        shared_budget_start_at: datetime | None = None,
    ) -> None:
        """Persist known rounds and hold paid usage plus the next paid request.

        Repeated progress writes are idempotent, and holds never shrink until
        a final receipt settles the complete usage. Progress is not permission
        to resume after a crash: the job remains durably submitted.
        """
        if type(will_continue) is not bool:
            raise ValueError("will_continue must be boolean")
        progress_json = self._provider_progress_json(progress)
        shared_limit = usd_to_microusd(shared_daily_budget_usd)
        if will_continue and (
            not progress["rounds"] or progress["rounds"][-1]["stop_reason"] != "pause_turn"
            or len(progress["rounds"]) != len(progress["request_ids"])
            or len(progress["rounds"]) >= 4
            or any(progress["confirmed_usage"].get(key) is None for key in (
                "input_tokens", "cached_input_tokens", "cache_creation_input_tokens",
                "output_tokens", "web_search_requests", "web_fetch_requests",
            ))
        ):
            raise ValueError("ai_job_provider_continuation_invalid")
        self.ensure_initialized()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _iso()
            row = connection.execute(
                """SELECT * FROM ai_jobs WHERE job_id=? AND lease_owner=? AND lease_expires_at>?
                     AND model=? AND anthropic_message_id=? AND openai_response_id IS NULL
                     AND submission_started_at IS NOT NULL AND status IN ('queued','in_progress')
                     AND provider_result_json IS NULL""",
                (job_id, owner, now, progress["model"], progress["id"]),
            ).fetchone()
            if row is None:
                raise RuntimeError("ai_job_provider_progress_rejected")
            previous = connection.execute(
                "SELECT progress_json FROM ai_job_provider_progress WHERE job_id=?", (job_id,),
            ).fetchone()
            if previous is not None and not self._progress_extends(json.loads(previous[0]), progress):
                raise RuntimeError("ai_job_provider_progress_conflict")
            held = int(row["budget_charge_microusd"] or 0)
            confirmed = _settled_budget_charge_microusd(
                str(row["job_type"]), progress["confirmed_usage"],
                fallback_microusd=held, model=str(row["model"]),
            )
            needed = confirmed + (
                _task_budget_reservation_microusd(str(row["job_type"]), model=str(row["model"]))
                if will_continue else 0
            )
            charge = max(held, needed)
            if will_continue and shared_limit > 0 and not can_reserve_in_transaction(
                connection, daily_budget_microusd=shared_limit,
                reservation_microusd=charge - held, now=_parse_time(now),
                accounting_start_at=shared_budget_start_at,
                enforce_limit=shared_budget_enforce_limit,
            ):
                raise RuntimeError("daily_budget_usd_reached")
            connection.execute(
                "UPDATE ai_jobs SET budget_charge_microusd=?,updated_at=? WHERE job_id=?",
                (charge, now, job_id),
            )
            connection.execute(
                """INSERT INTO ai_job_provider_progress(job_id,progress_json,updated_at) VALUES(?,?,?)
                   ON CONFLICT(job_id) DO UPDATE SET progress_json=excluded.progress_json,updated_at=excluded.updated_at""",
                (job_id, progress_json, now),
            )
            connection.commit()

    def get_provider_progress(self, job_id: str) -> dict[str, Any] | None:
        self.ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                """SELECT p.progress_json,j.model,j.anthropic_message_id
                   FROM ai_job_provider_progress p JOIN ai_jobs j ON j.job_id=p.job_id WHERE p.job_id=?""",
                (job_id,),
            ).fetchone()
        if row is None:
            return None
        progress = json.loads(row["progress_json"])
        self._provider_progress_json(progress)
        if progress["model"] != row["model"] or progress["id"] != row["anthropic_message_id"]:
            raise ValueError("ai_job_provider_progress_identity_mismatch")
        return progress

    def link_anthropic_message(self, job_id: str, owner: str, message_id: str) -> None:
        if not isinstance(message_id, str) or not 1 <= len(message_id) <= 256:
            raise ValueError("ai_job_provider_message_id_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _iso()
            updated = connection.execute(
                """UPDATE ai_jobs SET anthropic_message_id=?,updated_at=?
                   WHERE job_id=? AND lease_owner=? AND lease_expires_at>?
                     AND model IN (?,?) AND submission_started_at IS NOT NULL
                     AND status IN ('queued','in_progress') AND openai_response_id IS NULL
                     AND (anthropic_message_id IS NULL OR anthropic_message_id=?)""",
                (message_id, now, job_id, owner, now, _CLAUDE_MODEL, "claude-sonnet-5-5", message_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("ai_job_response_link_rejected")
            connection.commit()

    def record_openai_result(self, job_id: str, owner: str, receipt: dict[str, Any]) -> None:
        """Persist Responses evidence before publication; keep its paid identity."""
        receipt_json = self._provider_receipt_json(receipt)
        if receipt["provider"] != "openai":
            raise ValueError("ai_job_provider_receipt_invalid")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM ai_jobs WHERE job_id=? AND lease_owner=?
                   AND lease_expires_at>? AND status IN ('queued','in_progress')
                   AND openai_response_id=? AND model=?""",
                (job_id, owner, _iso(), receipt["id"], receipt["model"]),
            ).fetchone()
            if row is None:
                raise RuntimeError("ai_job_lease_lost")
            if row["provider_result_json"] is not None:
                if self._provider_receipt_json(json.loads(row["provider_result_json"])) != receipt_json:
                    raise RuntimeError("ai_job_provider_result_conflict")
                return
            charge = _settled_budget_charge_microusd(
                str(row["job_type"]), receipt["usage"], model=str(row["model"]),
                fallback_microusd=int(row["budget_charge_microusd"] or 0),
            )
            connection.execute(
                "UPDATE ai_jobs SET provider_result_json=?,budget_charge_microusd=?,updated_at=? WHERE job_id=?",
                (receipt_json, charge, _iso(), job_id),
            )
            connection.commit()

    def record_provider_result(self, job_id: str, owner: str, receipt: dict[str, Any]) -> None:
        """Save the terminal paid result and its accounting before local publication."""
        receipt_json = self._provider_receipt_json(receipt)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            now = _iso()
            row = connection.execute(
                """SELECT * FROM ai_jobs WHERE job_id=? AND lease_owner=?
                     AND lease_expires_at>? AND status IN ('queued','in_progress')
                     AND submission_started_at IS NOT NULL AND model=?
                     AND openai_response_id IS NULL""",
                (job_id, owner, now, receipt["model"]),
            ).fetchone()
            if row is None:
                raise RuntimeError("ai_job_lease_lost")
            if row["anthropic_message_id"] not in (None, receipt["id"]):
                raise RuntimeError("ai_job_provider_result_conflict")
            progress = connection.execute(
                "SELECT progress_json FROM ai_job_provider_progress WHERE job_id=?", (job_id,),
            ).fetchone()
            if progress is not None and (
                "request_ids" not in receipt
                or not self._progress_extends(json.loads(progress[0]), receipt)
            ):
                raise RuntimeError("ai_job_provider_result_conflict")
            if row["provider_result_json"] is not None:
                if self._provider_receipt_json(json.loads(row["provider_result_json"])) != receipt_json:
                    raise RuntimeError("ai_job_provider_result_conflict")
                connection.commit()
                return
            usage = receipt["usage"]
            charge = _settled_budget_charge_microusd(
                str(row["job_type"]), usage,
                fallback_microusd=int(row["budget_charge_microusd"] or 0),
                model=str(row["model"]),
            )
            connection.execute(
                """UPDATE ai_jobs SET anthropic_message_id=?,provider_result_json=?,
                     usage_input_tokens=?,usage_cached_input_tokens=?,
                     usage_cache_creation_input_tokens=?,usage_cache_creation_5m_input_tokens=?,
                     usage_cache_creation_1h_input_tokens=?,usage_output_tokens=?,
                     usage_reasoning_tokens=?,usage_total_tokens=?,budget_charge_microusd=?,updated_at=?
                   WHERE job_id=? AND lease_owner=?""",
                (receipt["id"], receipt_json, usage["input_tokens"], usage["cached_input_tokens"],
                 *(usage.get(field) for field in _CACHE_USAGE_FIELDS), usage["output_tokens"],
                 usage["reasoning_tokens"], usage["total_tokens"], charge, now, job_id, owner),
            )
            connection.commit()

    def get_provider_result(self, job_id: str) -> dict[str, Any] | None:
        self.ensure_initialized()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT model,anthropic_message_id,openai_response_id,provider_result_json FROM ai_jobs WHERE job_id=?", (job_id,),
            ).fetchone()
        if not row or not row["provider_result_json"]:
            return None
        receipt = json.loads(row["provider_result_json"])
        self._provider_receipt_json(receipt)
        if receipt["model"] != row["model"] or receipt["id"] != row["anthropic_message_id" if receipt["provider"] == "anthropic" else "openai_response_id"]:
            raise ValueError("ai_job_provider_result_identity_mismatch")
        receipt.setdefault("evidence_sources", [])
        return receipt

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
                """SELECT job_type,model,provider_result_json,payload_json,budget_charge_microusd FROM ai_jobs
                   WHERE job_id=? AND lease_owner=?""",
                (job_id, owner),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise RuntimeError("ai_job_completion_rejected")
            if current["provider_result_json"]:
                usage = json.loads(current["provider_result_json"])["usage"]
            budget_charge_microusd = _settled_budget_charge_microusd(
                str(current["job_type"]),
                usage,
                fallback_microusd=int(current["budget_charge_microusd"] or 0),
                model=str(current["model"]),
            )
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='completed', result_json=?, completed_at=?,
                    usage_input_tokens=?, usage_cached_input_tokens=?,
                    usage_cache_creation_input_tokens=?,
                    usage_cache_creation_5m_input_tokens=?,
                    usage_cache_creation_1h_input_tokens=?,
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
                    *(usage.get(field) for field in _CACHE_USAGE_FIELDS),
                    usage.get("output_tokens"),
                    usage.get("reasoning_tokens"),
                    usage.get("total_tokens"),
                    budget_charge_microusd,
                    now,
                    job_id,
                    owner,
                ),
            ).rowcount
            if updated == 1 and current["job_type"] == "earnings_impact":
                try:
                    earnings_payload = json.loads(
                        str(current["payload_json"] or "{}")
                    )
                except (TypeError, json.JSONDecodeError):
                    earnings_payload = {}
                report_id, ticker, report_date = self._earnings_identity(
                    earnings_payload
                )
                has_actual = (
                    earnings_payload.get("eps_actual") is not None
                    or earnings_payload.get("revenue_actual") is not None
                )
                has_comparison = (
                    earnings_payload.get("eps_actual") is not None
                    and earnings_payload.get("eps_estimate") is not None
                ) or (
                    earnings_payload.get("revenue_actual") is not None
                    and earnings_payload.get("revenue_estimate") is not None
                )
                if (
                    report_id
                    and ticker
                    and report_date
                    and has_actual
                    and has_comparison
                    and self._final_stage(earnings_payload)
                ):
                    connection.execute(
                        """INSERT INTO ai_earnings_final_locks(
                               report_id,ticker,earnings_date,report_year,
                               report_quarter,job_id,locked_at
                           ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            report_id,
                            ticker,
                            report_date,
                            earnings_payload.get("year"),
                            earnings_payload.get("quarter"),
                            job_id,
                            now,
                        ),
                    )
            connection.commit()
            if updated != 1:
                raise RuntimeError("ai_job_completion_rejected")

    def recover_schema_validation_failure(
        self,
        job_id: str,
        response_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any]:
        """Publish an already-paid result the local side failed to keep.

        Covers validation false positives and local write failures that were
        recorded as terminal after the provider accepted the job
        (RECOVERABLE_FAILURE_CODES). This transition is deliberately narrower
        than a retry: the failed job must still carry the exact durable
        provider response identity, and the retrieved result must pass the
        current validator for the stored input. Existing usage and budget
        accounting are preserved unchanged.
        """

        if not response_id:
            raise ValueError("ai_job_recovery_response_id_required")
        self.ensure_initialized()
        current = self.get_job(job_id)
        if current is None:
            raise RuntimeError("ai_job_recovery_not_found")
        claude_result = _uses_claude(current.get("model"))
        stored_response_id = current.get(
            "anthropic_message_id" if claude_result else "openai_response_id"
        )
        if (
            current.get("status") != "failed"
            or current.get("error_code") not in RECOVERABLE_FAILURE_CODES
            or current.get("result_json") is not None
            or stored_response_id != response_id
        ):
            raise RuntimeError("ai_job_recovery_rejected")
        try:
            payload = json.loads(str(current["payload_json"]))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise RuntimeError("ai_job_recovery_payload_invalid") from exc
        validated = validate_result(
            str(current["job_type"]),
            json.dumps(
                result,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ),
            payload,
        )
        if claude_result or current.get("provider_result_json"):
            receipt = self.get_provider_result(job_id)
            if receipt is None or receipt.get("terminal_error"):
                raise RuntimeError("ai_job_recovery_rejected")
            from app.services.ai_jobs.runtime import receipt_result
            original_result = receipt_result(receipt, str(current["job_type"]), payload)
            if validated != original_result:
                raise RuntimeError("ai_job_recovery_result_mismatch")
            if current["job_type"] == "market_focus" and payload.get("verification_version") == "web-evidence-v1":
                from app.services.ai_jobs.models import validate_market_focus_evidence

                validate_market_focus_evidence(validated, payload, receipt.get("tool_evidence", []))
        result_json = self._canonical_result(validated)
        now = _iso()
        recoverable_codes = sorted(RECOVERABLE_FAILURE_CODES)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            updated = connection.execute(
                f"""
                UPDATE ai_jobs
                SET status='completed',result_json=?,error_code=NULL,
                    error_detail=NULL,
                    completed_at=COALESCE(completed_at,?),next_attempt_at=NULL,
                    lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE job_id=? AND status='failed'
                  AND error_code IN ({",".join("?" for _ in recoverable_codes)})
                  AND result_json IS NULL
                  AND {"anthropic_message_id" if claude_result else "openai_response_id"}=?
                """,
                (result_json, now, now, job_id, *recoverable_codes, response_id),
            ).rowcount
            if updated != 1:
                connection.rollback()
                raise RuntimeError("ai_job_recovery_rejected")
            report_id, ticker, report_date = self._earnings_identity(payload)
            if (
                current.get("job_type") == "earnings_impact"
                and self._final_stage(payload)
                and report_id
                and ticker
                and report_date
                and (
                    (
                        payload.get("eps_actual") is not None
                        and payload.get("eps_estimate") is not None
                    )
                    or (
                        payload.get("revenue_actual") is not None
                        and payload.get("revenue_estimate") is not None
                    )
                )
            ):
                connection.execute(
                    """INSERT INTO ai_earnings_final_locks(
                           report_id,ticker,earnings_date,report_year,
                           report_quarter,job_id,locked_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (
                        report_id,
                        ticker,
                        report_date,
                        payload.get("year"),
                        payload.get("quarter"),
                        job_id,
                        now,
                    ),
                )
            recovered = connection.execute(
                """SELECT j.*,s.submission_source FROM ai_jobs AS j
                   JOIN ai_job_sources AS s ON s.job_id=j.job_id
                   WHERE j.job_id=?""",
                (job_id,),
            ).fetchone()
            connection.commit()
        if recovered is None:
            raise RuntimeError("ai_job_recovery_not_found")
        return dict(recovered)

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

    def defer_unsent_submission(
        self,
        job_id: str,
        owner: str,
        *,
        delay_seconds: float,
        error_code: str,
    ) -> None:
        """Put back a job whose provider request was never sent.

        本地写库失败时任务可能已经过了提交闸门（submission_started_at 已落库），
        请求却还没发出。就这样推迟，重新认领时会被当成结果未知、占道 15 分钟
        且永不重试；写成终态又把本地故障记在供应商头上。这里连同闸门留下的
        提交时间与预留一起撤回，任务回到 pending。只能由确知请求没有发出的
        调用方使用。
        """

        now_dt = _utcnow()
        now = _iso(now_dt)
        with self._connect() as connection:
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='pending',
                    attempt_count=CASE
                        WHEN submission_started_at IS NULL THEN attempt_count
                        ELSE MAX(0,attempt_count-1) END,
                    submission_started_at=NULL,submitted_at=NULL,
                    budget_charge_microusd=0,
                    next_attempt_at=?,last_polled_at=?,
                    poll_count=poll_count+1,error_code=?,
                    lease_owner=NULL,lease_expires_at=NULL,updated_at=?
                WHERE job_id=? AND lease_owner=?
                  AND openai_response_id IS NULL
                  AND status IN ('pending','in_progress')
                """,
                (
                    _iso(now_dt + timedelta(seconds=max(1.0, delay_seconds))),
                    now,
                    error_code[:120],
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
        detail: str | None = None,
        usage: dict[str, int | None] | None = None,
    ) -> None:
        now = _iso()
        safe_code = error_code[:120]
        safe_detail = (
            str(detail).strip()[:2000]
            if detail is not None and str(detail).strip()
            else None
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute(
                """SELECT job_type,model,provider_result_json,submission_started_at,budget_charge_microusd,openai_response_id
                   FROM ai_jobs
                   WHERE job_id=? AND lease_owner=?""",
                (job_id, owner),
            ).fetchone()
            if current is None:
                connection.rollback()
                raise RuntimeError("ai_job_lease_lost")
            usage_values = (
                json.loads(current["provider_result_json"])["usage"]
                if current["provider_result_json"]
                else dict(usage or {})
            )
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
                and not (_uses_claude(current["model"]) and current["submission_started_at"])
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
            if has_reported_usage:
                budget_charge_microusd = _settled_budget_charge_microusd(
                    str(current["job_type"]),
                    usage_values,
                    fallback_microusd=budget_charge_microusd,
                    model=str(current["model"]),
                )
            elif confirmed_without_usage or _reservation_released(
                "failed",
                safe_code,
                current["openai_response_id"],
                current["model"],
                current["submission_started_at"],
            ):
                # 美元账与 token 账同一规则：token 账释放、美元账却按满额预留
                # 记账的不一致，一旦重新启用美元上限就会复现 2026-08-14 误锁。
                budget_charge_microusd = 0
            updated = connection.execute(
                """
                UPDATE ai_jobs
                SET status='failed', error_code=?, error_detail=?, completed_at=?,
                    usage_input_tokens=COALESCE(?,usage_input_tokens),
                    usage_cached_input_tokens=COALESCE(?,usage_cached_input_tokens),
                    usage_cache_creation_input_tokens=COALESCE(?,usage_cache_creation_input_tokens),
                    usage_cache_creation_5m_input_tokens=COALESCE(?,usage_cache_creation_5m_input_tokens),
                    usage_cache_creation_1h_input_tokens=COALESCE(?,usage_cache_creation_1h_input_tokens),
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
                    safe_detail,
                    now,
                    usage_values.get("input_tokens"),
                    usage_values.get("cached_input_tokens"),
                    *(usage_values.get(field) for field in _CACHE_USAGE_FIELDS),
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
            select_sql = """SELECT job_type,model,provider_result_json,submission_started_at,budget_charge_microusd,error_code,
                                   openai_response_id
                            FROM ai_jobs
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
            usage_values = (
                json.loads(current["provider_result_json"])["usage"]
                if current["provider_result_json"]
                else dict(usage or {})
            )
            budget_charge_microusd = int(
                current["budget_charge_microusd"] or 0
            )
            if any(value is not None for value in usage_values.values()):
                budget_charge_microusd = _settled_budget_charge_microusd(
                    str(current["job_type"]),
                    usage_values,
                    fallback_microusd=budget_charge_microusd,
                    model=str(current["model"]),
                )
            elif _reservation_released(
                "cancelled",
                current["error_code"],
                current["openai_response_id"],
                current["model"],
                current["submission_started_at"],
            ):
                budget_charge_microusd = 0
            update_sql = """
                UPDATE ai_jobs
                SET status='cancelled', completed_at=?,
                    usage_input_tokens=COALESCE(?,usage_input_tokens),
                    usage_cached_input_tokens=COALESCE(?,usage_cached_input_tokens),
                    usage_cache_creation_input_tokens=COALESCE(?,usage_cache_creation_input_tokens),
                    usage_cache_creation_5m_input_tokens=COALESCE(?,usage_cache_creation_5m_input_tokens),
                    usage_cache_creation_1h_input_tokens=COALESCE(?,usage_cache_creation_1h_input_tokens),
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
                *(usage_values.get(field) for field in _CACHE_USAGE_FIELDS),
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
        self.ensure_initialized()
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
                result = validate_result_cached(
                    str(row["job_type"]),
                    json.dumps(
                        result,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    ),
                    payload,
                    validator=validate_result,
                )
            except (TypeError, ValueError) as exc:
                # 规则收紧后旧的付费结果会被隐藏；不留记录时只能重取响应现场
                # 复现（2026-08-13 「焦点周期没东西」就难在这里）。
                record_fallback_failure("ai_job_result_hidden", exc)
                legacy_output_hidden = True
        if legacy_output_hidden:
            result = None
        payload_source = payload
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
            error_detail=row.get("error_detail"),
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
        payload["usage"].update(
            {field: row.get("usage_" + field) for field in _CACHE_USAGE_FIELDS}
        )
        payload["evidence_sources"] = []
        payload["usage"].update({field: None for field in _TOOL_USAGE_FIELDS})
        validated_receipt = None
        if row.get("provider_result_json"):
            try:
                receipt = json.loads(row["provider_result_json"])
                AIJobRepository._provider_receipt_json(receipt)
                if (receipt["model"] != row.get("model")
                        or receipt["id"] != row.get("anthropic_message_id" if receipt["provider"] == "anthropic" else "openai_response_id")):
                    raise ValueError("ai_job_provider_result_identity_mismatch")
                validated_receipt = receipt
                payload["evidence_sources"] = receipt.get("evidence_sources", [])
                payload["usage"].update({
                    field: receipt["usage"].get(field) for field in _TOOL_USAGE_FIELDS
                })
            except (TypeError, ValueError) as exc:
                record_fallback_failure("ai_job_provider_result_hidden", exc)
        if (row.get("job_type") == "market_focus"
                and payload_source.get("verification_version") == "web-evidence-v1"):
            # Never expose mixed-event prose or self-reported verification as a
            # public result. Durable successful tool receipts must bind first.
            payload["evidence_sources"] = []
            if result is not None:
                try:
                    from app.services.ai_jobs.models import validate_market_focus_evidence
                    from app.services.ai_jobs.focus_verification import public_focus_result, public_focus_sources

                    if validated_receipt is None:
                        raise ValueError("market_focus_tool_receipt_missing")
                    tool_evidence = validated_receipt.get("tool_evidence", [])
                    validate_market_focus_evidence(result, payload_source, tool_evidence)
                    payload["result"] = public_focus_result(result)
                    payload["evidence_sources"] = public_focus_sources(result, tool_evidence)
                except (KeyError, TypeError, ValueError) as exc:
                    record_fallback_failure("ai_job_focus_evidence_hidden", exc)
                    payload["result"] = None
                    payload["error_code"] = payload.get("error_code") or "legacy_output_hidden"
        payload["submission_source"] = (
            row.get("submission_source")
            if row.get("submission_source") in {"manual", "scheduled"}
            else "manual"
        )
        if row.get("job_type") == "earnings_impact":
            analysis_stage = str(
                payload_source.get("analysis_stage")
                or payload_source.get("analysis_phase")
                or "pre_release"
            )
            locked = bool(row.get("earnings_final_locked"))
            payload["_analysis_stage"] = analysis_stage
            payload["_analysis_phase"] = analysis_stage
            payload["_report_date"] = str(
                payload_source.get("earnings_date") or ""
            )
            payload["_report_id"] = str(
                payload_source.get("report_id")
                or earnings_report_id(payload_source)
            )
            payload["_input_hash"] = str(
                payload_source.get("input_hash") or ""
            )
            payload["_locked"] = locked
            payload["_final"] = bool(
                locked
                and analysis_stage == "post_release_final"
                and row.get("status") == "completed"
            )
            payload["_finalization_in_progress"] = bool(
                row.get("earnings_finalization_in_progress")
            )
        return payload

    def budget_snapshot(
        self,
        *,
        daily_limit: int,
        daily_budget_usd: float,
        shared_daily_budget_usd: float = 0,
        shared_budget_start_at: datetime | None = None,
        shared_budget_enforce_limit: bool = True,
        daily_token_limit: int = 10_000_000,
        cooldown_seconds: int = 0,
        unknown_submission_hold_seconds: int = 86400,
        now: datetime | None = None,
        lane: str | None = None,
        model: str | None = None,
        max_concurrency: int = 1,
    ) -> dict[str, Any]:
        """Return a secret-free, point-in-time view of paid task capacity.

        The slot and cooldown use the worker's gate helpers. All models and
        lanes share ``max_concurrency``; OpenAI additionally shares one slot.
        Cooldown only exists on the manual lane.
        """

        self._concurrency_limit(_CLAUDE_MODEL, max_concurrency)
        self.ensure_initialized()
        shared_limit = usd_to_microusd(shared_daily_budget_usd)
        shared_budget_enabled = shared_limit > 0 and (model is None or model in JOB_MODELS)
        observed = now or _utcnow()
        if shared_budget_enabled:
            SharedModelBudget(
                self.path, shared_daily_budget_usd, self.path.parent / "market-brief",
                accounting_start_at=shared_budget_start_at,
                enforce_limit=shared_budget_enforce_limit,
            ).bootstrap_brief_history(observed)
        day_start_dt = observed.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_dt = day_start_dt + timedelta(days=1)
        with self._connect() as connection:
            connection.execute("BEGIN")
            shared_totals = model_budget_totals(connection, observed, shared_budget_start_at) if shared_budget_enabled else None
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
                """SELECT job_type,model,submission_started_at,status,error_code,openai_response_id,
                          usage_total_tokens
                   FROM ai_jobs
                   WHERE submission_started_at>=? AND submission_started_at<?""",
                (_iso(day_start_dt), _iso(day_end_dt)),
            ).fetchall()
            # This is a recent failure signal, not a live balance lookup. A
            # later successful paid result confirms recovery after funding;
            # pending jobs and local-only completions cannot confirm it.
            credit_exhausted_recent = connection.execute(
                """WITH failure AS (
                       SELECT MAX(updated_at) AS last_failed_at FROM ai_jobs
                       WHERE error_code='provider_credit_exhausted'
                         AND updated_at>=? AND updated_at<=?
                   )
                   SELECT 1 FROM failure
                   WHERE failure.last_failed_at IS NOT NULL
                     AND NOT EXISTS (
                         SELECT 1 FROM ai_jobs AS success
                         WHERE success.status='completed'
                           AND success.submission_started_at IS NOT NULL
                           AND success.completed_at>failure.last_failed_at
                           AND success.completed_at<=?
                     )
                   LIMIT 1""",
                (
                    _iso(observed - timedelta(hours=2)),
                    _iso(observed),
                    _iso(observed),
                ),
            ).fetchone() is not None
            occupants, concurrency_limit = self._submission_capacity(
                connection, model=model, lane=lane, max_concurrency=max_concurrency,
                now_dt=observed, unknown_submission_hold_seconds=unknown_submission_hold_seconds,
            )
            active = occupants[0] if occupants else None
            cooldown_until = (
                self._manual_cooldown_until(
                    connection,
                    now_dt=observed,
                    cooldown_seconds=cooldown_seconds,
                )
                if lane in {None, "manual"}
                else None
            )
        submitted_jobs = int(totals["submitted_jobs"] if totals else 0)
        charged_microusd = int(totals["charged"] if totals else 0)
        del daily_limit, daily_budget_usd
        token_limit = int(daily_token_limit)
        if not 102_400 <= token_limit <= 100_000_000:
            raise ValueError("daily_token_limit is invalid")
        token_budget_used = _daily_tokens_used(token_rows)
        active_public = self.public(dict(active)) if active is not None else None
        token_budget_available = (
            token_budget_used + _minimum_task_token_reservation(model=model)
            <= token_limit
        )
        dollar_budget_available = True
        if shared_totals is not None:
            from app.services.ai_jobs.runtime import AI_TASK_MAX_OUTPUT_TOKENS

            charged_microusd = shared_totals["used_microusd"]
            minimum_reservation = min(
                _task_budget_reservation_microusd(job_type, model=model or _CLAUDE_MODEL)
                for job_type in AI_TASK_MAX_OUTPUT_TOKENS
            )
            dollar_budget_available = (
                (shared_budget_start_at is None or observed >= shared_budget_start_at)
                and (not shared_budget_enforce_limit
                     or charged_microusd + minimum_reservation <= shared_limit)
            )
            token_budget_available = True  # Observability only under the shared dollar gate.
        return {
            "daily_max_jobs": 0,
            "daily_budget_usd": shared_limit / 1_000_000 if shared_budget_enabled else 0.0,
            "budget_basis": "shared_usd" if shared_budget_enabled else "tokens",
            "budget_enforced": shared_budget_enforce_limit if shared_budget_enabled else True,
            "budget_mode": ("tracking" if shared_budget_enabled and not shared_budget_enforce_limit else "enforced"),
            "budget_timezone": "UTC",
            "budget_reset_at": _iso(day_end_dt),
            "accounting_start_at": _iso(shared_budget_start_at) if shared_budget_start_at else None,
            "daily_token_limit": token_limit,
            "submitted_jobs": submitted_jobs,
            "budget_used_usd": charged_microusd / 1_000_000,
            **(shared_totals or {}),
            "budget_remaining_usd": max(0, shared_limit - charged_microusd) / 1_000_000 if shared_budget_enabled else None,
            "usage_total_tokens": int(totals["total_tokens"] if totals else 0),
            "token_budget_used_tokens": token_budget_used,
            "token_budget_remaining_tokens": max(
                0,
                token_limit - token_budget_used,
            ),
            "token_budget_available": token_budget_available,
            "budget_available": dollar_budget_available if shared_budget_enabled else token_budget_available,
            "provider_credit_exhausted": credit_exhausted_recent,
            "job_limit_available": True,
            "dollar_budget_available": dollar_budget_available,
            "concurrency_available": len(occupants) < concurrency_limit and self._provider_slot_available(model, occupants),
            "active_jobs_count": len(occupants),
            "concurrency_limit": concurrency_limit,
            "active_job": active_public,
            "cooldown_until": _iso(cooldown_until) if cooldown_until else None,
            "cooldown_complete": cooldown_until is None,
        }

    def news_analysis_progress(
        self,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Read one exact, persisted news batch without mutating either store.

        New jobs have durable batch membership. Rows created before that schema
        are deliberately treated as one-job batches; timestamps are never used
        to guess membership.
        """

        observed = now or _utcnow()
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        observed_text = _iso(observed)

        def idle() -> dict[str, Any]:
            return {
                "status": "idle",
                "scope": "latest_submission_batch",
                "batch_id": None,
                "batch_source": None,
                "total": 0,
                "finished": 0,
                "succeeded": 0,
                "awaiting_validation": 0,
                "rejected": 0,
                "failed": 0,
                "waiting": 0,
                "in_progress": 0,
                "cancelled": 0,
                "insufficient_context": 0,
                "budget_blocked": 0,
                "progress_percent": 0,
                "current_index": None,
                "current_news_id": None,
                "current_phase": None,
                "queue_total": 0,
                "queue_waiting": 0,
                "queue_in_progress": 0,
                "started_at": None,
                "last_updated_at": None,
                "as_of": observed_text,
                "_batch_jobs": [],
            }

        if not self.path.is_file():
            return idle()
        uri = f"file:{quote(self.path.resolve().as_posix(), safe='/')}?mode=ro"

        @contextmanager
        def read_connection() -> Iterator[sqlite3.Connection]:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            try:
                yield connection
            finally:
                connection.close()

        with read_connection() as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only=ON")
            tables = {
                str(row["name"])
                for row in connection.execute(
                    """SELECT name FROM sqlite_master
                       WHERE type='table' AND name IN (
                           'ai_jobs','ai_job_sources','ai_job_batch_members'
                       )"""
                ).fetchall()
            }
            if "ai_jobs" not in tables:
                return idle()
            has_sources = "ai_job_sources" in tables
            has_batches = "ai_job_batch_members" in tables
            source_select = (
                "COALESCE(s.submission_source,'manual') AS submission_source"
                if has_sources
                else "'manual' AS submission_source"
            )
            source_join = (
                "LEFT JOIN ai_job_sources AS s ON s.job_id=j.job_id"
                if has_sources
                else ""
            )
            batch_select = (
                "b.batch_id,b.position"
                if has_batches
                else "NULL AS batch_id,NULL AS position"
            )
            batch_join = (
                "LEFT JOIN ai_job_batch_members AS b ON b.job_id=j.job_id"
                if has_batches
                else ""
            )
            projection = f"""
                SELECT j.job_id,j.payload_json,j.result_json,j.status,
                       j.openai_response_id,j.submission_started_at,
                       j.created_at,j.updated_at,{source_select},{batch_select}
                FROM ai_jobs AS j
                {source_join}
                {batch_join}
            """
            anchor = connection.execute(
                projection
                + """
                  WHERE j.job_type='news_impact' AND j.created_at<=?
                    AND j.status IN ('pending','queued','in_progress')
                  ORDER BY j.created_at,j.job_id LIMIT 1
                  """,
                (observed_text,),
            ).fetchone()
            if anchor is None:
                anchor = connection.execute(
                    projection
                    + """
                      WHERE j.job_type='news_impact' AND j.created_at<=?
                      ORDER BY j.created_at DESC,j.job_id DESC LIMIT 1
                      """,
                    (observed_text,),
                ).fetchone()
            if anchor is None:
                return idle()
            anchor_dict = dict(anchor)
            persisted_batch_id = anchor_dict.get("batch_id")
            if has_batches and isinstance(persisted_batch_id, str):
                batch = [
                    dict(row)
                    for row in connection.execute(
                        projection
                        + """
                          WHERE j.job_type='news_impact'
                            AND b.batch_id=? AND j.created_at<=?
                          ORDER BY b.position,j.created_at,j.job_id
                          """,
                        (persisted_batch_id, observed_text),
                    ).fetchall()
                ]
            else:
                # A pre-schema row is an exact one-item historical batch.
                batch = [anchor_dict]
                persisted_batch_id = None
            queue = connection.execute(
                """
                SELECT
                  COUNT(*) AS total,
                  SUM(CASE
                        WHEN status='pending'
                          OR (
                            status='queued'
                            AND submission_started_at IS NULL
                            AND openai_response_id IS NULL
                          )
                        THEN 1 ELSE 0 END
                  ) AS waiting,
                  SUM(CASE
                        WHEN status='in_progress'
                          OR (
                            status='queued'
                            AND (
                              submission_started_at IS NOT NULL
                              OR openai_response_id IS NOT NULL
                            )
                          )
                        THEN 1 ELSE 0 END
                  ) AS in_progress
                FROM ai_jobs
                WHERE job_type='news_impact'
                  AND status IN ('pending','queued','in_progress')
                  AND created_at<=?
                """,
                (observed_text,),
            ).fetchone()

        counts = {
            status: sum(1 for row in batch if str(row["status"]) == status)
            for status in (
                "pending",
                "queued",
                "in_progress",
                "completed",
                "failed",
                "cancelled",
                "insufficient_context",
                "budget_blocked",
            )
        }
        provider_rows = [
            row
            for row in batch
            if str(row["status"]) == "in_progress"
            or (
                str(row["status"]) == "queued"
                and (
                    row.get("submission_started_at") is not None
                    or row.get("openai_response_id") is not None
                )
            )
        ]
        provider_job_ids = {str(row["job_id"]) for row in provider_rows}
        waiting = sum(
            1
            for row in batch
            if str(row["status"]) in {"pending", "queued"}
            and str(row["job_id"]) not in provider_job_ids
        )
        in_progress = len(provider_rows)
        finished = len(batch) - waiting - in_progress
        current_rows = [
            (index, row)
            for index, row in enumerate(batch, start=1)
            if str(row["job_id"]) in provider_job_ids
        ]
        current_index: int | None = None
        current_news_id: int | None = None
        current_phase: str | None = None
        if len(current_rows) == 1:
            current_index, current_row = current_rows[0]
            current_phase = (
                "provider_queued"
                if str(current_row["status"]) == "queued"
                else "provider_processing"
            )
            try:
                payload = json.loads(str(current_row["payload_json"]))
            except (TypeError, json.JSONDecodeError):
                payload = {}
            raw_news_id = payload.get("news_id") if isinstance(payload, dict) else None
            if isinstance(raw_news_id, int) and raw_news_id >= 1:
                current_news_id = raw_news_id

        queue_total = int(queue["total"] or 0) if queue is not None else 0
        queue_waiting = int(queue["waiting"] or 0) if queue is not None else 0
        queue_in_progress = (
            int(queue["in_progress"] or 0) if queue is not None else 0
        )
        updated_values = [
            str(row["updated_at"])
            for row in batch
            if isinstance(row.get("updated_at"), str) and row["updated_at"]
        ]
        return {
            "status": "active" if waiting or in_progress else "completed",
            "scope": "latest_submission_batch",
            "batch_id": persisted_batch_id,
            "batch_source": str(batch[0]["submission_source"]),
            "total": len(batch),
            "finished": finished,
            # Completed rows are classified only after the local, read-only
            # result-audit lookup performed by PersonalCatalystService.
            "succeeded": 0,
            "awaiting_validation": counts["completed"],
            "rejected": 0,
            "failed": counts["failed"],
            "waiting": waiting,
            "in_progress": in_progress,
            "cancelled": counts["cancelled"],
            "insufficient_context": counts["insufficient_context"],
            "budget_blocked": counts["budget_blocked"],
            "progress_percent": round(finished * 100 / len(batch)),
            "current_index": current_index,
            "current_news_id": current_news_id,
            "current_phase": current_phase,
            "queue_total": queue_total,
            "queue_waiting": queue_waiting,
            "queue_in_progress": queue_in_progress,
            "started_at": str(batch[0]["created_at"]),
            "last_updated_at": max(updated_values) if updated_values else None,
            "as_of": observed_text,
            "_batch_jobs": batch,
        }

    def health(self) -> dict[str, Any]:
        try:
            self.ensure_initialized()
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
        except Exception as exc:
            record_fallback_failure("ai_job_health", exc)
            return {
                "healthy": False,
                "status": "database_unavailable",
                "schema_version": _SCHEMA_VERSION,
                "pending": None,
                "submission_unknown": None,
            }
