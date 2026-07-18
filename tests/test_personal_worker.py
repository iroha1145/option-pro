from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app import runtime_environment
from app.worker import tasks as worker_tasks
from app.worker.__main__ import _load_worker_settings, main
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import WorkerLeaseLost, WorkerStateRepository
from app.worker.tasks import (
    AIJobsTask,
    BreakoutTask,
    CatalystSyncTask,
    FocusRefreshTask,
    FocusTask,
    MaintenanceTask,
    PublicHomeTask,
    RetentionTask,
    StrengthRefreshTask,
    build_default_tasks,
)


SCHEDULED_TASK_NAMES = {
    "breakout",
    "focus",
    "catalyst_sync",
    "ai_jobs",
    "maintenance",
    "public_home",
}
MANUAL_TASK_NAMES = {
    "focus_refresh",
    "strength_refresh",
    "breakout_refresh",
    "retention",
}
DEFAULT_TASK_NAMES = SCHEDULED_TASK_NAMES | MANUAL_TASK_NAMES
NOW = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
ETL_AS_OF = "2026-07-15T12:00:00Z"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"


def _worker_config(
    tmp_path: Path,
    *,
    token: str = "",
    url: str = "",
    cache_path: Path | None = None,
    ai_path: Path | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        internal_api_token=SecretStr(token),
        macrolens_url=url,
        macrolens_ca_bundle="",
        macrolens_cache_db_path=cache_path or tmp_path / "catalyst-cache.db",
        openai_job_db_path=ai_path or tmp_path / "ai-jobs.db",
        optix_worker_db_path=tmp_path / "optix-worker.db",
        optix_worker_lock_path=tmp_path / "optix-worker.lock",
        breakout_db_path=tmp_path / "optix.db",
        optix_backup_dir=tmp_path / "backups",
        personal_etl_enabled=bool(token and url),
    )


def _runtime_settings(
    *,
    manual: bool = True,
    scheduled: bool = False,
    sync_seconds: int = 120,
    focus_seconds: int = 1800,
    scheduled_times: tuple[str, ...] = ("08:00", "12:00", "16:00"),
    daily_max_jobs: int = 4,
    daily_budget_usd: float = 2.0,
    daily_token_limit: int = 10_000_000,
    analysis_cooldown_seconds: int = 30,
) -> SimpleNamespace:
    return SimpleNamespace(
        ai=SimpleNamespace(
            manual_analysis_enabled=manual,
            daily_max_jobs=daily_max_jobs,
            daily_budget_usd=daily_budget_usd,
            daily_token_limit=daily_token_limit,
            manual_analysis_cooldown_seconds=analysis_cooldown_seconds,
        ),
        catalyst=SimpleNamespace(
            sync_seconds=sync_seconds,
            focus_seconds=focus_seconds,
            manual_refresh_cooldown_seconds=30,
            scheduled_analysis_enabled=scheduled,
            scheduled_times_et=scheduled_times,
        ),
    )


def _empty_etl_page(path: str) -> dict:
    payload = {
        "items": [],
        "has_more": False,
        "next_cursor": None,
        "next_updated_after": ETL_AS_OF,
        "next_after_sequence": 0,
    }
    if path.endswith("/news/changes"):
        payload["watermark"] = {"sequence": 0, "as_of": ETL_AS_OF}
    else:
        payload.update(
            {
                "watermark": {
                    "sequence": 0,
                    "as_of": ETL_AS_OF,
                    "snapshot_token": None,
                },
                "data_through": None,
                "is_stale": False,
            }
        )
    return payload


def test_manual_actions_are_idempotent_fenced_and_cooled_down(tmp_path: Path) -> None:
    repository = WorkerStateRepository(tmp_path / "actions.db")
    repository.initialize(now=NOW)
    token = repository.acquire("worker", lease_seconds=120, now=NOW)
    assert token is not None

    queued = repository.request_action(
        "news_refresh",
        "catalyst_sync",
        "news-refresh:2026-07-16T00:00",
        cooldown_seconds=30,
        details={"request": {"stream": "news"}},
        now=NOW,
    )
    duplicate = repository.request_action(
        "news_refresh",
        "catalyst_sync",
        "news-refresh:second-click",
        cooldown_seconds=30,
        now=NOW + timedelta(seconds=1),
    )
    assert queued["status"] == "queued"
    assert duplicate["request_id"] == queued["request_id"]
    assert duplicate["reason"] == "already_running"

    claimed = repository.claim_actions(
        "worker",
        token,
        "catalyst_sync",
        now=NOW + timedelta(seconds=2),
    )
    assert [item["request_id"] for item in claimed] == [queued["request_id"]]
    assert claimed[0]["status"] == "running"
    assert repository.finish_actions(
        "worker",
        token,
        [queued["request_id"]],
        succeeded=True,
        details={"task_status": "idle"},
        now=NOW + timedelta(seconds=3),
    ) == 1
    completed = repository.action_request(queued["request_id"])
    assert completed is not None
    assert completed["details"]["request"] == {"stream": "news"}
    assert completed["details"]["task_status"] == "idle"

    cooling = repository.request_action(
        "news_refresh",
        "catalyst_sync",
        "news-refresh:during-cooldown",
        cooldown_seconds=30,
        now=NOW + timedelta(seconds=20),
    )
    assert cooling["request_id"] == queued["request_id"]
    assert cooling["reason"] == "cooldown"
    next_request = repository.request_action(
        "news_refresh",
        "catalyst_sync",
        "news-refresh:after-cooldown",
        cooldown_seconds=30,
        now=NOW + timedelta(seconds=34),
    )
    assert next_request["request_id"] != queued["request_id"]
    assert next_request["status"] == "queued"


def test_manual_action_details_are_bounded_and_reject_sensitive_fields(
    tmp_path: Path,
) -> None:
    repository = WorkerStateRepository(tmp_path / "action-details.db")
    repository.initialize(now=NOW)
    with pytest.raises(ValueError, match="sensitive"):
        repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength:secret",
            details={"api_key": "must-not-be-stored"},
            now=NOW,
        )
    with pytest.raises(ValueError, match="too large"):
        repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength:oversized",
            details={"parameters": {"note": "x" * 1025}},
            now=NOW,
        )


