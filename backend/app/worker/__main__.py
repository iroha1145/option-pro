from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from typing import TYPE_CHECKING, Sequence

from app import runtime_environment

from .lock import ProcessFileLock
from .runtime import WorkerSupervisor
from .state import WorkerAlreadyRunning, WorkerLeaseLost, WorkerStateRepository

if TYPE_CHECKING:
    from app.config import Settings

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optix personal single-process worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run each enabled task once")
    mode.add_argument(
        "--healthcheck",
        action="store_true",
        help="read the local process lock and task health only",
    )
    mode.add_argument(
        "--status",
        action="store_true",
        help="print local task history without requiring a live worker",
    )
    return parser


def _print(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")))


def _load_worker_settings() -> Settings:
    # No module that constructs subsystem settings is imported before this
    # merge. Repository files therefore have deterministic precedence in every
    # CLI mode and are not dependent on task scheduling or import order.
    runtime_environment.load_runtime_environment(runtime_environment.RUNTIME_ENV_FILES)
    from app.config import Settings

    return Settings()


async def _run(once: bool, settings: Settings) -> int:
    from .tasks import build_default_tasks

    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    repository = WorkerStateRepository(settings.optix_worker_db_path)
    supervisor = WorkerSupervisor(
        repository,
        build_default_tasks(owner_id, settings=settings),
        owner_id=owner_id,
        process_lock=ProcessFileLock(settings.optix_worker_lock_path),
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, supervisor.request_stop)
        except (NotImplementedError, RuntimeError):
            pass
    payload = await (supervisor.run_once() if once else supervisor.run_forever())
    if once:
        _print(payload)
        return int(
            any(
                task.get("status") == "degraded"
                for task in payload.get("tasks", {}).values()
            )
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        settings = _load_worker_settings()
        repository = WorkerStateRepository(settings.optix_worker_db_path)
        if arguments.healthcheck or arguments.status:
            from .tasks import DEFAULT_TASK_NAMES

            payload = repository.health(expected_tasks=DEFAULT_TASK_NAMES)
            _print(payload)
            if arguments.status:
                return 0
            return 0 if payload["healthy"] else 1
        return asyncio.run(_run(arguments.once, settings))
    except WorkerAlreadyRunning:
        _print({"healthy": False, "status": "already_running", "error_code": "worker_locked"})
        return 1
    except WorkerLeaseLost:
        _print({"healthy": False, "status": "lease_lost", "error_code": "worker_lease_lost"})
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        # .exception() 连 __cause__ 链一起打全栈：此前只打类型名，任务循环
        # 运行期死亡也被贴「startup failed」标签，生产崩溃循环一夜 85 次
        # 重启却看不到死因（2026-08-13 突破雷达停摆事故）。
        logging.getLogger("optix.worker").exception(
            "worker exited abnormally error_type=%s error=%s cause=%s",
            type(error).__name__,
            error,
            error.__cause__,
        )
        _print(
            {
                "healthy": False,
                "status": "invalid_configuration",
                "error_code": "worker_startup_failed",
            }
        )
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())


__all__ = ["main"]
