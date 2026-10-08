from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import PrivateAttr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data_paths import explicit_data_path, get_data_paths
from app.runtime_environment import load_runtime_environment


load_runtime_environment()


class CatalystSettings(BaseSettings):
    """Settings for the local Catalyst read and analysis facade.

    MacroLens transport uses the canonical URL and server-only owner token from
    ``app.config.Settings``. This local view deliberately has no remote action,
    HMAC, nonce, or key-id capability fields.
    """

    _cache_db_path_override: Path | None = PrivateAttr(default=None)
    model: Literal["claude-haiku-5-5", "gpt-5.6-terra"] = "claude-haiku-5-5"
    reasoning: Literal["xhigh", "max"] = "xhigh"

    @model_validator(mode="after")
    def _supported_analysis_identity(self) -> "CatalystSettings":
        if (self.model, self.reasoning) not in {
            ("claude-haiku-5-5", "xhigh"), ("gpt-5.6-terra", "max"),
        }:
            raise ValueError("unsupported catalyst model and reasoning")
        return self

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    def __init__(
        self,
        *,
        cache_db_path: str | Path | None = None,
        **values: Any,
    ) -> None:
        super().__init__(**values)
        if cache_db_path is not None:
            self._cache_db_path_override = explicit_data_path(
                cache_db_path,
                name="cache_db_path",
            )

    @property
    def cache_db_path(self) -> Path:
        return self._cache_db_path_override or get_data_paths().catalyst_cache_db


@lru_cache
def get_catalyst_settings() -> CatalystSettings:
    return CatalystSettings()
