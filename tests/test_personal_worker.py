from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from app import runtime_environment
from app.worker.__main__ import _load_worker_settings, main
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository
from app.worker.tasks import (
    AIJobsTask,
    CatalystSyncTask,
    FocusTask,
    MaintenanceTask,
    build_default_tasks,
)


TASK_NAMES = {"breakout", "focus", "catalyst_sync", "ai_jobs", "maintenance"}
NOW = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)
ETL_AS_OF = "2026-07-15T12:00:00Z"


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
    analysis_cooldown_seconds: int = 30,
) -> SimpleNamespace:
    return SimpleNamespace(
        ai=SimpleNamespace(
            manual_analysis_enabled=manual,
            daily_max_jobs=daily_max_jobs,
            daily_budget_usd=daily_budget_usd,
            manual_analysis_cooldown_seconds=analysis_cooldown_seconds,
        ),
        catalyst=SimpleNamespace(
            sync_seconds=sync_seconds,
            focus_seconds=focus_seconds,
            manual_refresh_enabled=True,
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
        assert action["details"] == {"task_status": "idle"}
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
        openai_manual_cooldown_seconds=30,
    )
    effective = _runtime_settings(
        daily_max_jobs=3,
        daily_budget_usd=1.25,
        analysis_cooldown_seconds=45,
    )
    seen: list[tuple[int, float, int]] = []

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
                worker_settings.openai_manual_cooldown_seconds,
            )
        )
        return False

    monkeypatch.setattr("app.services.ai_jobs.worker.run_once", capture)
    task = AIJobsTask("runtime-budget", settings=settings)
    first = asyncio.run(task())
    effective.ai.daily_budget_usd = 1.0
    second = asyncio.run(task())

    assert first.status == second.status == "idle"
    assert seen == [(3, 1.25, 45), (3, 1.0, 45)]


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


def test_worker_loads_root_files_once_and_injects_one_settings_object(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root_env = tmp_path / ".env"
    machine_env = tmp_path / "machine.env"
    secrets_env = tmp_path / "secrets.env"
    root_env.write_text(
        "MACROLENS_URL=https://macrolens.example\n",
        encoding="utf-8",
    )
    machine_env.write_text(
        "OPTIX_WORKER_DB_PATH=" + str(tmp_path / "worker.db") + "\n"
        "OPTIX_WORKER_LOCK_PATH=" + str(tmp_path / "worker.lock") + "\n"
        "MACROLENS_CACHE_DB_PATH=" + str(tmp_path / "catalyst.db") + "\n"
        "OPENAI_JOB_DB_PATH=" + str(tmp_path / "ai-jobs.db") + "\n"
        "BREAKOUT_DB_PATH=" + str(tmp_path / "breakout.db") + "\n"
        "OPTIX_BACKUP_DIR=" + str(tmp_path / "backups") + "\n",
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
        assert settings.optix_worker_db_path == tmp_path / "worker.db"

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
            assert options["mode"] == "manual"
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

    cache_path = tmp_path / "personal-catalyst.db"
    ai_path = tmp_path / "ai-jobs.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", str(cache_path))
    monkeypatch.setenv("OPENAI_JOB_DB_PATH", str(ai_path))
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
    assert all(request.url.params["limit"] == "50" for request in requests)
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

    cache_path = tmp_path / "real-local-catalyst.db"
    ai_path = tmp_path / "real-local-ai.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", str(cache_path))
    monkeypatch.setenv("OPENAI_JOB_DB_PATH", str(ai_path))
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
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", str(tmp_path / "unused.db"))
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
    assert not (tmp_path / "unused.db").exists()


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


def test_default_read_focus_never_runs_scheduled_ai_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class FakeLocalIntelligence:
        def __init__(self, _path: Path, _ai_repository: object, **options: object):
            assert options["mode"] == "manual"

        def initialize(self) -> None:
            calls.append("initialize")

        def run_scheduled(self, **_kwargs) -> dict:
            raise AssertionError("read mode must not queue scheduled work")

    ai_path = tmp_path / "read-ai-jobs.db"
    monkeypatch.setenv("INTERNAL_API_TOKEN", "owner-token")
    monkeypatch.setenv("MACROLENS_URL", "https://macrolens.example")
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", str(tmp_path / "catalyst.db"))
    monkeypatch.setenv("OPENAI_JOB_DB_PATH", str(ai_path))
    config = SimpleNamespace(
        catalyst=SimpleNamespace(focus_seconds=1800),
        catalyst_scheduled_enabled=False,
        features=SimpleNamespace(catalyst_mode="read"),
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
    assert result.next_delay_seconds == 1800
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
    monkeypatch.setenv("MACROLENS_CACHE_DB_PATH", str(tmp_path / "catalyst.db"))
    monkeypatch.setenv("OPENAI_JOB_DB_PATH", str(tmp_path / "ai-jobs.db"))
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

    worker_settings = _worker_config(
        tmp_path,
        cache_path=paths["MACROLENS_CACHE_DB_PATH"],
        ai_path=paths["OPENAI_JOB_DB_PATH"],
    )
    worker_settings.breakout_db_path = paths["BREAKOUT_DB_PATH"]
    worker_settings.optix_worker_db_path = worker_db
    worker_settings.optix_backup_dir = paths["OPTIX_BACKUP_DIR"]
    specs = build_default_tasks("inventory", settings=worker_settings)
    assert {spec.name for spec in specs} == TASK_NAMES
    names = [spec.name for spec in specs]
    assert names.index("catalyst_sync") < names.index("focus")
    catalyst_spec = next(spec for spec in specs if spec.name == "catalyst_sync")
    assert catalyst_spec.interval_seconds == 120
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
