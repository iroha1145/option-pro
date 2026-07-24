from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import json
import math
import time
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
)

from app.api.stocks import _sanitize
from app.access import current_request_is_owner, require_same_origin_json
from app.config import get_settings
from app.personal_config import get_personal_config
from app.services.ai_jobs import runtime as ai_job_runtime
from app.services.ai_jobs.models import (
    CancelRequest,
    EarningsImpactJobRequest,
    OptionAlertJobRequest,
    earnings_report_id,
    normalize_earnings_analysis_payload,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.services.request_security import request_client_ip
from app.services.runtime_settings import (
    RuntimeSettingsStorageError,
    get_effective_runtime_settings,
)

_MAX_AI_BODY_BYTES = 64 * 1024
_PUBLIC_EARNINGS_WINDOW_SECONDS = 10 * 60
_PUBLIC_EARNINGS_LIMIT = 3
_PUBLIC_EARNINGS_MAX_CLIENTS = 2_048
_RETRYABLE_EARNINGS_REPORT_STATUSES = frozenset(
    {"failed", "cancelled", "budget_blocked"}
)
_NON_RETRYABLE_EARNINGS_ERRORS = frozenset(
    {"submission_outcome_unknown", "duplicate_request_migrated"}
)
_public_earnings_recent: dict[str, deque[tuple[float, str]]] = {}
_PROMPT_VERSIONS = {
    "earnings_impact": "earnings-impact-zh-cn-v5",
    "option_alerts": "option-alerts-zh-cn-v4",
    "signal_analysis": "signal-analysis-zh-cn-v5",
    "news_impact": "news-impact-zh-cn-v6",
    "market_focus": "market-focus-zh-cn-v5",
}


class _BoundedBodyRoute(APIRoute):
    """Reject oversized AI bodies before Pydantic/model processing."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                    if length > _MAX_AI_BODY_BYTES:
                        raise HTTPException(status_code=413, detail="AI request body exceeds 64 KiB")
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
            # Content-Length is optional (for example with chunked transfer), so
            # enforce the limit while reading instead of buffering an unbounded
            # body first. Starlette reuses ``request._body`` when the endpoint
            # later asks for JSON, keeping this compatible with normal parsing.
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_AI_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="AI request body exceeds 64 KiB")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            return await original_handler(request)

        return bounded_handler


router = APIRouter(prefix="/api/ai", tags=["ai"], route_class=_BoundedBodyRoute)

Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=12,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-^]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Expiration = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=10, pattern=r"^(?:\d{4}-\d{2}-\d{2})?$"),
]


class AlertItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    strike: float = Field(ge=0, le=10_000_000)
    type: Literal["call", "put"]
    expiration: Expiration = ""
    # The option-chain service reports the precise time remaining to the
    # market close, so DTE can legitimately contain a fractional day.
    dte: Optional[float] = Field(default=None, ge=0, le=3660)
    volume: int = Field(ge=0, le=2_000_000_000)
    open_interest: int = Field(default=0, ge=0, le=2_000_000_000)
    last_price: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    implied_volatility: Optional[float] = Field(default=None, ge=0, le=100)
    premium_flow: Optional[float] = Field(default=None, ge=0, le=1_000_000_000_000_000)
    vol_oi_ratio: Optional[float] = Field(default=None, ge=0, le=2_000_000_000)
    reasons: list[ShortText] = Field(default_factory=list, max_length=8)
    signal: Optional[Literal["bullish", "bearish", "mixed", "unknown"]] = None
    inferred_direction: Optional[Literal["bullish", "bearish", "mixed", "unknown"]] = None
    moneyness: Optional[Literal["itm", "otm", "atm", "unavailable"]] = None
    direction: Optional[Literal["bullish", "bearish", "mixed", "unknown"]] = None
    direction_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    direction_status: Optional[
        Literal["available", "unavailable_without_trade_side"]
    ] = None
    direction_deprecated: bool = False
    direction_note: Annotated[str, StringConstraints(max_length=300)] = ""

    @field_validator("expiration")
    @classmethod
    def validate_expiration(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value


class AlertsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    ticker: Ticker
    force: StrictBool = False
    alerts: list[AlertItem] = Field(default_factory=list, max_length=10)
    underlying_price: float = Field(default=0, ge=0, le=10_000_000)
    expiration: Expiration = ""

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()

    @field_validator("expiration")
    @classmethod
    def validate_expiration(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value


class EarningsReportAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm: StrictBool


@lru_cache(maxsize=4)
def _job_repository_for_path(path: str) -> AIJobRepository:
    return AIJobRepository(path)


def _job_repository() -> AIJobRepository:
    return _job_repository_for_path(str(get_settings().openai_job_db_path))


def _require_runtime_capability() -> None:
    settings = get_settings()
    capability = ai_job_runtime.capability_status(settings)
    if not capability.get("supported"):
        raise HTTPException(
            status_code=503,
            detail={
                "code": capability.get("status") or "ai_runtime_unavailable",
                "message": "Persistent AI analysis is not available",
            },
        )


def _require_manual_analysis_enabled() -> None:
    """Fail closed before any user-initiated paid task reaches the queue."""

    if not get_personal_config().catalyst_manual_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "read_only_mode",
                "message": "当前为只读模式",
            },
        )
    try:
        effective = get_effective_runtime_settings()
    except RuntimeSettingsStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_settings_unavailable",
                "message": "运行设置暂不可用",
            },
        ) from exc
    if not effective.ai.manual_analysis_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manual_analysis_disabled",
                "message": "手动分析已关闭",
            },
        )


def _require_earnings_manual_analysis_enabled() -> None:
    """Apply the earnings manual switch without consulting catalyst mode."""

    try:
        effective = get_effective_runtime_settings()
    except RuntimeSettingsStorageError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "runtime_settings_unavailable",
                "message": "运行设置暂不可用",
            },
        ) from exc
    if not effective.ai.manual_analysis_enabled:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "manual_analysis_disabled",
                "message": "手动分析已关闭",
            },
        )


def _require_manual_submission() -> None:
    # Check the owner switch before provider configuration so callers receive
    # one stable public reason whenever manual paid work is disabled.
    _require_manual_analysis_enabled()
    _require_runtime_capability()


def _require_earnings_manual_submission() -> None:
    _require_earnings_manual_analysis_enabled()
    _require_runtime_capability()


def _create_job(
    job_type: str,
    payload: dict,
    *,
    force_retry: bool = False,
    priority: int = 80,
) -> tuple[dict, bool]:
    # Recheck at the durable enqueue boundary in case the owner changed the
    # switch after the endpoint's initial validation.
    if job_type == "earnings_impact":
        _require_earnings_manual_analysis_enabled()
    else:
        _require_manual_analysis_enabled()
    settings = get_settings()
    max_queued = settings.openai_job_max_queued
    if job_type == "earnings_impact":
        # Scheduled pre-release work may fill the normal queue. Keep a small,
        # quota-protected reserve so an explicit report analysis remains usable.
        max_queued += ai_job_runtime.EARNINGS_MANUAL_QUEUE_RESERVE
    ai_job_runtime.validate_job_payload(job_type, payload)
    schema_version, schema_sha256 = ai_job_runtime.schema_identity(job_type)
    try:
        return _job_repository().create_job(
            job_type=job_type,
            payload=payload,
            model=settings.openai_model,
            reasoning=settings.openai_reasoning,
            execution_mode=settings.openai_execution_mode,
            prompt_version=_PROMPT_VERSIONS[job_type],
            schema_version=schema_version,
            schema_sha256=schema_sha256,
            max_queued=max_queued,
            submission_source="manual",
            priority=priority,
            force_retry=force_retry,
        )
    except RuntimeError as exc:
        if str(exc) == "ai_job_queue_full":
            raise HTTPException(
                status_code=429,
                detail={"code": "ai_job_queue_full", "retry_after": 60},
                headers={"Retry-After": "60"},
            ) from exc
        if str(exc) in {
            "earnings_analysis_locked",
            "earnings_finalization_in_progress",
        }:
            code = str(exc)
            message = (
                "该财报的最终分析已经生成"
                if code == "earnings_analysis_locked"
                else "该财报的最终分析正在生成"
            )
            raise HTTPException(
                status_code=409,
                detail={"code": code, "message": message},
            ) from exc
        raise


def _reserve_public_earnings_submission(
    request: Request,
    payload: dict,
    *,
    force_retry: bool,
) -> None:
    if current_request_is_owner():
        return
    now = time.monotonic()
    cutoff = now - _PUBLIC_EARNINGS_WINDOW_SECONDS
    for client_id, attempts in list(_public_earnings_recent.items()):
        while attempts and attempts[0][0] <= cutoff:
            attempts.popleft()
        if not attempts:
            _public_earnings_recent.pop(client_id, None)
    if len(_public_earnings_recent) > _PUBLIC_EARNINGS_MAX_CLIENTS:
        overflow = len(_public_earnings_recent) - _PUBLIC_EARNINGS_MAX_CLIENTS
        for client_id, attempts in sorted(
            _public_earnings_recent.items(),
            key=lambda item: item[1][-1][0] if item[1] else float("-inf"),
        )[:overflow]:
            _public_earnings_recent.pop(client_id, None)

    task_key = hashlib.sha256(
        json.dumps(
            {
                "report_id": payload.get("report_id"),
                "input_hash": payload.get("input_hash"),
                "analysis_stage": payload.get("analysis_stage"),
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    client_id = request_client_ip(request)
    attempts = _public_earnings_recent.setdefault(client_id, deque())
    if not force_retry and any(key == task_key for _stamp, key in attempts):
        return
    if len(attempts) >= _PUBLIC_EARNINGS_LIMIT:
        retry_after = max(
            1,
            math.ceil(
                attempts[0][0] + _PUBLIC_EARNINGS_WINDOW_SECONDS - now
            ),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "earnings_analysis_rate_limited",
                "message": "财报分析触发过于频繁，请稍后再试",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )
    attempts.append((now, task_key))


def _earnings_report_force_retry(row: dict | None) -> bool:
    """Retry only settled report jobs that the repository can safely replace."""

    return bool(
        row
        and row.get("status") in _RETRYABLE_EARNINGS_REPORT_STATUSES
        and row.get("error_code") not in _NON_RETRYABLE_EARNINGS_ERRORS
    )


async def _normalized_earnings_submission(
    req: EarningsImpactJobRequest,
) -> dict:
    raw = req.model_dump(
        mode="json",
        exclude={
            "force",
            "analysis_stage",
            "analysis_phase",
            "report_id",
            "input_hash",
        },
    )
    if not current_request_is_owner():
        from app.api import earnings

        snapshot = await earnings._read_current_upcoming_earnings_snapshot()
        if (
            not isinstance(snapshot, dict)
            or snapshot.get("data_limited") is not False
            or snapshot.get("source_status") != "active"
        ):
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "earnings_snapshot_unavailable",
                    "message": "当前财报快照不可用",
                },
            )
        requested_date = str(req.earnings_date or "")
        matches = [
            value
            for value in list(snapshot.get("earnings") or [])
            if (
                isinstance(value, dict)
                and str(value.get("ticker") or "").strip().upper() == req.ticker
                and (
                    not requested_date
                    or str(value.get("earnings_date") or "") == requested_date
                )
            )
        ]
        if not matches:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "earnings_snapshot_mismatch",
                    "message": "当前快照中没有匹配的财报",
                },
            )
        source = matches[0]
        raw = {
            field: source.get(field)
            for field in (
                "ticker",
                "name",
                "sector",
                "earnings_date",
                "year",
                "quarter",
                "eps_estimate",
                "eps_actual",
                "revenue_estimate",
                "revenue_actual",
                "market_cap",
                "release_status",
            )
        }
    has_actual = (
        raw.get("eps_actual") is not None
        or raw.get("revenue_actual") is not None
    )
    stage: Literal["pre_release", "post_release_manual"] = (
        "post_release_manual" if has_actual else "pre_release"
    )
    try:
        return normalize_earnings_analysis_payload(
            raw,
            analysis_stage=stage,
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "earnings_snapshot_invalid",
                "message": "财报快照字段不完整",
            },
        ) from exc


async def _snapshot_bound_earnings_report(
    ticker: str,
    report_date: str,
    *,
    year: int | None,
    quarter: int | None,
) -> dict:
    from app.api import earnings

    snapshot = await earnings._read_current_upcoming_earnings_snapshot()
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("data_limited") is not False
        or snapshot.get("source_status") != "active"
    ):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "earnings_snapshot_unavailable",
                "message": "当前财报快照不可用",
            },
        )
    matches = [
        value
        for value in list(snapshot.get("earnings") or [])
        if (
            isinstance(value, dict)
            and str(value.get("ticker") or "").strip().upper() == ticker
            and str(value.get("earnings_date") or "") == report_date
            and (
                year is None
                or value.get("year") is None
                or value.get("year") == year
            )
            and (
                quarter is None
                or value.get("quarter") is None
                or value.get("quarter") == quarter
            )
        )
    ]
    if not matches:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "earnings_snapshot_mismatch",
                "message": "当前快照中没有匹配的财报",
            },
        )
    source = matches[0]
    raw = {
        field: source.get(field)
        for field in (
            "ticker",
            "name",
            "sector",
            "earnings_date",
            "year",
            "quarter",
            "eps_estimate",
            "eps_actual",
            "revenue_estimate",
            "revenue_actual",
            "market_cap",
            "release_status",
        )
    }
    has_actual = (
        raw.get("eps_actual") is not None
        or raw.get("revenue_actual") is not None
    )
    return normalize_earnings_analysis_payload(
        raw,
        analysis_stage=(
            "post_release_manual" if has_actual else "pre_release"
        ),
    )


def _public_earnings_report_status(
    repository: AIJobRepository,
    row: dict,
    *,
    cached: bool = False,
) -> dict:
    published = repository.public(row, cached=cached)
    return _sanitize(
        {
            "status": published.get("status"),
            "error_code": published.get("error_code"),
            "result": published.get("result"),
            "retry_after_seconds": (
                2
                if published.get("status")
                in {"pending", "queued", "in_progress"}
                else None
            ),
            **{
                key: published.get(key)
                for key in (
                    "_analysis_stage",
                    "_analysis_phase",
                    "_report_date",
                    "_report_id",
                    "_input_hash",
                    "_locked",
                    "_final",
                    "_finalization_in_progress",
                )
            },
        }
    )


@router.get("/status")
async def ai_status():
    settings = get_settings()
    capability = ai_job_runtime.capability_status(settings)
    return {
        "enabled": bool(capability.get("supported")),
        "status": capability.get("status"),
        "provider_capability_supported": bool(capability.get("supported")),
        "sdk_capability_supported": bool(capability.get("sdk_supported")),
        "methods": capability.get("methods", {}),
        "model": settings.openai_model,
        "reasoning": settings.openai_reasoning,
        "execution_mode": settings.openai_execution_mode,
        "background_poll_timeout_seconds": (
            settings.openai_background_poll_timeout_seconds
        ),
    }


@router.post("/analyze-alerts")
async def analyze_alerts(req: AlertsRequest):
    """Compatibility endpoint: validation remains, paid work moved to jobs."""

    return JSONResponse(
        {
            "status": "analysis_required",
            "message": "Create a persistent option-alerts job with POST /api/ai/jobs/option-alerts",
        },
        status_code=409,
    )


@router.get("/earnings-correlation")
async def earnings_correlation():
    return JSONResponse(
        {
            "status": "analysis_required",
            "message": "Synchronous GET no longer creates paid analysis",
        },
        status_code=409,
    )


@router.get("/earnings-impact/{ticker}")
async def earnings_impact(
    ticker: Annotated[Ticker, Path(description="US-listed ticker symbol")],
    report_id: Annotated[
        str | None,
        Query(max_length=96),
    ] = None,
    report_date: Annotated[
        str | None,
        Query(max_length=10),
    ] = None,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    quarter: Annotated[int | None, Query(ge=1, le=4)] = None,
):
    """Return a completed cache entry; GET never creates paid work."""

    resolved_report_id = report_id
    if not resolved_report_id and report_date:
        resolved_report_id = earnings_report_id(
            {
                "ticker": ticker,
                "earnings_date": report_date,
                "year": year,
                "quarter": quarter,
            }
        )
    repository = _job_repository()
    row = repository.latest_completed(
        "earnings_impact",
        ticker,
        report_id=resolved_report_id,
    )
    if row:
        published = repository.public(row, cached=True)
        result = published["result"]
        if result is not None:
            return _sanitize(
                {
                    **result,
                    "_cached": True,
                    "_job_id": row["job_id"],
                    "_generated_at": row.get("completed_at"),
                    **{
                        key: published.get(key)
                        for key in (
                            "_analysis_stage",
                            "_analysis_phase",
                            "_report_date",
                            "_report_id",
                            "_input_hash",
                            "_locked",
                            "_final",
                            "_finalization_in_progress",
                        )
                    },
                }
            )
    return JSONResponse(
        {
            "status": "analysis_required",
            "ticker": ticker.upper(),
            "message": "Create a persistent job with POST /api/ai/jobs/earnings-impact",
        },
        status_code=409,
    )


@router.get("/earnings-impact/{ticker}/reports/{report_date}")
async def earnings_report_impact(
    ticker: Annotated[Ticker, Path(description="US-listed ticker symbol")],
    report_date: Annotated[str, Path(min_length=10, max_length=10)],
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    quarter: Annotated[int | None, Query(ge=1, le=4)] = None,
):
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report date") from exc
    report_id = earnings_report_id(
        {
            "ticker": ticker,
            "earnings_date": report_date,
            "year": year,
            "quarter": quarter,
        }
    )
    repository = _job_repository()
    row = repository.latest_for_report(ticker, report_id)
    if row is None:
        return JSONResponse(
            {
                "status": "analysis_required",
                "_report_id": report_id,
                "_report_date": report_date,
                "_locked": False,
                "_final": False,
                "_finalization_in_progress": False,
            },
            status_code=409,
        )
    return _public_earnings_report_status(
        repository,
        row,
        cached=row.get("status") == "completed",
    )


@router.post(
    "/earnings-impact/{ticker}/reports/{report_date}",
    dependencies=[Depends(require_same_origin_json)],
)
async def trigger_earnings_report_impact(
    request: Request,
    ticker: Annotated[Ticker, Path(description="US-listed ticker symbol")],
    report_date: Annotated[str, Path(min_length=10, max_length=10)],
    action: EarningsReportAction,
    year: Annotated[int | None, Query(ge=1900, le=2200)] = None,
    quarter: Annotated[int | None, Query(ge=1, le=4)] = None,
):
    if not action.confirm:
        raise HTTPException(
            status_code=400,
            detail={"code": "confirmation_required", "message": "需要确认分析"},
        )
    try:
        date.fromisoformat(report_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid report date") from exc
    _require_earnings_manual_submission()
    payload = await _snapshot_bound_earnings_report(
        ticker,
        report_date,
        year=year,
        quarter=quarter,
    )
    repository = _job_repository()
    force_retry = _earnings_report_force_retry(
        repository.latest_for_report(
            ticker,
            str(payload["report_id"]),
        )
    )
    _reserve_public_earnings_submission(
        request,
        payload,
        force_retry=force_retry,
    )
    row, _created = _create_job(
        "earnings_impact",
        payload,
        force_retry=force_retry,
        priority=(
            ai_job_runtime.EARNINGS_OWNER_PRIORITY
            if current_request_is_owner()
            else ai_job_runtime.EARNINGS_VISITOR_PRIORITY
        ),
    )
    return JSONResponse(
        _public_earnings_report_status(repository, row),
        status_code=202,
        headers={"Retry-After": "2"},
    )


@router.post("/jobs/earnings-impact")
async def create_earnings_impact_job(
    request: Request,
    req: EarningsImpactJobRequest,
):
    _require_earnings_manual_submission()
    request_payload = await _normalized_earnings_submission(req)
    _reserve_public_earnings_submission(
        request,
        request_payload,
        force_retry=req.force,
    )
    owner = current_request_is_owner()
    row, created = _create_job(
        "earnings_impact",
        request_payload,
        force_retry=req.force,
        priority=(
            ai_job_runtime.EARNINGS_OWNER_PRIORITY
            if owner
            else ai_job_runtime.EARNINGS_VISITOR_PRIORITY
        ),
    )
    payload = _job_repository().public(
        row,
        cached=(not created and row["status"] == "completed"),
    )
    return JSONResponse(
        payload,
        status_code=202,
        headers={"Location": f"/api/ai/jobs/{row['job_id']}", "Retry-After": "2"},
    )


@router.post("/jobs/option-alerts")
async def create_option_alert_job(req: AlertsRequest):
    _require_manual_submission()
    payload = OptionAlertJobRequest.model_validate(
        req.model_dump(mode="json", exclude={"force"})
    ).model_dump(mode="json")
    row, created = _create_job(
        "option_alerts",
        payload,
        force_retry=req.force,
    )
    public = _job_repository().public(
        row,
        cached=(not created and row["status"] == "completed"),
    )
    return JSONResponse(
        public,
        status_code=202,
        headers={"Location": f"/api/ai/jobs/{row['job_id']}", "Retry-After": "2"},
    )


@router.get("/jobs/{job_id}")
async def get_ai_job(job_id: Annotated[str, Path(min_length=10, max_length=80)]):
    row = _job_repository().get_job(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="AI job not found")
    if not current_request_is_owner() and row.get("job_type") == "earnings_impact":
        stamp = row.get("completed_at") or row.get("updated_at") or row.get("created_at")
        try:
            observed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            observed = datetime.min.replace(tzinfo=timezone.utc)
        if observed < datetime.now(timezone.utc) - timedelta(days=30):
            raise HTTPException(status_code=404, detail="AI job not found")
    return _job_repository().public(row)


@router.post("/jobs/{job_id}/cancel")
async def cancel_ai_job(
    job_id: Annotated[str, Path(min_length=10, max_length=80)],
    req: CancelRequest,
):
    row = _job_repository().request_cancel(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="AI job not found")
    return _job_repository().public(row)
