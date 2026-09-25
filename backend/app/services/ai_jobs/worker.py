from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from app.config import get_settings
from app.execution_limits import BREAKOUT_TASK_TIMEOUT_SECONDS
from app.failure_diagnostics import record_fallback_failure
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import InvalidJobPayloadError
from app.services.ai_jobs.repository import AIJobRepository

from app.personal_config import personal_analysis_permissions as _personal_analysis_permissions


logger = logging.getLogger(__name__)

_NEW_SUBMISSION_RETRY_SECONDS = 30.0
_MIN_LEASE_HEARTBEAT_INTERVAL_SECONDS = 5.0
# Writes after the provider accepted a job (linking the response id, recording
# a poll, publishing the result, deferring) carry paid work. A transient SQLite
# busy error there used to cost the whole day's paid slot or the paid result,
# so each gets a few short retries before conceding.
_STORAGE_WRITE_RETRIES = 3
_STORAGE_WRITE_RETRY_DELAY_SECONDS = 0.25
_LOCAL_STORAGE_ERROR = "local_storage_error"


async def _with_storage_retry(
    write: Callable[..., Any],
    *args: Any,
    **kwargs: Any,
) -> Any:
    for attempt in range(_STORAGE_WRITE_RETRIES + 1):
        try:
            return write(*args, **kwargs)
        except sqlite3.OperationalError:
            # Transient lock/busy contention only; anything else (a rejected
            # link, a lost lease) keeps its original meaning and propagates.
            if attempt >= _STORAGE_WRITE_RETRIES:
                raise
            await asyncio.sleep(
                _STORAGE_WRITE_RETRY_DELAY_SECONDS * (attempt + 1)
            )


def _poll_delay(settings: Any, poll_count: int) -> float:
    initial = float(settings.openai_background_initial_poll_seconds)
    maximum = float(settings.openai_background_max_poll_seconds)
    return min(maximum, initial * (2 ** min(max(0, poll_count), 4)))


def _submitted_age_seconds(job: dict[str, Any]) -> float:
    for field in ("submitted_at", "submission_started_at", "created_at"):
        submitted = str(job.get(field) or "")
        if not submitted:
            continue
        try:
            submitted_at = datetime.fromisoformat(submitted.replace("Z", "+00:00"))
        except ValueError:
            continue
        return max(
            0.0,
            (datetime.now(timezone.utc) - submitted_at).total_seconds(),
        )
    return 0.0


def _poll_window_elapsed(settings: Any, job: dict[str, Any]) -> bool:
    poll_timeout = float(settings.openai_background_poll_timeout_seconds)
    return poll_timeout > 0 and _submitted_age_seconds(job) > poll_timeout


def _control_error_code(exc: Exception) -> str | None:
    """Terminal code for a definitive retrieve/cancel HTTP rejection.

    这些状态码重试也不会变（密钥换了、响应被删、请求本身被拒），一律推迟会把
    车道占到 openai_job_max_age_seconds（默认 24 小时）。429、5xx 与网络错误
    返回 None，由调用方继续推迟。
    """

    status_code = getattr(exc, "status_code", None)
    if status_code == 404:
        return "provider_response_expired"
    if status_code in {401, 403}:
        return "provider_auth_failed"
    if status_code == 400:
        return "provider_request_rejected"
    return None


def _public_error(
    exc: Exception,
    *,
    submitted: bool,
    response_id: str | None,
) -> str:
    if isinstance(exc, sqlite3.Error):
        # 本地写库失败不是供应商的错；请求已发出却没记下 response id 时，
        # 结果仍是未知。
        if submitted and not response_id:
            return "submission_outcome_unknown"
        return _LOCAL_STORAGE_ERROR
    if isinstance(exc, RuntimeError):
        code = str(exc)
        if code in {
            "ai_job_heartbeat_unavailable",
            "ai_job_lease_lost",
        }:
            if submitted and not response_id:
                return "submission_outcome_unknown"
            return code
    status_code = getattr(exc, "status_code", None)
    if submitted and not response_id and not isinstance(status_code, int):
        return "submission_outcome_unknown"
    if isinstance(status_code, int):
        if status_code in {401, 403}:
            return "provider_auth_failed"
        if status_code == 429:
            return "provider_rate_limited"
        if status_code >= 500:
            return "provider_server_error"
        return "provider_request_rejected"
    if isinstance(exc, ValueError):
        if isinstance(exc, InvalidJobPayloadError):
            return "invalid_job_payload"
        code = str(exc)
        if code in {
            "ai_input_too_large",
            "ai_empty_response",
            "earnings_ticker_mismatch",
            "earnings_impacted_count_invalid",
            "news_identity_mismatch",
            "news_ticker_binding_mismatch",
            "market_focus_cycle_mismatch",
            "market_focus_as_of_mismatch",
            "market_focus_input_hash_mismatch",
            "market_focus_event_binding_mismatch",
            "market_focus_ticker_binding_mismatch",
            "signal_ticker_mismatch",
        }:
            return code
        return "schema_validation_failed"
    if isinstance(exc, RuntimeError):
        code = str(exc)
        if code in {
            "ai_not_configured",
            "ai_sdk_unavailable",
            "runtime_configuration_invalid",
        }:
            return code
    return "provider_unavailable"


