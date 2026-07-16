"""Same-origin, local-cache-only Catalyst API."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request, status
from fastapi.routing import APIRoute
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.services.catalysts.config import CatalystSettings, get_catalyst_settings
from app.services.catalysts.errors import CatalystError, InvalidCursorError
from app.services.catalysts.models import TICKER_PATTERN
from app.services.catalysts.personal_service import PersonalCatalystService
from app.services.catalysts.service import CatalystService
from app.services.ai_jobs.security import require_expensive_action


_MAX_CATALYST_BODY_BYTES = 32 * 1024
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})


class _BoundedCatalystBodyRoute(APIRoute):
    """Reject oversized Catalyst writes before JSON/model parsing."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            if request.method.upper() not in _BODY_METHODS:
                return await original_handler(request)

            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                    if length > _MAX_CATALYST_BODY_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail="Catalyst request body exceeds 32 KiB",
                        )
                except ValueError as exc:
                    raise HTTPException(
                        status_code=400, detail="Invalid Content-Length"
                    ) from exc

            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_CATALYST_BODY_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail="Catalyst request body exceeds 32 KiB",
                    )
                chunks.append(chunk)
            # Starlette reuses this bounded body when FastAPI later parses JSON.
            request._body = b"".join(chunks)
            return await original_handler(request)

        return bounded_handler


router = APIRouter(
    prefix="/api/catalysts",
    tags=["catalysts"],
    route_class=_BoundedCatalystBodyRoute,
)


_HOTSPOT_STATUS_PUBLIC_FIELDS = frozenset(
    {
        "prepared_revision",
        "last_consumed_revision",
        "has_new_hotspots",
        "prepared_hot_count",
        "prepared_since",
        "last_cycle_at",
        "next_scheduled_at",
        "model",
        "reasoning",
        "data_through",
        "status",
        "as_of",
        "last_sync_at",
        "warnings",
        "analysis_availability",
    }
)
_CATALYST_STATUS_PUBLIC_FIELDS = frozenset(
    {
        "enabled",
        "status",
        "as_of",
        "data_through",
        "last_sync_at",
        "remote_status",
        "model",
        "reasoning",
        "expected_model",
        "expected_reasoning",
        "schema_version",
        "resync_required",
        "resync_generation",
        "last_resync_at",
        "sources",
        "streams",
        "warnings",
        "analysis_availability",
        "manual_refreshes",
    }
)
_CATALYST_STREAM_PUBLIC_FIELDS = frozenset(
    {
        "last_success_at",
        "data_through",
        "consecutive_failures",
        "last_error_code",
        "remote_status",
        "resync_required",
        "resync_generation",
        "last_resync_at",
    }
)
_CATALYST_SOURCE_PUBLIC_FIELDS = frozenset(
    {
        "source",
        "status",
        "last_success_at",
        "data_through",
        "consecutive_failures",
        "raw_count",
        "inserted_count",
        "duplicates_count",
        "source_fetch_status",
        "news_persistence_status",
        "event_projection_status",
    }
)
_HOTSPOT_ITEM_PUBLIC_FIELDS = frozenset(
    {
        "prepared_revision",
        "event_group_id",
        "event_group_version",
        "gate_version",
        "hot_score",
        "component_scores",
        "reasons",
        "status",
        "prepared_at",
        "representative_title",
        "event_type",
        "available_at",
        "first_published_at",
        "last_published_at",
        "source_count",
        "source_names",
        "validated_tickers",
    }
)
_MARKET_FOCUS_CYCLE_PUBLIC_FIELDS = frozenset(
    {
        "cycle_id",
        "status",
        "no_new_hot_events",
        "prepared_revision",
        "focus_revision",
        "snapshot_as_of",
        "event_group_count",
        "focus_symbol_count",
        "model",
        "reasoning_effort",
        "result",
        "error_code",
        "created_at",
        "completed_at",
        "updated_at",
        "cycle_revision",
        "force",
        "consumes_prepared_revision",
        "cancel_requested",
    }
)
_MARKET_FOCUS_ENVELOPE_PUBLIC_FIELDS = frozenset(
    {"status", "as_of", "data_through", "warnings"}
)