def test_manual_action_wakes_long_interval_worker_and_completes(tmp_path: Path) -> None:
    async def scenario() -> None:
        first_run = asyncio.Event()
        manual_run = asyncio.Event()
        calls = 0

        async def task_runner() -> TaskResult:
            nonlocal calls
            calls += 1
            if calls == 1:
                first_run.set()
            else:
                manual_run.set()
            return TaskResult(status="idle")

        repository = WorkerStateRepository(tmp_path / "wake.db")
        supervisor = WorkerSupervisor(
            repository,
            (TaskSpec("catalyst_sync", task_runner, 3600),),
            owner_id="wake-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "wake.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await asyncio.wait_for(first_run.wait(), timeout=2)
        queued = repository.request_action(
            "calendar_refresh",
            "catalyst_sync",
            "calendar-refresh:wake-test",
            cooldown_seconds=30,
        )
        await asyncio.wait_for(manual_run.wait(), timeout=2)
        for _ in range(50):
            action = repository.action_requests(action_type="calendar_refresh")[0]
            if action["status"] == "completed":
                break
            await asyncio.sleep(0.02)
        else:
            raise AssertionError("manual action did not reach a terminal state")
        assert action["request_id"] == queued["request_id"]
        assert action["details"]["task_status"] == "idle"
        assert action["details"]["task_completed_at"].endswith("Z")
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_manual_only_task_waits_for_action_and_never_runs_at_startup(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ran = asyncio.Event()
        calls = 0

        async def refresh() -> TaskResult:
            nonlocal calls
            calls += 1
            ran.set()
            return TaskResult(status="idle", details={"result": "refreshed"})

        repository = WorkerStateRepository(tmp_path / "manual-only.db")
        repository.initialize()
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "strength_refresh",
                    refresh,
                    86_400,
                    manual_only=True,
                ),
            ),
            owner_id="manual-only-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "manual-only.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            states = repository.task_states()
            if states:
                break
            await asyncio.sleep(0.01)
        assert states[0]["status"] == "idle"
        assert states[0]["details"] == {"mode": "manual", "waiting": True}
        await asyncio.sleep(0.05)
        assert calls == 0

        queued = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:manual-only",
            cooldown_seconds=30,
        )
        await asyncio.wait_for(ran.wait(), timeout=2)
        for _ in range(100):
            action = repository.action_request(queued["request_id"])
            if action is not None and action["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        assert action is not None
        assert action["status"] == "completed"
        assert action["completed_at"] is not None
        assert action["details"]["task_completed_at"].endswith("Z")
        assert calls == 1
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_manual_runner_receives_claimed_actions_and_completion_keeps_request_and_result(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        observed_actions: list[dict] = []

        class ParameterRunner:
            async def __call__(self) -> TaskResult:
                raise AssertionError("manual action must use run_for_actions")

            async def run_for_actions(self, actions: list[dict]) -> TaskResult:
                observed_actions.extend(actions)
                return TaskResult(
                    status="idle",
                    details={"snapshot": "strength-snapshot-v1-test.json"},
                )

        repository = WorkerStateRepository(tmp_path / "manual-parameters.db")
        repository.initialize()
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "strength_refresh",
                    ParameterRunner(),
                    86_400,
                    manual_only=True,
                ),
            ),
            owner_id="manual-parameters-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "manual-parameters.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        queued = repository.request_action(
            "strength_refresh",
            "strength_refresh",
            "strength-refresh:parameters",
            details={"parameters": {"top": 30}},
        )
        for _ in range(200):
            action = repository.action_request(queued["request_id"])
            if action is not None and action["status"] == "completed":
                break
            await asyncio.sleep(0.01)
        else:
            raise AssertionError("manual parameter action did not complete")

        assert observed_actions[0]["details"] == {"parameters": {"top": 30}}
        assert action is not None
        assert action["details"]["parameters"] == {"top": 30}
        assert action["details"]["result"] == {
            "snapshot": "strength-snapshot-v1-test.json"
        }
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_run_once_skips_manual_only_task_without_a_request(tmp_path: Path) -> None:
    calls = 0

    async def expensive_refresh() -> TaskResult:
        nonlocal calls
        calls += 1
        return TaskResult(status="idle")

    repository = WorkerStateRepository(tmp_path / "manual-once.db")
    supervisor = WorkerSupervisor(
        repository,
        (
            TaskSpec(
                "focus_refresh",
                expensive_refresh,
                86_400,
                manual_only=True,
            ),
        ),
        owner_id="manual-once-worker",
        process_lock=ProcessFileLock(tmp_path / "manual-once.lock"),
    )

    payload = asyncio.run(supervisor.run_once())

    assert calls == 0
    assert payload["tasks"]["focus_refresh"]["manual_only"] is True
    assert repository.task_states()[0]["last_started_at"] is None


def test_manual_only_failure_is_terminal_and_timestamped(tmp_path: Path) -> None:
    async def scenario() -> None:
        async def failed_refresh() -> TaskResult:
            raise RuntimeError("private provider detail")

        repository = WorkerStateRepository(tmp_path / "manual-failure.db")
        repository.initialize()
        supervisor = WorkerSupervisor(
            repository,
            (
                TaskSpec(
                    "focus_refresh",
                    failed_refresh,
                    86_400,
                    manual_only=True,
                ),
            ),
            owner_id="manual-failure-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "manual-failure.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        queued = repository.request_action(
            "focus_refresh",
            "focus_refresh",
            "focus-refresh:failure",
        )
        for _ in range(200):
            action = repository.action_request(queued["request_id"])
            if action is not None and action["status"] == "failed":
                break
            await asyncio.sleep(0.01)
        assert action is not None
        assert action["status"] == "failed"
        assert action["error_code"] == "task_failed"
        assert action["completed_at"] is not None
        assert action["details"]["task_completed_at"].endswith("Z")
        assert "private provider detail" not in json.dumps(action)
        state = repository.task_states()[0]
        assert state["status"] == "degraded"
        assert state["last_completed_at"] is not None
        assert state["next_run_at"] is None
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_ai_worker_fails_closed_when_runtime_settings_are_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.runtime_settings import RuntimeSettingsStorageError

    settings = SimpleNamespace(
        openai_job_db_path=tmp_path / "ai-jobs.db",
        openai_api_key=SecretStr("test-only-key"),
        openai_job_lease_seconds=60,
    )
    def unreadable():
        raise RuntimeSettingsStorageError("invalid runtime document")

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        unreadable,
    )
    result = asyncio.run(AIJobsTask("fail-closed", settings=settings)())

    assert result.status == "degraded"
    assert result.error_code == "runtime_settings_unavailable"
    assert result.details == {
        "reason": "runtime_settings_unavailable",
        "processed": 0,
    }


def test_ai_worker_uses_fresh_runtime_budget_without_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSettings(SimpleNamespace):
        def model_copy(self, *, update: dict[str, object]):
            values = dict(vars(self))
            values.update(update)
            return FakeSettings(**values)

    settings = FakeSettings(
        openai_job_db_path=tmp_path / "ai-jobs.db",
        openai_api_key=SecretStr("test-only-key"),
        openai_daily_max_jobs=4,
        openai_daily_budget_usd=2.0,
        openai_daily_token_limit=10_000_000,
        openai_manual_cooldown_seconds=30,
    )
    effective = _runtime_settings(
        daily_max_jobs=3,
        daily_budget_usd=1.25,
        daily_token_limit=9_000_000,
        analysis_cooldown_seconds=45,
    )
    seen: list[tuple[int, float, int, int]] = []

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: effective,
    )

    async def capture(
        _repository,
        worker_settings,
        _owner,
        **_options,
    ):
        seen.append(
            (
                worker_settings.openai_daily_max_jobs,
                worker_settings.openai_daily_budget_usd,
                worker_settings.openai_daily_token_limit,
                worker_settings.openai_manual_cooldown_seconds,
            )
        )
        return False

    monkeypatch.setattr("app.services.ai_jobs.worker.run_once", capture)
    task = AIJobsTask("runtime-budget", settings=settings)
    first = asyncio.run(task())
    effective.ai.daily_budget_usd = 1.0
    effective.ai.daily_token_limit = 8_000_000
    second = asyncio.run(task())

    assert first.status == second.status == "idle"
    assert seen == [
        (3, 1.25, 9_000_000, 45),
        (3, 1.0, 8_000_000, 45),
    ]