async def _lease_heartbeat(
    repository: AIJobRepository,
    job_id: str,
    owner: str,
    lease_seconds: int,
    stop: asyncio.Event,
    started: asyncio.Event | None = None,
    maximum_loop_stall_seconds: float | None = None,
    lease_lost: threading.Event | None = None,
) -> None:
    interval = max(_MIN_LEASE_HEARTBEAT_INTERVAL_SECONDS, lease_seconds / 3)
    thread_stop = threading.Event()
    thread_finished = threading.Event()
    errors: list[Exception] = []
    last_loop_pulse = time.monotonic()
    last_successful_renewal = time.monotonic()
    maximum_loop_stall = float(
        maximum_loop_stall_seconds
        if maximum_loop_stall_seconds is not None
        else lease_seconds * 3.0
    )
    if maximum_loop_stall < lease_seconds:
        if started is not None:
            started.set()
        raise ValueError("maximum_loop_stall_seconds cannot be shorter than the lease")

    def record_lease_loss() -> None:
        errors.append(RuntimeError("ai_job_lease_lost"))
        if lease_lost is not None:
            lease_lost.set()

    def heartbeat() -> None:
        nonlocal last_successful_renewal
        next_delay = interval
        try:
            while not thread_stop.wait(next_delay):
                if time.monotonic() - last_loop_pulse > maximum_loop_stall:
                    record_lease_loss()
                    return
                try:
                    renewed = repository.renew_lease(
                        job_id,
                        owner,
                        lease_seconds,
                    )
                except Exception as exc:
                    record_fallback_failure("ai_job_lease_renewal", exc)
                    if time.monotonic() - last_successful_renewal >= lease_seconds:
                        record_lease_loss()
                        return
                    next_delay = min(1.0, max(0.01, interval / 4.0))
                    continue
                if not renewed:
                    record_lease_loss()
                    return
                last_successful_renewal = time.monotonic()
                next_delay = interval
        finally:
            if not thread_stop.is_set() and not errors:
                record_lease_loss()
            thread_finished.set()

    heartbeat_thread = threading.Thread(
        target=heartbeat,
        name="ai-job-heartbeat",
    )
    try:
        heartbeat_thread.start()
    except Exception as exc:
        if lease_lost is not None:
            lease_lost.set()
        if started is not None:
            started.set()
        raise RuntimeError("ai_job_heartbeat_unavailable") from exc
    if started is not None:
        started.set()
    try:
        while not stop.is_set() and not thread_finished.is_set():
            last_loop_pulse = time.monotonic()
            await asyncio.sleep(min(0.05, interval))
    finally:
        thread_stop.set()
        while heartbeat_thread.is_alive():
            await asyncio.sleep(0.01)
    if errors:
        raise errors[0]


async def _retire_stalled_response(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
    response_id: str,
) -> None:
    # A single upstream background response occupies its lane's only paid
    # slot. Do not let an indefinitely queued response block every pending
    # analysis. Ask the provider to cancel it; if the provider cannot return
    # a terminal state, retire the local lease without creating a duplicate
    # retry.
    try:
        terminal = await runtime.cancel(settings, response_id)
    except Exception as exc:
        logger.warning(
            "AI response exceeded poll window; cancellation failed (%s)",
            type(exc).__name__,
        )
        repository.fail(job["job_id"], owner, "provider_poll_timeout")
        return
    terminal_status = str(getattr(terminal, "status", "") or "")
    if terminal_status in {"queued", "in_progress"}:
        repository.fail(job["job_id"], owner, "provider_poll_timeout")
        return
    if terminal_status == "cancelled":
        # This is distinct from an unknown cancellation outcome. The
        # provider has confirmed that the old paid response is terminal, so
        # the catalyst scheduler may safely apply its existing bounded retry
        # policy without overlapping work.
        repository.fail(
            job["job_id"],
            owner,
            "provider_poll_timeout_cancelled",
            usage=runtime.response_usage(terminal),
        )
        return
    await _finish_response(repository, settings, job, owner, terminal)


