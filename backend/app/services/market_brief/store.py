"""研判结果的落盘与读取（DATA_DIR/market-brief/）。

文件布局（原子写：mkstemp + fchmod 0600 + fsync + os.replace，与 public_home_snapshot 同一纪律）：
- runs/<trading_date>-<slot>-<run_id>.json   完整运行记录（含证据包、原始输出、用量）
- latest.json                                 最近一次「成功」运行的投影 + 比它更新的失败尝试
- index.json                                  最近 60 次运行的摘要列表（历史与状态接口用）

读取经 FingerprintedFileCache，loader 只依赖文件内容。写入方（worker 与命令行可能同时在跑）
用目录内的 flock 串行化，避免两边同时改 index.json 时丢条目。
"""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.data_paths import get_data_paths
from app.failure_diagnostics import record_fallback_failure
from app.services.snapshot_read_cache import FingerprintedFileCache

from . import errors
from .scheduler import BriefSchedule, next_slot_at
from .schema import SCHEMA_VERSION, BriefSlot, BriefTrigger

RunStatus = str  # "completed" | "failed"

STORE_VERSION = 1
RETAINED_RUNS = 60
RUN_STATUSES = frozenset({"completed", "failed"})
MODEL_LABELS: Mapping[str, str] = {"claude-opus-5-5": "Claude Opus 5.5"}
# 公开响应里的 coverage 只给这几项（与 tests/fixtures/market_brief_sample.json 一致）；
# owner 另外拿到裁剪、代码数、异常类型等排查字段。
PUBLIC_COVERAGE_KEYS = (
    "universe_size",
    "scored_count",
    "quotes_valid",
    "breadth_basis",
    "data_through",
    "missing_blocks",
    "evidence_bytes",
)
PUBLIC_EXTERNAL_SOURCES = 20
_SLOTS = frozenset({"pre_open", "post_close"})
_TRIGGERS = frozenset({"scheduled", "manual"})
_RUN_ID = re.compile(r"^mb_\d{8}_(?:pre_open|post_close)_[0-9a-f]{8}$")
_RUN_FILE = re.compile(r"^\d{4}-\d{2}-\d{2}-(?:pre_open|post_close)-mb_\d{8}_(?:pre_open|post_close)_[0-9a-f]{8}\.json$")
_MAX_FILE_BYTES = 4 * 1024 * 1024
_documents = FingerprintedFileCache("market_brief", max_paths=8, max_bytes=16 * 1024 * 1024)


@dataclass(frozen=True)
class BriefRunRecord:
    run_id: str
    slot: BriefSlot
    trading_date: date
    trigger: BriefTrigger
    status: RunStatus
    started_at: datetime
    completed_at: datetime | None
    model: str
    effort: str
    error_code: str | None = None
    error_detail: str | None = None
    evidence: Mapping[str, Any] = field(default_factory=dict)
    evidence_bytes: int = 0
    coverage: Mapping[str, Any] = field(default_factory=dict)
    request_meta: Mapping[str, Any] = field(default_factory=dict)
    raw_output_text: str | None = None
    result: Mapping[str, Any] | None = None
    validation_warnings: tuple[str, ...] = ()
    external_sources: tuple[Mapping[str, Any], ...] = ()
    usage: Mapping[str, Any] = field(default_factory=dict)
    cost_microusd: int | None = None
    duration_seconds: float | None = None
    continuation_count: int = 0
    usage_complete: bool = True
    request_rounds: tuple[Mapping[str, Any], ...] = ()


def new_run_id(trading_date: date, slot: BriefSlot) -> str:
    if slot not in _SLOTS:
        raise ValueError(f"unknown market brief slot: {slot!r}")
    return f"mb_{trading_date:%Y%m%d}_{slot}_{secrets.token_hex(4)}"


def model_label(model_id: str) -> str:
    return MODEL_LABELS.get(model_id, model_id)


