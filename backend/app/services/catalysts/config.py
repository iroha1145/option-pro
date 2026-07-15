from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import PrivateAttr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.data_paths import explicit_data_path, get_data_paths
from app.runtime_environment import RUNTIME_ENV_FILES, load_runtime_environment


load_runtime_environment()


class CatalystSettings(BaseSettings):
    """Settings for the local Catalyst read and analysis facade.

    MacroLens transport uses the canonical URL and server-only owner token from
    ``app.config.Settings``. This local view deliberately has no remote action,
    HMAC, nonce, or key-id capability fields.
    """

    _cache_db_path_override: Path | None = PrivateAttr(default=None)
    model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    reasoning: Literal["max"] = "max"

    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in RUNTIME_ENV_FILES),
        env_file_encoding="utf-8",
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