async def _resume_response(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
    response_id: str,
) -> None:
    """Continue a job whose provider response id is durably linked."""

    expired = _submitted_age_seconds(job) > int(
        settings.openai_job_max_age_seconds
    )
    cancel_requested = bool(job.get("cancel_requested_at"))
    # 超过最长保留期的任务先取回一次再判过期：OpenAI 保留响应 30 天，worker
    # 停机超过一天后恢复时，已付费、可取回的结果不该被丢掉再付一次。
    use_cancel = cancel_requested and not expired
    try:
        if use_cancel:
            response = await runtime.cancel(settings, response_id)
        else:
            response = await runtime.retrieve(settings, response_id)
    except Exception as exc:
        # 供应商 SDK 与网络异常没有共同基类；下面按状态码分流，确定性的拒绝
        # 写终态，其余推迟。
        logger.warning(
            "AI response %s failed (%s)",
            "cancellation" if use_cancel else "retrieve",
            type(exc).__name__,
        )
        terminal_code = (
            "provider_response_expired" if expired else _control_error_code(exc)
        )
        if terminal_code is not None:
            repository.fail(job["job_id"], owner, terminal_code, detail=str(exc))
            return
        if _poll_window_elapsed(settings, job):
            # 持续取回失败同样受轮询超时约束：车道最多被占到轮询窗口结束，而不是
            # openai_job_max_age_seconds。取回成功时照旧先看响应状态，免得把刚
            # 完成的付费结果当成停滞响应取消掉。
            await _retire_stalled_response(
                repository,
                settings,
                job,
                owner,
                response_id,
            )
            return
        await _with_storage_retry(
            repository.defer,
            job["job_id"],
            owner,
            delay_seconds=_poll_delay(settings, int(job.get("poll_count") or 0)),
            error_code=(
                "provider_cancel_deferred"
                if use_cancel
                else "provider_poll_deferred"
            ),
        )
        return
    if expired and str(getattr(response, "status", "") or "") in {
        "queued",
        "in_progress",
    }:
        repository.fail(job["job_id"], owner, "provider_response_expired")
        return
    await _finish_response(repository, settings, job, owner, response)


async def _finish_response(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
    response: Any,
) -> None:
    status = str(getattr(response, "status", "") or "")
    response_id = str(getattr(response, "id", "") or job.get("openai_response_id") or "")
    if status in {"queued", "in_progress"}:
        if not response_id:
            repository.fail(job["job_id"], owner, "provider_response_id_missing")
            return
        if _poll_window_elapsed(settings, job):
            await _retire_stalled_response(
                repository,
                settings,
                job,
                owner,
                response_id,
            )
            return
        await _with_storage_retry(
            repository.record_background_response,
            job["job_id"],
            owner,
            response_id,
            status,
            delay_seconds=_poll_delay(settings, int(job.get("poll_count") or 0)),
            error_code=None,
        )
        return
    usage = runtime.response_usage(response)
    terminal_error = runtime.response_terminal_error(response)
    # 供应商侧错误信息落盘（第三个缺口）：余额耗尽等终态失败的 message
    # 此前不可见，只能重取响应现场复现（2026-08-14 预算误锁事故）。
    provider_detail = runtime.response_error_detail(response)
    if status == "completed":
        if terminal_error:
            repository.fail(
                job["job_id"],
                owner,
                terminal_error,
                usage=usage,
                detail=provider_detail,
            )
            return
        payload = json.loads(job["payload_json"])
        try:
            result = runtime.response_result(response, job["job_type"], payload)
        except Exception as exc:
            # 后台任务的校验失败绝大多数从这里落库（轮询完成路径）——此前
            # 只有提交路径带 detail，生产 schema_validation_failed 全是 NULL，
            # 排障只能重取响应现场复现（2026-08-09 中文校验误伤即如此）。
            repository.fail(
                job["job_id"],
                owner,
                _public_error(
                    exc,
                    submitted=True,
                    response_id=response_id or None,
                ),
                usage=usage,
                detail=str(exc),
            )
            return
        await _with_storage_retry(
            repository.complete,
            job["job_id"],
            owner,
            result,
            usage,
        )
        return
    if status == "cancelled":
        repository.mark_cancelled(job["job_id"], owner, usage=usage)
        return
    if status in {"failed", "incomplete"}:
        repository.fail(
            job["job_id"],
            owner,
            terminal_error or f"provider_{status}",
            usage=usage,
            detail=provider_detail,
        )
        return
    repository.fail(
        job["job_id"],
        owner,
        "provider_status_unsupported",
        usage=usage,
        detail=provider_detail,
    )