def _iso_z(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("market brief timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_precise(value: datetime | None) -> str | None:
    """运行记录文件里保留微秒，读回来与写入时完全一致；摘要与公开响应用秒精度。"""

    if value is None:
        return None
    if value.tzinfo is None:
        raise ValueError("market brief timestamps must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("stored timestamp lacks a timezone")
    return parsed.astimezone(timezone.utc)


def _record_to_document(record: BriefRunRecord) -> dict[str, Any]:
    if not _RUN_ID.fullmatch(record.run_id):
        raise ValueError("run_id must look like mb_<YYYYMMDD>_<slot>_<8hex>")
    if record.slot not in _SLOTS or record.trigger not in _TRIGGERS or record.status not in RUN_STATUSES:
        raise ValueError("market brief record has an invalid slot, trigger or status")
    if record.status == "completed" and not isinstance(record.result, Mapping):
        raise ValueError("a completed market brief record needs a result")
    return {
        "run_id": record.run_id,
        "slot": record.slot,
        "trading_date": record.trading_date.isoformat(),
        "trigger": record.trigger,
        "status": record.status,
        "started_at": _iso_precise(record.started_at),
        "completed_at": _iso_precise(record.completed_at),
        "model": record.model,
        "effort": record.effort,
        "error_code": record.error_code,
        "error_detail": record.error_detail,
        "evidence": dict(record.evidence),
        "evidence_bytes": int(record.evidence_bytes),
        "coverage": dict(record.coverage),
        "request_meta": dict(record.request_meta),
        "raw_output_text": record.raw_output_text,
        "result": dict(record.result) if record.result is not None else None,
        "validation_warnings": list(record.validation_warnings),
        "external_sources": [dict(item) for item in record.external_sources],
        "usage": dict(record.usage),
        "cost_microusd": record.cost_microusd,
        "duration_seconds": record.duration_seconds,
        "continuation_count": int(record.continuation_count),
        "usage_complete": record.usage_complete,
        "request_rounds": list(record.request_rounds),
    }


def _record_from_document(document: Mapping[str, Any]) -> BriefRunRecord:
    # 缓存里的文档是共享的；交出去的记录用深拷贝，调用方改动不会污染缓存。
    data = copy.deepcopy(dict(document))
    started_at = _parse_time(data["started_at"])
    if started_at is None:
        raise ValueError("stored record lacks started_at")
    return BriefRunRecord(
        run_id=data["run_id"],
        slot=data["slot"],
        trading_date=date.fromisoformat(data["trading_date"]),
        trigger=data["trigger"],
        status=data["status"],
        started_at=started_at,
        completed_at=_parse_time(data.get("completed_at")),
        model=data["model"],
        effort=data["effort"],
        error_code=data.get("error_code"),
        error_detail=data.get("error_detail"),
        evidence=data.get("evidence") or {},
        evidence_bytes=int(data.get("evidence_bytes") or 0),
        coverage=data.get("coverage") or {},
        request_meta=data.get("request_meta") or {},
        raw_output_text=data.get("raw_output_text"),
        result=data.get("result"),
        validation_warnings=tuple(data.get("validation_warnings") or ()),
        external_sources=tuple(data.get("external_sources") or ()),
        usage=data.get("usage") or {},
        cost_microusd=data.get("cost_microusd"),
        duration_seconds=data.get("duration_seconds"),
        continuation_count=int(data.get("continuation_count") or 0),
        usage_complete=(data.get("usage_complete") is not False
                        and all(type((data.get("usage") or {}).get(key)) is int and (data.get("usage") or {})[key] >= 0
                                for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "web_search_requests"))
                        and type(data.get("cost_microusd")) is int and data["cost_microusd"] >= 0),
        request_rounds=tuple(data.get("request_rounds") or ()),
    )


def _summary(record: BriefRunRecord, file_name: str) -> dict[str, Any]:
    finished = record.completed_at or record.started_at
    return {
        "run_id": record.run_id,
        "trading_date": record.trading_date.isoformat(),
        "slot": record.slot,
        "trigger": record.trigger,
        "status": record.status,
        "started_at": _iso_z(record.started_at),
        "generated_at": _iso_z(finished),
        "error_code": record.error_code,
        "file": file_name,
    }


def _projection(record: BriefRunRecord) -> dict[str, Any]:
    """latest.json 里保存的完整投影（含 owner 字段）；公开响应再从中剔除。"""

    usage = dict(record.usage)
    return {
        "run_id": record.run_id,
        "trading_date": record.trading_date.isoformat(),
        "slot": record.slot,
        "trigger": record.trigger,
        "generated_at": _iso_z(record.completed_at or record.started_at),
        "model": {"id": record.model, "label": model_label(record.model), "effort": record.effort},
        "coverage": dict(record.coverage),
        "result": dict(record.result or {}),
        "external_sources": [dict(item) for item in record.external_sources],
        "validation_warnings": list(record.validation_warnings),
        "web_search_count": int(usage.get("web_search_requests") or 0),
        "usage": usage,
        "cost_usd": round(record.cost_microusd / 1_000_000, 4) if record.cost_microusd is not None else None,
        "duration_seconds": record.duration_seconds,
        "usage_complete": record.usage_complete,
    }


def _attempt(record: BriefRunRecord) -> dict[str, Any]:
    return {
        "run_id": record.run_id,
        "error_code": record.error_code,
        "at": _iso_z(record.completed_at or record.started_at),
        "slot": record.slot,
        "trading_date": record.trading_date.isoformat(),
    }


def _public_warning(text: str) -> str:
    """只保留「removed sectors[2].note」这样的位置，去掉冒号后的原因与被拒片段。"""

    return str(text).split(":", 1)[0].strip()


def _public_sources(sources: Any) -> list[dict[str, Any]]:
    entries = [item for item in sources or [] if isinstance(item, Mapping) and item.get("url")]
    # 抓取过全文的页面排在搜索结果前面：它们更可能是正文实际依据的来源。
    entries.sort(key=lambda item: item.get("via") != "web_fetch")
    return [{"url": item["url"], "title": item.get("title")} for item in entries[:PUBLIC_EXTERNAL_SOURCES]]


def _load_document(raw: bytes) -> dict[str, Any] | None:
    document = json.loads(raw.decode("utf-8"))
    if not isinstance(document, dict) or document.get("version") != STORE_VERSION:
        return None
    return document


class AdmissionRejected(RuntimeError):
    """A new run was not admitted; no provider call or run charge occurred."""


class AdmissionReplay(Exception):
    def __init__(self, record: BriefRunRecord) -> None:
        self.record = record


class BriefStore:
    def __init__(self, root: Path | None = None) -> None:
        """root 缺省为 get_data_paths().root / "market-brief"。"""

        self.root = Path(root) if root is not None else get_data_paths().root / "market-brief"
        if not self.root.is_absolute():
            raise ValueError("market brief store root must be an absolute path")
        self.runs_dir = self.root / "runs"
        self.index_path = self.root / "index.json"
        self.latest_path = self.root / "latest.json"
        self._lock_path = self.root / ".write.lock"
        self._run_lock_path = self.root / ".run.lock"
        self._admissions_path = self.root / "admissions.sqlite3"


    @contextmanager
    def _admissions(self, *, create: bool = True) -> Iterator[sqlite3.Connection]:
        if create:
            self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self._admissions_path.is_symlink():
            raise ValueError("market brief admission paths must not be symbolic links")
        target = str(self._admissions_path) if create else self._admissions_path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(target, timeout=5, uri=not create)
        connection.row_factory = sqlite3.Row
        try:
            if create:
                os.chmod(self._admissions_path, 0o600)
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute("""CREATE TABLE IF NOT EXISTS admissions (
                    run_id TEXT PRIMARY KEY, trading_date TEXT NOT NULL, slot TEXT NOT NULL,
                    trigger TEXT NOT NULL, started_at TEXT NOT NULL, model TEXT NOT NULL,
                    effort TEXT NOT NULL, status TEXT NOT NULL, submitted_at TEXT,
                    error_code TEXT, unknown_until TEXT
                )""")
                columns = {row[1] for row in connection.execute("PRAGMA table_info(admissions)")}
                if "request_key" not in columns:
                    connection.execute("ALTER TABLE admissions ADD COLUMN request_key TEXT")
                connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS admissions_request_key ON admissions(request_key) WHERE request_key IS NOT NULL")
                connection.commit()
            yield connection
        finally:
            connection.close()

    def mark_submitted(self, run_id: str) -> None:
        with self._admissions() as connection:
            updated = connection.execute(
                "UPDATE admissions SET submitted_at=COALESCE(submitted_at,?) WHERE run_id=? AND status='running'",
                (_iso_precise(datetime.now(timezone.utc)), run_id),
            ).rowcount
            if updated != 1:
                raise RuntimeError("market brief admission lost before submission")
            connection.commit()

    def _settle_admission(self, record: BriefRunRecord) -> None:
        if not self._admissions_path.exists():
            return
        unknown = not record.usage_complete
        until = _iso_precise(record.started_at + timedelta(hours=24)) if unknown else None
        with self._admissions() as connection:
            connection.execute(
                "UPDATE admissions SET status=?,error_code=?,unknown_until=? WHERE run_id=?",
                (record.status, record.error_code, until, record.run_id),
            )
            connection.commit()

    def _recover_abandoned(self) -> None:
        # The process-wide run flock is held: a remaining running row can only
        # belong to a crashed process. An already-written receipt wins recovery.
        with self._admissions() as connection:
            abandoned = connection.execute("SELECT * FROM admissions WHERE status='running'").fetchall()
        for row in abandoned:
            path = self.runs_dir / f"{row['trading_date']}-{row['slot']}-{row['run_id']}.json"
            document = self._read(path)
            if document and isinstance(document.get("record"), dict):
                record = _record_from_document(document["record"])
            else:
                submitted = bool(row["submitted_at"])
                record = BriefRunRecord(
                    run_id=row["run_id"], trading_date=date.fromisoformat(row["trading_date"]),
                    slot=row["slot"], trigger=row["trigger"], model=row["model"], effort=row["effort"],
                    started_at=_parse_time(row["started_at"]), completed_at=datetime.now(timezone.utc),
                    status="failed", error_code=errors.SUBMISSION_OUTCOME_UNKNOWN if submitted else errors.RUNTIME_ERROR,
                    error_detail="process stopped before durable completion", usage_complete=not submitted,
                    cost_microusd=None if submitted else 0,
                )
            # Also repairs a crash between receipt, index and projection writes.
            self.write_run(record)

    @contextmanager
    def admission(self, record: BriefRunRecord, *, daily_max_runs: int, request_key: str | None = None) -> Iterator[None]:
        """One paid run across CLI/workers; durable starts enforce the UTC-day cap.

        Ambiguous outcomes quarantine only the same trading-date/slot for 24h.
        Other independent slots can proceed; scheduled attempts are never replayed.
        """
        if type(daily_max_runs) is not int or daily_max_runs < 1:
            raise ValueError("daily_max_runs must be positive")
        if request_key is not None and (not isinstance(request_key, str) or not 1 <= len(request_key) <= 256):
            raise ValueError("invalid market brief request key")
        self.root.mkdir(parents=True, exist_ok=True)
        if self.root.is_symlink() or self._run_lock_path.is_symlink():
            raise ValueError("market brief run lock must not be a symbolic link")
        descriptor = os.open(self._run_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise AdmissionRejected(errors.MARKET_BRIEF_IN_PROGRESS) from exc
            self._recover_abandoned()
            with self._admissions() as connection:
                connection.execute("BEGIN IMMEDIATE")
                rows = connection.execute("SELECT * FROM admissions").fetchall()
                if request_key is not None:
                    previous = next((row for row in rows if row["request_key"] == request_key), None)
                    if previous is not None:
                        path = self.runs_dir / f"{previous['trading_date']}-{previous['slot']}-{previous['run_id']}.json"
                        document = self._read(path)
                        if document and isinstance(document.get("record"), dict):
                            raise AdmissionReplay(_record_from_document(document["record"]))
                        # Pruned or unreadable receipts never permit a second payment.
                        raise AdmissionRejected(errors.MARKET_BRIEF_IN_PROGRESS)
                day = record.started_at.astimezone(timezone.utc).date().isoformat()
                known = {row["run_id"] for row in rows}
                count = sum(row["started_at"][:10] == day for row in rows)
                count += sum(str(item.get("started_at") or "")[:10] == day and item.get("run_id") not in known
                             for item in self._index_runs())
                if count >= daily_max_runs:
                    raise AdmissionRejected(errors.DAILY_RUN_LIMIT_REACHED)
                for row in rows:
                    if row["trading_date"] != record.trading_date.isoformat() or row["slot"] != record.slot:
                        continue
                    until = _parse_time(row["unknown_until"])
                    if (until is not None and until > record.started_at) or record.trigger == "scheduled":
                        raise AdmissionRejected(errors.MARKET_BRIEF_IN_PROGRESS)
                if record.trigger == "scheduled" and self.completed(record.trading_date, record.slot):
                    raise AdmissionRejected(errors.MARKET_BRIEF_IN_PROGRESS)
                connection.execute(
                    "INSERT INTO admissions(run_id,trading_date,slot,trigger,started_at,model,effort,status,request_key) VALUES(?,?,?,?,?,?,?,'running',?)",
                    (record.run_id, record.trading_date.isoformat(), record.slot, record.trigger,
                     _iso_precise(record.started_at), record.model, record.effort, request_key),
                )
                connection.commit()
            yield
        finally:
            os.close(descriptor)

    def recover_interrupted(self) -> None:
        """Worker startup/slot checks reconcile crashes without starting paid work."""
        if not self._admissions_path.exists():
            return
        if self.root.is_symlink() or self._run_lock_path.is_symlink():
            raise ValueError("market brief run lock must not be a symbolic link")
        descriptor = os.open(self._run_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            self._recover_abandoned()
        finally:
            os.close(descriptor)

    def has_request(self, request_key: str) -> bool:
        if not self._admissions_path.exists():
            return False
        with self._admissions(create=False) as connection:
            return connection.execute("SELECT 1 FROM admissions WHERE request_key=?", (request_key,)).fetchone() is not None

    def attempted(self, trading_date: date, slot: BriefSlot) -> bool:
        if not self._admissions_path.exists():
            return False
        with self._admissions(create=False) as connection:
            return connection.execute(
                "SELECT 1 FROM admissions WHERE trading_date=? AND slot=? LIMIT 1",
                (trading_date.isoformat(), slot),
            ).fetchone() is not None

    # ---- 写入 ----

    @contextmanager
    def _write_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self._lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)

    def _write_json(self, path: Path, document: Mapping[str, Any]) -> None:
        if path.is_symlink() or path.parent.is_symlink():
            raise ValueError("market brief store paths must not be symbolic links")
        encoded = json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        if len(encoded) > _MAX_FILE_BYTES:
            raise ValueError(f"market brief document exceeds {_MAX_FILE_BYTES} bytes")
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        descriptor_open = True
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor_open = False
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, path)
        except BaseException:
            if descriptor_open:
                os.close(descriptor)
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        # 替换已经生效；目录 fsync 只影响掉电后的持久性，失败不能把成功写入报成失败。
        try:
            directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError as exc:
            record_fallback_failure("market_brief_store_dir_fsync", exc)

    def reconcile_request_rounds(self, budget: Any) -> int:
        """Finish saved accounting receipts without issuing another request."""
        path = Path(budget.path)
        if not path.exists():
            return 0
        with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=5.0) as connection:
            rows = connection.execute(
                "SELECT run_id,round_index FROM model_budget_brief_requests WHERE round_index>0 AND status IN ('reserved','unknown')"
            ).fetchall()
        settled = 0
        for run_id, index in rows:
            if not _RUN_ID.fullmatch(run_id):
                continue
            receipt = self._read(self.root / "request-rounds" / run_id / f"{index}.json")
            if receipt is None or receipt.get("round_index") != index:
                continue
            complete = receipt.get("accounting_complete") is True
            unbilled = receipt.get("confirmed_unbilled") is True
            cost = receipt.get("cost_microusd")
            if not (complete or unbilled) or type(cost) is not int or cost < 0:
                continue
            if unbilled and cost != 0:
                raise ValueError("market brief unbilled receipt has nonzero cost")
            if not unbilled and not all(
                type((receipt.get("usage") or {}).get(key)) is int and (receipt.get("usage") or {})[key] >= 0
                for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "web_search_requests")
            ):
                continue
            budget.settle_brief_request(run_id, index, cost_microusd=cost,
                                        accounting_complete=complete, confirmed_unbilled=unbilled)
            settled += 1
        return settled

    def write_request_round(self, run_id: str, metadata: Mapping[str, Any]) -> Path:
        """Save per-request billing evidence independently of final publication."""
        index = metadata.get("round_index")
        if not _RUN_ID.fullmatch(run_id) or type(index) is not int or index < 1:
            raise ValueError("market brief request round identity invalid")
        directory = self.root / "request-rounds" / run_id
        if self.root.is_symlink() or directory.parent.is_symlink() or directory.is_symlink():
            raise ValueError("market brief request round must not use symbolic links")
        path = directory / f"{index}.json"
        with self._write_lock():
            previous = self._read(path)
            value = {"version": STORE_VERSION, **dict(metadata)}
            if previous is None and path.exists():
                raise RuntimeError("market brief request round receipt unreadable")
            if previous is not None and previous != value:
                raise RuntimeError("market brief request round receipt conflict")
            if previous is None:
                self._write_json(path, value)
        return path

    def record_path(self, record: BriefRunRecord) -> Path:
        """运行记录文件的位置（文件名带交易日与槽位，按名字就能找到）。"""

        return self.runs_dir / f"{record.trading_date.isoformat()}-{record.slot}-{record.run_id}.json"

    def write_run(self, record: BriefRunRecord) -> Path:
        """落盘一条运行记录并刷新 index.json / latest.json；返回记录文件路径。"""

        document = _record_to_document(record)
        path = self.record_path(record)
        file_name = path.name
        with self._write_lock():
            self._write_json(path, {"version": STORE_VERSION, "record": document})
            runs = [item for item in self._index_runs() if item.get("run_id") != record.run_id]
            runs.append(_summary(record, file_name))
            # 新的在前；同一时刻按 run_id 稳定排序。
            runs.sort(key=lambda item: (item.get("started_at") or "", item.get("run_id") or ""), reverse=True)
            retained = runs[:RETAINED_RUNS]
            saved_at = _iso_z(datetime.now(timezone.utc))
            self._write_json(self.index_path, {"version": STORE_VERSION, "saved_at": saved_at, "runs": retained})
            current = self._latest_document() or {}
            success = current.get("latest_success")
            failure = current.get("latest_failure")
            finished = _iso_z(record.completed_at or record.started_at) or ""
            # 按完成时刻取较新者：worker 与命令行交错落盘时，旧结果不能覆盖新结果。
            if record.status == "completed":
                if not isinstance(success, dict) or (success.get("generated_at") or "") <= finished:
                    success = _projection(record)
                    if isinstance(failure, dict) and (failure.get("at") or "") <= finished:
                        failure = None
            elif not isinstance(failure, dict) or (failure.get("at") or "") <= finished:
                failure = _attempt(record)
            self._write_json(
                self.latest_path,
                {"version": STORE_VERSION, "saved_at": saved_at, "latest_success": success, "latest_failure": failure},
            )
            self._prune_runs({item["file"] for item in retained if isinstance(item.get("file"), str)})
        self._settle_admission(record)
        return path

    def _prune_runs(self, keep: set[str]) -> None:
        if not self.runs_dir.is_dir():
            return
        for entry in self.runs_dir.iterdir():
            # 只删本模块命名规则下、已不在索引里的记录文件；目录里的其他东西一概不动。
            if _RUN_FILE.fullmatch(entry.name) and entry.name not in keep and entry.is_file() and not entry.is_symlink():
                entry.unlink()

    # ---- 读取 ----

    def _read(self, path: Path) -> dict[str, Any] | None:
        if path.is_symlink():
            return None
        return _documents.read(path, _load_document, max_bytes=_MAX_FILE_BYTES)

    def _index_runs(self) -> list[dict[str, Any]]:
        document = self._read(self.index_path)
        runs = document.get("runs") if document else None
        return [dict(item) for item in runs if isinstance(item, dict)] if isinstance(runs, list) else []

    def _latest_document(self) -> dict[str, Any] | None:
        document = self._read(self.latest_path)
        if document is None:
            return None
        return copy.deepcopy(document)

    def _load_record(self, summary: Mapping[str, Any]) -> BriefRunRecord | None:
        file_name = summary.get("file")
        if not isinstance(file_name, str) or not _RUN_FILE.fullmatch(file_name):
            return None
        document = self._read(self.runs_dir / file_name)
        record = document.get("record") if document else None
        return _record_from_document(record) if isinstance(record, dict) else None

    def completed(self, trading_date: date, slot: BriefSlot) -> bool:
        """该交易日该槽是否已有成功记录。"""

        day = trading_date.isoformat()
        return any(
            item.get("status") == "completed" and item.get("trading_date") == day and item.get("slot") == slot
            for item in self._index_runs()
        )

    def runs_on(self, trading_date: date) -> int:
        """该日历日（UTC）已启动的运行次数（手动触发限额用）。

        参数名沿用桩签名；按 started_at 的 UTC 日期计数，不是按记录所属的交易日。
        """

        day = trading_date.isoformat()
        rows = []
        if self._admissions_path.exists():
            with self._admissions(create=False) as connection:
                rows = connection.execute("SELECT run_id,started_at FROM admissions").fetchall()
        known = {row["run_id"] for row in rows}
        return sum(row["started_at"][:10] == day for row in rows) + sum(
            1 for item in self._index_runs()
            if str(item.get("started_at") or "")[:10] == day and item.get("run_id") not in known
        )

    def latest_record(self, *, status: RunStatus | None = "completed") -> BriefRunRecord | None:
        """最近一条运行记录（含证据包与原始输出）；status=None 时不限状态。"""

        for item in self._index_runs():
            if status is None or item.get("status") == status:
                return self._load_record(item)
        return None

    def latest_public(
        self,
        *,
        now: datetime | None = None,
        owner: bool = False,
        schedule: BriefSchedule | None = None,
    ) -> Mapping[str, Any]:
        """GET /api/market-brief/latest 的响应体（见 tests/fixtures/market_brief_sample.json）。

        owner=True 时附加 usage / cost_usd / duration_seconds 与完整 coverage。没有任何成功
        记录时 status="missing"、brief=None，但仍给 latest_attempt 与 next_slot。
        结果只随落盘内容与槽位边界变化，便于 ETag。
        """

        observed = now or datetime.now(timezone.utc)
        document = self._latest_document() or {}
        success = document.get("latest_success")
        failure = document.get("latest_failure")
        brief = None
        if isinstance(success, dict):
            brief = {
                key: success.get(key)
                for key in ("run_id", "trading_date", "slot", "trigger", "generated_at", "model")
            }
            coverage = success.get("coverage") or {}
            brief["coverage"] = dict(coverage) if owner else {key: coverage.get(key) for key in PUBLIC_COVERAGE_KEYS}
            brief["result"] = success.get("result")
            brief["external_sources"] = _public_sources(success.get("external_sources"))
            warnings = list(success.get("validation_warnings") or [])
            # 警告正文里带着被拒文本的片段（可能是英文原句），访客只看到被剔除的位置。
            brief["validation_warnings"] = warnings if owner else [_public_warning(item) for item in warnings]
            brief["web_search_count"] = success.get("web_search_count") or 0
            if owner:
                brief["usage"] = success.get("usage")
                brief["cost_usd"] = success.get("cost_usd")
                brief["duration_seconds"] = success.get("duration_seconds")
                brief["usage_complete"] = success.get("usage_complete", True)
        latest_attempt = None
        if isinstance(failure, dict) and (
            brief is None or (failure.get("at") or "") > (brief.get("generated_at") or "")
        ):
            latest_attempt = {key: failure.get(key) for key in ("error_code", "at", "slot", "trading_date")}
        upcoming = next_slot_at(observed, schedule or BriefSchedule())
        return {
            "status": "ok" if brief is not None else "missing",
            "schema_version": SCHEMA_VERSION,
            "brief": brief,
            "latest_attempt": latest_attempt,
            "next_slot": {"slot": upcoming[2], "at": _iso_z(upcoming[0])} if upcoming else None,
            "snapshot_saved_at": document.get("saved_at"),
        }

    def history(self, *, limit: int = 10) -> list[Mapping[str, Any]]:
        """最近 limit 次运行的摘要（run_id, trading_date, slot, trigger, status, generated_at, error_code）。"""

        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        keys = ("run_id", "trading_date", "slot", "trigger", "status", "generated_at", "error_code")
        return [{key: item.get(key) for key in keys} for item in self._index_runs()[:limit]]


__all__ = [
    "AdmissionRejected",
    "AdmissionReplay",
    "BriefRunRecord",
    "BriefStore",
    "MODEL_LABELS",
    "PUBLIC_COVERAGE_KEYS",
    "RETAINED_RUNS",
    "RunStatus",
    "model_label",
    "new_run_id",
]
