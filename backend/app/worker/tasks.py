from __future__ import annotations

import asyncio
import inspect
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.personal_config import get_personal_config

from .runtime import TaskResult, TaskSpec


DEFAULT_TASK_NAMES = (
    "breakout",
    "focus",
    "catalyst_sync",
    "ai_jobs",
    "maintenance",
)


def _canonical_sector_tickers() -> tuple[str, ...]:
    """Return only the checked-in market universe; ticker-shaped text is not trusted."""

    from app.services.sectors import SECTORS

    values = {
        ticker.strip().upper()
        for sector in SECTORS.values()
        for ticker in sector.get("tickers", [])
        if isinstance(ticker, str) and ticker.strip()
    }
    return tuple(sorted(values))


async def _call_local(method: Any, *args: Any, **kwargs: Any) -> Any:
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    return await asyncio.to_thread(method, *args, **kwargs)


async def _close_optional(resource: Any) -> None:
    if resource is None:
        return
    method = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if not callable(method):
        return
    result = method()
    if inspect.isawaitable(result):
        await result


async def _build_local_intelligence(
    config: Any,
    settings: Any,
    *,
    factory: Any | None = None,
) -> Any:
    from app.services.ai_jobs.repository import AIJobRepository
    from app.services.runtime_settings import (
        RuntimeSettingsStorageError,
        get_effective_runtime_settings,
    )

    if factory is None:
        from app.services.catalysts.local_intelligence import (
            LocalCatalystIntelligence,
        )

        factory = LocalCatalystIntelligence
    database_path = settings.macrolens_cache_db_path
    ai_repository = AIJobRepository(settings.openai_job_db_path)
    await asyncio.to_thread(ai_repository.initialize)
    try:
        runtime_settings = get_effective_runtime_settings()
    except RuntimeSettingsStorageError:
        runtime_settings = None
    if runtime_settings is None:
        intelligence_mode = "read"
        refresh_cooldown = 30
    else:
        intelligence_mode = (
            "scheduled"
            if runtime_settings.catalyst.scheduled_analysis_enabled
            else "manual"
            if runtime_settings.ai.manual_analysis_enabled
            else "read"
        )
        refresh_cooldown = int(
            runtime_settings.catalyst.manual_refresh_cooldown_seconds
        )
    intelligence = factory(
        database_path,
        ai_repository,
        mode=intelligence_mode,
        canonical_tickers=_canonical_sector_tickers(),
        model=config.ai.model,
        reasoning=config.ai.reasoning,
        max_queued=200,
        manual_refresh_cooldown_seconds=refresh_cooldown,
    )
    await _call_local(intelligence.initialize)
    return intelligence


class AIJobsTask:
    def __init__(self, owner_id: str, *, settings: Any) -> None:
        self.owner_id = f"{owner_id}:ai"
        self._settings = settings
        self._repository: Any = None

    async def __call__(self) -> TaskResult:
        if self._repository is None:
            from app.services.ai_jobs.repository import AIJobRepository

            self._repository = AIJobRepository(self._settings.openai_job_db_path)
            await asyncio.to_thread(self._repository.initialize)
        secret = self._settings.openai_api_key.get_secret_value().strip()
        if not secret:
            return TaskResult(
                status="disabled",
                details={"reason": "api_key_missing"},
                next_delay_seconds=30.0,
            )
        from app.services.ai_jobs.worker import run_configured_once

        processed, runtime_state = await run_configured_once(
            self._repository,
            self._settings,
            self.owner_id,
        )
        if runtime_state == "runtime_settings_unavailable":
            return TaskResult(
                status="degraded",
                error_code="runtime_settings_unavailable",
                details={
                    "reason": "runtime_settings_unavailable",
                    "processed": int(processed),
                },
                next_delay_seconds=0.5 if processed else 30.0,
            )
        if runtime_state == "analysis_disabled":
            return TaskResult(
                status="disabled",
                details={
                    "reason": "analysis_disabled",
                    "processed": int(processed),
                },
                next_delay_seconds=0.5 if processed else 30.0,
            )
        return TaskResult(
            status="idle",
            details={"processed": int(processed)},
            next_delay_seconds=0.5 if processed else 2.0,
        )