@pytest.mark.parametrize("mode", ["read", "off"])
@pytest.mark.parametrize("submission_source", ["manual", "scheduled"])
def test_ai_worker_mode_caps_stale_enabled_runtime_switches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    submission_source: str,
) -> None:
    from app.services.ai_jobs import runtime, worker as ai_worker
    from app.services.ai_jobs.repository import AIJobRepository

    class CopyableSettings(SimpleNamespace):
        def model_copy(self, *, update: dict[str, object]):
            values = dict(vars(self))
            values.update(update)
            return CopyableSettings(**values)

    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    schema_version, schema_sha256 = runtime.schema_identity("earnings_impact")
    job, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": "AAPL", "name": "Apple"},
        model="gpt-5.6-terra",
        reasoning="max",
        execution_mode="background",
        prompt_version=f"mode-cap-{mode}-{submission_source}",
        schema_version=schema_version,
        schema_sha256=schema_sha256,
        max_queued=200,
        submission_source=submission_source,
    )
    assert created is True
    settings = CopyableSettings(
        openai_job_lease_seconds=60,
        openai_daily_max_jobs=4,
        openai_daily_budget_usd=2.0,
        openai_manual_cooldown_seconds=30,
    )
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(manual=True, scheduled=True),
    )
    provider_calls = 0

    async def forbidden_submission(*_args, **_kwargs):
        nonlocal provider_calls
        provider_calls += 1
        raise AssertionError("read/off mode must not submit provider work")

    monkeypatch.setattr(runtime, "submit_background", forbidden_submission)
    personal_config = SimpleNamespace(
        features=SimpleNamespace(catalyst_mode=mode),
    )

    processed, runtime_state = asyncio.run(
        ai_worker.run_configured_once(
            repository,
            settings,
            f"mode-cap-{mode}-{submission_source}",
            personal_config=personal_config,
        )
    )
    stored = repository.get_job(job["job_id"])

    assert (processed, runtime_state) == (1, "analysis_disabled")
    assert stored["status"] == "failed"
    assert stored["error_code"] == f"{submission_source}_analysis_disabled"
    assert stored["submission_started_at"] is None
    assert provider_calls == 0


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


def test_worker_once_records_six_scheduled_tasks_and_isolates_failure(
    tmp_path: Path,
) -> None:
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
        TaskSpec("public_home", lambda: success("public_home"), 60),
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
    assert set(payload["tasks"]) == SCHEDULED_TASK_NAMES
    assert payload["tasks"]["catalyst_sync"]["status"] == "degraded"
    assert set(calls) == SCHEDULED_TASK_NAMES
    states = {item["task_name"]: item for item in repository.task_states()}
    assert set(states) == SCHEDULED_TASK_NAMES
    assert states["catalyst_sync"]["error_code"] == "task_failed"
    assert states["catalyst_sync"]["details"] == {"error_type": "RuntimeError"}
    assert "secret upstream detail" not in json.dumps(states)
    with sqlite3.connect(repository.path) as connection:
        table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='worker_task_status'"
        ).fetchone()
    assert table == ("worker_task_status",)


def test_new_worker_inventory_removes_old_status_rows_after_acquire(
    tmp_path: Path,
) -> None:
    repository = WorkerStateRepository(tmp_path / "inventory.db")
    repository.initialize()
    old_token = repository.acquire("old-worker", lease_seconds=60)
    assert old_token is not None
    repository.record_task(
        "old-worker",
        old_token,
        "removed_task",
        enabled=True,
        status="idle",
    )
    assert repository.release("old-worker", old_token) is True

    supervisor = WorkerSupervisor(
        repository,
        (TaskSpec("breakout", lambda: TaskResult(), 60),),
        owner_id="new-worker",
        process_lock=ProcessFileLock(tmp_path / "inventory.lock"),
    )
    result = asyncio.run(supervisor.run_once())

    assert result["status"] == "completed"
    assert [item["task_name"] for item in repository.task_states()] == [
        "breakout"
    ]


