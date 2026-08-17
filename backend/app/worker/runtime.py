from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
import sqlite3
import threading
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from .lock import ProcessFileLock
from .state import (
    WorkerAlreadyRunning,
    WorkerLeaseLost,
    WorkerStateRepository,
    utc_now,
)


logger = logging.getLogger("optix.worker")
_TASK_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,119}$")
TaskStatus = Literal["idle", "paused", "disabled", "degraded"]


@dataclass(frozen=True)
class TaskResult:
    status: TaskStatus = "idle"
    details: Mapping[str, Any] = field(default_factory=dict)
    next_delay_seconds: float | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"idle", "paused", "disabled", "degraded"}:
            raise ValueError("task status is invalid")
        if self.next_delay_seconds is not None and (
            not math.isfinite(self.next_delay_seconds)
            or self.next_delay_seconds < 0
            or self.next_delay_seconds > 86_400
        ):
            raise ValueError("task delay must be between 0 and 86400 seconds")
        if self.error_code is not None and not _ERROR_CODE.fullmatch(self.error_code):
            raise ValueError("task error code is invalid")


@dataclass(frozen=True)
class TaskSpec:
    name: str
    runner: Callable[[], Awaitable[TaskResult] | TaskResult]
    interval_seconds: float
    enabled: bool = True
    timeout_seconds: float | None = None
    failure_backoff_seconds: float = 5.0
    max_backoff_seconds: float = 900.0
    drain_on_shutdown: bool = False
    manual_only: bool = False
    may_block_event_loop: bool = False
    # 重启后沿用状态库里持久化的 next_run_at，而不是立即执行一轮。
    # 只给重负载低频任务（如全量备份）用：进程重启（部署或崩溃恢复）
    # 不应触发一次计划外的 GB 级磁盘拷贝。
    honor_persisted_schedule: bool = False
    close: Callable[[], Awaitable[None] | None] | None = None

    def __post_init__(self) -> None:
        if not _TASK_NAME.fullmatch(self.name):
            raise ValueError("task name must use lower-case snake_case")
        for name, value in (
            ("interval", self.interval_seconds),
            ("failure backoff", self.failure_backoff_seconds),
            ("maximum backoff", self.max_backoff_seconds),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"task {name} must be positive")
        if self.max_backoff_seconds < self.failure_backoff_seconds:
            raise ValueError("maximum backoff cannot be smaller than initial backoff")
        if self.timeout_seconds is not None and (
            not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0
        ):
            raise ValueError("task timeout must be positive")
        if self.may_block_event_loop and self.timeout_seconds is None:
            raise ValueError("an event-loop-blocking task requires a timeout")


def _public_error_code(error: Exception) -> str:
    value = str(getattr(error, "code", "") or "")
    if _ERROR_CODE.fullmatch(value):
        return value
    if isinstance(error, asyncio.TimeoutError):
        return "task_timeout"
    return "task_failed"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


