"""Read-only, versioned Breakout Radar API."""

from __future__ import annotations

import sqlite3
import logging
from datetime import date as CalendarDate
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.health import BreakoutReadState, assess_breakout_read_state
from app.services.breakouts.models import (
    BreakoutLifecycleState,
    BreakoutSetupType,
    normalize_ticker,
)
from app.services.breakouts.repository import (
    BreakoutRepository,
    BreakoutRepositoryError,
    InvalidCursorError,
    SCHEMA_VERSION,
    SchemaVersionError,
)
from app.services.strength.market_shape import MARKET_SHAPE_VERSION
from app.services.strength.scoring import SCORE_VERSION as STRENGTH_SCORE_VERSION
from app.public_stock_data import register_public_stock_demand


router = APIRouter(prefix="/api/breakouts", tags=["breakouts"])
logger = logging.getLogger(__name__)


def _register_displayed_stocks(events: list[Any]) -> None:
    """Only validated rows actually returned to a visitor may add demand."""
    try:
        register_public_stock_demand(event.ticker for event in events)
    except (OSError, ValueError):
        logger.warning("Could not record displayed breakout stock demand")


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
    first_seen_at: AwareDatetime
    triggered_at: Optional[AwareDatetime] = None
    state_changed_at: AwareDatetime
    last_seen_at: AwareDatetime
    event_age_seconds: float = Field(ge=0)
    state_age_seconds: float = Field(ge=0)
    observation_age_seconds: float = Field(ge=0)
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
    range_persistence_self_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence_global_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence_sector_percentile: Optional[float] = Field(default=None, ge=0, le=100)
    range_persistence_status: str
    range_persistence_interaction: dict[str, Any]
    configured_weights: dict[str, float]
    effective_weights: dict[str, float]
    contribution_breakdown: dict[str, float]
    penalties: dict[str, float]
    missing_components: list[str]
    score_version: str
    market_shape: dict[str, Any]
    # Shadow only. macro_fit_score is the stock's macro fit; the adjustment is
    # what it *would* move alert_priority_score by, capped at +/-4, and
    # alert_priority_macro_shadow is that arithmetic applied. alert_priority_score
    # itself, every quality score and the event lifecycle are untouched -- a
    # macro headwind never deletes or downgrades a real breakout event.
    macro_fit_score: Optional[float] = Field(default=None, ge=0, le=100)
    macro_fit_confidence: Optional[float] = Field(default=None, ge=0, le=1)
    macro_tailwind: Optional[str] = None
    macro_priority_adjustment_shadow: Optional[float] = None
    alert_priority_macro_shadow: Optional[float] = Field(default=None, ge=0, le=100)
    macro_supporting_factors: list[dict[str, str]] = Field(default_factory=list)
    macro_opposing_factors: list[dict[str, str]] = Field(default_factory=list)
    macro_shadow_status: str = "unavailable"
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
    runtime_status: Optional[str] = None
    runtime_reason: Optional[str] = None
    market_session: Optional[str] = None
    next_session_at: Optional[AwareDatetime] = None
    failure_domain: Optional[str] = None


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
    runtime_status: Optional[str] = None
    runtime_reason: Optional[str] = None
    market_session: Optional[str] = None
    next_session_at: Optional[AwareDatetime] = None
    failure_domain: Optional[str] = None


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
        "market_shape_version": MARKET_SHAPE_VERSION,
        "strength_score_version": STRENGTH_SCORE_VERSION,
        "universe_version": "canonical-universe-v1",
        "database_version": SCHEMA_VERSION,
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


def _macro_reader() -> Any:
    """One read of the published macro snapshot, shared by every event on the page.

    Reading per event would open the same file fifty times for one request and
    could straddle a publication, so two events in one list would be annotated
    against two different macro environments.

    Returns None when macro is not readable at all. Failure is never fatal: these
    are shadow annotations, and an unreadable macro snapshot is not a reason to
    fail a breakout page.
    """

    try:
        from app.services.macro_conditions.linkage_reader import load_macro_fit_reader
    except Exception:
        return None
    try:
        return load_macro_fit_reader()
    except Exception:
        return None


