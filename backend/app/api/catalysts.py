"""Same-origin, local-cache-only Catalyst API."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

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

from app.access import (
    current_request_is_owner,
    require_owner_access,
    require_same_origin_action,
    require_same_origin_json,
)
from app.services.catalysts.config import CatalystSettings, get_catalyst_settings
from app.services.catalysts.errors import CatalystError, InvalidCursorError
from app.services.catalysts.personal_service import PersonalCatalystService


_MAX_CATALYST_BODY_BYTES = 32 * 1024
_BODY_METHODS = frozenset({"POST", "PUT", "PATCH"})
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,19}$")


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

_PUBLIC_MAX_WINDOW_HOURS = 7 * 24
_PUBLIC_MAX_FEED_LIMIT = 50
_PUBLIC_MAX_BATCH_TICKERS = 20
_PUBLIC_MAX_BATCH_LIMIT = 5


def _require_public_query_bound(
    name: str,
    value: int,
    maximum: int,
) -> None:
    if not current_request_is_owner() and value > maximum:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "public_query_too_large",
                "field": name,
                "maximum": maximum,
            },
        )


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
            if not _TICKER_PATTERN.fullmatch(ticker):
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


def _service(
    settings: CatalystSettings = Depends(get_catalyst_settings),
) -> PersonalCatalystService:
    return PersonalCatalystService(settings)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ticker(value: str) -> str:
    normalized = value.strip().upper()
    if not _TICKER_PATTERN.fullmatch(normalized):
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
        "read_only_mode": 409,
        "manual_analysis_disabled": 409,
        "manual_refresh_disabled": 409,
        "force_reanalysis_disabled": 409,
        "catalyst_disabled": 409,
        "catalyst_sync_disabled": 409,
        "ai_not_configured": 503,
        "worker_unavailable": 503,
        "runtime_settings_unavailable": 503,
        "cache_unavailable": 503,
        "analysis_unavailable": 503,
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
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    return service.status()


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
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    _require_public_query_bound(
        "window_hours",
        window_hours,
        _PUBLIC_MAX_WINDOW_HOURS,
    )
    _require_public_query_bound("limit", limit, _PUBLIC_MAX_FEED_LIMIT)
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
    news_id: int = Path(ge=1),
    as_of: Optional[AwareDatetime] = Query(default=None),
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    observed = as_of or _now()
    try:
        detail = service.news(news_id, as_of=observed)
    except CatalystError as error:
        _raise_safe(error)
    if detail is None:
        raise HTTPException(status_code=404, detail="catalyst news item not found")
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
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    _require_public_query_bound(
        "window_hours",
        window_hours,
        _PUBLIC_MAX_WINDOW_HOURS,
    )
    _require_public_query_bound("limit", limit, _PUBLIC_MAX_FEED_LIMIT)
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


@router.post(
    "/tickers/batch",
    dependencies=[Depends(require_same_origin_json)],
)
def ticker_catalyst_batch(
    request: BatchRequest,
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    _require_public_query_bound(
        "tickers",
        len(request.tickers),
        _PUBLIC_MAX_BATCH_TICKERS,
    )
    _require_public_query_bound(
        "window_hours",
        request.window_hours,
        _PUBLIC_MAX_WINDOW_HOURS,
    )
    _require_public_query_bound(
        "limit",
        request.limit,
        _PUBLIC_MAX_BATCH_LIMIT,
    )
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
    service: PersonalCatalystService = Depends(_service),
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
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    return service.hotspot_status()


@router.get("/hotspots")
def catalyst_hotspots(
    limit: int = Query(default=20, ge=1, le=100),
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    return service.hotspots(limit=limit)


@router.get("/market-focus-cycles/latest")
def latest_market_focus_cycle(
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    return service.latest_market_focus_cycle()


@router.post(
    "/market-focus-cycles",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_same_origin_action)],
)
def request_market_focus_cycle(
    request: MarketFocusCycleRequest,
    service: PersonalCatalystService = Depends(_service),
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
    cycle_id: Annotated[
        str, Path(pattern=r"^mfc_[0-9a-f]{32}$")
    ],
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    try:
        cycle = service.market_focus_cycle(cycle_id)
    except CatalystError as error:
        _raise_safe(error)
    if cycle is None:
        raise HTTPException(status_code=404, detail="market focus cycle not found")
    return cycle


@router.post(
    "/market-focus-cycles/{cycle_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_same_origin_action)],
)
def cancel_market_focus_cycle(
    cycle_id: Annotated[
        str, Path(pattern=r"^mfc_[0-9a-f]{32}$")
    ],
    service: PersonalCatalystService = Depends(_service),
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
    dependencies=[Depends(require_same_origin_action)],
)
def refresh_catalysts(
    request: Optional[RefreshRequest] = None,
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    try:
        payload = request or RefreshRequest()
        return service.request_refresh(
            payload.operation_type,
            idempotency_key=payload.idempotency_key,
        )
    except CatalystError as error:
        _raise_safe(error)


@router.get(
    "/refresh/{request_id}",
    dependencies=[Depends(require_owner_access)],
)
def catalyst_refresh_status(
    request_id: Annotated[
        str,
        Path(pattern=r"^refresh_[0-9a-f]{32}$"),
    ],
    service: PersonalCatalystService = Depends(_service),
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
    dependencies=[Depends(require_same_origin_action)],
)
def request_news_analysis(
    request: AnalysisRequest,
    news_id: int = Path(ge=1),
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    try:
        return service.request_analysis(news_id, force=request.force)
    except CatalystError as error:
        _raise_safe(error)


@router.get(
    "/analysis-jobs/{job_id}",
    dependencies=[Depends(require_owner_access)],
)
def analysis_job(
    job_id: Annotated[str, Path(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
    service: PersonalCatalystService = Depends(_service),
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
    dependencies=[Depends(require_same_origin_action)],
)
def cancel_analysis_job(
    job_id: Annotated[str, Path(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")],
    service: PersonalCatalystService = Depends(_service),
) -> dict:
    try:
        job = service.cancel_analysis_job(job_id)
    except CatalystError as error:
        _raise_safe(error)
    if job is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    return job
