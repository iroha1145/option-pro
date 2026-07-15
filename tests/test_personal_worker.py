from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from app.worker.__main__ import main
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import MaintenanceTask, build_default_tasks


TASK_NAMES = {"breakout", "focus", "catalyst_sync", "ai_jobs", "maintenance"}


def test_process_file_lock_excludes_two_descriptors_and_processes(tmp_path: Path) -> None:
    lock_path = tmp_path / "worker.lock"
    first = ProcessFileLock(lock_path)
    second = ProcessFileLock(lock_path)
    assert first.acquire("first") is True
    assert second.acquire("second") is False

    script = (
        "from app.worker.lock import ProcessFileLock;"
        f"lock=ProcessFileLock({str(lock_path)!r});"
        "raise SystemExit(0 if not lock.acquire('child') else 3)"
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "backend")
    child = subprocess.run(
        [sys.executable, "-c", script],
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 0, child.stderr

    first.release()
    assert second.acquire("second") is True
    second.release()


def test_worker_once_records_five_tasks_and_isolates_failure(tmp_path: Path) -> None:
    calls: list[str] = []

    async def success(name: str) -> TaskResult:
        calls.append(name)
        return TaskResult(status="idle", details={"name": name})

    async def failure() -> TaskResult:
        calls.append("catalyst_sync")
        raise RuntimeError("secret upstream detail must not be stored")

    tasks = (
        TaskSpec("breakout", lambda: success("breakout"), 60),
        TaskSpec("focus", lambda: success("focus"), 60),
        TaskSpec("catalyst_sync", failure, 60),
        TaskSpec("ai_jobs", lambda: success("ai_jobs"), 60, drain_on_shutdown=True),
        TaskSpec("maintenance", lambda: success("maintenance"), 60),
    )
    repository = WorkerStateRepository(tmp_path / "worker.db")
    supervisor = WorkerSupervisor(
        repository,
        tasks,
        owner_id="test-worker",
        process_lock=ProcessFileLock(tmp_path / "worker.lock"),
    )
    payload = asyncio.run(supervisor.run_once())

    assert payload["status"] == "completed"
    assert set(payload["tasks"]) == TASK_NAMES
    assert payload["tasks"]["catalyst_sync"]["status"] == "degraded"
    assert set(calls) == TASK_NAMES
    states = {item["task_name"]: item for item in repository.task_states()}
    assert set(states) == TASK_NAMES
    assert states["catalyst_sync"]["error_code"] == "task_failed"
    assert states["catalyst_sync"]["details"] == {"error_type": "RuntimeError"}
    assert "secret upstream detail" not in json.dumps(states)
    with sqlite3.connect(repository.path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_task_status'"
        ).fetchone()
    assert table == ("worker_task_status",)


def test_each_task_has_independent_backoff(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int]:
        counts = {"healthy": 0, "failing": 0}

        async def healthy() -> TaskResult:
            counts["healthy"] += 1
            return TaskResult(next_delay_seconds=0.01)

        async def failing() -> TaskResult:
            counts["failing"] += 1
            raise RuntimeError("offline failure")

        supervisor = WorkerSupervisor(
            WorkerStateRepository(tmp_path / "backoff.db"),
            (
                TaskSpec("healthy", healthy, 0.01),
                TaskSpec(
                    "failing",
                    failing,
                    0.01,
                    failure_backoff_seconds=0.05,
                    max_backoff_seconds=0.1,
                ),
            ),
            owner_id="backoff-worker",
            lease_seconds=2,
            shutdown_grace_seconds=0.2,
            process_lock=ProcessFileLock(tmp_path / "backoff.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await asyncio.sleep(0.14)
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)
        return counts["healthy"], counts["failing"]

    healthy_count, failing_count = asyncio.run(scenario())
    # SQLite status writes share the runner with both loops, so absolute loop
    # counts vary with disk speed. The healthy task must still outpace the
    # independently backed-off task by a clear margin.
    assert healthy_count >= 3
    assert healthy_count > failing_count
    assert 2 <= failing_count <= 3


def test_shutdown_drains_paid_task_and_cancels_other_long_task(tmp_path: Path) -> None:
    async def scenario() -> None:
        paid_started = asyncio.Event()
        paid_release = asyncio.Event()
        regular_started = asyncio.Event()
        regular_cancelled = asyncio.Event()
        paid_calls = 0

        async def paid() -> TaskResult:
            nonlocal paid_calls
            paid_calls += 1
            paid_started.set()
            await paid_release.wait()
            return TaskResult(status="idle", details={"safe": True})

        async def regular() -> TaskResult:
            regular_started.set()
            try:
                await asyncio.Event().wait()
            finally:
                regular_cancelled.set()
            return TaskResult()

        lock_path = tmp_path / "drain.lock"
        repository = WorkerStateRepository(tmp_path / "drain.db")
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "ai_jobs",
                    paid,
                    3600,
                    timeout_seconds=2,
                    drain_on_shutdown=True,
                ),
                TaskSpec("breakout", regular, 3600, timeout_seconds=30),
            ),
            owner_id="drain-worker",
            lease_seconds=2,
            shutdown_grace_seconds=0.02,
            process_lock=ProcessFileLock(lock_path),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await asyncio.wait_for(paid_started.wait(), timeout=1)
        await asyncio.wait_for(regular_started.wait(), timeout=1)
        supervisor.request_stop()
        await asyncio.wait_for(regular_cancelled.wait(), timeout=1)
        await asyncio.sleep(0.02)
        assert not running.done()
        contender = ProcessFileLock(lock_path)
        assert contender.acquire("contender") is False
        paid_release.set()
        await asyncio.wait_for(running, timeout=2)
        assert paid_calls == 1
        assert contender.acquire("contender") is True
        contender.release()
        states = {item["task_name"]: item for item in repository.task_states()}
        assert states["ai_jobs"]["status"] == "idle"
        assert states["breakout"]["status"] == "interrupted"

    asyncio.run(scenario())


