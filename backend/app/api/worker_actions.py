"""Owner-facing controls for the unified personal worker."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.strength import (
    DEFAULT_STRENGTH_SCAN_PARAMETERS,
    normalize_strength_scan_parameters,
    strength_scan_parameters_hash,
)
from app.data_paths import get_data_paths
from app.worker.state import WorkerStateRepository


router = APIRouter(prefix="/api/worker", tags=["worker"])

ActionType = Literal[
    "focus_refresh",
    "strength_refresh",
    "breakout_refresh",
    "earnings_analysis",
    "macro_conditions",
    "retention",
]

_ACTION_TASKS: dict[str, str] = {
    "focus_refresh": "focus_refresh",
    "strength_refresh": "strength_refresh",
    "breakout_refresh": "breakout_refresh",
    "earnings_analysis": "earnings_analysis",
    # Scheduled and manual macro refreshes deliberately share one task name.
    "macro_conditions": "macro_conditions",
    "retention": "retention",
}
_ACTION_COOLDOWNS: dict[str, float] = {
    "focus_refresh": 30.0,
    "strength_refresh": 30.0,
    "breakout_refresh": 30.0,
    "earnings_analysis": 60.0,
    "macro_conditions": 300.0,
    "retention": 300.0,
}


class StrengthRefreshParameters(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    universe: Literal["themes"]
    timeframe: Literal["short", "mid", "long", "all"]
    profile: Literal["conservative", "balanced", "aggressive"]
    top: int = Field(ge=5, le=120)
    sector_id: str | None
    min_price: float = Field(ge=0)
    min_avg_dollar_volume: float = Field(ge=0)
    include_options: bool


class ManualActionRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )
    parameters: StrengthRefreshParameters | None = None


def _state_path() -> Path:
    try:
        path = get_data_paths().worker_db
    except ValueError as exc:
        raise RuntimeError("worker_state_path_invalid") from exc
    if not path.is_absolute():
        raise RuntimeError("worker_state_path_invalid")
    return path


def _repository() -> WorkerStateRepository:
    return WorkerStateRepository(_state_path())


def _require_earnings_manual_submission() -> None:
    """Apply the same runtime gates as a single earnings AI job."""

    from app.api.ai import _require_earnings_manual_submission

    _require_earnings_manual_submission()


def _minute_key(
    action_type: str,
    observed: datetime | None = None,
    *,
    parameters_hash: str | None = None,
) -> str:
    current = (observed or datetime.now(timezone.utc)).astimezone(timezone.utc)
    key = f"{action_type}:{current.strftime('%Y%m%dT%H%MZ')}"
    return f"{key}:{parameters_hash}" if parameters_hash else key


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
    parameters_were_supplied = "parameters" in body.model_fields_set
    action_details: dict[str, Any] = {}
    parameters_hash: str | None = None
    if action_type == "strength_refresh":
        if parameters_were_supplied and body.parameters is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "strength_parameters_required"},
            )
        raw_parameters = (
            body.parameters.model_dump()
            if body.parameters is not None
            else dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
        )
        try:
            parameters = normalize_strength_scan_parameters(raw_parameters)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"code": "strength_parameters_invalid"},
            ) from exc
        parameters_hash = strength_scan_parameters_hash(parameters)
        action_details = {
            "parameters": parameters,
            "parameters_hash": parameters_hash,
        }
    elif parameters_were_supplied:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "action_parameters_not_allowed"},
        )

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
    if action_type == "earnings_analysis":
        _require_earnings_manual_submission()

    key = body.idempotency_key or _minute_key(
        action_type,
        parameters_hash=parameters_hash,
    )
    try:
        item = repository.request_action(
            action_type,
            task_name,
            key,
            cooldown_seconds=_ACTION_COOLDOWNS[action_type],
            details=action_details,
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
