"""Validated Breakout Radar bootstrap configuration."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data_paths import explicit_data_path, get_data_paths
from app.personal_config import get_personal_config
from app.runtime_environment import load_runtime_environment


_ROOT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
load_runtime_environment()
_PERSONAL_CONFIG = get_personal_config()
_PERSONAL_RANGE_PERSISTENCE_MODE = {
    "off": "disabled",
    "shadow": "shadow",
    "active": "enabled",
}[_PERSONAL_CONFIG.breakout.range_persistence_mode]


class BreakoutSettings(BaseSettings):
    _db_path_override: Path | None = PrivateAttr(default=None)

    enabled: bool = Field(
        default=_PERSONAL_CONFIG.features.breakout_enabled,
        alias="BREAKOUT_RADAR_ENABLED",
    )
    discovery_provider: Literal["tradingview"] = Field(
        default="tradingview",
        alias="BREAKOUT_DISCOVERY_PROVIDER",
    )
    provider_timeout_seconds: float = Field(
        default=10.0, ge=1.0, le=30.0, alias="BREAKOUT_PROVIDER_TIMEOUT_SECONDS"
    )
    provider_connect_timeout_seconds: float = Field(
        default=2.0, ge=0.25, le=10.0, alias="BREAKOUT_PROVIDER_CONNECT_TIMEOUT_SECONDS"
    )
    provider_read_timeout_seconds: float = Field(
        default=4.0, ge=0.5, le=20.0, alias="BREAKOUT_PROVIDER_READ_TIMEOUT_SECONDS"
    )
    provider_retry_attempts: int = Field(
        default=2, ge=1, le=3, alias="BREAKOUT_PROVIDER_RETRY_ATTEMPTS"
    )
    provider_retry_after_cap_seconds: float = Field(
        default=2.0, ge=0.1, le=10, alias="BREAKOUT_PROVIDER_RETRY_AFTER_CAP_SECONDS"
    )
    provider_cache_ttl_seconds: int = Field(
        default=60, ge=1, le=900, alias="BREAKOUT_PROVIDER_CACHE_TTL_SECONDS"
    )
    provider_stale_ttl_seconds: int = Field(
        default=900, ge=1, le=86400, alias="BREAKOUT_PROVIDER_STALE_TTL_SECONDS"
    )
    provider_failure_threshold: int = Field(
        default=3, ge=1, le=10, alias="BREAKOUT_PROVIDER_FAILURE_THRESHOLD"
    )
    provider_circuit_open_seconds: int = Field(
        default=300, ge=1, le=3600, alias="BREAKOUT_PROVIDER_CIRCUIT_OPEN_SECONDS"
    )
    provider_max_response_bytes: int = Field(
        default=2_000_000,
        ge=1024,
        le=5_000_000,
        alias="BREAKOUT_PROVIDER_MAX_RESPONSE_BYTES",
    )
    provider_result_limit: int = Field(
        default=150, ge=1, le=150, alias="BREAKOUT_PROVIDER_RESULT_LIMIT"
    )
    provider_max_concurrency: int = Field(
        default=4,
        ge=1,
        le=16,
        alias="BREAKOUT_PROVIDER_MAX_CONCURRENCY",
    )
    provider_min_market_cap: float = Field(
        default=200_000_000,
        ge=0,
        alias="BREAKOUT_PROVIDER_MIN_MARKET_CAP",
    )
    allow_etf: bool = Field(default=True, alias="BREAKOUT_ALLOW_ETF")

    daily_enrich_limit: int = Field(
        default=60, ge=1, le=60, alias="BREAKOUT_DAILY_ENRICH_LIMIT"
    )
    intraday_enrich_limit: int = Field(
        default=30, ge=1, le=40, alias="BREAKOUT_INTRADAY_ENRICH_LIMIT"
    )
    expired_due_limit: int = Field(
        default=40, ge=1, le=40, alias="BREAKOUT_EXPIRED_DUE_LIMIT"
    )
    scan_interval_premarket_seconds: int = Field(
        default=_PERSONAL_CONFIG.breakout.premarket_seconds,
        ge=60,
        le=3600,
        alias="BREAKOUT_SCAN_INTERVAL_PREMARKET_SECONDS",
    )
    scan_interval_regular_seconds: int = Field(
        default=_PERSONAL_CONFIG.breakout.regular_seconds,
        ge=60,
        le=1800,
        alias="BREAKOUT_SCAN_INTERVAL_REGULAR_SECONDS",
    )
    scan_interval_closed_seconds: int = Field(
        default=_PERSONAL_CONFIG.breakout.closed_seconds,
        ge=300,
        le=7200,
        alias="BREAKOUT_SCAN_INTERVAL_CLOSED_SECONDS",
    )
    worker_lease_ttl_seconds: int = Field(
        default=90, ge=15, le=900, alias="BREAKOUT_WORKER_LEASE_TTL_SECONDS"
    )
    worker_health_stale_seconds: int = Field(
        default=120, ge=30, le=1800, alias="BREAKOUT_WORKER_HEALTH_STALE_SECONDS"
    )
    raw_payload_retention_hours: int = Field(
        default=24, ge=1, le=168, alias="BREAKOUT_RAW_PAYLOAD_RETENTION_HOURS"
    )
    scan_retention_days: int = Field(
        default=_PERSONAL_CONFIG.storage.retention_days,
        ge=1,
        le=365,
        alias="BREAKOUT_SCAN_RETENTION_DAYS",
    )
    retention_batch_size: int = Field(
        default=500, ge=10, le=5000, alias="BREAKOUT_RETENTION_BATCH_SIZE"
    )

    min_price: float = Field(default=2.0, ge=0.01, alias="BREAKOUT_MIN_PRICE")
    min_avg_dollar_volume: float = Field(
        default=10_000_000, ge=0, alias="BREAKOUT_MIN_AVG_DOLLAR_VOLUME"
    )
    regular_min_change_pct: float = Field(
        default=3.0, ge=0, le=1000, alias="BREAKOUT_REGULAR_MIN_CHANGE_PCT"
    )
    regular_min_relative_volume: float = Field(
        default=1.5, ge=0, le=1000, alias="BREAKOUT_REGULAR_MIN_RELATIVE_VOLUME"
    )
    regular_min_dollar_volume: float = Field(
        default=5_000_000, ge=0, alias="BREAKOUT_REGULAR_MIN_DOLLAR_VOLUME"
    )
    premarket_min_change_pct: float = Field(
        default=5.0, ge=0, le=1000, alias="BREAKOUT_PREMARKET_MIN_CHANGE_PCT"
    )
    premarket_min_dollar_volume: float = Field(
        default=500_000, ge=0, alias="BREAKOUT_PREMARKET_MIN_DOLLAR_VOLUME"
    )

    base_min_days: int = Field(default=10, ge=5, le=40, alias="BREAKOUT_BASE_MIN_DAYS")
    base_max_days: int = Field(default=80, ge=20, le=160, alias="BREAKOUT_BASE_MAX_DAYS")
    pivot_tolerance_atr: float = Field(
        default=0.50, ge=0.05, le=2, alias="BREAKOUT_PIVOT_TOLERANCE_ATR"
    )
    break_buffer_atr: float = Field(
        default=0.10, ge=0, le=1, alias="BREAKOUT_BREAK_BUFFER_ATR"
    )
    break_buffer_pct: float = Field(
        default=0.0025, ge=0, le=0.05, alias="BREAKOUT_BREAK_BUFFER_PCT"
    )
    opening_range_minutes: int = Field(
        default=30, ge=5, le=120, alias="BREAKOUT_OPENING_RANGE_MINUTES"
    )
    confirmation_bars: int = Field(
        default=2, ge=1, le=6, alias="BREAKOUT_CONFIRMATION_BARS"
    )
    max_chase_distance_atr: float = Field(
        default=2.0, ge=0.5, le=10, alias="BREAKOUT_MAX_CHASE_DISTANCE_ATR"
    )
    event_ttl_seconds: int = Field(
        default=86400, ge=300, le=604800, alias="BREAKOUT_EVENT_TTL_SECONDS"
    )

    scoring_version: str = Field(
        default="breakout-score-v1", alias="BREAKOUT_SCORING_VERSION"
    )
    feature_version: str = Field(
        default="breakout-features-v1", alias="BREAKOUT_FEATURE_VERSION"
    )
    detector_version: str = Field(
        default="breakout-detector-v1", alias="BREAKOUT_DETECTOR_VERSION"
    )
    api_schema_version: str = Field(
        default="breakout-api-v1", alias="BREAKOUT_API_SCHEMA_VERSION"
    )
    provider_schema_version: str = Field(
        default="tradingview-discovery-v1", alias="BREAKOUT_PROVIDER_SCHEMA_VERSION"
    )

    range_persistence_mode: Literal["disabled", "shadow", "enabled"] = Field(
        default=_PERSONAL_RANGE_PERSISTENCE_MODE,
        alias="RANGE_PERSISTENCE_MODE",
    )
    range_persistence_validation_version: str = Field(
        default="", max_length=80, alias="RANGE_PERSISTENCE_VALIDATION_VERSION"
    )
    range_persistence_version: str = Field(
        default="range-persistence-v1", alias="RANGE_PERSISTENCE_VERSION"
    )
    range_persistence_length: int = Field(
        default=35, ge=2, le=252, alias="RANGE_PERSISTENCE_LENGTH"
    )
    range_persistence_fast_length: int = Field(
        default=3, ge=1, le=20, alias="RANGE_PERSISTENCE_FAST_LENGTH"
    )
    range_persistence_slope_days: int = Field(
        default=5, ge=2, le=30, alias="RANGE_PERSISTENCE_SLOPE_DAYS"
    )
    range_persistence_ratio_window: int = Field(
        default=10, ge=2, le=63, alias="RANGE_PERSISTENCE_RATIO_WINDOW"
    )
    range_persistence_ratio_threshold: float = Field(
        default=60, ge=0, le=100, alias="RANGE_PERSISTENCE_RATIO_THRESHOLD"
    )
    range_persistence_min_history_multiplier: int = Field(
        default=5, ge=2, le=10, alias="RANGE_PERSISTENCE_MIN_HISTORY_MULTIPLIER"
    )
    range_persistence_trend_family_weight: float = Field(
        default=0.15, ge=0, le=0.15, alias="RANGE_PERSISTENCE_TREND_FAMILY_WEIGHT"
    )
    range_persistence_final_weight_cap: float = Field(
        default=0.04, ge=0, le=0.04, alias="RANGE_PERSISTENCE_FINAL_WEIGHT_CAP"
    )
    range_persistence_breakout_interaction_enabled: bool = Field(
        default=False, alias="RANGE_PERSISTENCE_BREAKOUT_INTERACTION_ENABLED"
    )
    range_persistence_breakout_interaction_cap: float = Field(
        default=4.0, ge=0, le=4, alias="RANGE_PERSISTENCE_BREAKOUT_INTERACTION_CAP"
    )

    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    def __init__(self, **values: Any) -> None:
        """Keep explicit field-name overrides ahead of environment aliases.

        During settings-source merging, an environment alias and an explicit
        field-name key can remain distinct.  A value supplied as
        ``provider_retry_attempts=1`` may then be ignored in favour of
        ``BREAKOUT_PROVIDER_RETRY_ATTEMPTS`` from the environment.  Normalize
        explicit field names to their declared aliases before BaseSettings
        merges sources so all supported dependency versions behave alike.
        """

        normalized = dict(values)
        path_override = normalized.pop("db_path", None)
        for field_name, field in type(self).model_fields.items():
            alias = field.alias
            if field_name not in normalized or not isinstance(alias, str):
                continue
            if alias in normalized:
                raise TypeError(
                    f"conflicting explicit settings: {field_name!r} and {alias!r}"
                )
            normalized[alias] = normalized.pop(field_name)
        personal_runtime = {
            "enabled": _PERSONAL_CONFIG.features.breakout_enabled,
            "scan_interval_premarket_seconds": (
                _PERSONAL_CONFIG.breakout.premarket_seconds
            ),
            "scan_interval_regular_seconds": (
                _PERSONAL_CONFIG.breakout.regular_seconds
            ),
            "scan_interval_closed_seconds": (
                _PERSONAL_CONFIG.breakout.closed_seconds
            ),
            "scan_retention_days": _PERSONAL_CONFIG.storage.retention_days,
            "range_persistence_mode": _PERSONAL_RANGE_PERSISTENCE_MODE,
        }
        # Personal Edition owns these fields in personal.toml.  Legacy
        # BREAKOUT_* names are still understood by the offline migration tool,
        # but an exported old value must not override the running service.
        for field_name, value in personal_runtime.items():
            alias = type(self).model_fields[field_name].alias
            if isinstance(alias, str) and alias not in normalized:
                normalized[alias] = value
        super().__init__(**normalized)
        if path_override is not None:
            self._db_path_override = explicit_data_path(
                path_override,
                name="db_path",
            )

    @property
    def db_path(self) -> Path:
        return self._db_path_override or get_data_paths().optix_db

    @model_validator(mode="after")
    def validate_limits(self) -> "BreakoutSettings":
        if self.intraday_enrich_limit > self.daily_enrich_limit:
            raise ValueError("intraday enrich limit cannot exceed daily enrich limit")
        if self.base_min_days > self.base_max_days:
            raise ValueError("base min days cannot exceed max days")
        if self.provider_connect_timeout_seconds > self.provider_timeout_seconds:
            raise ValueError("connect timeout cannot exceed provider total timeout")
        if self.provider_read_timeout_seconds > self.provider_timeout_seconds:
            raise ValueError("read timeout cannot exceed provider total timeout")
        if self.worker_health_stale_seconds <= self.worker_lease_ttl_seconds:
            raise ValueError("worker health stale threshold must exceed lease TTL")
        if self.opening_range_minutes % 5 != 0:
            raise ValueError("opening range minutes must be a multiple of 5")
        if (
            self.range_persistence_mode == "enabled"
            and self.range_persistence_validation_version
            != self.range_persistence_version
        ):
            raise ValueError(
                "RANGE_PERSISTENCE_MODE=enabled requires "
                "RANGE_PERSISTENCE_VALIDATION_VERSION to match "
                "RANGE_PERSISTENCE_VERSION"
            )
        return self


@lru_cache
def get_breakout_settings() -> BreakoutSettings:
    return BreakoutSettings()
