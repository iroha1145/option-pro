from __future__ import annotations

import asyncio
import inspect
import logging
import math
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
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

    async def _record(self, task: TaskSpec, **values: Any) -> None:
        token = self._token
        if token is None:
            raise WorkerLeaseLost("unified worker lease is unavailable")
        await asyncio.to_thread(
            self.repository.record_task,
            self.owner_id,
            token,
            task.name,
            enabled=task.enabled,
            **values,
        )

    async def _heartbeat(self) -> None:
        interval = max(0.05, min(30.0, self.lease_seconds / 3.0))
        loop = asyncio.get_running_loop()
        # Scheduled scans and reconciliation share asyncio's default executor.
        # Lease renewal must remain runnable even when every general-purpose
        # worker thread is occupied by a long provider or SQLite operation.
        with ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="worker-heartbeat",
        ) as executor:
            while not self._heartbeat_stop.is_set():
                try:
                    await asyncio.wait_for(
                        self._heartbeat_stop.wait(),
                        timeout=interval,
                    )
                    return
                except asyncio.TimeoutError:
                    pass
                token = self._token
                if token is None:
                    return
                try:
                    renewed = await loop.run_in_executor(
                        executor,
                        partial(
                            self.repository.renew,
                            self.owner_id,
                            token,
                            lease_seconds=self.lease_seconds,
                        ),
                    )
                except Exception:
                    renewed = False
                if not renewed:
                    self._lease_lost.set()
                    self.stop.set()
                    return

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
        manual_actions = await asyncio.to_thread(
            self.repository.claim_actions,
            self.owner_id,
            token,
            task.name,
        )
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
                await asyncio.to_thread(
                    self.repository.finish_actions,
                    self.owner_id,
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
            await asyncio.to_thread(
                self.repository.finish_actions,
                self.owner_id,
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

    async def _wait_for_next(self, task: TaskSpec, delay: float) -> bool:
        """Wake scheduled loops promptly when the API queues a manual action."""

        if self.stop.is_set():
            return False
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max(0.0, delay)
        while not self.stop.is_set():
            if await asyncio.to_thread(
                self.repository.has_pending_actions,
                task.name,
            ):
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
            if await asyncio.to_thread(
                self.repository.has_pending_actions,
                task.name,
            ):
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
            if failed.cancelled():
                raise RuntimeError("worker task loop was cancelled unexpectedly")
            error = failed.exception()
            if error is None:
                raise RuntimeError("worker task loop exited unexpectedly")
            raise RuntimeError("worker task loop failed") from error
        finally:
            if not stop_waiter.done():
                stop_waiter.cancel()
            await asyncio.gather(stop_waiter, return_exceptions=True)

    async def _run(self, *, once: bool) -> dict[str, Any]:
        if not await asyncio.to_thread(self.process_lock.acquire, self.owner_id):
            raise WorkerAlreadyRunning("another unified worker holds the process file lock")
        heartbeat: asyncio.Task[None] | None = None
        loops: dict[TaskSpec, asyncio.Task[None]] = {}
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
            heartbeat = asyncio.create_task(self._heartbeat(), name="worker-heartbeat")
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
