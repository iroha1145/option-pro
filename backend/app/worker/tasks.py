from __future__ import annotations

import asyncio
import os
import sqlite3
from pathlib import Path
from typing import Any

from app.personal_config import get_personal_config

from .runtime import TaskResult, TaskSpec


class AIJobsTask:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = f"{owner_id}:ai"
        self._settings: Any = None
        self._repository: Any = None

    async def __call__(self) -> TaskResult:
        if self._settings is None:
            from app.config import get_settings
            from app.services.ai_jobs.repository import AIJobRepository

            self._settings = get_settings()
            self._repository = AIJobRepository(self._settings.openai_job_db_path)
            await asyncio.to_thread(self._repository.initialize)
        secret = self._settings.openai_api_key.get_secret_value().strip()
        if not secret:
            return TaskResult(
                status="disabled",
                details={"reason": "api_key_missing"},
                next_delay_seconds=30.0,
            )
        from app.services.ai_jobs.worker import run_once

        processed = await run_once(self._repository, self._settings, self.owner_id)
        return TaskResult(
            status="idle",
            details={"processed": int(processed)},
            next_delay_seconds=0.5 if processed else 2.0,
        )


class CatalystSyncTask:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = f"{owner_id}:catalyst"
        self._settings: Any = None
        self._repository: Any = None
        self._client: Any = None
        self._service: Any = None

    async def _prepare(self) -> bool:
        if self._settings is not None:
            return bool(self._settings.enabled)
        from app.services.catalysts.client import MacroLensClient
        from app.services.catalysts.config import get_catalyst_settings
        from app.services.catalysts.repository import CatalystRepository
        from app.services.catalysts.sync_service import CatalystSyncService

        self._settings = get_catalyst_settings()
        if not self._settings.enabled or self._settings.catalyst_mode == "disabled":
            return False
        self._repository = CatalystRepository(self._settings.cache_db_path)
        await asyncio.to_thread(self._repository.initialize)
        self._client = MacroLensClient(self._settings)
        await self._client.__aenter__()
        self._service = CatalystSyncService(
            self._settings,
            self._repository,
            self._client,
            worker_id=self.owner_id,
        )
        return True

    async def __call__(self) -> TaskResult:
        if not await self._prepare():
            return TaskResult(status="disabled", next_delay_seconds=30.0)
        payload = await self._service.run_once()
        if payload.get("status") == "standby":
            return TaskResult(
                status="degraded",
                error_code="legacy_worker_locked",
                details={"processed": []},
                next_delay_seconds=5.0,
            )
        return TaskResult(
            status="idle",
            details={
                "processed": list(payload.get("processed") or [])[:8],
                "jobs": int(payload.get("jobs") or 0),
            },
            next_delay_seconds=1.0,
        )

    async def aclose(self) -> None:
        if self._service is not None:
            self._service.release()
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
        self._service = None
        self._client = None


class FocusTask:
    def __init__(self, owner_id: str, *, enabled: bool) -> None:
        self.owner_id = f"focus-producer:{owner_id}"
        self.enabled = enabled
        self._producer: Any = None

    async def _prepare(self) -> bool:
        if not self.enabled:
            return False
        if self._producer is not None:
            return True
        from app.services.catalysts.focus_config import get_focus_context_settings
        from app.services.catalysts.focus_worker import FocusContextProducer
        from app.services.catalysts.repository import CatalystRepository

        settings = get_focus_context_settings().model_copy(
            update={"producer_enabled": True}
        )
        repository = CatalystRepository(settings.cache_db_path)
        await asyncio.to_thread(repository.initialize)
        self._producer = FocusContextProducer(
            settings=settings,
            repository=repository,
            owner_id=self.owner_id,
        )
        return True

    async def __call__(self) -> TaskResult:
        if not await self._prepare():
            return TaskResult(status="disabled", next_delay_seconds=30.0)
        payload = await self._producer.run_once()
        status = str(payload.get("status") or "unavailable")
        if status in {"unavailable", "locked"}:
            return TaskResult(
                status="degraded",
                error_code=(
                    "legacy_worker_locked" if status == "locked" else "focus_unavailable"
                ),
                details={"result": status},
            )
        return TaskResult(
            status="degraded" if status == "degraded" else "idle",
            error_code="focus_degraded" if status == "degraded" else None,
            details={"result": status, "revision": payload.get("revision")},
        )


