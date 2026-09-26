"""Regressions for the 2026-09-25 audit of worker scheduling and backups."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pandas as pd
import pytest
from pydantic import SecretStr

import app.tools.sqlite_backup as backup_module
from app import failure_diagnostics
from app.access import request_owner_access_context
from app.services.yfinance_batch import FAILED_TICKERS_ATTR, download_in_bounded_batches
from app.tools.sqlite_backup import BackupError, backup_database, backup_file
from app.worker import tasks as worker_tasks
from app.worker.lock import ProcessFileLock
from app.worker.runtime import TaskResult, TaskSpec, WorkerSupervisor
from app.worker.state import (
    WorkerStateRepository,
    _validate_action_detail,
    bounded_action_detail,
)
from app.worker.tasks import (
    AIJobsTask,
    BreakoutTask,
    CatalystSyncTask,
    MaintenanceTask,
    RetentionTask,
    StrengthRefreshTask,
    build_default_tasks,
)
from tests.test_personal_worker import (
    NOW,
    _empty_etl_page,
    _runtime_settings,
    _worker_config,
)


START = datetime(2026, 9, 1, tzinfo=timezone.utc)
EASTERN = ZoneInfo("America/New_York")


@pytest.fixture(autouse=True)
def _owner_request_context():
    with request_owner_access_context(True):
        yield


@pytest.fixture(autouse=True)
def _fresh_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    # record_fallback_failure logs once per stage and type every 300 s.
    monkeypatch.setattr(failure_diagnostics, "_seen", {})


def _sqlite(path: Path) -> Path:
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE t(value INTEGER)")
        connection.execute("INSERT INTO t VALUES (1)")
        connection.commit()
    return path


def _backup_times(destination: Path, label: str) -> list[datetime]:
    return sorted(
        datetime.strptime(path.name.split("-")[1], "%Y%m%dT%H%M%S.%fZ").replace(
            tzinfo=timezone.utc
        )
        for path in destination.glob(f"{label}-*.sqlite3")
    )


# W-1: a stepped copy restarted whenever the source committed between steps.
def test_backup_copies_in_one_step_while_a_writer_commits(tmp_path: Path) -> None:
    source = tmp_path / "busy.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t(id INTEGER PRIMARY KEY, blob BLOB)")
        with connection:
            connection.executemany(
                "INSERT INTO t(blob) VALUES (?)",
                ((os.urandom(3000),) for _ in range(400)),
            )
    writer = sqlite3.connect(source, timeout=5)
    remaining_after_step: list[int] = []

    def commit_after_every_step(_status: int, remaining: int, _total: int) -> None:
        # Worst case for a stepped copy: a commit lands between every step.
        remaining_after_step.append(remaining)
        with writer:
            writer.execute("INSERT INTO t(blob) VALUES (?)", (b"x",))
        if len(remaining_after_step) > 20:
            raise RuntimeError("stepped backup never converged")

    target = tmp_path / "copy.db"
    try:
        backup_module._copy_database(source, target, progress=commit_after_every_step)
    finally:
        writer.close()

    restarts = sum(
        1
        for before, after in zip(remaining_after_step, remaining_after_step[1:])
        if after >= before
    )
    assert restarts == 0
    assert remaining_after_step == [0]
    with closing(sqlite3.connect(target)) as copy:
        assert copy.execute("SELECT count(*) FROM t").fetchone() == (400,)
        assert copy.execute("PRAGMA integrity_check").fetchone() == ("ok",)


# W-2: one failing database made every run re-copy all the healthy ones.
def test_failing_label_retries_alone_with_backoff_and_keeps_history(
    tmp_path: Path,
) -> None:
    big = _sqlite(tmp_path / "big.db")
    small = _sqlite(tmp_path / "small.db")
    destination = tmp_path / "backups"
    clock = {"now": START}
    task = MaintenanceTask(
        {"big": big, "small": small},
        destination=destination,
        keep=2,
        now=lambda: clock["now"],
    )
    spec = next(
        item
        for item in build_default_tasks("audit", settings=_worker_config(tmp_path))
        if item.name == "maintenance"
    )

    assert asyncio.run(task()).status == "idle"
    first_big = _backup_times(destination, "big")
    assert len(first_big) == 1

    small.write_bytes(b"not a database " * 64)
    clock["now"] += timedelta(seconds=task.interval_seconds)
    cycle = asyncio.run(task())
    assert cycle.status == "degraded"
    assert cycle.details["backed_up"] == ["big"]
    assert cycle.details["failed"] == ["small"]
    delays = [cycle.next_delay_seconds]
    for _ in range(5):
        clock["now"] += timedelta(seconds=delays[-1])
        retry = asyncio.run(task())
        assert retry.details["backed_up"] == []
        assert retry.details["failed"] == ["small"]
        assert retry.error_code == "backup_failed"
        delays.append(retry.next_delay_seconds)

    expected = [
        min(spec.max_backoff_seconds, spec.failure_backoff_seconds * 2**step)
        for step in range(6)
    ]
    assert delays == expected == [300.0, 600.0, 1200.0, 2400.0, 3600.0, 3600.0]
    big_times = _backup_times(destination, "big")
    assert len(big_times) == 2
    assert set(first_big) <= set(big_times)


# W-2: a manifest left behind by a hand-deleted copy failed its label forever.
def test_orphaned_manifest_is_quarantined_and_backups_continue(tmp_path: Path) -> None:
    source = _sqlite(tmp_path / "small.db")
    destination = tmp_path / "backups"
    clock = {"now": START}
    task = MaintenanceTask(
        {"small": source},
        destination=destination,
        keep=2,
        now=lambda: clock["now"],
    )
    assert asyncio.run(task()).status == "idle"
    orphan = next(destination.glob("small-*.sqlite3"))
    orphan.unlink()

    clock["now"] += timedelta(seconds=task.interval_seconds)
    result = asyncio.run(task())

    assert result.status == "idle"
    assert result.details["backed_up"] == ["small"]
    quarantine = destination / backup_module.QUARANTINE_DIRECTORY
    assert sorted(path.name for path in quarantine.iterdir()) == [
        f"{orphan.name}.json",
        f"{orphan.name}.sha256",
    ]
    assert len(list(destination.glob("small-*.sqlite3.json"))) == 1


# W-2: count-based keep=7 covered only 1.75 days of 6-hourly backups.
def test_tiered_retention_spreads_keep_copies_over_several_days(
    tmp_path: Path,
) -> None:
    source = _sqlite(tmp_path / "optix.db")
    destination = tmp_path / "backups"
    for step in range(40):
        backup_database(
            source,
            destination,
            label="optix",
            keep=7,
            created_at=START + timedelta(hours=6 * step),
        )
    newest = START + timedelta(hours=6 * 39)

    kept = _backup_times(destination, "optix")

    assert len(kept) == 7
    assert kept[-1] == newest
    recent = [value for value in kept if newest - value < timedelta(hours=24)]
    older = [value for value in kept if newest - value >= timedelta(hours=24)]
    assert len(recent) == 4
    assert len({value.date() for value in older}) == len(older) == 3
    assert newest - kept[0] >= timedelta(days=3)


def test_failed_attempts_free_one_slot_but_never_thin_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _sqlite(tmp_path / "optix.db")
    destination = tmp_path / "backups"
    for step in range(20):
        backup_database(
            source,
            destination,
            label="optix",
            keep=7,
            created_at=START + timedelta(hours=6 * step),
        )
    assert len(_backup_times(destination, "optix")) == 7

    def unreadable_source(*_args: object, **_kwargs: object) -> None:
        raise BackupError("SQLite Backup API failed: file is not a database")

    monkeypatch.setattr(backup_module, "_copy_database", unreadable_source)
    # The worker was down for eight days; then every hourly attempt fails.
    first_attempt = START + timedelta(hours=6 * 19) + timedelta(days=8)
    after_first_failure: list[datetime] = []
    for hour in range(0, 3 * 24):
        with pytest.raises(BackupError):
            backup_database(
                source,
                destination,
                label="optix",
                keep=7,
                created_at=first_attempt + timedelta(hours=hour),
            )
        if hour == 0:
            after_first_failure = _backup_times(destination, "optix")

    # One slot is freed for the copy; nothing else goes without a replacement.
    assert len(after_first_failure) == 6
    assert _backup_times(destination, "optix") == after_first_failure


def test_published_copy_survives_a_clock_step_back(tmp_path: Path) -> None:
    source = _sqlite(tmp_path / "optix.db")
    destination = tmp_path / "backups"
    future = backup_database(
        source,
        destination,
        label="optix",
        keep=1,
        created_at=START + timedelta(days=2),
    )

    current = backup_database(source, destination, label="optix", keep=1, created_at=START)

    assert Path(current.backup).exists()
    assert not Path(future.backup).exists()


def test_checking_a_wal_copy_leaves_no_sidecar_files(tmp_path: Path) -> None:
    source = tmp_path / "wal.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE t(value INTEGER)")
        connection.execute("INSERT INTO t VALUES (1)")
        connection.commit()
    destination = tmp_path / "backups"
    stale = destination / ".wal.backup-v1.crashed.sqlite3.tmp-wal"
    destination.mkdir()
    stale.write_bytes(b"")

    backup_database(source, destination, label="wal", keep=2)

    leftovers = sorted(path.name for path in destination.glob(".wal.backup-v1.*"))
    assert leftovers == []


def test_retention_keeps_one_weekly_copy_beyond_the_daily_tier() -> None:
    backups = [
        backup_module._CompleteBackup(
            backup_path=Path(f"optix-{index:03d}.sqlite3"),
            manifest_path=Path(f"optix-{index:03d}.sqlite3.json"),
            checksum_path=Path(f"optix-{index:03d}.sqlite3.sha256"),
            created_at=START + timedelta(hours=6 * index),
        )
        for index in range(4 * 21)
    ]
    reference = backups[-1].created_at

    retained = backup_module._retained_backups(backups, reference=reference, keep=100)

    ages = [reference - item.created_at for item in retained]
    weekly = [
        item.created_at
        for item, age in zip(retained, ages)
        if age >= timedelta(days=7)
    ]
    assert len([age for age in ages if age < timedelta(hours=24)]) == 4
    assert weekly
    assert len({value.isocalendar()[:2] for value in weekly}) == len(weekly)
    assert len(backup_module._retained_backups(backups, reference=reference, keep=5)) == 5


# W-7: runtime-settings.json joins the same manifest and retention machinery.
def test_file_backup_uses_manifest_checksum_and_retention(tmp_path: Path) -> None:
    source = tmp_path / "runtime-settings.json"
    destination = tmp_path / "backups"
    results = []
    for step in range(3):
        source.write_text(json.dumps({"version": step}), encoding="utf-8")
        results.append(
            backup_file(
                source,
                destination,
                label="runtime-settings",
                keep=2,
                created_at=START + timedelta(minutes=step),
            )
        )

    latest = results[-1]
    assert Path(latest.backup).name.endswith(".json")
    assert Path(latest.backup).read_bytes() == source.read_bytes()
    manifest = json.loads(Path(latest.manifest).read_text(encoding="utf-8"))
    assert manifest["sha256"] == latest.sha256
    assert latest.sha256 == hashlib.sha256(source.read_bytes()).hexdigest()
    assert latest.integrity_check is None
    assert not Path(results[0].backup).exists()
    assert all(Path(item.backup).exists() for item in results[1:])


# W-8: retention ran only after a successful copy, so a full disk never freed.
def test_copy_failure_after_pre_copy_prune_keeps_the_newest_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _sqlite(tmp_path / "optix.db")
    destination = tmp_path / "backups"
    oldest = backup_database(source, destination, label="optix", keep=2, created_at=START)
    newest = backup_database(
        source,
        destination,
        label="optix",
        keep=2,
        created_at=START + timedelta(minutes=1),
    )
    only = backup_database(source, destination, label="worker", keep=1, created_at=START)

    def disk_full(*_args: object, **_kwargs: object) -> None:
        raise BackupError("database or disk is full")

    monkeypatch.setattr(backup_module, "_copy_database", disk_full)
    for label, keep in (("optix", 2), ("worker", 1)):
        with pytest.raises(BackupError, match="disk is full"):
            backup_database(
                source,
                destination,
                label=label,
                keep=keep,
                created_at=START + timedelta(minutes=2),
            )

    assert not Path(oldest.backup).exists()
    assert Path(newest.backup).exists()
    assert Path(only.backup).exists()
    assert not list(destination.glob(".*.backup-v1.*.tmp"))


def test_insufficient_space_skips_the_copy_with_an_explicit_code(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _sqlite(tmp_path / "optix.db")
    destination = tmp_path / "backups"
    oldest = backup_database(source, destination, label="optix", keep=2, created_at=START)
    newest = backup_database(
        source,
        destination,
        label="optix",
        keep=2,
        created_at=START + timedelta(minutes=1),
    )
    copies: list[object] = []
    monkeypatch.setattr(backup_module, "_free_bytes", lambda _path: 1024)
    monkeypatch.setattr(
        backup_module,
        "_copy_database",
        lambda *args, **_kwargs: copies.append(args),
    )

    with pytest.raises(BackupError) as caught:
        backup_database(
            source,
            destination,
            label="optix",
            keep=2,
            created_at=START + timedelta(minutes=2),
        )
    assert caught.value.code == "backup_insufficient_space"
    assert copies == []
    assert not Path(oldest.backup).exists()
    assert Path(newest.backup).exists()

    task = MaintenanceTask({"optix": source}, destination=destination, keep=2)
    result = asyncio.run(task())
    assert result.status == "degraded"
    assert result.error_code == "backup_insufficient_space"
    assert result.details["errors"] == {"optix": "backup_insufficient_space"}


# W-4: a result field finish_actions rejects used to kill the task loop.
def test_rejected_action_result_fails_the_action_and_keeps_the_loop(
    tmp_path: Path,
) -> None:
    repository = WorkerStateRepository(tmp_path / "actions.db")
    repository.initialize()
    queued = repository.request_action("probe_action", "probe_task", "probe-1")
    runs = {"manual": 0}

    class Runner:
        async def __call__(self) -> TaskResult:
            return TaskResult(status="idle")

        async def run_for_actions(self, _actions: list[dict]) -> TaskResult:
            runs["manual"] += 1
            # Valid for record_task, rejected by finish_actions.
            return TaskResult(
                status="idle",
                details={"tokens_used": 1200, "snapshot": "s.json", "note": "x" * 5000},
            )

    def supervisor() -> WorkerSupervisor:
        return WorkerSupervisor(
            repository,
            (TaskSpec("probe_task", Runner(), 3600.0),),
            owner_id="probe-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "actions.lock"),
        )

    first = asyncio.run(supervisor().run_once())
    restarted = asyncio.run(supervisor().run_once())

    assert first["tasks"]["probe_task"]["status"] == "idle"
    assert restarted["tasks"]["probe_task"]["status"] == "idle"
    assert runs["manual"] == 1
    action = repository.action_request(queued["request_id"])
    assert action is not None
    assert action["status"] == "failed"
    assert action["error_code"] == "action_result_invalid"
    assert action["details"]["result"]["snapshot"] == "s.json"
    assert "tokens_used" not in action["details"]["result"]
    assert len(action["details"]["result"]["note"].encode("utf-8")) <= 1024


def test_bounded_action_detail_always_passes_the_action_validator() -> None:
    nested: dict = {}
    cursor = nested
    for _ in range(12):
        cursor["next"] = {}
        cursor = cursor["next"]
    value = {
        "api_token": "hidden",
        "surrogate": "broken \udc80 text",
        "note": "é" * 2000,
        "items": list(range(500)),
        "nested": nested,
        "unsupported": object(),
        **{f"field_{index}": index for index in range(100)},
    }

    bounded = bounded_action_detail(value)

    _validate_action_detail(bounded)
    json.dumps(bounded, ensure_ascii=False).encode("utf-8")
    assert "api_token" not in bounded
    assert bounded["surrogate"] == "broken ? text"
    assert len(bounded) == 64
    assert len(bounded["items"]) == 128
    assert len(bounded["note"].encode("utf-8")) <= 1024
    assert bounded["unsupported"] is None


# W-5: the running row cleared next_run_at, so a restart re-ran the backup.
def test_restart_during_a_run_resumes_the_persisted_schedule(tmp_path: Path) -> None:
    repository = WorkerStateRepository(tmp_path / "schedule.db")
    repository.initialize()
    seed = repository.acquire("seed", lease_seconds=30)
    assert seed is not None
    repository.record_task(
        "seed",
        seed,
        "maintenance",
        enabled=True,
        status="idle",
        next_run_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    repository.release("seed", seed)
    starts: list[int] = []

    class Backup:
        async def __call__(self) -> TaskResult:
            starts.append(1)
            await asyncio.Event().wait()
            return TaskResult()

    spec = TaskSpec(
        "maintenance",
        Backup(),
        21_600.0,
        timeout_seconds=1_800.0,
        honor_persisted_schedule=True,
    )

    def supervisor(owner: str) -> WorkerSupervisor:
        return WorkerSupervisor(
            repository,
            (spec,),
            owner_id=owner,
            lease_seconds=5,
            shutdown_grace_seconds=0.05,
            process_lock=ProcessFileLock(tmp_path / "schedule.lock"),
        )

    def maintenance_row() -> dict:
        return next(
            row for row in repository.task_states() if row["task_name"] == "maintenance"
        )

    async def deploy_during_backup() -> str:
        worker = supervisor("first")
        running = asyncio.create_task(worker.run_forever())
        for _ in range(500):
            if starts:
                break
            await asyncio.sleep(0.01)
        row = maintenance_row()
        assert row["status"] == "running"
        assert row["next_run_at"]
        worker.request_stop()
        await asyncio.wait_for(running, timeout=10)
        return str(row["next_run_at"])

    planned = asyncio.run(deploy_during_backup())

    after_stop = maintenance_row()
    assert after_stop["status"] == "interrupted"
    assert after_stop["next_run_at"] == planned
    remaining = asyncio.run(supervisor("restart")._persisted_initial_delay(spec))
    assert remaining > 21_600.0 - 60
    assert len(starts) == 1


def test_manual_wake_keeps_the_planned_run_of_a_persisted_schedule(
    tmp_path: Path,
) -> None:
    repository = WorkerStateRepository(tmp_path / "wake.db")
    repository.initialize()
    seed = repository.acquire("seed", lease_seconds=30)
    assert seed is not None
    planned = datetime.now(timezone.utc) + timedelta(hours=5)
    repository.record_task(
        "seed",
        seed,
        "strength_refresh",
        enabled=True,
        status="idle",
        next_run_at=planned,
    )
    repository.release("seed", seed)
    entered = asyncio.Event()
    release = asyncio.Event()

    class Refresh:
        async def __call__(self) -> TaskResult:
            raise AssertionError("the scheduled run is five hours away")

        async def run_for_actions(self, _actions: list[dict]) -> TaskResult:
            entered.set()
            await release.wait()
            return TaskResult(status="idle", next_delay_seconds=3_600.0)

    spec = TaskSpec(
        "strength_refresh",
        Refresh(),
        86_400.0,
        honor_persisted_schedule=True,
        next_calendar_run_at=lambda _now: planned + timedelta(hours=1),
    )

    async def scenario() -> str:
        worker = WorkerSupervisor(
            repository,
            (spec,),
            owner_id="wake-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "wake.lock"),
        )
        running = asyncio.create_task(worker.run_forever())
        for _ in range(200):
            if repository.task_states():
                break
            await asyncio.sleep(0.01)
        repository.request_action("strength_refresh", "strength_refresh", "wake-1")
        await asyncio.wait_for(entered.wait(), timeout=5)
        row = next(iter(repository.task_states()))
        release.set()
        worker.request_stop()
        await asyncio.wait_for(running, timeout=10)
        assert row["status"] == "running"
        return str(row["next_run_at"])

    stored = datetime.fromisoformat(asyncio.run(scenario()).replace("Z", "+00:00"))

    # A crash during this manual run must not push the daily run a day out.
    assert abs((stored - planned).total_seconds()) < 5


@pytest.fixture
def _no_variant_work(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.api.strength as strength_api
    from app.services.strength import variant_demand

    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: [])
    monkeypatch.setattr(
        variant_demand,
        "complete_strength_variant_demand",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        strength_api,
        "list_recent_strength_variant_parameters",
        lambda *_args, **_kwargs: [],
    )


def _published_outcome() -> dict:
    return {
        "status": "RAN",
        "publish": {"ok": True},
        "published_at": 1_800_000_000.0,
        "available_variants": [],
        "session": "2026-09-24",
    }


# W-6: a default job that outlived the deadline was re-run back to back.
def test_default_job_finishing_after_the_deadline_is_not_run_again(
    _no_variant_work: None,
) -> None:
    started = threading.Event()
    release = threading.Event()
    runs: list[int] = []

    def slow_eod(**_kwargs: object) -> dict:
        runs.append(1)
        started.set()
        assert release.wait(timeout=5)
        return _published_outcome()

    task = StrengthRefreshTask(eod_runner=slow_eod, clock=lambda: 1_800_000_000.0)

    async def scenario() -> TaskResult:
        first = asyncio.create_task(task())
        assert await asyncio.to_thread(started.wait, 5)
        # What the supervisor's wait_for does when the task deadline passes.
        first.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        return await task()

    second = asyncio.run(scenario())

    assert len(runs) == 1
    assert second.status == "idle"
    assert second.details["published"] is True
    assert second.next_delay_seconds == pytest.approx(86_400.0)


def test_supervisor_timeout_on_the_default_job_does_not_repeat_it(
    tmp_path: Path,
    _no_variant_work: None,
) -> None:
    runs: list[int] = []

    def slow_eod(**_kwargs: object) -> dict:
        runs.append(1)
        time.sleep(1.0)
        return _published_outcome()

    spec = TaskSpec(
        "strength_refresh",
        StrengthRefreshTask(eod_runner=slow_eod),
        86_400.0,
        timeout_seconds=0.05,
        failure_backoff_seconds=0.01,
        max_backoff_seconds=0.01,
        drain_on_shutdown=True,
    )
    repository = WorkerStateRepository(tmp_path / "strength.db")
    # Status polling can start while the supervisor is still creating tables.
    repository.initialize()

    async def scenario() -> dict:
        worker = WorkerSupervisor(
            repository,
            (spec,),
            owner_id="strength-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "strength.lock"),
        )
        running = asyncio.create_task(worker.run_forever())
        state: dict = {}
        for _ in range(1_000):
            state = next(iter(repository.task_states()), {})
            if state.get("status") == "idle" and state.get("last_success_at"):
                break
            await asyncio.sleep(0.01)
        worker.request_stop()
        await asyncio.wait_for(running, timeout=10)
        return state

    state = asyncio.run(scenario())

    assert state["status"] == "idle"
    assert len(runs) == 1


# W-9: the default refresh followed process start-up, not the market close.
def test_default_snapshot_follows_the_post_close_slot(_no_variant_work: None) -> None:
    clock = {"now": datetime(2026, 9, 24, 14, 0, tzinfo=EASTERN).timestamp()}
    runs: list[float] = []

    def eod(**_kwargs: object) -> dict:
        runs.append(clock["now"])
        return _published_outcome()

    task = StrengthRefreshTask(
        eod_runner=eod,
        clock=lambda: clock["now"],
        refresh_times_et=(worker_tasks._strength_refresh_slot_et(),),
    )
    slot = datetime(2026, 9, 24, 22, 0, tzinfo=EASTERN).timestamp()

    first = asyncio.run(task())
    assert first.next_delay_seconds == pytest.approx(slot - clock["now"])
    clock["now"] = slot - 60
    early = asyncio.run(task())
    assert early.next_delay_seconds == pytest.approx(60.0)
    clock["now"] = slot
    second = asyncio.run(task())

    assert runs == [datetime(2026, 9, 24, 14, 0, tzinfo=EASTERN).timestamp(), slot]
    next_slot = datetime(2026, 9, 25, 22, 0, tzinfo=EASTERN).timestamp()
    assert second.next_delay_seconds == pytest.approx(next_slot - slot)


# W-14: failed download batches vanished without a trace.
def test_failed_download_batches_are_listed_on_the_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def download(*, tickers: str, **_kwargs: object) -> pd.DataFrame:
        symbols = tickers.split()
        if symbols[0] == "T02":
            raise TimeoutError("rate limited")
        columns = pd.MultiIndex.from_product([symbols, ["Close"]])
        return pd.DataFrame([[1.0] * len(symbols)], columns=columns)

    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        frame = download_in_bounded_batches(
            download,
            tickers=[f"T{number:02d}" for number in range(5)],
            batch_size=2,
            group_by="ticker",
        )

    assert frame.attrs[FAILED_TICKERS_ATTR] == ["T02", "T03"]
    assert list(frame.columns.get_level_values(0)) == ["T00", "T01", "T04"]
    assert "stage=yfinance_batch_download" in caplog.text
    clean = download_in_bounded_batches(
        download,
        tickers=["T00"],
        group_by="ticker",
    )
    assert clean.attrs[FAILED_TICKERS_ATTR] == []


# W-17: retry reads ran synchronously on the event loop.
def test_requeue_reads_retry_state_off_the_event_loop(tmp_path: Path) -> None:
    repository = WorkerStateRepository(tmp_path / "requeue.db")
    repository.initialize()
    token = repository.acquire("owner", lease_seconds=30)
    assert token is not None
    queued = repository.request_action("probe_action", "probe_task", "requeue-1")
    assert repository.claim_actions("owner", token, "probe_task")
    readers: list[int] = []
    real_read = repository.action_request

    def spy(request_id: str) -> dict | None:
        readers.append(threading.get_ident())
        return real_read(request_id)

    repository.action_request = spy  # type: ignore[method-assign]
    spec = TaskSpec("probe_task", lambda: TaskResult(), 60.0)
    supervisor = WorkerSupervisor(
        repository,
        (spec,),
        owner_id="owner",
        process_lock=ProcessFileLock(tmp_path / "requeue.lock"),
    )

    async def scenario() -> int:
        await supervisor._requeue_or_exhaust(
            spec,
            token,
            [queued["request_id"]],
            now=datetime.now(timezone.utc),
            reason="task_timeout",
        )
        return threading.get_ident()

    loop_thread = asyncio.run(scenario())

    assert readers and loop_thread not in readers
    row = real_read(queued["request_id"])
    assert row is not None
    assert row["status"] == "queued"
    assert row["details"]["retry"]["attempt"] == 1


@pytest.mark.parametrize(
    ("message", "code", "attempts"),
    [
        ("disk I/O error", "worker_state_error", 1),
        ("database is locked", "worker_state_locked", 4),
    ],
)
def test_claim_failure_leaves_no_running_row_and_names_the_cause(
    tmp_path: Path,
    message: str,
    code: str,
    attempts: int,
) -> None:
    repository = WorkerStateRepository(tmp_path / "claim.db")
    calls: list[int] = []

    def broken_claim(*_args: object, **_kwargs: object) -> list:
        calls.append(1)
        raise sqlite3.OperationalError(message)

    repository.claim_actions = broken_claim  # type: ignore[method-assign]
    ran: list[int] = []

    async def runner() -> TaskResult:
        ran.append(1)
        return TaskResult()

    supervisor = WorkerSupervisor(
        repository,
        (TaskSpec("probe_task", runner, 3600.0),),
        owner_id="claim-worker",
        lease_seconds=5,
        process_lock=ProcessFileLock(tmp_path / "claim.lock"),
    )
    supervisor._STATE_LOCK_RETRY_DELAY_SECONDS = 0.0  # type: ignore[misc]

    payload = asyncio.run(supervisor.run_once())

    assert payload["tasks"]["probe_task"]["error_code"] == code
    assert len(calls) == attempts
    assert ran == []
    state = repository.task_states()[0]
    assert state["status"] == "degraded"
    assert state["error_code"] == code


@pytest.mark.parametrize("manual_only", [False, True])
def test_failing_claim_with_queued_actions_waits_out_its_backoff(
    tmp_path: Path,
    manual_only: bool,
) -> None:
    repository = WorkerStateRepository(tmp_path / "spin.db")
    repository.initialize()
    repository.request_action("probe_task", "probe_task", "spin-1")
    claims: list[int] = []

    def full_disk(*_args: object, **_kwargs: object) -> list:
        claims.append(1)
        raise sqlite3.OperationalError("database or disk is full")

    repository.claim_actions = full_disk  # type: ignore[method-assign]

    async def runner() -> TaskResult:
        return TaskResult()

    spec = TaskSpec(
        "probe_task",
        runner,
        3600.0,
        failure_backoff_seconds=60.0,
        max_backoff_seconds=60.0,
        manual_only=manual_only,
    )

    async def scenario() -> dict:
        worker = WorkerSupervisor(
            repository,
            (spec,),
            owner_id="spin-worker",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "spin.lock"),
        )
        running = asyncio.create_task(worker.run_forever())
        for _ in range(500):
            if claims:
                break
            await asyncio.sleep(0.01)
        # A spinning loop claims hundreds of times in this window.
        await asyncio.sleep(0.3)
        row = next(iter(repository.task_states()), {})
        worker.request_stop()
        await asyncio.wait_for(running, timeout=10)
        return row

    row = asyncio.run(scenario())

    assert len(claims) == 1
    assert row["status"] == "degraded"
    assert row["error_code"] == "worker_state_error"


def test_restart_waiting_for_its_schedule_is_not_reported_interrupted(
    tmp_path: Path,
) -> None:
    repository = WorkerStateRepository(tmp_path / "resume.db")
    repository.initialize()
    token = repository.acquire("crashed", lease_seconds=30)
    assert token is not None
    planned = datetime.now(timezone.utc) + timedelta(hours=20)
    repository.record_task(
        "crashed",
        token,
        "strength_refresh",
        enabled=True,
        status="running",
        next_run_at=planned,
    )
    runs: list[int] = []

    async def refresh() -> TaskResult:
        runs.append(1)
        return TaskResult()

    spec = TaskSpec("strength_refresh", refresh, 86_400.0, honor_persisted_schedule=True)

    async def scenario() -> dict:
        worker = WorkerSupervisor(
            repository,
            (spec,),
            owner_id="restarted",
            lease_seconds=5,
            process_lock=ProcessFileLock(tmp_path / "resume.lock"),
        )
        running = asyncio.create_task(worker.run_forever())
        row: dict = {}
        for _ in range(500):
            row = next(iter(repository.task_states()), {})
            if row.get("status") == "idle":
                break
            await asyncio.sleep(0.01)
        worker.request_stop()
        await asyncio.wait_for(running, timeout=10)
        return row

    row = asyncio.run(scenario())

    assert row["status"] == "idle"
    stored = datetime.fromisoformat(str(row["next_run_at"]).replace("Z", "+00:00"))
    assert abs((stored - planned).total_seconds()) < 5
    assert runs == []


def test_supervisor_requires_an_explicit_process_lock(tmp_path: Path) -> None:
    with pytest.raises(TypeError):
        WorkerSupervisor(  # type: ignore[call-arg]
            WorkerStateRepository(tmp_path / "lock.db"),
            (),
            owner_id="no-lock",
        )


def test_renew_reads_the_clock_after_taking_the_write_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker import state as state_module

    repository = WorkerStateRepository(tmp_path / "renew.db")
    repository.initialize()
    token = repository.acquire("owner", lease_seconds=30)
    assert token is not None
    events: list[str] = []
    real_connect = repository._connect

    class TracingConnection:
        def __init__(self, connection: sqlite3.Connection) -> None:
            self._connection = connection

        def execute(self, sql: str, *args: object):
            if sql.strip().upper().startswith("BEGIN"):
                events.append("lock")
            return self._connection.execute(sql, *args)

        def __getattr__(self, name: str):
            return getattr(self._connection, name)

    @contextmanager
    def tracing_connect(*args: object, **kwargs: object):
        with real_connect(*args, **kwargs) as connection:
            yield TracingConnection(connection)

    real_now = state_module.utc_now

    def traced_now() -> datetime:
        events.append("clock")
        return real_now()

    repository._connect = tracing_connect  # type: ignore[method-assign]
    monkeypatch.setattr(state_module, "utc_now", traced_now)

    assert repository.renew("owner", token, lease_seconds=30) is True
    assert events.index("lock") < events.index("clock")


# AI-10: an ai-jobs.db failure stopped news and calendar sync as well.
def test_catalyst_sync_publishes_when_ai_jobs_initialization_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services.ai_jobs.repository import AIJobRepository

    def broken_initialize(_self: AIJobRepository) -> None:
        raise RuntimeError("ai_job_source_schema_checksum_mismatch")

    monkeypatch.setattr(AIJobRepository, "initialize", broken_initialize)
    requested: list[str] = []
    local_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        return httpx.Response(200, json=_empty_etl_page(request.url.path))

    class LocalIntelligence:
        def __init__(self, *_args: object, **_options: object) -> None:
            pass

        def initialize(self) -> None:
            local_calls.append("initialize")

        def consume_refresh_requested(self) -> bool:
            return False

        def reconcile(self, *, allow_scheduled_jobs: bool = False) -> dict:
            local_calls.append("reconcile")
            return {"news": 0}

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setattr(
        "app.services.runtime_settings.get_effective_runtime_settings",
        lambda: _runtime_settings(),
    )
    task = CatalystSyncTask(
        "ai-jobs-broken",
        settings=_worker_config(
            tmp_path,
            token="owner-token",
            url="https://macrolens.example",
        ),
        personal_config=SimpleNamespace(
            catalyst=SimpleNamespace(sync_seconds=120),
            features=SimpleNamespace(catalyst_mode="read"),
            ai=SimpleNamespace(model="gpt-5.6-terra", reasoning="max"),
        ),
        etl_transport=httpx.MockTransport(handler),
        intelligence_factory=LocalIntelligence,
    )

    async def run() -> TaskResult:
        result = await task()
        await task.aclose()
        return result

    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        result = asyncio.run(run())

    assert result.status == "idle"
    assert result.details["processed"] == ["news", "calendar", "local_intelligence"]
    assert requested == ["/internal/v1/news/changes", "/internal/v1/calendar"]
    assert local_calls == ["initialize", "reconcile"]
    assert "stage=ai_jobs_repository_init" in caplog.text


def test_ai_jobs_task_caches_only_an_initialized_repository(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.ai_jobs.repository import AIJobRepository

    attempts: list[int] = []
    real_initialize = AIJobRepository.initialize

    def flaky_initialize(self: AIJobRepository) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("ai_job_source_schema_checksum_mismatch")
        real_initialize(self)

    monkeypatch.setattr(AIJobRepository, "initialize", flaky_initialize)
    task = AIJobsTask(
        "ai-init",
        settings=SimpleNamespace(
            openai_job_db_path=tmp_path / "ai-jobs.db",
            openai_api_key=SecretStr(""),
        ),
    )

    with pytest.raises(RuntimeError, match="checksum_mismatch"):
        asyncio.run(task())
    assert task._repository is None
    result = asyncio.run(task())

    assert result.status == "disabled"
    assert len(attempts) == 2


# M-4: every scan built a new provider, so its breaker and cache never engaged.
def test_breakout_task_reuses_one_discovery_provider_and_closes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.breakouts import config as breakout_config
    from app.services.breakouts import providers
    from app.services.breakouts import repository as breakout_repository
    from app.services.breakouts import service as breakout_service
    from app.services.breakouts import worker as breakout_worker

    settings = SimpleNamespace(
        enabled=True,
        db_path=tmp_path / "optix.db",
        worker_lease_ttl_seconds=90,
        scan_interval_regular_seconds=60,
        scan_interval_premarket_seconds=60,
        scan_interval_closed_seconds=300,
    )
    monkeypatch.setattr(breakout_config, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(
        breakout_repository,
        "BreakoutRepository",
        lambda path: SimpleNamespace(path=path),
    )
    monkeypatch.setattr(
        breakout_service,
        "BreakoutRadarService",
        lambda _settings: SimpleNamespace(),
    )
    created: list = []

    class Provider:
        def __init__(self, _settings: object) -> None:
            self.closed = 0
            created.append(self)

        async def aclose(self) -> None:
            self.closed += 1

    borrowed: list = []

    class Worker:
        def __init__(self, _settings, _repository, provider=None, **_kwargs) -> None:
            borrowed.append(provider)

        async def run_once(self) -> dict:
            return {"status": "completed", "session": "regular"}

    monkeypatch.setattr(providers, "TradingViewDiscoveryProvider", Provider)
    monkeypatch.setattr(breakout_worker, "BreakoutWorker", Worker)
    task = BreakoutTask("audit")

    async def scenario() -> list[TaskResult]:
        results = [await task() for _ in range(3)]
        await task.aclose()
        return results

    results = asyncio.run(scenario())

    assert [result.status for result in results] == ["idle"] * 3
    assert len(created) == 1
    assert borrowed == [created[0]] * 3
    assert created[0].closed == 1


# News and focus jobs had no retention; reconcile re-read all of them.
def test_retention_prunes_scheduled_ai_history_with_the_news_window(
    tmp_path: Path,
) -> None:
    calls: list[tuple[int, object]] = []

    class Backup:
        async def __call__(self) -> TaskResult:
            return TaskResult(status="idle", details={"backed_up": ["ai-jobs"], "failed": []})

    class AIRepository:
        def prune_scheduled_history(self, *, retain_days: int, now=None) -> int:
            calls.append((retain_days, now))
            return 5

    class LockedAIRepository:
        def prune_scheduled_history(self, **_kwargs: object) -> int:
            raise sqlite3.OperationalError("database is locked")

    missing = SimpleNamespace(db_path=tmp_path / "absent.db")

    def retention(factory: object) -> RetentionTask:
        return RetentionTask(
            "audit",
            Backup(),  # type: ignore[arg-type]
            settings_factory=lambda: missing,
            ai_repository_factory=factory,  # type: ignore[arg-type]
            ai_history_retain_days=30,
            now=lambda: NOW,
        )

    result = asyncio.run(retention(AIRepository)())
    failed = asyncio.run(retention(LockedAIRepository)())
    # Below the local news window the analysis links would dangle.
    floored = RetentionTask(
        "audit",
        Backup(),  # type: ignore[arg-type]
        settings_factory=lambda: missing,
        ai_repository_factory=AIRepository,
        ai_history_retain_days=8,
        now=lambda: NOW,
    )
    asyncio.run(floored())

    assert result.status == "idle"
    assert result.details["retention"] == {"status": "skipped_missing_database"}
    assert result.details["ai_history"] == {
        "status": "completed",
        "retain_days": 30,
        "deleted": 5,
    }
    assert calls == [(30, NOW), (30, NOW)]
    assert result.details["completed_at"] == "2026-07-16T00:00:00Z"
    assert failed.status == "degraded"
    assert failed.error_code == "ai_history_retention_failed"
    assert failed.details["retention"] == {"status": "skipped_missing_database"}
    assert failed.details["ai_history"]["status"] == "failed"


def _option_chain_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "strike": 100.0,
                "impliedVolatility": 0.32,
                "lastPrice": 1.5,
                "bid": 1.4,
                "ask": 1.6,
                "volume": 10,
                "openInterest": 20,
                "inTheMoney": True,
            }
        ]
    )


def test_option_chain_without_underlying_price_is_not_cached_as_valid(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import yahoo

    class BrokenQuote:
        @property
        def last_price(self) -> float:
            raise RuntimeError("quote endpoint unavailable")

    class Ticker:
        fast_info = BrokenQuote()

        def option_chain(self, _expiration: str) -> SimpleNamespace:
            return SimpleNamespace(calls=_option_chain_frame(), puts=pd.DataFrame())

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: Ticker())
    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        chain = yahoo.get_option_chain("AUDZ", "2030-09-18")

    assert chain["underlying_price"] is None
    assert chain["source_status"] == "insufficient_data"
    assert "stage=yahoo_option_chain_price" in caplog.text
    expires_at, fetched_at, _value = yahoo._cache["chain:AUDZ:2030-09-18"]
    assert (expires_at - fetched_at).total_seconds() <= 30


def test_last_price_failure_leaves_a_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from app.services import yahoo

    class Ticker:
        @property
        def fast_info(self) -> object:
            raise RuntimeError("quote endpoint unavailable")

    monkeypatch.setattr(yahoo, "_get_ticker", lambda _symbol: Ticker())
    with caplog.at_level(logging.WARNING, logger=failure_diagnostics.__name__):
        assert yahoo.get_last_price("AUDZ") is None

    assert "stage=yahoo_last_price" in caplog.text


def test_full_market_job_failure_is_logged_with_its_traceback(
    _no_variant_work: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    def broken_eod(**_kwargs: object) -> dict:
        raise RuntimeError("provider unavailable")

    task = StrengthRefreshTask(eod_runner=broken_eod, clock=lambda: 1_800_000_000.0)
    with caplog.at_level(logging.WARNING, logger="optix.worker"):
        result = asyncio.run(task())

    assert result.error_code == "eod_limited_input_unavailable"
    assert "failed_sessions" not in result.details
    record = next(
        item
        for item in caplog.records
        if "full-market strength job failed" in item.getMessage()
    )
    assert record.exc_info and record.exc_info[0] is RuntimeError


def test_full_market_job_failure_names_the_failed_sessions(
    _no_variant_work: None,
) -> None:
    from app.services.eod_limited.market_data import AllMarketDataError

    failed = [(f"2026-07-{day:02d}", "empty_grouped_daily") for day in range(1, 31)]

    def broken_eod(**_kwargs: object) -> dict:
        raise AllMarketDataError(
            "Massive grouped daily fetch failed",
            reason_code="empty_grouped_daily",
            failed_sessions=failed,
        )

    task = StrengthRefreshTask(eod_runner=broken_eod, clock=lambda: 1_800_000_000.0)
    result = asyncio.run(task())

    assert result.error_code == "eod_limited_input_unavailable"
    assert result.details["reason"] == "AllMarketDataError"
    assert result.details["reason_code"] == "empty_grouped_daily"
    assert result.details["failed_session_count"] == 30
    assert result.details["failed_sessions"][0] == {
        "session": "2026-07-01",
        "reason_code": "empty_grouped_daily",
    }
    assert len(result.details["failed_sessions"]) == 20
    _validate_action_detail(dict(result.details))


def test_manual_refresh_finishing_after_the_deadline_settles_its_action(
    _no_variant_work: None,
) -> None:
    import app.api.strength as strength_api

    started = threading.Event()
    release = threading.Event()
    runs: list[int] = []

    def slow_eod(**_kwargs: object) -> dict:
        runs.append(1)
        started.set()
        assert release.wait(timeout=5)
        return _published_outcome()

    parameters = strength_api.scheduled_strength_scan_parameters()
    action = {
        "request_id": "act-late",
        "details": {
            "parameters": parameters,
            "parameters_hash": strength_api.strength_scan_parameters_hash(parameters),
        },
    }
    task = StrengthRefreshTask(eod_runner=slow_eod, clock=lambda: 1_800_000_000.0)

    async def scenario() -> None:
        running = asyncio.create_task(task.run_for_actions([action]))
        assert await asyncio.to_thread(started.wait, 5)
        # The supervisor cancels at the task deadline; the job still publishes.
        running.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await running

    asyncio.run(scenario())

    progress = task.settled_action_progress
    assert len(runs) == 1
    assert [item["request_id"] for item in progress["action_completions"]] == ["act-late"]
    assert progress["action_completions"][0]["succeeded"] is True
    assert progress["requeued_request_ids"] == []


def test_timed_out_default_that_then_fails_is_retried(_no_variant_work: None) -> None:
    clock = {"now": 1_800_000_000.0}
    started = threading.Event()
    release = threading.Event()
    runs: list[str] = []

    def eod(**_kwargs: object) -> dict:
        runs.append("run")
        if len(runs) == 2:
            started.set()
            assert release.wait(timeout=5)
            raise RuntimeError("provider failed after the deadline")
        return {**_published_outcome(), "published_at": clock["now"]}

    task = StrengthRefreshTask(eod_runner=eod, clock=lambda: clock["now"])

    async def scenario() -> TaskResult:
        assert (await task()).status == "idle"
        clock["now"] += 86_400.0
        second = asyncio.create_task(task())
        assert await asyncio.to_thread(started.wait, 5)
        second.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await second
        clock["now"] += 5.0
        return await task()

    retried = asyncio.run(scenario())

    # The failed run is retried, not replaced by yesterday's success.
    assert runs == ["run", "run", "run"]
    assert retried.status == "idle"
    assert retried.details["completed_at"] == worker_tasks._timestamp_text(clock["now"])


def test_recent_variants_still_refresh_after_a_late_default(
    monkeypatch: pytest.MonkeyPatch,
    _no_variant_work: None,
) -> None:
    import app.api.strength as strength_api

    variant = {**strength_api.scheduled_strength_scan_parameters(), "timeframe": "short"}
    monkeypatch.setattr(
        strength_api,
        "list_recent_strength_variant_parameters",
        lambda *_args, **_kwargs: [variant],
    )
    started = threading.Event()
    release = threading.Event()
    runs: list[str] = []

    def eod(*, horizon: str, **_kwargs: object) -> dict:
        runs.append(horizon)
        if len(runs) == 1:
            started.set()
            assert release.wait(timeout=5)
        return _published_outcome()

    task = StrengthRefreshTask(eod_runner=eod, clock=lambda: 1_800_000_000.0)

    async def scenario() -> TaskResult:
        first = asyncio.create_task(task())
        assert await asyncio.to_thread(started.wait, 5)
        first.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await first
        return await task()

    second = asyncio.run(scenario())

    assert runs == ["mid", "short"]
    assert second.details["variant_refresh_attempted"] == 1
    assert second.details["variant_refresh_published"] == 1


@pytest.mark.parametrize(
    ("started", "slot"),
    [
        (datetime(2026, 9, 24, 15, tzinfo=EASTERN),
         datetime(2026, 9, 24, 22, tzinfo=EASTERN)),
        (datetime(2026, 10, 31, 22, tzinfo=EASTERN),
         datetime(2026, 11, 1, 22, tzinfo=EASTERN)),
        (datetime(2026, 3, 7, 22, tzinfo=EASTERN),
         datetime(2026, 3, 8, 22, tzinfo=EASTERN)),
    ],
)
def test_owner_action_after_restart_leaves_the_default_for_its_slot(
    _no_variant_work: None,
    started: datetime,
    slot: datetime,
) -> None:
    import app.api.strength as strength_api

    now = started.timestamp()
    runs: list[str] = []

    def eod(*, horizon: str, **_kwargs: object) -> dict:
        runs.append(horizon)
        return _published_outcome()

    parameters = strength_api.scheduled_strength_scan_parameters()
    action = {
        "request_id": "act-owner",
        "details": {
            "parameters": parameters,
            "parameters_hash": strength_api.strength_scan_parameters_hash(parameters),
        },
    }
    task = StrengthRefreshTask(
        eod_runner=eod,
        clock=lambda: now,
        refresh_times_et=(worker_tasks._strength_refresh_slot_et(),),
    )

    result = asyncio.run(task.run_for_actions([action]))

    assert runs == ["mid"]
    assert result.next_delay_seconds == pytest.approx(slot.timestamp() - now)


def test_default_runs_once_on_the_day_daylight_saving_time_ends(
    _no_variant_work: None,
) -> None:
    clock = {"now": datetime(2026, 10, 31, 22, 0, tzinfo=EASTERN).timestamp()}
    runs: list[float] = []

    def eod(**_kwargs: object) -> dict:
        runs.append(clock["now"])
        return _published_outcome()

    task = StrengthRefreshTask(
        eod_runner=eod,
        clock=lambda: clock["now"],
        refresh_times_et=("22:00",),
    )
    asyncio.run(task())
    clock["now"] = datetime(2026, 11, 1, 21, 0, tzinfo=EASTERN).timestamp()
    early = asyncio.run(task())
    clock["now"] = datetime(2026, 11, 1, 22, 0, tzinfo=EASTERN).timestamp()
    asyncio.run(task())

    assert len(runs) == 2
    assert early.next_delay_seconds == pytest.approx(3_600.0)


@pytest.mark.parametrize(
    ("start", "expected_hours"),
    [
        (datetime(2026, 10, 31, 22, tzinfo=EASTERN), 25),
        (datetime(2026, 3, 7, 22, tzinfo=EASTERN), 23),
        (datetime(2026, 9, 24, 22, tzinfo=EASTERN), 24),
    ],
)
@pytest.mark.parametrize("interrupted", [False, True], ids=["completed", "in-flight"])
def test_calendar_strength_restart_waits_for_the_absolute_slot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _no_variant_work: None,
    start: datetime,
    expected_hours: int,
    interrupted: bool,
) -> None:
    from app.worker import runtime, state

    clock = {"now": start.astimezone(timezone.utc)}
    monkeypatch.setattr(runtime, "utc_now", lambda: clock["now"])
    monkeypatch.setattr(state, "utc_now", lambda: clock["now"])
    repository = WorkerStateRepository(tmp_path / "calendar-restart.db")
    runs: list[datetime] = []
    waits: list[float] = []
    entered = asyncio.Event()

    def supervisor(
        owner: str, *, stop_after_run: bool, interrupt_during_run: bool = False,
    ) -> WorkerSupervisor:
        # Rebuild both the production task specification and runner, as a
        # deployment does; retaining the old runner hides the 25-hour bug.
        spec = next(
            task
            for task in build_default_tasks(owner, settings=_worker_config(tmp_path))
            if task.name == "strength_refresh"
        )
        runner = spec.runner
        assert isinstance(runner, StrengthRefreshTask)
        runner._clock = lambda: clock["now"].timestamp()
        worker = WorkerSupervisor(
            repository,
            (spec,),
            owner_id=owner,
            # Virtual waiting advances a whole day without real heartbeats.
            lease_seconds=7 * 86_400,
            process_lock=ProcessFileLock(tmp_path / "calendar-restart.lock"),
        )

        async def run(_parameters: dict) -> TaskResult:
            runs.append(clock["now"].astimezone(EASTERN))
            if interrupt_during_run:
                entered.set()
                await asyncio.Event().wait()
            if stop_after_run:
                worker.request_stop()
            return TaskResult(status="idle", details={"published": True})

        async def wait(_spec: TaskSpec, delay: float) -> bool:
            waits.append(delay)
            clock["now"] += timedelta(seconds=delay)
            return True

        monkeypatch.setattr(runner, "_run", run)
        monkeypatch.setattr(worker, "_wait_for_next", wait)
        return worker

    async def interrupt_first_run() -> None:
        first = supervisor("before", stop_after_run=False, interrupt_during_run=True)
        running = asyncio.create_task(first.run_forever())
        await asyncio.wait_for(entered.wait(), timeout=5)
        # This row was saved before the runner returned any result.
        row = repository.task_states()[0]
        assert row["status"] == "running"
        assert datetime.fromisoformat(row["next_run_at"]) == start + timedelta(days=1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

    if interrupted:
        asyncio.run(interrupt_first_run())
    else:
        asyncio.run(supervisor("before", stop_after_run=False).run_once())
    stored = datetime.fromisoformat(repository.task_states()[0]["next_run_at"])
    asyncio.run(supervisor("after", stop_after_run=True).run_forever())

    expected = start + timedelta(days=1)
    assert stored == expected
    assert waits == [expected_hours * 3_600]
    assert runs == [start, expected]


@pytest.mark.parametrize("interval", [600.0, 21_600.0])
def test_noncalendar_schedule_restore_keeps_its_configured_bound(
    tmp_path: Path, interval: float,
) -> None:
    repository = WorkerStateRepository(tmp_path / "bounded-schedule.db")
    repository.initialize()
    token = repository.acquire("seed", lease_seconds=30)
    assert token is not None
    repository.record_task(
        "seed", token, "maintenance", enabled=True, status="idle",
        next_run_at=datetime.now(timezone.utc) + timedelta(days=400),
    )
    repository.release("seed", token)
    spec = TaskSpec("maintenance", lambda: TaskResult(), interval,
                    honor_persisted_schedule=True)
    worker = WorkerSupervisor(
        repository, (spec,), owner_id="bounded",
        process_lock=ProcessFileLock(tmp_path / "bounded-schedule.lock"),
    )
    assert asyncio.run(worker._persisted_initial_delay(spec)) == interval


@pytest.mark.parametrize(
    ("observed", "expected_seconds"),
    [
        (datetime(2026, 9, 24, 15, tzinfo=EASTERN), 7 * 3_600),
        (datetime(2026, 10, 31, 22, tzinfo=EASTERN), 25 * 3_600),
    ],
)
def test_calendar_schedule_restore_bounds_a_distant_future_timestamp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed: datetime,
    expected_seconds: int,
) -> None:
    from app.worker import runtime, state

    now = observed.astimezone(timezone.utc)
    monkeypatch.setattr(runtime, "utc_now", lambda: now)
    monkeypatch.setattr(state, "utc_now", lambda: now)
    repository = WorkerStateRepository(tmp_path / "future-schedule.db")
    repository.initialize()
    token = repository.acquire("seed", lease_seconds=30)
    assert token is not None
    repository.record_task(
        "seed", token, "strength_refresh", enabled=True, status="idle",
        next_run_at=now + timedelta(days=400),
    )
    repository.release("seed", token)
    spec = next(
        task
        for task in build_default_tasks("future", settings=_worker_config(tmp_path))
        if task.name == "strength_refresh"
    )
    worker = WorkerSupervisor(
        repository, (spec,), owner_id="future",
        process_lock=ProcessFileLock(tmp_path / "future-schedule.lock"),
    )
    assert asyncio.run(worker._persisted_initial_delay(spec)) == expected_seconds


def test_noncalendar_strength_keeps_its_configured_interval(
    monkeypatch: pytest.MonkeyPatch, _no_variant_work: None,
) -> None:
    clock = {"now": datetime(2026, 10, 31, 22, tzinfo=EASTERN).timestamp()}
    runs: list[float] = []
    task = StrengthRefreshTask(
        clock=lambda: clock["now"], scheduled_interval_seconds=600,
    )

    async def run(_parameters: dict) -> TaskResult:
        runs.append(clock["now"])
        return TaskResult(status="idle", details={"published": True})

    monkeypatch.setattr(task, "_run", run)
    assert asyncio.run(task()).next_delay_seconds == 600
    clock["now"] += 300
    assert asyncio.run(task()).next_delay_seconds == 300
    clock["now"] += 300
    assert asyncio.run(task()).next_delay_seconds == 600
    assert len(runs) == 2


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf"), 90_001])
def test_task_result_rejects_delays_outside_a_civil_day(delay: float) -> None:
    with pytest.raises(ValueError, match="task delay"):
        TaskResult(next_delay_seconds=delay)


def test_retention_backup_copies_every_label_on_every_call(tmp_path: Path) -> None:
    good = _sqlite(tmp_path / "good.db")
    flaky = _sqlite(tmp_path / "flaky.db")
    healthy_bytes = flaky.read_bytes()
    destination = tmp_path / "backups"
    clock = {"now": START}
    backup = MaintenanceTask(
        {"good": good, "flaky": flaky},
        destination=destination,
        keep=5,
        full_cycle_per_call=True,
        now=lambda: clock["now"],
    )

    flaky.write_bytes(b"not a database " * 64)
    first = asyncio.run(backup())
    flaky.write_bytes(healthy_bytes)
    clock["now"] += timedelta(minutes=5)
    second = asyncio.run(backup())

    assert first.status == "degraded"
    assert first.details["backed_up"] == ["good"]
    # The owner fixed the source: the next retention retries at once and
    # backs up the healthy label again right before pruning.
    assert second.status == "idle"
    assert second.details["backed_up"] == ["good", "flaky"]
    assert len(_backup_times(destination, "good")) == 2


def test_restarted_maintenance_skips_labels_backed_up_moments_ago(
    tmp_path: Path,
) -> None:
    big = _sqlite(tmp_path / "big.db")
    small = _sqlite(tmp_path / "small.db")
    destination = tmp_path / "backups"
    clock = {"now": START}

    def task() -> MaintenanceTask:
        return MaintenanceTask(
            {"big": big, "small": small},
            destination=destination,
            keep=5,
            now=lambda: clock["now"],
        )

    before_restart = task()
    small_bytes = small.read_bytes()
    small.write_bytes(b"not a database " * 64)
    assert asyncio.run(before_restart()).details["failed"] == ["small"]
    small.write_bytes(small_bytes)
    clock["now"] += timedelta(minutes=20)

    restarted = asyncio.run(task()())

    assert restarted.details["backed_up"] == ["small"]
    assert len(_backup_times(destination, "big")) == 1
    clock["now"] += timedelta(hours=6)
    assert asyncio.run(task()()).details["backed_up"] == ["big", "small"]