class CatalystSyncTask:
    def __init__(
        self,
        owner_id: str,
        *,
        settings: Any,
        personal_config: Any | None = None,
        etl_transport: Any | None = None,
        intelligence_factory: Any | None = None,
        initial_sync_complete: asyncio.Event | None = None,
    ) -> None:
        self.owner_id = f"{owner_id}:catalyst"
        self._runtime_settings = settings
        self._personal_config = personal_config or get_personal_config()
        self._etl_transport = etl_transport
        self._intelligence_factory = intelligence_factory
        self._initial_sync_complete = initial_sync_complete
        self._mode: str | None = None
        self._repository: Any = None
        self._client: Any = None
        self._service: Any = None
        self._intelligence: Any = None
        self._last_personal_sync_monotonic: float | None = None

    async def _prepare_personal(self) -> str:
        from app.services.catalysts.etl_client import EtlClientConfig, MacroLensEtlClient
        from app.services.catalysts.etl_repository import CatalystEtlRepository
        from app.services.catalysts.etl_sync import MacroLensIncrementalSync

        token = self._runtime_settings.internal_api_token.get_secret_value()
        client_config = EtlClientConfig(
            base_url=self._runtime_settings.macrolens_url,
            owner_token=token,
            ca_bundle=self._runtime_settings.macrolens_ca_bundle or None,
        )
        cache_path = self._runtime_settings.macrolens_cache_db_path
        repository = CatalystEtlRepository(cache_path)
        await asyncio.to_thread(repository.initialize)
        intelligence: Any = None
        client: Any = None
        try:
            intelligence = await _build_local_intelligence(
                self._personal_config,
                self._runtime_settings,
                factory=self._intelligence_factory,
            )
            client = MacroLensEtlClient(
                client_config,
                transport=self._etl_transport,
            )
            service = MacroLensIncrementalSync(client, repository)
        except Exception:
            if client is not None:
                await client.aclose()
            await _close_optional(intelligence)
            raise
        # Publish the initialized state only after every dependency succeeds.
        # A failed attempt therefore cannot leave a cached mode with missing
        # client or service objects.
        self._repository = repository
        self._client = client
        self._service = service
        self._intelligence = intelligence
        self._mode = "personal"
        return self._mode

    async def _prepare(self) -> str:
        if self._mode == "personal":
            return self._mode
        if not self._runtime_settings.personal_etl_enabled:
            return "disabled"
        return await self._prepare_personal()

    @staticmethod
    def _error_code(error: Exception) -> str:
        code = str(getattr(error, "code", "") or "")
        if code and code.replace("_", "").isalnum() and code.islower():
            return code[:120]
        return "sync_failed"

    async def _run_personal(self) -> TaskResult:
        from app.services.runtime_settings import (
            RuntimeSettingsStorageError,
            get_effective_runtime_settings,
        )

        processed: list[str] = []
        errors: dict[str, str] = {}
        metrics: dict[str, dict[str, int | bool]] = {}
        manual_request: dict[str, Any] | None = None
        legacy_refresh_requested = False
        try:
            effective = get_effective_runtime_settings()
        except RuntimeSettingsStorageError:
            return TaskResult(
                status="degraded",
                error_code="runtime_settings_unavailable",
                details={
                    "processed": [],
                    "streams": {},
                    "refresh_requested": False,
                    "errors": {
                        "runtime_settings": "runtime_settings_unavailable"
                    },
                },
                next_delay_seconds=30.0,
            )

        if hasattr(self._intelligence, "manual_refresh_cooldown_seconds"):
            self._intelligence.manual_refresh_cooldown_seconds = int(
                effective.catalyst.manual_refresh_cooldown_seconds
            )
        if effective.catalyst.manual_refresh_enabled:
            try:
                raw_request = await _call_local(
                    self._intelligence.consume_refresh_requested
                )
                if isinstance(raw_request, dict):
                    manual_request = raw_request
                else:
                    legacy_refresh_requested = bool(raw_request)
            except Exception as exc:
                errors["refresh_request"] = self._error_code(exc)

        sync_seconds = float(effective.catalyst.sync_seconds)
        clock = time.monotonic()
        scheduled_due = (
            self._last_personal_sync_monotonic is None
            or clock - self._last_personal_sync_monotonic >= sync_seconds
        )
        requested_type = (
            str(manual_request.get("operation_type"))
            if manual_request is not None
            else "source_health"
            if legacy_refresh_requested
            else None
        )
        if not scheduled_due and requested_type is None and not errors:
            return TaskResult(
                status="idle",
                details={
                    "processed": [],
                    "streams": {},
                    "refresh_requested": False,
                },
                next_delay_seconds=2.0,
            )
        stream_operations = {
            "news": ("news", self._service.sync_news),
            "calendar": ("calendar", self._service.sync_calendar),
        }
        selected_streams = (
            [stream_operations[requested_type]]
            if requested_type in stream_operations
            else list(stream_operations.values())
        )
        for stream, operation in selected_streams:
            try:
                result = await operation()
            except Exception as exc:
                errors[stream] = self._error_code(exc)
                continue
            processed.append(stream)
            metrics[stream] = {
                "pages": int(result.pages),
                "records": int(result.records),
                "replayed": int(result.replayed),
                "complete": bool(result.complete),
                "watermark_sequence": int(result.watermark_sequence),
            }
            if stream == "news":
                metrics[stream]["deletes"] = int(result.deletes)

        projection = None
        if selected_streams:
            try:
                projection = await _call_local(
                    self._intelligence.reconcile,
                    allow_scheduled_jobs=False,
                )
            except Exception as exc:
                errors["local_intelligence"] = self._error_code(exc)
            else:
                processed.append("local_intelligence")
        if scheduled_due:
            self._last_personal_sync_monotonic = clock

        if manual_request is not None and hasattr(
            self._intelligence,
            "complete_refresh_request",
        ):
            error_code = next(iter(errors.values()), None)
            try:
                await _call_local(
                    self._intelligence.complete_refresh_request,
                    str(manual_request["request_id"]),
                    error_code=error_code,
                )
            except Exception as exc:
                errors["refresh_completion"] = self._error_code(exc)

        delay = 2.0
        details: dict[str, Any] = {
            "processed": processed,
            "streams": metrics,
            "refresh_requested": bool(
                manual_request is not None or legacy_refresh_requested
            ),
            "refresh_operation_type": requested_type,
            "refresh_request_id": (
                manual_request.get("request_id")
                if manual_request is not None
                else None
            ),
        }
        if isinstance(projection, dict):
            projection_metrics: dict[str, bool | int | float | None] = {}
            for index, (key, value) in enumerate(projection.items()):
                if index >= 16:
                    break
                if isinstance(value, (bool, int, float, type(None))):
                    projection_metrics[str(key)[:64]] = value
            details["local_intelligence"] = projection_metrics
        if errors:
            details["errors"] = errors
            return TaskResult(
                status="degraded",
                error_code="catalyst_sync_degraded",
                details=details,
                next_delay_seconds=delay,
            )
        return TaskResult(
            status="idle",
            details=details,
            next_delay_seconds=delay,
        )

    async def _run_once(self) -> TaskResult:
        mode = await self._prepare()
        if mode == "disabled":
            return TaskResult(status="disabled", next_delay_seconds=30.0)
        return await self._run_personal()

    async def __call__(self) -> TaskResult:
        try:
            return await self._run_once()
        finally:
            # Focus may start concurrently with this task. Release it after the
            # first ETL attempt even when setup, transport, or shutdown fails.
            if self._initial_sync_complete is not None:
                self._initial_sync_complete.set()

    async def aclose(self) -> None:
        await _close_optional(self._client)
        await _close_optional(self._intelligence)
        self._service = None
        self._client = None
        self._repository = None
        self._intelligence = None
        self._mode = None