class BreakoutTask:
    def __init__(self, owner_id: str) -> None:
        self.owner_id = f"{owner_id}:breakout"
        self._settings: Any = None
        self._repository: Any = None
        self._service: Any = None

    async def _prepare(self) -> bool:
        if self._settings is not None:
            return bool(self._settings.enabled)
        from app.services.breakouts.config import get_breakout_settings
        from app.services.breakouts.repository import BreakoutRepository
        from app.services.breakouts.service import BreakoutRadarService

        self._settings = get_breakout_settings()
        if not self._settings.enabled:
            return False
        self._repository = BreakoutRepository(self._settings.db_path)
        self._service = BreakoutRadarService(self._settings)
        return True

    def _interval(self, session: str | None) -> float:
        if session == "premarket":
            return float(self._settings.scan_interval_premarket_seconds)
        if session == "regular":
            return float(self._settings.scan_interval_regular_seconds)
        return float(self._settings.scan_interval_closed_seconds)

    async def __call__(self) -> TaskResult:
        if not await self._prepare():
            return TaskResult(status="disabled", next_delay_seconds=30.0)
        from app.services.breakouts.worker import BreakoutWorker

        worker = BreakoutWorker(
            self._settings,
            self._repository,
            scan_service=self._service,
            owner_id=self.owner_id,
        )
        payload = await worker.run_once()
        status = str(payload.get("status") or "degraded")
        session = payload.get("session")
        delay = self._interval(str(session) if session else None)
        if status in {"degraded", "locked"}:
            return TaskResult(
                status="degraded",
                error_code=(
                    "legacy_worker_locked"
                    if status == "locked"
                    else str(payload.get("error_code") or "breakout_degraded")
                ),
                details={"result": status, "session": session},
                next_delay_seconds=min(delay, 300.0),
            )
        if status == "paused":
            return TaskResult(
                status="paused",
                details={"reason": payload.get("reason"), "session": session},
                next_delay_seconds=delay,
            )
        return TaskResult(
            status="idle" if status == "completed" else "disabled",
            details={
                "result": status,
                "scan_run_id": payload.get("scan_run_id"),
                "event_count": payload.get("event_count"),
            },
            next_delay_seconds=delay,
        )


class MaintenanceTask:
    def __init__(
        self,
        databases: dict[str, Path],
        *,
        destination: Path,
        keep: int,
    ) -> None:
        self.databases = dict(databases)
        self.destination = destination
        self.keep = keep

    async def __call__(self) -> TaskResult:
        from app.tools.sqlite_backup import BackupError, backup_database

        completed: list[str] = []
        skipped: list[str] = []
        failed: list[str] = []
        for label, path in self.databases.items():
            if not path.is_file():
                skipped.append(label)
                continue
            try:
                await asyncio.to_thread(
                    backup_database,
                    path,
                    self.destination,
                    label=label,
                    keep=self.keep,
                )
            except (BackupError, OSError, sqlite3.Error):
                failed.append(label)
            else:
                completed.append(label)
        details = {
            "backed_up": completed,
            "skipped_missing": skipped,
            "failed": failed,
        }
        if failed:
            return TaskResult(
                status="degraded",
                error_code="backup_failed",
                details=details,
                next_delay_seconds=300.0,
            )
        return TaskResult(status="idle", details=details)


def _path_from_env(name: str, default: str) -> Path:
    value = Path(os.environ.get(name, default)).expanduser()
    if not value.is_absolute() or ".." in value.parts:
        raise ValueError(f"{name} must be an absolute path without parent traversal")
    return value


def build_default_tasks(owner_id: str, *, worker_db_path: Path) -> tuple[TaskSpec, ...]:
    config = get_personal_config()
    ai = AIJobsTask(owner_id)
    catalyst = CatalystSyncTask(owner_id)
    focus = FocusTask(owner_id, enabled=config.catalyst_sync_enabled)
    breakout = BreakoutTask(owner_id)
    maintenance = MaintenanceTask(
        {
            "optix": _path_from_env("BREAKOUT_DB_PATH", "/data/optix.db"),
            "catalyst-cache": _path_from_env(
                "MACROLENS_CACHE_DB_PATH", "/data/catalyst-cache.db"
            ),
            "ai-jobs": _path_from_env("OPENAI_JOB_DB_PATH", "/data/ai-jobs.db"),
            "optix-worker": worker_db_path,
        },
        destination=_path_from_env("OPTIX_BACKUP_DIR", "/data/backups"),
        keep=config.storage.backup_keep,
    )
    return (
        TaskSpec(
            "breakout",
            breakout,
            interval_seconds=float(config.breakout.regular_seconds),
            enabled=config.features.breakout_enabled,
            timeout_seconds=900.0,
        ),
        TaskSpec(
            "focus",
            focus,
            interval_seconds=float(config.catalyst.focus_seconds),
            enabled=config.catalyst_sync_enabled,
            timeout_seconds=1200.0,
        ),
        TaskSpec(
            "catalyst_sync",
            catalyst,
            interval_seconds=1.0,
            enabled=config.catalyst_sync_enabled,
            timeout_seconds=120.0,
            close=catalyst.aclose,
        ),
        TaskSpec(
            "ai_jobs",
            ai,
            interval_seconds=2.0,
            timeout_seconds=2000.0,
            drain_on_shutdown=True,
        ),
        TaskSpec(
            "maintenance",
            maintenance,
            interval_seconds=21_600.0,
            timeout_seconds=1800.0,
            failure_backoff_seconds=300.0,
            max_backoff_seconds=3600.0,
        ),
    )


__all__ = [
    "AIJobsTask",
    "BreakoutTask",
    "CatalystSyncTask",
    "FocusTask",
    "MaintenanceTask",
    "build_default_tasks",
]
