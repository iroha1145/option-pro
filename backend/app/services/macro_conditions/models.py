"""Immutable models for Optix Macro Conditions.

Scores here are rolling historical percentiles of public macro and cross-asset
data. They are not forecasts, not probabilities of a market move, and never an
instruction to buy or sell. Nothing in this package writes into the official
stock ranking.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Mapping, Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

#: Overall readiness of the published macro snapshot.
MacroStatus = Literal[
    "disabled",
    "active",
    "degraded",
    "stale",
    "unavailable",
    "insufficient_history",
]

#: Where a value's history came from. ``latest_revised_backfill`` is what today's
#: FRED revision says about the past — it is *not* what the market could see at
#: the time. ``local_point_in_time`` rows were first observed locally after this
#: feature shipped and carry real point-in-time meaning.
HistoryBasis = Literal[
    "latest_revised_backfill",
    "local_point_in_time",
    "mixed",
]

#: Per-factor freshness/computability outcome.
FactorStatus = Literal["ok", "stale", "missing", "insufficient_history"]

ScoreMethod = Literal[
    "supportive_high_percentile",
    "supportive_low_percentile",
    "target_distance",
    "direct_score",
]

ModuleId = Literal[
    "liquidity",
    "funding",
    "treasury",
    "rates",
    "credit",
    "risk",
    "external",
]

SyncStatus = Literal["running", "succeeded", "degraded", "failed"]

SyncTrigger = Literal["scheduled", "manual", "initial_backfill"]


#: Machine-readable failure codes. Distinct causes stay distinct: an upstream
#: FRED problem must never be reported as a database problem.
ERROR_CODES = (
    "fred_api_key_missing",
    "fred_api_key_invalid",
    "fred_rate_limited",
    "fred_unavailable",
    "fred_schema_mismatch",
    "fred_units_mismatch",
    "fred_response_too_large",
    "etf_history_unavailable",
    "macro_insufficient_history",
    "macro_insufficient_modules",
    "macro_refresh_in_progress",
    "macro_refresh_cooldown",
    "macro_store_unavailable",
    "macro_snapshot_unavailable",
)


class MacroError(RuntimeError):
    """Carries one of :data:`ERROR_CODES` and never an upstream response body."""

    def __init__(self, code: str, message: str = "") -> None:
        if code not in ERROR_CODES:
            raise ValueError("unsupported macro error code")
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------


def finite(value: Any) -> Optional[float]:
    """Return a finite float or ``None``.

    NaN and infinity are rejected outright: they must never reach the database,
    the API, or a percentile window.
    """

    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def require_finite(value: Any, *, field_name: str) -> float:
    number = finite(value)
    if number is None:
        raise ValueError(f"{field_name} must be a finite number")
    return number


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeriesObservation:
    """One FRED observation already converted to its canonical unit."""

    series_id: str
    observation_date: date
    value: Optional[float]

    def __post_init__(self) -> None:
        if self.value is not None and finite(self.value) is None:
            raise ValueError("series observation value must be finite or None")


@dataclass(frozen=True, slots=True)
class SeriesMetadata:
    """FRED series metadata as returned, plus the resolved canonical scale."""

    series_id: str
    units: str
    frequency_short: str
    canonical_unit: str
    scale_to_canonical: float
    source_last_updated: Optional[str]
    realtime_start: Optional[str]
    realtime_end: Optional[str]


@dataclass(frozen=True, slots=True)
class SeriesFetch:
    """A complete, validated fetch for one series."""

    metadata: SeriesMetadata
    observations: tuple[SeriesObservation, ...]


@dataclass(frozen=True, slots=True)
class EtfObservation:
    """One completed daily bar, adjusted close only."""

    symbol: str
    observation_date: date
    adjusted_close: float
    provider: str

    def __post_init__(self) -> None:
        require_finite(self.adjusted_close, field_name="adjusted_close")


# ---------------------------------------------------------------------------
# Aligned inputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AlignedValue:
    """One as-of-joined input on the daily score grid."""

    value: float
    observation_date: date
    available_at: str
    history_basis: str
    source_age_days: int


@dataclass(frozen=True, slots=True)
class FactorPoint:
    """One factor's raw value on one grid date, before percentile scoring."""

    factor_id: str
    snapshot_date: date
    #: Value shown to the reader.
    raw_value: Optional[float]
    #: Value fed to the scoring method. Equals ``raw_value`` unless the factor
    #: scores a transformed quantity (an absolute spread, a distance to target).
    score_value: Optional[float]
    #: Signed spread kept for display where the score uses ``abs()``.
    signed_value: Optional[float] = None
    status: FactorStatus = "missing"
    data_through: Optional[date] = None
    available_at: Optional[str] = None
    history_basis: Optional[str] = None
    source_age_days: Optional[int] = None
    missing_inputs: tuple[str, ...] = ()
    stale_inputs: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FactorSnapshot:
    snapshot_date: date
    as_of: str
    factor_id: str
    module_id: str
    raw_value: Optional[float]
    raw_unit: str
    score: Optional[float]
    score_method: str
    score_change_7d: Optional[float]
    raw_change_7d: Optional[float]
    confidence: Optional[float]
    valid_observations: int
    history_basis: Optional[str]
    data_through: Optional[str]
    available_at: Optional[str]
    status: str
    scoring_version: str
    signed_value: Optional[float] = None
    missing_inputs: tuple[str, ...] = ()
    stale_inputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ModuleSnapshot:
    snapshot_date: date
    as_of: str
    module_id: str
    score: Optional[float]
    score_change_7d: Optional[float]
    confidence: Optional[float]
    valid_factor_count: int
    total_factor_count: int
    data_through: Optional[str]
    available_at: Optional[str]
    status: str
    scoring_version: str


@dataclass(frozen=True, slots=True)
class CompositeSnapshot:
    snapshot_date: date
    as_of: str
    score: Optional[float]
    score_change_7d: Optional[float]
    confidence: Optional[float]
    regime: Optional[str]
    valid_module_count: int
    data_through: Optional[str]
    available_at: Optional[str]
    history_basis: Optional[str]
    status: str
    scoring_version: str


@dataclass(frozen=True, slots=True)
class SnapshotBundle:
    """One atomically publishable candidate snapshot set."""

    as_of: str
    scoring_version: str
    factors: tuple[FactorSnapshot, ...]
    modules: tuple[ModuleSnapshot, ...]
    composites: tuple[CompositeSnapshot, ...]
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SyncRun:
    run_id: str
    status: str
    trigger: str
    started_at: str
    completed_at: Optional[str] = None
    data_through: Optional[str] = None
    series_succeeded: int = 0
    series_failed: int = 0
    error_codes: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------


def iso_instant(value: datetime) -> str:
    """Render one UTC instant as ``…SSZ`` so string order equals time order."""

    from datetime import timezone

    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def iso_date(value: date) -> str:
    return value.isoformat()


__all__ = [
    "ERROR_CODES",
    "AlignedValue",
    "CompositeSnapshot",
    "EtfObservation",
    "FactorPoint",
    "FactorSnapshot",
    "FactorStatus",
    "HistoryBasis",
    "MacroError",
    "MacroStatus",
    "ModuleId",
    "ModuleSnapshot",
    "ScoreMethod",
    "SeriesFetch",
    "SeriesMetadata",
    "SeriesObservation",
    "SnapshotBundle",
    "SyncRun",
    "SyncStatus",
    "SyncTrigger",
    "finite",
    "iso_date",
    "iso_instant",
    "require_finite",
]
