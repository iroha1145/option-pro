from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.runtime_environment import RUNTIME_ENV_FILES, load_runtime_environment


load_runtime_environment()


class CatalystSettings(BaseSettings):
    """Settings for the local Catalyst read and analysis facade.

    MacroLens transport uses the canonical URL and server-only owner token from
    ``app.config.Settings``. This local view deliberately has no remote action,
    HMAC, nonce, or key-id capability fields.
    """

    cache_db_path: Path = Field(
        default=Path("/data/catalyst-cache.db"),
        alias="MACROLENS_CACHE_DB_PATH",
    )
    model: Literal["gpt-5.6-terra"] = "gpt-5.6-terra"
    reasoning: Literal["max"] = "max"

    model_config = SettingsConfigDict(
        env_file=tuple(str(path) for path in RUNTIME_ENV_FILES),
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )


@lru_cache
def get_catalyst_settings() -> CatalystSettings:
    return CatalystSettings()
