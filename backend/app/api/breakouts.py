"""Read-only, versioned Breakout Radar API."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.models import normalize_ticker
from app.services.breakouts.repository import (
    BreakoutRepository,
    BreakoutRepositoryError,
    InvalidCursorError,
    SchemaVersionError,
)


router = APIRouter(prefix="/api/breakouts", tags=["breakouts"])


class _ResponseModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PriceZoneResponse(_ResponseModel):
    low: float
    high: float
    strength: Optional[float] = None
    touches: Optional[int] = None
    age_days: Optional[int] = None


class BreakoutEventResponse(_ResponseModel):
    event_id: str
    ticker: str
    name: Optional[str] = None
    exchange: Optional[str] = None
    asset_type: str
    sector: Optional[str] = None
    session: str
    setup_type: str
    lifecycle_state: str
    event_at: AwareDatetime
    event_age_seconds: float = Field(ge=0)
    event_price: Optional[float] = None
    current_price: Optional[float] = None
    session_change_pct: Optional[float] = None
    gap_pct: Optional[float] = None
    rvol_time_of_day: Optional[float] = None
    pivot_price: Optional[float] = None
    support_zone: Optional[PriceZoneResponse] = None
    resistance_zone: Optional[PriceZoneResponse] = None
    invalidation_price: Optional[float] = None
    intrinsic_strength_score: Optional[float] = Field(default=None, ge=0, le=100)
    base_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    breakout_confirmation_score: Optional[float] = Field(default=None, ge=0, le=100)
    liquidity_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    chase_risk_score: Optional[float] = Field(default=None, ge=0, le=100)
    sector_fit_score: Optional[float] = Field(default=None, ge=0, le=100)
    market_fit_score: Optional[float] = Field(default=None, ge=0, le=100)
    breakout_quality_score: Optional[float] = Field(default=None, ge=0, le=100)
    alert_priority_score: Optional[float] = Field(default=None, ge=0, le=100)
    data_confidence_score: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence_slope_5d: Optional[float] = None
    range_persistence_ratio_10d: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence_status: str
    effective_weights: dict[str, float]
    contribution_breakdown: dict[str, float]
    market_shape: dict[str, Any]
    warnings: list[str]
    source_status: dict[str, Any]
    provenance: dict[str, Any]
    versions: dict[str, str]


class BreakoutRootResponse(_ResponseModel):
    as_of: AwareDatetime
    market_timezone: str = "America/New_York"
    session: Optional[str] = None
    status: str
    versions: dict[str, str]
    source_status: dict[str, Any]
    events: list[BreakoutEventResponse]
    scan_run_id: Optional[str] = None


class BreakoutEventPageResponse(BreakoutRootResponse):
    next_cursor: Optional[str] = None


class BreakoutEventDetailResponse(_ResponseModel):
    as_of: AwareDatetime
    status: str
    versions: dict[str, str]
    event: BreakoutEventResponse
    structure: Optional[dict[str, Any]] = None
    scores: dict[str, Any]
    transitions: list[dict[str, Any]]
    source_status: dict[str, Any]


class BreakoutTickerResponse(_ResponseModel):
    as_of: AwareDatetime
    status: str
    versions: dict[str, str]
    ticker: str
    events: list[BreakoutEventResponse]
    current_state: Optional[str] = None


class BreakoutStatusResponse(_ResponseModel):
    as_of: AwareDatetime
    status: str
    enabled: bool
    range_persistence_mode: str
    versions: dict[str, str]
    database: dict[str, Any]
    worker: Optional[dict[str, Any]] = None
    latest_completed_scan: Optional[dict[str, Any]] = None
    provider_health: list[dict[str, Any]] = Field(default_factory=list)
    worker_lock: Optional[dict[str, Any]] = None
    strength_adapter: dict[str, Any]
    market_shape_adapter: dict[str, Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _versions(settings: BreakoutSettings, stored: Any = None) -> dict[str, str]:
    versions = {
        "api_schema": settings.api_schema_version,
        "provider_schema": settings.provider_schema_version,
        "feature_version": settings.feature_version,
        "detector_version": settings.detector_version,
        "scoring_version": settings.scoring_version,
        "range_persistence_version": settings.range_persistence_version,
        "market_shape_version": "market-shape-adapter-v1",
        "strength_score_version": "strength-intrinsic-v1",
        "universe_version": "canonical-universe-v1",
        "database_version": "breakout-db-v1",
    }
    if isinstance(stored, dict):
        for key, value in stored.items():
            if value is not None:
                versions[str(key)] = str(value)
    return versions


def _finite(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _event_time(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("breakout event time must include a timezone")
    return parsed


def _public_event(
    settings: BreakoutSettings,
    stored: dict[str, Any],
    *,
    default_session: Optional[str] = None,
    observed_at: Optional[datetime] = None,
) -> BreakoutEventResponse:
    features = dict(stored.get("features") or {})
    structure = dict(stored.get("structure") or {})
    scores = dict(stored.get("scores") or {})
    details = dict(scores.get("details") or {})
    priority = dict(details.get("alert_priority") or {})
    quality = dict(stored.get("data_quality") or {})
    event_at = _event_time(stored.get("event_at"))
    now = observed_at or _now()
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=timezone.utc)
    age = max(0.0, (now.astimezone(timezone.utc) - event_at.astimezone(timezone.utc)).total_seconds())
    return BreakoutEventResponse(
        event_id=str(stored.get("event_id") or ""),
        ticker=normalize_ticker(stored.get("ticker")),
        name=stored.get("name"),
        exchange=stored.get("exchange"),
        asset_type=str(stored.get("asset_type") or "unknown"),
        sector=stored.get("sector"),
        session=str(stored.get("session") or features.get("session") or default_session or "closed"),
        setup_type=str(stored.get("setup_type") or "MOMENTUM_SPIKE"),
        lifecycle_state=str(stored.get("lifecycle_state") or "DISCOVERED"),
        event_at=event_at,
        event_age_seconds=age,
        event_price=_finite(stored.get("event_price")),
        current_price=_finite(features.get("current_price", stored.get("event_price"))),
        session_change_pct=_finite(features.get("session_change_pct")),
        gap_pct=_finite(features.get("gap_pct")),
        rvol_time_of_day=_finite(features.get("rvol_time_of_day")),
        pivot_price=_finite(structure.get("pivot_price")),
        support_zone=structure.get("support_zone"),
        resistance_zone=structure.get("resistance_zone"),
        invalidation_price=_finite(structure.get("invalidation_price")),
        intrinsic_strength_score=_finite(scores.get("intrinsic_strength_score")),
        base_quality_score=_finite(scores.get("base_quality_score")),
        breakout_confirmation_score=_finite(scores.get("breakout_confirmation_score")),
        liquidity_quality_score=_finite(scores.get("liquidity_quality_score")),
        chase_risk_score=_finite(scores.get("chase_risk_score")),
        sector_fit_score=_finite(scores.get("sector_fit_score")),
        market_fit_score=_finite(scores.get("market_fit_score")),
        breakout_quality_score=_finite(scores.get("breakout_quality_score")),
        alert_priority_score=_finite(scores.get("alert_priority_score")),
        data_confidence_score=_finite(scores.get("data_confidence_score")),
        range_persistence=_finite(features.get("range_persistence")),
        range_persistence_slope_5d=_finite(features.get("range_persistence_slope_5d")),
        range_persistence_ratio_10d=_finite(features.get("range_persistence_ratio_10d")),
        range_persistence_status=str(features.get("range_persistence_status") or "unavailable"),
        effective_weights={
            str(key): float(value)
            for key, value in dict(priority.get("effective_weights") or {}).items()
            if _finite(value) is not None
        },
        contribution_breakdown={
            str(key): float(value)
            for key, value in dict(priority.get("contribution_breakdown") or {}).items()
            if _finite(value) is not None
        },
        market_shape={
            "status": quality.get("market_shape_status", "unavailable"),
            "state": quality.get("market_shape_state"),
            "version": quality.get("market_shape_version", "market-shape-adapter-v1"),
        },
        warnings=[str(item) for item in list(stored.get("warnings") or [])],
        source_status={
            "discovery": quality.get("discovery_source", "unavailable"),
            "daily_prices": quality.get("daily_price_source", "unavailable"),
            "intraday_prices": quality.get("intraday_price_source", "unavailable"),
            "strength": quality.get("strength_status", "unavailable"),
            "market_shape": quality.get("market_shape_status", "unavailable"),
        },
        provenance={
            "source_snapshot_id": stored.get("source_snapshot_id"),
            "pivot_id": stored.get("pivot_id"),
            "feature_cutoff_at": features.get("feature_cutoff_at"),
            "calculation_cutoff_at": features.get("calculation_cutoff_at"),
        },
        versions=_versions(settings, stored.get("versions")),
    )


def _repository(settings: BreakoutSettings) -> BreakoutRepository:
    return BreakoutRepository(settings.db_path, read_only=True)


def _unavailable_root(
    settings: BreakoutSettings,
    *,
    status: str,
    warning: str,
) -> BreakoutRootResponse:
    return BreakoutRootResponse(
        as_of=_now(),
        session=None,
        status=status,
        versions=_versions(settings),
        source_status={"database": status, "warnings": [warning]},
        events=[],
        scan_run_id=None,
    )


def _root_from_scan(
    settings: BreakoutSettings,
    scan: dict[str, Any],
) -> BreakoutRootResponse:
    provider = scan.get("provider_snapshot") or {}
    provider_status = str(provider.get("status") or "active")
    status = (
        "stale"
        if provider_status == "stale"
        else "degraded"
        if provider_status in {"degraded", "unavailable"}
        else "active"
    )
    as_of = scan.get("published_at") or scan.get("completed_at") or scan.get("scheduled_at")
    response_time = _event_time(as_of)
    scan_session = str(scan.get("session") or provider.get("session") or "") or None
    return BreakoutRootResponse(
        as_of=response_time,
        session=scan_session,
        status=status,
        versions=_versions(settings, scan.get("versions")),
        source_status={
            "database": "active",
            "provider": provider_status,
            "provider_as_of": provider.get("as_of"),
        },
        events=[
            _public_event(
                settings,
                dict(item),
                default_session=scan_session,
                observed_at=response_time,
            )
            for item in list(scan.get("events") or [])
        ],
        scan_run_id=str(scan.get("scan_run_id") or "") or None,
    )


@router.get("/current", response_model=BreakoutRootResponse)
def current() -> BreakoutRootResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        return _unavailable_root(
            settings,
            status="disabled",
            warning="breakout_radar_disabled",
        )
    try:
        scan = _repository(settings).latest_completed_scan()
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, SchemaVersionError, OSError):
        return _unavailable_root(
            settings,
            status="unavailable",
            warning="breakout_database_unavailable",
        )
    if scan is None:
        return _unavailable_root(
            settings,
            status="unavailable",
            warning="no_completed_scan",
        )
    return _root_from_scan(settings, dict(scan))


@router.get("/events", response_model=BreakoutEventPageResponse)
def events(
    date: Optional[str] = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$"),
    ticker: Optional[str] = Query(default=None, max_length=15),
    setup_type: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
    session: Optional[str] = Query(
        default=None,
        pattern="^(premarket|regular|postmarket|closed)$",
    ),
    min_priority: Optional[float] = Query(default=None, ge=0, le=100),
    limit: int = Query(default=50, ge=1, le=200),
    cursor: Optional[str] = Query(default=None, max_length=2048),
) -> BreakoutEventPageResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        root = _unavailable_root(settings, status="disabled", warning="breakout_radar_disabled")
        return BreakoutEventPageResponse(**root.model_dump(), next_cursor=None)
    normalized = None
    if ticker is not None:
        try:
            normalized = normalize_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid ticker") from exc
    try:
        page = _repository(settings).list_events(
            date=date,
            ticker=normalized,
            setup_type=setup_type,
            lifecycle_state=lifecycle_state,
            session=session,
            min_priority=min_priority,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired cursor") from exc
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, SchemaVersionError, OSError):
        root = _unavailable_root(
            settings,
            status="unavailable",
            warning="breakout_database_unavailable",
        )
        return BreakoutEventPageResponse(**root.model_dump(), next_cursor=None)
    return BreakoutEventPageResponse(
        as_of=_now(),
        session=session,
        status="active" if page.get("scan_run_id") else "unavailable",
        versions=_versions(settings),
        source_status={"database": "active"},
        events=[
            _public_event(settings, dict(item), default_session=session)
            for item in list(page.get("events") or [])
        ],
        scan_run_id=page.get("scan_run_id"),
        next_cursor=page.get("next_cursor"),
    )


@router.get("/events/{event_id}", response_model=BreakoutEventDetailResponse)
def event_detail(event_id: str) -> BreakoutEventDetailResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        raise HTTPException(status_code=404, detail="Breakout Radar is disabled")
    try:
        event = _repository(settings).get_event(event_id)
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, SchemaVersionError, OSError):
        raise HTTPException(status_code=503, detail="Breakout database is unavailable")
    if event is None:
        raise HTTPException(status_code=404, detail="Breakout event not found")
    public_event = _public_event(settings, dict(event))
    return BreakoutEventDetailResponse(
        as_of=_now(),
        status="active",
        versions=_versions(settings, event.get("versions")),
        event=public_event,
        structure=event.get("structure"),
        scores=dict(event.get("scores") or {}),
        transitions=list(event.get("transitions") or []),
        source_status=public_event.source_status,
    )


@router.get("/tickers/{ticker}", response_model=BreakoutTickerResponse)
def ticker_events(ticker: str) -> BreakoutTickerResponse:
    settings = get_breakout_settings()
    try:
        symbol = normalize_ticker(ticker)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid ticker") from exc
    if not settings.enabled:
        return BreakoutTickerResponse(
            as_of=_now(),
            status="disabled",
            versions=_versions(settings),
            ticker=symbol,
            events=[],
            current_state=None,
        )
    try:
        items = list(_repository(settings).events_for_ticker(symbol))
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, SchemaVersionError, OSError):
        return BreakoutTickerResponse(
            as_of=_now(),
            status="unavailable",
            versions=_versions(settings),
            ticker=symbol,
            events=[],
            current_state=None,
        )
    return BreakoutTickerResponse(
        as_of=_now(),
        status="active" if items else "unavailable",
        versions=_versions(settings),
        ticker=symbol,
        events=[_public_event(settings, dict(item)) for item in items],
        current_state=str(items[0].get("lifecycle_state")) if items else None,
    )


@router.get("/status", response_model=BreakoutStatusResponse)
def status() -> BreakoutStatusResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        return BreakoutStatusResponse(
            as_of=_now(),
            status="disabled",
            enabled=False,
            range_persistence_mode=settings.range_persistence_mode,
            versions=_versions(settings),
            database={"status": "not_opened"},
            strength_adapter={"status": "available", "version": "strength-intrinsic-v1"},
            market_shape_adapter={
                "status": "unavailable",
                "version": "market-shape-adapter-v1",
                "warning": "six-state market shape is not implemented",
            },
        )
    try:
        payload = dict(_repository(settings).status())
        provider_states = {
            str(item.get("status"))
            for item in payload.get("provider_health", [])
            if isinstance(item, dict)
        }
        overall = (
            "stale"
            if "stale" in provider_states
            else "degraded"
            if provider_states.intersection({"degraded", "unavailable"})
            else "active"
        )
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, SchemaVersionError, OSError):
        payload = {
            "database": {"status": "unavailable"},
            "worker": None,
            "latest_completed_scan": None,
            "provider_health": [],
            "worker_lock": None,
        }
        overall = "unavailable"
    database = dict(payload.get("database") or {})
    database.pop("path", None)
    database.pop("schema_checksum", None)
    worker = dict(payload.get("worker") or {}) or None
    if worker is not None:
        worker.pop("worker_id", None)
    lock = dict(payload.get("worker_lock") or {}) or None
    if lock is not None:
        lock = {
            "status": "held" if lock.get("owner_id") else "released",
            "heartbeat_at": lock.get("heartbeat_at"),
            "expires_at": lock.get("expires_at"),
        }
    return BreakoutStatusResponse(
        as_of=_now(),
        status=overall,
        enabled=True,
        range_persistence_mode=settings.range_persistence_mode,
        versions=_versions(
            settings,
            (payload.get("latest_completed_scan") or {}).get("versions"),
        ),
        database=database,
        worker=worker,
        latest_completed_scan=payload.get("latest_completed_scan"),
        provider_health=list(payload.get("provider_health") or []),
        worker_lock=lock,
        strength_adapter={"status": "available", "version": "strength-intrinsic-v1"},
        market_shape_adapter={
            "status": "unavailable",
            "version": "market-shape-adapter-v1",
            "warning": "six-state market shape is not implemented",
        },
    )