async def process_job(
    repository: AIJobRepository,
    settings: Any,
    job: dict[str, Any],
    owner: str,
    *,
    allow_new_submissions: bool = True,
    new_submission_block_reason: str = "analysis_disabled",
    manual_analysis_enabled: bool | Mapping[str, bool] = True,
    scheduled_analysis_enabled: bool | Mapping[str, bool] = True,
) -> None:
    stop = asyncio.Event()
    heartbeat_started = asyncio.Event()
    lease_lost = threading.Event()
    lease_seconds = int(settings.openai_job_lease_seconds)
    heartbeat = asyncio.create_task(
        _lease_heartbeat(
            repository,
            job["job_id"],
            owner,
            lease_seconds,
            stop,
            heartbeat_started,
            max(
                lease_seconds * 3.0,
                float(settings.openai_timeout_seconds) + lease_seconds,
                BREAKOUT_TASK_TIMEOUT_SECONDS + lease_seconds,
            ),
            lease_lost,
        )
    )
    submitted = bool(job.get("submission_started_at"))
    response_id = job.get("openai_response_id")
    heartbeat_failures: list[Exception] = []
    owner_task = asyncio.current_task()

    def stop_on_heartbeat_failure(task: asyncio.Task[None]) -> None:
        if stop.is_set() or task.cancelled():
            return
        error = task.exception()
        if error is None:
            return
        heartbeat_failures.append(error)
        if owner_task is not None and not owner_task.done():
            owner_task.cancel()

    def require_live_lease() -> None:
        if lease_lost.is_set():
            raise RuntimeError("ai_job_lease_lost")

    def persist_failure(code: str, detail: str | None = None) -> None:
        if response_id and code in {
            "ai_job_heartbeat_unavailable",
            "ai_job_lease_lost",
        }:
            repository.defer(
                job["job_id"],
                owner,
                delay_seconds=_poll_delay(
                    settings,
                    int(job.get("poll_count") or 0),
                ),
                error_code=code,
            )
            return
        repository.fail(job["job_id"], owner, code, detail=detail)

    def persist_local_storage_failure(exc: sqlite3.Error) -> None:
        # 本地写库失败不是供应商的错，也不能写成可自动重试的终态：那会丢掉
        # 已付费的结果、放行车道并让调度器再付一次（2026-09-25 审计）。
        try:
            if response_id:
                repository.defer(
                    job["job_id"],
                    owner,
                    delay_seconds=_poll_delay(
                        settings,
                        int(job.get("poll_count") or 0),
                    ),
                    error_code=_LOCAL_STORAGE_ERROR,
                )
            elif submitted:
                repository.fail(
                    job["job_id"],
                    owner,
                    "submission_outcome_unknown",
                    detail=str(exc),
                )
            else:
                repository.defer_unsent_submission(
                    job["job_id"],
                    owner,
                    delay_seconds=_poll_delay(
                        settings,
                        int(job.get("poll_count") or 0),
                    ),
                    error_code=_LOCAL_STORAGE_ERROR,
                )
        except (sqlite3.Error, RuntimeError) as persist_exc:
            # 推迟也写不进去就什么都不写：租约到期后重新认领，从持久状态接着
            # 走（有 response id 的会重新取回，不会重复提交）。
            record_fallback_failure("ai_job_local_storage_persist", persist_exc)

    def persist_failure_or_record(code: str, detail: str) -> None:
        try:
            persist_failure(code, detail=detail)
        except Exception as persist_exc:
            # 落库失败本身也要留痕：任务留着租约，到期后重新认领。
            record_fallback_failure("ai_job_failure_persist", persist_exc)

    heartbeat.add_done_callback(stop_on_heartbeat_failure)
    try:
        await heartbeat_started.wait()
        if heartbeat.done():
            await heartbeat
        require_live_lease()
        if response_id:
            await _resume_response(
                repository,
                settings,
                job,
                owner,
                str(response_id),
            )
            return

        if submitted:
            # 提交已开始却没有 response id：请求可能已经发出，结果未知。用户的
            # 取消请求也不能把它改写成普通取消，否则 unknown 的占道与当日
            # token 预留一起消失（2026-09-25 审计）。
            repository.fail(
                job["job_id"],
                owner,
                "submission_outcome_unknown",
            )
            return

        if job.get("cancel_requested_at"):
            repository.mark_cancelled(job["job_id"], owner)
            return

        if not allow_new_submissions:
            await _with_storage_retry(
                repository.defer,
                job["job_id"],
                owner,
                delay_seconds=_NEW_SUBMISSION_RETRY_SECONDS,
                error_code=new_submission_block_reason,
            )
            return

        submission_source = (
            str(job.get("submission_source"))
            if job.get("submission_source") in {"manual", "scheduled"}
            else "manual"
        )
        scheduled_enabled_for_job = (
            bool(scheduled_analysis_enabled.get(str(job.get("job_type")), False))
            if isinstance(scheduled_analysis_enabled, Mapping)
            else bool(scheduled_analysis_enabled)
        )
        manual_enabled_for_job = (
            bool(manual_analysis_enabled.get(str(job.get("job_type")), False))
            if isinstance(manual_analysis_enabled, Mapping)
            else bool(manual_analysis_enabled)
        )
        source_disabled_error = (
            "manual_analysis_disabled"
            if submission_source == "manual" and not manual_enabled_for_job
            else "scheduled_analysis_disabled"
            if submission_source == "scheduled" and not scheduled_enabled_for_job
            else None
        )
        if source_disabled_error is not None:
            # This task has never reached the provider. Make the disabled
            # decision terminal so a later switch change cannot revive old
            # queue entries and unexpectedly spend money.
            repository.fail(job["job_id"], owner, source_disabled_error)
            return

        current_schema_version, current_schema_sha256 = runtime.schema_identity(
            job["job_type"]
        )
        if (
            job["model"] != runtime.OFFICIAL_OPENAI_MODEL
            or job["reasoning"] != runtime.OFFICIAL_REASONING_EFFORT
            or job["execution_mode"] != runtime.OFFICIAL_EXECUTION_MODE
            or not runtime.runtime_configuration_valid(settings)
            or job["schema_version"] != current_schema_version
            or job["schema_sha256"] != current_schema_sha256
        ):
            repository.fail(
                job["job_id"],
                owner,
                "runtime_configuration_changed",
            )
            return

        require_live_lease()
        payload = json.loads(job["payload_json"])
        prepared = runtime.prepare_background(
            settings,
            job["job_type"],
            payload,
        )
        require_live_lease()
        try:
            submission_state = repository.mark_submission_started(
                job["job_id"],
                owner,
                daily_limit=int(settings.openai_daily_max_jobs),
                daily_budget_usd=float(settings.openai_daily_budget_usd),
                daily_token_limit=int(settings.openai_daily_token_limit),
                cooldown_seconds=int(settings.openai_manual_cooldown_seconds),
                unknown_submission_hold_seconds=int(
                    settings.openai_job_max_age_seconds
                ),
            )
        except RuntimeError as exc:
            if str(exc) == "ai_job_not_submittable":
                return
            raise
        if submission_state != "started":
            return
        persisted_job = repository.get_job(job["job_id"])
        if (
            persisted_job is None
            or not persisted_job.get("submission_started_at")
            or not persisted_job.get("submitted_at")
        ):
            raise RuntimeError("ai_job_submission_state_missing")
        # The claimed row can be much older than the provider submission.
        # Keep timeout decisions anchored to the timestamps written by
        # mark_submission_started instead of falling back to created_at.
        job.update(persisted_job)
        # Every local validation and SDK construction step has completed. From
        # this point an exception can represent a request whose upstream
        # outcome is unknown, so it must consume both budget and concurrency.
        require_live_lease()
        submitted = True
        response = await runtime.submit_background(
            settings,
            job["job_type"],
            payload,
            prepared=prepared,
        )
        submitted_response_id = str(getattr(response, "id", "") or "") or None
        if not submitted_response_id:
            repository.fail(
                job["job_id"],
                owner,
                "submission_outcome_unknown",
            )
            return
        await _with_storage_retry(
            repository.link_background_response,
            job["job_id"],
            owner,
            submitted_response_id,
        )
        # Treat the upstream identity as recoverable only after it is
        # durably linked. A failed link otherwise makes an untracked paid
        # response look retryable and can submit the same job twice.
        response_id = submitted_response_id
        job["openai_response_id"] = response_id
        await _finish_response(repository, settings, job, owner, response)
    except asyncio.CancelledError:
        if not heartbeat_failures:
            raise
        exc = heartbeat_failures[0]
        code = _public_error(
            exc,
            submitted=submitted,
            response_id=response_id,
        )
        logger.warning("AI job stopped after heartbeat failure (%s)", code)
        persist_failure_or_record(code, str(exc))
    except sqlite3.Error as exc:
        logger.warning("AI job local storage failed (%s)", type(exc).__name__)
        record_fallback_failure("ai_job_local_storage", exc)
        persist_local_storage_failure(exc)
    except Exception as exc:
        code = _public_error(
            exc,
            submitted=submitted,
            response_id=response_id,
        )
        logger.warning("AI job failed (%s, %s)", code, type(exc).__name__)
        # str(exc) 对校验失败是 pydantic 的字段路径+规则消息——这正是
        # 「schema_validation_failed 到底挂在哪条规则」的现场证据。
        persist_failure_or_record(code, str(exc))
    finally:
        heartbeat.remove_done_callback(stop_on_heartbeat_failure)
        stop.set()
        try:
            await heartbeat
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            code = _public_error(
                exc,
                submitted=submitted,
                response_id=response_id,
            )
            logger.warning("AI job heartbeat failed (%s)", code)
            persist_failure_or_record(code, str(exc))


