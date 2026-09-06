from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from app.data_paths import get_data_paths
from app.execution_limits import BREAKOUT_TASK_TIMEOUT_SECONDS
from app.personal_config import get_personal_config

from .runtime import TaskResult, TaskSpec


DEFAULT_TASK_NAMES = (
    "breakout",
    "focus",
    "catalyst_sync",
    "ai_jobs",
    "maintenance",
    "stock_directory",
    "public_home",
    "earnings_analysis",
    "macro_conditions",
    "focus_refresh",
    "strength_refresh",
    "breakout_refresh",
    "retention",
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
    task = asyncio.create_task(asyncio.to_thread(method, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError as cancelled:
        # A cancelled asyncio wrapper cannot stop a thread already inside a
        # SQLite transaction. Keep the worker lease and process lock until the
        # local operation has really left its transaction boundary.
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        try:
            task.result()
        except BaseException:
            pass
        raise cancelled


async def _close_optional(resource: Any) -> None:
    if resource is None:
        return
    method = getattr(resource, "aclose", None) or getattr(resource, "close", None)
    if not callable(method):
        return
    result = method()
    if inspect.isawaitable(result):
        await result


def _timestamp_text(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _personal_analysis_permissions(config: Any) -> tuple[bool, bool]:
    features = getattr(config, "features", None)
    mode = getattr(features, "catalyst_mode", None)
    if mode is not None:
        return mode in {"manual", "scheduled"}, mode == "scheduled"
    return (
        bool(getattr(config, "catalyst_manual_enabled", False)),
        bool(getattr(config, "catalyst_scheduled_enabled", False)),
    )


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
    await _call_local(ai_repository.initialize)
    try:
        runtime_settings = get_effective_runtime_settings()
    except RuntimeSettingsStorageError:
        runtime_settings = None
    if runtime_settings is None:
        intelligence_mode = "read"
        refresh_cooldown = 30
    else:
        mode_allows_manual, mode_allows_scheduled = (
            _personal_analysis_permissions(config)
        )
        manual_analysis_enabled = bool(
            mode_allows_manual and runtime_settings.ai.manual_analysis_enabled
        )
        scheduled_analysis_enabled = bool(
            mode_allows_scheduled
            and runtime_settings.catalyst.scheduled_analysis_enabled
        )
        intelligence_mode = (
            "scheduled"
            if scheduled_analysis_enabled
            else "manual"
            if manual_analysis_enabled
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
    def __init__(
        self,
        owner_id: str,
        *,
        settings: Any | None = None,
        personal_config: Any | None = None,
    ) -> None:
        self.owner_id = f"{owner_id}:ai"
        self._personal_config = personal_config or get_personal_config()
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
            personal_config=self._personal_config,
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


class EarningsAnalysisTask:
    """Queue real earnings-impact jobs within the configured report window."""

    def __init__(
        self,
        owner_id: str,
        *,
        settings: Any,
        personal_config: Any | None = None,
        repository: Any | None = None,
        builder: Callable[[date], Any] | None = None,
        runtime_settings_reader: Callable[[], Any] | None = None,
        today: Callable[[], date] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.owner_id = f"{owner_id}:earnings-analysis"
        self._settings = settings
        self._personal_config = personal_config or get_personal_config()
        self._repository = repository
        self._builder = builder
        self._runtime_settings_reader = runtime_settings_reader
        self._today = today
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def _effective_settings(self) -> Any:
        if self._runtime_settings_reader is not None:
            return await _call_local(self._runtime_settings_reader)
        from app.services.runtime_settings import get_effective_runtime_settings

        return await _call_local(get_effective_runtime_settings)

    async def _prepare_repository(self) -> Any:
        if self._repository is None:
            from app.services.ai_jobs.repository import AIJobRepository

            self._repository = AIJobRepository(self._settings.openai_job_db_path)
        await _call_local(self._repository.initialize)
        return self._repository

    @staticmethod
    def _same_report_date(row: Mapping[str, Any] | None, report_date: str) -> bool:
        if not row:
            return False
        try:
            payload = json.loads(str(row.get("payload_json") or ""))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        return (
            isinstance(payload, dict)
            and str(payload.get("earnings_date") or "") == report_date
        )

    @staticmethod
    def _updated_before_utc_date(
        row: Mapping[str, Any] | None,
        utc_date: date,
    ) -> bool:
        if not row:
            return False
        raw = str(
            row.get("updated_at")
            or row.get("completed_at")
            or row.get("created_at")
            or ""
        )
        if not raw:
            return False
        try:
            updated = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return False
        if updated.tzinfo is None or updated.utcoffset() is None:
            updated = updated.replace(tzinfo=timezone.utc)
        return updated.astimezone(timezone.utc).date() < utc_date

    async def _run(self, *, manual: bool) -> TaskResult:
        from app.api import earnings
        from app.services.ai_jobs import runtime as ai_runtime
        from app.services.ai_jobs.models import (
            normalize_earnings_analysis_payload,
        )
        from app.services.runtime_settings import RuntimeSettingsStorageError

        try:
            effective = await self._effective_settings()
        except RuntimeSettingsStorageError:
            return TaskResult(
                status="degraded",
                error_code="runtime_settings_unavailable",
                details={"reason": "runtime_settings_unavailable", "queued": 0},
            )
        repository = await self._prepare_repository()
        retention_deleted = await _call_local(
            repository.prune_earnings_retention,
            now=self._now(),
            retention_days=30,
        )
        if manual and not bool(effective.ai.manual_analysis_enabled):
            return TaskResult(
                status="disabled",
                details={"reason": "manual_analysis_disabled", "queued": 0},
            )
        if not manual and not bool(
            effective.earnings.scheduled_analysis_enabled
        ):
            return TaskResult(
                status="idle",
                details={"result": "not_scheduled", "queued": 0},
            )

        capability = ai_runtime.capability_status(self._settings)
        if not bool(capability.get("supported")):
            return TaskResult(
                status="disabled",
                details={
                    "reason": str(
                        capability.get("status") or "ai_runtime_unavailable"
                    ),
                    "queued": 0,
                },
            )

        observed = (
            self._today()
            if self._today is not None
            else earnings._market_today()
        )
        if self._builder is not None:
            snapshot = await _call_local(self._builder, observed)
        else:
            snapshot = await earnings._read_current_upcoming_earnings_snapshot(
                observed
            )
            if snapshot is None:
                snapshot = await _call_local(
                    earnings._build_upcoming_earnings,
                    observed,
                )
        if (
            not isinstance(snapshot, Mapping)
            or snapshot.get("data_limited") is not False
            or snapshot.get("source_status") != "active"
        ):
            # A provider outage can leave only the curated Yahoo supplement.
            # Do not mistake that partial hot-list for the full-market scope.
            return TaskResult(
                status="degraded",
                error_code="earnings_snapshot_incomplete",
                details={
                    "reason": "earnings_snapshot_incomplete",
                    "queued": 0,
                },
            )
        rows = (
            list(snapshot.get("earnings") or [])
            if isinstance(snapshot, Mapping)
            else []
        )
        lookahead_days = int(effective.earnings.lookahead_days)
        eligible: list[dict[str, Any]] = []
        for value in rows:
            if not isinstance(value, Mapping):
                continue
            try:
                days_until = int(value.get("days_until"))
            except (TypeError, ValueError):
                continue
            has_actual = (
                value.get("eps_actual") is not None
                or value.get("revenue_actual") is not None
            )
            if (
                0 <= days_until <= lookahead_days
                or (has_actual and -30 <= days_until < 0)
            ):
                eligible.append(dict(value))
        eligible.sort(
            key=lambda item: (
                0
                if (
                    item.get("eps_actual") is not None
                    or item.get("revenue_actual") is not None
                )
                else 1,
                int(item.get("days_until") or 0),
                str(item.get("ticker") or ""),
            )
        )

        schema_version, schema_sha256 = ai_runtime.schema_identity(
            "earnings_impact"
        )
        queued = 0
        existing = 0
        invalid = 0
        skipped_final_without_pre_release = 0
        scheduled_pre_release_active = (
            0
            if manual
            else await _call_local(
                repository.active_scheduled_earnings_pre_release_count
            )
        )
        scheduled_pre_release_slots = max(
            0,
            ai_runtime.EARNINGS_PRE_RELEASE_ACTIVE_LIMIT
            - scheduled_pre_release_active,
        )
        skipped_pre_release_capacity = 0
        queue_full = False
        utc_date = self._now().astimezone(timezone.utc).date()
        for item in eligible:
            raw_payload = {
                "ticker": str(item.get("ticker") or ""),
                "name": str(item.get("name") or ""),
                "sector": str(item.get("sector") or ""),
                "earnings_date": str(item.get("earnings_date") or item.get("date") or ""),
                "year": item.get("year"),
                "quarter": item.get("quarter"),
                "eps_estimate": item.get("eps_estimate"),
                "eps_actual": item.get("eps_actual"),
                "revenue_estimate": item.get("revenue_estimate"),
                "revenue_actual": item.get("revenue_actual"),
                "market_cap": item.get("market_cap"),
                "release_status": item.get("release_status"),
            }
            has_actual = (
                raw_payload["eps_actual"] is not None
                or raw_payload["revenue_actual"] is not None
            )
            analysis_stage = (
                "post_release_final"
                if has_actual and not manual
                else "post_release_manual"
                if has_actual
                else "pre_release"
            )
            try:
                payload = normalize_earnings_analysis_payload(
                    raw_payload,
                    analysis_stage=analysis_stage,
                )
            except (TypeError, ValueError):
                invalid += 1
                continue
            if analysis_stage == "post_release_final":
                preliminary = await _call_local(
                    repository.latest_for_report,
                    payload["ticker"],
                    payload["report_id"],
                    analysis_stage="pre_release",
                    status="completed",
                    # Analyses written before report ids were bound still
                    # count as the preliminary run, otherwise a released
                    # report can never finalize.
                    legacy_report_date=payload.get("earnings_date"),
                    now=self._now(),
                )
                if not preliminary:
                    skipped_final_without_pre_release += 1
                    continue
            latest = await _call_local(
                repository.latest_for_report,
                payload["ticker"],
                payload["report_id"],
                analysis_stage=analysis_stage,
                now=self._now(),
            )
            latest_status = str((latest or {}).get("status") or "")
            retry_failed_report = bool(
                latest
                and (
                    latest_status in {"failed", "cancelled"}
                    or (
                        latest_status == "budget_blocked"
                        and self._updated_before_utc_date(latest, utc_date)
                    )
                )
            )
            # Duplicate suppression has to cover the released stages too.
            # Scoping it to pre-release let every scheduled cycle enqueue a
            # fresh finalization for an already-queued or budget-blocked
            # report: a single blocked attempt grew into dozens of identical
            # rows per report and drained the daily token budget. A completed
            # row still falls through so revised actuals can be re-analysed;
            # create_job deduplicates when the payload hash is unchanged.
            if latest and not retry_failed_report and (
                analysis_stage == "pre_release"
                or latest_status
                in {"pending", "queued", "in_progress", "budget_blocked"}
            ):
                existing += 1
                continue
            if (
                not manual
                and analysis_stage == "pre_release"
                and scheduled_pre_release_slots <= 0
            ):
                skipped_pre_release_capacity += 1
                continue
            try:
                queue_limit = self._settings.openai_job_max_queued
                if analysis_stage == "post_release_final":
                    # Final analyses must still enter when scheduled
                    # pre-release work and the manual reserve are saturated.
                    # The larger reserve remains a hard total cap.
                    queue_limit += ai_runtime.EARNINGS_FINAL_QUEUE_RESERVE
                _row, created = await _call_local(
                    repository.create_job,
                    job_type="earnings_impact",
                    payload=payload,
                    model=self._settings.openai_model,
                    reasoning=self._settings.openai_reasoning,
                    execution_mode=self._settings.openai_execution_mode,
                    prompt_version="earnings-impact-zh-cn-v5",
                    schema_version=schema_version,
                    schema_sha256=schema_sha256,
                    max_queued=queue_limit,
                    submission_source="manual" if manual else "scheduled",
                    priority=(
                        ai_runtime.EARNINGS_FINAL_PRIORITY
                        if analysis_stage == "post_release_final"
                        else ai_runtime.EARNINGS_OWNER_PRIORITY
                        if manual
                        else ai_runtime.EARNINGS_PRE_RELEASE_PRIORITY
                    ),
                    force_retry=retry_failed_report,
                )
            except RuntimeError as exc:
                if str(exc) == "ai_job_queue_full":
                    queue_full = True
                    break
                if str(exc) in {
                    "earnings_analysis_locked",
                    "earnings_finalization_in_progress",
                }:
                    existing += 1
                    continue
                raise
            queued += int(bool(created))
            existing += int(not created)
            if (
                created
                and not manual
                and analysis_stage == "pre_release"
            ):
                scheduled_pre_release_slots -= 1

        details = {
            "result": "queued" if manual else "scheduled",
            "range_start": observed.isoformat(),
            "range_end": (observed + timedelta(days=lookahead_days)).isoformat(),
            "lookahead_days": lookahead_days,
            "discovered": len(rows),
            "eligible": len(eligible),
            "queued": queued,
            "existing": existing,
            "invalid": invalid,
            "skipped_final_without_pre_release": (
                skipped_final_without_pre_release
            ),
            "pre_release_active_limit": (
                ai_runtime.EARNINGS_PRE_RELEASE_ACTIVE_LIMIT
            ),
            "pre_release_active_before": scheduled_pre_release_active,
            "skipped_pre_release_capacity": skipped_pre_release_capacity,
            "retention_deleted": int(retention_deleted),
        }
        if queue_full:
            return TaskResult(
                status="degraded",
                error_code="ai_job_queue_full",
                details={**details, "reason": "ai_job_queue_full"},
                next_delay_seconds=60.0,
            )
        return TaskResult(status="idle", details=details)

    async def run_for_actions(
        self,
        _actions: list[dict[str, Any]],
    ) -> TaskResult:
        return await self._run(manual=True)

    async def __call__(self) -> TaskResult:
        return await self._run(manual=False)


# 变更日志修剪的节流间隔：修剪是 cutoff 幂等操作，6 小时一次足以压住增长；
# 稳态一轮只有一个索引探测查询，代价可忽略。
_JOURNAL_PRUNE_INTERVAL_SECONDS = 6 * 3600.0


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
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
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
        self._last_journal_prune_monotonic: float | None = None

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
        if scheduled_due and "news" not in errors and "calendar" not in errors:
            self._last_personal_sync_monotonic = clock

        # 变更日志保留：同步成功的整点周期里按节流间隔修剪。hasattr 守卫让
        # 不带该能力的测试替身自然跳过（与 manual_refresh_cooldown 同范式）。
        journal_prune_metrics: dict[str, int] | None = None
        if (
            scheduled_due
            and "news" not in errors
            and hasattr(self._intelligence, "prune_journal")
            and (
                self._last_journal_prune_monotonic is None
                or clock - self._last_journal_prune_monotonic
                >= _JOURNAL_PRUNE_INTERVAL_SECONDS
            )
        ):
            retention_days = int(
                getattr(
                    getattr(self._personal_config, "catalyst", None),
                    "journal_retention_days",
                    30,
                )
                or 30
            )
            try:
                pruned = await _call_local(
                    self._intelligence.prune_journal,
                    retention_days=retention_days,
                )
            except Exception as exc:
                errors["journal_prune"] = self._error_code(exc)
            else:
                self._last_journal_prune_monotonic = clock
                processed.append("journal_prune")
                if isinstance(pruned, dict):
                    journal_prune_metrics = {
                        str(key)[:64]: int(value)
                        for index, (key, value) in enumerate(pruned.items())
                        if index < 8 and isinstance(value, int)
                    }

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
        if journal_prune_metrics is not None:
            details["journal_prune"] = journal_prune_metrics
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
                # Let the supervisor apply exponential backoff. A fixed 2s
                # retry used to spin the worker and hide a broken cursor.
                next_delay_seconds=None,
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
        settings: Any | None = None,
        personal_config: Any | None = None,
        intelligence_factory: Any | None = None,
        initial_sync_complete: asyncio.Event | None = None,
    ) -> None:
        self.owner_id = f"focus-producer:{owner_id}"
        self.enabled = enabled
        if settings is None:
            from app.config import get_settings

            settings = get_settings()
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
        mode_allows_manual, mode_allows_scheduled = _personal_analysis_permissions(
            self._personal_config
        )
        manual_analysis_enabled = bool(
            mode_allows_manual and effective.ai.manual_analysis_enabled
        )
        scheduled_analysis_enabled = bool(
            mode_allows_scheduled
            and effective.catalyst.scheduled_analysis_enabled
        )
        if not scheduled_analysis_enabled:
            if hasattr(self._intelligence, "mode"):
                self._intelligence.mode = (
                    "manual" if manual_analysis_enabled else "read"
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


class FocusRefreshTask:
    """Rebuild the shared full-watchlist snapshot on an explicit request."""

    def __init__(
        self,
        *,
        builder: Callable[..., Any] | None = None,
        writer: Callable[..., Any] | None = None,
        snapshot_path: Path | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._builder = builder
        self._writer = writer
        self._snapshot_path = snapshot_path
        self._clock = clock

    async def __call__(self) -> TaskResult:
        from app.api import stocks

        builder = self._builder or stocks._build_watchlist
        writer = self._writer or stocks._write_watchlist_snapshot
        path = self._snapshot_path or get_data_paths().watchlist_snapshot
        payload = await _call_local(builder)
        saved_at = float(self._clock())
        await _call_local(
            writer,
            path,
            payload=payload,
            saved_at=saved_at,
        )
        succeeded = (
            int(payload.get("succeeded") or 0)
            if isinstance(payload, dict)
            else 0
        )
        return TaskResult(
            status="idle",
            details={
                "result": "refreshed",
                "snapshot": path.name,
                "succeeded": max(0, succeeded),
                "completed_at": _timestamp_text(saved_at),
            },
        )


class StockDirectoryTask:
    """Warm and persist the complete Massive U.S. stock directory."""

    async def __call__(self) -> TaskResult:
        from app.api import stocks

        payload = await stocks._refresh_stock_directory()
        if (
            not isinstance(payload, dict)
            or not payload.get("items")
            or payload.get("provider") != "Massive"
            or payload.get("_stale") is True
        ):
            raise RuntimeError("stock_directory_unavailable")
        return TaskResult(
            status="idle",
            details={
                "result": "refreshed",
                "provider": str(payload.get("provider") or "Massive"),
                "count": max(0, int(payload.get("count") or 0)),
            },
        )


@dataclass
class _PublicHomeFailure:
    attempts: int
    retry_after: float
    baseline_saved_at: float | None


@dataclass
class _PublicHomeInflight:
    task: asyncio.Task[dict[str, Any]]
    parameters: dict[str, Any]
    baseline_saved_at: float | None
    started_at: float
    superseded: bool = False


class PublicHomeTask:
    """Refresh restart-safe anonymous home-page data without model work."""

    _INTERVAL_FIELDS = {
        "watchlist": "watchlist_seconds",
        "indices": "indices_seconds",
        "focus_overview": "overview_seconds",
        "focus_chart": "chart_seconds",
        "focus_signals": "signals_seconds",
        "market_signals": "signals_seconds",
        "breakout_lead_chart": "chart_seconds",
        "earnings": "earnings_seconds",
        "unusual": "unusual_seconds",
        "cta_trend": "cta_seconds",
    }
    _HEAVY_RESOURCES = ("earnings", "unusual")

    def __init__(
        self,
        config: Any,
        *,
        builders: Mapping[str, Callable[..., Any]] | None = None,
        reader: Callable[..., Any] | None = None,
        writer: Callable[..., Any] | None = None,
        snapshot_path: Path | None = None,
        watchlist_reader: Callable[..., Any] | None = None,
        watchlist_writer: Callable[..., Any] | None = None,
        watchlist_path: Path | None = None,
        breakout_lead_ticker_reader: Callable[..., Any] | None = None,
        stock_data_refresh: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._config = config
        self._builders = dict(builders or {})
        self._reader = reader
        self._writer = writer
        self._snapshot_path = snapshot_path
        self._watchlist_reader = watchlist_reader
        self._watchlist_writer = watchlist_writer
        self._watchlist_path = watchlist_path
        self._breakout_lead_ticker_reader = breakout_lead_ticker_reader
        self._stock_data_refresh = stock_data_refresh
        self._stock_data_error = False
        self._clock = clock
        self._failures: dict[str, _PublicHomeFailure] = {}
        self._inflight: dict[str, _PublicHomeInflight] = {}
        self._next_heavy = "earnings"

    async def _default_build(
        self,
        resource: str,
        parameters: Mapping[str, Any],
    ) -> Any:
        from app.api import earnings, market, options, signals, stocks

        if resource == "watchlist":
            return await stocks._build_watchlist()
        if resource == "indices":
            return await market._build_indices()
        if resource == "focus_overview":
            return await stocks._stock_overview_impl(str(parameters["ticker"]))
        if resource == "focus_chart":
            return await stocks._stock_chart_impl(
                str(parameters["ticker"]),
                str(parameters["range"]),
                str(parameters["adjustment"]),
            )
        if resource == "focus_signals":
            return await stocks._build_stock_signals(str(parameters["ticker"]))
        if resource == "market_signals":
            return await signals._build_market_signals_payload()
        if resource == "cta_trend":
            return await market._build_cta_trend()
        if resource == "breakout_lead_chart":
            return await stocks._stock_chart_impl(
                str(parameters["ticker"]),
                str(parameters["range"]),
                str(parameters["adjustment"]),
            )
        if resource == "earnings":
            market_date = datetime.fromisoformat(
                str(parameters["market_date"])
            ).date()
            return await earnings._build_upcoming_earnings(market_date)
        if resource == "unusual":
            return await options._unusual_activity_impl(
                str(parameters["type"]),
                float(parameters["min_vol_oi"]),
            )
        raise ValueError("unknown public home resource")

    @staticmethod
    def _default_breakout_lead_ticker() -> str | None:
        try:
            from app.services.breakouts.config import get_breakout_settings
            from app.services.breakouts.repository import BreakoutRepository

            settings = get_breakout_settings()
            if not settings.enabled:
                return None
            scan = BreakoutRepository(
                settings.db_path,
                read_only=True,
            ).latest_completed_scan()
            events = scan.get("events") if isinstance(scan, Mapping) else None
            first = events[0] if isinstance(events, list) and events else None
            ticker = first.get("ticker") if isinstance(first, Mapping) else None
            return str(ticker).strip().upper() if ticker else None
        except (FileNotFoundError, OSError, RuntimeError, ValueError, sqlite3.Error):
            return None

    async def _breakout_lead_ticker(self) -> str | None:
        reader = (
            self._breakout_lead_ticker_reader
            or self._default_breakout_lead_ticker
        )
        value = await _call_local(reader)
        if not isinstance(value, str) or not value.strip():
            return None
        from app.public_home_snapshot import breakout_lead_chart_parameters

        try:
            return str(
                breakout_lead_chart_parameters(value)["ticker"]
            )
        except ValueError:
            return None

    async def _build(
        self,
        resource: str,
        parameters: Mapping[str, Any],
    ) -> Any:
        builder = self._builders.get(resource)
        if builder is None:
            return await self._default_build(resource, parameters)
        return await _call_local(builder, dict(parameters))

    def _interval(self, resource: str) -> float:
        return float(getattr(self._config, self._INTERVAL_FIELDS[resource]))

    @staticmethod
    def _saved_at(entry: Any) -> float | None:
        if not isinstance(entry, Mapping):
            return None
        value = entry.get("saved_at")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    def _is_due(
        self,
        resource: str,
        entry: Any,
        parameters: Mapping[str, Any],
        observed: float,
        *,
        interval_seconds: float | None = None,
    ) -> bool:
        saved_at = self._saved_at(entry)
        interval = (
            self._interval(resource)
            if interval_seconds is None
            else float(interval_seconds)
        )
        return bool(
            not isinstance(entry, Mapping)
            or entry.get("parameters") != dict(parameters)
            or saved_at is None
            or observed - saved_at >= interval
        )

    @staticmethod
    def _market_phase(observed: float, *, resource: str | None = None) -> str:
        from app.services.market_calendar import (
            ET,
            early_close_minutes,
            is_trading_day,
            options_close_minutes,
        )

        local = datetime.fromtimestamp(observed, ET)
        if not is_trading_day(local.date()):
            return "closed"
        minutes = local.hour * 60 + local.minute
        close = (
            options_close_minutes(local.date())
            if resource == "unusual"
            else early_close_minutes(local.date()) or 16 * 60
        )
        close = close or 16 * 60
        if 9 * 60 + 30 <= minutes < close:
            return "regular"
        if 4 * 60 <= minutes < 9 * 60 + 30:
            return "premarket"
        if close <= minutes < 20 * 60:
            return "afterhours"
        return "closed"

    def _effective_interval(self, resource: str, phase: str) -> float:
        interval = self._interval(resource)
        if phase != "closed":
            return interval
        if resource == "earnings":
            return max(interval, 12 * 60 * 60)
        if resource == "unusual":
            return max(interval, 6 * 60 * 60)
        return max(interval, 6 * 60 * 60)

    @staticmethod
    def _is_hard_servable(
        resource: str,
        entry: Any,
        parameters: Mapping[str, Any],
        observed: float,
    ) -> bool:
        if resource == "watchlist":
            return isinstance(entry, Mapping)
        from app.public_home_snapshot import public_home_entry_is_servable

        return public_home_entry_is_servable(
            resource,
            entry,
            parameters=parameters,
            now=observed,
        )

    def _external_fresh(
        self,
        resource: str,
        entry: Any,
        parameters: Mapping[str, Any],
        observed: float,
        baseline_saved_at: float | None,
    ) -> bool:
        saved_at = self._saved_at(entry)
        return bool(
            saved_at is not None
            and (baseline_saved_at is None or saved_at > baseline_saved_at)
            and isinstance(entry, Mapping)
            and entry.get("parameters") == dict(parameters)
            and not self._is_due(resource, entry, parameters, observed)
        )

    def _record_failure(
        self,
        resource: str,
        *,
        baseline_saved_at: float | None,
    ) -> _PublicHomeFailure:
        previous = self._failures.get(resource)
        attempts = (previous.attempts if previous is not None else 0) + 1
        base = float(self._config.failure_retry_seconds)
        delay = min(self._interval(resource), base * (2 ** min(attempts - 1, 10)))
        phase = self._market_phase(float(self._clock()), resource=resource)
        if phase == "closed":
            delay = max(delay, self._effective_interval(resource, phase))
        state = _PublicHomeFailure(
            attempts=attempts,
            retry_after=float(self._clock()) + delay,
            baseline_saved_at=baseline_saved_at,
        )
        self._failures[resource] = state
        return state

    async def _produce_entry(
        self,
        resource: str,
        parameters: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload = await self._build(resource, parameters)
        if resource == "watchlist":
            return {
                "payload": payload,
                "saved_at": float(self._clock()),
                "parameters": dict(parameters),
            }
        if resource == "earnings" and (
            not isinstance(payload, Mapping)
            or payload.get("data_limited") is not False
            or payload.get("source_status") != "active"
        ):
            # Never replace a complete public calendar with a partial
            # hot-list fallback produced during a provider outage.
            raise RuntimeError("earnings_snapshot_incomplete")
        from app.public_home_snapshot import create_public_home_entry

        return create_public_home_entry(
            resource,
            payload,
            saved_at=float(self._clock()),
            parameters=parameters,
        )

    async def _read_entries(
        self,
        path: Path,
        watchlist_path: Path,
        *,
        now: float,
    ) -> dict[str, dict[str, Any]]:
        from app.public_home_snapshot import read_public_home_entries

        reader = self._reader or read_public_home_entries
        entries = await _call_local(reader, path, now=now)
        result = dict(entries) if isinstance(entries, dict) else {}
        from app.api import stocks

        watchlist_reader = self._watchlist_reader or stocks._read_watchlist_snapshot
        watchlist = await _call_local(watchlist_reader, watchlist_path, now=now)
        if watchlist is not None:
            fetched_at = getattr(watchlist, "fetched_at", None)
            payload = getattr(watchlist, "value", None)
            if isinstance(fetched_at, (int, float)) and isinstance(payload, dict):
                from app.services.watchlist_scope import DEFAULT_WATCHLIST_TICKERS, scope_watchlist

                result["watchlist"] = {
                    "payload": scope_watchlist(payload, DEFAULT_WATCHLIST_TICKERS),
                    "saved_at": float(fetched_at),
                    "parameters": {"tickers": None},
                }
        elif isinstance(result.get("watchlist"), dict):
            from app.services.watchlist_scope import DEFAULT_WATCHLIST_TICKERS, scope_watchlist

            result["watchlist"] = {
                **result["watchlist"],
                "payload": scope_watchlist(result["watchlist"].get("payload"), DEFAULT_WATCHLIST_TICKERS),
            }
        return result

    async def _publish_entry(
        self,
        resource: str,
        entry: dict[str, Any],
        *,
        path: Path,
        watchlist_path: Path,
    ) -> dict[str, dict[str, Any]]:
        from app.public_home_snapshot import write_public_home_snapshot

        if resource == "watchlist":
            from app.api import stocks

            watchlist_writer = self._watchlist_writer or stocks._write_watchlist_snapshot
            await _call_local(
                watchlist_writer,
                watchlist_path,
                payload=entry["payload"],
                saved_at=float(entry["saved_at"]),
            )
            return await self._read_entries(
                path,
                watchlist_path,
                now=float(self._clock()),
            )
        latest = await self._read_entries(
            path,
            watchlist_path,
            now=float(self._clock()),
        )
        bundle = {key: value for key, value in latest.items() if key != "watchlist"}
        candidate = {**bundle, resource: entry}
        writer = self._writer or write_public_home_snapshot
        await _call_local(
            writer,
            path,
            candidate,
            now=float(self._clock()),
        )
        return await self._read_entries(
            path,
            watchlist_path,
            now=float(self._clock()),
        )

    async def _harvest(
        self,
        resource: str,
        attempt: _PublicHomeInflight,
        *,
        path: Path,
        watchlist_path: Path,
        entries: dict[str, dict[str, Any]],
        failed: list[str],
        refreshed: list[str],
    ) -> dict[str, dict[str, Any]]:
        current = entries.get(resource)
        if attempt.superseded or self._external_fresh(
            resource,
            current,
            attempt.parameters,
            float(self._clock()),
            attempt.baseline_saved_at,
        ):
            try:
                attempt.task.result()
            except BaseException:
                pass
            self._inflight.pop(resource, None)
            self._failures.pop(resource, None)
            return entries
        try:
            produced = attempt.task.result()
        except asyncio.CancelledError:
            self._inflight.pop(resource, None)
            raise
        except Exception:
            self._inflight.pop(resource, None)
            self._record_failure(
                resource,
                baseline_saved_at=attempt.baseline_saved_at,
            )
            failed.append(resource)
            return entries
        try:
            entries = await self._publish_entry(
                resource,
                produced,
                path=path,
                watchlist_path=watchlist_path,
            )
        except Exception:
            self._record_failure(
                resource,
                baseline_saved_at=attempt.baseline_saved_at,
            )
            failed.append(resource)
            # Keep the completed single-flight result. Once the persistence
            # cooldown expires, a later leased round retries only the write.
            return entries
        self._inflight.pop(resource, None)
        self._failures.pop(resource, None)
        refreshed.append(resource)
        return entries

    def _result(
        self,
        *,
        path: Path,
        watchlist_path: Path,
        resource_order: tuple[str, ...],
        parameters: Mapping[str, Mapping[str, Any]],
        entries: Mapping[str, Any],
        refreshed: list[str],
        failed: list[str],
        deferred: list[str],
        in_flight: list[str],
        cooling: list[str],
    ) -> TaskResult:
        retry_after = {
            resource: max(
                0,
                int(round(state.retry_after - float(self._clock()))),
            )
            for resource, state in self._failures.items()
        }
        observed = float(self._clock())
        available = [
            resource
            for resource in resource_order
            if self._is_hard_servable(
                resource,
                entries.get(resource),
                parameters[resource],
                observed,
            )
        ]
        unavailable = [
            resource for resource in resource_order if resource not in available
        ]
        details = {
            "snapshot": path.name,
            "watchlist_snapshot": watchlist_path.name,
            "refreshed": refreshed,
            "failed": failed,
            "deferred": deferred,
            "in_flight": sorted(set(in_flight)),
            "cooling": sorted(set(cooling)),
            "retry_after_seconds": retry_after,
            "available": available,
            "unavailable": unavailable,
            "completed_at": _timestamp_text(float(self._clock())),
        }
        if self._stock_data_refresh is not None:
            details["stock_data"] = dict(self._stock_data_refresh.summary())
            if self._stock_data_error:
                details["stock_data"]["error_code"] = "public_stock_queue_unavailable"
        degraded = bool(self._failures or in_flight or failed or unavailable or self._stock_data_error)
        if failed:
            error_code = "public_home_refresh_failed"
        elif in_flight:
            error_code = "public_home_refresh_in_flight"
        elif self._stock_data_error:
            error_code = "public_stock_queue_unavailable"
        elif degraded:
            error_code = (
                "public_home_snapshot_unavailable"
                if unavailable
                else "public_home_refresh_cooling"
            )
        else:
            error_code = None
        return TaskResult(
            status="degraded" if degraded else "idle",
            error_code=error_code,
            details=details,
            next_delay_seconds=float(self._config.poll_seconds),
        )

    async def run_for_actions(self, actions: list[dict[str, Any]]) -> TaskResult:
        """Owner 手动财报刷新（审计 P2-04）：动作路径立即重建并发布 earnings。

        此前 POST /earnings/upcoming/refresh 在 HTTP 请求内同步跑完全部上游
        （Finnhub/可选 FMP/Yahoo/市值补全/期权预期波动），按钮时长完全由
        上游决定。现在 POST 只入队（earnings_calendar → 本任务名），这里
        承担全部 provider 工作，并复用与定时发布完全相同的完整性闸——
        上游断供时 _produce_entry 抛 earnings_snapshot_incomplete，上一份
        完整日历原样保留，动作由 runner 统一记失败；前端跟进窗口按快照
        as_of 是否翻新判定，超时提示后台仍在进行。
        """
        from app.public_home_snapshot import public_home_resource_parameters

        for action in actions:
            action_type = (
                action.get("action_type") if isinstance(action, dict) else None
            )
            if action_type != "earnings_calendar":
                raise ValueError(
                    "public_home only serves earnings_calendar actions"
                )
        path = self._snapshot_path or get_data_paths().public_home_snapshot
        watchlist_path = self._watchlist_path or (
            path.parent / "watchlist-snapshot-v1.json"
            if self._snapshot_path is not None
            else get_data_paths().watchlist_snapshot
        )
        observed = float(self._clock())
        parameters = public_home_resource_parameters("earnings", now=observed)
        entry = await self._produce_entry("earnings", parameters)
        await self._publish_entry(
            "earnings",
            entry,
            path=path,
            watchlist_path=watchlist_path,
        )
        # 手动重建同时压掉同资源的在途定时构建：旧结果晚到不得覆盖新发布。
        inflight = self._inflight.get("earnings")
        if inflight is not None:
            inflight.superseded = True
        self._failures.pop("earnings", None)
        return TaskResult(
            status="idle",
            details={
                "result": "earnings_refreshed",
                "saved_at": _timestamp_text(float(entry["saved_at"])),
            },
            next_delay_seconds=2.0,
        )

    async def __call__(self) -> TaskResult:
        from app.public_home_snapshot import (
            PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER,
            PUBLIC_HOME_RESOURCE_ORDER,
            breakout_lead_chart_parameters,
            public_home_resource_parameters,
        )
        path = self._snapshot_path or get_data_paths().public_home_snapshot
        watchlist_path = self._watchlist_path or (
            path.parent / "watchlist-snapshot-v1.json"
            if self._snapshot_path is not None
            else get_data_paths().watchlist_snapshot
        )
        observed = float(self._clock())
        breakout_lead_ticker = await self._breakout_lead_ticker()
        if breakout_lead_ticker is None:
            orphaned = self._inflight.get("breakout_lead_chart")
            if orphaned is not None:
                orphaned.superseded = True
                if orphaned.task.done():
                    try:
                        orphaned.task.result()
                    except BaseException:
                        pass
                    self._inflight.pop("breakout_lead_chart", None)
        optional_resources = (
            tuple(PUBLIC_HOME_OPTIONAL_RESOURCE_ORDER)
            if breakout_lead_ticker is not None
            else ()
        )
        # cta_trend 既有硬编码位（领跑票缺席时也要刷新）又在 OPTIONAL 列表里；
        # 领跑票在场时会叠出重复项——due 清单收录两次 → 同一轮双构建双落盘，
        # 生产 status 的 available 也双列。按首次出现去重。
        resource_order = tuple(
            dict.fromkeys(
                (
                    "watchlist",
                    *tuple(PUBLIC_HOME_RESOURCE_ORDER),
                    # CTA 趋势资金：无条件刷新的可选资源（不依赖雷达领跑票）。
                    "cta_trend",
                    *optional_resources,
                )
            )
        )
        market_phases = {
            resource: self._market_phase(observed, resource=resource)
            for resource in resource_order
        }
        entries = await self._read_entries(path, watchlist_path, now=observed)
        if self._stock_data_refresh is not None:
            try:
                # This only updates a bounded queue. The two consumers carry on
                # while unrelated home resources refresh or this round times out.
                await self._stock_data_refresh.poll(entries)
                self._stock_data_error = False
            except Exception:
                self._stock_data_error = True
        parameters = {
            resource: (
                {"tickers": None}
                if resource == "watchlist"
                else breakout_lead_chart_parameters(breakout_lead_ticker)
                if resource == "breakout_lead_chart"
                else public_home_resource_parameters(resource, now=observed)
            )
            for resource in resource_order
        }
        refreshed: list[str] = []
        failed: list[str] = []
        deferred: list[str] = []
        in_flight: list[str] = []
        cooling: list[str] = []

        # A separate process or an administrative refresh can repair a failed
        # resource. Observe that newer fresh generation before applying local
        # cooldown state or harvesting an older child task.
        for resource in resource_order:
            entry = entries.get(resource)
            failure = self._failures.get(resource)
            if failure is not None and self._external_fresh(
                resource,
                entry,
                parameters[resource],
                observed,
                failure.baseline_saved_at,
            ):
                self._failures.pop(resource, None)
            attempt = self._inflight.get(resource)
            if attempt is not None and self._external_fresh(
                resource,
                entry,
                attempt.parameters,
                observed,
                attempt.baseline_saved_at,
            ):
                attempt.superseded = True

        # Harvest completed children first. An unfinished child survives the
        # supervisor timeout and blocks a duplicate provider batch.
        for resource in resource_order:
            attempt = self._inflight.get(resource)
            if attempt is None:
                continue
            if not attempt.task.done():
                in_flight.append(resource)
                continue
            failure = self._failures.get(resource)
            if failure is not None and observed < failure.retry_after:
                cooling.append(resource)
                continue
            entries = await self._harvest(
                resource,
                attempt,
                path=path,
                watchlist_path=watchlist_path,
                entries=entries,
                failed=failed,
                refreshed=refreshed,
            )

        due: list[str] = []
        for resource in resource_order:
            if resource in self._inflight:
                continue
            if (
                resource == "unusual"
                and market_phases[resource] != "regular"
                and (
                    resource in self._failures
                    or self._is_hard_servable(
                        resource,
                        entries.get(resource),
                        parameters[resource],
                        observed,
                    )
                )
            ):
                deferred.append(resource)
                continue
            if not self._is_due(
                resource,
                entries.get(resource),
                parameters[resource],
                observed,
                interval_seconds=self._effective_interval(
                    resource,
                    market_phases[resource],
                ),
            ):
                self._failures.pop(resource, None)
                continue
            failure = self._failures.get(resource)
            if failure is not None and observed < failure.retry_after:
                cooling.append(resource)
                continue
            due.append(resource)

        heavy_due = [item for item in self._HEAVY_RESOURCES if item in due]
        heavy_in_flight = any(
            resource in self._HEAVY_RESOURCES
            and not attempt.task.done()
            for resource, attempt in self._inflight.items()
        )
        if heavy_in_flight:
            deferred.extend(heavy_due)
            due = [item for item in due if item not in self._HEAVY_RESOURCES]
        elif len(heavy_due) > 1:
            selected = (
                self._next_heavy
                if self._next_heavy in heavy_due
                else heavy_due[0]
            )
            self._next_heavy = (
                "unusual" if selected == "earnings" else "earnings"
            )
            deferred = [item for item in heavy_due if item != selected]
            due = [item for item in due if item not in deferred]

        for resource in due:
            baseline = self._saved_at(entries.get(resource))
            child = asyncio.create_task(
                self._produce_entry(resource, parameters[resource]),
                name=f"public-home-{resource}",
            )
            attempt = _PublicHomeInflight(
                task=child,
                parameters=dict(parameters[resource]),
                baseline_saved_at=baseline,
                started_at=float(self._clock()),
            )
            self._inflight[resource] = attempt
            try:
                await asyncio.shield(child)
            except asyncio.CancelledError:
                # 取消必须立刻返回：子任务留在 _inflight 单飞，下一轮
                # 报 public_home_refresh_in_flight、之后收割结果（
                # test_worker_cancelled_round_keeps_single_flight_and_later_harvests
                # 钉住的契约）。在这里等子任务跑完会把慢快照拖成
                # 停机死锁。
                raise
            except Exception:
                pass
            entries = await self._harvest(
                resource,
                attempt,
                path=path,
                watchlist_path=watchlist_path,
                entries=entries,
                failed=failed,
                refreshed=refreshed,
            )

        cooling.extend(
            resource
            for resource, state in self._failures.items()
            if observed < state.retry_after
        )
        if self._stock_data_refresh is not None and any(
            resource in refreshed for resource in ("watchlist", "earnings")
        ):
            try:
                await self._stock_data_refresh.poll(entries)
                self._stock_data_error = False
            except Exception:
                self._stock_data_error = True
        return self._result(
            path=path,
            watchlist_path=watchlist_path,
            resource_order=resource_order,
            parameters=parameters,
            entries=entries,
            refreshed=refreshed,
            failed=failed,
            deferred=deferred,
            in_flight=in_flight,
            cooling=cooling,
        )

    async def aclose(self) -> None:
        stock_drain = (
            asyncio.create_task(self._stock_data_refresh.aclose())
            if self._stock_data_refresh is not None
            else None
        )
        tasks = [attempt.task for attempt in self._inflight.values()]
        for task in tasks:
            if not task.done():
                task.cancel()
        try:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            self._inflight.clear()
            if stock_drain is not None:
                # The stock queue drains even when shutdown itself is cancelled;
                # its close method shields already-running provider/disk work.
                await stock_drain


class StrengthRefreshTask:
    """Refresh the daily default snapshot and exact owner-requested variants."""

    def __init__(
        self,
        *,
        scanner: Callable[..., Any] | None = None,
        writer: Callable[..., Any] | None = None,
        snapshot_path: Path | None = None,
        clock: Callable[[], float] = time.time,
        scheduled_interval_seconds: float = 86_400.0,
    ) -> None:
        self._scanner = scanner
        self._writer = writer
        self._snapshot_path = snapshot_path
        self._clock = clock
        self._scheduled_interval_seconds = float(scheduled_interval_seconds)
        self._last_scheduled_at: float | None = None

    def _next_scheduled_delay(self) -> float:
        if self._last_scheduled_at is None:
            return 0.0
        due_at = self._last_scheduled_at + self._scheduled_interval_seconds
        return min(
            self._scheduled_interval_seconds,
            max(0.0, due_at - float(self._clock())),
        )

    async def _run(self, parameters: dict[str, Any]) -> TaskResult:
        from app.api.strength import (
            _strength_snapshot_path,
            _write_strength_snapshot,
            normalize_strength_scan_parameters,
            strength_scan_parameters_hash,
        )
        from app.services.strength.scanner import scan_strength
        from app.services.utils import sanitize

        scanner = self._scanner or scan_strength
        writer = self._writer or _write_strength_snapshot
        base_path = self._snapshot_path or get_data_paths().strength_snapshot
        parameters = normalize_strength_scan_parameters(parameters)
        path = _strength_snapshot_path(parameters, base_path=base_path)
        payload = sanitize(
            await _call_local(
                scanner,
                **parameters,
                force_refresh=True,
            )
        )
        saved_at = float(self._clock())
        await _call_local(
            writer,
            path,
            parameters=parameters,
            payload=payload,
            saved_at=saved_at,
            base_path=base_path,
        )
        count = int(payload.get("count") or 0) if isinstance(payload, dict) else 0
        return TaskResult(
            status="idle",
            details={
                "result": "refreshed",
                "snapshot": path.name,
                "count": max(0, count),
                "parameters": parameters,
                "parameters_hash": strength_scan_parameters_hash(parameters),
                "completed_at": _timestamp_text(saved_at),
            },
        )

    async def run_for_actions(self, actions: list[dict[str, Any]]) -> TaskResult:
        from app.api.strength import (
            DEFAULT_STRENGTH_SCAN_PARAMETERS,
            normalize_strength_scan_parameters,
            strength_scan_parameters_hash,
        )

        selected: dict[str, Any] | None = None
        for action in actions:
            details = action.get("details") if isinstance(action, dict) else None
            raw = details.get("parameters") if isinstance(details, dict) else None
            if not isinstance(raw, dict):
                raise ValueError("strength refresh action parameters are missing")
            parameters = normalize_strength_scan_parameters(raw)
            expected_hash = strength_scan_parameters_hash(parameters)
            stored_hash = details.get("parameters_hash")
            if stored_hash is not None and stored_hash != expected_hash:
                raise ValueError("strength refresh action parameter hash is invalid")
            if selected is not None and parameters != selected:
                raise ValueError("strength refresh actions have conflicting parameters")
            selected = parameters
        result = await self._run(
            selected or dict(DEFAULT_STRENGTH_SCAN_PARAMETERS)
        )
        return TaskResult(
            status=result.status,
            details=result.details,
            next_delay_seconds=self._next_scheduled_delay(),
            error_code=result.error_code,
        )

    async def __call__(self) -> TaskResult:
        from app.api.strength import DEFAULT_STRENGTH_SCAN_PARAMETERS

        result = await self._run(dict(DEFAULT_STRENGTH_SCAN_PARAMETERS))
        self._last_scheduled_at = float(self._clock())
        return result


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
            maximum_loop_stall_seconds=(
                BREAKOUT_TASK_TIMEOUT_SECONDS
                + float(self._settings.worker_lease_ttl_seconds)
            ),
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


def seconds_until_next_et_slot(
    now: datetime,
    times_et: Sequence[str],
    *,
    default_seconds: float = 3_600.0,
) -> float:
    """Seconds to the next absolute America/New_York wall-clock slot.

    Scheduling on absolute times rather than a fixed interval means the run
    times never drift, and subtracting two zone-aware instants keeps daylight
    saving transitions correct.
    """

    from zoneinfo import ZoneInfo

    eastern = ZoneInfo("America/New_York")
    local = now.astimezone(eastern)
    candidates: list[datetime] = []
    for value in times_et:
        parts = str(value).split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            continue
        hour, minute = (int(part) for part in parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            continue
        for offset in (0, 1):
            candidate = (local + timedelta(days=offset)).replace(
                hour=hour,
                minute=minute,
                second=0,
                microsecond=0,
            )
            if candidate > local:
                candidates.append(candidate)
    if not candidates:
        return default_seconds
    # Subtract in UTC on purpose. Python ignores a shared tzinfo when
    # subtracting two aware datetimes, so a same-zone subtraction returns the
    # wall-clock difference and would fire an hour early on a daylight saving
    # transition day. Converting first makes the delay real elapsed time.
    delay = (
        min(candidates).astimezone(timezone.utc) - local.astimezone(timezone.utc)
    ).total_seconds()
    return max(1.0, min(86_400.0, delay))


class MacroConditionsTask:
    """Optix 宏观环境 refresh — one task for both scheduled and manual runs.

    A missing FRED key reports ``disabled`` with a specific reason rather than
    failing, so the unified worker stays healthy while the feature is unconfigured.
    A failed refresh leaves the previously published snapshot readable.
    """

    def __init__(
        self,
        owner_id: str,
        *,
        settings: Any | None = None,
        personal_config: Any | None = None,
        service_factory: Callable[..., Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.owner_id = f"{owner_id}:macro"
        self._settings = settings
        self._personal_config = personal_config or get_personal_config()
        self._service_factory = service_factory
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._service: Any = None
        # Only one macro refresh may run at a time inside this process; the
        # worker action table enforces the same at the API boundary.
        self._lock = threading.Lock()

    def _config(self) -> Any:
        return self._personal_config.macro

    def _next_delay(self) -> float:
        return seconds_until_next_et_slot(
            self._now(),
            tuple(self._config().refresh_times_et),
        )

    def _resolved_settings(self) -> Any:
        if self._settings is not None:
            return self._settings
        from app.config import get_settings

        return get_settings()

    def _build_service(self) -> Any:
        if self._service is not None:
            return self._service
        if self._service_factory is not None:
            self._service = self._service_factory()
            return self._service
        from app.services.macro_conditions.repository import MacroRepository
        from app.services.macro_conditions.service import (
            MacroConditionsService,
            MacroServiceConfig,
        )

        settings = self._resolved_settings()
        repository = MacroRepository(settings.macro_conditions_db_path)
        self._service = MacroConditionsService(
            repository,
            config=MacroServiceConfig.from_personal_config(self._personal_config),
        )
        return self._service

    def _disabled(self, reason: str) -> TaskResult:
        return TaskResult(
            status="disabled",
            details={"result": "disabled", "reason": reason},
            next_delay_seconds=self._next_delay(),
        )

    async def _run(self, trigger: str) -> TaskResult:
        config = self._config()
        if not config.enabled:
            return self._disabled("macro_disabled")
        settings = self._resolved_settings()
        if not bool(getattr(settings, "macro_conditions_configured", False)):
            return self._disabled("fred_api_key_missing")
        if not self._lock.acquire(blocking=False):
            return TaskResult(
                status="idle",
                error_code="macro_refresh_in_progress",
                details={"result": "skipped", "reason": "macro_refresh_in_progress"},
                next_delay_seconds=self._next_delay(),
            )
        try:
            service = self._build_service()
            # SQLite plus one sequential upstream read: keep it off the loop.
            outcome = await _call_local(service.refresh, trigger=trigger)
        finally:
            self._lock.release()
        details = {
            "result": str(outcome.get("status") or "unknown"),
            "trigger": trigger,
            "published": bool(outcome.get("published")),
            "series_succeeded": int(outcome.get("series_succeeded") or 0),
            "series_failed": int(outcome.get("series_failed") or 0),
            "data_through": outcome.get("data_through"),
            "composite_score": outcome.get("composite_score"),
            "valid_module_count": outcome.get("valid_module_count"),
            # Warnings name series ids and safe error codes only.
            "warnings": list(outcome.get("warnings") or [])[:20],
        }
        error_codes = list(outcome.get("error_codes") or [])
        if outcome.get("published") and not error_codes:
            return TaskResult(
                status="idle",
                details=details,
                next_delay_seconds=self._next_delay(),
            )
        return TaskResult(
            status="degraded",
            error_code=str(error_codes[0]) if error_codes else "macro_snapshot_unavailable",
            details=details,
            next_delay_seconds=self._next_delay(),
        )

    async def __call__(self) -> TaskResult:
        return await self._run("scheduled")

    async def run_for_actions(self, actions: Sequence[Mapping[str, Any]]) -> TaskResult:
        """Owner-requested refresh. Shares this task and its exclusion lock."""

        return await self._run("manual")


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


class RetentionTask:
    """Back up personal databases before pruning bounded scan attachments."""

    def __init__(
        self,
        owner_id: str,
        backup: MaintenanceTask,
        *,
        settings_factory: Callable[[], Any] | None = None,
        repository_factory: Callable[[Path], Any] | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.owner_id = f"{owner_id}:retention"
        self.backup = backup
        self._settings_factory = settings_factory
        self._repository_factory = repository_factory
        self._now = now or (lambda: datetime.now(timezone.utc))

    async def __call__(self) -> TaskResult:
        backup_result = await self.backup()
        if not isinstance(backup_result, TaskResult):
            raise TypeError("retention backup must return TaskResult")
        if backup_result.status == "degraded":
            return TaskResult(
                status="degraded",
                error_code=backup_result.error_code or "backup_failed",
                details={
                    "backup": dict(backup_result.details),
                    "retention": {"status": "skipped_backup_failed"},
                    "completed_at": self._now()
                    .astimezone(timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )

        if self._settings_factory is None:
            from app.services.breakouts.config import get_breakout_settings

            settings_factory = get_breakout_settings
        else:
            settings_factory = self._settings_factory
        settings = await _call_local(settings_factory)
        database_path = Path(settings.db_path)
        if not database_path.is_file():
            completed = self._now().astimezone(timezone.utc)
            return TaskResult(
                status="idle",
                details={
                    "backup": dict(backup_result.details),
                    "retention": {"status": "skipped_missing_database"},
                    "completed_at": completed.isoformat().replace("+00:00", "Z"),
                },
            )

        if self._repository_factory is None:
            from app.services.breakouts.repository import BreakoutRepository

            repository_factory = BreakoutRepository
        else:
            repository_factory = self._repository_factory
        repository = await _call_local(repository_factory, database_path)
        await _call_local(repository.initialize)

        from app.services.breakouts.repository import DEFAULT_LOCK_NAME

        observed = self._now().astimezone(timezone.utc)
        lease_seconds = float(
            getattr(settings, "worker_lease_ttl_seconds", 90.0)
        )
        lease_token = await _call_local(
            repository.acquire_lock,
            DEFAULT_LOCK_NAME,
            self.owner_id,
            lease_seconds,
            observed,
        )
        if lease_token is None:
            return TaskResult(
                status="degraded",
                error_code="retention_locked",
                details={
                    "backup": dict(backup_result.details),
                    "retention": {"status": "locked"},
                    "completed_at": observed.isoformat().replace("+00:00", "Z"),
                },
            )
        try:
            counts = await _call_local(
                repository.prune_retention,
                owner_id=self.owner_id,
                lease_token=int(lease_token),
                raw_payload_hours=int(settings.raw_payload_retention_hours),
                scan_days=int(settings.scan_retention_days),
                batch_size=int(settings.retention_batch_size),
                now=observed,
            )
        finally:
            await _call_local(
                repository.release_lock,
                DEFAULT_LOCK_NAME,
                self.owner_id,
                int(lease_token),
                self._now().astimezone(timezone.utc),
            )
        completed = self._now().astimezone(timezone.utc)
        return TaskResult(
            status="idle",
            details={
                "backup": dict(backup_result.details),
                "retention": {
                    "status": "completed",
                    **{str(key): int(value) for key, value in dict(counts).items()},
                },
                "completed_at": completed.isoformat().replace("+00:00", "Z"),
            },
        )


def build_default_tasks(owner_id: str, *, settings: Any) -> tuple[TaskSpec, ...]:
    config = get_personal_config()
    ai = AIJobsTask(owner_id, settings=settings, personal_config=config)
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
    manual_breakout = BreakoutTask(f"{owner_id}:manual")
    maintenance = MaintenanceTask(
        {
            "optix": settings.breakout_db_path,
            "catalyst-cache": settings.macrolens_cache_db_path,
            "ai-jobs": settings.openai_job_db_path,
            "optix-worker": settings.optix_worker_db_path,
            "macro-conditions": settings.macro_conditions_db_path,
        },
        destination=settings.optix_backup_dir,
        keep=config.storage.backup_keep,
    )
    retention_backup = MaintenanceTask(
        dict(maintenance.databases),
        destination=maintenance.destination,
        keep=maintenance.keep,
    )
    retention = RetentionTask(owner_id, retention_backup)
    from app.public_stock_data import PublicStockDataRefresh

    public_home = PublicHomeTask(
        config.public_home, stock_data_refresh=PublicStockDataRefresh(),
    )
    stock_directory = StockDirectoryTask()
    earnings_analysis = EarningsAnalysisTask(
        owner_id,
        settings=settings,
        personal_config=config,
    )
    macro_conditions = MacroConditionsTask(
        owner_id,
        settings=settings,
        personal_config=config,
    )
    return (
        TaskSpec(
            "breakout",
            breakout,
            interval_seconds=float(config.breakout.regular_seconds),
            enabled=config.features.breakout_enabled,
            timeout_seconds=BREAKOUT_TASK_TIMEOUT_SECONDS,
            may_block_event_loop=True,
        ),
        TaskSpec(
            "catalyst_sync",
            catalyst,
            interval_seconds=float(config.catalyst.sync_seconds),
            enabled=config.catalyst_sync_enabled,
            # The first news backfill walks up to 100 checkpointed pages and
            # cannot finish inside 120s; incremental rounds stay far below.
            timeout_seconds=600.0,
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
            # 重启不重跑：全量备份约 3GB 磁盘拷贝，跟着进程重启立即执行会
            # 与其余任务的启动风暴抢盘（2026-08-15 崩溃循环里每 5 分钟
            # 一轮从头备份）。沿用状态库里的 next_run_at。
            honor_persisted_schedule=True,
        ),
        TaskSpec(
            "stock_directory",
            stock_directory,
            interval_seconds=86_400.0,
            enabled=bool(getattr(settings, "massive_api_key", "")),
            timeout_seconds=900.0,
            failure_backoff_seconds=300.0,
            max_backoff_seconds=3600.0,
        ),
        TaskSpec(
            "public_home",
            public_home,
            interval_seconds=float(config.public_home.poll_seconds),
            enabled=config.access.mode == "password",
            timeout_seconds=1800.0,
            failure_backoff_seconds=float(config.public_home.poll_seconds),
            max_backoff_seconds=float(config.public_home.poll_seconds),
            may_block_event_loop=False,
            close=public_home.aclose,
        ),
        TaskSpec(
            "earnings_analysis",
            earnings_analysis,
            interval_seconds=float(config.public_home.earnings_seconds),
            # Earnings analysis has its own runtime switch. Keep the task
            # addressable even when the unrelated news-catalyst mode is off.
            enabled=True,
            timeout_seconds=1800.0,
            failure_backoff_seconds=300.0,
            max_backoff_seconds=3600.0,
        ),
        TaskSpec(
            "macro_conditions",
            macro_conditions,
            # The loop re-arms on the next absolute America/New_York slot from
            # every result, so this interval is only the first wake-up. The
            # supervisor also wakes the loop early for a queued owner action, so
            # scheduled and manual refreshes share this one task.
            interval_seconds=3_600.0,
            enabled=config.macro.enabled,
            timeout_seconds=1_800.0,
            failure_backoff_seconds=300.0,
            max_backoff_seconds=3600.0,
        ),
        TaskSpec(
            "focus_refresh",
            FocusRefreshTask(),
            interval_seconds=86_400.0,
            timeout_seconds=300.0,
            manual_only=True,
        ),
        TaskSpec(
            "strength_refresh",
            StrengthRefreshTask(),
            # 默认快照每天刷新；参数化 API 动作不会重置默认快照的绝对截止时间。
            interval_seconds=86_400.0,
            timeout_seconds=1200.0,
        ),
        TaskSpec(
            "breakout_refresh",
            manual_breakout,
            interval_seconds=86_400.0,
            enabled=config.features.breakout_enabled,
            timeout_seconds=BREAKOUT_TASK_TIMEOUT_SECONDS,
            may_block_event_loop=True,
            manual_only=True,
        ),
        TaskSpec(
            "retention",
            retention,
            interval_seconds=86_400.0,
            timeout_seconds=1800.0,
            failure_backoff_seconds=300.0,
            max_backoff_seconds=3600.0,
            manual_only=True,
        ),
    )


__all__ = [
    "AIJobsTask",
    "BreakoutTask",
    "CatalystSyncTask",
    "EarningsAnalysisTask",
    "FocusTask",
    "FocusRefreshTask",
    "MacroConditionsTask",
    "MaintenanceTask",
    "DEFAULT_TASK_NAMES",
    "PublicHomeTask",
    "RetentionTask",
    "StockDirectoryTask",
    "StrengthRefreshTask",
    "build_default_tasks",
    "seconds_until_next_et_slot",
]