def test_graceful_forever_shutdown_clears_current_task_status(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def runner() -> TaskResult:
            started.set()
            return TaskResult(next_delay_seconds=3600)

        repository = WorkerStateRepository(tmp_path / "graceful-clear.db")
        supervisor = WorkerSupervisor(
            repository,
            (TaskSpec("breakout", runner, 3600),),
            owner_id="graceful-clear-worker",
            process_lock=ProcessFileLock(tmp_path / "graceful-clear.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await asyncio.wait_for(started.wait(), timeout=1)
        for _ in range(100):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        assert repository.task_states()
        supervisor.request_stop()
        result = await asyncio.wait_for(running, timeout=2)
        assert result["status"] == "stopped"
        assert repository.task_states() == []

    asyncio.run(scenario())


def test_each_task_has_independent_backoff(tmp_path: Path) -> None:
    async def scenario() -> tuple[int, int]:
        counts = {"healthy": 0, "failing": 0}
        observed_independent_progress = asyncio.Event()

        def record_progress() -> None:
            if counts["healthy"] >= 4 and counts["failing"] >= 2:
                observed_independent_progress.set()

        async def healthy() -> TaskResult:
            counts["healthy"] += 1
            record_progress()
            return TaskResult(next_delay_seconds=0.01)

        async def failing() -> TaskResult:
            counts["failing"] += 1
            record_progress()
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
        try:
            await asyncio.wait_for(observed_independent_progress.wait(), timeout=2)
        finally:
            supervisor.request_stop()
            await asyncio.wait_for(running, timeout=2)
        return counts["healthy"], counts["failing"]

    healthy_count, failing_count = asyncio.run(scenario())
    # SQLite status writes share the runner with both loops, so absolute loop
    # counts vary with disk speed. The healthy task must still outpace the
    # independently backed-off task by a clear margin.
    assert healthy_count >= 4
    assert healthy_count > failing_count
    assert 2 <= failing_count <= 3


def test_worker_heartbeat_survives_a_saturated_default_executor(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="saturated-default",
            )
        )
        task_started = asyncio.Event()
        task_release = asyncio.Event()
        blocker_started = threading.Event()
        blocker_release = threading.Event()

        async def long_task() -> TaskResult:
            task_started.set()
            await task_release.wait()
            return TaskResult(status="idle")

        repository = WorkerStateRepository(tmp_path / "heartbeat-executor.db")
        supervisor = WorkerSupervisor(
            repository,
            (TaskSpec("catalyst_sync", long_task, 3600),),
            owner_id="heartbeat-executor-worker",
            lease_seconds=0.3,
            process_lock=ProcessFileLock(tmp_path / "heartbeat-executor.lock"),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await asyncio.wait_for(task_started.wait(), timeout=1)

        def occupy_default_executor() -> None:
            blocker_started.set()
            blocker_release.wait()

        blocked = loop.run_in_executor(None, occupy_default_executor)
        for _ in range(100):
            if blocker_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert blocker_started.is_set()

        try:
            await asyncio.sleep(0.65)
            health = repository.health(
                heartbeat_stale_seconds=0.25,
                expected_tasks=("catalyst_sync",),
            )
            assert health["healthy"] is True
            assert health["lock_live"] is True
        finally:
            blocker_release.set()
            await blocked
            task_release.set()
            supervisor.request_stop()
            await asyncio.wait_for(running, timeout=2)

    asyncio.run(scenario())


def test_worker_heartbeat_survives_a_blocked_event_loop(tmp_path: Path) -> None:
    renewal_threads: list[str] = []
    repository = WorkerStateRepository(tmp_path / "heartbeat-blocked-loop.db")
    original_renew = repository.renew

    def observed_renew(*args, **kwargs):
        renewal_threads.append(threading.current_thread().name)
        return original_renew(*args, **kwargs)

    repository.renew = observed_renew  # type: ignore[method-assign]

    async def blocking_task() -> TaskResult:
        threading.Event().wait(0.7)
        return TaskResult(status="idle")

    supervisor = WorkerSupervisor(
        repository,
        (
            TaskSpec(
                "breakout",
                blocking_task,
                3600,
                timeout_seconds=0.8,
                may_block_event_loop=True,
            ),
        ),
        owner_id="heartbeat-blocked-loop-worker",
        lease_seconds=0.1,
        process_lock=ProcessFileLock(tmp_path / "heartbeat-blocked-loop.lock"),
    )

    async def scenario():
        return await asyncio.wait_for(supervisor.run_once(), timeout=2)

    result = asyncio.run(scenario())
    assert result["status"] == "completed"
    assert len(renewal_threads) >= 4
    assert all(name.startswith("worker-heartbeat") for name in renewal_threads)


def test_worker_heartbeat_start_failure_stops_before_tasks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls = 0

    async def runner() -> TaskResult:
        nonlocal calls
        calls += 1
        return TaskResult(status="idle")

    original_start = threading.Thread.start

    def fail_heartbeat_start(thread):
        if thread.name == "worker-heartbeat":
            raise RuntimeError("simulated heartbeat thread failure")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_heartbeat_start)
    repository = WorkerStateRepository(tmp_path / "heartbeat-start-failure.db")
    supervisor = WorkerSupervisor(
        repository,
        (TaskSpec("breakout", runner, 60),),
        owner_id="heartbeat-start-failure-worker",
        lease_seconds=0.2,
        process_lock=ProcessFileLock(tmp_path / "heartbeat-start-failure.lock"),
    )

    with pytest.raises(WorkerLeaseLost, match="heartbeat could not start"):
        asyncio.run(supervisor.run_once())
    assert calls == 0


@pytest.mark.parametrize("fail_after_release", [False, True])
def test_call_local_waits_through_repeated_cancellation(
    fail_after_release: bool,
) -> None:
    async def scenario() -> None:
        started = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def local_transaction() -> str:
            started.set()
            release.wait(timeout=1)
            try:
                if fail_after_release:
                    raise sqlite3.OperationalError("simulated local write failure")
                return "completed"
            finally:
                finished.set()

        operation = asyncio.create_task(
            worker_tasks._call_local(local_transaction)
        )
        for _ in range(100):
            if started.is_set():
                break
            await asyncio.sleep(0.01)
        assert started.is_set()

        operation.cancel()
        await asyncio.sleep(0)
        operation.cancel()
        await asyncio.sleep(0)
        assert not operation.done()
        assert not finished.is_set()

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await operation
        assert finished.is_set()

    asyncio.run(scenario())


def test_unexpected_task_loop_exit_terminates_supervisor_and_releases_lock(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository = WorkerStateRepository(tmp_path / "failed-loop.db")
        lock_path = tmp_path / "failed-loop.lock"
        original_record = repository.record_task

        def fail_running_record(*args: object, **kwargs: object) -> None:
            if kwargs.get("status") == "running":
                raise sqlite3.OperationalError("simulated state failure")
            original_record(*args, **kwargs)

        repository.record_task = fail_running_record  # type: ignore[method-assign]
        supervisor = WorkerSupervisor(
            repository,
            (TaskSpec("breakout", lambda: TaskResult(), 60),),
            owner_id="failed-loop-worker",
            lease_seconds=2,
            process_lock=ProcessFileLock(lock_path),
        )

        with pytest.raises(RuntimeError, match="task loop failed"):
            await asyncio.wait_for(supervisor.run_forever(), timeout=2)

        contender = ProcessFileLock(lock_path)
        assert contender.acquire("contender") is True
        contender.release()

    asyncio.run(scenario())


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
        assert repository.task_states() == []

    asyncio.run(scenario())


def test_status_is_read_only_and_does_not_require_live_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path = tmp_path / "optix-worker.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))

    assert main(["--status"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["status"] == "not_started"
    assert status["tasks"] == []
    assert not state_path.exists()

    assert main(["--healthcheck"]) == 1
    health = json.loads(capsys.readouterr().out)
    assert health["healthy"] is False


@pytest.mark.parametrize(
    ("working_directory", "directory_name"),
    ((REPOSITORY_ROOT, "repository-root"), (BACKEND_ROOT, "backend")),
    ids=("repository-root", "backend"),
)
@pytest.mark.parametrize(
    ("argument", "expected_returncode"),
    (("--status", 0), ("--healthcheck", 1)),
)
def test_worker_module_cli_is_identical_without_pythonpath(
    tmp_path: Path,
    working_directory: Path,
    directory_name: str,
    argument: str,
    expected_returncode: int,
) -> None:
    data_dir = tmp_path / directory_name
    environment = {
        key: value
        for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
        if (value := os.environ.get(key)) is not None
    }
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "DATA_DIR": str(data_dir),
            "OPTIX_WORKER_DB_PATH": str(data_dir / "optix-worker.db"),
            "OPTIX_WORKER_LOCK_PATH": str(data_dir / "optix-worker.lock"),
            "MACROLENS_CACHE_DB_PATH": str(data_dir / "catalyst-cache.db"),
            "OPENAI_JOB_DB_PATH": str(data_dir / "ai-jobs.db"),
            "BREAKOUT_DB_PATH": str(data_dir / "optix.db"),
            "OPTIX_BACKUP_DIR": str(data_dir / "backups"),
            "MACROLENS_URL": "",
            "INTERNAL_API_TOKEN": "",
            "MACROLENS_BASE_URL": "",
            "MACROLENS_INTERNAL_TOKEN": "",
            "OPENAI_API_KEY": "",
        }
    )
    assert "PYTHONPATH" not in environment

    completed = subprocess.run(
        [sys.executable, "-m", "app.worker", argument],
        cwd=working_directory,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )

    assert completed.returncode == expected_returncode, completed.stderr
    assert "ModuleNotFoundError" not in completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["healthy"] is False
    assert payload["status"] == "not_started"
    assert payload["tasks"] == []
    assert not (data_dir / "optix-worker.db").exists()


def test_worker_once_selects_personal_etl_from_repository_files_offline(
    tmp_path: Path,
) -> None:
    data_dir = tmp_path / "data"
    root_env = tmp_path / ".env"
    secrets_env = tmp_path / "secrets.env"
    token = "subprocess-file-owner-token"
    root_env.write_text(
        "MACROLENS_URL=https://macrolens.invalid\n"
        f"DATA_DIR={data_dir}\n"
        f"OPTIX_WORKER_DB_PATH={data_dir / 'optix-worker.db'}\n"
        f"OPTIX_WORKER_LOCK_PATH={data_dir / 'optix-worker.lock'}\n"
        f"MACROLENS_CACHE_DB_PATH={data_dir / 'catalyst-cache.db'}\n"
        f"OPENAI_JOB_DB_PATH={data_dir / 'ai-jobs.db'}\n"
        f"BREAKOUT_DB_PATH={data_dir / 'optix.db'}\n"
        f"OPTIX_BACKUP_DIR={data_dir / 'backups'}\n",
        encoding="utf-8",
    )
    secrets_env.write_text(
        f"INTERNAL_API_TOKEN={token}\n",
        encoding="utf-8",
    )
    script = textwrap.dedent(
        f"""
        from pathlib import Path

        import httpx

        from app import personal_config, runtime_environment

        config = personal_config.get_personal_config()
        features = config.features.model_copy(
            update={{"breakout_enabled": False, "catalyst_mode": "manual"}}
        )
        config = config.model_copy(update={{"features": features}})
        personal_config.get_personal_config = lambda: config
        runtime_environment.RUNTIME_ENV_FILES = (
            Path({str(root_env)!r}),
            Path({str(secrets_env)!r}),
        )

        # Import the Catalyst package only after the isolated runtime files are
        # selected.  Its settings module intentionally loads runtime files on
        # import, so importing it earlier would let a checked-out repository
        # .env shadow this subprocess fixture.
        from app.services.catalysts import etl_client
        from app.worker import tasks as worker_tasks
        from app.worker.runtime import TaskResult

        async def offline_public_home(_self):
            return TaskResult(status="idle", details={{"result": "offline-test"}})

        worker_tasks.PublicHomeTask.__call__ = offline_public_home

        def handle(request):
            assert request.url.host == "macrolens.invalid"
            assert request.headers["authorization"] == "Bearer {token}"
            payload = {{
                "items": [],
                "has_more": False,
                "next_cursor": None,
                "next_updated_after": "{ETL_AS_OF}",
                "next_after_sequence": 0,
            }}
            if request.url.path.endswith("/news/changes"):
                payload["watermark"] = {{"sequence": 0, "as_of": "{ETL_AS_OF}"}}
            else:
                assert request.url.path.endswith("/calendar")
                payload.update(
                    {{
                        "watermark": {{
                            "sequence": 0,
                            "as_of": "{ETL_AS_OF}",
                            "snapshot_token": None,
                        }},
                        "data_through": None,
                        "is_stale": False,
                    }}
                )
            return httpx.Response(
                200,
                headers={{"content-type": "application/json"}},
                json=payload,
            )

        real_client = etl_client.MacroLensEtlClient

        class OfflineMacroLensEtlClient(real_client):
            def __init__(self, client_config, **_options):
                super().__init__(
                    client_config,
                    transport=httpx.MockTransport(handle),
                )

        etl_client.MacroLensEtlClient = OfflineMacroLensEtlClient

        from app.worker.__main__ import main

        raise SystemExit(main(["--once"]))
        """
    )
    environment = {
        key: value
        for key in ("HOME", "PATH", "TMPDIR", "LANG", "LC_ALL")
        if (value := os.environ.get(key)) is not None
    }
    environment.update(
        {
            "PYTHONNOUSERSITE": "1",
            "OPENAI_API_KEY": "",
            "MACROLENS_BASE_URL": "",
            "MACROLENS_INTERNAL_TOKEN": "",
        }
    )
    assert "PYTHONPATH" not in environment
    assert "MACROLENS_URL" not in environment
    assert "INTERNAL_API_TOKEN" not in environment

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPOSITORY_ROOT,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["status"] == "completed"
    assert payload["tasks"]["breakout"]["status"] == "disabled"
    assert payload["tasks"]["catalyst_sync"]["status"] == "idle"
    assert payload["tasks"]["focus"]["status"] == "idle"
    assert payload["tasks"]["ai_jobs"]["status"] == "disabled"
    cache_path = data_dir / "catalyst-cache.db"
    assert cache_path.is_file()
    with sqlite3.connect(cache_path) as connection:
        streams = connection.execute(
            "SELECT stream,completed_as_of FROM macrolens_etl_state ORDER BY stream"
        ).fetchall()
    assert streams == [("calendar", ETL_AS_OF), ("news", ETL_AS_OF)]
    assert token not in completed.stdout
    assert token not in completed.stderr


def test_worker_loads_root_files_once_and_injects_one_settings_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root_env = tmp_path / ".env"
    machine_env = tmp_path / "machine.env"
    secrets_env = tmp_path / "secrets.env"
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    root_env.write_text(
        "MACROLENS_URL=https://macrolens.example\n",
        encoding="utf-8",
    )
    machine_env.write_text(
        "DATA_DIR=" + str(data_dir) + "\n",
        encoding="utf-8",
    )
    secrets_env.write_text(
        "INTERNAL_API_TOKEN=test-owner-token\n",
        encoding="utf-8",
    )
    keys = {
        "MACROLENS_URL",
        "INTERNAL_API_TOKEN",
        "MACROLENS_BASE_URL",
        "MACROLENS_INTERNAL_TOKEN",
        "DATA_DIR",
        "OPTIX_WORKER_DB_PATH",
        "OPTIX_WORKER_LOCK_PATH",
        "MACROLENS_CACHE_DB_PATH",
        "OPENAI_JOB_DB_PATH",
        "BREAKOUT_DB_PATH",
        "OPTIX_BACKUP_DIR",
    }
    original = {key: os.environ.get(key) for key in keys}
    monkeypatch.setattr(
        runtime_environment,
        "RUNTIME_ENV_FILES",
        (root_env, machine_env, secrets_env),
    )
    try:
        for key in keys:
            os.environ.pop(key, None)
        settings = _load_worker_settings()
        assert settings.macrolens_url == "https://macrolens.example"
        assert settings.internal_api_token.get_secret_value() == "test-owner-token"
        assert settings.optix_worker_db_path == data_dir / "optix-worker.db"
        assert settings.optix_worker_lock_path == data_dir / "optix-worker.lock"
        assert settings.macrolens_cache_db_path == data_dir / "catalyst-cache.db"
        assert settings.openai_job_db_path == data_dir / "ai-jobs.db"
        assert settings.breakout_db_path == data_dir / "optix.db"
        assert settings.optix_backup_dir == data_dir / "backups"

        specs = build_default_tasks("settings-injection", settings=settings)
        runners = {spec.name: spec.runner for spec in specs}
        assert runners["ai_jobs"]._settings is settings
        assert runners["catalyst_sync"]._runtime_settings is settings
        assert runners["focus"]._runtime_settings is settings

        monkeypatch.chdir(tmp_path)
        assert main(["--status"]) == 0
        output = capsys.readouterr().out
        assert "test-owner-token" not in output
        assert json.loads(output)["status"] == "not_started"
    finally:
        for key in keys:
            os.environ.pop(key, None)
            if original[key] is not None:
                os.environ[key] = original[key]


def test_personal_catalyst_task_uses_https_bearer_etl_and_closes_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []
    local_calls: list[str] = []

    class FakeLocalIntelligence:
        def __init__(
            self,
            database_path: Path,
            ai_repository: object,
            **options: object,
        ) -> None:
            assert database_path == cache_path
            assert getattr(ai_repository, "path") == ai_path
            assert options["mode"] == "read"
            assert options["model"] == "gpt-5.6-terra"
            assert options["reasoning"] == "max"
            tickers = set(options["canonical_tickers"])
            assert "NVDA" in tickers
            assert "ZZZZ" not in tickers

        def initialize(self) -> None:
            local_calls.append("initialize")

        def consume_refresh_requested(self) -> bool:
            local_calls.append("consume_refresh_requested")
            return True

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            assert allow_scheduled_jobs is False
            local_calls.append("reconcile")
            return {"news": 0, "queued": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.scheme == "https"
        assert request.headers.get_list("authorization") == ["Bearer owner-token"]
        assert request.url.path in {
            "/internal/v1/news/changes",
            "/internal/v1/calendar",
        }
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    cache_path = tmp_path / "catalyst-cache.db"
    ai_path = tmp_path / "ai-jobs.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config = SimpleNamespace(
        catalyst=SimpleNamespace(sync_seconds=37),
        features=SimpleNamespace(catalyst_mode="read"),
        ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
    )
    task = CatalystSyncTask(
        "personal-worker",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
            cache_path=cache_path,
            ai_path=ai_path,
        ),
        personal_config=config,
        etl_transport=httpx.MockTransport(handler),
        intelligence_factory=FakeLocalIntelligence,
    )

    async def run() -> tuple[TaskResult, object]:
        result = await task()
        client = task._client
        await task.aclose()
        return result, client

    result, client = asyncio.run(run())

    assert result.status == "idle"
    assert result.next_delay_seconds == 2
    assert result.details["processed"] == [
        "news",
        "calendar",
        "local_intelligence",
    ]
    assert result.details["refresh_requested"] is True
    assert set(result.details["streams"]) == {"news", "calendar"}
    assert [request.url.path for request in requests] == [
        "/internal/v1/news/changes",
        "/internal/v1/calendar",
    ]
    assert all(request.url.params["after_sequence"] == "0" for request in requests)
    assert [request.url.params["limit"] for request in requests] == ["500", "50"]
    assert getattr(client, "_client").is_closed is True
    assert task._client is None
    assert cache_path.is_file()
    assert "owner-token" not in repr(getattr(client, "config"))
    assert local_calls == ["initialize", "consume_refresh_requested", "reconcile"]
    with sqlite3.connect(ai_path) as connection:
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_personal_catalyst_task_reconciles_with_real_local_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    cache_path = tmp_path / "catalyst-cache.db"
    ai_path = tmp_path / "ai-jobs.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config = SimpleNamespace(
        catalyst=SimpleNamespace(sync_seconds=53),
        features=SimpleNamespace(catalyst_mode="read"),
        ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
    )
    task = CatalystSyncTask(
        "real-local-worker",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
            cache_path=cache_path,
            ai_path=ai_path,
        ),
        personal_config=config,
        etl_transport=httpx.MockTransport(handler),
    )

    async def run() -> TaskResult:
        result = await task()
        await task.aclose()
        return result

    result = asyncio.run(run())

    assert result.status == "idle"
    assert result.details["local_intelligence"]["ingested"] == 0
    assert result.details["local_intelligence"]["queued"] == 0
    with sqlite3.connect(cache_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
    assert "macrolens_etl_state" in tables
    assert "catalyst_local_schema" in tables
    with sqlite3.connect(ai_path) as connection:
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_personal_catalyst_task_isolates_stream_failure(tmp_path: Path) -> None:
    class SyncError(RuntimeError):
        code = "upstream_unavailable"

    class FakeSync:
        async def sync_news(self) -> None:
            raise SyncError("private upstream detail")

        async def sync_calendar(self) -> object:
            return SimpleNamespace(
                pages=1,
                records=2,
                deletes=0,
                replayed=0,
                complete=True,
                watermark_sequence=9,
            )

    class FakeIntelligence:
        def consume_refresh_requested(self) -> bool:
            return False

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            assert allow_scheduled_jobs is False
            return {"news": 2, "queued": 0}

    task = CatalystSyncTask(
        "isolated-worker",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
        ),
        personal_config=SimpleNamespace(catalyst=SimpleNamespace(sync_seconds=41)),
    )
    task._mode = "personal"
    task._service = FakeSync()
    task._intelligence = FakeIntelligence()

    result = asyncio.run(task())

    assert result.status == "degraded"
    assert result.error_code == "catalyst_sync_degraded"
    assert result.next_delay_seconds == 2
    assert result.details["processed"] == ["calendar", "local_intelligence"]
    assert result.details["errors"] == {"news": "upstream_unavailable"}
    assert "private upstream detail" not in json.dumps(result.details)


