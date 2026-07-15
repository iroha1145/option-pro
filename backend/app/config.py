from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import AliasChoices, AnyHttpUrl, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.personal_config import get_personal_config


# Resolve the repository-level .env independently of the process working
# directory. This keeps `uvicorn app.main:app` reliable when run from backend/.
_ROOT_ENV_FILE = Path(__file__).resolve().parents[2] / ".env"
_PERSONAL_CONFIG = get_personal_config()
# Several existing middleware settings are read directly from os.environ.
# Load the same root file once (without overriding exported variables) so local
# uvicorn runs and BaseSettings observe one consistent configuration source.
load_dotenv(dotenv_path=_ROOT_ENV_FILE, override=False)


class Settings(BaseSettings):
    """Runtime configuration loaded from environment or .env."""

    openai_api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    openai_base_url: str = Field(default="", alias="OPENAI_BASE_URL")
    allow_custom_openai_base_url: bool = Field(
        default=False,
        alias="ALLOW_CUSTOM_OPENAI_BASE_URL",
    )
    openai_custom_capabilities_confirmed: bool = Field(
        default=False,
        alias="OPENAI_CUSTOM_CAPABILITIES_CONFIRMED",
    )
    openai_model: str = Field(
        default=_PERSONAL_CONFIG.ai.model,
        min_length=1,
        max_length=120,
        alias="OPENAI_MODEL",
    )
    openai_reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"] = Field(
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
        le=4,
        alias="OPENAI_MAX_CONCURRENCY",
    )
    openai_daily_max_jobs: int = Field(
        default=_PERSONAL_CONFIG.ai.daily_max_jobs,
        ge=1,
        le=100,
        alias="OPENAI_DAILY_MAX_JOBS",
    )
    openai_execution_mode: Literal["background", "worker_sync"] = Field(
        default=_PERSONAL_CONFIG.ai.execution_mode,
        alias="OPENAI_EXECUTION_MODE",
    )
    openai_require_zdr: bool = Field(default=False, alias="OPENAI_REQUIRE_ZDR")
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
    openai_job_db_path: Path = Field(
        default=Path("/data/ai-jobs.db"),
        alias="OPENAI_JOB_DB_PATH",
    )
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

    massive_api_key: str = Field(default="", alias="MASSIVE_API_KEY")
    massive_base_url: AnyHttpUrl = Field(default="https://api.massive.com", alias="MASSIVE_BASE_URL")
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
    marketdata_token: str = Field(default="", alias="MARKETDATA_TOKEN")
    marketdata_api_token: str = Field(default="", alias="MARKETDATA_API_TOKEN")
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
        env_file=str(_ROOT_ENV_FILE),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("openai_base_url")
    @classmethod
    def validate_openai_base_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("OPENAI_BASE_URL must be empty or start with https:// or http://")
        if any(char.isspace() for char in value):
            raise ValueError("OPENAI_BASE_URL must not contain whitespace")
        if value:
            parsed = urlsplit(value)
            if not parsed.hostname:
                raise ValueError("OPENAI_BASE_URL must include a hostname")
            if parsed.username or parsed.password:
                raise ValueError("OPENAI_BASE_URL must not contain credentials")
        return value

    @field_validator("openai_model")
    @classmethod
    def validate_openai_model(cls, value: str) -> str:
        value = value.strip()
        if not value or any(char.isspace() or ord(char) < 32 for char in value):
            raise ValueError("OPENAI_MODEL must be a bounded model identifier without whitespace")
        return value

    @model_validator(mode="after")
    def require_custom_base_url_opt_in(self) -> "Settings":
        if not self.openai_base_url:
            return self
        if not self.allow_custom_openai_base_url:
            raise ValueError(
                "custom OPENAI_BASE_URL requires ALLOW_CUSTOM_OPENAI_BASE_URL=true"
            )
        parsed = urlsplit(self.openai_base_url)
        local_hosts = {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and parsed.hostname not in local_hosts:
            raise ValueError("custom OPENAI_BASE_URL must use HTTPS unless it is localhost")
        return self

    @model_validator(mode="after")
    def validate_openai_runtime(self) -> "Settings":
        if (
            self.openai_background_max_poll_seconds
            < self.openai_background_initial_poll_seconds
        ):
            raise ValueError(
                "OPENAI_BACKGROUND_MAX_POLL_SECONDS must be at least the initial interval"
            )
        if self.openai_require_zdr and self.openai_execution_mode == "background":
            raise ValueError(
                "OPENAI_REQUIRE_ZDR=true requires OPENAI_EXECUTION_MODE=worker_sync"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
