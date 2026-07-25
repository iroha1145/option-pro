"""Read API and owner refresh action for Optix 宏观环境 (Optix Macro Conditions).

Every GET reads only the local SQLite snapshot: it never contacts FRED, never
downloads ETF history, and never queues worker work. The single POST enqueues one
worker action and returns; the request thread does no network I/O.

Scores served here are five-year historical percentiles of public macro data.
They are not forecasts, not probabilities of a market move, and not advice.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.access import require_owner_access, require_same_origin_action
from app.config import get_settings
from app.data_paths import get_data_paths
from app.personal_config import get_personal_config
from app.services.macro_conditions.models import MacroError
from app.services.macro_conditions.registry import (
    FACTORS_BY_ID,
    MODULES_BY_ID,
    SCORING_VERSION,
)
from app.services.macro_conditions.repository import MacroRepository
from app.services.macro_conditions.service import (
    DEFAULT_HISTORY_DAYS,
    MAX_HISTORY_DAYS,
    MIN_HISTORY_DAYS,
    MacroConditionsService,
    MacroServiceConfig,
    cached_read,
)
from app.worker.state import WorkerStateRepository


router = APIRouter(prefix="/api/macro/conditions", tags=["macro"])

MACRO_ACTION_TYPE = "macro_conditions"
MACRO_TASK_NAME = "macro_conditions"


class MacroRefreshRequest(BaseModel):
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


def _config() -> MacroServiceConfig:
    return MacroServiceConfig.from_personal_config(get_personal_config())


def _key_configured() -> bool:
    return get_settings().macro_conditions_configured


def _service() -> MacroConditionsService:
    """Read-only service. Constructing it never creates or migrates the file."""

    path = get_data_paths().macro_conditions_db
    repository = MacroRepository(path, read_only=True)
    return MacroConditionsService(repository, config=_config())


def _read(key: str, producer: Any) -> Any:
    return cached_read(get_data_paths().macro_conditions_db, key, producer)


@router.get("")
async def macro_conditions() -> dict[str, Any]:
    service = _service()
    return _read(
        "current",
        lambda: service.current(key_configured=_key_configured()),
    )


@router.get("/history")
async def macro_conditions_history(
    days: Annotated[int, Query(ge=MIN_HISTORY_DAYS, le=MAX_HISTORY_DAYS)] = (
        DEFAULT_HISTORY_DAYS
    ),
) -> dict[str, Any]:
    service = _service()
    return _read(f"history:{days}", lambda: service.history(days=days))


@router.get("/modules/{module_id}")
async def macro_conditions_module(module_id: str) -> dict[str, Any]:
    if module_id not in MODULES_BY_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "macro_snapshot_unavailable", "resource": "module"},
        )
    service = _service()
    try:
        return _read(
            f"module:{module_id}",
            lambda: service.module_detail(module_id),
        )
    except MacroError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        ) from exc


@router.get("/factors/{factor_id}/history")
async def macro_conditions_factor_history(
    factor_id: str,
    days: Annotated[int, Query(ge=MIN_HISTORY_DAYS, le=MAX_HISTORY_DAYS)] = (
        DEFAULT_HISTORY_DAYS
    ),
) -> dict[str, Any]:
    if factor_id not in FACTORS_BY_ID:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "macro_snapshot_unavailable", "resource": "factor"},
        )
    service = _service()
    try:
        return _read(
            f"factor:{factor_id}:{days}",
            lambda: service.factor_history(factor_id, days=days),
        )
    except MacroError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": exc.code},
        ) from exc


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    # The gateway allowlist is not the only gate. Owner identity and the
    # same-origin action guard are asserted by the route itself, so this endpoint
    # stays closed even if the allowlist is ever mis-edited.
    dependencies=[
        Depends(require_owner_access),
        Depends(require_same_origin_action),
    ],
)
async def refresh_macro_conditions(
    body: MacroRefreshRequest,
    response: Response,
) -> dict[str, Any]:
    """Queue one macro refresh. The request thread performs no network I/O."""

    config = _config()
    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "macro_disabled"},
        )
    if not _key_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "fred_api_key_missing"},
        )

    try:
        state_path = get_data_paths().worker_db
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc
    repository = WorkerStateRepository(state_path)
    try:
        worker = repository.health()
    except (OSError, sqlite3.Error, TypeError, ValueError):
        worker = {"healthy": False, "status": "unavailable", "tasks": []}
    if not worker.get("healthy"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "worker_unavailable",
                "worker_status": str(worker.get("status") or "unavailable"),
            },
        )
    task = next(
        (
            item
            for item in worker.get("tasks", [])
            if str(item.get("task_name") or "") == MACRO_TASK_NAME
        ),
        None,
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_task_unavailable", "task": MACRO_TASK_NAME},
        )
    if not bool(task.get("enabled")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "worker_task_disabled", "task": MACRO_TASK_NAME},
        )

    observed = datetime.now(timezone.utc)
    key = body.idempotency_key or (
        f"{MACRO_ACTION_TYPE}:{observed.strftime('%Y%m%dT%H%MZ')}"
    )
    try:
        item = repository.request_action(
            MACRO_ACTION_TYPE,
            MACRO_TASK_NAME,
            key,
            cooldown_seconds=float(config.manual_refresh_cooldown_seconds),
            details={"scoring_version": SCORING_VERSION},
            now=observed,
        )
    except (OSError, sqlite3.Error, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc

    reason = str(item.get("reason") or "")
    if reason in {"idempotent", "already_running", "cooldown"}:
        response.status_code = status.HTTP_200_OK
    return {
        "request_id": item.get("request_id"),
        "action_type": item.get("action_type"),
        "task_name": item.get("task_name"),
        "status": item.get("status"),
        "reason": reason,
        "reused": bool(item.get("reused")),
        "requested_at": item.get("requested_at"),
        "cooldown_until": item.get("cooldown_until"),
        "cooldown_seconds": float(config.manual_refresh_cooldown_seconds),
        "error_code": (
            "macro_refresh_in_progress"
            if reason == "already_running"
            else "macro_refresh_cooldown"
            if reason == "cooldown"
            else None
        ),
    }


__all__ = ["MACRO_ACTION_TYPE", "MACRO_TASK_NAME", "router"]