def _macro_shadow(
    reader: Any,
    ticker: str,
    alert_priority: Optional[float],
) -> dict[str, Any]:
    """Shadow macro fields for one event. Moves no production score.

    ``macro_shadow_status`` is reported rather than inferred from nulls, because
    "no macro snapshot", "this ticker is outside the theme map" and "this
    sector's exposure profile is too thinly observed to score" are three
    different facts and all three would otherwise look like a missing number.
    """

    blank = {
        "macro_fit_score": None,
        "macro_fit_confidence": None,
        "macro_tailwind": None,
        "macro_priority_adjustment_shadow": None,
        "alert_priority_macro_shadow": None,
        "macro_supporting_factors": [],
        "macro_opposing_factors": [],
    }
    if reader is None:
        return {**blank, "macro_shadow_status": "unavailable"}
    if not getattr(reader, "available", False):
        return {**blank, "macro_shadow_status": str(reader.reason or "unavailable")}

    try:
        from app.services.macro_conditions.linkage import (
            factor_driver,
            shadow_alert_priority_adjustment,
        )
        from app.services.sectors import primary_sector_id
    except Exception:
        return {**blank, "macro_shadow_status": "unavailable"}

    sector_id = primary_sector_id(ticker)
    if sector_id is None:
        # Outside the theme map: there is no exposure profile to apply. Falling
        # back to a market-wide fit would give every unclassified ticker the same
        # tilt, which is the failure this whole registry exists to avoid.
        return {**blank, "macro_shadow_status": "sector_unclassified"}

    fit = reader.fit_for(sector_id)
    if fit.score is None:
        return {
            **blank,
            "macro_fit_confidence": round(fit.confidence, 4),
            "macro_shadow_status": "exposure_coverage_low",
        }

    adjustment = shadow_alert_priority_adjustment(fit)
    shadow_priority: Optional[float] = None
    if alert_priority is not None:
        shadow_priority = round(max(0.0, min(100.0, alert_priority + adjustment)), 3)
    return {
        "macro_fit_score": fit.score,
        "macro_fit_confidence": round(fit.confidence, 4),
        "macro_tailwind": fit.tailwind,
        "macro_priority_adjustment_shadow": adjustment,
        "alert_priority_macro_shadow": shadow_priority,
        "macro_supporting_factors": [factor_driver(f) for f in fit.supporting],
        "macro_opposing_factors": [factor_driver(f) for f in fit.opposing],
        "macro_shadow_status": "ok",
    }