def test_personal_catalyst_task_makes_no_calls_when_runtime_settings_are_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.runtime_settings import RuntimeSettingsStorageError

    calls: list[str] = []

    class NetworkForbidden:
        async def sync_news(self) -> None:
            calls.append("sync_news")

        async def sync_calendar(self) -> None:
            calls.append("sync_calendar")

    class LocalWorkForbidden:
        def consume_refresh_requested(self) -> bool:
            calls.append("consume_refresh_requested")
            return True

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            calls.append("reconcile")
            return {}

    def unreadable():
        raise RuntimeSettingsStorageError("invalid runtime document")

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        unreadable,
    )
    task = CatalystSyncTask(
        "runtime-settings-failure",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
        ),
        personal_config=SimpleNamespace(catalyst=SimpleNamespace(sync_seconds=41)),
    )
    task._mode = "personal"
    task._service = NetworkForbidden()
    task._intelligence = LocalWorkForbidden()

    result = asyncio.run(task())

    assert result.status == "degraded"
    assert result.error_code == "runtime_settings_unavailable"
    assert result.next_delay_seconds == 30
    assert result.details == {
        "processed": [],
        "streams": {},
        "refresh_requested": False,
        "errors": {"runtime_settings": "runtime_settings_unavailable"},
    }
    assert calls == []


