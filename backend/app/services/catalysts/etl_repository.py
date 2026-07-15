from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from .etl_client import CalendarPage, NewsChangesPage


StreamName = Literal["news", "calendar"]
SCHEMA_VERSION = "macrolens-etl-local-v2"
EPOCH = "1970-01-01T00:00:00Z"
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS macrolens_etl_schema (
    version TEXT PRIMARY KEY,
    checksum TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macrolens_etl_state (
    stream TEXT PRIMARY KEY CHECK(stream IN ('news','calendar')),
    generation INTEGER NOT NULL DEFAULT 0 CHECK(generation >= 0),
    cursor TEXT,
    updated_after TEXT NOT NULL,
    pending_watermark_sequence INTEGER CHECK(
        pending_watermark_sequence IS NULL OR pending_watermark_sequence >= 0
    ),
    pending_watermark_as_of TEXT,
    pending_snapshot_token TEXT,
    completed_watermark_sequence INTEGER NOT NULL DEFAULT 0
        CHECK(completed_watermark_sequence >= 0),
    completed_as_of TEXT,
    reset_count INTEGER NOT NULL DEFAULT 0 CHECK(reset_count >= 0),
    last_success_at TEXT,
    last_error_code TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macrolens_etl_news (
    news_id INTEGER PRIMARY KEY CHECK(news_id >= 1),
    change_sequence INTEGER NOT NULL CHECK(change_sequence >= 1),
    deleted INTEGER NOT NULL CHECK(deleted IN (0,1)),
    source TEXT,
    title TEXT,
    summary TEXT,
    url TEXT,
    image_url TEXT,
    published_at TEXT,
    fetched_at TEXT,
    source_updated_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    content_hash TEXT,
    source_tickers_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    source_observations_json TEXT NOT NULL DEFAULT '[]',
    raw_json TEXT,
    synced_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_macrolens_etl_news_active
    ON macrolens_etl_news(deleted,available_at DESC,news_id DESC);

CREATE TABLE IF NOT EXISTS macrolens_etl_news_changes (
    change_sequence INTEGER PRIMARY KEY CHECK(change_sequence >= 1),
    news_id INTEGER NOT NULL CHECK(news_id >= 1),
    operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
    changed_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    payload_hash TEXT NOT NULL CHECK(length(payload_hash)=64),
    raw_json TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_macrolens_etl_news_changes_item
    ON macrolens_etl_news_changes(news_id,change_sequence DESC);

CREATE TABLE IF NOT EXISTS macrolens_etl_news_tombstones (
    news_id INTEGER NOT NULL CHECK(news_id >= 1),
    change_sequence INTEGER NOT NULL CHECK(change_sequence >= 1),
    deleted_at TEXT NOT NULL,
    source_updated_at TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    applied_at TEXT NOT NULL,
    PRIMARY KEY(news_id,change_sequence)
);

CREATE TABLE IF NOT EXISTS macrolens_etl_calendar_snapshots (
    snapshot_sequence INTEGER PRIMARY KEY CHECK(snapshot_sequence >= 1),
    snapshot_token TEXT NOT NULL,
    as_of TEXT NOT NULL,
    data_through TEXT,
    is_stale INTEGER NOT NULL CHECK(is_stale IN (0,1)),
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1)),
    last_ordinal INTEGER NOT NULL DEFAULT 0 CHECK(last_ordinal >= 0),
    synced_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS macrolens_etl_calendar_events (
    snapshot_sequence INTEGER NOT NULL
        REFERENCES macrolens_etl_calendar_snapshots(snapshot_sequence) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL CHECK(ordinal >= 1),
    event_id TEXT NOT NULL,
    scheduled_at_utc TEXT NOT NULL,
    title TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    PRIMARY KEY(snapshot_sequence,ordinal),
    UNIQUE(snapshot_sequence,event_id)
);
""".strip()
SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode("utf-8")).hexdigest()


class EtlRepositoryError(RuntimeError):
    pass


class EtlCheckpointConflict(EtlRepositoryError):
    pass


class EtlWatermarkConflict(EtlRepositoryError):
    pass


@dataclass(frozen=True)
class SyncState:
    stream: StreamName
    generation: int
    cursor: str | None
    updated_after: str
    pending_watermark_sequence: int | None
    pending_watermark_as_of: str | None
    pending_snapshot_token: str | None
    completed_watermark_sequence: int
    completed_as_of: str | None
    reset_count: int
    last_success_at: str | None
    last_error_code: str | None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise EtlWatermarkConflict(
            "macrolens_etl_checkpoint_time_is_invalid"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EtlWatermarkConflict("macrolens_etl_checkpoint_time_is_invalid")
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class CatalystEtlRepository:
    """Small transactional store for raw ETL input and resumable checkpoints."""

    def __init__(self, path: str | Path) -> None:
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
        observed = _utc_now()
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA_SQL)
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT checksum FROM macrolens_etl_schema WHERE version=?",
                    (SCHEMA_VERSION,),
                ).fetchone()
                if row is not None and str(row["checksum"]) != SCHEMA_CHECKSUM:
                    raise EtlRepositoryError(
                        "macrolens_etl_schema_checksum_mismatch"
                    )
                self._migrate_schema(connection)
                connection.execute(
                    """INSERT OR IGNORE INTO macrolens_etl_schema(
                           version,checksum,applied_at
                       ) VALUES(?,?,?)""",
                    (SCHEMA_VERSION, SCHEMA_CHECKSUM, observed),
                )
                for stream in ("news", "calendar"):
                    connection.execute(
                        """INSERT OR IGNORE INTO macrolens_etl_state(
                               stream,generation,cursor,updated_after,updated_at
                           ) VALUES(?,0,NULL,?,?)""",
                        (stream, EPOCH, observed),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute(
                "PRAGMA table_info(macrolens_etl_state)"
            ).fetchall()
        }
        if "generation" not in columns:
            connection.execute(
                """ALTER TABLE macrolens_etl_state
                   ADD COLUMN generation INTEGER NOT NULL DEFAULT 0
                       CHECK(generation >= 0)"""
            )

    @staticmethod
    def _state_from_row(row: sqlite3.Row) -> SyncState:
        return SyncState(
            stream=row["stream"],
            generation=int(row["generation"]),
            cursor=row["cursor"],
            updated_after=str(row["updated_after"]),
            pending_watermark_sequence=row["pending_watermark_sequence"],
            pending_watermark_as_of=row["pending_watermark_as_of"],
            pending_snapshot_token=row["pending_snapshot_token"],
            completed_watermark_sequence=int(row["completed_watermark_sequence"]),
            completed_as_of=row["completed_as_of"],
            reset_count=int(row["reset_count"]),
            last_success_at=row["last_success_at"],
            last_error_code=row["last_error_code"],
        )

    @staticmethod
    def _require_stream(stream: str) -> StreamName:
        if stream not in {"news", "calendar"}:
            raise ValueError("unknown ETL stream")
        return stream  # type: ignore[return-value]

    def state(self, stream: StreamName) -> SyncState:
        name = self._require_stream(stream)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM macrolens_etl_state WHERE stream=?", (name,)
            ).fetchone()
        if row is None:
            raise EtlRepositoryError("macrolens_etl_state_not_initialized")
        return self._state_from_row(row)

    @staticmethod
    def _assert_checkpoint(
        state: SyncState,
        *,
        expected_cursor: str | None,
        expected_generation: int,
    ) -> None:
        if (
            state.cursor != expected_cursor
            or state.generation != expected_generation
        ):
            raise EtlCheckpointConflict("macrolens_etl_checkpoint_changed")

    @staticmethod
    def _assert_frozen_news_window(state: SyncState, page: NewsChangesPage) -> None:
        if state.cursor is None:
            return
        if (
            state.pending_watermark_sequence != page.watermark.sequence
            or state.pending_watermark_as_of != page.watermark.as_of
        ):
            raise EtlWatermarkConflict("macrolens_etl_news_watermark_changed")

    @staticmethod
    def _assert_frozen_calendar_window(state: SyncState, page: CalendarPage) -> None:
        if state.cursor is None:
            return
        if (
            state.pending_watermark_sequence != page.watermark.sequence
            or state.pending_watermark_as_of != page.watermark.as_of
            or state.pending_snapshot_token != page.watermark.snapshot_token
        ):
            raise EtlWatermarkConflict("macrolens_etl_calendar_watermark_changed")

    @staticmethod
    def _assert_sequence_boundary(
        state: SyncState,
        *,
        watermark_sequence: int,
        next_after_sequence: int | None,
        next_updated_after: str | None,
        has_more: bool,
    ) -> None:
        completed = state.completed_watermark_sequence
        if watermark_sequence < completed:
            raise EtlWatermarkConflict("macrolens_etl_sequence_regressed")
        if has_more:
            return
        if (
            next_after_sequence is None
            or next_after_sequence != watermark_sequence
            or next_after_sequence < completed
            or next_updated_after is None
        ):
            raise EtlWatermarkConflict("macrolens_etl_checkpoint_regressed")
        if _parse_utc(next_updated_after) < _parse_utc(state.updated_after):
            raise EtlWatermarkConflict("macrolens_etl_checkpoint_time_regressed")

    def apply_news_page(
        self,
        page: NewsChangesPage,
        *,
        expected_cursor: str | None,
        expected_generation: int,
    ) -> dict[str, int | bool]:
        observed = _utc_now()
        upserts = 0
        deletes = 0
        replayed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM macrolens_etl_state WHERE stream='news'"
                ).fetchone()
                if row is None:
                    raise EtlRepositoryError("macrolens_etl_state_not_initialized")
                state = self._state_from_row(row)
                self._assert_checkpoint(
                    state,
                    expected_cursor=expected_cursor,
                    expected_generation=expected_generation,
                )
                self._assert_frozen_news_window(state, page)
                self._assert_sequence_boundary(
                    state,
                    watermark_sequence=page.watermark.sequence,
                    next_after_sequence=page.next_after_sequence,
                    next_updated_after=page.next_updated_after,
                    has_more=page.has_more,
                )
                if any(
                    change.sequence <= state.completed_watermark_sequence
                    for change in page.items
                ):
                    raise EtlWatermarkConflict(
                        "macrolens_etl_news_sequence_did_not_advance"
                    )
                for change in page.items:
                    raw = change.model_dump(mode="json")
                    raw_json = _json(raw)
                    payload_hash = hashlib.sha256(raw_json.encode()).hexdigest()
                    existing = connection.execute(
                        """SELECT payload_hash FROM macrolens_etl_news_changes
                           WHERE change_sequence=?""",
                        (change.sequence,),
                    ).fetchone()
                    if existing is not None and str(existing["payload_hash"]) != payload_hash:
                        raise EtlWatermarkConflict(
                            "macrolens_etl_change_sequence_was_reused"
                        )
                    is_replay = existing is not None
                    if is_replay:
                        replayed += 1
                    connection.execute(
                        """INSERT OR IGNORE INTO macrolens_etl_news_changes(
                               change_sequence,news_id,operation,changed_at,
                               source_updated_at,available_at,payload_hash,raw_json,applied_at
                           ) VALUES(?,?,?,?,?,?,?,?,?)""",
                        (
                            change.sequence,
                            change.news_id,
                            change.operation,
                            change.changed_at,
                            change.source_updated_at,
                            change.available_at,
                            payload_hash,
                            raw_json,
                            observed,
                        ),
                    )
                    if change.operation == "delete":
                        if not is_replay:
                            deletes += 1
                        connection.execute(
                            """INSERT OR IGNORE INTO macrolens_etl_news_tombstones(
                                   news_id,change_sequence,deleted_at,source_updated_at,
                                   raw_json,applied_at
                               ) VALUES(?,?,?,?,?,?)""",
                            (
                                change.news_id,
                                change.sequence,
                                change.available_at,
                                change.source_updated_at,
                                raw_json,
                                observed,
                            ),
                        )
                        connection.execute(
                            """INSERT INTO macrolens_etl_news(
                                   news_id,change_sequence,deleted,source_updated_at,
                                   available_at,synced_at
                               ) VALUES(?,?,1,?,?,?)
                               ON CONFLICT(news_id) DO UPDATE SET
                                   change_sequence=excluded.change_sequence,deleted=1,
                                   source_updated_at=excluded.source_updated_at,
                                   available_at=excluded.available_at,
                                   synced_at=excluded.synced_at
                               WHERE excluded.change_sequence>=macrolens_etl_news.change_sequence""",
                            (
                                change.news_id,
                                change.sequence,
                                change.source_updated_at,
                                change.available_at,
                                observed,
                            ),
                        )
                        continue

                    if not is_replay:
                        upserts += 1
                    assert change.news is not None
                    news = change.news
                    news_raw = news.model_dump(mode="json")
                    connection.execute(
                        """INSERT INTO macrolens_etl_news(
                               news_id,change_sequence,deleted,source,title,summary,url,
                               image_url,published_at,fetched_at,source_updated_at,
                               available_at,content_hash,source_tickers_json,sources_json,
                               source_observations_json,raw_json,synced_at
                           ) VALUES(?,?,0,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                           ON CONFLICT(news_id) DO UPDATE SET
                               change_sequence=excluded.change_sequence,
                               deleted=0,source=excluded.source,title=excluded.title,
                               summary=excluded.summary,url=excluded.url,
                               image_url=excluded.image_url,published_at=excluded.published_at,
                               fetched_at=excluded.fetched_at,
                               source_updated_at=excluded.source_updated_at,
                               available_at=excluded.available_at,
                               content_hash=excluded.content_hash,
                               source_tickers_json=excluded.source_tickers_json,
                               sources_json=excluded.sources_json,
                               source_observations_json=excluded.source_observations_json,
                               raw_json=excluded.raw_json,synced_at=excluded.synced_at
                           WHERE excluded.change_sequence >=
                                 macrolens_etl_news.change_sequence""",
                        (
                            news.id,
                            change.sequence,
                            news.source,
                            news.title,
                            news.summary,
                            news.url,
                            news.image_url,
                            news.published_at,
                            news.fetched_at,
                            change.source_updated_at,
                            change.available_at,
                            news.content_hash,
                            _json(news.source_tickers),
                            _json(news.sources),
                            _json(
                                [
                                    item.model_dump(mode="json")
                                    for item in news.source_observations
                                ]
                            ),
                            _json(news_raw),
                            observed,
                        ),
                    )

                if page.has_more:
                    connection.execute(
                        """UPDATE macrolens_etl_state SET
                               generation=generation+1,cursor=?,pending_watermark_sequence=?,
                               pending_watermark_as_of=?,pending_snapshot_token=NULL,
                               last_error_code=NULL,updated_at=?
                           WHERE stream='news'""",
                        (
                            page.next_cursor,
                            page.watermark.sequence,
                            page.watermark.as_of,
                            observed,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE macrolens_etl_state SET
                               generation=generation+1,cursor=NULL,updated_after=?,
                               pending_watermark_sequence=NULL,pending_watermark_as_of=NULL,
                               pending_snapshot_token=NULL,completed_watermark_sequence=?,
                               completed_as_of=?,last_success_at=?,last_error_code=NULL,
                               updated_at=?
                           WHERE stream='news'""",
                        (
                            page.next_updated_after,
                            page.next_after_sequence,
                            page.watermark.as_of,
                            observed,
                            observed,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "upserts": upserts,
            "deletes": deletes,
            "replayed": replayed,
            "complete": not page.has_more,
        }

    def apply_calendar_page(
        self,
        page: CalendarPage,
        *,
        expected_cursor: str | None,
        expected_generation: int,
    ) -> dict[str, int | bool]:
        observed = _utc_now()
        new_items = 0
        replayed = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                row = connection.execute(
                    "SELECT * FROM macrolens_etl_state WHERE stream='calendar'"
                ).fetchone()
                if row is None:
                    raise EtlRepositoryError("macrolens_etl_state_not_initialized")
                state = self._state_from_row(row)
                self._assert_checkpoint(
                    state,
                    expected_cursor=expected_cursor,
                    expected_generation=expected_generation,
                )
                self._assert_frozen_calendar_window(state, page)
                self._assert_sequence_boundary(
                    state,
                    watermark_sequence=page.watermark.sequence,
                    next_after_sequence=page.next_after_sequence,
                    next_updated_after=page.next_updated_after,
                    has_more=page.has_more,
                )
                sequence = page.watermark.sequence
                token = page.watermark.snapshot_token
                if page.items and sequence <= state.completed_watermark_sequence:
                    raise EtlWatermarkConflict(
                        "macrolens_etl_calendar_sequence_did_not_advance"
                    )
                if sequence:
                    existing = connection.execute(
                        """SELECT snapshot_token FROM macrolens_etl_calendar_snapshots
                           WHERE snapshot_sequence=?""",
                        (sequence,),
                    ).fetchone()
                    if existing is not None and str(existing["snapshot_token"]) != token:
                        raise EtlWatermarkConflict(
                            "macrolens_etl_calendar_sequence_was_reused"
                        )
                    should_restart_snapshot = (
                        expected_cursor is None
                        and (
                            bool(page.items)
                            or state.completed_watermark_sequence != sequence
                        )
                    )
                    prior_event_payloads = {
                        int(item["ordinal"]): str(item["raw_json"])
                        for item in connection.execute(
                            """SELECT ordinal,raw_json
                               FROM macrolens_etl_calendar_events
                               WHERE snapshot_sequence=?""",
                            (sequence,),
                        ).fetchall()
                    }
                    connection.execute(
                        """INSERT INTO macrolens_etl_calendar_snapshots(
                               snapshot_sequence,snapshot_token,as_of,data_through,is_stale,
                               complete,last_ordinal,synced_at
                           ) VALUES(?,?,?,?,?,0,0,?)
                           ON CONFLICT(snapshot_sequence) DO UPDATE SET
                               as_of=excluded.as_of,data_through=excluded.data_through,
                               is_stale=excluded.is_stale,synced_at=excluded.synced_at""",
                        (
                            sequence,
                            token,
                            page.watermark.as_of,
                            page.data_through,
                            int(page.is_stale),
                            observed,
                        ),
                    )
                    if should_restart_snapshot:
                        connection.execute(
                            """DELETE FROM macrolens_etl_calendar_events
                               WHERE snapshot_sequence=?""",
                            (sequence,),
                        )
                        connection.execute(
                            """UPDATE macrolens_etl_calendar_snapshots
                               SET complete=0,last_ordinal=0 WHERE snapshot_sequence=?""",
                            (sequence,),
                        )
                    for event in page.items:
                        event_json = _json(event.model_dump(mode="json"))
                        prior_payload = prior_event_payloads.get(event.ordinal)
                        if prior_payload is None:
                            new_items += 1
                        elif prior_payload == event_json:
                            replayed += 1
                        else:
                            raise EtlWatermarkConflict(
                                "macrolens_etl_calendar_event_was_reused"
                            )
                        connection.execute(
                            """INSERT INTO macrolens_etl_calendar_events(
                                   snapshot_sequence,ordinal,event_id,scheduled_at_utc,
                                   title,raw_json,synced_at
                               ) VALUES(?,?,?,?,?,?,?)
                               ON CONFLICT(snapshot_sequence,ordinal) DO UPDATE SET
                                   event_id=excluded.event_id,
                                   scheduled_at_utc=excluded.scheduled_at_utc,
                                   title=excluded.title,raw_json=excluded.raw_json,
                                   synced_at=excluded.synced_at""",
                            (
                                sequence,
                                event.ordinal,
                                event.event_id,
                                event.scheduled_at_utc,
                                event.title,
                                event_json,
                                observed,
                            ),
                        )
                    last_ordinal = max((item.ordinal for item in page.items), default=0)
                    connection.execute(
                        """UPDATE macrolens_etl_calendar_snapshots SET
                               complete=?,last_ordinal=MAX(last_ordinal,?),synced_at=?
                           WHERE snapshot_sequence=?""",
                        (int(not page.has_more), last_ordinal, observed, sequence),
                    )

                if page.has_more:
                    connection.execute(
                        """UPDATE macrolens_etl_state SET
                               generation=generation+1,cursor=?,pending_watermark_sequence=?,
                               pending_watermark_as_of=?,pending_snapshot_token=?,
                               last_error_code=NULL,updated_at=?
                           WHERE stream='calendar'""",
                        (
                            page.next_cursor,
                            sequence,
                            page.watermark.as_of,
                            token,
                            observed,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE macrolens_etl_state SET
                               generation=generation+1,cursor=NULL,updated_after=?,
                               pending_watermark_sequence=NULL,
                               pending_watermark_as_of=NULL,pending_snapshot_token=NULL,
                               completed_watermark_sequence=?,completed_as_of=?,
                               last_success_at=?,last_error_code=NULL,updated_at=?
                           WHERE stream='calendar'""",
                        (
                            page.next_updated_after,
                            page.next_after_sequence,
                            page.watermark.as_of,
                            observed,
                            observed,
                        ),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "items": new_items,
            "replayed": replayed,
            "complete": not page.has_more,
        }

    def reset_cursor(self, stream: StreamName, *, error_code: str) -> SyncState:
        name = self._require_stream(stream)
        observed = _utc_now()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE macrolens_etl_state SET
                       generation=generation+1,cursor=NULL,pending_watermark_sequence=NULL,
                       pending_watermark_as_of=NULL,pending_snapshot_token=NULL,
                       reset_count=reset_count+1,last_error_code=?,updated_at=?
                   WHERE stream=?""",
                (error_code[:100], observed, name),
            )
            connection.commit()
        return self.state(name)

    def record_error(self, stream: StreamName, error_code: str) -> None:
        name = self._require_stream(stream)
        with self._connect() as connection:
            connection.execute(
                """UPDATE macrolens_etl_state SET last_error_code=?,updated_at=?
                   WHERE stream=?""",
                (error_code[:100], _utc_now(), name),
            )
            connection.commit()

    def get_news(self, news_id: int, *, include_deleted: bool = False) -> dict[str, Any] | None:
        if isinstance(news_id, bool) or news_id < 1:
            raise ValueError("news_id must be positive")
        query = "SELECT * FROM macrolens_etl_news WHERE news_id=?"
        if not include_deleted:
            query += " AND deleted=0"
        with self._connect() as connection:
            row = connection.execute(query, (news_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in (
            "source_tickers_json",
            "sources_json",
            "source_observations_json",
            "raw_json",
        ):
            result[field.removesuffix("_json")] = (
                json.loads(result[field]) if result[field] is not None else None
            )
        return result

    def list_active_news(self, *, limit: int = 100) -> list[dict[str, Any]]:
        if isinstance(limit, bool) or not 1 <= limit <= 1_000:
            raise ValueError("active-news limit is invalid")
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM macrolens_etl_news WHERE deleted=0
                   ORDER BY available_at DESC,news_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        output = []
        for row in rows:
            item = dict(row)
            item["raw"] = json.loads(item["raw_json"])
            item["source_tickers"] = json.loads(item["source_tickers_json"])
            item["sources"] = json.loads(item["sources_json"])
            item["source_count"] = len(item["sources"])
            item["source_observations"] = json.loads(
                item["source_observations_json"]
            )
            output.append(item)
        return output

    def tombstones(self, news_id: int | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM macrolens_etl_news_tombstones"
        params: tuple[Any, ...] = ()
        if news_id is not None:
            query += " WHERE news_id=?"
            params = (news_id,)
        query += " ORDER BY change_sequence"
        with self._connect() as connection:
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def calendar_events(self, snapshot_sequence: int | None = None) -> list[dict[str, Any]]:
        with self._connect() as connection:
            sequence = snapshot_sequence
            if sequence is None:
                row = connection.execute(
                    """SELECT completed_watermark_sequence FROM macrolens_etl_state
                       WHERE stream='calendar'"""
                ).fetchone()
                sequence = int(row[0]) if row is not None else 0
            rows = connection.execute(
                """SELECT raw_json FROM macrolens_etl_calendar_events
                   WHERE snapshot_sequence=? ORDER BY ordinal""",
                (sequence,),
            ).fetchall()
        return [json.loads(row["raw_json"]) for row in rows]
