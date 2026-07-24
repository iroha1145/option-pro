from __future__ import annotations

from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Request
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
from app.config import get_settings
from app.personal_config import get_personal_config
from app.services.ai_jobs import runtime as ai_job_runtime
from app.services.ai_jobs.models import (
    CancelRequest,
    EarningsImpactJobRequest,
    OptionAlertJobRequest,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.services.runtime_settings import (
    RuntimeSettingsStorageError,
    get_effective_runtime_settings,
)

_MAX_AI_BODY_BYTES = 64 * 1024
_PROMPT_VERSIONS = {
    "earnings_impact": "earnings-impact-zh-cn-v4",
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


def _job_repository() -> AIJobRepository:
    return AIJobRepository(get_settings().openai_job_db_path)


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
) -> tuple[dict, bool]:
    # Recheck at the durable enqueue boundary in case the owner changed the
    # switch after the endpoint's initial validation.
    if job_type == "earnings_impact":
        _require_earnings_manual_analysis_enabled()
    else:
        _require_manual_analysis_enabled()
    settings = get_settings()
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
            max_queued=settings.openai_job_max_queued,
            submission_source="manual",
            priority=80,
            force_retry=force_retry,
        )
    except RuntimeError as exc:
        if str(exc) == "ai_job_queue_full":
            raise HTTPException(
                status_code=429,
                detail={"code": "ai_job_queue_full", "retry_after": 60},
                headers={"Retry-After": "60"},
            ) from exc
        raise


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
):
    """Return a completed cache entry; GET never creates paid work."""

    repository = _job_repository()
    row = repository.latest_completed("earnings_impact", ticker)
    if row:
        result = repository.public(row, cached=True)["result"]
        if result is not None:
            return _sanitize(
                {
                    **result,
                    "_cached": True,
                    "_job_id": row["job_id"],
                    "_generated_at": row.get("completed_at"),
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


@router.post("/jobs/earnings-impact")
async def create_earnings_impact_job(req: EarningsImpactJobRequest):
    _require_earnings_manual_submission()
    request_payload = req.model_dump(mode="json", exclude={"force"})
    row, created = _create_job(
        "earnings_impact",
        request_payload,
        force_retry=req.force,
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
