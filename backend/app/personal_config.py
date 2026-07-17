from __future__ import annotations

import ipaddress
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility for legacy deploy checks.
    import tomli as tomllib  # type: ignore[no-redef]


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PERSONAL_CONFIG_PATH = REPOSITORY_ROOT / "config" / "personal.toml"
_PRIVATE_NETWORK_ENVELOPES = tuple(
    ipaddress.ip_network(value)
    for value in (
        "127.0.0.0/8",
        "10.0.0.0/8",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "100.64.0.0/10",
        "::1/128",
        "fc00::/7",
    )
)


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AccessConfig(StrictConfigModel):
    mode: Literal["private_network", "password"] = "private_network"
    allowed_private_cidrs: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.0/8",
            "::1/128",
            "10.0.0.0/8",
            "172.16.0.0/12",
            "192.168.0.0/16",
            "100.64.0.0/10",
        ],
        min_length=1,
        max_length=32,
    )

    @field_validator("allowed_private_cidrs")
    @classmethod
    def validate_private_cidrs(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            try:
                network = ipaddress.ip_network(value.strip(), strict=False)
            except ValueError as exc:
                raise ValueError("private access networks must use CIDR notation") from exc
            allowed = any(
                network.version == envelope.version
                and network.subnet_of(envelope)
                for envelope in _PRIVATE_NETWORK_ENVELOPES
            )
            if not allowed:
                raise ValueError(
                    "private access networks must use loopback, RFC1918, "
                    "Tailscale, or IPv6 unique-local ranges"
                )
            item = str(network)
            if item not in normalized:
                normalized.append(item)
        return normalized


class FeatureConfig(StrictConfigModel):
    breakout_enabled: bool = True
    catalyst_mode: Literal["off", "read", "manual", "scheduled"] = "read"


class AIConfig(StrictConfigModel):
    model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    reasoning: Literal["max"] = "max"
    max_concurrency: Literal[1] = 1
    daily_max_jobs: int = Field(default=4, ge=1, le=4)
    daily_budget_usd: float = Field(default=2.0, ge=0.01, le=100.0)
    execution_mode: Literal["background"] = "background"


class CatalystConfig(StrictConfigModel):
    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
    manual_force_reanalysis: Literal[True] = True
    manual_refresh_cooldown_seconds: int = Field(default=30, ge=0, le=3600)
    scheduled_times_et: list[str] = Field(
        default_factory=lambda: ["08:00", "12:00", "16:00"],
        min_length=1,
        max_length=8,
    )

    @field_validator("scheduled_times_et")
    @classmethod
    def validate_times(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            parts = value.split(":")
            if len(parts) != 2 or not all(part.isdigit() for part in parts):
                raise ValueError("scheduled times must use HH:MM")
            hour, minute = (int(part) for part in parts)
            if not 0 <= hour <= 23 or not 0 <= minute <= 59:
                raise ValueError("scheduled times must be valid clock times")
            item = f"{hour:02d}:{minute:02d}"
            if item not in normalized:
                normalized.append(item)
        return normalized


class BreakoutConfig(StrictConfigModel):
    regular_seconds: int = Field(default=300, ge=30, le=86_400)
    premarket_seconds: int = Field(default=600, ge=30, le=86_400)
    closed_seconds: int = Field(default=1800, ge=60, le=86_400)
    range_persistence_mode: Literal["off", "shadow", "active"] = "shadow"


class PublicHomeConfig(StrictConfigModel):
    poll_seconds: int = Field(default=30, ge=10, le=300)
    watchlist_seconds: int = Field(default=1800, ge=300, le=86_400)
    indices_seconds: int = Field(default=300, ge=300, le=86_400)
    overview_seconds: int = Field(default=300, ge=300, le=86_400)
    chart_seconds: int = Field(default=300, ge=300, le=86_400)
    signals_seconds: int = Field(default=900, ge=900, le=86_400)
    earnings_seconds: int = Field(default=21_600, ge=21_600, le=172_800)
    unusual_seconds: int = Field(default=1800, ge=1800, le=86_400)
    failure_retry_seconds: int = Field(default=300, ge=60, le=3600)


class StorageConfig(StrictConfigModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    backup_keep: int = Field(default=7, ge=1, le=100)


class PersonalConfig(StrictConfigModel):
    access: AccessConfig = Field(default_factory=AccessConfig)
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    catalyst: CatalystConfig = Field(default_factory=CatalystConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
    public_home: PublicHomeConfig = Field(default_factory=PublicHomeConfig)
    storage: StorageConfig = Field(default_factory=StorageConfig)

    @property
    def catalyst_sync_enabled(self) -> bool:
        return self.features.catalyst_mode != "off"

    @property
    def catalyst_manual_enabled(self) -> bool:
        return self.features.catalyst_mode in {"manual", "scheduled"}

    @property
    def catalyst_scheduled_enabled(self) -> bool:
        return self.features.catalyst_mode == "scheduled"


def load_personal_config(path: Path = DEFAULT_PERSONAL_CONFIG_PATH) -> PersonalConfig:
    try:
        raw = path.read_bytes()
    except FileNotFoundError as exc:
        raise RuntimeError(f"personal configuration is missing: {path}") from exc
    except OSError as exc:
        raise RuntimeError(f"personal configuration cannot be read: {path}") from exc
    try:
        payload = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise RuntimeError(f"personal configuration is invalid: {path}") from exc
    return PersonalConfig.model_validate(payload)


@lru_cache(maxsize=1)
def get_personal_config() -> PersonalConfig:
    return load_personal_config()