def _public_event(
    settings: BreakoutSettings,
    stored: dict[str, Any],
    *,
    default_session: Optional[str] = None,
    observed_at: Optional[datetime] = None,
    macro: Any = None,
) -> BreakoutEventResponse:
    features = dict(stored.get("features") or {})
    structure = dict(stored.get("structure") or {})
    scores = dict(stored.get("scores") or {})
    details = dict(scores.get("details") or {})
    priority = dict(details.get("alert_priority") or {})
    quality = dict(stored.get("data_quality") or {})
    lifecycle_state = str(stored.get("lifecycle_state") or "DISCOVERED")
    first_seen_at = _event_time(
        stored.get("first_seen_at") or stored.get("event_at")
    )
    triggered_supplied = "triggered_at" in stored
    triggered_value = stored.get("triggered_at")
    if not triggered_supplied and lifecycle_state in {
        "TRIGGERED",
        "CONFIRMED",
        "HOLDING",
        "RETESTING",
        "RETEST_HELD",
        "REACCELERATING",
        "EXTENDED",
        "FAILED",
    }:
        triggered_value = stored.get("event_at")
    triggered_at = _event_time(triggered_value) if triggered_value is not None else None
    event_at = triggered_at or first_seen_at
    state_changed_at = _event_time(
        stored.get("state_changed_at") or stored.get("event_at") or first_seen_at
    )
    last_seen_at = _event_time(
        stored.get("last_seen_at") or stored.get("event_at") or event_at
    )
    now = observed_at or _now()
    if now.tzinfo is None or now.utcoffset() is None:
        now = now.replace(tzinfo=timezone.utc)
    now_utc = now.astimezone(timezone.utc)
    age = max(0.0, (now_utc - event_at.astimezone(timezone.utc)).total_seconds())
    state_age = max(
        0.0,
        (now_utc - state_changed_at.astimezone(timezone.utc)).total_seconds(),
    )
    observation_age = max(
        0.0,
        (now_utc - last_seen_at.astimezone(timezone.utc)).total_seconds(),
    )
    ticker = normalize_ticker(stored.get("ticker"))
    return BreakoutEventResponse(
        event_id=str(stored.get("event_id") or ""),
        ticker=ticker,
        name=stored.get("name"),
        exchange=stored.get("exchange"),
        asset_type=str(stored.get("asset_type") or "unknown"),
        sector=stored.get("sector"),
        session=str(stored.get("session") or features.get("session") or default_session or "closed"),
        setup_type=str(stored.get("setup_type") or "MOMENTUM_SPIKE"),
        lifecycle_state=lifecycle_state,
        event_at=event_at,
        first_seen_at=first_seen_at,
        triggered_at=triggered_at,
        state_changed_at=state_changed_at,
        last_seen_at=last_seen_at,
        event_age_seconds=age,
        state_age_seconds=state_age,
        observation_age_seconds=observation_age,
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
        range_persistence_self_percentile=_finite(
            features.get("range_persistence_self_percentile")
        ),
        range_persistence_global_percentile=_finite(
            features.get("range_persistence_global_percentile")
        ),
        range_persistence_sector_percentile=_finite(
            features.get("range_persistence_sector_percentile")
        ),
        range_persistence_status=str(features.get("range_persistence_status") or "unavailable"),
        range_persistence_interaction=dict(
            features.get("range_persistence_interaction") or {}
        ),
        configured_weights={
            str(key): float(value)
            for key, value in dict(priority.get("configured_weights") or {}).items()
            if _finite(value) is not None
        },
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
        penalties={
            str(key): float(value)
            for key, value in dict(priority.get("penalties") or {}).items()
            if _finite(value) is not None
        },
        missing_components=[
            str(item) for item in list(priority.get("missing_components") or [])
        ],
        score_version=str(
            priority.get("score_version")
            or scores.get("score_version")
            or settings.scoring_version
        ),
        market_shape={
            "status": quality.get("market_shape_status", "unavailable"),
            "state": quality.get("market_shape_state"),
            "confidence": _finite(quality.get("market_shape_confidence")),
            "transition_risk": _finite(quality.get("market_transition_risk")),
            "eligibility": quality.get("market_eligibility", "unknown"),
            "rules": dict(quality.get("market_shape_rules") or {}),
            "version": quality.get("market_shape_version", MARKET_SHAPE_VERSION),
        },
        **_macro_shadow(macro, ticker, _finite(scores.get("alert_priority_score"))),
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
    read_state: BreakoutReadState | None = None,
    database_status: str | None = None,
) -> BreakoutRootResponse:
    details = dict(read_state.details) if read_state is not None else {}
    return BreakoutRootResponse(
        as_of=_now(),
        session=None,
        status=status,
        versions=_versions(settings),
        source_status={
            "database": database_status or status,
            "warnings": [warning],
        },
        events=[],
        scan_run_id=None,
        runtime_status=read_state.status if read_state is not None else status,
        runtime_reason=read_state.reason if read_state is not None else warning,
        market_session=details.get("market_session"),
        next_session_at=details.get("next_session_at"),
        failure_domain=details.get("failure_domain"),
    )


def _schema_unavailable_root(
    settings: BreakoutSettings,
    error: SchemaVersionError,
) -> BreakoutRootResponse:
    return BreakoutRootResponse(
        as_of=_now(),
        session=None,
        status="unavailable",
        versions=_versions(settings),
        source_status={
            "database": "schema_upgrade_required",
            "warnings": ["breakout_database_schema_upgrade_required"],
            "schema": error.status_payload(),
        },
        events=[],
        scan_run_id=None,
    )