class WorkerSupervisor:
    """Run isolated task loops behind one fenced process lease."""

    def __init__(
        self,
        repository: WorkerStateRepository,
        tasks: Sequence[TaskSpec],
        *,
        owner_id: str,
        lease_seconds: float = 60.0,
        shutdown_grace_seconds: float = 30.0,
        stop: asyncio.Event | None = None,
        process_lock: ProcessFileLock | None = None,
    ) -> None:
        names = [task.name for task in tasks]
        if len(names) != len(set(names)):
            raise ValueError("worker task names must be unique")
        if lease_seconds <= 0 or shutdown_grace_seconds < 0:
            raise ValueError("worker timing values are invalid")
        self.repository = repository
        self.tasks = tuple(tasks)
        self.owner_id = owner_id
        self.lease_seconds = float(lease_seconds)
        self.shutdown_grace_seconds = float(shutdown_grace_seconds)
        self.stop = stop or asyncio.Event()
        self.process_lock = process_lock or ProcessFileLock(
            Path(str(repository.path) + ".lock")
        )
        self._heartbeat_stop = asyncio.Event()
        self._lease_lost = asyncio.Event()
        self._token: int | None = None
        self._failures: dict[str, int] = {task.name: 0 for task in self.tasks}
        self._results: dict[str, dict[str, Any]] = {}

    def request_stop(self) -> None:
        """Stop accepting new task runs; in-flight paid work is drained."""

        self.stop.set()

    # 状态库写入的锁竞争兜底：维护任务备份 GB 级数据库时，同盘 I/O 风暴能把
    # BEGIN IMMEDIATE 拖过 busy_timeout。状态记录是遥测不是正确性——这里失败
    # 绝不能杀死任务循环（循环死→supervisor 杀进程→重启→备份从头再来→
    # 风暴延续，2026-08-13/08-15 两次生产崩溃循环即此自馈闭环）。
    # 租约围栏（WorkerLeaseLost）不属于锁竞争，照常上抛。
    _STATE_LOCK_RETRIES = 3
    _STATE_LOCK_RETRY_DELAY_SECONDS = 2.0

    async def _state_call(self, func: Callable[..., Any], /, *args: Any) -> Any:
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(func, *args)
            except sqlite3.OperationalError:
                attempt += 1
                if attempt > self._STATE_LOCK_RETRIES:
                    raise
                await asyncio.sleep(self._STATE_LOCK_RETRY_DELAY_SECONDS * attempt)

    async def _record(self, task: TaskSpec, **values: Any) -> None:
        token = self._token
        if token is None:
            raise WorkerLeaseLost("unified worker lease is unavailable")

        def write() -> None:
            self.repository.record_task(
                self.owner_id,
                token,
                task.name,
                enabled=task.enabled,
                **values,
            )

        try:
            await self._state_call(write)
        except sqlite3.OperationalError as error:
            logger.warning(
                "worker task status write dropped task=%s error=%s",
                task.name,
                error,
            )

    async def _finish_actions_guarded(
        self,
        task: TaskSpec,
        token: int,
        request_ids: Sequence[str],
        **values: Any,
    ) -> None:
        def write() -> None:
            self.repository.finish_actions(
                self.owner_id,
                token,
                list(request_ids),
                **values,
            )

        try:
            await self._state_call(write)
        except sqlite3.OperationalError as error:
            # 未标记完成的手动请求留给死租约回收；比让循环陪葬好。
            logger.warning(
                "worker manual action finish dropped task=%s error=%s",
                task.name,
                error,
            )

    async def _heartbeat(self, started: asyncio.Event | None = None) -> None:
        interval = max(0.05, min(30.0, self.lease_seconds / 3.0))
        thread_stop = threading.Event()
        thread_finished = threading.Event()
        lease_lost = threading.Event()
        last_loop_pulse = time.monotonic()
        last_successful_renewal = time.monotonic()
        blocking_timeouts = [
            float(task.timeout_seconds)
            for task in self.tasks
            if task.enabled
            and task.may_block_event_loop
            and task.timeout_seconds is not None
        ]
        maximum_loop_stall = max(
            self.lease_seconds * 3.0,
            max(blocking_timeouts, default=0.0) + self.lease_seconds,
        )

        def heartbeat() -> None:
            nonlocal last_successful_renewal
            next_delay = interval
            try:
                while not thread_stop.wait(next_delay):
                    if time.monotonic() - last_loop_pulse > maximum_loop_stall:
                        lease_lost.set()
                        return
                    token = self._token
                    if token is None:
                        lease_lost.set()
                        return
                    try:
                        renewed = self.repository.renew(
                            self.owner_id,
                            token,
                            lease_seconds=self.lease_seconds,
                        )
                    except Exception:
                        if (
                            time.monotonic() - last_successful_renewal
                            >= self.lease_seconds
                        ):
                            lease_lost.set()
                            return
                        next_delay = min(1.0, max(0.01, interval / 4.0))
                        continue
                    if not renewed:
                        lease_lost.set()
                        return
                    last_successful_renewal = time.monotonic()
                    next_delay = interval
            finally:
                if not thread_stop.is_set() and not lease_lost.is_set():
                    lease_lost.set()
                thread_finished.set()

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name="worker-heartbeat",
        )
        try:
            heartbeat_thread.start()
        except Exception as exc:
            if started is not None:
                started.set()
            self._lease_lost.set()
            self.stop.set()
            raise WorkerLeaseLost("unified worker heartbeat could not start") from exc
        if started is not None:
            started.set()
        try:
            while (
                not self._heartbeat_stop.is_set()
                and not thread_finished.is_set()
            ):
                last_loop_pulse = time.monotonic()
                await asyncio.sleep(min(0.05, interval))
            if lease_lost.is_set():
                self._lease_lost.set()
                self.stop.set()
        finally:
            thread_stop.set()
            while heartbeat_thread.is_alive():
                await asyncio.sleep(0.01)

    def _backoff(self, task: TaskSpec, failures: int) -> float:
        return min(
            task.max_backoff_seconds,
            task.failure_backoff_seconds * (2 ** min(max(failures - 1, 0), 10)),
        )

    async def _execute(self, task: TaskSpec) -> dict[str, Any]:
        started = utc_now()
        await self._record(
            task,
            status="running",
            consecutive_failures=self._failures[task.name],
            last_started_at=started,
            details={},
        )
        token = self._token
        if token is None:
            raise WorkerLeaseLost("unified worker lease is unavailable")
        try:
            manual_actions = await self._state_call(
                self.repository.claim_actions,
                self.owner_id,
                token,
                task.name,
            )
        except sqlite3.OperationalError as error:
            # 认领不到就整轮跳过：不确定手动请求是否在场时直接跑 runner
            # 会让手动动作在下一轮被再次认领、重复执行。
            logger.warning(
                "worker task round skipped (state locked) task=%s error=%s",
                task.name,
                error,
            )
            payload = {
                "status": "degraded",
                "error_code": "worker_state_locked",
                "next_delay_seconds": self._backoff(task, 1),
            }
            self._results[task.name] = payload
            return payload
        manual_request_ids = [item["request_id"] for item in manual_actions]
        try:
            action_runner = getattr(task.runner, "run_for_actions", None)
            operation = _maybe_await(
                action_runner(manual_actions)
                if manual_actions and callable(action_runner)
                else task.runner()
            )
            if task.timeout_seconds is None:
                result = await operation
            else:
                result = await asyncio.wait_for(operation, timeout=task.timeout_seconds)
            if not isinstance(result, TaskResult):
                raise TypeError("worker task must return TaskResult")
        except asyncio.CancelledError:
            if manual_request_ids:
                await self._finish_actions_guarded(
                    task,
                    token,
                    manual_request_ids,
                    succeeded=False,
                    error_code="shutdown_cancelled",
                    details={
                        "task_status": "interrupted",
                        "task_completed_at": utc_now().isoformat().replace(
                            "+00:00", "Z"
                        ),
                    },
                    now=utc_now(),
                )
            raise
        except Exception as error:
            failures = self._failures[task.name] + 1
            self._failures[task.name] = failures
            delay = self._backoff(task, failures)
            completed = utc_now()
            error_code = _public_error_code(error)
            details = {"error_type": type(error).__name__}
            await self._record(
                task,
                status="degraded",
                consecutive_failures=failures,
                last_completed_at=completed,
                next_run_at=(
                    None
                    if task.manual_only
                    else completed + timedelta(seconds=delay)
                ),
                error_code=error_code,
                details=details,
            )
            if manual_request_ids:
                await self._finish_actions_guarded(
                    task,
                    token,
                    manual_request_ids,
                    succeeded=False,
                    error_code=error_code,
                    details={
                        "task_status": "degraded",
                        "task_completed_at": completed.isoformat().replace(
                            "+00:00", "Z"
                        ),
                        "result": details,
                    },
                    now=completed,
                )
            payload = {
                "status": "degraded",
                "error_code": error_code,
                "next_delay_seconds": delay,
            }
            self._results[task.name] = payload
            logger.warning(
                "worker task failed task=%s error_type=%s",
                task.name,
                type(error).__name__,
            )
            return payload

        completed = utc_now()
        degraded = result.status == "degraded"
        failures = self._failures[task.name] + 1 if degraded else 0
        self._failures[task.name] = failures
        delay = (
            result.next_delay_seconds
            if result.next_delay_seconds is not None
            else self._backoff(task, failures)
            if degraded
            else task.interval_seconds
        )
        try:
            await self._record(
                task,
                status=result.status,
                consecutive_failures=failures,
                last_completed_at=completed,
                last_success_at=None if degraded else completed,
                next_run_at=(
                    None
                    if task.manual_only
                    else completed + timedelta(seconds=delay)
                ),
                error_code=result.error_code,
                details=result.details,
            )
        except WorkerLeaseLost:
            raise
        except (TypeError, ValueError) as error:
            failures += 1
            self._failures[task.name] = failures
            delay = self._backoff(task, failures)
            await self._record(
                task,
                status="degraded",
                consecutive_failures=failures,
                last_completed_at=completed,
                next_run_at=(
                    None
                    if task.manual_only
                    else completed + timedelta(seconds=delay)
                ),
                error_code="task_result_invalid",
                details={"error_type": type(error).__name__},
            )
            result = TaskResult(
                status="degraded",
                error_code="task_result_invalid",
                next_delay_seconds=delay,
            )
            degraded = True
        payload = {
            "status": result.status,
            "error_code": result.error_code,
            "next_delay_seconds": delay,
        }
        if manual_request_ids:
            await self._finish_actions_guarded(
                task,
                token,
                manual_request_ids,
                succeeded=not degraded,
                error_code=result.error_code or "task_degraded",
                details={
                    "task_status": result.status,
                    "task_completed_at": completed.isoformat().replace(
                        "+00:00", "Z"
                    ),
                    "result": dict(result.details),
                },
                now=completed,
            )
        self._results[task.name] = payload
        return payload

    async def _persisted_initial_delay(self, task: TaskSpec) -> float:
        """Resume the stored schedule so restarts do not re-run heavy tasks."""

        try:
            states = await asyncio.to_thread(self.repository.task_states)
        except (sqlite3.OperationalError, OSError, ValueError):
            return 0.0
        row = next(
            (item for item in states if item.get("task_name") == task.name),
            None,
        )
        raw = str((row or {}).get("next_run_at") or "")
        if not raw:
            return 0.0
        try:
            next_run = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return 0.0
        if next_run.tzinfo is None or next_run.utcoffset() is None:
            return 0.0
        remaining = (next_run - utc_now()).total_seconds()
        # 上限一个周期：时钟异常或脏数据不能把任务锁死到远未来。
        return min(max(0.0, remaining), task.interval_seconds)

    async def _has_pending_actions(self, task: TaskSpec) -> bool:
        # 轮询本身每 0.5s 一次，锁竞争时当作「暂无手动请求」继续等即可。
        try:
            return bool(
                await asyncio.to_thread(
                    self.repository.has_pending_actions,
                    task.name,
                )
            )
        except sqlite3.OperationalError:
            return False

    async def _wait_for_next(self, task: TaskSpec, delay: float) -> bool:
        """Wake scheduled loops promptly when the API queues a manual action."""

        if self.stop.is_set():
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, delay)
        while not self.stop.is_set():
            if await self._has_pending_actions(task):
                return True
            remaining = deadline - loop.time()
            if remaining <= 0:
                return True
            try:
                await asyncio.wait_for(
                    self.stop.wait(),
                    timeout=min(0.5, remaining),
                )
                return False
            except asyncio.TimeoutError:
                continue
        return False

    async def _wait_for_manual_action(self, task: TaskSpec) -> bool:
        """Wait indefinitely; manual-only tasks have no scheduled deadline."""

        while not self.stop.is_set():
            if await self._has_pending_actions(task):
                return True
            try:
                await asyncio.wait_for(self.stop.wait(), timeout=0.5)
                return False
            except asyncio.TimeoutError:
                continue
        return False

    async def _task_loop(self, task: TaskSpec) -> None:
        if not task.enabled:
            await self._record(
                task,
                status="disabled",
                consecutive_failures=0,
                details={},
            )
            return
        if task.manual_only:
            await self._record(
                task,
                status="idle",
                consecutive_failures=0,
                details={"mode": "manual", "waiting": True},
            )
            try:
                while not self.stop.is_set():
                    if not await self._wait_for_manual_action(task):
                        return
                    if self.stop.is_set():
                        return
                    await self._execute(task)
            except asyncio.CancelledError:
                if not self._lease_lost.is_set():
                    try:
                        await self._record(
                            task,
                            status="interrupted",
                            consecutive_failures=self._failures[task.name],
                            last_completed_at=utc_now(),
                            error_code="shutdown_cancelled",
                            details={"mode": "manual"},
                        )
                    except WorkerLeaseLost:
                        pass
                raise
            return
        delay = 0.0
        if task.honor_persisted_schedule:
            delay = await self._persisted_initial_delay(task)
        try:
            while not self.stop.is_set():
                if delay > 0 and not await self._wait_for_next(task, delay):
                    return
                if self.stop.is_set():
                    return
                result = await self._execute(task)
                delay = float(result["next_delay_seconds"])
        except asyncio.CancelledError:
            if not self._lease_lost.is_set():
                try:
                    await self._record(
                        task,
                        status="interrupted",
                        consecutive_failures=self._failures[task.name],
                        last_completed_at=utc_now(),
                        error_code="shutdown_cancelled",
                        details={},
                    )
                except WorkerLeaseLost:
                    pass
            raise

    async def _close_tasks(self) -> None:
        for task in reversed(self.tasks):
            if task.close is None:
                continue
            try:
                await _maybe_await(task.close())
            except Exception as error:
                logger.warning(
                    "worker task close failed task=%s error_type=%s",
                    task.name,
                    type(error).__name__,
                )

    async def _drain(self, loops: Mapping[TaskSpec, asyncio.Task[None]]) -> None:
        regular = [loop for spec, loop in loops.items() if not spec.drain_on_shutdown]
        protected = [loop for spec, loop in loops.items() if spec.drain_on_shutdown]
        if regular:
            _, pending = await asyncio.wait(
                regular,
                timeout=self.shutdown_grace_seconds,
            )
            for loop in pending:
                loop.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        # Paid AI work has its own bounded task timeout and durable submission
        # state. Keep the total worker lease alive until it reaches that safe
        # boundary instead of cancelling it during the response-id write gap.
        if protected:
            await asyncio.gather(*protected, return_exceptions=True)

    async def _wait_for_stop_or_loop_failure(
        self,
        loops: Mapping[TaskSpec, asyncio.Task[None]],
    ) -> None:
        """Stop the process when an enabled task loop exits unexpectedly."""

        stop_waiter = asyncio.create_task(self.stop.wait(), name="worker-stop-waiter")
        try:
            done, _pending = await asyncio.wait(
                (stop_waiter, *loops.values()),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_waiter in done:
                return

            self.stop.set()
            failed = next(loop for loop in loops.values() if loop in done)
            # 带上任务名：崩溃循环时（生产实测一夜 85 次重启）日志必须能一眼
            # 看出死的是哪个循环，而不是匿名的「task loop failed」。
            failed_name = next(
                (spec.name for spec, loop in loops.items() if loop is failed),
                failed.get_name(),
            )
            if failed.cancelled():
                raise RuntimeError(
                    f"worker task loop was cancelled unexpectedly: {failed_name}"
                )
            error = failed.exception()
            if error is None:
                raise RuntimeError(
                    f"worker task loop exited unexpectedly: {failed_name}"
                )
            raise RuntimeError(f"worker task loop failed: {failed_name}") from error
        finally:
            if not stop_waiter.done():
                stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)

    async def _run(self, *, once: bool) -> dict[str, Any]:
        if not await asyncio.to_thread(self.process_lock.acquire, self.owner_id):
            raise WorkerAlreadyRunning("another unified worker holds the process file lock")
        heartbeat: asyncio.Task[None] | None = None
        loops: dict[TaskSpec, asyncio.Task[None]] = {}
        completed_normally = False
        try:
            await asyncio.to_thread(self.repository.initialize)
            token = await asyncio.to_thread(
                self.repository.acquire,
                self.owner_id,
                lease_seconds=self.lease_seconds,
                # Holding the OS file lock proves that a previous process is
                # gone even when its last SQLite lease has not expired yet.
                force=True,
            )
            if token is None:
                raise WorkerAlreadyRunning("another unified worker holds the process lease")
            self._token = token
            await asyncio.to_thread(
                self.repository.recover_interrupted,
                self.owner_id,
                token,
            )
            await asyncio.to_thread(
                self.repository.reconcile_task_status,
                self.owner_id,
                token,
                tuple(task.name for task in self.tasks),
            )
            heartbeat_started = asyncio.Event()
            heartbeat = asyncio.create_task(
                self._heartbeat(heartbeat_started),
                name="worker-heartbeat",
            )
            await heartbeat_started.wait()
            if heartbeat.done():
                await heartbeat
            if once:
                for task in self.tasks:
                    if not task.enabled:
                        await self._record(
                            task,
                            status="disabled",
                            consecutive_failures=0,
                            details={},
                        )
                        self._results[task.name] = {
                            "status": "disabled",
                            "error_code": None,
                            "next_delay_seconds": task.interval_seconds,
                        }
                        continue
                    if task.manual_only and not await asyncio.to_thread(
                        self.repository.has_pending_actions,
                        task.name,
                    ):
                        await self._record(
                            task,
                            status="idle",
                            consecutive_failures=0,
                            details={"mode": "manual", "waiting": True},
                        )
                        self._results[task.name] = {
                            "status": "idle",
                            "error_code": None,
                            "next_delay_seconds": task.interval_seconds,
                            "manual_only": True,
                        }
                        continue
                    await self._execute(task)
            else:
                for task in self.tasks:
                    if task.enabled:
                        continue
                    await self._record(
                        task,
                        status="disabled",
                        consecutive_failures=0,
                        details={},
                    )
                    self._results[task.name] = {
                        "status": "disabled",
                        "error_code": None,
                        "next_delay_seconds": task.interval_seconds,
                    }
                loops = {
                    task: asyncio.create_task(
                        self._task_loop(task),
                        name=f"worker-task-{task.name}",
                    )
                    for task in self.tasks
                    if task.enabled
                }
                await self._wait_for_stop_or_loop_failure(loops)
                await self._drain(loops)
            if self._lease_lost.is_set():
                raise WorkerLeaseLost("unified worker lease was lost")
            completed_normally = True
            return {
                "status": "completed" if once else "stopped",
                "tasks": dict(self._results),
            }
        finally:
            if loops:
                for loop in loops.values():
                    if not loop.done():
                        loop.cancel()
                await asyncio.gather(*loops.values(), return_exceptions=True)
            await self._close_tasks()
            self._heartbeat_stop.set()
            if heartbeat is not None:
                await asyncio.gather(heartbeat, return_exceptions=True)
            if self._token is not None:
                if not once and completed_normally and not self._lease_lost.is_set():
                    await asyncio.to_thread(
                        self.repository.reconcile_task_status,
                        self.owner_id,
                        self._token,
                        (),
                    )
                await asyncio.to_thread(
                    self.repository.release,
                    self.owner_id,
                    self._token,
                )
            self._token = None
            await asyncio.to_thread(self.process_lock.release)

    async def run_once(self) -> dict[str, Any]:
        return await self._run(once=True)

    async def run_forever(self) -> dict[str, Any]:
        return await self._run(once=False)


__all__ = ["TaskResult", "TaskSpec", "WorkerSupervisor"]
