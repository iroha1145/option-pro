"""SQLite storage for Optix Macro Conditions.

Lives in its own file (``/data/macro-conditions.db``) because macro history has a
different lifecycle from breakout events: it can be backed up, migrated and
retained independently without touching the breakout schema, and without adding
a service or a container.

Durability rules enforced here:

* WAL, ``busy_timeout``, foreign keys on, ``synchronous=FULL``;
* migrations are idempotent and run inside one ``BEGIN IMMEDIATE`` with
  ``foreign_key_check`` before commit;
* a candidate snapshot set is published in a single transaction, so a failed
  refresh leaves the previous snapshot readable and never deletes old rows;
* no NaN or infinity is ever stored, and every JSON column is bounded and
  written with stable key ordering.
"""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence
from urllib.parse import quote

from .models import (
    CompositeSnapshot,
    EtfObservation,
    FactorSnapshot,
    MacroError,
    ModuleSnapshot,
    SeriesMetadata,
    SeriesObservation,
    SnapshotBundle,
    finite,
    iso_instant,
)
from .registry import SCORING_VERSION


#: v2 rekeys ETF observations on the value itself and adds the append-only
#: publication log. v1 keyed ETF rows on ``available_at``, which is stamped at
#: write time, so every refresh inserted a fresh row for an unchanged price --
#: 8 symbols x ~252 sessions, twice a day, forever. And factor/module/composite
#: rows were updated in place, so the store could answer "what does this past
#: date recompute to today" but never "what was published on that date", which
#: is the question a walk-forward test asks.
SCHEMA_VERSION = "macro-conditions-v2"
#: The version this database may be upgraded *from*.
SCHEMA_VERSION_PREVIOUS = "macro-conditions-v1"

HISTORY_BASIS_BACKFILL = "latest_revised_backfill"
HISTORY_BASIS_LOCAL = "local_point_in_time"
HISTORY_BASIS_MIXED = "mixed"

_MAX_JSON_BYTES = 64 * 1024