async def run_once(
    repository: AIJobRepository,
    settings: Any,
    owner: str,
    *,
    allow_new_submissions: bool = True,
    new_submission_block_reason: str = "analysis_disabled",
    manual_analysis_enabled: bool | Mapping[str, bool] = True,
    scheduled_analysis_enabled: bool | Mapping[str, bool] = True,
) -> int:
    job = await asyncio.to_thread(
        repository.claim_due,
        owner,
        int(settings.openai_job_lease_seconds),
        cooldown_seconds=int(settings.openai_manual_cooldown_seconds),
        unknown_submission_hold_seconds=int(settings.openai_job_max_age_seconds),
    )
    if not job:
        return 0
    await process_job(
        repository,
        settings,
        job,
        owner,
        allow_new_submissions=allow_new_submissions,
        new_submission_block_reason=new_submission_block_reason,
        manual_analysis_enabled=manual_analysis_enabled,
        scheduled_analysis_enabled=scheduled_analysis_enabled,
    )
    return 1


async def run_configured_once(
    repository: AIJobRepository,
    settings: Any,
    owner: str,
    *,
    personal_config: Any | None = None,
) -> tuple[int, str]:
    """Run one due job using the latest non-secret runtime controls.

    Invalid runtime settings pause only new provider submissions. Jobs that
    already have an upstream response, an uncertain submission outcome, or a
    cancellation request still pass through ``process_job`` and can finish.
    """

    from app.services.runtime_settings import (
        RuntimeSettingsStorageError,
        get_effective_runtime_settings,
    )

    try:
        effective = get_effective_runtime_settings()
    except RuntimeSettingsStorageError:
        processed = await run_once(
            repository,
            settings,
            owner,
            allow_new_submissions=False,
            new_submission_block_reason="runtime_settings_unavailable",
        )
        return processed, "runtime_settings_unavailable"

    if personal_config is None:
        from app.personal_config import get_personal_config

        personal_config = get_personal_config()
    mode_allows_manual, mode_allows_scheduled = _personal_analysis_permissions(
        personal_config
    )

    effective_settings = settings.model_copy(
        update={
            "openai_daily_max_jobs": effective.ai.daily_max_jobs,
            "openai_daily_budget_usd": effective.ai.daily_budget_usd,
            "openai_daily_token_limit": int(effective.ai.daily_token_limit),
            "openai_manual_cooldown_seconds": (
                effective.ai.manual_analysis_cooldown_seconds
            ),
        }
    )
    catalyst_manual_analysis_enabled = bool(
        mode_allows_manual and effective.ai.manual_analysis_enabled
    )
    earnings_manual_analysis_enabled = bool(effective.ai.manual_analysis_enabled)
    manual_analysis_enabled = {
        "news_impact": catalyst_manual_analysis_enabled,
        "market_focus": catalyst_manual_analysis_enabled,
        "option_alerts": catalyst_manual_analysis_enabled,
        "signal_analysis": catalyst_manual_analysis_enabled,
        "earnings_impact": earnings_manual_analysis_enabled,
    }
    catalyst_scheduled_analysis_enabled = bool(
        mode_allows_scheduled and effective.catalyst.scheduled_analysis_enabled
    )
    earnings_scheduled_analysis_enabled = bool(
        effective.earnings.scheduled_analysis_enabled
    )
    scheduled_analysis_enabled = {
        "news_impact": catalyst_scheduled_analysis_enabled,
        "market_focus": catalyst_scheduled_analysis_enabled,
        "earnings_impact": earnings_scheduled_analysis_enabled,
    }
    analysis_enabled = bool(
        any(manual_analysis_enabled.values())
        or catalyst_scheduled_analysis_enabled
        or earnings_scheduled_analysis_enabled
    )
    processed = await run_once(
        repository,
        effective_settings,
        owner,
        # The source-specific switches below decide whether an unsubmitted
        # task may proceed. The global flag remains reserved for an unreadable
        # runtime document, which is retryable rather than terminal.
        allow_new_submissions=True,
        new_submission_block_reason="analysis_disabled",
        manual_analysis_enabled=manual_analysis_enabled,
        scheduled_analysis_enabled=scheduled_analysis_enabled,
    )
    return processed, "enabled" if analysis_enabled else "analysis_disabled"


