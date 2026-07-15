from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import socket
import uuid
from pathlib import Path
from typing import Sequence

from .lock import ProcessFileLock
from .runtime import WorkerSupervisor
from .state import WorkerAlreadyRunning, WorkerLeaseLost, WorkerStateRepository
from .tasks import build_default_tasks


def _absolute_path(name: str, default: str) -> Path:
    path = Path(os.environ.get(name, default)).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{name} must be an absolute path without parent traversal")
    return path


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


async def _run(once: bool, state_path: Path, lock_path: Path) -> int:
    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
    repository = WorkerStateRepository(state_path)
    supervisor = WorkerSupervisor(
        repository,
        build_default_tasks(owner_id, worker_db_path=state_path),
        owner_id=owner_id,
        process_lock=ProcessFileLock(lock_path),
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
        state_path = _absolute_path("OPTIX_WORKER_DB_PATH", "/data/optix-worker.db")
        lock_path = _absolute_path("OPTIX_WORKER_LOCK_PATH", "/data/optix-worker.lock")
        repository = WorkerStateRepository(state_path)
        if arguments.healthcheck or arguments.status:
            payload = repository.health()
            _print(payload)
            if arguments.status:
                return 0
            return 0 if payload["healthy"] else 1
        return asyncio.run(_run(arguments.once, state_path, lock_path))
    except WorkerAlreadyRunning:
        _print({"healthy": False, "status": "already_running", "error_code": "worker_locked"})
        return 1
    except WorkerLeaseLost:
        _print({"healthy": False, "status": "lease_lost", "error_code": "worker_lease_lost"})
        return 1
    except (OSError, ValueError, RuntimeError) as error:
        logging.getLogger("optix.worker").error(
            "worker startup failed error_type=%s", type(error).__name__
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