def _anonymous_public_read(request: Request) -> bool:
    """Only crop requests admitted by the public-read gateway without a token."""

    return bool(
        getattr(request.state, "public_read_authenticated", False)
    ) and not bool(
        getattr(request.state, "app_authenticated", False),
    )


def _select_fields(
    payload: Mapping[str, Any], fields: frozenset[str]
) -> dict[str, Any]:
    return {key: payload[key] for key in fields if key in payload}


def _public_hotspot_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select_fields(payload, _HOTSPOT_STATUS_PUBLIC_FIELDS)
    # Anonymous display requests never gain action capability from remote state.
    projected.update(
        {
            "manual_enabled": False,
            "action_enabled": False,
            "capability": "disabled",
        }
    )
    return projected


def _public_catalyst_status(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select_fields(payload, _CATALYST_STATUS_PUBLIC_FIELDS)
    raw_sources = payload.get("sources")
    projected["sources"] = (
        [
            _select_fields(source, _CATALYST_SOURCE_PUBLIC_FIELDS)
            for source in raw_sources
            if isinstance(source, Mapping)
        ]
        if isinstance(raw_sources, list)
        else []
    )
    raw_streams = payload.get("streams")
    projected["streams"] = {}
    if isinstance(raw_streams, Mapping):
        projected["streams"] = {
            str(stream): _select_fields(state, _CATALYST_STREAM_PUBLIC_FIELDS)
            for stream, state in raw_streams.items()
            if isinstance(state, Mapping)
        }
    projected["analysis_trigger_enabled"] = False
    return projected


def _public_hotspots(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select_fields(payload, _MARKET_FOCUS_ENVELOPE_PUBLIC_FIELDS)
    items = payload.get("items")
    projected["items"] = (
        [
            _select_fields(item, _HOTSPOT_ITEM_PUBLIC_FIELDS)
            for item in items
            if isinstance(item, Mapping)
        ]
        if isinstance(items, list)
        else []
    )
    return projected


def _public_market_focus_cycle(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _select_fields(payload, _MARKET_FOCUS_CYCLE_PUBLIC_FIELDS)


def _public_market_focus_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    projected = _select_fields(payload, _MARKET_FOCUS_ENVELOPE_PUBLIC_FIELDS)
    cycle = payload.get("cycle")
    projected["cycle"] = (
        _public_market_focus_cycle(cycle) if isinstance(cycle, Mapping) else None
    )
    return projected


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BatchRequest(_RequestModel):
    tickers: list[str] = Field(min_length=1, max_length=50)
    as_of: Optional[AwareDatetime] = None
    window_hours: int = Field(default=72, ge=1, le=24 * 365)
    limit: int = Field(default=20, ge=1, le=100)
    min_confidence: int = Field(default=0, ge=0, le=100)
    include_neutral: bool = False
    include_unanalyzed: bool = True

    @field_validator("tickers")
    @classmethod
    def normalize_tickers(cls, values: list[str]) -> list[str]:
        output: list[str] = []
        seen: set[str] = set()
        for value in values:
            ticker = value.strip().upper()
            if not TICKER_PATTERN.fullmatch(ticker):
                raise ValueError("invalid ticker")
            if ticker not in seen:
                seen.add(ticker)
                output.append(ticker)
        return output


class AnalysisRequest(_RequestModel):
    force: bool = False


class RefreshRequest(_RequestModel):
    operation_type: Literal["news", "calendar", "source_health"] = "news"
    idempotency_key: Optional[str] = Field(
        default=None,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._:-]+$",
    )


class MarketFocusCycleRequest(_RequestModel):
    trigger: Literal["manual"] = "manual"
    expected_prepared_revision: Optional[int] = Field(default=None, ge=0)
    retry_cycle_id: Optional[str] = Field(
        default=None, pattern=r"^mfc_[0-9a-f]{32}$"
    )
    force: bool = False

    @model_validator(mode="after")
    def validate_creation_mode(self) -> "MarketFocusCycleRequest":
        if (self.expected_prepared_revision is None) == (self.retry_cycle_id is None):
            raise ValueError(
                "exactly one of expected_prepared_revision or retry_cycle_id is required"
            )
        if self.retry_cycle_id is not None and self.force:
            raise ValueError("retry_cycle_id cannot be combined with force")
        return self


CatalystApiService = CatalystService | PersonalCatalystService


def _internal_token_configured(settings: CatalystSettings) -> bool:
    value = getattr(settings, "internal_token", None)
    if hasattr(value, "get_secret_value"):
        value = value.get_secret_value()
    if isinstance(value, str) and value.strip():
        return True
    # Kept as a migration bridge until CatalystSettings owns the new field.
    # Only the presence bit is read; the token is never copied or logged.
    return bool(os.environ.get("MACROLENS_INTERNAL_TOKEN", "").strip())


def _legacy_read_configured(settings: CatalystSettings) -> bool:
    secret = getattr(settings, "read_secret", None)
    if hasattr(secret, "get_secret_value"):
        secret = secret.get_secret_value()
    return bool(getattr(settings, "read_key_id", "") and secret)


def _service(
    settings: CatalystSettings = Depends(get_catalyst_settings),
) -> CatalystApiService:
    if (
        getattr(settings, "enabled", True)
        and settings.catalyst_mode != "disabled"
        and (
            _internal_token_configured(settings)
            or not _legacy_read_configured(settings)
        )
    ):
        return PersonalCatalystService(settings)
    return CatalystService(settings)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not TICKER_PATTERN.fullmatch(normalized):
        raise HTTPException(status_code=422, detail="invalid ticker")
    return normalized


def _raise_safe(error: CatalystError) -> None:
    status_code = {
        "invalid_cursor": 400,
        "news_not_found": 404,
        "market_focus_cycle_not_found": 404,
        "prepared_revision_changed": 409,
        "no_new_hot_events": 409,
        "market_focus_cycle_not_retryable": 409,
        "market_focus_retry_snapshot_unavailable": 409,
        "market_focus_retry_outcome_unknown": 409,
        "invalid_market_focus_request": 422,
        "invalid_refresh_type": 422,
        "invalid_idempotency_key": 422,
        "ai_job_queue_full": 429,
        "daily_job_limit_reached": 429,
        "daily_budget_usd_reached": 429,
        "daily_output_token_limit_reached": 429,
        "analysis_cooldown_active": 429,
        "analysis_in_progress": 409,
        "ai_not_configured": 503,
        "worker_unavailable": 503,
        "runtime_settings_unavailable": 503,
        "cache_unavailable": 503,
        "capability_disabled": 503,
    }.get(error.code, 503)
    headers = (
        {"Retry-After": str(error.retry_after_seconds)}
        if error.retry_after_seconds is not None
        else None
    )
    raise HTTPException(
        status_code=status_code,
        detail={
            "code": error.code,
            "message": error.message,
            "retryable": error.retryable,
            "retry_after": error.retry_after_seconds,
        },
        headers=headers,
    ) from error


@router.get("/status")
def catalyst_status(
    request: Request,
    service: CatalystService = Depends(_service),
) -> dict:
    payload = service.status()
    return (
        _public_catalyst_status(payload)
        if _anonymous_public_read(request)
        else payload
    )


@router.get("/feed")
def catalyst_feed(
    as_of: Optional[AwareDatetime] = Query(default=None),
    window_hours: int = Query(default=72, ge=1, le=24 * 365),
    limit: int = Query(default=50, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, max_length=4096),
    ticker: Optional[str] = Query(default=None, max_length=20),
    source: Optional[str] = Query(default=None, max_length=500),
    classification: Optional[Literal["bullish", "bearish", "neutral"]] = Query(default=None),
    analysis_status: Optional[str] = Query(default=None, max_length=40),
    min_confidence: int = Query(default=0, ge=0, le=100),
    include_unanalyzed: bool = Query(default=True),
    min_abs_impact: Optional[int] = Query(default=None, ge=0, le=100),
    include_neutral: bool = Query(default=True),
    horizon: Optional[Literal["intraday", "days", "weeks", "uncertain"]] = Query(default=None),
    mechanism: Optional[Literal[
        "direct_company",
        "supplier_customer",
        "sector_readthrough",
        "macro_rate",
        "commodity_input",
        "regulatory",
        "competitive",
        "other",
    ]] = Query(default=None),
    multi_source_only: bool = Query(default=False),
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        return service.feed(
            as_of=as_of or _now(),
            window_hours=window_hours,
            limit=limit,
            cursor=cursor,
            ticker=_ticker(ticker) if ticker else None,
            source=source,
            classification=classification,
            analysis_status=analysis_status,
            min_confidence=min_confidence,
            include_unanalyzed=include_unanalyzed,
            min_abs_impact=min_abs_impact,
            include_neutral=include_neutral,
            horizon=horizon,
            mechanism=mechanism,
            multi_source_only=multi_source_only,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get("/news/{news_id}")
def catalyst_news(
    request: Request,
    news_id: int = Path(ge=1),
    as_of: Optional[AwareDatetime] = Query(default=None),
    service: CatalystService = Depends(_service),
) -> dict:
    observed = as_of or _now()
    try:
        detail = service.news(news_id, as_of=observed)
    except CatalystError as error:
        _raise_safe(error)
    if detail is None:
        raise HTTPException(status_code=404, detail="catalyst news item not found")
    if _anonymous_public_read(request):
        detail = dict(detail)
        detail["analysis_job"] = None
        detail["analysis_trigger_enabled"] = False
    return detail


@router.get("/tickers/{ticker}")
def ticker_catalysts(
    ticker: str,
    as_of: Optional[AwareDatetime] = Query(default=None),
    window_hours: int = Query(default=72, ge=1, le=24 * 365),
    limit: int = Query(default=20, ge=1, le=100),
    cursor: Optional[str] = Query(default=None, max_length=4096),
    min_confidence: int = Query(default=0, ge=0, le=100),
    include_unanalyzed: bool = Query(default=True),
    include_neutral: bool = Query(default=False),
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        return service.ticker(
            _ticker(ticker),
            as_of=as_of or _now(),
            window_hours=window_hours,
            limit=limit,
            cursor=cursor,
            min_confidence=min_confidence,
            include_unanalyzed=include_unanalyzed,
            include_neutral=include_neutral,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.post("/tickers/batch")
def ticker_catalyst_batch(
    request: BatchRequest,
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        return service.batch(
            request.tickers,
            as_of=request.as_of or _now(),
            window_hours=request.window_hours,
            limit=request.limit,
            min_confidence=request.min_confidence,
            include_unanalyzed=request.include_unanalyzed,
            include_neutral=request.include_neutral,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get("/calendar")
def catalyst_calendar(
    date_from: Optional[date] = Query(default=None),
    date_to: Optional[date] = Query(default=None),
    as_of: Optional[AwareDatetime] = Query(default=None),
    currencies: Optional[str] = Query(default=None, max_length=200),
    min_impact: Optional[Literal["low", "medium", "high"]] = Query(default=None),
    service: CatalystService = Depends(_service),
) -> dict:
    start_date = date_from or _now().date()
    end_date = date_to or (start_date + timedelta(days=14))
    if end_date < start_date or (end_date - start_date).days > 92:
        raise HTTPException(status_code=422, detail="invalid calendar date range")
    currency_values = (
        [value.strip().upper() for value in currencies.split(",") if value.strip()]
        if currencies
        else None
    )
    try:
        return service.calendar(
            date_from=start_date,
            date_to=end_date,
            as_of=as_of or _now(),
            currencies=currency_values,
            min_impact=min_impact,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get("/hotspots/status")
def catalyst_hotspot_status(
    request: Request,
    service: CatalystService = Depends(_service),
) -> dict:
    payload = service.hotspot_status()
    return (
        _public_hotspot_status(payload)
        if _anonymous_public_read(request)
        else payload
    )


@router.get("/hotspots")
def catalyst_hotspots(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    service: CatalystService = Depends(_service),
) -> dict:
    payload = service.hotspots(limit=limit)
    return _public_hotspots(payload) if _anonymous_public_read(request) else payload


@router.get("/market-focus-cycles/latest")
def latest_market_focus_cycle(
    request: Request,
    service: CatalystService = Depends(_service),
) -> dict:
    payload = service.latest_market_focus_cycle()
    return (
        _public_market_focus_envelope(payload)
        if _anonymous_public_read(request)
        else payload
    )


@router.post(
    "/market-focus-cycles",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def request_market_focus_cycle(
    request: MarketFocusCycleRequest,
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        return service.request_market_focus_cycle(
            expected_prepared_revision=request.expected_prepared_revision,
            retry_cycle_id=request.retry_cycle_id,
            force=request.force,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get("/market-focus-cycles/{cycle_id}")
def market_focus_cycle(
    request: Request,
    cycle_id: Annotated[
        str, Path(pattern=r"^mfc_[0-9a-f]{32}$")
    ],
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        cycle = service.market_focus_cycle(cycle_id)
    except CatalystError as error:
        _raise_safe(error)
    if cycle is None:
        raise HTTPException(status_code=404, detail="market focus cycle not found")
    return (
        _public_market_focus_cycle(cycle)
        if _anonymous_public_read(request)
        else cycle
    )


@router.post(
    "/market-focus-cycles/{cycle_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def cancel_market_focus_cycle(
    cycle_id: Annotated[
        str, Path(pattern=r"^mfc_[0-9a-f]{32}$")
    ],
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        cycle = service.cancel_market_focus_cycle(cycle_id)
    except CatalystError as error:
        _raise_safe(error)
    if cycle is None:
        raise HTTPException(status_code=404, detail="market focus cycle not found")
    return cycle


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def refresh_catalysts(
    request: Optional[RefreshRequest] = None,
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        payload = request or RefreshRequest()
        return service.request_refresh(
            payload.operation_type,
            idempotency_key=payload.idempotency_key,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get("/refresh/{request_id}")
def catalyst_refresh_status(
    request_id: Annotated[
        str,
        Path(pattern=r"^refresh_[0-9a-f]{32}$"),
    ],
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        operation = service.manual_operation(request_id)
    except CatalystError as error:
        _raise_safe(error)
    if operation is None:
        raise HTTPException(status_code=404, detail="refresh request not found")
    return operation


@router.post(
    "/news/{news_id}/analysis",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def request_news_analysis(
    request: AnalysisRequest,
    news_id: int = Path(ge=1),
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        return service.request_analysis(news_id, force=request.force)
    except CatalystError as error:
        _raise_safe(error)


@router.get("/analysis-jobs/{job_id}")
def analysis_job(
    job_id: Annotated[str, Path(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        job = service.analysis_job(job_id)
    except CatalystError as error:
        _raise_safe(error)
    if job is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    return job


@router.post(
    "/analysis-jobs/{job_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def cancel_analysis_job(
    job_id: Annotated[str, Path(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
    service: CatalystService = Depends(_service),
) -> dict:
    try:
        job = service.cancel_analysis_job(job_id)
    except CatalystError as error:
        _raise_safe(error)
    if job is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    return job