def test_personal_catalyst_task_rejects_http_without_network(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "http://macrolens.example")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    task = CatalystSyncTask(
        "invalid-worker",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="http://macrolens.example",
        ),
        personal_config=SimpleNamespace(catalyst=SimpleNamespace(sync_seconds=43)),
    )

    with pytest.raises(ValueError, match="HTTPS"):
        asyncio.run(task())

    assert task._mode is None
    assert task._client is None
    assert not (tmp_path / "catalyst-cache.db").exists()


def test_personal_tasks_disable_without_token_and_never_choose_legacy(
    tmp_path: Path,
) -> None:
    settings = _worker_config(
        tmp_path,
        url="https://macrolens.example",
        token="",
    )
    config = SimpleNamespace(
        catalyst=SimpleNamespace(sync_seconds=120, focus_seconds=1800),
    )
    catalyst = CatalystSyncTask(
        "disabled-personal",
        settings=settings,
        personal_config=config,
    )
    focus = FocusTask(
        "disabled-personal",
        enabled=True,
        settings=settings,
        personal_config=config,
    )

    catalyst_result = asyncio.run(catalyst())
    focus_result = asyncio.run(focus())

    assert catalyst_result.status == focus_result.status == "disabled"
    assert catalyst._mode is None
    assert focus._mode is None
    assert catalyst._service is None
    assert focus._intelligence is None


def test_catalyst_initialization_failure_leaves_no_cached_half_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class FlakyIntelligence:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def initialize(self) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("local initialization failed")

        def consume_refresh_requested(self) -> bool:
            return False

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            assert allow_scheduled_jobs is False
            return {"queued": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(),
    )
    config = SimpleNamespace(
        catalyst=SimpleNamespace(sync_seconds=120),
        features=SimpleNamespace(catalyst_mode="read"),
        ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
    )
    task = CatalystSyncTask(
        "retry-initialization",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
        ),
        personal_config=config,
        etl_transport=httpx.MockTransport(handler),
        intelligence_factory=FlakyIntelligence,
    )

    with pytest.raises(OSError, match="local initialization failed"):
        asyncio.run(task())
    assert task._mode is None
    assert task._client is None
    assert task._service is None
    assert task._intelligence is None

    result = asyncio.run(task())
    assert result.status == "idle"
    assert attempts == 2
    asyncio.run(task.aclose())


@pytest.mark.parametrize("mode", ["read", "off"])
def test_read_and_off_focus_ignore_stale_enabled_runtime_switches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    calls: list[str] = []

    class FakeLocalIntelligence:
        def __init__(self, _path: Path, _ai_repository: object, **options: object):
            assert options["mode"] == "read"

        def initialize(self) -> None:
            calls.append("initialize")

        def run_scheduled(self, **_kwargs) -> dict:
            raise AssertionError("read mode must not queue scheduled work")

    ai_path = tmp_path / "ai-jobs.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(
            manual=True,
            scheduled=True,
            focus_seconds=601,
        ),
    )
    config = SimpleNamespace(
        catalyst=SimpleNamespace(focus_seconds=1800),
        catalyst_manual_enabled=False,
        catalyst_scheduled_enabled=False,
        features=SimpleNamespace(catalyst_mode=mode),
        ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
    )
    task = FocusTask(
        "read-worker",
        enabled=True,
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
            ai_path=ai_path,
        ),
        personal_config=config,
        intelligence_factory=FakeLocalIntelligence,
    )

    result = asyncio.run(task())

    assert result.status == "idle"
    assert result.details == {"result": "not_scheduled", "queued": 0, "skipped": 0}
    assert result.next_delay_seconds == 601
    assert calls == ["initialize"]
    with sqlite3.connect(ai_path) as connection:
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 0