class FocusTask:
    def __init__(
        self,
        owner_id: str,
        *,
        enabled: bool,
        settings: Any,
        personal_config: Any | None = None,
        intelligence_factory: Any | None = None,
        initial_sync_complete: asyncio.Event | None = None,
    ) -> None:
        self.owner_id = f"focus-producer:{owner_id}"
        self.enabled = enabled
        self._runtime_settings = settings
        self._personal_config = personal_config or get_personal_config()
        self._intelligence_factory = intelligence_factory
        self._initial_sync_complete = initial_sync_complete
        self._mode: str | None = None
        self._intelligence: Any = None

    async def _prepare_personal(self) -> str:
        from app.services.catalysts.etl_client import EtlClientConfig

        EtlClientConfig(
            base_url=self._runtime_settings.macrolens_url,
            owner_token=self._runtime_settings.internal_api_token.get_secret_value(),
            ca_bundle=self._runtime_settings.macrolens_ca_bundle or None,
        )
        intelligence = await _build_local_intelligence(
            self._personal_config,
            self._runtime_settings,
            factory=self._intelligence_factory,
        )
        # Do not cache a mode until local storage and intelligence are ready.
        self._intelligence = intelligence
        self._mode = "personal"
        return self._mode

    async def _prepare(self) -> str:
        if not self.enabled:
            return "disabled"
        if self._mode == "personal":
            return self._mode
        if not self._runtime_settings.personal_etl_enabled:
            return "disabled"
        return await self._prepare_personal()

    async def _run_personal(self) -> TaskResult:
        from app.services.runtime_settings import (
            RuntimeSettingsStorageError,
            get_effective_runtime_settings,
        )

        try:
            effective = get_effective_runtime_settings()
        except RuntimeSettingsStorageError:
            return TaskResult(
                status="degraded",
                error_code="runtime_settings_unavailable",
                details={"result": "runtime_settings_unavailable"},
                next_delay_seconds=float(
                    self._personal_config.catalyst.focus_seconds
                ),
            )

        delay = float(effective.catalyst.focus_seconds)
        if not effective.catalyst.scheduled_analysis_enabled:
            if hasattr(self._intelligence, "mode"):
                self._intelligence.mode = (
                    "manual" if effective.ai.manual_analysis_enabled else "read"
                )
            return TaskResult(
                status="idle",
                details={"result": "not_scheduled", "queued": 0, "skipped": 0},
                next_delay_seconds=delay,
            )
        if hasattr(self._intelligence, "mode"):
            self._intelligence.mode = "scheduled"
        payload = await _call_local(
            lambda: self._intelligence.run_scheduled(
                scheduled_times_et=tuple(effective.catalyst.scheduled_times_et)
            )
        )
        payload = payload if isinstance(payload, dict) else {}
        return TaskResult(
            status="idle",
            details={
                "result": "scheduled",
                "queued": max(0, int(payload.get("queued") or 0)),
                "skipped": max(0, int(payload.get("skipped") or 0)),
            },
            next_delay_seconds=delay,
        )

    async def __call__(self) -> TaskResult:
        if self._initial_sync_complete is not None:
            await self._initial_sync_complete.wait()
        mode = await self._prepare()
        if mode == "disabled":
            return TaskResult(status="disabled", next_delay_seconds=30.0)
        return await self._run_personal()

    async def aclose(self) -> None:
        await _close_optional(self._intelligence)
        self._intelligence = None
        self._mode = None


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


