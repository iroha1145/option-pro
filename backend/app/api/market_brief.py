"""首页「市场综合研判」的读取接口与 Owner 手动补发。

读接口只读 Worker 写好的研判存储（DATA_DIR/market-brief/）：从不调用模型，也不
触发任何供应商请求；密码模式下匿名访客可读最新一份与历史摘要。补发只排一个
Worker 动作就返回，请求线程不做网络 I/O。

研判是对公开数据的解释，不是涨跌概率，也不构成买卖、仓位或目标价建议。
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app.access import (
    current_request_is_owner,
    require_owner_access,
    require_same_origin_action,
)
from app.config import get_settings
from app.data_paths import get_data_paths
from app.personal_config import get_personal_config
from app.services.http_read_cache import respond_with_snapshot, snapshot_version_key
from app.worker.state import WorkerStateRepository


router = APIRouter(prefix="/api/market-brief", tags=["market-brief"])

MARKET_BRIEF_ACTION_TYPE = "market_brief"
MARKET_BRIEF_TASK_NAME = "market_brief"
# 与 api/worker_actions.py 的 _ACTION_COOLDOWNS["market_brief"] 一致（测试钉住）。
MARKET_BRIEF_COOLDOWN_SECONDS = 600.0
_LATEST_CACHE_CONTROL = "private, max-age=60, stale-while-revalidate=300"
_HISTORY_DEFAULT_LIMIT = 10
_HISTORY_MAX_LIMIT = 30


class MarketBriefRunRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        str_strip_whitespace=True,
        allow_inf_nan=False,
    )

    #: 不填时由 Worker 按运行时的美东钟点决定：中午前补开盘前那份，之后补收盘后那份。
    slot: Literal["pre_open", "post_close"] | None = None
    idempotency_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


def _config() -> Any:
    return get_personal_config().market_brief


def _schedule() -> Any:
    return _config().to_schedule()


def _key_configured() -> bool:
    return get_settings().market_brief_configured


def _shared_budget(now: datetime) -> dict[str, Any] | None:
    settings = get_settings()
    amount = float(getattr(settings, "model_daily_budget_usd", 0.0))
    if amount <= 0:
        return None
    from app.services.model_budget import SharedModelBudget
    from app.services.market_brief.claude_runtime import request_budget_reservation_microusd

    config = _config().to_run_config()
    reservation = request_budget_reservation_microusd(config)
    budget = SharedModelBudget(
        settings.openai_job_db_path, amount, brief_store_path=_store().root,
        accounting_start_at=getattr(settings, "model_budget_start_at", None),
        enforce_limit=getattr(settings, "model_budget_enforce_limit", True),
    )
    budget.bootstrap_brief_history(
        now=now, unknown_reservation_microusd=reservation,
    )
    return budget.snapshot(now=now)


def _store() -> Any:
    from app.services.market_brief import BriefStore

    return BriefStore()


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_iso(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _next_slot(now: datetime) -> dict[str, Any] | None:
    from app.services.market_brief import next_slot_at

    upcoming = next_slot_at(now, _schedule())
    if upcoming is None:
        return None
    at, trading_date, slot = upcoming
    return {"slot": slot, "trading_date": trading_date.isoformat(), "at": _iso(at)}


def _require_readable(request: Request) -> bool:
    """返回调用方是否 Owner；关闭公开阅读时把访客挡在门外。"""

    owner = current_request_is_owner()
    if not owner and not _config().public_read:
        require_owner_access(request)
    return owner


def _latest_version_key(payload: Mapping[str, Any], *, owner: bool) -> str:
    brief = payload.get("brief")
    run_id = brief.get("run_id") if isinstance(brief, Mapping) else None
    # next_slot 按读取时刻计算、latest_attempt 随失败的运行变化：两者都进键，
    # 否则字节缓存会在同一份研判下继续送出过期的「下一份」时间与尝试状态。
    dynamic = json.dumps(
        [
            payload.get("status"),
            payload.get("next_slot"),
            payload.get("latest_attempt"),
        ],
        sort_keys=True,
        ensure_ascii=False,
        default=str,
    )
    return snapshot_version_key(
        "market_brief",
        "owner" if owner else "public",
        run_id or "missing",
        payload.get("snapshot_saved_at"),
        dynamic,
    )


@router.get("/latest")
async def latest_market_brief(request: Request) -> Response:
    """最新一份研判。还没有任何成功记录时 status="missing"，仍是 200。

    Owner 额外看到用量与费用；访客投影由存储层负责裁掉。
    """

    owner = _require_readable(request)
    store = _store()
    payload = await asyncio.to_thread(
        store.latest_public,
        now=datetime.now(timezone.utc),
        owner=owner,
        schedule=_schedule(),
    )
    body = dict(payload)
    return await respond_with_snapshot(
        request,
        body,
        version_key=_latest_version_key(body, owner=owner),
        cache_control=_LATEST_CACHE_CONTROL,
    )


@router.get("/history")
async def market_brief_history(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=_HISTORY_MAX_LIMIT)] = _HISTORY_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """最近几次运行的摘要（含失败的运行与其错误码，不含正文）。"""

    _require_readable(request)
    store = _store()
    runs = await asyncio.to_thread(store.history, limit=limit)
    return {"runs": [dict(item) for item in runs]}


def _worker_actions(now: datetime) -> tuple[dict[str, Any] | None, str | None]:
    """排队或进行中的补发请求，以及仍在生效的冷却截止时刻。"""

    try:
        items = WorkerStateRepository(get_data_paths().worker_db).action_requests(
            action_type=MARKET_BRIEF_ACTION_TYPE,
            limit=10,
        )
    except (OSError, sqlite3.Error, ValueError):
        return None, None
    pending: dict[str, Any] | None = None
    active = next(
        (item for item in items if item.get("status") in {"queued", "running"}),
        None,
    )
    if active is not None:
        pending = {
            "request_id": active.get("request_id"),
            "status": active.get("status"),
            "requested_at": active.get("requested_at"),
            "started_at": active.get("started_at"),
            "slot": _requested_slot(active),
        }
    cooldown_until: str | None = None
    completed = [
        item
        for item in items
        if item.get("status") == "completed" and _parse_iso(item.get("cooldown_until"))
    ]
    if completed:
        latest = max(completed, key=lambda item: str(item.get("completed_at") or ""))
        until = _parse_iso(latest.get("cooldown_until"))
        if until is not None and until > now:
            cooldown_until = str(latest.get("cooldown_until"))
    return pending, cooldown_until


def _requested_slot(item: Mapping[str, Any]) -> str | None:
    details = item.get("details")
    parameters = details.get("parameters") if isinstance(details, Mapping) else None
    slot = parameters.get("slot") if isinstance(parameters, Mapping) else None
    return slot if isinstance(slot, str) else None


@router.get("/status", dependencies=[Depends(require_owner_access)])
def market_brief_status() -> dict[str, Any]:
    """Owner 面板：开关、密钥是否配置、下一槽、最近一次运行、排队中的补发与当日次数。

    同步路由：研判存储与 Worker 状态库都是本地文件读，放线程池而不占事件循环。
    """

    from app.services.runtime_settings import (
        RuntimeSettingsStorageError,
        get_effective_runtime_settings,
    )

    config = _config()
    now = datetime.now(timezone.utc)
    store = _store()
    try:
        scheduled_enabled: bool | None = bool(
            get_effective_runtime_settings().market_brief.scheduled_enabled
        )
    except RuntimeSettingsStorageError:
        scheduled_enabled = None
    recent = store.history(limit=1)
    pending, cooldown_until = _worker_actions(now)
    try:
        shared_budget = _shared_budget(now)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "shared_budget_unavailable"},
        ) from exc
    return {
        "enabled": bool(config.enabled),
        "configured": _key_configured(),
        "scheduled_enabled": scheduled_enabled,
        "next_slot": _next_slot(now),
        "last_run": dict(recent[0]) if recent else None,
        "pending_action": pending,
        "cooldown_until": cooldown_until,
        "cooldown_seconds": MARKET_BRIEF_COOLDOWN_SECONDS,
        # 次数按 UTC 日历日计，与 Worker 的手动补发闸门同一口径。
        "daily_runs": int(store.runs_on(now.date())),
        "daily_max_runs": int(config.daily_max_runs),
        "shared_budget": shared_budget,
    }


@router.post(
    "/runs",
    status_code=status.HTTP_202_ACCEPTED,
    # 网关白名单不是唯一一道门：Owner 身份与同源动作校验写在路由自己身上，
    # 白名单哪天被误改，这个接口也不会对访客开放。
    dependencies=[
        Depends(require_owner_access),
        Depends(require_same_origin_action),
    ],
)
def request_market_brief_run(
    body: MarketBriefRunRequest,
    response: Response,
) -> dict[str, Any]:
    """排一次手动补发。请求线程不做网络 I/O。

    同步路由：Worker 状态库写入可能为 SQLite 锁等待最多 30 秒，这段等待属于
    线程池，不该卡住事件循环。
    """

    config = _config()
    if not config.enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "market_brief_disabled"},
        )
    if not _key_configured():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "anthropic_api_key_missing"},
        )
    observed = datetime.now(timezone.utc)
    daily_runs = int(_store().runs_on(observed.date()))
    if daily_runs >= int(config.daily_max_runs):
        # Worker 认领时还会再查一次；这里先拦，免得排一个注定被拒的动作。
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "daily_run_limit_reached",
                "daily_runs": daily_runs,
                "daily_max_runs": int(config.daily_max_runs),
            },
        )

    try:
        shared_budget = _shared_budget(observed)
    except (OSError, sqlite3.Error, RuntimeError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "shared_budget_unavailable"},
        ) from exc
    if shared_budget is not None and not shared_budget["budget_available"]:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "daily_budget_usd_reached",
                "daily_budget_usd": shared_budget["daily_budget_usd"],
                "budget_remaining_usd": shared_budget["budget_remaining_usd"],
            },
        )

    try:
        repository = WorkerStateRepository(get_data_paths().worker_db)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_state_unavailable"},
        ) from exc
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
            if str(item.get("task_name") or "") == MARKET_BRIEF_TASK_NAME
        ),
        None,
    )
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "worker_task_unavailable", "task": MARKET_BRIEF_TASK_NAME},
        )
    if not bool(task.get("enabled")):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "worker_task_disabled", "task": MARKET_BRIEF_TASK_NAME},
        )

    # 槽位进幂等键：同一分钟里先后点「开盘前」和「收盘后」是两个不同的请求。
    key = body.idempotency_key or (
        f"{MARKET_BRIEF_ACTION_TYPE}:{observed.strftime('%Y%m%dT%H%MZ')}"
        f":{body.slot or 'auto'}"
    )
    try:
        item = repository.request_action(
            MARKET_BRIEF_ACTION_TYPE,
            MARKET_BRIEF_TASK_NAME,
            key,
            cooldown_seconds=MARKET_BRIEF_COOLDOWN_SECONDS,
            details={"parameters": {"slot": body.slot}} if body.slot else {},
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
        "cooldown_seconds": MARKET_BRIEF_COOLDOWN_SECONDS,
        "slot": _requested_slot(item),
        "error_code": (
            "market_brief_in_progress"
            if reason == "already_running"
            else "market_brief_cooldown"
            if reason == "cooldown"
            else None
        ),
    }


__all__ = [
    "MARKET_BRIEF_ACTION_TYPE",
    "MARKET_BRIEF_COOLDOWN_SECONDS",
    "MARKET_BRIEF_TASK_NAME",
    "router",
]