def _combine_read_status(*values: str) -> str:
    if "paused" in values:
        return "paused"
    rank = {"active": 0, "degraded": 1, "stale": 2, "unavailable": 3}
    normalized = [value if value in rank else "degraded" for value in values]
    return max(normalized, key=lambda value: rank[value]) if normalized else "active"


def _read_state(
    settings: BreakoutSettings,
    repository: BreakoutRepository,
    *,
    completed_snapshot: dict[str, Any] | None,
) -> BreakoutReadState:
    try:
        runtime = repository.status()
    except (
        FileNotFoundError,
        sqlite3.Error,
        BreakoutRepositoryError,
        SchemaVersionError,
        OSError,
        ValueError,
    ):
        # A completed immutable snapshot was already read successfully.  Do not
        # hide it merely because the secondary liveness query failed.
        runtime = {"database": {"status": "active"}}
    return assess_breakout_read_state(
        settings,
        runtime,
        now=_now(),
        completed_snapshot=completed_snapshot,
    )


def _root_from_scan(
    settings: BreakoutSettings,
    scan: dict[str, Any],
    *,
    read_state: BreakoutReadState | None = None,
) -> BreakoutRootResponse:
    provider = scan.get("provider_snapshot") or {}
    provider_status = str(provider.get("status") or "active")
    provider_read_status = (
        "stale"
        if provider_status == "stale"
        else "degraded"
        if provider_status in {"degraded", "unavailable"}
        else "active"
    )
    status = _combine_read_status(
        provider_read_status,
        read_state.status if read_state is not None else "active",
    )
    as_of = scan.get("published_at") or scan.get("completed_at") or scan.get("scheduled_at")
    response_time = _event_time(as_of)
    requested_at = _now()
    scan_session = str(scan.get("session") or provider.get("session") or "") or None
    macro = _macro_reader()
    return BreakoutRootResponse(
        as_of=response_time,
        session=scan_session,
        status=status,
        versions=_versions(settings, scan.get("versions")),
        source_status={
            "database": "active",
            "provider": provider_status,
            "provider_as_of": provider.get("as_of"),
            "runtime": read_state.status if read_state is not None else "active",
            "runtime_reason": read_state.reason if read_state is not None else "fresh",
            "freshness": dict(read_state.details) if read_state is not None else {},
        },
        events=[
            _public_event(
                settings,
                dict(item),
                default_session=scan_session,
                observed_at=requested_at,
                macro=macro,
            )
            for item in list(scan.get("events") or [])
        ],
        scan_run_id=str(scan.get("scan_run_id") or "") or None,
        runtime_status=read_state.status if read_state is not None else "active",
        runtime_reason=read_state.reason if read_state is not None else "fresh",
        market_session=(
            read_state.details.get("market_session")
            if read_state is not None
            else scan_session
        ),
        next_session_at=(
            read_state.details.get("next_session_at")
            if read_state is not None
            else None
        ),
        failure_domain=(
            read_state.details.get("failure_domain")
            if read_state is not None
            else None
        ),
    )


@router.get("/current", response_model=BreakoutRootResponse)
def current() -> BreakoutRootResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        return _unavailable_root(
            settings,
            status="disabled",
            warning="breakout_radar_disabled",
            database_status="not_opened",
        )
    try:
        repository = _repository(settings)
        scan = repository.latest_completed_scan()
    except SchemaVersionError as exc:
        return _schema_unavailable_root(settings, exc)
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, OSError):
        return _unavailable_root(
            settings,
            status="unavailable",
            warning="breakout_database_unavailable",
        )
    if scan is None:
        read_state = _read_state(
            settings,
            repository,
            completed_snapshot=None,
        )
        if read_state.status == "paused":
            return _unavailable_root(
                settings,
                status="paused",
                warning="market_closed",
                read_state=read_state,
                database_status="active",
            )
        return _unavailable_root(
            settings,
            status="unavailable",
            warning="no_completed_scan",
            database_status="active",
        )
    stored_scan = dict(scan)
    result = _root_from_scan(
        settings,
        stored_scan,
        read_state=_read_state(
            settings,
            repository,
            completed_snapshot=stored_scan,
        ),
    )
    _register_displayed_stocks(result.events)
    return result