def test_scheduled_focus_calls_local_scheduler_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeLocalIntelligence:
        def __init__(self, _path: Path, _ai_repository: object, **options: object):
            assert options["mode"] == "scheduled"

        def initialize(self) -> None:
            calls.append("initialize")

        def run_scheduled(self, **kwargs) -> dict:
            assert kwargs["scheduled_times_et"] == ("08:00", "12:00", "16:00")
            calls.append("run_scheduled")
            return {"queued": 1, "skipped": 2}

    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(scheduled=True, focus_seconds=601),
    )
    config = SimpleNamespace(
        catalyst=SimpleNamespace(focus_seconds=777),
        catalyst_scheduled_enabled=True,
        features=SimpleNamespace(catalyst_mode="scheduled"),
        ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
    )
    task = FocusTask(
        "scheduled-worker",
        enabled=True,
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
            ai_path=tmp_path / "ai-jobs.db",
        ),
        personal_config=config,
        intelligence_factory=FakeLocalIntelligence,
    )

    result = asyncio.run(task())

    assert result.status == "idle"
    assert result.details == {"result": "scheduled", "queued": 1, "skipped": 2}
    assert result.next_delay_seconds == 601
    assert calls == ["initialize", "run_scheduled"]


def test_focus_waits_for_first_catalyst_sync_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(
            scheduled=True,
            scheduled_times=("08:00",),
        ),
    )
    async def scenario() -> tuple[TaskResult, TaskResult, list[str]]:
        initial_sync_complete = asyncio.Event()
        sync_started = asyncio.Event()
        release_sync = asyncio.Event()
        calls: list[str] = []

        def sync_result() -> object:
            return SimpleNamespace(
                pages=1,
                records=0,
                deletes=0,
                replayed=0,
                complete=True,
                watermark_sequence=0,
            )

        class FakeSync:
            async def sync_news(self) -> object:
                calls.append("news_started")
                sync_started.set()
                await release_sync.wait()
                calls.append("news_completed")
                return sync_result()

            async def sync_calendar(self) -> object:
                calls.append("calendar_completed")
                return sync_result()

        class FakeCatalystIntelligence:
            def consume_refresh_requested(self) -> bool:
                return False

            def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
                assert allow_scheduled_jobs is False
                calls.append("reconciled")
                return {"news": 0, "queued": 0}

        class FakeFocusIntelligence:
            def run_scheduled(self, **kwargs: object) -> dict:
                assert kwargs["scheduled_times_et"] == ("08:00",)
                calls.append("focus_scheduled")
                return {"queued": 0, "skipped": 0}

        config = SimpleNamespace(
            catalyst=SimpleNamespace(
                sync_seconds=120,
                focus_seconds=1800,
                scheduled_times_et=("08:00",),
            ),
            catalyst_scheduled_enabled=True,
        )
        catalyst = CatalystSyncTask(
            "startup-order",
            settings=_worker_config(
                tmp_path,
                token="owner-token",
                url="https://macrolens.example",
            ),
            personal_config=config,
            initial_sync_complete=initial_sync_complete,
        )
        catalyst._mode = "personal"
        catalyst._service = FakeSync()
        catalyst._intelligence = FakeCatalystIntelligence()
        focus = FocusTask(
            "startup-order",
            enabled=True,
            settings=_worker_config(
                tmp_path,
                token="owner-token",
                url="https://macrolens.example",
            ),
            personal_config=config,
            initial_sync_complete=initial_sync_complete,
        )
        focus._mode = "personal"
        focus._intelligence = FakeFocusIntelligence()

        focus_run = asyncio.create_task(focus())
        await asyncio.sleep(0)
        assert calls == []
        catalyst_run = asyncio.create_task(catalyst())
        await asyncio.wait_for(sync_started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert "focus_scheduled" not in calls

        release_sync.set()
        catalyst_result, focus_result = await asyncio.gather(
            catalyst_run,
            focus_run,
        )
        assert initial_sync_complete.is_set()
        return catalyst_result, focus_result, calls

    catalyst_result, focus_result, calls = asyncio.run(scenario())

    assert catalyst_result.status == "idle"
    assert focus_result.status == "idle"
    assert calls == [
        "news_started",
        "news_completed",
        "calendar_completed",
        "reconciled",
        "focus_scheduled",
    ]


def test_catalyst_failure_releases_initial_focus_gate(tmp_path: Path) -> None:
    async def scenario() -> tuple[BaseException | None, bool]:
        initial_sync_complete = asyncio.Event()
        catalyst = CatalystSyncTask(
            "startup-failure",
            settings=_worker_config(
                tmp_path,
                token="owner-token",
                url="https://macrolens.example",
            ),
            personal_config=SimpleNamespace(
                catalyst=SimpleNamespace(sync_seconds=120)
            ),
            initial_sync_complete=initial_sync_complete,
        )

        async def fail() -> str:
            raise RuntimeError("offline")

        catalyst._prepare = fail
        error: BaseException | None = None
        try:
            await catalyst()
        except BaseException as exc:
            error = exc
        return error, initial_sync_complete.is_set()

    error, released = asyncio.run(scenario())

    assert isinstance(error, RuntimeError)
    assert released is True


def test_focus_refresh_rebuilds_atomic_watchlist_snapshot_without_network(
    tmp_path: Path,
) -> None:
    snapshot_path = tmp_path / "nested" / "watchlist-snapshot-v1.json"
    payload = {
        "groups": [
            {
                "id": "focus",
                "name": "关注",
                "stocks": [
                    {
                        "ticker": "AAPL",
                        "name": "苹果",
                        "price": 100.0,
                        "change_percent": 1.25,
                        "spark": [96.0, 97.0, 98.0, 99.0, 100.0],
                    }
                ],
            }
        ],
        "succeeded": 1,
    }
    calls = 0

    async def fake_builder() -> dict:
        nonlocal calls
        calls += 1
        return payload

    result = asyncio.run(
        FocusRefreshTask(
            builder=fake_builder,
            snapshot_path=snapshot_path,
            clock=lambda: 1_789_000_000.0,
        )()
    )

    assert calls == 1
    assert result.status == "idle"
    assert result.details["succeeded"] == 1
    document = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert document["saved_at"] == 1_789_000_000.0
    assert document["parameters"] == {"tickers": None}
    assert document["payload"] == payload
    assert list(snapshot_path.parent.glob(".*.tmp")) == []


def test_strength_refresh_runs_default_forced_scan_and_persists_snapshot(
    tmp_path: Path,
) -> None:
    from app.api import strength

    snapshot_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []
    row = {"ticker": "AAPL", "score": 91.0}

    async def fake_scanner(**kwargs) -> dict:
        calls.append(kwargs)
        return {
            "as_of": "2026-07-16T00:00:00+00:00",
            "params": {
                key: value
                for key, value in kwargs.items()
                if key not in {"include_options", "force_refresh"}
            },
            "count": 1,
            "rows": [row],
            "results": [row],
        }

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=snapshot_path,
            clock=lambda: 1_789_000_000.0,
        )()
    )

    assert calls == [
        {
            **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
            "force_refresh": True,
        }
    ]
    assert result.status == "idle"
    assert result.details["count"] == 1
    snapshot = strength._read_strength_snapshot(
        snapshot_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        now=1_789_000_001.0,
    )
    assert snapshot is not None
    assert snapshot["payload"]["rows"] == [row]


