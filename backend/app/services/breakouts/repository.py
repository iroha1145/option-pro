"""SQLite persistence for atomically published Breakout Radar snapshots.

The worker owns scheduled scan rows; the quote service writes only the separate
versioned live overlay. API callers construct the repository with ``read_only=True``;
those connections use SQLite ``mode=ro`` and ``query_only`` and cannot create or
migrate a database by accident.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import sqlite3
from dataclasses import asdict, dataclass, is_dataclass
from datetime import date, datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence
from urllib.parse import quote


LEGACY_SCHEMA_VERSION = "breakout-db-v1"
V2_SCHEMA_VERSION = "breakout-db-v2"
SCHEMA_VERSION = "breakout-db-v3"
DEFAULT_LOCK_NAME = "breakout-worker"
MAX_PROVIDER_JSON_BYTES = 2_000_000
MAX_DEBUG_JSON_BYTES = 16_384
MAX_EVENT_JSON_BYTES = 524_288
MAX_STRUCTURE_JSON_BYTES = 262_144
MAX_GENERIC_JSON_BYTES = 65_536
MAX_MIGRATION_PREVIEW_CHARS = 4_096
_CURSOR_VERSION = 2
_TRIGGERED_LIFECYCLE_STATES = {
    "TRIGGERED",
    "CONFIRMED",
    "HOLDING",
    "RETESTING",
    "RETEST_HELD",
    "REACCELERATING",
    "EXTENDED",
    "FAILED",
}


class BreakoutRepositoryError(RuntimeError):
    """Base class for repository failures."""


class ReadOnlyRepositoryError(BreakoutRepositoryError):
    """Raised when a write is attempted through an API repository."""


class SchemaVersionError(BreakoutRepositoryError):
    """Raised when a reader or writer encounters an unsupported schema."""

    def __init__(
        self,
        message: str,
        *,
        current_version: str | None = None,
        required_version: str = SCHEMA_VERSION,
    ) -> None:
        super().__init__(message)
        self.current_version = current_version
        self.required_version = required_version

    def status_payload(self) -> dict[str, Any]:
        return {
            "status": "schema_upgrade_required",
            "schema_version": self.current_version,
            "required_schema_version": self.required_version,
            "migration_required": True,
            "message": str(self),
        }


class LeaseLostError(BreakoutRepositoryError):
    """Raised when a worker tries to publish with an expired fencing token."""


class InvalidCursorError(BreakoutRepositoryError):
    """Raised when an event cursor is malformed or does not match its scan."""


@dataclass(frozen=True)
class LeaseInfo:
    lock_name: str
    owner_id: str
    fencing_token: int
    heartbeat_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class CarryoverBatch:
    """A deterministic, bounded batch of events that still need evaluation."""

    events: tuple[Mapping[str, Any], ...]
    expired_due_event_ids: frozenset[str]
    has_more: bool = False


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware_utc(value: datetime | str, *, field: str = "datetime") -> datetime:
    if isinstance(value, str):
        text = value[:-1] + "+00:00" if value.endswith("Z") else value
        try:
            value = datetime.fromisoformat(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be an ISO-8601 datetime") from exc
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{field} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime | str) -> str:
    return _aware_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str | None) -> datetime | None:
    if value is None:
        return None
    return _aware_utc(value)


def _enum_value(value: Any) -> Any:
    return value.value if isinstance(value, Enum) else value


def _mapping(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return dict(model_dump(mode="python"))
    if is_dataclass(value):
        return dict(asdict(value))
    raise TypeError(f"expected a mapping-compatible value, got {type(value).__name__}")


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return _timestamp(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return asdict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(f"cannot encode {type(value).__name__} as JSON")


def _json_encode(value: Any) -> str:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            default=_json_default,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("JSON fields must contain finite, serializable values") from exc


def _json_dumps(value: Any, *, max_bytes: int = MAX_GENERIC_JSON_BYTES) -> str:
    encoded = _json_encode(value)
    size = len(encoded.encode("utf-8"))
    if size > max_bytes:
        raise ValueError(f"JSON field exceeds {max_bytes} bytes")
    return encoded


def _json_loads(value: str | None, fallback: Any = None) -> Any:
    if value is None:
        return fallback
    return json.loads(value)


def _digest(value: Any) -> str:
    payload = _json_dumps(value, max_bytes=MAX_PROVIDER_JSON_BYTES)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _database_snapshot_id(
    scan_run_id: str,
    provider: str,
    provider_cache_key: str,
    as_of: datetime | str,
) -> str:
    """Return the scan-scoped identity for one persisted provider snapshot."""

    payload = "".join(
        (
            str(scan_run_id),
            str(provider),
            str(provider_cache_key),
            _timestamp(as_of),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_db_path(path: str | Path) -> Path:
    text = str(path)
    candidate = Path(path)
    if text.startswith("file:") or not candidate.is_absolute():
        raise ValueError("database path must be an absolute filesystem path")
    if ".." in candidate.parts:
        raise ValueError("database path must not contain parent traversal")
    if candidate.exists() and candidate.is_symlink():
        raise ValueError("database path must not be a symbolic link")
    parent = candidate.parent
    if not parent.exists() or not parent.is_dir():
        raise ValueError("database parent directory must already exist")
    return candidate


_LEGACY_SCHEMA = (
    """
    CREATE TABLE IF NOT EXISTS breakout_schema_version (
        version TEXT PRIMARY KEY,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_scan_runs (
        scan_run_id TEXT PRIMARY KEY,
        idempotency_key TEXT NOT NULL UNIQUE,
        provider TEXT NOT NULL,
        profile TEXT,
        session TEXT NOT NULL,
        scheduled_at TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        published_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','abandoned')),
        candidate_count INTEGER NOT NULL DEFAULT 0 CHECK(candidate_count >= 0),
        event_count INTEGER NOT NULL DEFAULT 0 CHECK(event_count >= 0),
        error_code TEXT,
        error_message TEXT CHECK(error_message IS NULL OR length(error_message) <= 1000),
        config_hash TEXT NOT NULL,
        versions_hash TEXT NOT NULL,
        versions_json TEXT NOT NULL DEFAULT '{}'
            CHECK(length(versions_json) <= 65536 AND json_valid(versions_json)),
        source_snapshot_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_provider_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        scan_run_id TEXT NOT NULL UNIQUE REFERENCES breakout_scan_runs(scan_run_id) ON DELETE CASCADE,
        provider TEXT NOT NULL,
        status TEXT NOT NULL,
        as_of TEXT NOT NULL,
        session TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        candidate_count INTEGER NOT NULL CHECK(candidate_count >= 0),
        is_stale INTEGER NOT NULL DEFAULT 0 CHECK(is_stale IN (0,1)),
        warnings_json TEXT NOT NULL CHECK(length(warnings_json) <= 65536 AND json_valid(warnings_json)),
        payload_json TEXT NOT NULL CHECK(length(payload_json) <= 2000000 AND json_valid(payload_json)),
        created_at TEXT NOT NULL,
        expires_at TEXT
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_candidates (
        scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id) ON DELETE CASCADE,
        ticker TEXT NOT NULL,
        source TEXT NOT NULL,
        provider_timestamp TEXT NOT NULL,
        candidate_json TEXT NOT NULL CHECK(length(candidate_json) <= 65536 AND json_valid(candidate_json)),
        raw_provider_fields_json TEXT NOT NULL
            CHECK(length(raw_provider_fields_json) <= 16384 AND json_valid(raw_provider_fields_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(scan_run_id, ticker)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_structures (
        scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id) ON DELETE CASCADE,
        pivot_id TEXT NOT NULL,
        ticker TEXT NOT NULL,
        calculation_cutoff_at TEXT NOT NULL,
        structure_json TEXT NOT NULL CHECK(length(structure_json) <= 262144 AND json_valid(structure_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(scan_run_id, pivot_id)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_events (
        event_id TEXT PRIMARY KEY,
        trading_date TEXT NOT NULL,
        ticker TEXT NOT NULL,
        setup_type TEXT NOT NULL,
        pivot_id TEXT NOT NULL,
        lifecycle_state TEXT NOT NULL,
        event_at TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        source_snapshot_id TEXT NOT NULL,
        alert_priority_score REAL,
        data_confidence_score REAL,
        current_scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id),
        event_json TEXT NOT NULL CHECK(length(event_json) <= 262144 AND json_valid(event_json)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(trading_date, ticker, setup_type, pivot_id)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_transitions (
        transition_id TEXT PRIMARY KEY,
        event_id TEXT NOT NULL REFERENCES breakout_events(event_id) ON DELETE CASCADE,
        from_state TEXT NOT NULL DEFAULT '',
        to_state TEXT NOT NULL,
        reason TEXT NOT NULL DEFAULT '',
        evidence_at TEXT NOT NULL,
        scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id),
        transition_json TEXT NOT NULL CHECK(length(transition_json) <= 65536 AND json_valid(transition_json)),
        created_at TEXT NOT NULL,
        UNIQUE(event_id, from_state, to_state, reason, evidence_at)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_scan_events (
        scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id) ON DELETE CASCADE,
        event_id TEXT NOT NULL REFERENCES breakout_events(event_id),
        rank INTEGER NOT NULL CHECK(rank > 0),
        ticker TEXT NOT NULL,
        session TEXT NOT NULL,
        setup_type TEXT NOT NULL,
        lifecycle_state TEXT NOT NULL,
        event_at TEXT NOT NULL,
        alert_priority_score REAL,
        sort_priority REAL NOT NULL,
        event_snapshot_json TEXT NOT NULL
            CHECK(length(event_snapshot_json) <= 262144 AND json_valid(event_snapshot_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(scan_run_id, event_id),
        UNIQUE(scan_run_id, rank)
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_provider_health (
        provider TEXT PRIMARY KEY,
        status TEXT NOT NULL,
        consecutive_failures INTEGER NOT NULL DEFAULT 0 CHECK(consecutive_failures >= 0),
        last_success_at TEXT,
        last_failure_at TEXT,
        circuit_open_until TEXT,
        stale_snapshot_available INTEGER NOT NULL DEFAULT 0 CHECK(stale_snapshot_available IN (0,1)),
        error_code TEXT,
        details_json TEXT NOT NULL DEFAULT '{}'
            CHECK(length(details_json) <= 65536 AND json_valid(details_json)),
        updated_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_worker_lock (
        lock_name TEXT PRIMARY KEY,
        owner_id TEXT,
        fencing_token INTEGER NOT NULL CHECK(fencing_token > 0),
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS breakout_worker_status (
        worker_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        status TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        current_scan_run_id TEXT,
        last_completed_scan_run_id TEXT,
        last_completed_at TEXT,
        error_code TEXT,
        details_json TEXT NOT NULL DEFAULT '{}'
            CHECK(length(details_json) <= 65536 AND json_valid(details_json)),
        updated_at TEXT NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE IF NOT EXISTS range_persistence_shadow (
        scan_run_id TEXT NOT NULL REFERENCES breakout_scan_runs(scan_run_id) ON DELETE CASCADE,
        event_id TEXT NOT NULL REFERENCES breakout_events(event_id),
        ticker TEXT NOT NULL,
        production_score REAL,
        hypothetical_score REAL,
        production_rank INTEGER,
        hypothetical_rank INTEGER,
        rank_delta INTEGER,
        version TEXT NOT NULL,
        shadow_json TEXT NOT NULL CHECK(length(shadow_json) <= 65536 AND json_valid(shadow_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(scan_run_id, event_id)
    ) STRICT
    """,
    "CREATE INDEX IF NOT EXISTS idx_breakout_scans_completed ON breakout_scan_runs(status, published_at DESC, scan_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_events_ticker_recent ON breakout_events(ticker, last_seen_at DESC, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_events_date_state ON breakout_events(trading_date DESC, lifecycle_state, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_scan_events_rank ON breakout_scan_events(scan_run_id, rank)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_scan_events_page ON breakout_scan_events(scan_run_id, event_at DESC, sort_priority DESC, event_id)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_transitions_timeline ON breakout_transitions(event_id, evidence_at, transition_id)",
)

LEGACY_SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(" ".join(statement.split()) for statement in _LEGACY_SCHEMA).encode(
        "utf-8"
    )
).hexdigest()


def _v2_statement(statement: str) -> str:
    if "CREATE TABLE IF NOT EXISTS breakout_events" not in statement:
        return statement
    old = """        event_at TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,"""
    new = """        event_at TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        triggered_at TEXT,
        state_changed_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,"""
    if old not in statement:
        raise RuntimeError("legacy breakout_events schema definition changed")
    return statement.replace(old, new)


_V2_SCHEMA = tuple(_v2_statement(statement) for statement in _LEGACY_SCHEMA)
V2_SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(" ".join(statement.split()) for statement in _V2_SCHEMA).encode(
        "utf-8"
    )
).hexdigest()


def _v3_statement(statement: str) -> str:
    if "CREATE TABLE IF NOT EXISTS breakout_provider_snapshots" in statement:
        old = """        snapshot_id TEXT PRIMARY KEY,
        scan_run_id TEXT NOT NULL UNIQUE"""
        new = """        snapshot_id TEXT PRIMARY KEY,
        provider_cache_key TEXT NOT NULL,
        scan_run_id TEXT NOT NULL UNIQUE"""
        if old not in statement:
            raise RuntimeError("v2 provider snapshot schema definition changed")
        return statement.replace(old, new)
    if "CREATE TABLE IF NOT EXISTS breakout_events" in statement:
        old = "event_json TEXT NOT NULL CHECK(length(event_json) <= 262144 AND json_valid(event_json))"
        new = (
            "event_json TEXT NOT NULL "
            "CHECK(length(CAST(event_json AS BLOB)) <= 524288 AND json_valid(event_json))"
        )
        if old not in statement:
            raise RuntimeError("v2 breakout_events JSON constraint changed")
        return statement.replace(old, new)
    if "CREATE TABLE IF NOT EXISTS breakout_scan_events" in statement:
        old = "CHECK(length(event_snapshot_json) <= 262144 AND json_valid(event_snapshot_json))"
        new = (
            "CHECK(length(CAST(event_snapshot_json AS BLOB)) <= 524288 "
            "AND json_valid(event_snapshot_json))"
        )
        if old not in statement:
            raise RuntimeError("v2 breakout_scan_events JSON constraint changed")
        return statement.replace(old, new)
    return statement


_QUARANTINE_SCHEMA = """
    CREATE TABLE IF NOT EXISTS breakout_migration_quarantine (
        id INTEGER PRIMARY KEY,
        source_schema TEXT NOT NULL,
        table_name TEXT NOT NULL,
        record_key TEXT NOT NULL,
        error_code TEXT NOT NULL,
        raw_sha256 TEXT NOT NULL,
        raw_preview TEXT NOT NULL CHECK(length(raw_preview) <= 4096),
        recovered_json TEXT NOT NULL
            CHECK(length(CAST(recovered_json AS BLOB)) <= 524288 AND json_valid(recovered_json)),
        created_at TEXT NOT NULL
    ) STRICT
    """

_SCHEMA = tuple(_v3_statement(statement) for statement in _V2_SCHEMA) + (
    _QUARANTINE_SCHEMA,
    "CREATE INDEX IF NOT EXISTS idx_breakout_scan_events_event ON breakout_scan_events(event_id, scan_run_id)",
    "CREATE INDEX IF NOT EXISTS idx_breakout_scans_retention ON breakout_scan_runs(status, updated_at, scan_run_id)",
)
SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(" ".join(statement.split()) for statement in _SCHEMA).encode("utf-8")
).hexdigest()

# Independent additive extension: v3 scan snapshots remain byte-for-byte
# compatible and contain only scheduled-bar evidence.
LIVE_SCHEMA_VERSION = "breakout-live-v1"
_LIVE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS breakout_live_schema (
        version TEXT PRIMARY KEY, checksum TEXT NOT NULL, applied_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS breakout_live_events (
        event_id TEXT PRIMARY KEY REFERENCES breakout_events(event_id),
        state_version INTEGER NOT NULL CHECK(state_version > 0),
        evidence_at TEXT NOT NULL,
        event_json TEXT NOT NULL
            CHECK(length(CAST(event_json AS BLOB)) <= 524288 AND json_valid(event_json)),
        updated_at TEXT NOT NULL
    ) STRICT""",
    """CREATE TABLE IF NOT EXISTS breakout_live_transitions (
        event_id TEXT NOT NULL REFERENCES breakout_live_events(event_id),
        state_version INTEGER NOT NULL CHECK(state_version > 0),
        evidence_at TEXT NOT NULL,
        transition_json TEXT NOT NULL
            CHECK(length(CAST(transition_json AS BLOB)) <= 65536 AND json_valid(transition_json)),
        created_at TEXT NOT NULL,
        PRIMARY KEY(event_id, state_version)
    ) STRICT""",
    "CREATE INDEX IF NOT EXISTS idx_breakout_live_events_recent ON breakout_live_events(updated_at DESC,event_id)",
)
LIVE_SCHEMA_CHECKSUM = hashlib.sha256(
    "\n".join(" ".join(statement.split()) for statement in _LIVE_SCHEMA).encode()
).hexdigest()


_CARRYOVER_LATEST_ROWS_SQL = """
WITH latest_rowids AS MATERIALIZED (
    SELECT head.event_id,
           (
               SELECT snapshot.rowid
               FROM breakout_scan_events AS snapshot
                    INDEXED BY idx_breakout_scan_events_event
               CROSS JOIN breakout_scan_runs AS scan
               WHERE snapshot.event_id=head.event_id
                 AND scan.scan_run_id=snapshot.scan_run_id
                 AND scan.status='completed'
                 AND scan.published_at IS NOT NULL
                 AND scan.published_at<=?
                 AND snapshot.event_at<=?
                 AND json_extract(
                     snapshot.event_snapshot_json,'$.first_seen_at'
                 )<=?
                 AND json_extract(
                     snapshot.event_snapshot_json,'$.last_seen_at'
                 )<=?
               ORDER BY scan.published_at DESC,
                        snapshot.event_at DESC,
                        scan.scan_run_id DESC
               LIMIT 1
           ) AS snapshot_rowid
    FROM breakout_events AS head
)
SELECT snapshot.rowid AS snapshot_rowid,
       snapshot.event_id,
       json_extract(
           snapshot.event_snapshot_json,'$.first_seen_at'
       ) AS first_seen_at,
       json_extract(
           snapshot.event_snapshot_json,'$.last_seen_at'
       ) AS last_seen_at,
       snapshot.lifecycle_state
FROM latest_rowids
CROSS JOIN breakout_scan_events AS snapshot
WHERE latest_rowids.snapshot_rowid IS NOT NULL
  AND snapshot.rowid=latest_rowids.snapshot_rowid
  AND snapshot.lifecycle_state NOT IN ('FAILED','EXPIRED')
"""

_CARRYOVER_SNAPSHOT_SQL = """
SELECT event_snapshot_json
FROM breakout_scan_events
WHERE rowid=?
"""


class BreakoutRepository:
    """Versioned SQLite repository with lease-fenced atomic publication."""

    def __init__(
        self,
        path: str | Path,
        read_only: bool = False,
        *,
        clock: Callable[[], datetime] = _utc_now,
        busy_timeout_ms: int = 5_000,
    ) -> None:
        self.path = _safe_db_path(path)
        self.read_only = bool(read_only)
        self._clock = clock
        self.busy_timeout_ms = int(busy_timeout_ms)
        if self.busy_timeout_ms < 0:
            raise ValueError("busy_timeout_ms must be non-negative")

    def _now(self, supplied: datetime | None = None) -> datetime:
        return _aware_utc(supplied if supplied is not None else self._clock(), field="now")

    def _write_connection(self) -> sqlite3.Connection:
        if self.read_only:
            raise ReadOnlyRepositoryError("read-only repository cannot write or migrate")
        connection = sqlite3.connect(
            str(self.path),
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _read_connection(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise FileNotFoundError(str(self.path))
        uri = f"file:{quote(str(self.path), safe='/')}?mode=ro"
        connection = sqlite3.connect(
            uri,
            uri=True,
            timeout=self.busy_timeout_ms / 1000,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA query_only=ON")
        return connection

    def initialize(self) -> None:
        """Create v3 or migrate a verified v1/v2 database atomically.

        Operators must back up an existing database before upgrading it.  Table
        rebuilds run with foreign-key enforcement temporarily disabled because
        SQLite cannot replace a referenced parent table in place; integrity is
        checked before the transaction is allowed to commit.
        """

        connection = self._write_connection()
        try:
            effective_mode = str(
                connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            ).lower()
            if effective_mode != "wal":
                raise BreakoutRepositoryError(
                    f"SQLite WAL mode is required, got {effective_mode}"
                )
            connection.execute("PRAGMA foreign_keys=OFF")
            connection.execute("BEGIN IMMEDIATE")
            for statement in _SCHEMA:
                connection.execute(statement)
            rows = connection.execute(
                "SELECT version,checksum FROM breakout_schema_version ORDER BY version"
            ).fetchall()
            if not rows:
                connection.execute(
                    "INSERT INTO breakout_schema_version(version, checksum, applied_at) VALUES(?,?,?)",
                    (SCHEMA_VERSION, SCHEMA_CHECKSUM, _timestamp(self._now())),
                )
            elif len(rows) == 1 and rows[0]["version"] == SCHEMA_VERSION:
                if rows[0]["checksum"] != SCHEMA_CHECKSUM:
                    raise SchemaVersionError(
                        f"{SCHEMA_VERSION} schema checksum mismatch",
                        current_version=SCHEMA_VERSION,
                    )
            elif len(rows) == 1 and rows[0]["version"] in {
                LEGACY_SCHEMA_VERSION,
                V2_SCHEMA_VERSION,
            }:
                source_version = str(rows[0]["version"])
                expected_checksum = (
                    LEGACY_SCHEMA_CHECKSUM
                    if source_version == LEGACY_SCHEMA_VERSION
                    else V2_SCHEMA_CHECKSUM
                )
                if rows[0]["checksum"] != expected_checksum:
                    raise SchemaVersionError(
                        f"{source_version} schema checksum mismatch",
                        current_version=source_version,
                    )
                self._migrate_to_v3(connection, source_version=source_version)
                for statement in _SCHEMA:
                    connection.execute(statement)
            else:
                versions = ",".join(str(row["version"]) for row in rows)
                raise SchemaVersionError(
                    f"unsupported breakout schema versions: {versions}",
                    current_version=versions or None,
                )
            self._require_schema(connection)
            self._initialize_live_schema(connection)
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                preview = "; ".join(
                    f"{row[0]}:{row[1]}->{row[2]}" for row in violations[:10]
                )
                raise BreakoutRepositoryError(
                    f"breakout migration foreign_key_check failed: {preview}"
                )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.execute("PRAGMA foreign_keys=ON")
            connection.close()

    def _initialize_live_schema(self, connection: sqlite3.Connection) -> None:
        for statement in _LIVE_SCHEMA:
            connection.execute(statement)
        rows = connection.execute("SELECT version,checksum FROM breakout_live_schema").fetchall()
        if not rows:
            connection.execute(
                "INSERT INTO breakout_live_schema VALUES(?,?,?)",
                (LIVE_SCHEMA_VERSION, LIVE_SCHEMA_CHECKSUM, _timestamp(self._now())),
            )
        elif len(rows) != 1 or rows[0]["version"] != LIVE_SCHEMA_VERSION or rows[0]["checksum"] != LIVE_SCHEMA_CHECKSUM:
            raise SchemaVersionError("unsupported breakout live schema or checksum")

    @staticmethod
    def _has_live_schema(connection: sqlite3.Connection) -> bool:
        # Read-only callers of older v3 databases never create tables.
        exists = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='breakout_live_schema'"
        ).fetchone()
        if exists is None:
            return False
        rows = connection.execute("SELECT version,checksum FROM breakout_live_schema").fetchall()
        if len(rows) != 1 or rows[0]["version"] != LIVE_SCHEMA_VERSION or rows[0]["checksum"] != LIVE_SCHEMA_CHECKSUM:
            raise SchemaVersionError("unsupported breakout live schema or checksum")
        return True

    def overlay_live_events(
        self, events: Sequence[Mapping[str, Any]], *, as_of: datetime | None = None,
        with_transitions: bool = False,
    ) -> list[dict[str, Any]]:
        """Opt-in effective view. Scheduled snapshots and public defaults stay pure."""
        result = [dict(event) for event in events]
        if not result:
            return result
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            if not self._has_live_schema(connection):
                return result
            for index, event in enumerate(result):
                row = connection.execute(
                    "SELECT event_json,evidence_at FROM breakout_live_events WHERE event_id=?",
                    (str(event.get("event_id") or ""),),
                ).fetchone()
                if row is None or (as_of is not None and row["evidence_at"] > _timestamp(as_of)):
                    continue
                live = _json_loads(row["event_json"], {})
                # A scheduled terminal decision is also valid for the live view
                # when no live recheck was possible (e.g. a worker upgrade).
                if str(event.get("lifecycle_state")) in {"FAILED", "EXPIRED"} and _timestamp(event["last_seen_at"]) >= row["evidence_at"]:
                    continue
                if with_transitions:
                    transitions = connection.execute(
                        "SELECT transition_json FROM breakout_live_transitions WHERE event_id=? ORDER BY state_version",
                        (str(event["event_id"]),),
                    ).fetchall()
                    live["transitions"] = [_json_loads(item["transition_json"], {}) for item in transitions]
                result[index] = live
            return result
        finally:
            connection.close()

    def recent_live_events(
        self, *, as_of: datetime, lookback_seconds: int = 172_800, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Bounded durable stream recovery, including recently terminal events."""
        if not 1 <= limit <= 500 or not 1 <= lookback_seconds <= 604_800:
            raise ValueError("invalid realtime recovery window")
        observed = _aware_utc(as_of)
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            if not self._has_live_schema(connection):
                return []
            rows = connection.execute(
                """SELECT event_json FROM breakout_live_events
                   WHERE updated_at>=? AND updated_at<=? AND evidence_at<=?
                   ORDER BY updated_at DESC,event_id LIMIT ?""",
                (_timestamp(observed - timedelta(seconds=lookback_seconds)),
                 _timestamp(observed), _timestamp(observed), limit),
            ).fetchall()
            return [_json_loads(row["event_json"], {}) for row in rows]
        finally:
            connection.close()

    def commit_live_trigger(
        self, event: Mapping[str, Any], *, expected_version: int = 0,
    ) -> dict[str, Any] | None:
        """Atomically publish the first observed trade trigger, never a scan row."""
        body = dict(event)
        event_id = str(body["event_id"])
        evidence_at = _timestamp(body["evidence_at"])
        now_text = _timestamp(self._now())
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            self._initialize_live_schema(connection)
            base = connection.execute(
                "SELECT lifecycle_state,last_seen_at FROM breakout_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            current = connection.execute(
                "SELECT state_version FROM breakout_live_events WHERE event_id=?", (event_id,)
            ).fetchone()
            if (base is None or base["lifecycle_state"] != "WATCHING"
                or base["last_seen_at"] >= evidence_at or current is not None
                or expected_version != 0 or body.get("lifecycle_state") != "TRIGGERED"):
                return None
            body["state_version"] = 1
            body["evidence_at"] = evidence_at
            connection.execute(
                "INSERT INTO breakout_live_events VALUES(?,?,?,?,?)",
                (event_id, 1, evidence_at, _json_dumps(body, max_bytes=MAX_EVENT_JSON_BYTES), now_text),
            )
            transition = {
                "event_id": event_id, "state_version": 1,
                "from_state": "WATCHING", "to_state": "TRIGGERED",
                "reason": "realtime_trade_above_breakout_buffer",
                "evidence_at": evidence_at, "source": "finnhub",
                "price": body.get("event_price"),
            }
            connection.execute(
                "INSERT INTO breakout_live_transitions VALUES(?,?,?,?,?)",
                (event_id, 1, evidence_at, _json_dumps(transition), now_text),
            )
            connection.commit()
            return body
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    def _reconcile_live_scan(
        self, connection: sqlite3.Connection, events: Sequence[Any],
        transitions: Sequence[Mapping[str, Any]], now_text: str,
    ) -> None:
        if not events or not self._has_live_schema(connection):
            return
        for value in events:
            event = _mapping(value)
            event["lifecycle_state"] = str(_enum_value(event["lifecycle_state"]))
            event_id = str(event["event_id"])
            row = connection.execute(
                "SELECT state_version,evidence_at,event_json FROM breakout_live_events WHERE event_id=?",
                (event_id,),
            ).fetchone()
            if row is None or int(event.get("state_version") or 0) != row["state_version"]:
                continue
            prior = _json_loads(row["event_json"], {})
            evidence_at = _timestamp(event.get("evidence_at") or event["last_seen_at"])
            if (evidence_at <= row["evidence_at"]
                or str(prior.get("lifecycle_state")) in {"FAILED", "EXPIRED"}):
                continue
            # The state machine processes each live prior separately; CAS also
            # fences a late scan calculated before a newer trade/scan commit.
            version = int(row["state_version"]) + 1
            event["state_version"] = version
            event["evidence_at"] = evidence_at
            connection.execute(
                "UPDATE breakout_live_events SET state_version=?,evidence_at=?,event_json=?,updated_at=? WHERE event_id=? AND state_version=?",
                (version, evidence_at, _json_dumps(event, max_bytes=MAX_EVENT_JSON_BYTES), now_text, event_id, row["state_version"]),
            )
            event_changes = [dict(item) for item in transitions if str(item.get("event_id")) == event_id]
            if str(event.get("lifecycle_state")) != str(prior.get("lifecycle_state")):
                transition = {
                    "event_id": event_id, "state_version": version,
                    "from_state": prior["lifecycle_state"], "to_state": event["lifecycle_state"],
                    "reason": event.get("transition_reason"), "evidence_at": evidence_at,
                    "source": "completed_5m_bars", "steps": event_changes,
                }
                connection.execute(
                    "INSERT INTO breakout_live_transitions VALUES(?,?,?,?,?)",
                    (event_id, version, evidence_at, _json_dumps(transition), now_text),
                )

    @staticmethod
    def _event_table_columns(connection: sqlite3.Connection) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(breakout_events)")
        }

    def _migration_hook(self, phase: str, connection: sqlite3.Connection) -> None:
        """Fault-injection seam for transactional migration tests."""

    @staticmethod
    def _migration_table_columns(
        connection: sqlite3.Connection, table_name: str
    ) -> set[str]:
        return {
            str(row["name"])
            for row in connection.execute(f"PRAGMA table_info({table_name})")
        }

    @staticmethod
    def _create_rebuild_table(
        connection: sqlite3.Connection, table_name: str, temporary_name: str
    ) -> None:
        marker = f"CREATE TABLE IF NOT EXISTS {table_name}"
        for statement in _SCHEMA:
            if marker in statement:
                connection.execute(f"DROP TABLE IF EXISTS {temporary_name}")
                connection.execute(
                    statement.replace(
                        marker, f"CREATE TABLE {temporary_name}", 1
                    )
                )
                return
        raise RuntimeError(f"v3 schema lacks table definition for {table_name}")

    @staticmethod
    def _migration_insert(
        connection: sqlite3.Connection,
        sql: str,
        params: Sequence[Any],
        *,
        table_name: str,
        record_key: str,
    ) -> None:
        try:
            connection.execute(sql, params)
        except sqlite3.Error as exc:
            raise BreakoutRepositoryError(
                f"failed migrating {table_name} record {record_key}: {exc}"
            ) from exc

    @staticmethod
    def _migration_timestamp(*values: Any) -> str:
        for value in values:
            if value in (None, ""):
                continue
            try:
                return _timestamp(value)
            except (TypeError, ValueError):
                continue
        return "1970-01-01T00:00:00.000000Z"

    @staticmethod
    def _migration_mapping(raw: str) -> dict[str, Any] | None:
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, Mapping):
                return None
            _json_encode(parsed)
        except (TypeError, ValueError, RecursionError):
            return None
        return dict(parsed)

    @staticmethod
    def _remove_compactable_migration_fields(body: dict[str, Any]) -> list[str]:
        removable = (
            "raw_provider_fields",
            "raw_provider_fields_json",
            "debug",
            "debug_data",
            "oversized_evidence_details",
            "evidence_details",
            "raw_payload",
            "provider_payload",
        )
        removed: list[str] = []

        def visit(value: Any, target: str, path: str) -> None:
            if isinstance(value, dict):
                for key in sorted(tuple(value)):
                    key_path = f"{path}.{key}" if path else str(key)
                    if key == target:
                        value.pop(key, None)
                        removed.append(key_path)
                    else:
                        visit(value[key], target, key_path)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    visit(item, target, f"{path}[{index}]")

        for target in removable:
            visit(body, target, "")
        return removed

    def _quarantine_migration_value(
        self,
        connection: sqlite3.Connection,
        *,
        source_schema: str,
        table_name: str,
        record_key: str,
        error_code: str,
        raw: str,
        recovered_json: str,
    ) -> None:
        raw_text = str(raw)
        connection.execute(
            """
            INSERT INTO breakout_migration_quarantine(
                source_schema,table_name,record_key,error_code,raw_sha256,
                raw_preview,recovered_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?)
            """,
            (
                source_schema,
                table_name,
                record_key,
                error_code,
                hashlib.sha256(
                    raw_text.encode("utf-8", errors="replace")
                ).hexdigest(),
                raw_text[:MAX_MIGRATION_PREVIEW_CHARS],
                recovered_json,
                _timestamp(self._now()),
            ),
        )

    def _migration_json(
        self,
        connection: sqlite3.Connection,
        *,
        source_schema: str,
        table_name: str,
        record_key: str,
        raw: str,
        parsed: dict[str, Any] | None,
        enrichment: Mapping[str, Any],
        minimal: Mapping[str, Any],
        invalid_code: str,
        oversized_code: str,
    ) -> str:
        if parsed is None:
            recovered = dict(minimal)
            recovered["migration_warning"] = invalid_code
            recovered["migration_warnings"] = [invalid_code]
            encoded = _json_dumps(recovered, max_bytes=MAX_EVENT_JSON_BYTES)
            self._quarantine_migration_value(
                connection,
                source_schema=source_schema,
                table_name=table_name,
                record_key=record_key,
                error_code=invalid_code,
                raw=raw,
                recovered_json=encoded,
            )
            return encoded

        body = dict(parsed)
        body.update(enrichment)
        encoded = _json_encode(body)
        if len(encoded.encode("utf-8")) <= MAX_EVENT_JSON_BYTES:
            return encoded

        try:
            removed = self._remove_compactable_migration_fields(body)
        except RecursionError:
            removed = []
        if removed:
            body["migration_compacted_fields"] = removed[:256]
            body["migration_compacted_field_count"] = len(removed)
            encoded = _json_encode(body)
            if len(encoded.encode("utf-8")) <= MAX_EVENT_JSON_BYTES:
                return encoded

        recovered = dict(minimal)
        recovered["migration_warning"] = oversized_code
        recovered["migration_warnings"] = [oversized_code]
        if removed:
            recovered["migration_compacted_fields"] = removed[:256]
            recovered["migration_compacted_field_count"] = len(removed)
        encoded = _json_dumps(recovered, max_bytes=MAX_EVENT_JSON_BYTES)
        self._quarantine_migration_value(
            connection,
            source_schema=source_schema,
            table_name=table_name,
            record_key=record_key,
            error_code=oversized_code,
            raw=raw,
            recovered_json=encoded,
        )
        return encoded

    def _migrate_to_v3(
        self, connection: sqlite3.Connection, *, source_version: str
    ) -> None:
        """Rebuild bounded JSON tables while preserving every event identity."""

        provider_columns = self._migration_table_columns(
            connection, "breakout_provider_snapshots"
        )
        self._migration_hook("rows_loaded", connection)

        provider_table = "breakout_provider_snapshots_v3_new"
        event_table = "breakout_events_v3_new"
        snapshot_table = "breakout_scan_events_v3_new"
        self._create_rebuild_table(
            connection, "breakout_provider_snapshots", provider_table
        )
        self._create_rebuild_table(connection, "breakout_events", event_table)
        self._create_rebuild_table(
            connection, "breakout_scan_events", snapshot_table
        )

        provider_ids: dict[str, tuple[str, str, str]] = {}
        for row in connection.execute(
            "SELECT * FROM breakout_provider_snapshots ORDER BY scan_run_id"
        ):
            old_snapshot_id = str(row["snapshot_id"])
            payload = self._migration_mapping(str(row["payload_json"])) or {}
            provider_cache_key = str(
                (
                    row["provider_cache_key"]
                    if "provider_cache_key" in provider_columns
                    else None
                )
                or payload.get("cache_key")
                or payload.get("snapshot_id")
                or old_snapshot_id
            )
            as_of = self._migration_timestamp(row["as_of"])
            new_snapshot_id = _database_snapshot_id(
                str(row["scan_run_id"]),
                str(row["provider"]),
                provider_cache_key,
                as_of,
            )
            provider_ids[str(row["scan_run_id"])] = (
                old_snapshot_id,
                new_snapshot_id,
                provider_cache_key,
            )
            self._migration_insert(
                connection,
                f"""
                INSERT INTO {provider_table}(
                    snapshot_id,provider_cache_key,scan_run_id,provider,status,as_of,
                    session,schema_version,candidate_count,is_stale,warnings_json,
                    payload_json,created_at,expires_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_snapshot_id,
                    provider_cache_key,
                    row["scan_run_id"],
                    row["provider"],
                    row["status"],
                    as_of,
                    row["session"],
                    row["schema_version"],
                    row["candidate_count"],
                    row["is_stale"],
                    row["warnings_json"],
                    row["payload_json"],
                    row["created_at"],
                    row["expires_at"],
                ),
                table_name="breakout_provider_snapshots",
                record_key=str(row["scan_run_id"]),
            )

        for row in connection.execute(
            "SELECT * FROM breakout_transitions ORDER BY transition_id"
        ):
            scan_run_id = str(row["scan_run_id"])
            provider_identity = provider_ids.get(scan_run_id)
            if provider_identity is None:
                continue
            _old_snapshot_id, new_snapshot_id, provider_cache_key = provider_identity
            raw = str(row["transition_json"])
            parsed = self._migration_mapping(raw)
            minimal = {
                "transition_id": str(row["transition_id"]),
                "event_id": str(row["event_id"]),
                "from_state": str(row["from_state"] or "") or None,
                "to_state": str(row["to_state"]),
                "reason": str(row["reason"]),
                "evidence_at": self._migration_timestamp(row["evidence_at"]),
            }
            body = dict(parsed or minimal)
            evidence = (
                dict(body.get("evidence") or {})
                if isinstance(body.get("evidence"), Mapping)
                else {}
            )
            evidence["provider_cache_key"] = provider_cache_key
            evidence["source_snapshot_id"] = new_snapshot_id
            body.update(minimal)
            body["evidence"] = evidence
            encoded = _json_encode(body)
            error_code: str | None = None
            if parsed is None:
                error_code = "invalid_transition_json_shape"
            elif len(encoded.encode("utf-8")) > MAX_GENERIC_JSON_BYTES:
                try:
                    removed = self._remove_compactable_migration_fields(body)
                except RecursionError:
                    removed = []
                if removed:
                    body["migration_compacted_fields"] = removed[:256]
                    body["migration_compacted_field_count"] = len(removed)
                    encoded = _json_encode(body)
                if len(encoded.encode("utf-8")) > MAX_GENERIC_JSON_BYTES:
                    error_code = "oversized_transition_json"
            if error_code is not None:
                body = dict(minimal)
                body["evidence"] = evidence
                body["migration_warning"] = error_code
                encoded = _json_dumps(body, max_bytes=MAX_GENERIC_JSON_BYTES)
                self._quarantine_migration_value(
                    connection,
                    source_schema=source_version,
                    table_name="breakout_transitions",
                    record_key=str(row["transition_id"]),
                    error_code=error_code,
                    raw=raw,
                    recovered_json=encoded,
                )
            connection.execute(
                "UPDATE breakout_transitions SET transition_json=? WHERE transition_id=?",
                (encoded, row["transition_id"]),
            )

        event_meta: dict[str, dict[str, Any]] = {}
        for row in connection.execute(
            "SELECT * FROM breakout_events ORDER BY event_id"
        ):
            event_id = str(row["event_id"])
            latest_transition = connection.execute(
                """
                SELECT evidence_at FROM breakout_transitions
                WHERE event_id=? ORDER BY evidence_at DESC,transition_id DESC LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            trigger_transition = connection.execute(
                """
                SELECT evidence_at FROM breakout_transitions
                WHERE event_id=? AND to_state='TRIGGERED'
                ORDER BY evidence_at,transition_id LIMIT 1
                """,
                (event_id,),
            ).fetchone()
            lifecycle_state = str(row["lifecycle_state"])
            first_seen_at = self._migration_timestamp(
                row["first_seen_at"], row["event_at"]
            )
            last_seen_at = self._migration_timestamp(
                row["last_seen_at"], row["event_at"], first_seen_at
            )
            if source_version == LEGACY_SCHEMA_VERSION:
                was_triggered = (
                    lifecycle_state in _TRIGGERED_LIFECYCLE_STATES
                    or (
                        lifecycle_state == "EXPIRED"
                        and trigger_transition is not None
                    )
                )
                triggered_at = (
                    self._migration_timestamp(row["event_at"])
                    if was_triggered
                    else None
                )
                state_changed_at = self._migration_timestamp(
                    latest_transition["evidence_at"]
                    if latest_transition is not None
                    else None,
                    row["event_at"],
                    first_seen_at,
                )
            else:
                triggered_at = (
                    self._migration_timestamp(row["triggered_at"])
                    if row["triggered_at"] not in (None, "")
                    else None
                )
                if (
                    triggered_at is None
                    and lifecycle_state in _TRIGGERED_LIFECYCLE_STATES
                ):
                    triggered_at = self._migration_timestamp(row["event_at"])
                state_changed_at = self._migration_timestamp(
                    row["state_changed_at"], row["event_at"], first_seen_at
                )
            event_at = triggered_at or first_seen_at
            current_scan_run_id = str(row["current_scan_run_id"])
            provider_identity = provider_ids.get(
                current_scan_run_id,
                (str(row["source_snapshot_id"]), str(row["source_snapshot_id"]), ""),
            )
            source_snapshot_id = provider_identity[1]
            enrichment = {
                "event_id": event_id,
                "trading_date": str(row["trading_date"]),
                "ticker": str(row["ticker"]),
                "setup_type": str(row["setup_type"]),
                "pivot_id": str(row["pivot_id"]),
                "lifecycle_state": lifecycle_state,
                "event_at": event_at,
                "first_seen_at": first_seen_at,
                "triggered_at": triggered_at,
                "state_changed_at": state_changed_at,
                "last_seen_at": last_seen_at,
                "source_snapshot_id": source_snapshot_id,
            }
            scores = {
                "alert_priority_score": row["alert_priority_score"],
                "data_confidence_score": row["data_confidence_score"],
            }
            minimal = dict(enrichment)
            minimal["scores"] = {
                key: value for key, value in scores.items() if value is not None
            }
            raw = str(row["event_json"])
            parsed = self._migration_mapping(raw)
            event_json = self._migration_json(
                connection,
                source_schema=source_version,
                table_name="breakout_events",
                record_key=event_id,
                raw=raw,
                parsed=parsed,
                enrichment=enrichment,
                minimal=minimal,
                invalid_code="invalid_event_json",
                oversized_code="oversized_event_json",
            )
            self._migration_insert(
                connection,
                f"""
                INSERT INTO {event_table}(
                    event_id,trading_date,ticker,setup_type,pivot_id,lifecycle_state,
                    event_at,first_seen_at,triggered_at,state_changed_at,last_seen_at,
                    source_snapshot_id,alert_priority_score,data_confidence_score,
                    current_scan_run_id,event_json,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event_id,
                    row["trading_date"],
                    row["ticker"],
                    row["setup_type"],
                    row["pivot_id"],
                    lifecycle_state,
                    event_at,
                    first_seen_at,
                    triggered_at,
                    state_changed_at,
                    last_seen_at,
                    source_snapshot_id,
                    row["alert_priority_score"],
                    row["data_confidence_score"],
                    current_scan_run_id,
                    event_json,
                    row["created_at"],
                    row["updated_at"],
                ),
                table_name="breakout_events",
                record_key=event_id,
            )
            event_meta[event_id] = {
                **enrichment,
                "scores": minimal["scores"],
            }
        snapshot_rows = connection.execute(
            """
            SELECT snapshots.*,runs.published_at
            FROM breakout_scan_events AS snapshots
            JOIN breakout_scan_runs AS runs USING(scan_run_id)
            ORDER BY runs.published_at,snapshots.scan_run_id,snapshots.event_id
            """
        )
        for row in snapshot_rows:
            event_id = str(row["event_id"])
            raw = str(row["event_snapshot_json"])
            parsed = self._migration_mapping(raw)
            current_event = event_meta.get(event_id, {})
            lifecycle_state = str(
                (parsed or {}).get("lifecycle_state")
                or row["lifecycle_state"]
                or current_event.get("lifecycle_state")
                or "DISCOVERED"
            )
            old_event_at = self._migration_timestamp(
                (parsed or {}).get("event_at"), row["event_at"]
            )
            first_seen_at = self._migration_timestamp(
                (parsed or {}).get("first_seen_at"),
                current_event.get("first_seen_at"),
                old_event_at,
            )
            last_seen_at = self._migration_timestamp(
                (parsed or {}).get("last_seen_at"),
                old_event_at,
                current_event.get("last_seen_at"),
            )
            published_at = row["published_at"]
            trigger_transition = connection.execute(
                """
                SELECT evidence_at FROM breakout_transitions
                WHERE event_id=? AND to_state='TRIGGERED'
                  AND (? IS NULL OR evidence_at<=?)
                ORDER BY evidence_at,transition_id LIMIT 1
                """,
                (event_id, published_at, published_at),
            ).fetchone()
            latest_transition = connection.execute(
                """
                SELECT evidence_at FROM breakout_transitions
                WHERE event_id=? AND (? IS NULL OR evidence_at<=?)
                ORDER BY evidence_at DESC,transition_id DESC LIMIT 1
                """,
                (event_id, published_at, published_at),
            ).fetchone()
            if source_version == LEGACY_SCHEMA_VERSION:
                was_triggered = (
                    lifecycle_state in _TRIGGERED_LIFECYCLE_STATES
                    or (
                        lifecycle_state == "EXPIRED"
                        and trigger_transition is not None
                    )
                )
                triggered_at = old_event_at if was_triggered else None
                state_changed_at = self._migration_timestamp(
                    latest_transition["evidence_at"]
                    if latest_transition is not None
                    else None,
                    old_event_at,
                    first_seen_at,
                )
            else:
                parsed_triggered = (parsed or {}).get("triggered_at")
                triggered_at = (
                    self._migration_timestamp(parsed_triggered)
                    if parsed_triggered not in (None, "")
                    else current_event.get("triggered_at")
                )
                if (
                    triggered_at is None
                    and lifecycle_state in _TRIGGERED_LIFECYCLE_STATES
                ):
                    triggered_at = old_event_at
                state_changed_at = self._migration_timestamp(
                    (parsed or {}).get("state_changed_at"),
                    current_event.get("state_changed_at"),
                    old_event_at,
                )
            event_at = triggered_at or first_seen_at
            scan_run_id = str(row["scan_run_id"])
            provider_identity = provider_ids.get(
                scan_run_id,
                (
                    str(current_event.get("source_snapshot_id") or ""),
                    str(current_event.get("source_snapshot_id") or ""),
                    "",
                ),
            )
            source_snapshot_id = provider_identity[1]
            enrichment = {
                "event_id": event_id,
                "trading_date": str(
                    current_event.get("trading_date") or event_at[:10]
                ),
                "ticker": str(row["ticker"]),
                "session": str(row["session"]),
                "setup_type": str(row["setup_type"]),
                "pivot_id": str(current_event.get("pivot_id") or ""),
                "lifecycle_state": lifecycle_state,
                "event_at": event_at,
                "first_seen_at": first_seen_at,
                "triggered_at": triggered_at,
                "state_changed_at": state_changed_at,
                "last_seen_at": last_seen_at,
                "source_snapshot_id": source_snapshot_id,
            }
            minimal = dict(enrichment)
            scores = dict(current_event.get("scores") or {})
            if row["alert_priority_score"] is not None:
                scores["alert_priority_score"] = row["alert_priority_score"]
            if scores:
                minimal["scores"] = scores
            snapshot_json = self._migration_json(
                connection,
                source_schema=source_version,
                table_name="breakout_scan_events",
                record_key=f"{scan_run_id}:{event_id}",
                raw=raw,
                parsed=parsed,
                enrichment=enrichment,
                minimal=minimal,
                invalid_code="invalid_event_snapshot_json",
                oversized_code="oversized_event_snapshot_json",
            )
            self._migration_insert(
                connection,
                f"""
                INSERT INTO {snapshot_table}(
                    scan_run_id,event_id,rank,ticker,session,setup_type,lifecycle_state,
                    event_at,alert_priority_score,sort_priority,event_snapshot_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scan_run_id,
                    event_id,
                    row["rank"],
                    row["ticker"],
                    row["session"],
                    row["setup_type"],
                    lifecycle_state,
                    event_at,
                    row["alert_priority_score"],
                    row["sort_priority"],
                    snapshot_json,
                    row["created_at"],
                ),
                table_name="breakout_scan_events",
                record_key=f"{scan_run_id}:{event_id}",
            )

        self._migration_hook("tables_copied", connection)
        connection.execute("DROP TABLE breakout_scan_events")
        connection.execute("DROP TABLE breakout_events")
        connection.execute("DROP TABLE breakout_provider_snapshots")
        connection.execute(
            f"ALTER TABLE {provider_table} RENAME TO breakout_provider_snapshots"
        )
        connection.execute(f"ALTER TABLE {event_table} RENAME TO breakout_events")
        connection.execute(
            f"ALTER TABLE {snapshot_table} RENAME TO breakout_scan_events"
        )
        for scan_run_id, (_old_id, new_id, _cache_key) in provider_ids.items():
            connection.execute(
                """
                UPDATE breakout_scan_runs SET source_snapshot_id=?
                WHERE scan_run_id=?
                """,
                (new_id, scan_run_id),
            )
        self._migration_hook("tables_rebuilt", connection)

        connection.execute("DELETE FROM breakout_schema_version")
        connection.execute(
            """
            INSERT INTO breakout_schema_version(version,checksum,applied_at)
            VALUES(?,?,?)
            """,
            (SCHEMA_VERSION, SCHEMA_CHECKSUM, _timestamp(self._now())),
        )
        self._migration_hook("version_recorded", connection)

    def _require_schema(self, connection: sqlite3.Connection) -> None:
        try:
            rows = connection.execute(
                "SELECT version,checksum FROM breakout_schema_version ORDER BY version"
            ).fetchall()
        except sqlite3.Error as exc:
            raise SchemaVersionError(
                f"breakout database is missing schema {SCHEMA_VERSION}",
                current_version=None,
            ) from exc
        if len(rows) != 1:
            versions = ",".join(str(row["version"]) for row in rows)
            raise SchemaVersionError(
                f"breakout database requires {SCHEMA_VERSION}; found {versions or 'none'}",
                current_version=versions or None,
            )
        row = rows[0]
        version = str(row["version"])
        if version != SCHEMA_VERSION:
            raise SchemaVersionError(
                f"breakout database schema {version} requires migration to {SCHEMA_VERSION}",
                current_version=version,
            )
        if row["checksum"] != SCHEMA_CHECKSUM:
            raise SchemaVersionError(
                f"breakout database {SCHEMA_VERSION} checksum mismatch",
                current_version=version,
            )
        columns = self._event_table_columns(connection)
        required = {"triggered_at", "state_changed_at"}
        if not required.issubset(columns):
            missing = ",".join(sorted(required - columns))
            raise SchemaVersionError(
                f"breakout database {SCHEMA_VERSION} is missing columns: {missing}",
                current_version=version,
            )
        provider_columns = self._migration_table_columns(
            connection, "breakout_provider_snapshots"
        )
        if "provider_cache_key" not in provider_columns:
            raise SchemaVersionError(
                f"breakout database {SCHEMA_VERSION} is missing provider_cache_key",
                current_version=version,
            )
        quarantine = connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type='table' AND name='breakout_migration_quarantine'
            """
        ).fetchone()
        if quarantine is None:
            raise SchemaVersionError(
                f"breakout database {SCHEMA_VERSION} is missing migration quarantine",
                current_version=version,
            )
        for table_name, json_column in (
            ("breakout_events", "event_json"),
            ("breakout_scan_events", "event_snapshot_json"),
        ):
            definition = connection.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            sql = str(definition["sql"] if definition is not None else "")
            if (
                json_column not in sql
                or str(MAX_EVENT_JSON_BYTES) not in sql
                or "CAST" not in sql.upper()
            ):
                raise SchemaVersionError(
                    f"breakout database {SCHEMA_VERSION} has stale {json_column} bounds",
                    current_version=version,
                )

    def begin_scan(
        self,
        provider: str | Mapping[str, Any],
        session: Any = None,
        scheduled_at: datetime | None = None,
        config_hash: str | None = None,
        versions_hash: str | None = None,
        *,
        profile: Any = None,
        idempotency_key: str | None = None,
        scan_id: str | None = None,
        versions: Mapping[str, Any] | None = None,
        now: datetime | None = None,
        **aliases: Any,
    ) -> str:
        """Register a short ``running`` transaction and return its stable id."""
        if isinstance(provider, Mapping):
            values = dict(provider)
            provider = str(values.get("provider") or values.get("source") or "")
            session = values.get("session", session)
            scheduled_at = values.get("scheduled_at", scheduled_at)
            config_hash = values.get("config_hash", config_hash)
            versions_hash = values.get(
                "versions_hash", values.get("version_hash", versions_hash)
            )
            profile = values.get("profile", profile)
            idempotency_key = values.get("idempotency_key", idempotency_key)
            scan_id = values.get("scan_id", values.get("scan_run_id", scan_id))
            versions = values.get("versions", versions)
        versions_hash = versions_hash or aliases.pop("version_hash", None)
        if aliases:
            unknown = ", ".join(sorted(aliases))
            raise TypeError(f"unexpected begin_scan arguments: {unknown}")
        provider = str(provider).strip()
        session_text = str(_enum_value(session) or "").strip()
        profile_text = str(_enum_value(profile) or "").strip() or None
        if not provider or not session_text:
            raise ValueError("provider and session are required")
        current = self._now(now)
        schedule = _aware_utc(scheduled_at or current, field="scheduled_at")
        config_hash = str(config_hash or "unversioned")
        versions_json = _json_dumps(dict(versions or {}))
        versions_hash = str(versions_hash or _digest(dict(versions or {})))
        if idempotency_key is None:
            idempotency_key = _digest(
                {
                    "provider": provider,
                    "profile": profile_text,
                    "session": session_text,
                    "scheduled_at": _timestamp(schedule),
                    "config_hash": config_hash,
                    "versions_hash": versions_hash,
                }
            )
        idempotency_key = str(idempotency_key)
        scan_id = str(scan_id or f"scan_{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}")
        now_text = _timestamp(current)
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            row = connection.execute(
                "SELECT scan_run_id, status FROM breakout_scan_runs WHERE idempotency_key=?",
                (idempotency_key,),
            ).fetchone()
            if row is None:
                connection.execute(
                    """
                    INSERT INTO breakout_scan_runs(
                        scan_run_id,idempotency_key,provider,profile,session,scheduled_at,
                        started_at,status,config_hash,versions_hash,versions_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,'running',?,?,?,?,?)
                    """,
                    (
                        scan_id,
                        idempotency_key,
                        provider,
                        profile_text,
                        session_text,
                        _timestamp(schedule),
                        now_text,
                        config_hash,
                        versions_hash,
                        versions_json,
                        now_text,
                        now_text,
                    ),
                )
                result = scan_id
            else:
                result = str(row["scan_run_id"])
                if row["status"] in {"failed", "abandoned"}:
                    connection.execute(
                        """
                        UPDATE breakout_scan_runs
                        SET status='running',started_at=?,completed_at=NULL,published_at=NULL,
                            candidate_count=0,event_count=0,error_code=NULL,error_message=NULL,
                            updated_at=?
                        WHERE scan_run_id=?
                        """,
                        (now_text, now_text, result),
                    )
            connection.commit()
            return result
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    register_scan = begin_scan

    def fail_scan(
        self,
        scan_id: str,
        error_code: str,
        error_message: str | None = None,
        *,
        now: datetime | None = None,
    ) -> bool:
        current = _timestamp(self._now(now))
        message = None if error_message is None else str(error_message)[:1000]
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            cursor = connection.execute(
                """
                UPDATE breakout_scan_runs
                SET status='failed',completed_at=?,error_code=?,error_message=?,updated_at=?
                WHERE scan_run_id=? AND status='running'
                """,
                (current, str(error_code)[:120], message, current, str(scan_id)),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    mark_scan_failed = fail_scan

    def abandon_running_scans(
        self,
        *,
        owner_id: str,
        lease_token: int,
        except_scan_id: str | None = None,
        now: datetime | None = None,
        lock_name: str = DEFAULT_LOCK_NAME,
    ) -> int:
        current = _timestamp(self._now(now))
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            self._verify_lease(
                connection,
                owner_id,
                lease_token,
                current,
                lock_name,
            )
            if except_scan_id is None:
                cursor = connection.execute(
                    """
                    UPDATE breakout_scan_runs
                    SET status='abandoned',completed_at=?,error_code='worker_restarted',updated_at=?
                    WHERE status='running'
                    """,
                    (current, current),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE breakout_scan_runs
                    SET status='abandoned',completed_at=?,error_code='worker_restarted',updated_at=?
                    WHERE status='running' AND scan_run_id<>?
                    """,
                    (current, current, except_scan_id),
                )
            connection.commit()
            return cursor.rowcount
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def acquire_lock(
        self,
        lock_name: str,
        owner_id: str,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> int | None:
        if not lock_name or not owner_id:
            raise ValueError("lock_name and owner_id are required")
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = self._now(now)
        heartbeat = _timestamp(current)
        expires = _timestamp(current + timedelta(seconds=float(ttl_seconds)))
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            row = connection.execute(
                "SELECT owner_id,fencing_token,expires_at FROM breakout_worker_lock WHERE lock_name=?",
                (lock_name,),
            ).fetchone()
            if row is None:
                token = 1
                connection.execute(
                    """
                    INSERT INTO breakout_worker_lock(lock_name,owner_id,fencing_token,heartbeat_at,expires_at)
                    VALUES(?,?,?,?,?)
                    """,
                    (lock_name, owner_id, token, heartbeat, expires),
                )
            else:
                current_owner = row["owner_id"]
                expired = row["expires_at"] <= heartbeat
                if current_owner == owner_id and not expired:
                    token = int(row["fencing_token"])
                    connection.execute(
                        """
                        UPDATE breakout_worker_lock SET heartbeat_at=?,expires_at=?
                        WHERE lock_name=? AND owner_id=? AND fencing_token=?
                        """,
                        (heartbeat, expires, lock_name, owner_id, token),
                    )
                elif current_owner is None or expired:
                    token = int(row["fencing_token"]) + 1
                    connection.execute(
                        """
                        UPDATE breakout_worker_lock
                        SET owner_id=?,fencing_token=?,heartbeat_at=?,expires_at=?
                        WHERE lock_name=?
                        """,
                        (owner_id, token, heartbeat, expires, lock_name),
                    )
                else:
                    connection.rollback()
                    return None
            connection.commit()
            return token
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def heartbeat_lock(
        self,
        lock_name: str,
        owner_id: str,
        lease_token: int,
        ttl_seconds: float,
        now: datetime | None = None,
    ) -> bool:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        current = self._now(now)
        heartbeat = _timestamp(current)
        expires = _timestamp(current + timedelta(seconds=float(ttl_seconds)))
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            cursor = connection.execute(
                """
                UPDATE breakout_worker_lock SET heartbeat_at=?,expires_at=?
                WHERE lock_name=? AND owner_id=? AND fencing_token=? AND expires_at>?
                """,
                (heartbeat, expires, lock_name, owner_id, int(lease_token), heartbeat),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def release_lock(
        self,
        lock_name: str,
        owner_id: str,
        lease_token: int,
        now: datetime | None = None,
    ) -> bool:
        current = _timestamp(self._now(now))
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            cursor = connection.execute(
                """
                UPDATE breakout_worker_lock SET owner_id=NULL,heartbeat_at=?,expires_at=?
                WHERE lock_name=? AND owner_id=? AND fencing_token=?
                """,
                (current, current, lock_name, owner_id, int(lease_token)),
            )
            connection.commit()
            return cursor.rowcount == 1
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def acquire_lease(
        self,
        owner_id: str,
        ttl_seconds: float,
        *,
        lock_name: str = DEFAULT_LOCK_NAME,
        now: datetime | None = None,
    ) -> LeaseInfo | None:
        token = self.acquire_lock(lock_name, owner_id, ttl_seconds, now)
        if token is None:
            return None
        current = self._now(now)
        return LeaseInfo(
            lock_name=lock_name,
            owner_id=owner_id,
            fencing_token=token,
            heartbeat_at=current,
            expires_at=current + timedelta(seconds=float(ttl_seconds)),
        )

    def heartbeat_lease(self, lease: LeaseInfo, ttl_seconds: float, *, now: datetime | None = None) -> bool:
        return self.heartbeat_lock(
            lease.lock_name,
            lease.owner_id,
            lease.fencing_token,
            ttl_seconds,
            now,
        )

    def release_lease(self, lease: LeaseInfo, *, now: datetime | None = None) -> bool:
        return self.release_lock(
            lease.lock_name,
            lease.owner_id,
            lease.fencing_token,
            now,
        )

    def current_lock(self, lock_name: str = DEFAULT_LOCK_NAME) -> Mapping[str, Any] | None:
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            row = connection.execute(
                "SELECT * FROM breakout_worker_lock WHERE lock_name=?", (lock_name,)
            ).fetchone()
            return self._row_dict(row) if row is not None else None
        finally:
            connection.close()

    def _verify_lease(
        self,
        connection: sqlite3.Connection,
        owner_id: str | None,
        lease_token: int | None,
        now_text: str,
        lock_name: str = DEFAULT_LOCK_NAME,
    ) -> None:
        if owner_id is None and lease_token is None:
            active = connection.execute(
                """
                SELECT 1 FROM breakout_worker_lock
                WHERE lock_name=? AND owner_id IS NOT NULL AND expires_at>?
                """,
                (lock_name, now_text),
            ).fetchone()
            if active is not None:
                raise LeaseLostError("an active worker lease requires a matching fencing token")
            return
        if owner_id is None or lease_token is None:
            raise LeaseLostError("owner_id and lease_token must be supplied together")
        row = connection.execute(
            """
            SELECT 1 FROM breakout_worker_lock
            WHERE lock_name=? AND owner_id=? AND fencing_token=? AND expires_at>?
            """,
            (lock_name, owner_id, int(lease_token), now_text),
        ).fetchone()
        if row is None:
            raise LeaseLostError("worker lease expired or fencing token is stale")

    def _publish_hook(self, phase: str, connection: sqlite3.Connection) -> None:
        """Fault-injection seam used by transaction tests."""

    def publish_scan(
        self,
        scan_id: str,
        snapshot: Mapping[str, Any] | Any,
        owner_id: str | None = None,
        lease_token: int | None = None,
        *,
        now: datetime | None = None,
        lock_name: str = DEFAULT_LOCK_NAME,
    ) -> str:
        """Publish all scan attachments and mark completed in one transaction."""
        payload = _mapping(snapshot)
        current = self._now(now)
        now_text = _timestamp(current)
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            run = connection.execute(
                "SELECT * FROM breakout_scan_runs WHERE scan_run_id=?", (str(scan_id),)
            ).fetchone()
            if run is None:
                raise BreakoutRepositoryError(f"unknown scan: {scan_id}")
            if run["status"] == "completed":
                connection.rollback()
                return str(scan_id)
            if run["status"] != "running":
                raise BreakoutRepositoryError(
                    f"scan {scan_id} cannot publish from status {run['status']}"
                )
            self._verify_lease(connection, owner_id, lease_token, now_text, lock_name)

            provider_value = payload.get("provider_snapshot") or payload.get("discovery_snapshot")
            if provider_value is None and {
                "provider",
                "status",
                "as_of",
            }.issubset(payload):
                provider_value = payload
            provider_snapshot = _mapping(provider_value) if provider_value is not None else {}
            candidates = list(
                payload.get("candidates")
                or provider_snapshot.get("candidates")
                or ()
            )
            events = [_mapping(item) for item in (payload.get("events") or ())]
            structures = [_mapping(item) for item in (payload.get("structures") or ())]
            for event in events:
                if event.get("structure") is not None:
                    structures.append(_mapping(event["structure"]))
            transitions = [_mapping(item) for item in (payload.get("transitions") or ())]
            shadows = [
                _mapping(item)
                for item in (
                    payload.get("range_persistence_shadow")
                    or payload.get("shadows")
                    or ()
                )
            ]

            provider = str(provider_snapshot.get("provider") or run["provider"])
            provider_as_of = provider_snapshot.get("as_of") or run["scheduled_at"]
            provider_cache_key = str(
                provider_snapshot.get("cache_key")
                or provider_snapshot.get("snapshot_id")
                or payload.get("source_snapshot_id")
                or "provider_"
                + _digest(
                    {
                        "provider": provider,
                        "as_of": provider_as_of,
                    }
                )[:24]
            )
            snapshot_id = _database_snapshot_id(
                str(scan_id),
                provider,
                provider_cache_key,
                provider_as_of,
            )
            self._insert_provider_snapshot(
                connection,
                str(scan_id),
                run,
                snapshot_id,
                provider_cache_key,
                provider_snapshot,
                candidates,
                now_text,
            )
            self._publish_hook("provider_snapshot", connection)

            self._insert_candidates(connection, str(scan_id), candidates, now_text)
            self._insert_structures(connection, str(scan_id), structures, now_text)
            self._publish_hook("candidates_structures", connection)

            rejected_event_ids: set[str] = set()
            event_records, id_aliases = self._upsert_events(
                connection,
                str(scan_id),
                snapshot_id,
                events,
                transitions,
                now_text,
                rejected_event_ids=rejected_event_ids,
            )
            self._insert_transitions(
                connection,
                str(scan_id),
                snapshot_id,
                provider_cache_key,
                transitions,
                event_records,
                id_aliases,
                now_text,
                rejected_event_ids=rejected_event_ids,
            )
            self._reconcile_live_scan(
                connection, list(payload.get("realtime_events") or ()),
                list(payload.get("realtime_transitions") or ()), now_text,
            )
            self._publish_hook("events_transitions", connection)

            self._insert_scan_events(
                connection,
                str(scan_id),
                str(run["session"]),
                event_records,
                now_text,
            )
            self._insert_shadows(
                connection,
                str(scan_id),
                shadows,
                id_aliases,
                now_text,
            )
            health = payload.get("provider_health")
            self._upsert_provider_health_tx(
                connection,
                _mapping(health) if health is not None else self._health_from_snapshot(
                    provider_snapshot, str(run["provider"]), current
                ),
                now_text,
            )
            worker_status = payload.get("worker_status")
            if worker_status is not None:
                self._upsert_worker_status_tx(connection, _mapping(worker_status), now_text)
            self._publish_hook("attachments", connection)

            connection.execute(
                """
                UPDATE breakout_scan_runs
                SET status='completed',completed_at=?,published_at=?,candidate_count=?,
                    event_count=?,error_code=NULL,error_message=NULL,source_snapshot_id=?,updated_at=?
                WHERE scan_run_id=? AND status='running'
                """,
                (
                    now_text,
                    now_text,
                    len(candidates),
                    len(event_records),
                    snapshot_id,
                    now_text,
                    str(scan_id),
                ),
            )
            self._publish_hook("before_complete", connection)
            connection.commit()
            return str(scan_id)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _insert_provider_snapshot(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        run: sqlite3.Row,
        snapshot_id: str,
        provider_cache_key: str,
        provider_snapshot: Mapping[str, Any],
        candidates: Sequence[Any],
        now_text: str,
    ) -> None:
        body = dict(provider_snapshot)
        safe_candidates = []
        for value in candidates:
            candidate = _mapping(value)
            candidate.pop("raw_provider_fields", None)
            safe_candidates.append(candidate)
        body["candidates"] = safe_candidates
        warnings = list(body.get("warnings") or ())
        status = str(_enum_value(body.get("status")) or "unavailable")
        as_of = body.get("as_of") or run["scheduled_at"]
        session = str(_enum_value(body.get("session")) or run["session"])
        provider = str(body.get("provider") or run["provider"])
        schema_version = str(body.get("schema_version") or "unknown")
        is_stale = status == "stale" or bool(body.get("is_stale", False))
        connection.execute(
            """
            INSERT INTO breakout_provider_snapshots(
                snapshot_id,provider_cache_key,scan_run_id,provider,status,as_of,session,schema_version,
                candidate_count,is_stale,warnings_json,payload_json,created_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                snapshot_id,
                provider_cache_key,
                scan_id,
                provider,
                status,
                _timestamp(as_of),
                session,
                schema_version,
                len(candidates),
                int(is_stale),
                _json_dumps(warnings),
                _json_dumps(body, max_bytes=MAX_PROVIDER_JSON_BYTES),
                now_text,
                _timestamp(body["expires_at"]) if body.get("expires_at") else None,
            ),
        )

    def _insert_candidates(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        candidates: Sequence[Any],
        now_text: str,
    ) -> None:
        for value in candidates:
            candidate = _mapping(value)
            ticker = str(candidate.get("ticker") or "").strip().upper()
            if not ticker:
                raise ValueError("candidate ticker is required")
            raw = candidate.pop("raw_provider_fields", {}) or {}
            provider_timestamp = candidate.get("provider_timestamp")
            if provider_timestamp is None:
                raise ValueError(f"candidate {ticker} lacks provider_timestamp")
            connection.execute(
                """
                INSERT INTO breakout_candidates(
                    scan_run_id,ticker,source,provider_timestamp,candidate_json,
                    raw_provider_fields_json,created_at
                ) VALUES(?,?,?,?,?,?,?)
                """,
                (
                    scan_id,
                    ticker,
                    str(candidate.get("source") or "unknown"),
                    _timestamp(provider_timestamp),
                    _json_dumps(candidate),
                    _json_dumps(raw, max_bytes=MAX_DEBUG_JSON_BYTES),
                    now_text,
                ),
            )

    def _insert_structures(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        structures: Sequence[Mapping[str, Any]],
        now_text: str,
    ) -> None:
        seen: set[str] = set()
        for structure in structures:
            pivot_id = str(structure.get("pivot_id") or "")
            if not pivot_id or pivot_id in seen:
                continue
            seen.add(pivot_id)
            cutoff = structure.get("calculation_cutoff_at")
            if cutoff is None:
                raise ValueError(f"structure {pivot_id} lacks calculation_cutoff_at")
            connection.execute(
                """
                INSERT INTO breakout_structures(
                    scan_run_id,pivot_id,ticker,calculation_cutoff_at,structure_json,created_at
                ) VALUES(?,?,?,?,?,?)
                """,
                (
                    scan_id,
                    pivot_id,
                    str(structure.get("ticker") or "").upper(),
                    _timestamp(cutoff),
                    _json_dumps(structure, max_bytes=MAX_STRUCTURE_JSON_BYTES),
                    now_text,
                ),
            )

    @staticmethod
    def _score(event: Mapping[str, Any], name: str) -> float | None:
        scores_value = event.get("scores") or {}
        scores = _mapping(scores_value) if not isinstance(scores_value, Mapping) else scores_value
        value = scores.get(name)
        if value is None:
            return None
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ValueError(f"{name} must be finite")
        return number

    def _upsert_events(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        snapshot_id: str,
        events: Sequence[Mapping[str, Any]],
        transitions: Sequence[Mapping[str, Any]],
        now_text: str,
        *, rejected_event_ids: set[str] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        records: list[dict[str, Any]] = []
        aliases: dict[str, str] = {}
        trigger_hints: dict[str, str] = {}
        for transition in transitions:
            if str(_enum_value(transition.get("to_state")) or "") != "TRIGGERED":
                continue
            transition_event_id = str(transition.get("event_id") or "")
            evidence = transition.get("evidence_at") or transition.get("event_at")
            if not transition_event_id or evidence is None:
                continue
            evidence_at = _timestamp(evidence)
            current = trigger_hints.get(transition_event_id)
            if current is None or evidence_at < current:
                trigger_hints[transition_event_id] = evidence_at
        for incoming in events:
            event = dict(incoming)
            triggered_at_supplied = "triggered_at" in event
            incoming_event_at = event.get("event_at") or event.get("first_seen_at")
            if incoming_event_at is None:
                raise ValueError("event_at or first_seen_at is required")
            incoming_event_at = _timestamp(incoming_event_at)
            ticker = str(event.get("ticker") or "").strip().upper()
            setup = str(_enum_value(event.get("setup_type")) or "")
            pivot_id = str(event.get("pivot_id") or "")
            if not ticker or not setup or not pivot_id:
                raise ValueError("event ticker, setup_type and pivot_id are required")
            trading_date = str(event.get("trading_date") or incoming_event_at[:10])
            incoming_id = str(
                event.get("event_id")
                or "event_"
                + _digest(
                    {
                        "trading_date": trading_date,
                        "ticker": ticker,
                        "setup_type": setup,
                        "pivot_id": pivot_id,
                    }
                )[:24]
            )
            existing = connection.execute(
                """
                SELECT event_id,event_at,first_seen_at,triggered_at,state_changed_at,
                       last_seen_at,lifecycle_state,event_json
                FROM breakout_events
                WHERE event_id=?
                """,
                (incoming_id,),
            ).fetchone()
            if existing is None:
                existing = connection.execute(
                    """
                    SELECT event_id,event_at,first_seen_at,triggered_at,state_changed_at,
                           last_seen_at,lifecycle_state,event_json
                    FROM breakout_events
                    WHERE trading_date=? AND ticker=? AND setup_type=? AND pivot_id=?
                    """,
                    (trading_date, ticker, setup, pivot_id),
                ).fetchone()
            event_id = str(existing["event_id"]) if existing is not None else incoming_id
            aliases[incoming_id] = event_id
            event["event_id"] = event_id
            event["ticker"] = ticker
            event["setup_type"] = setup
            event["trading_date"] = trading_date
            event["lifecycle_state"] = str(
                _enum_value(event.get("lifecycle_state")) or "DISCOVERED"
            )
            incoming_first_seen_at = _timestamp(
                event.get("first_seen_at") or incoming_event_at
            )
            incoming_last_seen_at = _timestamp(
                event.get("last_seen_at") or incoming_event_at
            )
            incoming_triggered_at = (
                _timestamp(event["triggered_at"])
                if event.get("triggered_at") is not None
                else None
            )
            incoming_state_changed_at = (
                _timestamp(event["state_changed_at"])
                if event.get("state_changed_at") is not None
                else None
            )
            incoming_trigger_hint = trigger_hints.get(incoming_id)
            previous_state = str(_enum_value(event.get("previous_state")) or "")
            safe_transition_anchor = (
                incoming_trigger_hint
                or (
                    incoming_state_changed_at
                    if previous_state in {"DISCOVERED", "WATCHING"}
                    and event["lifecycle_state"] in _TRIGGERED_LIFECYCLE_STATES
                    else None
                )
            )
            if existing is None:
                first_seen_at = incoming_first_seen_at
                triggered_at = incoming_triggered_at
                if (
                    triggered_at is None
                    and event["lifecycle_state"] in _TRIGGERED_LIFECYCLE_STATES
                ):
                    if triggered_at_supplied:
                        triggered_at = safe_transition_anchor
                        if triggered_at is None:
                            raise ValueError(
                                "triggered_at cannot be null for a triggered "
                                "lifecycle state without first-trigger evidence"
                            )
                    else:
                        triggered_at = safe_transition_anchor or incoming_event_at
                state_changed_at = (
                    incoming_state_changed_at or incoming_event_at or first_seen_at
                )
            else:
                first_seen_at = str(existing["first_seen_at"])
                stored_trigger = connection.execute(
                    """
                    SELECT evidence_at FROM breakout_transitions
                    WHERE event_id=? AND to_state='TRIGGERED'
                    ORDER BY evidence_at,transition_id LIMIT 1
                    """,
                    (event_id,),
                ).fetchone()
                existing_body = _json_loads(existing["event_json"], {})
                existing_body_triggered = existing_body.get("triggered_at")
                triggered_at = (
                    str(existing["triggered_at"])
                    if existing["triggered_at"] is not None
                    else _timestamp(stored_trigger["evidence_at"])
                    if stored_trigger is not None
                    else _timestamp(existing_body_triggered)
                    if existing_body_triggered is not None
                    else str(existing["event_at"])
                    if str(existing["lifecycle_state"])
                    in _TRIGGERED_LIFECYCLE_STATES
                    else incoming_triggered_at
                )
                state_changed_at = str(
                    existing["state_changed_at"] or existing["event_at"]
                )
                if (
                    triggered_at is None
                    and event["lifecycle_state"] in _TRIGGERED_LIFECYCLE_STATES
                ):
                    if triggered_at_supplied:
                        triggered_at = safe_transition_anchor
                        if triggered_at is None:
                            raise ValueError(
                                "triggered_at cannot be null for a triggered "
                                "lifecycle state without first-trigger evidence"
                            )
                    elif str(existing["lifecycle_state"]) in {
                        "DISCOVERED",
                        "WATCHING",
                    }:
                        triggered_at = (
                            incoming_triggered_at
                            or safe_transition_anchor
                            or incoming_last_seen_at
                        )
                if event["lifecycle_state"] != str(existing["lifecycle_state"]):
                    state_changed_at = (
                        incoming_state_changed_at or incoming_last_seen_at
                    )
            event_at = triggered_at or first_seen_at
            event["event_at"] = event_at
            event["first_seen_at"] = first_seen_at
            event["triggered_at"] = triggered_at
            event["state_changed_at"] = state_changed_at
            event["last_seen_at"] = incoming_last_seen_at
            event["source_snapshot_id"] = str(snapshot_id)
            if (
                existing is not None
                and str(existing["lifecycle_state"]) in {"FAILED", "EXPIRED"}
                and event["lifecycle_state"] != str(existing["lifecycle_state"])
            ):
                # Terminal lifecycle states are monotonic.  A rediscovered
                # ticker may create a new event identity, but it must never
                # revive an old terminal row through an upsert race.
                if rejected_event_ids is not None:
                    rejected_event_ids.add(event_id)
                records.append(_json_loads(existing["event_json"], {}))
                continue
            if existing is not None and incoming_last_seen_at < existing["last_seen_at"]:
                if rejected_event_ids is not None:
                    rejected_event_ids.add(event_id)
                records.append(_json_loads(existing["event_json"], {}))
                continue
            priority = self._score(event, "alert_priority_score")
            confidence = self._score(event, "data_confidence_score")
            event_json = _json_dumps(event, max_bytes=MAX_EVENT_JSON_BYTES)
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO breakout_events(
                        event_id,trading_date,ticker,setup_type,pivot_id,lifecycle_state,
                        event_at,first_seen_at,triggered_at,state_changed_at,last_seen_at,
                        source_snapshot_id,
                        alert_priority_score,data_confidence_score,current_scan_run_id,
                        event_json,created_at,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        event_id,
                        trading_date,
                        ticker,
                        setup,
                        pivot_id,
                        event["lifecycle_state"],
                        event_at,
                        event["first_seen_at"],
                        event["triggered_at"],
                        event["state_changed_at"],
                        event["last_seen_at"],
                        event["source_snapshot_id"],
                        priority,
                        confidence,
                        scan_id,
                        event_json,
                        now_text,
                        now_text,
                    ),
                )
            else:
                connection.execute(
                    """
                    UPDATE breakout_events
                    SET trading_date=?,setup_type=?,pivot_id=?,lifecycle_state=?,
                        event_at=?,first_seen_at=?,triggered_at=?,state_changed_at=?,
                        last_seen_at=?,
                        source_snapshot_id=?,alert_priority_score=?,data_confidence_score=?,
                        current_scan_run_id=?,event_json=?,updated_at=?
                    WHERE event_id=?
                    """,
                    (
                        trading_date,
                        setup,
                        pivot_id,
                        event["lifecycle_state"],
                        event_at,
                        event["first_seen_at"],
                        event["triggered_at"],
                        event["state_changed_at"],
                        event["last_seen_at"],
                        event["source_snapshot_id"],
                        priority,
                        confidence,
                        scan_id,
                        event_json,
                        now_text,
                        event_id,
                    ),
                )
            records.append(event)
        return records, aliases

    def _insert_transitions(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        snapshot_id: str,
        provider_cache_key: str,
        transitions: Sequence[Mapping[str, Any]],
        events: Sequence[Mapping[str, Any]],
        aliases: Mapping[str, str],
        now_text: str,
        *, rejected_event_ids: set[str] | None = None,
    ) -> None:
        values = [dict(item) for item in transitions]
        explicit_event_ids = {
            aliases.get(str(item.get("event_id") or ""), str(item.get("event_id") or ""))
            for item in values
        }
        terminal_by_event_id = {
            str(event.get("event_id") or ""): str(
                _enum_value(event.get("lifecycle_state")) or ""
            )
            for event in events
            if str(_enum_value(event.get("lifecycle_state")) or "")
            in {"FAILED", "EXPIRED"}
        }
        for event in events:
            previous = _enum_value(event.get("previous_state"))
            current = _enum_value(event.get("lifecycle_state"))
            if (
                previous is not None
                and str(previous) != str(current)
                and str(event["event_id"]) not in explicit_event_ids
            ):
                values.append(
                    {
                        "event_id": event["event_id"],
                        "from_state": previous,
                        "to_state": current,
                        "reason": event.get("transition_reason") or "",
                        "evidence_at": event.get("state_changed_at")
                        or event.get("last_seen_at"),
                    }
                )
        for transition in values:
            incoming_id = str(transition.get("event_id") or "")
            event_id = aliases.get(incoming_id, incoming_id)
            if event_id in (rejected_event_ids or set()):
                continue
            if not event_id:
                raise ValueError("transition event_id is required")
            from_state = str(_enum_value(transition.get("from_state")) or "")
            to_state = str(_enum_value(transition.get("to_state")) or "")
            terminal_state = terminal_by_event_id.get(event_id)
            if terminal_state is not None and to_state != terminal_state:
                # _upsert_events may have preserved an older terminal row when
                # discovery rediscovered the same deterministic identity.  Do
                # not attach a synthetic revival transition to that row.
                continue
            reason = str(transition.get("reason") or "")[:120]
            evidence = transition.get("evidence_at") or transition.get("event_at")
            if not to_state or evidence is None:
                raise ValueError("transition to_state and evidence_at are required")
            evidence_at = _timestamp(evidence)
            transition_id = str(
                transition.get("transition_id")
                or "transition_"
                + _digest(
                    {
                        "event_id": event_id,
                        "from_state": from_state,
                        "to_state": to_state,
                        "reason": reason,
                        "evidence_at": evidence_at,
                    }
                )[:24]
            )
            body = dict(transition)
            body.update(
                {
                    "transition_id": transition_id,
                    "event_id": event_id,
                    "from_state": from_state or None,
                    "to_state": to_state,
                    "reason": reason,
                    "evidence_at": evidence_at,
                }
            )
            evidence_body = (
                dict(body.get("evidence") or {})
                if isinstance(body.get("evidence"), Mapping)
                else {}
            )
            evidence_body["provider_cache_key"] = str(provider_cache_key)
            evidence_body["source_snapshot_id"] = str(snapshot_id)
            body["evidence"] = evidence_body
            connection.execute(
                """
                INSERT INTO breakout_transitions(
                    transition_id,event_id,from_state,to_state,reason,evidence_at,
                    scan_run_id,transition_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?)
                ON CONFLICT(event_id,from_state,to_state,reason,evidence_at) DO NOTHING
                """,
                (
                    transition_id,
                    event_id,
                    from_state,
                    to_state,
                    reason,
                    evidence_at,
                    scan_id,
                    _json_dumps(body),
                    now_text,
                ),
            )

    def _insert_scan_events(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        session: str,
        events: Sequence[Mapping[str, Any]],
        now_text: str,
    ) -> None:
        ranked = sorted(
            events,
            key=lambda event: (
                str(event["event_at"]),
                self._score(event, "alert_priority_score")
                if self._score(event, "alert_priority_score") is not None
                else -1.0,
                str(event["event_id"]),
            ),
            reverse=True,
        )
        for rank, event in enumerate(ranked, 1):
            priority = self._score(event, "alert_priority_score")
            connection.execute(
                """
                INSERT INTO breakout_scan_events(
                    scan_run_id,event_id,rank,ticker,session,setup_type,lifecycle_state,
                    event_at,alert_priority_score,sort_priority,event_snapshot_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scan_id,
                    event["event_id"],
                    rank,
                    event["ticker"],
                    str(_enum_value(event.get("session")) or session),
                    event["setup_type"],
                    event["lifecycle_state"],
                    event["event_at"],
                    priority,
                    priority if priority is not None else -1.0,
                    _json_dumps(event, max_bytes=MAX_EVENT_JSON_BYTES),
                    now_text,
                ),
            )

    def _insert_shadows(
        self,
        connection: sqlite3.Connection,
        scan_id: str,
        shadows: Sequence[Mapping[str, Any]],
        aliases: Mapping[str, str],
        now_text: str,
    ) -> None:
        for shadow in shadows:
            incoming_id = str(shadow.get("event_id") or "")
            event_id = aliases.get(incoming_id, incoming_id)
            if not event_id:
                raise ValueError("range shadow event_id is required")
            production_rank = shadow.get("production_rank")
            hypothetical_rank = shadow.get("hypothetical_rank")
            rank_delta = shadow.get("rank_delta")
            version = shadow.get("version")
            feature_version = shadow.get("feature_version")
            if version and feature_version and str(version) != str(feature_version):
                raise ValueError("range shadow version fields disagree")
            if (
                rank_delta is None
                and production_rank is not None
                and hypothetical_rank is not None
            ):
                rank_delta = int(hypothetical_rank) - int(production_rank)
            connection.execute(
                """
                INSERT INTO range_persistence_shadow(
                    scan_run_id,event_id,ticker,production_score,hypothetical_score,
                    production_rank,hypothetical_rank,rank_delta,version,shadow_json,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    scan_id,
                    event_id,
                    str(shadow.get("ticker") or "").upper(),
                    shadow.get("production_score"),
                    shadow.get("hypothetical_score"),
                    production_rank,
                    hypothetical_rank,
                    rank_delta,
                    str(version or feature_version or "range-persistence-v1"),
                    _json_dumps(shadow),
                    now_text,
                ),
            )

    @staticmethod
    def _health_from_snapshot(
        snapshot: Mapping[str, Any], provider: str, now: datetime
    ) -> dict[str, Any]:
        status = str(_enum_value(snapshot.get("status")) or "unavailable")
        active = status == "active"
        return {
            "provider": str(snapshot.get("provider") or provider),
            "status": status,
            "consecutive_failures": 0 if active else 1,
            "last_success_at": now if active else None,
            "last_failure_at": None if active else now,
            "stale_snapshot_available": status == "stale",
            "details": {"warnings": list(snapshot.get("warnings") or ())},
        }

    def _upsert_provider_health_tx(
        self,
        connection: sqlite3.Connection,
        health: Mapping[str, Any],
        now_text: str,
    ) -> None:
        provider = str(health.get("provider") or "")
        if not provider:
            return
        status = str(_enum_value(health.get("status")) or "unavailable")
        details = health.get("details") or {}
        connection.execute(
            """
            INSERT INTO breakout_provider_health(
                provider,status,consecutive_failures,last_success_at,last_failure_at,
                circuit_open_until,stale_snapshot_available,error_code,details_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(provider) DO UPDATE SET
                status=excluded.status,
                consecutive_failures=excluded.consecutive_failures,
                last_success_at=COALESCE(excluded.last_success_at,breakout_provider_health.last_success_at),
                last_failure_at=COALESCE(excluded.last_failure_at,breakout_provider_health.last_failure_at),
                circuit_open_until=excluded.circuit_open_until,
                stale_snapshot_available=excluded.stale_snapshot_available,
                error_code=excluded.error_code,
                details_json=excluded.details_json,
                updated_at=excluded.updated_at
            """,
            (
                provider,
                status,
                int(health.get("consecutive_failures") or 0),
                _timestamp(health["last_success_at"]) if health.get("last_success_at") else None,
                _timestamp(health["last_failure_at"]) if health.get("last_failure_at") else None,
                _timestamp(health["circuit_open_until"])
                if health.get("circuit_open_until")
                else None,
                int(bool(health.get("stale_snapshot_available", False))),
                str(health["error_code"])[:120] if health.get("error_code") else None,
                _json_dumps(details),
                now_text,
            ),
        )

    def record_provider_health(
        self,
        health: Mapping[str, Any] | Any,
        *,
        now: datetime | None = None,
    ) -> None:
        current = _timestamp(self._now(now))
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            self._upsert_provider_health_tx(connection, _mapping(health), current)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _upsert_worker_status_tx(
        self,
        connection: sqlite3.Connection,
        status: Mapping[str, Any],
        now_text: str,
    ) -> None:
        worker_id = str(status.get("worker_id") or status.get("owner_id") or "")
        if not worker_id:
            raise ValueError("worker status requires worker_id")
        heartbeat = status.get("heartbeat_at") or now_text
        details = status.get("details") or {}
        connection.execute(
            """
            INSERT INTO breakout_worker_status(
                worker_id,mode,status,heartbeat_at,current_scan_run_id,
                last_completed_scan_run_id,last_completed_at,error_code,details_json,updated_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(worker_id) DO UPDATE SET
                mode=excluded.mode,status=excluded.status,heartbeat_at=excluded.heartbeat_at,
                current_scan_run_id=excluded.current_scan_run_id,
                last_completed_scan_run_id=COALESCE(
                    excluded.last_completed_scan_run_id,
                    breakout_worker_status.last_completed_scan_run_id
                ),
                last_completed_at=COALESCE(
                    excluded.last_completed_at,
                    breakout_worker_status.last_completed_at
                ),
                error_code=excluded.error_code,details_json=excluded.details_json,
                updated_at=excluded.updated_at
            """,
            (
                worker_id,
                str(status.get("mode") or "continuous"),
                str(status.get("status") or "unknown"),
                _timestamp(heartbeat),
                status.get("current_scan_run_id"),
                status.get("last_completed_scan_run_id"),
                _timestamp(status["last_completed_at"])
                if status.get("last_completed_at")
                else None,
                str(status["error_code"])[:120] if status.get("error_code") else None,
                _json_dumps(details),
                now_text,
            ),
        )

    def update_worker_status(
        self,
        worker_id: str,
        mode: str,
        status: str,
        *,
        heartbeat_at: datetime | None = None,
        current_scan_run_id: str | None = None,
        last_completed_scan_run_id: str | None = None,
        last_completed_at: datetime | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        current_dt = self._now(now)
        current = _timestamp(current_dt)
        values = {
            "worker_id": worker_id,
            "mode": mode,
            "status": status,
            "heartbeat_at": heartbeat_at or current_dt,
            "current_scan_run_id": current_scan_run_id,
            "last_completed_scan_run_id": last_completed_scan_run_id,
            "last_completed_at": last_completed_at,
            "error_code": error_code,
            "details": dict(details or {}),
        }
        connection = self._write_connection()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            self._upsert_worker_status_tx(connection, values, current)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _row_dict(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        for key in tuple(value):
            if key.endswith("_json"):
                value[key[:-5]] = _json_loads(value.pop(key), {})
            elif key in {"is_stale", "stale_snapshot_available"}:
                value[key] = bool(value[key])
        return value

    def open_read_connection(self) -> sqlite3.Connection:
        """Return a caller-owned ``mode=ro``/``query_only`` connection."""
        connection = self._read_connection()
        self._require_schema(connection)
        return connection

    connect_read_only = open_read_connection

    def latest_completed_scan(self) -> Mapping[str, Any] | None:
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            row = connection.execute(
                """
                SELECT * FROM breakout_scan_runs
                WHERE status='completed'
                ORDER BY published_at DESC,scan_run_id DESC LIMIT 1
                """
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            result = self._scan_result(connection, row)
            connection.commit()
            return result
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _scan_result(self, connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
        result = self._row_dict(row)
        provider = connection.execute(
            "SELECT * FROM breakout_provider_snapshots WHERE scan_run_id=?",
            (row["scan_run_id"],),
        ).fetchone()
        result["provider_snapshot"] = self._row_dict(provider) if provider is not None else None
        event_rows = connection.execute(
            """
            SELECT event_snapshot_json FROM breakout_scan_events
            WHERE scan_run_id=? ORDER BY rank
            """,
            (row["scan_run_id"],),
        ).fetchall()
        result["events"] = [_json_loads(item["event_snapshot_json"], {}) for item in event_rows]
        return result

    @staticmethod
    def _encode_cursor(
        scan_id: str,
        row: sqlite3.Row,
        filter_hash: str,
    ) -> str:
        value = {
            "v": _CURSOR_VERSION,
            "scan_run_id": scan_id,
            "event_at": row["event_at"],
            "priority": row["sort_priority"],
            "event_id": row["event_id"],
            "filter_hash": filter_hash,
        }
        signature = hashlib.sha256(
            (_json_dumps(value) + SCHEMA_CHECKSUM).encode("utf-8")
        ).hexdigest()[:32]
        value["signature"] = signature
        raw = _json_dumps(value).encode("utf-8")
        return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")

    @staticmethod
    def _decode_cursor(cursor: str) -> dict[str, Any]:
        try:
            padding = "=" * (-len(cursor) % 4)
            value = json.loads(base64.urlsafe_b64decode(cursor + padding).decode("utf-8"))
            signature = value.pop("signature", None) if isinstance(value, dict) else None
            expected = (
                hashlib.sha256(
                    (_json_dumps(value) + SCHEMA_CHECKSUM).encode("utf-8")
                ).hexdigest()[:32]
                if isinstance(value, dict)
                else ""
            )
            if (
                not isinstance(value, dict)
                or value.get("v") != _CURSOR_VERSION
                or not isinstance(value.get("scan_run_id"), str)
                or not isinstance(value.get("event_at"), str)
                or not isinstance(value.get("event_id"), str)
                or not isinstance(value.get("priority"), (int, float))
                or not isinstance(value.get("filter_hash"), str)
                or not isinstance(signature, str)
                or not hmac.compare_digest(signature, expected)
            ):
                raise ValueError
            return value
        except (
            ValueError,
            TypeError,
            UnicodeError,
            binascii.Error,
            json.JSONDecodeError,
        ) as exc:
            raise InvalidCursorError("invalid breakout event cursor") from exc

    def list_events(
        self,
        filters: Mapping[str, Any] | None = None,
        *,
        date: date | str | None = None,
        trading_date: date | str | None = None,
        ticker: str | None = None,
        setup_type: Any = None,
        lifecycle_state: Any = None,
        session: Any = None,
        min_priority: float | None = None,
        limit: int = 50,
        cursor: str | None = None,
        scan_run_id: str | None = None,
    ) -> Mapping[str, Any]:
        """List an immutable completed scan using a scan-bound stable cursor."""
        values = dict(filters or {})
        date = values.get("date", values.get("trading_date", date or trading_date))
        ticker = values.get("ticker", ticker)
        setup_type = values.get("setup_type", setup_type)
        lifecycle_state = values.get("lifecycle_state", lifecycle_state)
        session = values.get("session", session)
        min_priority = values.get("min_priority", min_priority)
        limit = int(values.get("limit", limit))
        cursor = values.get("cursor", cursor)
        scan_run_id = values.get("scan_run_id", scan_run_id)
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        normalized_filters = {
            "date": str(date) if date is not None else None,
            "ticker": str(ticker).strip().upper() if ticker is not None else None,
            "setup_type": str(_enum_value(setup_type)) if setup_type is not None else None,
            "lifecycle_state": str(_enum_value(lifecycle_state))
            if lifecycle_state is not None
            else None,
            "session": str(_enum_value(session)) if session is not None else None,
            "min_priority": float(min_priority) if min_priority is not None else None,
        }
        filter_hash = hashlib.sha256(
            _json_dumps(normalized_filters).encode("utf-8")
        ).hexdigest()[:24]
        cursor_data = self._decode_cursor(cursor) if cursor else None
        if cursor_data is not None:
            if cursor_data["filter_hash"] != filter_hash:
                raise InvalidCursorError("cursor filters do not match request")
            if scan_run_id is not None and scan_run_id != cursor_data["scan_run_id"]:
                raise InvalidCursorError("cursor belongs to a different scan")
            scan_run_id = cursor_data["scan_run_id"]

        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            if scan_run_id is None:
                scan = connection.execute(
                    """
                    SELECT scan_run_id FROM breakout_scan_runs
                    WHERE status='completed' ORDER BY published_at DESC,scan_run_id DESC LIMIT 1
                    """
                ).fetchone()
                if scan is None:
                    connection.commit()
                    return {
                        "scan_run_id": None,
                        "completed_scan": None,
                        "events": [],
                        "next_cursor": None,
                    }
                scan_run_id = str(scan["scan_run_id"])
            completed = connection.execute(
                """
                SELECT scan_run_id,provider,session,scheduled_at,completed_at,published_at,
                       candidate_count,event_count,versions_json
                FROM breakout_scan_runs WHERE scan_run_id=? AND status='completed'
                """,
                (scan_run_id,),
            ).fetchone()
            if completed is None:
                raise InvalidCursorError("cursor scan is unavailable or incomplete")

            clauses = ["scan_run_id=?"]
            params: list[Any] = [scan_run_id]
            if date is not None:
                clauses.append(
                    "json_extract(event_snapshot_json,'$.trading_date')=?"
                )
                params.append(str(date))
            if ticker is not None:
                clauses.append("ticker=?")
                params.append(str(ticker).strip().upper())
            if setup_type is not None:
                clauses.append("setup_type=?")
                params.append(str(_enum_value(setup_type)))
            if lifecycle_state is not None:
                clauses.append("lifecycle_state=?")
                params.append(str(_enum_value(lifecycle_state)))
            if session is not None:
                clauses.append("session=?")
                params.append(str(_enum_value(session)))
            if min_priority is not None:
                clauses.append("alert_priority_score>=?")
                params.append(float(min_priority))
            if cursor_data is not None:
                clauses.append(
                    """
                    (event_at<? OR (event_at=? AND sort_priority<?) OR
                     (event_at=? AND sort_priority=? AND event_id<?))
                    """
                )
                params.extend(
                    [
                        cursor_data["event_at"],
                        cursor_data["event_at"],
                        cursor_data["priority"],
                        cursor_data["event_at"],
                        cursor_data["priority"],
                        cursor_data["event_id"],
                    ]
                )
            params.append(limit + 1)
            rows = connection.execute(
                f"""
                SELECT * FROM breakout_scan_events WHERE {' AND '.join(clauses)}
                ORDER BY event_at DESC,sort_priority DESC,event_id DESC LIMIT ?
                """,
                params,
            ).fetchall()
            page = rows[:limit]
            events = [_json_loads(row["event_snapshot_json"], {}) for row in page]
            next_cursor = (
                self._encode_cursor(scan_run_id, page[-1], filter_hash)
                if len(rows) > limit and page
                else None
            )
            connection.commit()
            return {
                "scan_run_id": scan_run_id,
                "completed_scan": self._row_dict(completed),
                "events": events,
                "next_cursor": next_cursor,
            }
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def get_event(self, event_id: str) -> Mapping[str, Any] | None:
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            row = connection.execute(
                """
                SELECT e.event_json
                FROM breakout_events e
                WHERE e.event_id=? AND EXISTS(
                    SELECT 1 FROM breakout_scan_events se
                    JOIN breakout_scan_runs sr ON sr.scan_run_id=se.scan_run_id
                    WHERE se.event_id=e.event_id AND sr.status='completed'
                )
                """,
                (str(event_id),),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            event = _json_loads(row["event_json"], {})
            transition_rows = connection.execute(
                """
                SELECT transition_json FROM breakout_transitions
                WHERE event_id=? ORDER BY evidence_at,transition_id
                """,
                (str(event_id),),
            ).fetchall()
            event["transitions"] = [
                _json_loads(item["transition_json"], {}) for item in transition_rows
            ]
            connection.commit()
            return event
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def events_for_ticker(self, ticker: str, *, limit: int = 50) -> list[Mapping[str, Any]]:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            rows = connection.execute(
                """
                SELECT e.event_json FROM breakout_events e
                WHERE e.ticker=? AND EXISTS(
                    SELECT 1 FROM breakout_scan_events se
                    JOIN breakout_scan_runs sr ON sr.scan_run_id=se.scan_run_id
                    WHERE se.event_id=e.event_id AND sr.status='completed'
                )
                ORDER BY e.last_seen_at DESC,e.event_id DESC LIMIT ?
                """,
                (str(ticker).strip().upper(), limit),
            ).fetchall()
            return [_json_loads(row["event_json"], {}) for row in rows]
        finally:
            connection.close()

    ticker_events = events_for_ticker

    def latest_events_for_tickers(
        self,
        tickers: Sequence[str],
        *,
        per_ticker: int = 10,
    ) -> dict[str, list[Mapping[str, Any]]]:
        """Load prior published events in one bounded read transaction."""
        symbols = sorted(
            {
                str(ticker).strip().upper()
                for ticker in tickers
                if str(ticker).strip()
            }
        )
        if not symbols:
            return {}
        if len(symbols) > 200:
            raise ValueError("at most 200 tickers can be loaded")
        if per_ticker < 1 or per_ticker > 50:
            raise ValueError("per_ticker must be between 1 and 50")
        placeholders = ",".join("?" for _ in symbols)
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            rows = connection.execute(
                f"""
                WITH ranked AS (
                    SELECT ticker,event_json,
                           ROW_NUMBER() OVER(
                               PARTITION BY ticker
                               ORDER BY last_seen_at DESC,event_id DESC
                           ) AS position
                    FROM breakout_events
                    WHERE ticker IN ({placeholders}) AND EXISTS(
                        SELECT 1 FROM breakout_scan_events se
                        JOIN breakout_scan_runs sr ON sr.scan_run_id=se.scan_run_id
                        WHERE se.event_id=breakout_events.event_id
                          AND sr.status='completed'
                    )
                )
                SELECT ticker,event_json FROM ranked
                WHERE position<=?
                ORDER BY ticker,position
                """,
                (*symbols, per_ticker),
            ).fetchall()
            result: dict[str, list[Mapping[str, Any]]] = {symbol: [] for symbol in symbols}
            for row in rows:
                result[str(row["ticker"])].append(
                    _json_loads(row["event_json"], {})
                )
            return result
        finally:
            connection.close()

    def load_carryover_events(
        self,
        *,
        as_of: datetime,
        event_ttl_seconds: float,
        limit: int = 150,
        expired_due_limit: int = 40,
    ) -> CarryoverBatch:
        """Load a fair, point-in-time batch of non-terminal published events.

        Recent events are evaluated least-recently first.  A reserved, bounded
        lane also returns events whose lifecycle TTL has just elapsed so the
        service can publish the terminal ``EXPIRED`` transition instead of
        leaving stale non-terminal rows behind after a worker outage.
        """

        observed_at = _aware_utc(as_of, field="as_of")
        if event_ttl_seconds <= 0:
            raise ValueError("event_ttl_seconds must be positive")
        if limit < 1 or limit > 200:
            raise ValueError("carryover limit must be between 1 and 200")
        if expired_due_limit < 1 or expired_due_limit > min(limit, 40):
            raise ValueError(
                "expired_due_limit must be between 1 and min(limit, 40)"
            )
        ttl_cutoff = _timestamp(
            observed_at - timedelta(seconds=float(event_ttl_seconds))
        )
        observed_text = _timestamp(observed_at)
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            rows = connection.execute(
                _CARRYOVER_LATEST_ROWS_SQL,
                (
                    observed_text,
                    observed_text,
                    observed_text,
                    observed_text,
                ),
            ).fetchall()
            # Rank at most one compact row per logical event in Python. This
            # prevents SQLite from building a temporary B-tree containing
            # historical 512 KiB snapshots inside the 128 MiB production tmpfs.
            due_rows = sorted(
                (row for row in rows if str(row["first_seen_at"]) < ttl_cutoff),
                key=lambda row: (
                    str(row["first_seen_at"]),
                    str(row["event_id"]),
                ),
            )
            selected_due = due_rows[:expired_due_limit]
            live_capacity = limit - len(selected_due)
            live_rows = sorted(
                (row for row in rows if str(row["first_seen_at"]) >= ttl_cutoff),
                key=lambda row: (
                    str(row["last_seen_at"]),
                    str(row["event_id"]),
                ),
            )
            selected_live = live_rows[:live_capacity]
            selected = [*selected_due, *selected_live]
            snapshots: list[Mapping[str, Any]] = []
            for row in selected:
                snapshot = connection.execute(
                    _CARRYOVER_SNAPSHOT_SQL,
                    (int(row["snapshot_rowid"]),),
                ).fetchone()
                if snapshot is None:
                    raise BreakoutRepositoryError(
                        "selected carryover snapshot disappeared"
                    )
                snapshots.append(
                    _json_loads(snapshot["event_snapshot_json"], {})
                )
            return CarryoverBatch(
                events=tuple(snapshots),
                expired_due_event_ids=frozenset(
                    str(row["event_id"]) for row in selected_due
                ),
                has_more=(
                    len(due_rows) > len(selected_due)
                    or len(live_rows) > len(selected_live)
                ),
            )
        finally:
            connection.close()

    def active_events_for_carryover(
        self,
        *,
        as_of: datetime,
        event_ttl_seconds: float,
        limit: int = 150,
    ) -> list[Mapping[str, Any]]:
        """Compatibility view of :meth:`load_carryover_events`."""

        return list(
            self.load_carryover_events(
                as_of=as_of,
                event_ttl_seconds=event_ttl_seconds,
                limit=limit,
                expired_due_limit=min(limit, 40),
            ).events
        )

    def status(self) -> Mapping[str, Any]:
        """Read database, worker and Provider status without creating the file."""
        connection = self._read_connection()
        try:
            self._require_schema(connection)
            connection.execute("BEGIN")
            schema = connection.execute(
                "SELECT version,checksum,applied_at FROM breakout_schema_version"
            ).fetchone()
            latest = connection.execute(
                """
                SELECT scan_run_id,provider,session,scheduled_at,published_at,
                       candidate_count,event_count,versions_json
                FROM breakout_scan_runs WHERE status='completed'
                ORDER BY published_at DESC,scan_run_id DESC LIMIT 1
                """
            ).fetchone()
            lock = connection.execute(
                "SELECT * FROM breakout_worker_lock WHERE lock_name=?",
                (DEFAULT_LOCK_NAME,),
            ).fetchone()
            lock_owner = str(lock["owner_id"]) if lock is not None and lock["owner_id"] else None
            worker = (
                connection.execute(
                    "SELECT * FROM breakout_worker_status WHERE worker_id=?",
                    (lock_owner,),
                ).fetchone()
                if lock_owner is not None
                else connection.execute(
                    "SELECT * FROM breakout_worker_status ORDER BY updated_at DESC LIMIT 1"
                ).fetchone()
            )
            providers = connection.execute(
                "SELECT * FROM breakout_provider_health ORDER BY provider"
            ).fetchall()
            payload = {
                "database": {
                    "status": "active",
                    "path": str(self.path),
                    "schema_version": schema["version"],
                    "schema_checksum": schema["checksum"],
                    "applied_at": schema["applied_at"],
                },
                "latest_completed_scan": self._row_dict(latest) if latest is not None else None,
                "worker": self._row_dict(worker) if worker is not None else None,
                "provider_health": [self._row_dict(row) for row in providers],
                "worker_lock": self._row_dict(lock) if lock is not None else None,
            }
            connection.commit()
            return payload
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def prune_retention(
        self,
        *,
        owner_id: str,
        lease_token: int,
        raw_payload_hours: int = 24,
        scan_days: int = 30,
        batch_size: int = 500,
        now: datetime | None = None,
        lock_name: str = DEFAULT_LOCK_NAME,
    ) -> Mapping[str, int]:
        """Bound debug payload growth without deleting event history.

        Event rows, transitions and shadow research records are retained. Old
        scan attachments are removed only when a newer completed scan still
        references the same event.
        """
        if raw_payload_hours < 1 or scan_days < 1:
            raise ValueError("retention windows must be positive")
        if batch_size < 1 or batch_size > 5000:
            raise ValueError("batch_size must be between 1 and 5000")
        current = self._now(now)
        current_text = _timestamp(current)
        raw_cutoff = _timestamp(current - timedelta(hours=raw_payload_hours))
        scan_cutoff = _timestamp(current - timedelta(days=scan_days))
        connection = self._write_connection()
        counts = {"provider_payloads": 0, "candidate_raw": 0, "scan_attachments": 0}
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._require_schema(connection)
            self._verify_lease(
                connection,
                owner_id,
                lease_token,
                current_text,
                lock_name,
            )
            raw_ids = [
                str(row["scan_run_id"])
                for row in connection.execute(
                    """
                    SELECT scan_run_id FROM breakout_provider_snapshots
                    WHERE created_at<? AND payload_json<>'{"redacted":true}'
                    ORDER BY created_at LIMIT ?
                    """,
                    (raw_cutoff, batch_size),
                ).fetchall()
            ]
            if raw_ids:
                placeholders = ",".join("?" for _ in raw_ids)
                counts["provider_payloads"] = connection.execute(
                    f"UPDATE breakout_provider_snapshots SET payload_json='{{\"redacted\":true}}' "
                    f"WHERE scan_run_id IN ({placeholders})",
                    raw_ids,
                ).rowcount
                counts["candidate_raw"] = connection.execute(
                    f"UPDATE breakout_candidates SET raw_provider_fields_json='{{}}' "
                    f"WHERE scan_run_id IN ({placeholders}) AND raw_provider_fields_json<>'{{}}'",
                    raw_ids,
                ).rowcount

            old_ids = [
                str(row["scan_run_id"])
                for row in connection.execute(
                    """
                    SELECT run.scan_run_id FROM breakout_scan_runs AS run
                    WHERE run.status<>'running' AND run.updated_at<?
                      AND run.scan_run_id<>COALESCE(
                        (SELECT scan_run_id FROM breakout_scan_runs
                         WHERE status='completed'
                         ORDER BY published_at DESC,scan_run_id DESC LIMIT 1),
                        ''
                      )
                      AND (
                        EXISTS(
                          SELECT 1 FROM breakout_provider_snapshots provider
                          WHERE provider.scan_run_id=run.scan_run_id
                        )
                        OR EXISTS(
                          SELECT 1 FROM breakout_candidates candidate
                          WHERE candidate.scan_run_id=run.scan_run_id
                        )
                        OR EXISTS(
                          SELECT 1 FROM breakout_structures structure
                          WHERE structure.scan_run_id=run.scan_run_id
                        )
                        OR EXISTS(
                          SELECT 1 FROM breakout_scan_events old_event
                          WHERE old_event.scan_run_id=run.scan_run_id
                            AND EXISTS(
                              SELECT 1
                              FROM breakout_scan_events newer_event
                              JOIN breakout_scan_runs newer_run
                                ON newer_run.scan_run_id=newer_event.scan_run_id
                               AND newer_run.status='completed'
                              WHERE newer_event.event_id=old_event.event_id
                                AND (
                                  COALESCE(newer_run.published_at,newer_run.completed_at,
                                           newer_run.updated_at,'')
                                    > COALESCE(run.published_at,run.completed_at,
                                               run.updated_at,'')
                                  OR (
                                    COALESCE(newer_run.published_at,newer_run.completed_at,
                                             newer_run.updated_at,'')
                                      = COALESCE(run.published_at,run.completed_at,
                                                 run.updated_at,'')
                                    AND newer_run.scan_run_id>run.scan_run_id
                                  )
                                )
                            )
                        )
                      )
                    ORDER BY run.updated_at,run.scan_run_id LIMIT ?
                    """,
                    (scan_cutoff, batch_size),
                ).fetchall()
            ]
            if old_ids:
                placeholders = ",".join("?" for _ in old_ids)
                counts["scan_attachments"] += connection.execute(
                    f"""
                    DELETE FROM breakout_scan_events AS old
                    WHERE old.scan_run_id IN ({placeholders})
                      AND EXISTS(
                        SELECT 1 FROM breakout_scan_runs old_run
                        WHERE old_run.scan_run_id=old.scan_run_id
                          AND EXISTS(
                            SELECT 1
                            FROM breakout_scan_events newer
                            JOIN breakout_scan_runs newer_run
                              ON newer_run.scan_run_id=newer.scan_run_id
                             AND newer_run.status='completed'
                            WHERE newer.event_id=old.event_id
                              AND (
                                COALESCE(newer_run.published_at,newer_run.completed_at,
                                         newer_run.updated_at,'')
                                  > COALESCE(old_run.published_at,old_run.completed_at,
                                             old_run.updated_at,'')
                                OR (
                                  COALESCE(newer_run.published_at,newer_run.completed_at,
                                           newer_run.updated_at,'')
                                    = COALESCE(old_run.published_at,old_run.completed_at,
                                               old_run.updated_at,'')
                                  AND newer_run.scan_run_id>old_run.scan_run_id
                                )
                              )
                          )
                      )
                    """,
                    old_ids,
                ).rowcount
                counts["scan_attachments"] += connection.execute(
                    f"DELETE FROM breakout_structures WHERE scan_run_id IN ({placeholders})",
                    old_ids,
                ).rowcount
                counts["scan_attachments"] += connection.execute(
                    f"DELETE FROM breakout_candidates WHERE scan_run_id IN ({placeholders})",
                    old_ids,
                ).rowcount
                counts["scan_attachments"] += connection.execute(
                    f"DELETE FROM breakout_provider_snapshots WHERE scan_run_id IN ({placeholders})",
                    old_ids,
                ).rowcount
            connection.commit()
            return counts
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def checkpoint_passive(self) -> tuple[int, int, int]:
        connection = self._write_connection()
        try:
            row = connection.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
            return int(row[0]), int(row[1]), int(row[2])
        finally:
            connection.close()


__all__ = [
    "BreakoutRepository",
    "BreakoutRepositoryError",
    "CarryoverBatch",
    "DEFAULT_LOCK_NAME",
    "InvalidCursorError",
    "LEGACY_SCHEMA_CHECKSUM",
    "LEGACY_SCHEMA_VERSION",
    "LeaseInfo",
    "LeaseLostError",
    "ReadOnlyRepositoryError",
    "SCHEMA_CHECKSUM",
    "SCHEMA_VERSION",
    "SchemaVersionError",
    "V2_SCHEMA_CHECKSUM",
    "V2_SCHEMA_VERSION",
]