def _next_iteration_delay(processed: int, runtime_state: str) -> float:
    if processed:
        return 0.5
    return 2.0 if runtime_state == "enabled" else 30.0


def health_payload(repository: AIJobRepository, settings: Any) -> dict[str, Any]:
    payload = repository.health()
    configured = bool(settings.openai_api_key.get_secret_value().strip())
    capability = runtime.capability_status(settings)
    payload.update(
        {
            "status": (
                payload["status"]
                if not payload["healthy"]
                else capability["status"]
                if configured
                else "disabled"
            ),
            "configured": configured,
            "provider_capability_supported": bool(capability.get("supported")),
            "sdk_capability_supported": bool(capability.get("sdk_supported")),
            "methods": capability.get("methods", {}),
            "model": runtime.OFFICIAL_OPENAI_MODEL,
            "reasoning": runtime.OFFICIAL_REASONING_EFFORT,
            "execution_mode": runtime.OFFICIAL_EXECUTION_MODE,
        }
    )
    return payload


async def run_forever() -> None:
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    repository.initialize()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:12]}"
    while True:
        if not settings.openai_api_key.get_secret_value().strip():
            await asyncio.sleep(30)
            continue
        processed, runtime_state = await run_configured_once(
            repository,
            settings,
            owner,
        )
        await asyncio.sleep(_next_iteration_delay(processed, runtime_state))


def main() -> None:
    parser = argparse.ArgumentParser(description="Option Pro persistent AI worker")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--healthcheck", action="store_true")
    args = parser.parse_args()
    settings = get_settings()
    repository = AIJobRepository(settings.openai_job_db_path)
    if args.healthcheck:
        payload = health_payload(repository, settings)
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        raise SystemExit(0 if payload["healthy"] else 1)
    if args.once:
        if not settings.openai_api_key.get_secret_value().strip():
            print(
                json.dumps(
                    {
                        "status": "disabled",
                        "processed": 0,
                        "as_of": datetime.now(timezone.utc).isoformat(),
                    },
                    separators=(",", ":"),
                )
            )
            return
        owner = f"once-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        processed, runtime_state = asyncio.run(
            run_configured_once(repository, settings, owner)
        )
        worker_status = (
            "degraded"
            if runtime_state == "runtime_settings_unavailable"
            else "disabled"
            if runtime_state == "analysis_disabled"
            else "completed"
        )
        print(
            json.dumps(
                {
                    "status": worker_status,
                    "processed": processed,
                    "runtime_state": runtime_state,
                },
                separators=(",", ":"),
            )
        )
        return
    try:
        asyncio.run(run_forever())
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