@router.get("/events", response_model=BreakoutEventPageResponse)
def events(
    date: Optional[CalendarDate] = Query(default=None),
    ticker: Optional[str] = Query(default=None, max_length=15),
    setup_type: Optional[BreakoutSetupType] = None,
    lifecycle_state: Optional[BreakoutLifecycleState] = None,
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
        root = _unavailable_root(
            settings,
            status="disabled",
            warning="breakout_radar_disabled",
            database_status="not_opened",
        )
        return BreakoutEventPageResponse(**root.model_dump(), next_cursor=None)
    normalized = None
    if ticker is not None:
        try:
            normalized = normalize_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid ticker") from exc
    try:
        repository = _repository(settings)
        page = repository.list_events(
            date=date.isoformat() if date is not None else None,
            ticker=normalized,
            setup_type=setup_type.value if setup_type is not None else None,
            lifecycle_state=(
                lifecycle_state.value if lifecycle_state is not None else None
            ),
            session=session,
            min_priority=min_priority,
            limit=limit,
            cursor=cursor,
        )
    except InvalidCursorError as exc:
        raise HTTPException(status_code=400, detail="Invalid or expired cursor") from exc
    except SchemaVersionError as exc:
        root = _schema_unavailable_root(settings, exc)
        return BreakoutEventPageResponse(**root.model_dump(), next_cursor=None)
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, OSError):
        root = _unavailable_root(
            settings,
            status="unavailable",
            warning="breakout_database_unavailable",
        )
        return BreakoutEventPageResponse(**root.model_dump(), next_cursor=None)
    completed_scan = page.get("completed_scan")
    read_state = _read_state(
        settings,
        repository,
        completed_snapshot=(
            dict(completed_scan) if isinstance(completed_scan, dict) else None
        ),
    )
    page_status = (
        read_state.status
        if page.get("scan_run_id") or read_state.status == "paused"
        else "unavailable"
    )
    macro = _macro_reader()
    result = BreakoutEventPageResponse(
        as_of=_now(),
        session=session,
        status=page_status,
        versions=_versions(settings),
        source_status={
            "database": "active",
            "runtime": read_state.status,
            "runtime_reason": read_state.reason,
            "freshness": dict(read_state.details),
        },
        events=[
            _public_event(settings, dict(item), default_session=session, macro=macro)
            for item in list(page.get("events") or [])
        ],
        scan_run_id=page.get("scan_run_id"),
        next_cursor=page.get("next_cursor"),
        runtime_status=read_state.status,
        runtime_reason=read_state.reason,
        market_session=read_state.details.get("market_session"),
        next_session_at=read_state.details.get("next_session_at"),
        failure_domain=read_state.details.get("failure_domain"),
    )
    _register_displayed_stocks(result.events)
    return result


@router.get("/events/{event_id}", response_model=BreakoutEventDetailResponse)
def event_detail(event_id: str) -> BreakoutEventDetailResponse:
    settings = get_breakout_settings()
    if not settings.enabled:
        raise HTTPException(status_code=404, detail="Breakout Radar is disabled")
    try:
        event = _repository(settings).get_event(event_id)
    except SchemaVersionError as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "message": "Breakout database schema upgrade required",
                "schema": exc.status_payload(),
            },
        ) from exc
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, OSError):
        raise HTTPException(status_code=503, detail="Breakout database is unavailable")
    if event is None:
        raise HTTPException(status_code=404, detail="Breakout event not found")
    public_event = _public_event(settings, dict(event), macro=_macro_reader())
    _register_displayed_stocks([public_event])
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
    except SchemaVersionError:
        return BreakoutTickerResponse(
            as_of=_now(),
            status="schema_upgrade_required",
            versions=_versions(settings),
            ticker=symbol,
            events=[],
            current_state=None,
        )
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, OSError):
        return BreakoutTickerResponse(
            as_of=_now(),
            status="unavailable",
            versions=_versions(settings),
            ticker=symbol,
            events=[],
            current_state=None,
        )
    macro = _macro_reader() if items else None
    return BreakoutTickerResponse(
        as_of=_now(),
        status="active" if items else "empty",
        versions=_versions(settings),
        ticker=symbol,
        events=[_public_event(settings, dict(item), macro=macro) for item in items],
        current_state=str(items[0].get("lifecycle_state")) if items else None,
    )


