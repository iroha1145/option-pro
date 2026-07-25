"""Single-root filesystem layout for the personal runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


DEFAULT_DATA_DIR = Path("/data")


def _absolute_path(value: str | Path, *, name: str) -> Path:
    path = Path(value).expanduser()
    if str(value).startswith("file:") or not path.is_absolute() or ".." in path.parts:
        raise ValueError(
            f"{name} must be an absolute filesystem path without parent traversal"
        )
    return path


def data_dir(value: str | Path | None = None) -> Path:
    """Resolve the only runtime path setting without creating directories."""

    if value is None:
        raw = os.environ.get("DATA_DIR", "").strip()
        value = raw or DEFAULT_DATA_DIR
    return _absolute_path(value, name="DATA_DIR")


def explicit_data_path(value: str | Path, *, name: str) -> Path:
    """Validate a programmatic test override without making it an env setting."""

    return _absolute_path(value, name=name)


@dataclass(frozen=True)
class DataPaths:
    root: Path
    ai_jobs_db: Path
    catalyst_cache_db: Path
    optix_db: Path
    worker_db: Path
    worker_lock: Path
    watchlist_snapshot: Path
    strength_snapshot: Path
    public_home_snapshot: Path
    backups_dir: Path
    runtime_settings: Path
    accounts_db: Path


def get_data_paths(value: str | Path | None = None) -> DataPaths:
    root = data_dir(value)
    return DataPaths(
        root=root,
        ai_jobs_db=root / "ai-jobs.db",
        catalyst_cache_db=root / "catalyst-cache.db",
        optix_db=root / "optix.db",
        worker_db=root / "optix-worker.db",
        worker_lock=root / "optix-worker.lock",
        watchlist_snapshot=root / "watchlist-snapshot-v1.json",
        strength_snapshot=root / "strength-snapshot-v1.json",
        public_home_snapshot=root / "public-home-snapshot-v1.json",
        backups_dir=root / "backups",
        runtime_settings=root / "runtime-settings.json",
        accounts_db=root / "accounts.db",
    )


__all__ = [
    "DEFAULT_DATA_DIR",
    "DataPaths",
    "data_dir",
    "explicit_data_path",
    "get_data_paths",
]
