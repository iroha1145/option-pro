"""Bounded customer demand for optional strength ranking variants.

Signed-in non-owners cannot POST owner worker actions. When an A0 snapshot is
missing they register a deduped demand; the existing strength_refresh task
picks it up. GET never scans the pool itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.data_paths import get_data_paths
from app.services.algorithm_modes import A0_ALGORITHM, a0_view_supported


VARIANT_DEMAND_TTL_SECONDS = 30 * 60
VARIANT_DEMAND_MAX = 4
VARIANT_DEMAND_WRITE_INTERVAL_SECONDS = 30.0
VARIANT_ACTION_TYPE = "strength_variant_refresh"
VARIANT_TASK_NAME = "strength_refresh"
VARIANT_COOLDOWN_SECONDS = 30.0
PREPARING_STATUSES = frozenset({"queued", "running", "accepted", "preparing"})
FAILED_STATUSES = frozenset({"failed", "unavailable"})


def _root(path: Path | None = None) -> Path:
    return path or (get_data_paths().root / "strength-variant-demand")


def _finite_time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if number > 0 and number == number and number not in {float("inf"), float("-inf")} else None


@contextmanager
def _demand_lock(directory: Path) -> Iterator[None]:
    directory.mkdir(parents=True, exist_ok=True)
    fd = os.open(
        directory / ".lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("invalid strength variant demand lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if len(raw) > 16 * 1024:
        return None
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def customer_variant_parameters_allowed(parameters: Mapping[str, Any]) -> bool:
    from app.api.strength import normalize_strength_scan_parameters

    try:
        normalized = normalize_strength_scan_parameters(dict(parameters))
    except (TypeError, ValueError):
        return False
    return (
        normalized.get("ranking_algorithm") == A0_ALGORITHM
        and a0_view_supported(normalized.get("timeframe"), normalized.get("profile"))
    )


def _minute_key(digest: str, now: float) -> str:
    current = datetime.fromtimestamp(now, timezone.utc)
    return f"strength-variant:{digest}:{current.strftime('%Y%m%dT%H%MZ')}"


def _public_demand(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "status",
            "parameters_hash",
            "requested_at",
            "expires_at",
            "error_code",
            "request_id",
            "reason",
            "reused",
        )
        if key in item
    }


def _prune_locked(directory: Path, now: float) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for path in directory.glob("*.json"):
        if path.name.startswith("."):
            continue
        payload = _read_json(path)
        requested_at = _finite_time((payload or {}).get("requested_at"))
        expires_at = _finite_time((payload or {}).get("expires_at"))
        digest = str((payload or {}).get("parameters_hash") or path.stem)
        if (
            payload is None
            or requested_at is None
            or expires_at is None
            or expires_at <= now
            or digest != path.stem
        ):
            try:
                path.unlink()
            except OSError:
                pass
            continue
        active[digest] = payload
    overflow = len(active) - VARIANT_DEMAND_MAX
    if overflow > 0:
        oldest = sorted(active, key=lambda key: (float(active[key]["requested_at"]), key))
        for digest in oldest[:overflow]:
            try:
                (directory / f"{digest}.json").unlink()
            except OSError:
                pass
            active.pop(digest, None)
    return active


def list_pending_strength_variant_demands(
    *,
    root: Path | None = None,
    now: float | None = None,
) -> list[dict[str, Any]]:
    """Newest first, hard-capped, only still-allowed A0 identities."""

    directory = _root(root)
    observed = time.time() if now is None else float(now)
    pending: list[tuple[float, dict[str, Any]]] = []
    try:
        with _demand_lock(directory):
            active = _prune_locked(directory, observed)
    except OSError:
        return []
    for item in active.values():
        if str(item.get("status") or "") in FAILED_STATUSES:
            continue
        parameters = item.get("parameters")
        if not isinstance(parameters, dict) or not customer_variant_parameters_allowed(parameters):
            continue
        pending.append((float(item["requested_at"]), dict(parameters)))
    pending.sort(key=lambda row: row[0], reverse=True)
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    from app.api.strength import strength_scan_parameters_hash

    for _stamp, parameters in pending:
        digest = strength_scan_parameters_hash(parameters)
        if digest in seen:
            continue
        seen.add(digest)
        unique.append(parameters)
        if len(unique) >= VARIANT_DEMAND_MAX:
            break
    return unique


def complete_strength_variant_demand(
    parameters: Mapping[str, Any],
    *,
    status: str,
    error_code: str | None = None,
    root: Path | None = None,
    now: float | None = None,
) -> None:
    from app.api.strength import normalize_strength_scan_parameters, strength_scan_parameters_hash

    try:
        normalized = normalize_strength_scan_parameters(dict(parameters))
        digest = strength_scan_parameters_hash(normalized)
    except (TypeError, ValueError):
        return
    directory = _root(root)
    observed = time.time() if now is None else float(now)
    path = directory / f"{digest}.json"
    try:
        with _demand_lock(directory):
            current = _read_json(path) or {}
            current.update(
                {
                    "parameters_hash": digest,
                    "parameters": normalized,
                    "status": status,
                    "error_code": error_code,
                    "completed_at": observed,
                }
            )
            if status in {"completed", "idle"}:
                try:
                    path.unlink()
                except OSError:
                    pass
                return
            _write_json(path, current)
    except OSError:
        return


def enqueue_customer_variant_refresh(
    parameters: Mapping[str, Any],
    *,
    now: float | None = None,
) -> dict[str, Any]:
    """Queue the existing strength_refresh task for one allowed A0 identity."""

    from app.api.strength import normalize_strength_scan_parameters, strength_scan_parameters_hash
    from app.worker.state import WorkerStateRepository

    normalized = normalize_strength_scan_parameters(dict(parameters))
    if not customer_variant_parameters_allowed(normalized):
        raise ValueError("strength variant demand parameters are not allowed")
    digest = strength_scan_parameters_hash(normalized)
    observed = time.time() if now is None else float(now)
    repository = WorkerStateRepository(get_data_paths().worker_db)
    try:
        worker = repository.health()
    except Exception:
        worker = {"healthy": False, "status": "unavailable", "tasks": []}
    tasks = list(worker.get("tasks") or [])
    task = next((item for item in tasks if str(item.get("task_name") or "") == VARIANT_TASK_NAME), None)
    if task is not None and not bool(task.get("enabled")):
        return {
            "status": "failed",
            "error_code": "worker_task_disabled",
            "parameters_hash": digest,
            "reason": "worker_task_disabled",
        }
    try:
        item = repository.request_action(
            VARIANT_ACTION_TYPE,
            VARIANT_TASK_NAME,
            _minute_key(digest, observed),
            cooldown_seconds=VARIANT_COOLDOWN_SECONDS,
            details={
                "parameters": normalized,
                "parameters_hash": digest,
                "source": "customer_variant",
            },
        )
    except Exception as exc:
        return {
            "status": "failed",
            "error_code": "worker_state_unavailable",
            "parameters_hash": digest,
            "reason": type(exc).__name__,
        }
    status = str(item.get("status") or "queued")
    if item.get("reason") in {"already_running", "idempotent", "cooldown"}:
        status = str(item.get("status") or "queued")
    return {
        "status": status,
        "parameters_hash": digest,
        "request_id": item.get("request_id"),
        "reason": item.get("reason"),
        "reused": item.get("reused"),
        "error_code": item.get("error_code"),
    }


def register_strength_variant_demand(
    parameters: Mapping[str, Any],
    *,
    principal: str | None = None,
    root: Path | None = None,
    now: float | None = None,
    enqueue: bool = True,
) -> dict[str, Any]:
    """Dedupe, cap, expire, and optionally wake the strength_refresh task."""

    from app.api.strength import normalize_strength_scan_parameters, strength_scan_parameters_hash

    normalized = normalize_strength_scan_parameters(dict(parameters))
    if not customer_variant_parameters_allowed(normalized):
        raise ValueError("strength variant demand parameters are not allowed")
    digest = strength_scan_parameters_hash(normalized)
    observed = time.time() if now is None else float(now)
    directory = _root(root)
    with _demand_lock(directory):
        active = _prune_locked(directory, observed)
        existing = active.get(digest)
        if existing is not None:
            previous = _finite_time(existing.get("requested_at")) or 0.0
            if observed - previous < VARIANT_DEMAND_WRITE_INTERVAL_SECONDS:
                public = _public_demand(existing)
                public.setdefault("parameters_hash", digest)
                public["reused"] = True
                public["reason"] = public.get("reason") or "deduped"
                return public
        elif len(active) >= VARIANT_DEMAND_MAX:
            return {
                "status": "failed",
                "error_code": "variant_demand_limit",
                "parameters_hash": digest,
                "reason": "variant_demand_limit",
            }
        record = {
            "parameters": normalized,
            "parameters_hash": digest,
            "principal": principal,
            "status": "preparing",
            "requested_at": observed,
            "expires_at": observed + VARIANT_DEMAND_TTL_SECONDS,
        }
        _write_json(directory / f"{digest}.json", record)
    queued: dict[str, Any] = {}
    if enqueue:
        queued = enqueue_customer_variant_refresh(normalized, now=observed)
        fatal = queued.get("error_code") in {"worker_task_disabled", "variant_demand_limit"}
        try:
            with _demand_lock(directory):
                current = _read_json(directory / f"{digest}.json") or record
                current.update(
                    {
                        "status": (
                            "failed" if fatal
                            else queued.get("status") or current.get("status") or "preparing"
                        ),
                        "request_id": queued.get("request_id"),
                        "reason": queued.get("reason"),
                        "error_code": queued.get("error_code") if fatal else None,
                        "reused": queued.get("reused"),
                    }
                )
                if not fatal and current.get("status") in FAILED_STATUSES:
                    current["status"] = "preparing"
                _write_json(directory / f"{digest}.json", current)
                record = current
        except OSError:
            record.update(queued)
            if not fatal:
                record["status"] = "preparing"
                record.pop("error_code", None)
    else:
        record.update(queued)
    public = _public_demand(record)
    public.setdefault("parameters_hash", digest)
    public.setdefault("status", "preparing")
    return public


def strength_variant_unavailable_detail(
    demand: Mapping[str, Any] | None,
) -> dict[str, Any]:
    status = str((demand or {}).get("status") or "")
    error_code = (demand or {}).get("error_code")
    preparing = bool(demand) and status in PREPARING_STATUSES and error_code not in {
        "variant_demand_limit",
        "worker_task_disabled",
    }
    if preparing:
        return {
            "code": "strength_snapshot_preparing",
            "status": "preparing",
            "message": "中长期趋势排序正在后台生成，请稍候。",
            "variant_demand": dict(demand or {}),
        }
    return {
        "code": "strength_snapshot_unavailable",
        "status": "unavailable" if not error_code else "failed",
        "message": "强势雷达后台快照暂不可用",
        "variant_demand": dict(demand or {}) if demand else None,
        "error_code": error_code,
    }