@router.get("/status", response_model=BreakoutStatusResponse)
def status() -> BreakoutStatusResponse:
    settings = get_breakout_settings()
    clock_snapshot = MarketClock().snapshot(_now())
    if not settings.enabled:
        return BreakoutStatusResponse(
            as_of=_now(),
            status="disabled",
            enabled=False,
            range_persistence_mode=settings.range_persistence_mode,
            versions=_versions(settings),
            database={"status": "not_opened"},
            strength_adapter={"status": "available", "version": STRENGTH_SCORE_VERSION},
            market_shape_adapter={
                "status": "available",
                "version": MARKET_SHAPE_VERSION,
                "warning": "runtime data status is reported by completed scan snapshots",
            },
        )
    try:
        repository = _repository(settings)
        payload = dict(repository.status())
        completed = payload.get("latest_completed_scan")
        read_state = assess_breakout_read_state(
            settings,
            payload,
            now=_now(),
            completed_snapshot=(dict(completed) if isinstance(completed, dict) else None),
        )
        overall = read_state.status
    except SchemaVersionError as exc:
        payload = {
            "database": exc.status_payload(),
            "worker": None,
            "latest_completed_scan": None,
            "provider_health": [],
            "worker_lock": None,
        }
        overall = "unavailable"
        read_state = None
    except (FileNotFoundError, sqlite3.Error, BreakoutRepositoryError, OSError):
        payload = {
            "database": {"status": "unavailable"},
            "worker": None,
            "latest_completed_scan": None,
            "provider_health": [],
            "worker_lock": None,
        }
        overall = "unavailable"
        read_state = None
    database = dict(payload.get("database") or {})
    database.pop("path", None)
    database.pop("schema_checksum", None)
    worker = dict(payload.get("worker") or {}) or None
    if worker is not None:
        worker.pop("worker_id", None)
        if read_state is not None:
            worker["health_status"] = read_state.status
            worker["health_reason"] = read_state.reason
            worker["heartbeat_age_seconds"] = read_state.details.get(
                "heartbeat_age_seconds"
            )
    latest_completed_scan = dict(payload.get("latest_completed_scan") or {}) or None
    if latest_completed_scan is not None and read_state is not None:
        latest_completed_scan["freshness_status"] = read_state.status
        latest_completed_scan["snapshot_age_seconds"] = read_state.details.get(
            "snapshot_age_seconds"
        )
        latest_completed_scan["stale_after_seconds"] = read_state.details.get(
            "snapshot_stale_after_seconds"
        )
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
        latest_completed_scan=latest_completed_scan,
        provider_health=list(payload.get("provider_health") or []),
        worker_lock=lock,
        strength_adapter={"status": "available", "version": STRENGTH_SCORE_VERSION},
        market_shape_adapter={
            "status": "available",
            "version": MARKET_SHAPE_VERSION,
            "warning": "runtime data status is reported by completed scan snapshots",
        },
        runtime_status=read_state.status if read_state is not None else overall,
        runtime_reason=read_state.reason if read_state is not None else None,
        # The session chip must reflect the market NOW. Worker-stored details
        # only carry a session while paused and lag scan cadence otherwise,
        # so classify the current instant with the shared market clock.
        # next_session_at keeps its original contract — "when the paused
        # worker resumes" — and stays absent while sessions are running.
        market_session=clock_snapshot.session.value,
        next_session_at=(
            read_state.details.get("next_session_at")
            if read_state is not None
            else None
        ),
        failure_domain=(
            read_state.details.get("failure_domain")
            if read_state is not None
            else None
        ),
    )
