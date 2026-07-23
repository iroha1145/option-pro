from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import (
    AliasChoices,
    AnyHttpUrl,
    Field,
    PrivateAttr,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data_paths import explicit_data_path, get_data_paths
from app.personal_config import get_personal_config
from app.runtime_environment import load_runtime_environment


_PERSONAL_CONFIG = get_personal_config()
load_runtime_environment()


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or .env."""

    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    # Deprecated provider switches remain as rejecting sentinels for one
    # migration release. They cannot alter the official Responses runtime.
    openai_base_url: Literal[""] = Field(default="", alias="OPENAI_BASE_URL")
    allow_custom_openai_base_url: bool = Field(
        default=False,
        alias="ALLOW_CUSTOM_OPENAI_BASE_URL",
    )
    openai_custom_capabilities_confirmed: bool = Field(
        default=False,
        alias="OPENAI_CUSTOM_CAPABILITIES_CONFIRMED",
    )
    openai_require_zdr: bool = Field(
        default=False,
        alias="OPENAI_REQUIRE_ZDR",
    )
    openai_model: Literal["gpt-5.6-terra"] = Field(
        default=_PERSONAL_CONFIG.ai.model,
        alias="OPENAI_MODEL",
    )
    openai_reasoning: Literal["max"] = Field(
        default=_PERSONAL_CONFIG.ai.reasoning,
        alias="OPENAI_REASONING",
    )
    openai_timeout_seconds: float = Field(
        default=900.0,
        ge=5.0,
        le=1800.0,
        alias="OPENAI_TIMEOUT_SECONDS",
    )
    openai_max_retries: int = Field(default=0, ge=0, le=0, alias="OPENAI_MAX_RETRIES")
    openai_max_output_tokens: int = Field(
        default=32768,
        ge=256,
        le=128000,
        validation_alias=AliasChoices(
            "OPTION_PRO_AI_MAX_OUTPUT_TOKENS",
            "OPENAI_MAX_OUTPUT_TOKENS",
        ),
    )
    openai_max_concurrency: int = Field(
        default=_PERSONAL_CONFIG.ai.max_concurrency,
        ge=1,
        le=1,
        alias="OPENAI_MAX_CONCURRENCY",
    )
    openai_daily_max_jobs: int = Field(
        default=_PERSONAL_CONFIG.ai.daily_max_jobs,
        ge=0,
        le=100_000,
        alias="OPENAI_DAILY_MAX_JOBS",
    )
    openai_daily_budget_usd: float = Field(
        default=_PERSONAL_CONFIG.ai.daily_budget_usd,
        ge=0.0,
        le=10_000.0,
        alias="OPENAI_DAILY_BUDGET_USD",
    )
    openai_daily_token_limit: int = Field(
        default=_PERSONAL_CONFIG.ai.daily_token_limit,
        ge=102_400,
        le=100_000_000,
        alias="OPENAI_DAILY_TOKEN_LIMIT",
    )
    openai_manual_cooldown_seconds: int = Field(
        default=_PERSONAL_CONFIG.catalyst.manual_refresh_cooldown_seconds,
        ge=0,
        le=3600,
        alias="OPENAI_MANUAL_COOLDOWN_SECONDS",
    )
    openai_execution_mode: Literal["background"] = Field(
        default=_PERSONAL_CONFIG.ai.execution_mode,
        alias="OPENAI_EXECUTION_MODE",
    )
    openai_background_poll_timeout_seconds: float = Field(
        default=1800.0,
        ge=60.0,
        le=86400.0,
        alias="OPENAI_BACKGROUND_POLL_TIMEOUT_SECONDS",
    )
    openai_background_initial_poll_seconds: float = Field(
        default=2.0,
        ge=1.0,
        le=30.0,
        alias="OPENAI_BACKGROUND_INITIAL_POLL_SECONDS",
    )
    openai_background_max_poll_seconds: float = Field(
        default=15.0,
        ge=1.0,
        le=300.0,
        alias="OPENAI_BACKGROUND_MAX_POLL_SECONDS",
    )
    openai_control_timeout_seconds: float = Field(
        default=30.0,
        ge=3.0,
        le=60.0,
        alias="OPENAI_CONTROL_TIMEOUT_SECONDS",
    )
    _openai_job_db_path_override: Path | None = PrivateAttr(default=None)
    openai_job_lease_seconds: int = Field(
        default=60,
        ge=30,
        le=600,
        alias="OPENAI_JOB_LEASE_SECONDS",
    )
    openai_job_max_age_seconds: int = Field(
        default=86400,
        ge=1800,
        le=604800,
        alias="OPENAI_JOB_MAX_AGE_SECONDS",
    )
    openai_job_max_queued: int = Field(
        default=200,
        ge=1,
        le=10000,
        alias="OPENAI_JOB_MAX_QUEUED",
    )

    macrolens_url: str = Field(
        default="",
        alias="MACROLENS_URL",
        max_length=500,
    )
    internal_api_token: SecretStr = Field(
        default=SecretStr(""),
        alias="INTERNAL_API_TOKEN",
    )
    macrolens_ca_bundle: str = Field(
        default="",
        alias="MACROLENS_CA_BUNDLE",
        max_length=4096,
    )
    _macrolens_cache_db_path_override: Path | None = PrivateAttr(default=None)
    _optix_worker_db_path_override: Path | None = PrivateAttr(default=None)
    _optix_worker_lock_path_override: Path | None = PrivateAttr(default=None)
    _breakout_db_path_override: Path | None = PrivateAttr(default=None)
    _optix_backup_dir_override: Path | None = PrivateAttr(default=None)

    # Migration-only sentinels. The worker never consumes either legacy name;
    # retaining them here lets startup reject an unmigrated or conflicting file
    # without exposing either value.
    legacy_macrolens_base_url: str = Field(
        default="",
        alias="MACROLENS_BASE_URL",
        max_length=500,
        exclude=True,
        repr=False,
    )
    legacy_macrolens_internal_token: SecretStr = Field(
        default=SecretStr(""),
        alias="MACROLENS_INTERNAL_TOKEN",
        exclude=True,
        repr=False,
    )

    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    finnhub_base_url: AnyHttpUrl = Field(default="https://finnhub.io/api/v1", alias="FINNHUB_BASE_URL")
    finnhub_enrich_limit: int = Field(default=20, alias="FINNHUB_ENRICH_LIMIT")
    finnhub_candle_fallback_enabled: bool = Field(default=True, alias="FINNHUB_CANDLE_FALLBACK_ENABLED")
    finnhub_candle_fallback_limit: int = Field(default=80, alias="FINNHUB_CANDLE_FALLBACK_LIMIT")
    stooq_price_fallback_enabled: bool = Field(default=True, alias="STOOQ_PRICE_FALLBACK_ENABLED")
    stooq_price_fallback_limit: int = Field(default=260, alias="STOOQ_PRICE_FALLBACK_LIMIT")
    yahoo_options_enabled: bool = Field(default=True, alias="YAHOO_OPTIONS_ENABLED")
    yahoo_options_enrich_limit: int = Field(default=90, alias="YAHOO_OPTIONS_ENRICH_LIMIT")
    yahoo_option_target_dte: int = Field(default=30, alias="YAHOO_OPTION_TARGET_DTE")
    yahoo_option_min_dte: int = Field(default=14, alias="YAHOO_OPTION_MIN_DTE")
    yahoo_option_max_dte: int = Field(default=60, alias="YAHOO_OPTION_MAX_DTE")
    yahoo_option_strike_window_pct: float = Field(default=0.16, alias="YAHOO_OPTION_STRIKE_WINDOW_PCT")
    yahoo_options_failure_limit: int = Field(default=8, alias="YAHOO_OPTIONS_FAILURE_LIMIT")
    massive_api_key: str = Field(default="", alias="MASSIVE_API_KEY")
    massive_base_url: str = Field(default="https://api.massive.com", alias="MASSIVE_BASE_URL")
    marketdata_token: str = Field(default="", alias="MARKETDATA_TOKEN")
    marketdata_base_url: AnyHttpUrl = Field(default="https://api.marketdata.app", alias="MARKETDATA_BASE_URL")
    marketdata_stock_candle_fallback_enabled: bool = Field(default=True, alias="MARKETDATA_STOCK_CANDLE_FALLBACK_ENABLED")
    marketdata_stock_candle_fallback_limit: int = Field(default=260, alias="MARKETDATA_STOCK_CANDLE_FALLBACK_LIMIT")
    marketdata_options_enrich_limit: int = Field(default=8, alias="MARKETDATA_OPTIONS_ENRICH_LIMIT")
    marketdata_option_dte: int = Field(default=30, alias="MARKETDATA_OPTION_DTE")
    marketdata_option_strike_limit: int = Field(default=8, alias="MARKETDATA_OPTION_STRIKE_LIMIT")
    marketdata_option_mode: str = Field(default="delayed", alias="MARKETDATA_OPTION_MODE")
    cache_ttl: int = Field(default=60, alias="CACHE_TTL")
    request_timeout: float = Field(default=20.0, alias="REQUEST_TIMEOUT")

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("macrolens_url")
    @classmethod
    def validate_macrolens_url(cls, value: str) -> str:
        if value != value.strip() or any(character.isspace() for character in value):
            raise ValueError("MACROLENS_URL must not contain whitespace")
        value = value.rstrip("/")
        if not value:
            return value
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("MACROLENS_URL must be an absolute HTTPS origin")
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("MACROLENS_URL contains an invalid port") from exc
        if parsed.netloc.endswith(":") or (port is not None and port < 1):
            raise ValueError("MACROLENS_URL contains an invalid port")
        if parsed.username or parsed.password:
            raise ValueError("MACROLENS_URL must not contain credentials")
        if parsed.path or parsed.query or parsed.fragment:
            raise ValueError("MACROLENS_URL must contain only scheme, host, and port")
        return value

    @field_validator("internal_api_token")
    @classmethod
    def validate_internal_api_token(cls, value: SecretStr) -> SecretStr:
        token = value.get_secret_value()
        if token != token.strip() or "\r" in token or "\n" in token:
            raise ValueError("INTERNAL_API_TOKEN contains unsupported whitespace")
        if len(token) > 4096:
            raise ValueError("INTERNAL_API_TOKEN is too long")
        return SecretStr(token)

    @field_validator("macrolens_ca_bundle")
    @classmethod
    def validate_macrolens_ca_bundle(cls, value: str) -> str:
        value = value.strip()
        if not value:
            return value
        path = Path(value).expanduser()
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError("MACROLENS_CA_BUNDLE must be an absolute path")
        if not path.is_file():
            raise ValueError("MACROLENS_CA_BUNDLE must point to a readable file")
        return str(path)

    def __init__(
        self,
        *,
        openai_job_db_path: str | Path | None = None,
        macrolens_cache_db_path: str | Path | None = None,
        optix_worker_db_path: str | Path | None = None,
        optix_worker_lock_path: str | Path | None = None,
        breakout_db_path: str | Path | None = None,
        optix_backup_dir: str | Path | None = None,
        **values: Any,
    ) -> None:
        normalized = dict(values)
        personal_runtime = {
            "openai_model": _PERSONAL_CONFIG.ai.model,
            "openai_reasoning": _PERSONAL_CONFIG.ai.reasoning,
            "openai_max_concurrency": _PERSONAL_CONFIG.ai.max_concurrency,
            "openai_daily_max_jobs": _PERSONAL_CONFIG.ai.daily_max_jobs,
            "openai_daily_budget_usd": _PERSONAL_CONFIG.ai.daily_budget_usd,
            "openai_daily_token_limit": _PERSONAL_CONFIG.ai.daily_token_limit,
            "openai_manual_cooldown_seconds": (
                _PERSONAL_CONFIG.catalyst.manual_refresh_cooldown_seconds
            ),
            "openai_execution_mode": _PERSONAL_CONFIG.ai.execution_mode,
        }
        # These values moved from the process environment to personal.toml.
        # Supplying them as the highest-priority settings source prevents a
        # stale exported OPENAI_* variable from silently changing production.
        # Explicit constructor values remain available to isolated tests.
        for field_name, value in personal_runtime.items():
            field = type(self).model_fields[field_name]
            alias = field.alias
            if field_name in normalized or (
                isinstance(alias, str) and alias in normalized
            ):
                continue
            normalized[alias if isinstance(alias, str) else field_name] = value
        super().__init__(**normalized)
        overrides = (
            ("_openai_job_db_path_override", "openai_job_db_path", openai_job_db_path),
            (
                "_macrolens_cache_db_path_override",
                "macrolens_cache_db_path",
                macrolens_cache_db_path,
            ),
            ("_optix_worker_db_path_override", "optix_worker_db_path", optix_worker_db_path),
            (
                "_optix_worker_lock_path_override",
                "optix_worker_lock_path",
                optix_worker_lock_path,
            ),
            ("_breakout_db_path_override", "breakout_db_path", breakout_db_path),
            ("_optix_backup_dir_override", "optix_backup_dir", optix_backup_dir),
        )
        for attribute, name, value in overrides:
            if value is not None:
                setattr(self, attribute, explicit_data_path(value, name=name))

    @property
    def openai_job_db_path(self) -> Path:
        return self._openai_job_db_path_override or get_data_paths().ai_jobs_db

    @property
    def macrolens_cache_db_path(self) -> Path:
        return self._macrolens_cache_db_path_override or get_data_paths().catalyst_cache_db

    @property
    def optix_worker_db_path(self) -> Path:
        return self._optix_worker_db_path_override or get_data_paths().worker_db

    @property
    def optix_worker_lock_path(self) -> Path:
        return self._optix_worker_lock_path_override or get_data_paths().worker_lock

    @property
    def breakout_db_path(self) -> Path:
        return self._breakout_db_path_override or get_data_paths().optix_db

    @property
    def optix_backup_dir(self) -> Path:
        return self._optix_backup_dir_override or get_data_paths().backups_dir

    @model_validator(mode="after")
    def validate_openai_runtime(self) -> "Settings":
        if self.allow_custom_openai_base_url:
            raise ValueError("custom OpenAI endpoints are disabled")
        if self.openai_custom_capabilities_confirmed:
            raise ValueError("custom OpenAI capabilities are disabled")
        if self.openai_require_zdr:
            raise ValueError("background Responses cannot require ZDR")
        if (
            self.openai_background_max_poll_seconds
            < self.openai_background_initial_poll_seconds
        ):
            raise ValueError(
                "OPENAI_BACKGROUND_MAX_POLL_SECONDS must be at least the initial interval"
            )
        token = self.internal_api_token.get_secret_value()
        legacy_url = self.legacy_macrolens_base_url.strip().rstrip("/")
        legacy_token = self.legacy_macrolens_internal_token.get_secret_value().strip()
        if legacy_url and not self.macrolens_url:
            raise ValueError("MACROLENS_BASE_URL is migration-only; use MACROLENS_URL")
        if legacy_url and legacy_url != self.macrolens_url:
            raise ValueError("conflicting MacroLens URL settings")
        if legacy_token and not token:
            raise ValueError(
                "MACROLENS_INTERNAL_TOKEN is migration-only; use INTERNAL_API_TOKEN"
            )
        if legacy_token and legacy_token != token:
            raise ValueError("conflicting MacroLens token settings")
        if self.macrolens_url and not token:
            raise ValueError("MACROLENS_URL requires INTERNAL_API_TOKEN")
        if token and not self.macrolens_url:
            raise ValueError("INTERNAL_API_TOKEN requires MACROLENS_URL")
        return self

    @property
    def personal_etl_enabled(self) -> bool:
        return bool(self.macrolens_url and self.internal_api_token.get_secret_value())


@lru_cache
def get_settings() -> Settings:
    return Settings()
