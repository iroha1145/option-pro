"""Shared UTC-day admission accounting for Haiku jobs and Opus brief requests.

Haiku's existing job charge is authoritative: it already represents either a
reservation or a settled estimate. Only Opus needs a new request ledger. This
is an application admission budget, not an upper bound on provider invoices.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterator

HAIKU_MODEL = "claude-haiku-5-5"
OPUS_MODEL = "claude-opus-5-5"
_SCHEMA_VERSION = "shared-model-budget-v1"
_MAX_INTEGER = 2**63 - 1
_MAX_HISTORY_JSON_BYTES = 8 * 1024 * 1024
_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS model_budget_brief_requests (
    run_id TEXT NOT NULL,
    round_index INTEGER NOT NULL CHECK(round_index>=-1),
    budget_day TEXT NOT NULL,
    model TEXT NOT NULL CHECK(model='claude-opus-5-5'),
    reservation_microusd INTEGER NOT NULL CHECK(reservation_microusd>=0),
    actual_microusd INTEGER CHECK(actual_microusd IS NULL OR actual_microusd>=0),
    charge_microusd INTEGER NOT NULL CHECK(charge_microusd>=0),
    status TEXT NOT NULL CHECK(status IN ('reserved','unknown','settled','unbilled')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(run_id,round_index)
);
CREATE INDEX IF NOT EXISTS model_budget_brief_day
ON model_budget_brief_requests(budget_day,created_at);
CREATE TABLE IF NOT EXISTS model_budget_bootstrap (
    budget_day TEXT NOT NULL,
    window_start TEXT NOT NULL,
    imported_count INTEGER NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(budget_day,window_start)
);
CREATE INDEX IF NOT EXISTS idx_ai_jobs_model_budget_day
ON ai_jobs(model,submission_started_at,budget_charge_microusd);
"""
_SCHEMA_CHECKSUM = hashlib.sha256(_SCHEMA_SQL.encode()).hexdigest()


class DailyBudgetExceeded(RuntimeError):
    def __init__(self) -> None:
        super().__init__("daily_budget_usd_reached")