def test_status_is_read_only_and_does_not_require_live_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path = tmp_path / "status.db"
    monkeypatch.setenv("OPTIX_WORKER_DB_PATH", str(state_path))
    monkeypatch.setenv("OPTIX_WORKER_LOCK_PATH", str(tmp_path / "status.lock"))

    assert main(["--status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "not_started"
    assert status["tasks"] == []
    assert not state_path.exists()

    assert main(["--healthcheck"]) == 1
    health = json.loads(capsys.readouterr().out)
    assert health["healthy"] is False


def test_default_task_inventory_and_maintenance_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    paths = {
        "BREAKOUT_DB_PATH": tmp_path / "optix.db",
        "MACROLENS_CACHE_DB_PATH": tmp_path / "catalyst-cache.db",
        "OPENAI_JOB_DB_PATH": tmp_path / "ai-jobs.db",
        "OPTIX_BACKUP_DIR": tmp_path / "backups",
    }
    for name, path in paths.items():
        monkeypatch.setenv(name, str(path))
    worker_db = tmp_path / "optix-worker.db"
    for path in (*paths.values(), worker_db):
        if path.name == "backups":
            continue
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sample(value INTEGER)")
            connection.execute("INSERT INTO sample VALUES(1)")

    specs = build_default_tasks("inventory", worker_db_path=worker_db)
    assert {spec.name for spec in specs} == TASK_NAMES
    maintenance_spec = next(spec for spec in specs if spec.name == "maintenance")
    assert isinstance(maintenance_spec.runner, MaintenanceTask)
    result = asyncio.run(maintenance_spec.runner())
    assert result.status == "idle"
    assert set(result.details["backed_up"]) == {
        "optix",
        "catalyst-cache",
        "ai-jobs",
        "optix-worker",
    }
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 4
