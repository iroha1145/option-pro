from __future__ import annotations

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


class StrictConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class FeatureConfig(StrictConfigModel):
    breakout_enabled: bool = True
    catalyst_mode: Literal["off", "read", "manual", "scheduled"] = "read"


class AIConfig(StrictConfigModel):
    model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    reasoning: Literal["max"] = "max"
    max_concurrency: Literal[1] = 1
    daily_max_jobs: int = Field(default=4, ge=1, le=100)
    execution_mode: Literal["background"] = "background"

class CatalystConfig(StrictConfigModel):
    sync_seconds: int = Field(default=120, ge=30, le=86_400)
    focus_seconds: int = Field(default=1800, ge=300, le=86_400)
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


class StorageConfig(StrictConfigModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    backup_keep: int = Field(default=7, ge=1, le=100)


class PersonalConfig(StrictConfigModel):
    features: FeatureConfig = Field(default_factory=FeatureConfig)
    ai: AIConfig = Field(default_factory=AIConfig)
    catalyst: CatalystConfig = Field(default_factory=CatalystConfig)
    breakout: BreakoutConfig = Field(default_factory=BreakoutConfig)
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
