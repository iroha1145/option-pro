from __future__ import annotations

import ipaddress
import hmac
import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.services.request_security import parse_trusted_proxy_cidrs


_ROOT_ENV_FILE = Path(__file__).resolve().parents[4] / ".env"
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.^/_-]{0,19}$")


def _ticker_list(value: str) -> str:
    output: list[str] = []
    for item in value.split(","):
        ticker = item.strip().upper()
        if not ticker:
            continue
        if not _TICKER_PATTERN.fullmatch(ticker):
            raise ValueError(f"invalid focus ticker: {ticker}")
        if ticker not in output:
            output.append(ticker)
    return ",".join(output)


class FocusContextSettings(BaseSettings):
    key_id: str = Field(default="", alias="MACROLENS_FOCUS_KEY_ID", max_length=128)
    secret: SecretStr = Field(default=SecretStr(""), alias="MACROLENS_FOCUS_SECRET")
    previous_secret: SecretStr = Field(
        default=SecretStr(""), alias="MACROLENS_FOCUS_PREVIOUS_SECRET"
    )
    allowed_cidrs: str = Field(default="", alias="MACROLENS_FOCUS_ALLOWED_CIDRS")
    trusted_proxy_cidrs: str = Field(
        default="", alias="MACROLENS_FOCUS_TRUSTED_PROXY_CIDRS"
    )
    clock_skew_seconds: int = Field(
        default=300, ge=30, le=3600, alias="MACROLENS_FOCUS_CLOCK_SKEW_SECONDS"
    )
    nonce_ttl_seconds: int = Field(
        default=600, ge=60, le=86400, alias="MACROLENS_FOCUS_NONCE_TTL_SECONDS"
    )
    cache_db_path: Path = Field(
        default=Path("/data/catalyst-cache.db"), alias="MACROLENS_CACHE_DB_PATH"
    )

    dollar_volume_count: int = Field(
        default=20, ge=1, le=100, alias="FOCUS_DOLLAR_VOLUME_COUNT"
    )
    strength_count: int = Field(default=10, ge=0, le=100, alias="FOCUS_STRENGTH_COUNT")
    max_symbols: int = Field(default=40, ge=1, le=200, alias="FOCUS_MAX_SYMBOLS")
    enter_dollar_volume_rank: int = Field(
        default=20, ge=1, le=200, alias="FOCUS_ENTER_DOLLAR_VOLUME_RANK"
    )
    retain_dollar_volume_rank: int = Field(
        default=30, ge=1, le=500, alias="FOCUS_RETAIN_DOLLAR_VOLUME_RANK"
    )
    max_replacements_per_cycle: int = Field(
        default=5, ge=0, le=100, alias="FOCUS_MAX_REPLACEMENTS_PER_CYCLE"
    )
    refresh_seconds: int = Field(
        default=1800, ge=60, le=86400, alias="FOCUS_CONTEXT_REFRESH_SECONDS"
    )
    producer_snapshot_grace_seconds: int = Field(
        default=120,
        ge=30,
        le=900,
        alias="FOCUS_PRODUCER_SNAPSHOT_GRACE_SECONDS",
    )
    producer_enabled: bool = Field(
        default=False, alias="FOCUS_PRODUCER_ENABLED"
    )
    producer_interval_seconds: int = Field(
        default=1800,
        ge=1800,
        le=1800,
        alias="FOCUS_PRODUCER_INTERVAL_SECONDS",
    )
    producer_candidate_limit: int = Field(
        default=40,
        ge=40,
        le=60,
        alias="FOCUS_PRODUCER_CANDIDATE_LIMIT",
    )
    producer_heartbeat_seconds: int = Field(
        default=30,
        ge=5,
        le=300,
        alias="FOCUS_PRODUCER_HEARTBEAT_SECONDS",
    )
    producer_health_stale_seconds: int = Field(
        default=120,
        ge=30,
        le=900,
        alias="FOCUS_PRODUCER_HEALTH_STALE_SECONDS",
    )
    producer_lease_seconds: int = Field(
        default=90,
        ge=30,
        le=900,
        alias="FOCUS_PRODUCER_LEASE_SECONDS",
    )
    daily_strength_degraded_ttl_seconds: int = Field(
        default=300,
        ge=60,
        le=1800,
        alias="FOCUS_DAILY_STRENGTH_DEGRADED_TTL_SECONDS",
    )
    daily_strength_settlement_delay_seconds: int = Field(
        default=1800,
        ge=300,
        le=7200,
        alias="FOCUS_DAILY_STRENGTH_SETTLEMENT_DELAY_SECONDS",
    )
    daily_strength_min_coverage: float = Field(
        default=0.9,
        ge=0.5,
        le=1.0,
        alias="FOCUS_DAILY_STRENGTH_MIN_COVERAGE",
    )
    daily_strength_retention_days: int = Field(
        default=30,
        ge=1,
        le=365,
        alias="FOCUS_DAILY_STRENGTH_RETENTION_DAYS",
    )
    snapshot_retention_days: int = Field(
        default=90,
        ge=1,
        le=730,
        alias="FOCUS_SNAPSHOT_RETENTION_DAYS",
    )
    snapshot_full_resolution_days: int = Field(
        default=30,
        ge=1,
        le=730,
        alias="FOCUS_SNAPSHOT_FULL_RESOLUTION_DAYS",
    )
    snapshot_daily_rollup_enabled: bool = Field(
        default=True,
        alias="FOCUS_SNAPSHOT_DAILY_ROLLUP_ENABLED",
    )
    priority_watchlist: str = Field(default="", alias="FOCUS_PRIORITY_WATCHLIST")
    major_index_constituents: str = Field(
        default="", alias="FOCUS_MAJOR_INDEX_CONSTITUENTS"
    )
    major_market_symbols: str = Field(
        default="SPY,QQQ,DIA,IWM", alias="FOCUS_MAJOR_MARKET_SYMBOLS"
    )

    model_config = SettingsConfigDict(
        env_file=str(_ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("key_id")
    @classmethod
    def validate_key_id(cls, value: str) -> str:
        value = value.strip()
        if value and not _KEY_ID_PATTERN.fullmatch(value):
            raise ValueError("MacroLens focus key id contains unsupported characters")
        return value

    @field_validator("secret", "previous_secret")
    @classmethod
    def validate_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if secret and len(secret.encode("utf-8")) < 32:
            raise ValueError("MacroLens focus HMAC secrets must contain at least 32 bytes")
        return SecretStr(secret)

    @field_validator("allowed_cidrs", "trusted_proxy_cidrs")
    @classmethod
    def validate_cidrs(cls, value: str) -> str:
        normalized = ",".join(item.strip() for item in value.split(",") if item.strip())
        parse_trusted_proxy_cidrs(normalized)
        return normalized

    @field_validator(
        "priority_watchlist", "major_index_constituents", "major_market_symbols"
    )
    @classmethod
    def validate_tickers(cls, value: str) -> str:
        return _ticker_list(value)

    @model_validator(mode="after")
    def validate_boundary(self) -> "FocusContextSettings":
        secret = self.secret.get_secret_value()
        if bool(self.key_id) != bool(secret):
            raise ValueError("MacroLens focus key id and secret must be configured together")
        if self.previous_secret.get_secret_value() and not secret:
            raise ValueError("MacroLens focus previous secret requires the current secret")
        if self.previous_secret.get_secret_value() and hmac.compare_digest(
            self.previous_secret.get_secret_value(), secret
        ):
            raise ValueError("MacroLens focus previous secret must differ from the current secret")
        if self.configured and not self.allowed_networks:
            raise ValueError("MACROLENS_FOCUS_ALLOWED_CIDRS is required when focus HMAC is configured")
        if self.retain_dollar_volume_rank < self.enter_dollar_volume_rank:
            raise ValueError("FOCUS_RETAIN_DOLLAR_VOLUME_RANK must be at least the enter rank")
        if self.dollar_volume_count < self.enter_dollar_volume_rank:
            raise ValueError("FOCUS_DOLLAR_VOLUME_COUNT must cover the enter rank")
        if self.nonce_ttl_seconds < self.clock_skew_seconds:
            raise ValueError("MACROLENS_FOCUS_NONCE_TTL_SECONDS must cover the clock skew")
        if self.producer_lease_seconds <= self.producer_heartbeat_seconds * 2:
            raise ValueError(
                "FOCUS_PRODUCER_LEASE_SECONDS must exceed two heartbeat intervals"
            )
        if self.producer_health_stale_seconds <= self.producer_heartbeat_seconds:
            raise ValueError(
                "FOCUS_PRODUCER_HEALTH_STALE_SECONDS must exceed the heartbeat interval"
            )
        if (
            self.producer_enabled
            and self.refresh_seconds != self.producer_interval_seconds
        ):
            raise ValueError(
                "FOCUS_CONTEXT_REFRESH_SECONDS must match "
                "FOCUS_PRODUCER_INTERVAL_SECONDS when the producer is enabled"
            )
        if self.snapshot_full_resolution_days > self.snapshot_retention_days:
            raise ValueError(
                "FOCUS_SNAPSHOT_FULL_RESOLUTION_DAYS must not exceed "
                "FOCUS_SNAPSHOT_RETENTION_DAYS"
            )
        return self

    @property
    def configured(self) -> bool:
        return bool(self.key_id and self.secret.get_secret_value())

    @property
    def allowed_networks(self) -> tuple[ipaddress._BaseNetwork, ...]:
        return parse_trusted_proxy_cidrs(self.allowed_cidrs)

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress._BaseNetwork, ...]:
        return parse_trusted_proxy_cidrs(self.trusted_proxy_cidrs)

    @property
    def priority_symbols(self) -> list[str]:
        return self.priority_watchlist.split(",") if self.priority_watchlist else []

    @property
    def index_constituent_symbols(self) -> list[str]:
        return self.major_index_constituents.split(",") if self.major_index_constituents else []

    @property
    def market_symbols(self) -> list[str]:
        return self.major_market_symbols.split(",") if self.major_market_symbols else []


@lru_cache
def get_focus_context_settings() -> FocusContextSettings:
    return FocusContextSettings()