def build_default_tasks(owner_id: str, *, settings: Any) -> tuple[TaskSpec, ...]:
    config = get_personal_config()
    ai = AIJobsTask(owner_id, settings=settings)
    initial_sync_complete = asyncio.Event()
    catalyst = CatalystSyncTask(
        owner_id,
        settings=settings,
        personal_config=config,
        initial_sync_complete=initial_sync_complete,
    )
    focus = FocusTask(
        owner_id,
        enabled=config.catalyst_sync_enabled,
        settings=settings,
        personal_config=config,
        initial_sync_complete=initial_sync_complete,
    )
    breakout = BreakoutTask(owner_id)
    maintenance = MaintenanceTask(
        {
            "optix": settings.breakout_db_path,
            "catalyst-cache": settings.macrolens_cache_db_path,
            "ai-jobs": settings.openai_job_db_path,
            "optix-worker": settings.optix_worker_db_path,
        },
        destination=settings.optix_backup_dir,
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
            "catalyst_sync",
            catalyst,
            interval_seconds=float(config.catalyst.sync_seconds),
            enabled=config.catalyst_sync_enabled,
            timeout_seconds=120.0,
            close=catalyst.aclose,
        ),
        TaskSpec(
            "focus",
            focus,
            interval_seconds=float(config.catalyst.focus_seconds),
            enabled=config.catalyst_sync_enabled,
            timeout_seconds=1200.0,
            close=focus.aclose,
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
    "DEFAULT_TASK_NAMES",
    "build_default_tasks",
]