def test_strength_refresh_runs_claimed_nondefault_parameters_and_writes_variant(
    tmp_path: Path,
) -> None:
    from app.api import strength

    base_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []
    parameters = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "timeframe": "long",
        "profile": "aggressive",
        "top": 30,
        "sector_id": "software",
        "min_price": 20.0,
        "min_avg_dollar_volume": 50_000_000.0,
        "include_options": False,
    }
    row = {"ticker": "MSFT", "score": 93.0}

    async def fake_scanner(**kwargs) -> dict:
        calls.append(kwargs)
        return {
            "as_of": "2026-07-16T00:00:00+00:00",
            "params": {
                key: value
                for key, value in kwargs.items()
                if key not in {"include_options", "force_refresh"}
            },
            "count": 1,
            "rows": [row],
            "results": [row],
        }

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=base_path,
            clock=lambda: 1_789_000_000.0,
        ).run_for_actions(
            [
                {
                    "request_id": "act_test",
                    "details": {"parameters": parameters},
                }
            ]
        )
    )

    target = strength._strength_snapshot_path(parameters, base_path=base_path)
    assert calls == [{**parameters, "force_refresh": True}]
    assert target.is_file()
    assert not base_path.exists()
    assert result.details["parameters"] == parameters
    assert result.details["parameters_hash"] == strength.strength_scan_parameters_hash(
        parameters
    )
    snapshot = strength._read_strength_snapshot(
        target,
        parameters=parameters,
        now=1_789_000_001.0,
    )
    assert snapshot is not None
    assert snapshot["payload"]["rows"] == [row]


def test_strength_refresh_failure_keeps_the_previous_snapshot(
    tmp_path: Path,
) -> None:
    from app.api import strength

    base_path = tmp_path / "strength-snapshot-v1.json"
    row = {"ticker": "AAPL", "score": 88.0}
    payload = {
        "as_of": "2026-07-16T00:00:00+00:00",
        "params": {
            key: value
            for key, value in strength.DEFAULT_STRENGTH_SCAN_PARAMETERS.items()
            if key != "include_options"
        },
        "count": 1,
        "rows": [row],
        "results": [row],
    }
    strength._write_strength_snapshot(
        base_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=payload,
        saved_at=1_788_999_900.0,
    )
    original = base_path.read_bytes()

    async def failed_scanner(**_kwargs) -> dict:
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError, match="provider unavailable"):
        asyncio.run(
            StrengthRefreshTask(
                scanner=failed_scanner,
                snapshot_path=base_path,
                clock=lambda: 1_789_000_000.0,
            )()
        )

    assert base_path.read_bytes() == original


def test_retention_backs_up_before_pruning_existing_bounded_data(
    tmp_path: Path,
) -> None:
    order: list[str] = []
    database_path = tmp_path / "optix.db"
    database_path.touch()

    class Backup:
        async def __call__(self) -> TaskResult:
            order.append("backup")
            return TaskResult(
                status="idle",
                details={"backed_up": ["optix"], "failed": []},
            )

    class Repository:
        def initialize(self) -> None:
            order.append("initialize")

        def acquire_lock(self, *_args) -> int:
            order.append("lock")
            return 7

        def prune_retention(self, **kwargs) -> dict[str, int]:
            order.append("prune")
            assert kwargs["owner_id"] == "test:retention"
            assert kwargs["lease_token"] == 7
            assert kwargs["raw_payload_hours"] == 24
            assert kwargs["scan_days"] == 90
            assert kwargs["batch_size"] == 500
            return {
                "provider_payloads": 2,
                "candidate_raw": 3,
                "scan_attachments": 4,
            }

        def release_lock(self, *_args) -> bool:
            order.append("release")
            return True

    settings = SimpleNamespace(
        db_path=database_path,
        worker_lease_ttl_seconds=90,
        raw_payload_retention_hours=24,
        scan_retention_days=90,
        retention_batch_size=500,
    )
    result = asyncio.run(
        RetentionTask(
            "test",
            Backup(),  # type: ignore[arg-type]
            settings_factory=lambda: settings,
            repository_factory=lambda path: Repository(),
            now=lambda: NOW,
        )()
    )

    assert order == ["backup", "initialize", "lock", "prune", "release"]
    assert result.status == "idle"
    assert result.details["retention"] == {
        "status": "completed",
        "provider_payloads": 2,
        "candidate_raw": 3,
        "scan_attachments": 4,
    }
    assert result.details["completed_at"] == "2026-07-16T00:00:00Z"


def test_default_task_inventory_and_maintenance_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    paths = {
        "optix": tmp_path / "optix.db",
        "catalyst-cache": tmp_path / "catalyst-cache.db",
        "ai-jobs": tmp_path / "ai-jobs.db",
        "backups": tmp_path / "backups",
    }
    worker_db = tmp_path / "optix-worker.db"
    for path in (*paths.values(), worker_db):
        if path.name == "backups":
            continue
        with sqlite3.connect(path) as connection:
            connection.execute("CREATE TABLE sample(value INTEGER)")
            connection.execute("INSERT INTO sample VALUES(1)")

    worker_settings = _worker_config(
        tmp_path,
        cache_path=paths["catalyst-cache"],
        ai_path=paths["ai-jobs"],
    )
    worker_settings.breakout_db_path = paths["optix"]
    worker_settings.optix_worker_db_path = worker_db
    worker_settings.optix_backup_dir = paths["backups"]
    specs = build_default_tasks("inventory", settings=worker_settings)
    assert {spec.name for spec in specs} == DEFAULT_TASK_NAMES
    assert set(worker_tasks.DEFAULT_TASK_NAMES) == DEFAULT_TASK_NAMES
    names = [spec.name for spec in specs]
    assert names.index("catalyst_sync") < names.index("focus")
    catalyst_spec = next(spec for spec in specs if spec.name == "catalyst_sync")
    assert catalyst_spec.interval_seconds == 120
    maintenance_spec = next(spec for spec in specs if spec.name == "maintenance")
    assert isinstance(maintenance_spec.runner, MaintenanceTask)
    public_home_spec = next(spec for spec in specs if spec.name == "public_home")
    assert isinstance(public_home_spec.runner, PublicHomeTask)
    assert public_home_spec.interval_seconds == 30
    assert public_home_spec.enabled is False
    assert public_home_spec.manual_only is False
    assert public_home_spec.may_block_event_loop is False
    manual_specs = {spec.name: spec for spec in specs if spec.manual_only}
    assert set(manual_specs) == MANUAL_TASK_NAMES
    assert isinstance(manual_specs["focus_refresh"].runner, FocusRefreshTask)
    assert isinstance(manual_specs["strength_refresh"].runner, StrengthRefreshTask)
    assert isinstance(manual_specs["breakout_refresh"].runner, BreakoutTask)
    assert isinstance(manual_specs["retention"].runner, RetentionTask)
    result = asyncio.run(maintenance_spec.runner())
    assert result.status == "idle"
    assert set(result.details["backed_up"]) == {
        "optix",
        "catalyst-cache",
        "ai-jobs",
        "optix-worker",
    }
    assert len(list((tmp_path / "backups").glob("*.sqlite3"))) == 4

    private_config = worker_tasks.get_personal_config()
    password_config = private_config.model_copy(
        update={
            "access": private_config.access.model_copy(
                update={"mode": "password"}
            )
        }
    )
    monkeypatch.setattr(
        worker_tasks,
        "get_personal_config",
        lambda: password_config,
    )
    password_specs = build_default_tasks("inventory", settings=worker_settings)
    password_public_home = next(
        spec for spec in password_specs if spec.name == "public_home"
    )
    assert password_public_home.enabled is True
