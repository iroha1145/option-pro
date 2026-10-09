"""Idle worker loops: one shared demand read, wake probes and lock-free polls.

Counts, not wall-clock budgets: every assertion is about how many reads,
rounds or write transactions happened, never about how long they took.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from app.access import request_owner_access_context
from app.services import sector_iv_refresh as sector_refresh
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts.local_intelligence import (
    LocalCatalystIntelligence,
    queued_manual_operations_signature,
)
from app.worker import tasks as worker_tasks
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import WorkerStateRepository


class _CountingStateRepository(WorkerStateRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.demand_reads = 0
        self.per_task_reads = 0

    def queued_action_delays(self, *, now=None):
        self.demand_reads += 1
        return super().queued_action_delays(now=now)

    def has_claimable_actions(self, task_name, *, now=None):
        self.per_task_reads += 1
        return super().has_claimable_actions(task_name, now=now)

    def next_action_retry_delay(self, task_name, *, now=None):
        self.per_task_reads += 1
        return super().next_action_retry_delay(task_name, now=now)


async def _until(predicate, *, attempts: int = 2_000) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition was not reached")


def _supervisor(
    tmp_path: Path,
    repository: WorkerStateRepository,
    tasks: tuple[TaskSpec, ...],
) -> WorkerSupervisor:
    supervisor = WorkerSupervisor(
        repository,
        tasks,
        owner_id="idle-wakeups",
        lease_seconds=30,
        shutdown_grace_seconds=1,
        process_lock=ProcessFileLock(tmp_path / "worker.lock"),
    )
    supervisor.DEMAND_POLL_SECONDS = 0.01
    return supervisor


def test_idle_loops_share_one_demand_read_per_poll(tmp_path: Path) -> None:
    repository = _CountingStateRepository(tmp_path / "state.db")
    runs = {name: 0 for name in ("alpha", "beta", "gamma", "delta", "epsilon")}

    def runner(name: str):
        async def run() -> TaskResult:
            runs[name] += 1
            return TaskResult(status="idle")

        return run

    async def scenario() -> None:
        supervisor = _supervisor(
            tmp_path,
            repository,
            tuple(TaskSpec(name, runner(name), 3_600) for name in runs),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await _until(lambda: all(runs.values()) and repository.demand_reads >= 40)
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(scenario())

    # Five idle loops used to read the request table twice per loop every
    # half second; now one watcher read serves all of them.
    assert repository.per_task_reads == 0
    assert repository.demand_reads >= 40
    assert runs == dict.fromkeys(runs, 1)


def test_manual_action_wakes_its_loop_through_the_shared_watcher(tmp_path: Path) -> None:
    repository = _CountingStateRepository(tmp_path / "state.db")
    runs: list[list[str]] = []

    class Runner:
        async def __call__(self) -> TaskResult:
            runs.append([])
            return TaskResult(status="idle")

        async def run_for_actions(self, actions):
            runs.append([str(item["action_type"]) for item in actions])
            return TaskResult(status="idle")

    async def scenario() -> None:
        supervisor = _supervisor(
            tmp_path,
            repository,
            (
                TaskSpec("long_interval", Runner(), 86_400),
                TaskSpec("manual_only", Runner(), 86_400, manual_only=True),
            ),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await _until(lambda: len(runs) == 1)
        await asyncio.to_thread(
            repository.request_action,
            "focus_refresh",
            "manual_only",
            "manual-only:1",
        )
        await asyncio.to_thread(
            repository.request_action,
            "calendar_refresh",
            "long_interval",
            "long-interval:1",
        )
        await _until(lambda: len(runs) == 3)
        reads_after_actions = repository.demand_reads
        await _until(lambda: repository.demand_reads >= reads_after_actions + 20)
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(scenario())

    assert sorted(map(tuple, runs)) == [(), ("calendar_refresh",), ("focus_refresh",)]
    assert repository.per_task_reads == 0
    states = {
        item["action_type"]: item["status"]
        for item in repository.action_requests(limit=10)
    }
    assert states == {"calendar_refresh": "completed", "focus_refresh": "completed"}


def test_wake_probe_change_ends_an_idle_wait_and_no_change_keeps_it(
    tmp_path: Path,
) -> None:
    repository = _CountingStateRepository(tmp_path / "state.db")
    probe = {"value": ("queue", 0)}
    runs = 0

    async def run() -> TaskResult:
        nonlocal runs
        runs += 1
        return TaskResult(status="idle")

    async def scenario() -> None:
        supervisor = _supervisor(
            tmp_path,
            repository,
            (TaskSpec("probed", run, 3_600, wake_probe=lambda: probe["value"]),),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await _until(lambda: runs == 1 and repository.demand_reads >= 30)
        # An unchanged probe never ends the hour-long wait.
        assert runs == 1
        probe["value"] = ("queue", 1)
        await _until(lambda: runs == 2)
        reads = repository.demand_reads
        await _until(lambda: repository.demand_reads >= reads + 30)
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(scenario())
    assert runs == 2


def test_own_writes_seen_by_the_probe_cost_at_most_one_extra_round(
    tmp_path: Path,
) -> None:
    repository = _CountingStateRepository(tmp_path / "state.db")
    probe = {"value": 0}
    runs = 0

    async def run() -> TaskResult:
        nonlocal runs
        runs += 1
        if runs == 2:
            # The round itself changes what the probe reads (a claimed job's
            # updated_at, a consumed request).
            probe["value"] += 1
            await asyncio.sleep(0.05)
        return TaskResult(status="idle")

    async def scenario() -> None:
        supervisor = _supervisor(
            tmp_path,
            repository,
            (TaskSpec("self_writing", run, 3_600, wake_probe=lambda: probe["value"]),),
        )
        running = asyncio.create_task(supervisor.run_forever())
        await _until(lambda: runs == 1 and repository.demand_reads >= 5)
        probe["value"] += 1
        await _until(lambda: runs >= 2)
        reads = repository.demand_reads
        await _until(lambda: repository.demand_reads >= reads + 40)
        supervisor.request_stop()
        await asyncio.wait_for(running, timeout=5)

    asyncio.run(scenario())
    assert runs in {2, 3}


class _TracedAIJobRepository(AIJobRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.statements: list[str] = []

    @contextmanager
    def _connect(self) -> Iterator:
        with super()._connect() as connection:
            connection.set_trace_callback(self.statements.append)
            yield connection

    def write_transactions(self) -> int:
        return sum(statement == "BEGIN IMMEDIATE" for statement in self.statements)


def _create_job(repository: AIJobRepository, ticker: str = "AAPL") -> dict:
    model = "gpt-5.6-terra"
    version, digest = ai_runtime.schema_identity("earnings_impact", model=model)
    row, created = repository.create_job(
        job_type="earnings_impact",
        payload={"ticker": ticker, "name": ticker},
        model=model,
        reasoning="max",
        execution_mode="background",
        prompt_version=ai_runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version,
        schema_sha256=digest,
        max_queued=500,
        submission_source="scheduled",
        priority=70,
    )
    assert created is True
    return row


def test_idle_claim_takes_no_write_lock_and_due_work_still_claims(
    tmp_path: Path,
) -> None:
    repository = _TracedAIJobRepository(tmp_path / "ai-jobs.db")
    repository.initialize()
    repository.statements.clear()

    for _ in range(5):
        assert repository.claim_due("idle-owner", 60) is None
    assert repository.write_transactions() == 0

    job = _create_job(repository)
    repository.statements.clear()
    claimed = repository.claim_due("busy-owner", 60)
    assert claimed is not None
    assert claimed["job_id"] == job["job_id"]
    assert repository.write_transactions() == 1


def test_active_queue_signature_tracks_enqueue_cancel_and_claims(
    tmp_path: Path,
) -> None:
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    assert repository.active_queue_signature() is None
    repository.initialize()
    empty = repository.active_queue_signature()
    assert empty == (0, None)
    assert repository.next_due_delay() is None

    job = _create_job(repository)
    queued = repository.active_queue_signature()
    assert queued != empty
    assert repository.next_due_delay() == 0.0
    # A poll that finds nothing to claim leaves the signature unchanged.
    repository.claim_due("other-owner", 60)
    leased = repository.active_queue_signature()
    assert leased != queued
    assert repository.claim_due("other-owner", 60) is None
    assert repository.active_queue_signature() == leased
    assert 0.0 < repository.next_due_delay() <= 60.0

    repository.request_cancel(job["job_id"])
    assert repository.active_queue_signature() != leased


@pytest.mark.parametrize(
    ("due", "expected"),
    [
        (None, worker_tasks.AI_JOBS_IDLE_SECONDS),
        (0.0, worker_tasks.AI_JOBS_HELD_RECHECK_SECONDS),
        (12.5, 12.5),
        (3_600.0, worker_tasks.AI_JOBS_IDLE_SECONDS),
    ],
)
def test_ai_task_idle_delay_follows_the_next_due_job(due, expected) -> None:
    task = worker_tasks.AIJobsTask("idle-delay", settings=object(), personal_config=object())
    task._repository = type("Repository", (), {"next_due_delay": lambda self: due})()
    assert asyncio.run(task._idle_delay()) == expected


def test_catalyst_probe_sees_queued_owner_refresh(tmp_path: Path) -> None:
    path = tmp_path / "catalyst-cache.db"
    assert queued_manual_operations_signature(path) is None
    with request_owner_access_context(True):
        intelligence = LocalCatalystIntelligence(
            path,
            AIJobRepository(tmp_path / "ai-jobs.db"),
            "read",
            (),
        )
        intelligence.initialize()
        assert queued_manual_operations_signature(path) == (0, None)
        intelligence.request_refresh("news", idempotency_key="probe-test")
        queued = queued_manual_operations_signature(path)
        assert queued is not None and queued[0] == 1
        intelligence.consume_refresh_requested()
    assert queued_manual_operations_signature(path)[0] == 0


def test_sector_probe_sees_visitor_requests_and_idle_rounds_sleep(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [1_790_000_000.0]
    store = sector_refresh.SectorIVRefreshStore(
        tmp_path / "queue.sqlite", clock=lambda: clock[0],
    )
    assert store.queued_signature() is None
    store.status("semiconductors")
    assert store.queued_signature() == (0, None)
    store.request("semiconductors")
    assert store.queued_signature() == (1, clock[0])

    async def idle_batch():
        return {"completed": 0, "failed": 0}

    monkeypatch.setattr(sector_refresh, "run_refresh_batch", idle_batch)
    outcome = asyncio.run(worker_tasks.SectorIVTask()())
    assert outcome.next_delay_seconds == worker_tasks.SECTOR_IV_IDLE_SECONDS


def test_fresh_repositories_do_not_repeat_the_schema_transaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "ai-jobs.db"
    seeded = AIJobRepository(path)
    _create_job(seeded, "AAPL")
    original = AIJobRepository._initialize_database
    calls = 0

    def counted(repository: AIJobRepository) -> None:
        nonlocal calls
        calls += 1
        original(repository)

    monkeypatch.setattr(AIJobRepository, "_initialize_database", counted)
    # The web process builds one repository per request.
    for _ in range(5):
        repository = _TracedAIJobRepository(path)
        assert repository.get_job("missing") is None
        assert repository.write_transactions() == 0
    assert calls == 0

    # A schema change made out of band brings the full checks back once,
    # and they repair it.
    with seeded._connect() as connection:
        connection.execute("DROP INDEX idx_ai_jobs_due")
        connection.commit()
    for _ in range(3):
        AIJobRepository(path).get_job("missing")
    assert calls == 1
    with seeded._connect() as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name='idx_ai_jobs_due'"
        ).fetchone() is not None


def test_schema_transaction_skips_full_scans_when_nothing_needs_backfill(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ai-jobs.db"
    seeded = AIJobRepository(path)
    for index in range(3):
        _create_job(seeded, f"T{index}")
    repository = _TracedAIJobRepository(path)
    repository.initialize()
    in_lock = []
    inside = False
    for statement in repository.statements:
        if statement == "BEGIN IMMEDIATE":
            inside = True
        elif statement in {"COMMIT", "ROLLBACK"}:
            inside = False
        elif inside:
            in_lock.append(" ".join(statement.split()))
    assert not any("SELECT job_id,'manual',created_at FROM ai_jobs" in item for item in in_lock)
    assert not any("budget_charge_microusd=0" in item and item.startswith("SELECT") for item in in_lock)


def test_schema_transaction_still_backfills_a_missing_source_row(tmp_path: Path) -> None:
    path = tmp_path / "ai-jobs.db"
    seeded = AIJobRepository(path)
    job = _create_job(seeded, "LEGACY")
    with seeded._connect() as connection:
        connection.execute("DELETE FROM ai_job_sources WHERE job_id=?", (job["job_id"],))
        connection.commit()
    AIJobRepository(path).initialize()
    with seeded._connect() as connection:
        source = connection.execute(
            "SELECT submission_source FROM ai_job_sources WHERE job_id=?",
            (job["job_id"],),
        ).fetchone()
    assert source is not None and source["submission_source"] == "manual"