def _nonnegative_integer(value: Any, name: str) -> int:
    if type(value) is not int or not 0 <= value <= _MAX_INTEGER:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def usd_to_microusd(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("daily_budget_usd must be a finite nonnegative amount")
    try:
        amount = Decimal(str(value))
        scaled = amount * 1_000_000
        if (not amount.is_finite() or amount < 0 or not scaled.is_finite()
                or scaled != scaled.to_integral_value() or scaled > _MAX_INTEGER):
            raise ValueError("daily_budget_usd must use at most six decimal places")
        return int(scaled)
    except (InvalidOperation, OverflowError) as exc:
        raise ValueError("daily_budget_usd must be a finite nonnegative amount") from exc


def _time(now: datetime | None = None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _day_bounds(
    now: datetime, accounting_start_at: datetime | None = None,
) -> tuple[str, str, str]:
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = max(midnight, _time(accounting_start_at)) if accounting_start_at is not None else midnight
    return midnight.date().isoformat(), _iso(start), _iso(midnight + timedelta(days=1))


def initialize_schema(connection: sqlite3.Connection) -> None:
    """Use the caller's initialization transaction; never open a nested writer."""
    previous = connection.execute(
        "SELECT checksum FROM ai_job_schema WHERE version=?", (_SCHEMA_VERSION,),
    ).fetchone()
    if previous is not None and previous[0] != _SCHEMA_CHECKSUM:
        raise RuntimeError("model_budget_schema_checksum_mismatch")
    for statement in _SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    connection.execute(
        "INSERT OR IGNORE INTO ai_job_schema(version,checksum,applied_at) VALUES(?,?,?)",
        (_SCHEMA_VERSION, _SCHEMA_CHECKSUM, _iso(_time())),
    )


def totals_in_transaction(
    connection: sqlite3.Connection, now: datetime,
    accounting_start_at: datetime | None = None,
) -> dict[str, int]:
    """Indexed sums only; called inside the same transaction as new admission."""
    day, start, end = _day_bounds(_time(now), accounting_start_at)
    # Legacy repository timestamps omit the fractional part when it is zero.
    # Lexically, HH:MM:SSZ sorts *after* HH:MM:SS.500000Z. Exclude that one
    # boundary value while retaining an indexed range/covering sum, rather than
    # applying datetime functions to every historical job.
    start_time = datetime.fromisoformat(start.replace("Z", "+00:00"))
    excluded_whole_second = (
        start_time.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if start_time.microsecond else ""
    )
    haiku = connection.execute(
        """SELECT COALESCE(SUM(budget_charge_microusd),0) FROM ai_jobs
           WHERE model=? AND submission_started_at>=? AND submission_started_at<?
             AND submission_started_at<>?""",
        (HAIKU_MODEL, start, end, excluded_whole_second),
    ).fetchone()[0]
    brief = connection.execute(
        """SELECT COALESCE(SUM(charge_microusd),0),
                  COALESCE(SUM(CASE WHEN status IN ('reserved','unknown')
                                    THEN charge_microusd ELSE 0 END),0)
           FROM model_budget_brief_requests WHERE budget_day=? AND created_at>=? AND created_at<?""", (day, start, end),
    ).fetchone()
    return {
        "haiku_charge_microusd": int(haiku),
        "opus_charge_microusd": int(brief[0]),
        "opus_unsettled_microusd": int(brief[1]),
        "used_microusd": int(haiku) + int(brief[0]),
    }


def can_reserve_in_transaction(
    connection: sqlite3.Connection, *, daily_budget_microusd: int,
    reservation_microusd: int, now: datetime,
    accounting_start_at: datetime | None = None,
) -> bool:
    limit = _nonnegative_integer(daily_budget_microusd, "daily_budget_microusd")
    reservation = _nonnegative_integer(reservation_microusd, "reservation_microusd")
    if accounting_start_at is not None and _time(now) < _time(accounting_start_at):
        return False
    return limit == 0 or totals_in_transaction(connection, now, accounting_start_at)["used_microusd"] + reservation <= limit


class SharedModelBudget:
    def __init__(
        self, path: str | Path, daily_budget_usd: Any,
        brief_store_path: str | Path | None = None,
        accounting_start_at: datetime | None = None,
    ) -> None:
        self.path = Path(path)
        self.accounting_start_at = _time(accounting_start_at) if accounting_start_at is not None else None
        self.daily_budget_microusd = usd_to_microusd(daily_budget_usd)
        self.brief_store_path = Path(brief_store_path) if brief_store_path is not None else None
        self._initialized = False
        self._initialize_lock = threading.Lock()

    def _ensure_initialized(self) -> None:
        with self._initialize_lock:
            if self._initialized:
                return
            # Existing instances only read a tiny schema marker; never run
            # historical AI backfills on each brief/snapshot construction.
            if self.path.exists():
                connection = sqlite3.connect(self.path, timeout=5)
                try:
                    try:
                        previous = connection.execute(
                            "SELECT checksum FROM ai_job_schema WHERE version=?", (_SCHEMA_VERSION,),
                        ).fetchone()
                    except sqlite3.OperationalError as exc:
                        if "no such table" not in str(exc):
                            raise
                        previous = None
                    if previous is not None:
                        if previous[0] != _SCHEMA_CHECKSUM:
                            raise RuntimeError("model_budget_schema_checksum_mismatch")
                        self._initialized = True
                        return
                finally:
                    connection.close()
            from app.services.ai_jobs.repository import AIJobRepository
            AIJobRepository(self.path).ensure_initialized()
            self._initialized = True

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        self._ensure_initialized()
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _key(run_id: str, round_index: int) -> None:
        if (not isinstance(run_id, str) or not 1 <= len(run_id) <= 256
                or any(not (char.isascii() and (char.isalnum() or char in "_-")) for char in run_id)):
            raise ValueError("invalid model budget run_id")
        if type(round_index) is not int or not 0 <= round_index <= 10_000:
            raise ValueError("invalid model budget round_index")

    def reserve_brief_request(
        self, run_id: str, round_index: int, reservation_microusd: int,
        now: datetime | None = None,
    ) -> bool:
        """True only for a new paid request. False must never authorize resending."""
        self._key(run_id, round_index)
        reservation = _nonnegative_integer(reservation_microusd, "reservation_microusd")
        observed = _time(now)
        if self.brief_store_path is not None:
            self.bootstrap_brief_history(observed, exclude_run_id=run_id)
        day = _day_bounds(observed)[0]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """SELECT 1 FROM model_budget_brief_requests
                   WHERE run_id=? AND round_index IN (?, -1)""", (run_id, round_index),
            ).fetchone()
            if existing is not None:
                return False
            if not can_reserve_in_transaction(
                connection, daily_budget_microusd=self.daily_budget_microusd,
                reservation_microusd=reservation, now=observed,
                accounting_start_at=self.accounting_start_at,
            ):
                raise DailyBudgetExceeded()
            connection.execute(
                """INSERT INTO model_budget_brief_requests(
                       run_id,round_index,budget_day,model,reservation_microusd,
                       charge_microusd,status,created_at,updated_at
                   ) VALUES(?,?,?,?,?,?,'reserved',?,?)""",
                (run_id, round_index, day, OPUS_MODEL, reservation, reservation,
                 _iso(observed), _iso(observed)),
            )
            connection.commit()
            return True

    def settle_brief_request(
        self, run_id: str, round_index: int, *, cost_microusd: int | None = None,
        accounting_complete: bool = False, confirmed_unbilled: bool = False,
    ) -> dict[str, Any]:
        self._key(run_id, round_index)
        if type(accounting_complete) is not bool or type(confirmed_unbilled) is not bool:
            raise ValueError("accounting flags must be boolean")
        if cost_microusd is not None:
            _nonnegative_integer(cost_microusd, "cost_microusd")
        if confirmed_unbilled and cost_microusd not in (None, 0):
            raise ValueError("confirmed_unbilled conflicts with nonzero usage")
        if accounting_complete and cost_microusd is None and not confirmed_unbilled:
            raise ValueError("complete accounting requires a cost")
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM model_budget_brief_requests WHERE run_id=? AND round_index=?",
                (run_id, round_index),
            ).fetchone()
            if row is None:
                raise RuntimeError("model_budget_reservation_missing")
            complete = accounting_complete or confirmed_unbilled
            actual = 0 if confirmed_unbilled else cost_microusd
            if row["status"] in {"settled", "unbilled"}:
                if complete and row["actual_microusd"] != actual:
                    raise RuntimeError("model_budget_settlement_conflict")
                return dict(row)
            if complete:
                charge = int(actual)
                status = "unbilled" if confirmed_unbilled else "settled"
            else:
                charge = max(int(row["charge_microusd"]), int(actual or 0))
                actual = max(int(row["actual_microusd"] or 0), int(actual or 0)) or None
                status = "unknown"
            connection.execute(
                """UPDATE model_budget_brief_requests
                   SET actual_microusd=?,charge_microusd=?,status=?,updated_at=?
                   WHERE run_id=? AND round_index=?""",
                (actual, charge, status, _iso(_time()), run_id, round_index),
            )
            result = dict(connection.execute(
                "SELECT * FROM model_budget_brief_requests WHERE run_id=? AND round_index=?",
                (run_id, round_index),
            ).fetchone())
            connection.commit()
            return result

    def snapshot(
        self, now: datetime | None = None, *, reservation_microusd: int = 0,
    ) -> dict[str, Any]:
        reservation = _nonnegative_integer(reservation_microusd, "reservation_microusd")
        observed = _time(now)
        if self.brief_store_path is not None:
            self.bootstrap_brief_history(observed)
        with self._connect() as connection:
            connection.execute("BEGIN")
            totals = totals_in_transaction(connection, observed, self.accounting_start_at)
        limit = self.daily_budget_microusd
        return {
            **totals,
            "budget_day": _day_bounds(observed)[0], "budget_timezone": "UTC",
            "accounting_start_at": _iso(self.accounting_start_at) if self.accounting_start_at else None,
            "budget_window_start": _day_bounds(observed, self.accounting_start_at)[1],
            "budget_reset_at": _day_bounds(observed)[2],
            "budget_basis": "shared_usd",
            "daily_budget_usd": limit / 1_000_000,
            "budget_used_usd": totals["used_microusd"] / 1_000_000,
            "budget_remaining_usd": max(0, limit - totals["used_microusd"]) / 1_000_000 if limit else None,
            "budget_available": (self.accounting_start_at is None or observed >= self.accounting_start_at)
                and (not limit or (totals["used_microusd"] + reservation <= limit
                                  and (reservation > 0 or totals["used_microusd"] < limit))),
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.exists():
            return None
        if path.is_symlink() or path.stat().st_size > _MAX_HISTORY_JSON_BYTES:
            raise RuntimeError("model_budget_history_invalid")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("model_budget_history_invalid")
        return value

    def _brief_history(self, observed: datetime, exclude_run_id: str | None) -> list[dict[str, Any]]:
        """Read only today's slim admissions and indexed reports, outside a write lock."""
        root = self.brief_store_path
        if root is None or not root.exists():
            return []
        if root.is_symlink():
            raise RuntimeError("model_budget_history_invalid")
        day, start, end = _day_bounds(observed, self.accounting_start_at)
        candidates: dict[str, dict[str, Any]] = {}
        admissions_path = root / "admissions.sqlite3"
        if admissions_path.exists():
            if admissions_path.is_symlink():
                raise RuntimeError("model_budget_history_invalid")
            connection = sqlite3.connect(admissions_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                for row in connection.execute(
                    "SELECT * FROM admissions WHERE COALESCE(submitted_at,started_at)>=? AND COALESCE(submitted_at,started_at)<? AND model=?",
                    (start, end, OPUS_MODEL),
                ):
                    candidates[str(row["run_id"])] = dict(row)
            finally:
                connection.close()
        index = self._read_json(root / "index.json") or {}
        if not isinstance(index.get("runs", []), list):
            raise RuntimeError("model_budget_history_invalid")
        for item in index.get("runs", []):
            if not isinstance(item, dict) or str(item.get("started_at") or "")[:10] != day:
                continue
            ident = item.get("run_id")
            if isinstance(ident, str):
                candidates.setdefault(ident, {}).update({"file": item.get("file"), "index_started_at": item.get("started_at")})
        # An index entry is organized by report start time, which may differ
        # from its payment day. Fetch its admission by primary key even when
        # it was outside the day's range above; never substitute the report's
        # start for a known next-day submission.
        index_only = [ident for ident, metadata in candidates.items() if "submitted_at" not in metadata]
        if admissions_path.exists() and index_only:
            connection = sqlite3.connect(admissions_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
            connection.row_factory = sqlite3.Row
            try:
                for ident in index_only:
                    row = connection.execute("SELECT * FROM admissions WHERE run_id=?", (ident,)).fetchone()
                    if row is not None:
                        candidates[ident].update(dict(row))
            finally:
                connection.close()
        # Runs already recorded in the request ledger cannot be imported again.
        with self._connect() as connection:
            known = {row[0] for row in connection.execute(
                "SELECT DISTINCT run_id FROM model_budget_brief_requests WHERE budget_day=?", (day,),
            )}
        result = []
        for ident, metadata in candidates.items():
            if ident == exclude_run_id or ident in known:
                continue
            self._key(ident, 0)
            if metadata.get("model") not in (None, OPUS_MODEL):
                continue
            submitted = metadata.get("submitted_at") or metadata.get("started_at") or metadata.get("index_started_at")
            if not isinstance(submitted, str):
                raise RuntimeError("model_budget_history_invalid")
            submitted_time = _time(datetime.fromisoformat(submitted.replace("Z", "+00:00")))
            if not start <= _iso(submitted_time) < end:
                continue
            file_name = metadata.get("file")
            if not file_name:
                trading_date = str(metadata.get("trading_date") or "")
                slot = str(metadata.get("slot") or "")
                if slot not in {"pre_open", "post_close"}:
                    raise RuntimeError("model_budget_history_invalid")
                datetime.strptime(trading_date, "%Y-%m-%d")
                file_name = f"{trading_date}-{slot}-{ident}.json"
            if not isinstance(file_name, str) or Path(file_name).name != file_name:
                raise RuntimeError("model_budget_history_invalid")
            document = self._read_json(root / "runs" / file_name)
            record = document.get("record") if document else None
            if record is not None and not isinstance(record, dict):
                raise RuntimeError("model_budget_history_invalid")
            if record:
                if record.get("model") != OPUS_MODEL:
                    if metadata.get("model") == OPUS_MODEL:
                        raise RuntimeError("model_budget_history_invalid")
                    continue
                if record.get("run_id") != ident or not isinstance(record.get("started_at"), str):
                    raise RuntimeError("model_budget_history_invalid")
                record_started = _time(datetime.fromisoformat(record["started_at"].replace("Z", "+00:00")))
                recorded_admission_start = metadata.get("started_at") or metadata.get("index_started_at")
                if (record_started > submitted_time
                        or (recorded_admission_start is not None and record_started != _time(
                            datetime.fromisoformat(recorded_admission_start.replace("Z", "+00:00"))
                        ))):
                    raise RuntimeError("model_budget_history_invalid")
                # An Opus run may start before UTC midnight and submit after it.
                # Identity and start ordering must agree; its charge belongs to
                # the submission day, not the report's trading/start day.
                cost = record.get("cost_microusd")
                usage = record.get("usage")
                complete = (
                    record.get("usage_complete") is True
                    and record.get("accounting_complete", True) is True
                    and isinstance(usage, dict)
                    and all(type(usage.get(key)) is int and 0 <= usage[key] <= _MAX_INTEGER
                            for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens",
                                        "cache_read_input_tokens", "web_search_requests"))
                    and type(cost) is int and 0 <= cost <= _MAX_INTEGER
                )
                if complete:
                    result.append({"run_id": ident, "cost": cost, "complete": True, "submitted_at": _iso(submitted_time)})
                    continue
            # A never-submitted admission did not send a paid request. Ignore
            # it rather than creating an import row that would obstruct it later.
            if not record and "submitted_at" in metadata and not metadata["submitted_at"]:
                continue
            partial_cost = record.get("cost_microusd") if record else None
            if type(partial_cost) is not int or not 0 <= partial_cost <= _MAX_INTEGER:
                partial_cost = None
            result.append({"run_id": ident, "cost": partial_cost, "complete": False, "submitted_at": _iso(submitted_time)})
        return result

    def bootstrap_brief_history(
        self, now: datetime | None = None, *, unknown_reservation_microusd: int | None = None,
        exclude_run_id: str | None = None,
    ) -> dict[str, Any]:
        observed = _time(now)
        day, window_start, _ = _day_bounds(observed, self.accounting_start_at)
        if unknown_reservation_microusd is not None:
            _nonnegative_integer(unknown_reservation_microusd, "unknown_reservation_microusd")
        with self._connect() as connection:
            done = connection.execute(
                "SELECT imported_count FROM model_budget_bootstrap WHERE budget_day=? AND window_start=?", (day, window_start),
            ).fetchone()
        if self.brief_store_path is not None:
            # A saved receipt may precede its settlement if the process exited
            # between the two writes. All readers/admission paths must finish
            # that local accounting before deciding whether another run fits.
            from app.services.market_brief.store import BriefStore
            BriefStore(self.brief_store_path).reconcile_request_rounds(self)
        if done is not None:
            return {"budget_day": day, "imported_count": int(done[0]), "already_bootstrapped": True}
        if self.brief_store_path is None:
            return {"budget_day": day, "imported_count": 0, "already_bootstrapped": False}
        records = self._brief_history(observed, exclude_run_id)
        if any(not record["complete"] for record in records) and unknown_reservation_microusd is None:
            raise RuntimeError("model_budget_bootstrap_unknown_reservation_required")
        count = 0
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM model_budget_bootstrap WHERE budget_day=? AND window_start=?", (day, window_start)).fetchone():
                return {"budget_day": day, "imported_count": 0, "already_bootstrapped": True}
            for record in records:
                # Recheck after taking the write lock: another process might
                # have admitted this run while the history files were read.
                if connection.execute(
                    "SELECT 1 FROM model_budget_brief_requests WHERE run_id=? LIMIT 1", (record["run_id"],),
                ).fetchone():
                    continue
                charge = (record["cost"] if record["complete"]
                          else max(int(unknown_reservation_microusd), int(record["cost"] or 0)))
                connection.execute(
                    """INSERT INTO model_budget_brief_requests(
                           run_id,round_index,budget_day,model,reservation_microusd,
                           actual_microusd,charge_microusd,status,created_at,updated_at
                       ) VALUES(?,-1,?,?,?,?,?,?,?,?)""",
                    (record["run_id"], day, OPUS_MODEL, charge,
                     record["cost"], charge, "settled" if record["complete"] else "unknown",
                     record["submitted_at"], _iso(observed)),
                )
                count += 1
            connection.execute(
                "INSERT INTO model_budget_bootstrap VALUES(?,?,?,?)", (day, window_start, count, _iso(observed)),
            )
            connection.commit()
        return {"budget_day": day, "imported_count": count, "already_bootstrapped": False}
