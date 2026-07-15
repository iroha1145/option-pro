"""Owner-facing controls for the unified personal worker."""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.worker.state import WorkerStateRepository


router = APIRouter(prefix="/api/worker", tags=["worker"])

ActionType = Literal[
    "focus_refresh",
    "strength_refresh",
    "breakout_refresh",
    "retention",
]

_ACTION_TASKS: dict[str, str] = {
    "focus_refresh": "focus",
    "strength_refresh": "focus",
    "breakout_refresh": "breakout",
    "retention": "maintenance",
}
_ACTION_COOLDOWNS: dict[str, float] = {
    "focus_refresh": 30.0,
    "strength_refresh": 30.0,
    "breakout_refresh": 30.0,
    "retention": 300.0,
}


class ManualActionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


def _state_path() -> Path:
    legacy = os.environ.get("OPTIX_WORKER_DB_PATH", "").strip()
    if legacy:
        path = Path(legacy).expanduser()
    else:
        data_dir = Path(
            os.environ.get("DATA_DIR", "/data").strip() or "/data"
        ).expanduser()
        path = data_dir / "optix-worker.db"
    if not path.is_absolute() or ".." in path.parts:
        raise RuntimeError("worker_state_path_invalid")
    return path


def _repository() -> WorkerStateRepository:
    return WorkerStateRepository(_state_path())


def _minute_key(action_type: str, observed: datetime | None = None) -> str:
    current = (observed or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return f"{action_type}:{current.strftime('%Y%m%dT%H%MZ')}"


def _task_state(worker: dict[str, Any], task_name: str) -> dict[str, Any] | None:
    return next(
        (
            item
            for item in worker.get("tasks", [])
            if str(item.get("task_name") or "") == task_name
        ),
        None,
    )


def _public_action(item: dict[str, Any]) -> dict[str, Any]:
    return {
        key: item.get(key)
        for key in (
            "request_id",
            "action_type",
            "task_name",
            "status",
            "requested_at",
            "started_at",
            "completed_at",
            "cooldown_until",
            "error_code",
            "details",
            "reused",
            "reason",
        )
        if key in item
    }


def _read_health(repository: WorkerStateRepository) -> dict[str, Any]:
    try:
        return repository.health()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return {
            "healthy": False,
            "status": "unavailable",
            "tasks": [],
            "actions": [],
        }


@router.get("/status")
async def worker_status() -> dict[str, Any]:
    worker = _read_health(_repository())
    return {
        "healthy": bool(worker.get("healthy")),
        "status": str(worker.get("status") or "unavailable"),
        "heartbeat_at": worker.get("heartbeat_at"),
        "heartbeat_age_seconds": worker.get("heartbeat_age_seconds"),
        "tasks": list(worker.get("tasks") or []),
        "actions": [
            _public_action(item) for item in list(worker.get("actions") or [])
        ],
    }


@router.get("/actions")
async def list_actions(
    action_type: ActionType | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    try:
        items = _repository().action_requests(
            action_type=action_type,
            limit=limit,
        )
    except (OSError, sqlite3.Error, TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc
    return {"actions": [_public_action(item) for item in items]}


@router.get("/actions/{request_id}")
async def get_action(request_id: str) -> dict[str, Any]:
    try:
        item = _repository().action_request(request_id)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "invalid_request_id"},
        ) from exc
    except (OSError, sqlite3.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "action_not_found"},
        )
    return _public_action(item)


@router.post("/actions/{action_type}", status_code=status.HTTP_202_ACCEPTED)
async def request_action(
    action_type: ActionType,
    body: ManualActionRequest,
    response: Response,
) -> dict[str, Any]:
    repository = _repository()
    worker = _read_health(repository)
    if not worker.get("healthy"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "worker_unavailable",
                "worker_status": str(worker.get("status") or "unavailable"),
            },
        )

    task_name = _ACTION_TASKS[action_type]
    task = _task_state(worker, task_name)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_task_unavailable", "task": task_name},
        )
    if not bool(task.get("enabled")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "worker_task_disabled", "task": task_name},
        )

    key = body.idempotency_key or _minute_key(action_type)
    try:
        item = repository.request_action(
            action_type,
            task_name,
            key,
            cooldown_seconds=_ACTION_COOLDOWNS[action_type],
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc

    if item.get("reason") in {"idempotent", "already_running", "cooldown"}:
        response.status_code = status.HTTP_200_OK
    return _public_action(item)


__all__ = ["router"]