_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS macro_schema (
        version TEXT PRIMARY KEY,
        checksum TEXT NOT NULL,
        applied_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_series_revisions (
        series_id TEXT NOT NULL,
        observation_date TEXT NOT NULL,
        value REAL,
        value_hash TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        realtime_start TEXT,
        realtime_end TEXT,
        source_last_updated TEXT,
        frequency TEXT NOT NULL,
        units TEXT NOT NULL,
        history_basis TEXT NOT NULL
            CHECK(history_basis IN ('latest_revised_backfill','local_point_in_time')),
        PRIMARY KEY(series_id, observation_date, value_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_series_revisions_lookup
        ON macro_series_revisions(series_id, observation_date DESC, last_seen_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_etf_observations (
        symbol TEXT NOT NULL,
        observation_date TEXT NOT NULL,
        adjusted_close REAL NOT NULL,
        provider TEXT NOT NULL,
        value_hash TEXT NOT NULL,
        data_through TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        history_basis TEXT NOT NULL
            CHECK(history_basis IN ('latest_revised_backfill','local_point_in_time')),
        PRIMARY KEY(symbol, observation_date, provider, value_hash)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_etf_observations_lookup
        ON macro_etf_observations(symbol, observation_date DESC, last_seen_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_snapshot_publications (
        publication_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        snapshot_date TEXT NOT NULL,
        scoring_version TEXT NOT NULL,
        published_at TEXT NOT NULL,
        available_at TEXT,
        factor_payload_hash TEXT NOT NULL,
        module_payload_hash TEXT NOT NULL,
        composite_payload TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_snapshot_publications_lookup
        ON macro_snapshot_publications(snapshot_date DESC, published_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_sync_runs (
        run_id TEXT PRIMARY KEY,
        status TEXT NOT NULL
            CHECK(status IN ('running','succeeded','degraded','failed')),
        trigger TEXT NOT NULL
            CHECK(trigger IN ('scheduled','manual','initial_backfill')),
        started_at TEXT NOT NULL,
        completed_at TEXT,
        data_through TEXT,
        series_succeeded INTEGER NOT NULL DEFAULT 0,
        series_failed INTEGER NOT NULL DEFAULT 0,
        error_codes_json TEXT NOT NULL DEFAULT '[]',
        details_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_sync_runs_recent
        ON macro_sync_runs(started_at DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_factor_snapshots (
        snapshot_date TEXT NOT NULL,
        as_of TEXT NOT NULL,
        factor_id TEXT NOT NULL,
        module_id TEXT NOT NULL,
        raw_value REAL,
        raw_unit TEXT NOT NULL,
        signed_value REAL,
        score REAL,
        score_method TEXT NOT NULL,
        score_change_7d REAL,
        raw_change_7d REAL,
        confidence REAL,
        valid_observations INTEGER NOT NULL DEFAULT 0,
        history_basis TEXT,
        data_through TEXT,
        available_at TEXT,
        status TEXT NOT NULL,
        scoring_version TEXT NOT NULL,
        missing_inputs_json TEXT NOT NULL DEFAULT '[]',
        stale_inputs_json TEXT NOT NULL DEFAULT '[]',
        PRIMARY KEY(snapshot_date, factor_id, scoring_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_factor_snapshots_factor
        ON macro_factor_snapshots(factor_id, scoring_version, snapshot_date DESC)
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_module_snapshots (
        snapshot_date TEXT NOT NULL,
        as_of TEXT NOT NULL,
        module_id TEXT NOT NULL,
        score REAL,
        score_change_7d REAL,
        confidence REAL,
        valid_factor_count INTEGER NOT NULL DEFAULT 0,
        total_factor_count INTEGER NOT NULL DEFAULT 0,
        data_through TEXT,
        available_at TEXT,
        status TEXT NOT NULL,
        scoring_version TEXT NOT NULL,
        PRIMARY KEY(snapshot_date, module_id, scoring_version)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS macro_composite_snapshots (
        snapshot_date TEXT NOT NULL,
        as_of TEXT NOT NULL,
        score REAL,
        score_change_7d REAL,
        confidence REAL,
        regime TEXT,
        valid_module_count INTEGER NOT NULL DEFAULT 0,
        data_through TEXT,
        available_at TEXT,
        history_basis TEXT,
        status TEXT NOT NULL,
        scoring_version TEXT NOT NULL,
        PRIMARY KEY(snapshot_date, scoring_version)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_macro_composite_snapshots_recent
        ON macro_composite_snapshots(scoring_version, snapshot_date DESC)
    """,
)

SCHEMA_CHECKSUM = sha256(
    "\n".join(" ".join(statement.split()) for statement in _SCHEMA).encode("utf-8")
).hexdigest()


class MacroSchemaError(MacroError):
    def __init__(self, message: str) -> None:
        super().__init__("macro_store_unavailable", message)


def _safe_db_path(path: str | Path) -> Path:
    resolved = Path(path)
    if not resolved.is_absolute() or ".." in resolved.parts:
        raise MacroSchemaError("macro database path must be absolute")
    return resolved


def _stable_json(payload: Any) -> str:
    text = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    if len(text.encode("utf-8")) > _MAX_JSON_BYTES:
        raise MacroSchemaError("macro JSON payload exceeds its bound")
    return text


def _loads(text: Any, fallback: Any) -> Any:
    if not isinstance(text, str) or not text:
        return fallback
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return fallback


def _storable(value: Any) -> Optional[float]:
    """Reject NaN/infinity at the storage boundary, not after the fact."""

    if value is None:
        return None
    number = finite(value)
    if number is None:
        raise MacroSchemaError("macro values must be finite")
    return number


def _json_text(payload: Mapping[str, Any]) -> str:
    """Compact, key-sorted JSON so two identical publications compare equal."""

    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    if len(body.encode("utf-8")) > _MAX_JSON_BYTES:
        raise MacroSchemaError("macro publication payload too large")
    return body


def _payload_hash(rows: Sequence[tuple]) -> str:
    """Stable digest of a published payload, order-independent."""

    body = "\n".join(
        "|".join("" if part is None else str(part) for part in row)
        for row in sorted(rows, key=lambda row: tuple(str(part) for part in row))
    )
    return sha256(body.encode("utf-8")).hexdigest()


def _value_hash(value: Optional[float]) -> str:
    body = "null" if value is None else repr(float(value))
    return sha256(body.encode("utf-8")).hexdigest()[:32]


def _date_text(value: date | str | None) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


class MacroRepository:
    """Versioned macro store with atomic candidate publication."""

    def __init__(
        self,
        path: str | Path,
        *,
        read_only: bool = False,
        busy_timeout_ms: int = 5_000,
        clock: Any = None,
    ) -> None:
        self.path = _safe_db_path(path)
        self.read_only = bool(read_only)
        self.busy_timeout_ms = int(busy_timeout_ms)
        if self.busy_timeout_ms < 0:
            raise MacroSchemaError("busy_timeout_ms must be non-negative")
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    # -- connections -------------------------------------------------------

    def _now_text(self) -> str:
        return iso_instant(self._clock())

    def _write_connection(self) -> sqlite3.Connection:
        if self.read_only:
            raise MacroSchemaError("read-only macro repository cannot write")
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
            raise MacroError("macro_store_unavailable", "macro database is missing")
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

    @contextmanager
    def write(self) -> Iterator[sqlite3.Connection]:
        connection = self._write_connection()
        try:
            yield connection
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @contextmanager
    def read(self) -> Iterator[sqlite3.Connection]:
        connection = self._read_connection()
        try:
            yield connection
        finally:
            connection.close()

    # -- migration ---------------------------------------------------------

    def initialize(self) -> None:
        """Create or verify the schema. Safe to call repeatedly."""

        connection = self._write_connection()
        try:
            mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            if mode != "wal":
                raise MacroSchemaError(f"SQLite WAL mode is required, got {mode}")
            connection.execute("BEGIN IMMEDIATE")
            # Before the CREATE IF NOT EXISTS pass: an existing v1 table would
            # otherwise satisfy "IF NOT EXISTS" and silently keep its old shape.
            self._upgrade_etf_observations_to_v2(connection)
            for statement in _SCHEMA:
                connection.execute(statement)
            rows = connection.execute(
                "SELECT version,checksum FROM macro_schema ORDER BY version"
            ).fetchall()
            known = {str(row["version"]): str(row["checksum"]) for row in rows}
            if SCHEMA_VERSION not in known:
                connection.execute(
                    "INSERT INTO macro_schema(version,checksum,applied_at) VALUES(?,?,?)",
                    (SCHEMA_VERSION, SCHEMA_CHECKSUM, self._now_text()),
                )
            elif known[SCHEMA_VERSION] != SCHEMA_CHECKSUM:
                raise MacroSchemaError(f"{SCHEMA_VERSION} schema checksum mismatch")
            violations = connection.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise MacroSchemaError("macro migration foreign_key_check failed")
            connection.commit()
        except sqlite3.Error as exc:
            if connection.in_transaction:
                connection.rollback()
            raise MacroSchemaError("macro migration failed") from exc
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            if connection.in_transaction:
                connection.rollback()
            connection.close()

    @staticmethod
    def _upgrade_etf_observations_to_v2(connection: sqlite3.Connection) -> int:
        """Rekey ETF observations on the value, collapsing duplicate prices.

        v1 keyed rows on ``available_at``, which is stamped at write time, so
        ``INSERT OR IGNORE`` never ignored anything: an unchanged price was
        written again on every refresh -- 8 symbols x ~252 sessions, twice a day.
        The table shape now mirrors ``macro_series_revisions``: identity is the
        value, and re-seeing it only moves ``last_seen_at``.

        Rows are collapsed by (symbol, observation_date, provider, value), which
        is the same grouping the new primary key expresses, so this rewrite
        cannot merge two genuinely different prices. ``first_seen_at`` keeps the
        earliest ``available_at`` we ever recorded for that price -- discarding
        it would move the point-in-time visibility of history that is already
        stored, which is the one thing this table exists to preserve.

        Returns the number of rows the collapse removed. Idempotent: a database
        already on the v2 shape is left untouched.
        """

        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if "macro_etf_observations" not in tables:
            return 0
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(macro_etf_observations)"
            ).fetchall()
        }
        if "value_hash" in columns:
            return 0
        if "available_at" not in columns:
            raise MacroSchemaError(
                "macro_etf_observations has neither available_at nor value_hash"
            )

        before = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM macro_etf_observations"
            ).fetchone()["n"]
        )
        # Group by exactly what the new primary key expresses, so the rewrite
        # cannot merge two genuinely different prices.
        #
        # history_basis is *not* part of the grouping: the same price can appear
        # once as a backfill and again as a live observation, and those are one
        # price, not two. The merged row takes the basis of its earliest
        # sighting, because first_seen_at comes from that sighting -- keeping the
        # label attached to the timestamp it describes. Deciding it by insert
        # order instead would be non-deterministic and would sometimes label a
        # backfill-derived visibility as locally observed, which is the one
        # distinction this whole storage design rests on.
        rows = connection.execute(
            """SELECT symbol, observation_date, adjusted_close, provider,
                      MAX(data_through)   AS data_through,
                      MIN(fetched_at)     AS fetched_at,
                      MIN(available_at)   AS first_seen_at,
                      MAX(available_at)   AS last_seen_at,
                      (SELECT inner.history_basis
                         FROM macro_etf_observations AS inner
                        WHERE inner.symbol = outer.symbol
                          AND inner.observation_date = outer.observation_date
                          AND inner.provider = outer.provider
                          AND inner.adjusted_close = outer.adjusted_close
                        ORDER BY inner.available_at ASC, inner.rowid ASC
                        LIMIT 1)  AS history_basis
               FROM macro_etf_observations AS outer
               GROUP BY symbol, observation_date, provider, adjusted_close"""
        ).fetchall()

        connection.execute("ALTER TABLE macro_etf_observations RENAME TO macro_etf_observations_v1")
        connection.execute("DROP INDEX IF EXISTS idx_macro_etf_observations_lookup")
        connection.execute(
            """CREATE TABLE macro_etf_observations (
                   symbol TEXT NOT NULL,
                   observation_date TEXT NOT NULL,
                   adjusted_close REAL NOT NULL,
                   provider TEXT NOT NULL,
                   value_hash TEXT NOT NULL,
                   data_through TEXT NOT NULL,
                   fetched_at TEXT NOT NULL,
                   first_seen_at TEXT NOT NULL,
                   last_seen_at TEXT NOT NULL,
                   history_basis TEXT NOT NULL
                       CHECK(history_basis IN ('latest_revised_backfill','local_point_in_time')),
                   PRIMARY KEY(symbol, observation_date, provider, value_hash)
               )"""
        )
        for row in rows:
            connection.execute(
                """INSERT INTO macro_etf_observations(
                       symbol,observation_date,adjusted_close,provider,value_hash,
                       data_through,fetched_at,first_seen_at,last_seen_at,history_basis
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    row["symbol"],
                    row["observation_date"],
                    row["adjusted_close"],
                    row["provider"],
                    _value_hash(row["adjusted_close"]),
                    row["data_through"],
                    row["fetched_at"],
                    row["first_seen_at"],
                    row["last_seen_at"],
                    row["history_basis"],
                ),
            )
        after = int(
            connection.execute(
                "SELECT COUNT(*) AS n FROM macro_etf_observations"
            ).fetchone()["n"]
        )
        if after == 0 and before > 0:
            # Never trade real history for a tidier table.
            raise MacroSchemaError("macro ETF migration would have emptied the table")
        connection.execute("DROP TABLE macro_etf_observations_v1")
        return before - after

    def integrity_report(self) -> dict[str, Any]:
        with self.read() as connection:
            integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
            foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            versions = [
                str(row["version"])
                for row in connection.execute(
                    "SELECT version FROM macro_schema ORDER BY version"
                )
            ]
        return {
            "integrity_check": integrity,
            "foreign_key_violations": len(foreign_keys),
            "journal_mode": journal,
            "schema_versions": versions,
            "schema_version": SCHEMA_VERSION,
        }

    # -- series revisions --------------------------------------------------

    def record_series_revisions(
        self,
        metadata: SeriesMetadata,
        observations: Sequence[SeriesObservation],
        *,
        history_basis: str,
        observed_at: str | None = None,
    ) -> dict[str, int]:
        """Append changed observations; refresh ``last_seen_at`` for unchanged ones.

        A revised value becomes a new row keyed by its hash. The previous row is
        kept, so the local revision trail is immutable.
        """

        if history_basis not in {HISTORY_BASIS_BACKFILL, HISTORY_BASIS_LOCAL}:
            raise MacroSchemaError("unsupported history basis")
        seen_at = observed_at or self._now_text()
        inserted = 0
        touched = 0
        with self.write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for observation in observations:
                    value = _storable(observation.value)
                    digest = _value_hash(value)
                    existing = connection.execute(
                        """SELECT first_seen_at FROM macro_series_revisions
                           WHERE series_id=? AND observation_date=? AND value_hash=?""",
                        (
                            metadata.series_id,
                            observation.observation_date.isoformat(),
                            digest,
                        ),
                    ).fetchone()
                    if existing is None:
                        connection.execute(
                            """INSERT INTO macro_series_revisions(
                                   series_id,observation_date,value,value_hash,
                                   first_seen_at,last_seen_at,realtime_start,realtime_end,
                                   source_last_updated,frequency,units,history_basis
                               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (
                                metadata.series_id,
                                observation.observation_date.isoformat(),
                                value,
                                digest,
                                seen_at,
                                seen_at,
                                metadata.realtime_start,
                                metadata.realtime_end,
                                metadata.source_last_updated,
                                metadata.frequency_short,
                                metadata.units,
                                history_basis,
                            ),
                        )
                        inserted += 1
                    else:
                        connection.execute(
                            """UPDATE macro_series_revisions
                               SET last_seen_at=?, source_last_updated=?,
                                   realtime_start=?, realtime_end=?
                               WHERE series_id=? AND observation_date=? AND value_hash=?""",
                            (
                                seen_at,
                                metadata.source_last_updated,
                                metadata.realtime_start,
                                metadata.realtime_end,
                                metadata.series_id,
                                observation.observation_date.isoformat(),
                                digest,
                            ),
                        )
                        touched += 1
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise MacroSchemaError("macro revision write failed") from exc
        return {"inserted": inserted, "unchanged": touched}

    def active_series(self, series_id: str) -> list[dict[str, Any]]:
        """Latest revision per observation date, ascending by date.

        Two revisions can share a ``last_seen_at`` second, so the tiebreak
        continues on insertion order. Without it the same observation date could
        return twice and the choice would depend on the query plan.
        """

        with self.read() as connection:
            rows = connection.execute(
                """
                SELECT r.observation_date, r.value, r.first_seen_at, r.history_basis,
                       r.source_last_updated, r.units, r.frequency
                FROM macro_series_revisions AS r
                WHERE r.series_id=?
                  AND r.rowid = (
                      SELECT candidate.rowid FROM macro_series_revisions AS candidate
                      WHERE candidate.series_id = r.series_id
                        AND candidate.observation_date = r.observation_date
                      ORDER BY candidate.last_seen_at DESC, candidate.rowid DESC
                      LIMIT 1
                  )
                ORDER BY r.observation_date ASC
                """,
                (series_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def series_ids(self) -> list[str]:
        with self.read() as connection:
            return [
                str(row["series_id"])
                for row in connection.execute(
                    "SELECT DISTINCT series_id FROM macro_series_revisions ORDER BY series_id"
                )
            ]

    def series_coverage(self) -> dict[str, dict[str, Optional[str]]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT series_id, MIN(observation_date) AS earliest,
                          MAX(observation_date) AS latest
                   FROM macro_series_revisions GROUP BY series_id"""
            ).fetchall()
        return {
            str(row["series_id"]): {
                "earliest": row["earliest"],
                "latest": row["latest"],
            }
            for row in rows
        }

    def etf_coverage(self) -> dict[str, dict[str, Optional[str]]]:
        """Earliest/latest observation per ETF symbol.

        The backfill decision used to look only at FRED series (incremental
        review P1): once those existed, every ETF request dropped from ten years
        to one. An ETF whose first ten-year download failed therefore stayed on
        one year of history forever -- long enough to clear the minimum sample
        count, so it still produced a "historical percentile" computed against
        a single year.
        """

        with self.read() as connection:
            rows = connection.execute(
                """SELECT symbol, MIN(observation_date) AS earliest,
                          MAX(observation_date) AS latest
                   FROM macro_etf_observations GROUP BY symbol"""
            ).fetchall()
        return {
            str(row["symbol"]): {
                "earliest": row["earliest"],
                "latest": row["latest"],
            }
            for row in rows
        }

    # -- ETF observations --------------------------------------------------

    def record_etf_observations(
        self,
        observations: Sequence[EtfObservation],
        *,
        data_through: date,
        history_basis: str,
        observed_at: str | None = None,
    ) -> dict[str, int]:
        if history_basis not in {HISTORY_BASIS_BACKFILL, HISTORY_BASIS_LOCAL}:
            raise MacroSchemaError("unsupported history basis")
        seen_at = observed_at or self._now_text()
        inserted = 0
        touched = 0
        with self.write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for observation in observations:
                    value = _storable(observation.adjusted_close)
                    digest = _value_hash(value)
                    # Identity is the value, mirroring macro_series_revisions.
                    # Keying on the write-time stamp meant OR IGNORE never
                    # ignored anything and an unchanged price was stored again
                    # on every refresh (incremental review P2). Re-seeing a price
                    # is not a revision; it only moves last_seen_at.
                    cursor = connection.execute(
                        """UPDATE macro_etf_observations
                           SET last_seen_at=?, data_through=?
                           WHERE symbol=? AND observation_date=? AND provider=?
                                 AND value_hash=?""",
                        (
                            seen_at,
                            data_through.isoformat(),
                            observation.symbol,
                            observation.observation_date.isoformat(),
                            observation.provider,
                            digest,
                        ),
                    )
                    if cursor.rowcount:
                        touched += int(cursor.rowcount)
                        continue
                    connection.execute(
                        """INSERT INTO macro_etf_observations(
                               symbol,observation_date,adjusted_close,provider,value_hash,
                               data_through,fetched_at,first_seen_at,last_seen_at,history_basis
                           ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (
                            observation.symbol,
                            observation.observation_date.isoformat(),
                            value,
                            observation.provider,
                            digest,
                            data_through.isoformat(),
                            seen_at,
                            seen_at,
                            seen_at,
                            history_basis,
                        ),
                    )
                    inserted += 1
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise MacroSchemaError("macro ETF write failed") from exc
        return {"inserted": inserted, "unchanged": touched}

    @staticmethod
    def _etf_columns(connection: sqlite3.Connection) -> tuple[str, str]:
        """(first-visible column, newest-revision column) for the shape on disk.

        The migration runs in the worker, inside ``refresh()``. The API process
        opens the same database read-only and ships in the same release, so
        between a deploy and the worker next macro run a v2 read path would meet
        a v1 table and the macro panel would report unavailable for hours.
        Reading whichever shape is present removes that window instead of
        relying on the two containers starting in a particular order.
        """

        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(macro_etf_observations)")
        }
        if "first_seen_at" in columns:
            return "first_seen_at", "last_seen_at"
        return "available_at", "available_at"

    def active_etf(self, symbol: str) -> list[dict[str, Any]]:
        """Latest recorded close per observation date, ascending by date.

        ``available_at`` is kept in the projection under that name: it is the
        moment this price first became visible, which is what the alignment
        layer means by the field, and after the v2 rekey that value lives in
        ``first_seen_at``. The newest *revision* is still chosen by
        ``last_seen_at`` -- when a provider restates a close, the restated row is
        the current one even though it was first seen later.
        """

        with self.read() as connection:
            first_seen, newest = self._etf_columns(connection)
            rows = connection.execute(
                f"""
                SELECT o.observation_date, o.adjusted_close, o.provider,
                       o.{first_seen} AS available_at, o.history_basis
                FROM macro_etf_observations AS o
                WHERE o.symbol=?
                  AND o.rowid = (
                      SELECT candidate.rowid FROM macro_etf_observations AS candidate
                      WHERE candidate.symbol = o.symbol
                        AND candidate.observation_date = o.observation_date
                      ORDER BY candidate.{newest} DESC, candidate.rowid DESC
                      LIMIT 1
                  )
                ORDER BY o.observation_date ASC
                """,
                (symbol,),
            ).fetchall()
        return [dict(row) for row in rows]

    # -- sync runs ---------------------------------------------------------

    def start_sync_run(self, run_id: str, trigger: str, *, started_at: str | None = None) -> str:
        started = started_at or self._now_text()
        with self.write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """INSERT INTO macro_sync_runs(run_id,status,trigger,started_at)
                       VALUES(?,?,?,?)""",
                    (run_id, "running", trigger, started),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise MacroSchemaError("macro sync run insert failed") from exc
        return started

    def finish_sync_run(
        self,
        run_id: str,
        *,
        status: str,
        data_through: date | str | None,
        series_succeeded: int,
        series_failed: int,
        error_codes: Sequence[str],
        details: Mapping[str, Any],
        completed_at: str | None = None,
    ) -> None:
        with self.write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                connection.execute(
                    """UPDATE macro_sync_runs
                       SET status=?, completed_at=?, data_through=?,
                           series_succeeded=?, series_failed=?,
                           error_codes_json=?, details_json=?
                       WHERE run_id=?""",
                    (
                        status,
                        completed_at or self._now_text(),
                        _date_text(data_through),
                        int(series_succeeded),
                        int(series_failed),
                        _stable_json(sorted({str(code) for code in error_codes})),
                        _stable_json(dict(details)),
                        run_id,
                    ),
                )
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise MacroSchemaError("macro sync run update failed") from exc

    def latest_sync_run(self) -> Optional[dict[str, Any]]:
        with self.read() as connection:
            row = connection.execute(
                "SELECT * FROM macro_sync_runs ORDER BY started_at DESC, run_id DESC LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        item["error_codes"] = _loads(item.pop("error_codes_json", "[]"), [])
        item["details"] = _loads(item.pop("details_json", "{}"), {})
        return item

    def recent_sync_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        bounded = max(1, min(int(limit), 50))
        with self.read() as connection:
            rows = connection.execute(
                """SELECT run_id,status,trigger,started_at,completed_at,data_through,
                          series_succeeded,series_failed,error_codes_json
                   FROM macro_sync_runs ORDER BY started_at DESC, run_id DESC LIMIT ?""",
                (bounded,),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["error_codes"] = _loads(item.pop("error_codes_json", "[]"), [])
            output.append(item)
        return output

    # -- snapshot publication ---------------------------------------------

    def publish(self, bundle: SnapshotBundle, *, run_id: str) -> dict[str, int]:
        """Replace this scoring version's snapshots in one transaction.

        Rows are upserted rather than deleted-then-inserted, so a reader that
        opens between statements still sees a complete previous snapshot, and a
        failed refresh never has to remove data to recover.
        """

        if bundle.scoring_version != SCORING_VERSION:
            raise MacroSchemaError("candidate scoring version does not match the code")
        # A publication that cannot name its run is not evidence of anything.
        if not run_id:
            raise MacroSchemaError("publication requires the sync run that produced it")
        with self.write() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                for factor in bundle.factors:
                    connection.execute(
                        """INSERT INTO macro_factor_snapshots(
                               snapshot_date,as_of,factor_id,module_id,raw_value,raw_unit,
                               signed_value,score,score_method,score_change_7d,raw_change_7d,
                               confidence,valid_observations,history_basis,data_through,
                               available_at,status,scoring_version,missing_inputs_json,
                               stale_inputs_json
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(snapshot_date,factor_id,scoring_version) DO UPDATE SET
                               as_of=excluded.as_of,
                               module_id=excluded.module_id,
                               raw_value=excluded.raw_value,
                               raw_unit=excluded.raw_unit,
                               signed_value=excluded.signed_value,
                               score=excluded.score,
                               score_method=excluded.score_method,
                               score_change_7d=excluded.score_change_7d,
                               raw_change_7d=excluded.raw_change_7d,
                               confidence=excluded.confidence,
                               valid_observations=excluded.valid_observations,
                               history_basis=excluded.history_basis,
                               data_through=excluded.data_through,
                               available_at=excluded.available_at,
                               status=excluded.status,
                               missing_inputs_json=excluded.missing_inputs_json,
                               stale_inputs_json=excluded.stale_inputs_json""",
                        (
                            factor.snapshot_date.isoformat(),
                            factor.as_of,
                            factor.factor_id,
                            factor.module_id,
                            _storable(factor.raw_value),
                            factor.raw_unit,
                            _storable(factor.signed_value),
                            _storable(factor.score),
                            factor.score_method,
                            _storable(factor.score_change_7d),
                            _storable(factor.raw_change_7d),
                            _storable(factor.confidence),
                            int(factor.valid_observations),
                            factor.history_basis,
                            factor.data_through,
                            factor.available_at,
                            factor.status,
                            factor.scoring_version,
                            _stable_json(list(factor.missing_inputs)),
                            _stable_json(list(factor.stale_inputs)),
                        ),
                    )
                for module in bundle.modules:
                    connection.execute(
                        """INSERT INTO macro_module_snapshots(
                               snapshot_date,as_of,module_id,score,score_change_7d,confidence,
                               valid_factor_count,total_factor_count,data_through,available_at,
                               status,scoring_version
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(snapshot_date,module_id,scoring_version) DO UPDATE SET
                               as_of=excluded.as_of,
                               score=excluded.score,
                               score_change_7d=excluded.score_change_7d,
                               confidence=excluded.confidence,
                               valid_factor_count=excluded.valid_factor_count,
                               total_factor_count=excluded.total_factor_count,
                               data_through=excluded.data_through,
                               available_at=excluded.available_at,
                               status=excluded.status""",
                        (
                            module.snapshot_date.isoformat(),
                            module.as_of,
                            module.module_id,
                            _storable(module.score),
                            _storable(module.score_change_7d),
                            _storable(module.confidence),
                            int(module.valid_factor_count),
                            int(module.total_factor_count),
                            module.data_through,
                            module.available_at,
                            module.status,
                            module.scoring_version,
                        ),
                    )
                for composite in bundle.composites:
                    connection.execute(
                        """INSERT INTO macro_composite_snapshots(
                               snapshot_date,as_of,score,score_change_7d,confidence,regime,
                               valid_module_count,data_through,available_at,history_basis,
                               status,scoring_version
                           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(snapshot_date,scoring_version) DO UPDATE SET
                               as_of=excluded.as_of,
                               score=excluded.score,
                               score_change_7d=excluded.score_change_7d,
                               confidence=excluded.confidence,
                               regime=excluded.regime,
                               valid_module_count=excluded.valid_module_count,
                               data_through=excluded.data_through,
                               available_at=excluded.available_at,
                               history_basis=excluded.history_basis,
                               status=excluded.status""",
                        (
                            composite.snapshot_date.isoformat(),
                            composite.as_of,
                            _storable(composite.score),
                            _storable(composite.score_change_7d),
                            _storable(composite.confidence),
                            composite.regime,
                            int(composite.valid_module_count),
                            composite.data_through,
                            composite.available_at,
                            composite.history_basis,
                            composite.status,
                            composite.scoring_version,
                        ),
                    )
                publications = self._record_publications(connection, bundle, run_id)
                connection.commit()
            except sqlite3.Error as exc:
                connection.rollback()
                raise MacroSchemaError("macro snapshot publication failed") from exc
        return {
            "factors": len(bundle.factors),
            "modules": len(bundle.modules),
            "composites": len(bundle.composites),
            "publications": publications,
        }

    @staticmethod
    def _record_publications(
        connection: sqlite3.Connection,
        bundle: SnapshotBundle,
        run_id: str,
    ) -> int:
        """Append what was published, alongside the current-view upsert.

        The snapshot tables answer "what does this date recompute to, given
        everything known today". They cannot answer "what did the system publish
        on that date", because a later refresh overwrites the row in place
        (incremental review P1). A walk-forward test asks the second question,
        and answering it with the first silently feeds the test revisions that
        were not visible at the time.

        This log is append-only and never read by the display path, so a
        publication row can never change a score anyone already saw. It stores
        payload hashes rather than the payloads: enough to prove which numbers
        were published and to detect a rewrite, without a second copy of the
        data to drift.
        """

        if not bundle.composites:
            return 0
        # One row per run, for the newest snapshot date in the bundle.
        #
        # A bundle carries the whole recomputed history grid; appending all of it
        # on every run would copy ~2000 rows twice a day and would answer the
        # wrong question anyway. "What did the system publish on that date" is
        # about the snapshot that was current when the run finished. The earlier
        # grid dates are recomputation, and they are already in the snapshot
        # tables as the current view.
        latest = max(bundle.composites, key=lambda row: _date_text(row.snapshot_date) or "")
        recorded = 0
        for composite in (latest,):
            factor_rows = [
                factor for factor in bundle.factors
                if factor.snapshot_date == composite.snapshot_date
            ]
            module_rows = [
                module for module in bundle.modules
                if module.snapshot_date == composite.snapshot_date
            ]
            publication_id = f"mpb_{uuid.uuid4().hex}"
            connection.execute(
                """INSERT INTO macro_snapshot_publications(
                       publication_id,run_id,snapshot_date,scoring_version,
                       published_at,available_at,factor_payload_hash,
                       module_payload_hash,composite_payload
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    publication_id,
                    run_id,
                    _date_text(composite.snapshot_date),
                    composite.scoring_version,
                    bundle.as_of,
                    composite.available_at,
                    _payload_hash(
                        [(row.factor_id, row.score, row.status) for row in factor_rows]
                    ),
                    _payload_hash(
                        [(row.module_id, row.score, row.status) for row in module_rows]
                    ),
                    _json_text(
                        {
                            "score": composite.score,
                            "confidence": composite.confidence,
                            "regime": composite.regime,
                            "valid_module_count": composite.valid_module_count,
                            "status": composite.status,
                            "data_through": composite.data_through,
                        }
                    ),
                ),
            )
            recorded += 1
        return recorded

    # -- reads -------------------------------------------------------------

    def latest_composite(
        self,
        *,
        scoring_version: str = SCORING_VERSION,
        on_or_before: date | None = None,
    ) -> Optional[dict[str, Any]]:
        clause = "AND snapshot_date<=?" if on_or_before is not None else ""
        parameters: list[Any] = [scoring_version]
        if on_or_before is not None:
            parameters.append(on_or_before.isoformat())
        with self.read() as connection:
            row = connection.execute(
                f"""SELECT * FROM macro_composite_snapshots
                    WHERE scoring_version=? AND score IS NOT NULL {clause}
                    ORDER BY snapshot_date DESC LIMIT 1""",
                tuple(parameters),
            ).fetchone()
        return dict(row) if row is not None else None

    def composite_history(
        self,
        *,
        start: date,
        end: date,
        scoring_version: str = SCORING_VERSION,
        limit: int = 4_000,
    ) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT snapshot_date,score,confidence,regime,history_basis,status,
                          data_through,valid_module_count
                   FROM macro_composite_snapshots
                   WHERE scoring_version=? AND snapshot_date>=? AND snapshot_date<=?
                     AND score IS NOT NULL
                   ORDER BY snapshot_date ASC LIMIT ?""",
                (
                    scoring_version,
                    start.isoformat(),
                    end.isoformat(),
                    max(1, min(int(limit), 4_000)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def modules_for_dates(
        self,
        dates: Sequence[date],
        *,
        scoring_version: str = SCORING_VERSION,
    ) -> dict[str, dict[str, Optional[float]]]:
        if not dates:
            return {}
        keys = [value.isoformat() for value in dates]
        placeholders = ",".join("?" for _ in keys)
        with self.read() as connection:
            rows = connection.execute(
                f"""SELECT snapshot_date,module_id,score FROM macro_module_snapshots
                    WHERE scoring_version=? AND snapshot_date IN ({placeholders})""",
                (scoring_version, *keys),
            ).fetchall()
        grouped: dict[str, dict[str, Optional[float]]] = {key: {} for key in keys}
        for row in rows:
            grouped.setdefault(str(row["snapshot_date"]), {})[
                str(row["module_id"])
            ] = row["score"]
        return grouped

    def modules_at(
        self,
        snapshot_date: date,
        *,
        scoring_version: str = SCORING_VERSION,
    ) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT * FROM macro_module_snapshots
                   WHERE scoring_version=? AND snapshot_date=?
                   ORDER BY module_id ASC""",
                (scoring_version, snapshot_date.isoformat()),
            ).fetchall()
        return [dict(row) for row in rows]

    def factors_at(
        self,
        snapshot_date: date,
        *,
        module_id: str | None = None,
        scoring_version: str = SCORING_VERSION,
    ) -> list[dict[str, Any]]:
        clause = "AND module_id=?" if module_id else ""
        parameters: list[Any] = [scoring_version, snapshot_date.isoformat()]
        if module_id:
            parameters.append(module_id)
        with self.read() as connection:
            rows = connection.execute(
                f"""SELECT * FROM macro_factor_snapshots
                    WHERE scoring_version=? AND snapshot_date=? {clause}
                    ORDER BY factor_id ASC""",
                tuple(parameters),
            ).fetchall()
        output: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["missing_inputs"] = _loads(item.pop("missing_inputs_json", "[]"), [])
            item["stale_inputs"] = _loads(item.pop("stale_inputs_json", "[]"), [])
            output.append(item)
        return output

    def factor_history(
        self,
        factor_id: str,
        *,
        start: date,
        end: date,
        scoring_version: str = SCORING_VERSION,
        limit: int = 4_000,
    ) -> list[dict[str, Any]]:
        with self.read() as connection:
            rows = connection.execute(
                """SELECT snapshot_date,raw_value,signed_value,score,status,data_through,
                          history_basis,valid_observations
                   FROM macro_factor_snapshots
                   WHERE factor_id=? AND scoring_version=?
                     AND snapshot_date>=? AND snapshot_date<=?
                   ORDER BY snapshot_date ASC LIMIT ?""",
                (
                    factor_id,
                    scoring_version,
                    start.isoformat(),
                    end.isoformat(),
                    max(1, min(int(limit), 4_000)),
                ),
            ).fetchall()
        return [dict(row) for row in rows]

    def snapshot_dates(
        self,
        *,
        scoring_version: str = SCORING_VERSION,
    ) -> tuple[Optional[str], Optional[str]]:
        with self.read() as connection:
            row = connection.execute(
                """SELECT MIN(snapshot_date) AS earliest, MAX(snapshot_date) AS latest
                   FROM macro_composite_snapshots WHERE scoring_version=?""",
                (scoring_version,),
            ).fetchone()
        if row is None:
            return None, None
        return row["earliest"], row["latest"]


__all__ = [
    "HISTORY_BASIS_BACKFILL",
    "HISTORY_BASIS_LOCAL",
    "HISTORY_BASIS_MIXED",
    "SCHEMA_CHECKSUM",
    "SCHEMA_VERSION",
    "MacroRepository",
    "MacroSchemaError",
]
