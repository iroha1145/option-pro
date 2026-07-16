from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import Path
from typing import Literal, Union
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.personal_config import get_personal_config
from app.runtime_environment import RUNTIME_ENV_FILES, load_runtime_environment


load_runtime_environment()
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PERSONAL_CONFIG = get_personal_config()
_PERSONAL_CATALYST_MODE = {
    "off": "disabled",
    "read": "display",
    "manual": "enabled",
    "scheduled": "enabled",
}[_PERSONAL_CONFIG.features.catalyst_mode]


class CatalystSettings(BaseSettings):
    """Configuration owned by the optional Catalyst subsystem.

    It is intentionally independent from ``app.config.Settings`` so importing
    the core API remains safe when the integration is disabled or incomplete.
    """

    enabled: bool = Field(
        default=_PERSONAL_CONFIG.catalyst_sync_enabled,
        alias="MACROLENS_ENABLED",
    )
    catalyst_mode: Literal["disabled", "display", "shadow", "enabled"] = Field(
        default=_PERSONAL_CATALYST_MODE, alias="CATALYST_MODE"
    )
    base_url: str = Field(
        default="",
        alias="MACROLENS_URL",
        validation_alias=AliasChoices("MACROLENS_URL", "MACROLENS_BASE_URL"),
        max_length=500,
    )
    allow_local_http: bool = Field(
        default=False, alias="MACROLENS_ALLOW_LOCAL_HTTP"
    )
    verify_tls: bool = Field(default=True, alias="MACROLENS_VERIFY_TLS")
    ca_bundle: str = Field(default="", alias="MACROLENS_CA_BUNDLE", max_length=4096)

    read_key_id: str = Field(default="", alias="MACROLENS_READ_KEY_ID", max_length=128)
    read_secret: SecretStr = Field(default=SecretStr(""), alias="MACROLENS_READ_SECRET")
    action_key_id: str = Field(default="", alias="MACROLENS_ACTION_KEY_ID", max_length=128)
    action_secret: SecretStr = Field(default=SecretStr(""), alias="MACROLENS_ACTION_SECRET")

    connect_timeout_seconds: float = Field(
        default=3.0, ge=0.1, le=30.0, alias="MACROLENS_CONNECT_TIMEOUT_SECONDS"
    )
    read_timeout_seconds: float = Field(
        default=12.0, ge=0.5, le=60.0, alias="MACROLENS_READ_TIMEOUT_SECONDS"
    )
    total_timeout_seconds: float = Field(
        default=20.0, ge=1.0, le=120.0, alias="MACROLENS_TOTAL_TIMEOUT_SECONDS"
    )
    max_response_bytes: int = Field(
        default=5_000_000,
        ge=16_384,
        le=20_000_000,
        alias="MACROLENS_MAX_RESPONSE_BYTES",
    )
    request_max_attempts: int = Field(
        default=2, ge=1, le=3, alias="MACROLENS_REQUEST_MAX_ATTEMPTS"
    )
    failure_threshold: int = Field(
        default=3, ge=1, le=20, alias="MACROLENS_FAILURE_THRESHOLD"
    )
    circuit_open_seconds: int = Field(
        default=300, ge=5, le=3600, alias="MACROLENS_CIRCUIT_OPEN_SECONDS"
    )
    stale_ttl_seconds: int = Field(
        default=86_400, ge=60, le=2_592_000, alias="MACROLENS_STALE_TTL_SECONDS"
    )
    cache_db_path: Path = Field(
        default=Path("/data/catalyst-cache.db"), alias="MACROLENS_CACHE_DB_PATH"
    )

    health_interval_seconds: int = Field(
        default=60, ge=5, le=3600, alias="MACROLENS_HEALTH_INTERVAL_SECONDS"
    )
    feed_interval_seconds: int = Field(
        default=_PERSONAL_CONFIG.catalyst.sync_seconds,
        ge=10,
        le=7200,
        alias="MACROLENS_FEED_INTERVAL_SECONDS",
    )
    calendar_interval_seconds: int = Field(
        default=600, ge=30, le=21_600, alias="MACROLENS_CALENDAR_INTERVAL_SECONDS"
    )
    job_interval_seconds: int = Field(
        default=5, ge=2, le=300, alias="MACROLENS_JOB_INTERVAL_SECONDS"
    )
    market_focus_interval_seconds: int = Field(
        default=60,
        ge=10,
        le=3600,
        alias="MACROLENS_MARKET_FOCUS_INTERVAL_SECONDS",
    )
    hotspot_sync_limit: int = Field(
        default=100,
        ge=1,
        le=100,
        alias="MACROLENS_HOTSPOT_SYNC_LIMIT",
    )
    worker_lease_seconds: int = Field(
        default=45, ge=10, le=600, alias="MACROLENS_WORKER_LEASE_SECONDS"
    )
    latest_page_limit: int = Field(
        default=250, ge=1, le=1000, alias="MACROLENS_LATEST_PAGE_LIMIT"
    )
    latest_window_days: int = Field(
        default=7, ge=1, le=7, alias="MACROLENS_LATEST_WINDOW_DAYS"
    )
    resync_max_pages: int = Field(
        default=500, ge=1, le=5000, alias="MACROLENS_RESYNC_MAX_PAGES"
    )
    calendar_lookback_days: int = Field(
        default=2, ge=0, le=30, alias="MACROLENS_CALENDAR_LOOKBACK_DAYS"
    )
    calendar_lookahead_days: int = Field(
        default=14, ge=1, le=90, alias="MACROLENS_CALENDAR_LOOKAHEAD_DAYS"
    )

    schema_version: str = Field(
        default="macrolens-option-pro-v2", alias="MACROLENS_SCHEMA_VERSION"
    )
    schema_sha256: str = Field(default="", alias="MACROLENS_SCHEMA_SHA256", max_length=64)
    model: str = Field(
        default=_PERSONAL_CONFIG.ai.model,
        alias="OPENAI_MODEL",
        max_length=120,
    )
    reasoning: Literal["none", "low", "medium", "high", "xhigh", "max"] = Field(
        default=_PERSONAL_CONFIG.ai.reasoning, alias="OPENAI_REASONING"
    )

    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in RUNTIME_ENV_FILES),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator("base_url")
    @classmethod
    def validate_base_url_shape(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value:
            return value
        if any(character.isspace() for character in value):
            raise ValueError("MACROLENS_URL must not contain whitespace")
        parsed = urlsplit(value)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname:
            raise ValueError("MACROLENS_URL must be an absolute HTTP(S) origin")
        if parsed.username or parsed.password:
            raise ValueError("MACROLENS_URL must not contain credentials")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("MACROLENS_URL must contain only scheme, host, and port")
        return value

    @field_validator("ca_bundle")
    @classmethod
    def validate_ca_bundle(cls, value: str) -> str:
        value = value.strip()
        if value and not Path(value).is_absolute():
            raise ValueError("MACROLENS_CA_BUNDLE must be an absolute path")
        return value

    @field_validator("read_key_id", "action_key_id")
    @classmethod
    def validate_key_id(cls, value: str) -> str:
        value = value.strip()
        if value and not _KEY_ID_PATTERN.fullmatch(value):
            raise ValueError("MacroLens key id contains unsupported characters")
        return value

    @field_validator("read_secret", "action_secret")
    @classmethod
    def validate_hmac_secret(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()
        if secret and len(secret.encode("utf-8")) < 32:
            raise ValueError("MacroLens HMAC secrets must contain at least 32 bytes")
        return SecretStr(secret)

    @field_validator("schema_sha256")
    @classmethod
    def validate_schema_digest(cls, value: str) -> str:
        value = value.strip().lower()
        if value and (len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)):
            raise ValueError("MACROLENS_SCHEMA_SHA256 must be 64 lowercase hex characters")
        return value

    @model_validator(mode="after")
    def validate_remote_boundary(self) -> "CatalystSettings":
        read_secret = self.read_secret.get_secret_value()
        action_secret = self.action_secret.get_secret_value()
        if bool(self.read_key_id) != bool(read_secret):
            raise ValueError("MacroLens read key id and secret must be configured together")
        if bool(self.action_key_id) != bool(action_secret):
            raise ValueError("MacroLens action key id and secret must be configured together")
        if self.read_key_id and self.read_key_id == self.action_key_id:
            raise ValueError("MacroLens read and action key ids must be different")
        if not self.enabled:
            return self
        if not self.base_url:
            raise ValueError("MACROLENS_URL is required when MACROLENS_ENABLED=true")
        parsed = urlsplit(self.base_url)
        hostname = (parsed.hostname or "").lower()
        is_local = hostname in _LOCAL_HOSTS
        if not is_local:
            try:
                is_local = ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
        if parsed.scheme != "https" and not (is_local and self.allow_local_http):
            raise ValueError("MacroLens must use HTTPS; local HTTP requires explicit opt-in")
        if parsed.scheme == "https" and not self.verify_tls and not is_local:
            raise ValueError("MACROLENS_VERIFY_TLS cannot be disabled for a remote host")
        if self.ca_bundle and not Path(self.ca_bundle).is_file():
            raise ValueError("MACROLENS_CA_BUNDLE must point to a readable file")
        if not self.read_key_id or not read_secret:
            raise ValueError("MacroLens read credentials are required when enabled")
        return self

    @property
    def action_enabled(self) -> bool:
        return bool(self.action_key_id and self.action_secret.get_secret_value())

    @property
    def tls_verify_value(self) -> Union[bool, str]:
        if self.ca_bundle:
            return self.ca_bundle
        return self.verify_tls


@lru_cache
def get_catalyst_settings() -> CatalystSettings:
    return CatalystSettings()
