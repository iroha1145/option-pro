"""Same-origin, local-cache-only Catalyst API."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from app.services.catalysts.config import CatalystSettings, get_catalyst_settings
from app.services.catalysts.errors import CatalystError, InvalidCursorError
from app.services.catalysts.models import TICKER_PATTERN
from app.services.catalysts.service import CatalystService
from app.services.ai_jobs.security import require_expensive_action


router = APIRouter(prefix="/api/catalysts", tags=["catalysts"])


class _RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BatchRequest(_RequestModel):
    tickers: list[str] = Field(min_length=1, max_length=50)
    as_of: Optional[AwareDatetime] = None
    window_hours: int = Field(default=72, ge=1, le=24 * 365)
    limit: int = Field(default=20, ge=1, le=100)
    min_confidence: Optional[int] = Field(default=None, ge=0, le=100)
    include_neutral: bool = False

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


def _service(
    settings: CatalystSettings = Depends(get_catalyst_settings),
) -> CatalystService:
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
def catalyst_status(service: CatalystService = Depends(_service)) -> dict:
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
    min_confidence: Optional[int] = Query(default=None, ge=0, le=100),
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
    service: CatalystService = Depends(_service),
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
    min_confidence: Optional[int] = Query(default=None, ge=0, le=100),
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


@router.post(
    "/refresh",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_expensive_action)],
)
def refresh_catalysts(service: CatalystService = Depends(_service)) -> dict:
    try:
        return service.request_refresh()
    except CatalystError as error:
        _raise_safe(error)


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
