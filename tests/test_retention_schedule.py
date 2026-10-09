"""Scheduled retention: AI history is pruned after each backup cycle, in batches."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from app.services.ai_jobs import repository as repo_mod
from app.services.ai_jobs import runtime as ai_runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.worker.tasks import MaintenanceTask

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)


def _database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE sample(value INTEGER)")
        connection.execute("INSERT INTO sample VALUES(1)")
    return path


def test_maintenance_prunes_once_per_completed_backup_cycle(tmp_path: Path) -> None:
    clock = {"now": NOW}
    calls: list[datetime] = []

    async def prune():
        calls.append(clock["now"])
        return {"status": "completed", "retain_days": 30, "deleted": 7}, None

    task = MaintenanceTask(
        {"first": _database(tmp_path / "first.db"), "second": _database(tmp_path / "second.db")},
        destination=tmp_path / "backups",
        keep=2,
        interval_seconds=3_600,
        after_cycle=prune,
        now=lambda: clock["now"],
    )
    first = asyncio.run(task())
    assert set(first.details["backed_up"]) == {"first", "second"}
    assert first.details["ai_history"] == {"status": "completed", "retain_days": 30, "deleted": 7}
    clock["now"] += timedelta(minutes=10)
    again = asyncio.run(task())
    assert again.details["backed_up"] == []
    assert "ai_history" not in again.details
    clock["now"] += timedelta(hours=1)
    next_cycle = asyncio.run(task())
    assert set(next_cycle.details["backed_up"]) == {"first", "second"}
    assert len(calls) == 2


def test_maintenance_waits_for_failed_backups_before_pruning(tmp_path: Path) -> None:
    clock = {"now": NOW}
    calls = 0

    async def prune():
        nonlocal calls
        calls += 1
        return {"status": "completed", "retain_days": 30, "deleted": 0}, None

    healthy = _database(tmp_path / "healthy.db")
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"not a database" * 200)
    task = MaintenanceTask(
        {"healthy": healthy, "broken": broken},
        destination=tmp_path / "backups",
        keep=2,
        interval_seconds=3_600,
        failure_backoff_seconds=60,
        max_backoff_seconds=120,
        after_cycle=prune,
        now=lambda: clock["now"],
    )
    failed = asyncio.run(task())
    assert failed.status == "degraded"
    assert "ai_history" not in failed.details
    assert calls == 0
    broken.unlink()
    _database(broken)
    clock["now"] += timedelta(minutes=2)
    repaired = asyncio.run(task())
    assert repaired.details["backed_up"] == ["broken"]
    assert repaired.details["ai_history"]["status"] == "completed"
    assert calls == 1


class _TracedRepository(AIJobRepository):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.statements: list[str] = []

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with super()._connect() as connection:
            connection.set_trace_callback(self.statements.append)
            yield connection


def _news_job(repository: AIJobRepository, news_id: int) -> str:
    model = "gpt-5.6-terra"
    version, digest = ai_runtime.schema_identity("news_impact", model=model)
    row, _created = repository.create_job(
        job_type="news_impact",
        payload={"news_id": news_id, "change_sequence": 1, "content_hash": f"h{news_id}"},
        model=model,
        reasoning="max",
        execution_mode="background",
        prompt_version=ai_runtime.PROMPT_VERSIONS["news_impact"],
        schema_version=version,
        schema_sha256=digest,
        max_queued=500,
        submission_source="scheduled",
        priority=50,
    )
    return str(row["job_id"])


def test_history_prune_deletes_in_short_batches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(repo_mod, "_utcnow", lambda: NOW - timedelta(days=60))
    repository = _TracedRepository(tmp_path / "ai-jobs.db")
    expired = [_news_job(repository, news_id) for news_id in range(1, 6)]
    keep = _news_job(repository, 99)
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET status='completed',completed_at=? WHERE job_id!=?",
            (repo_mod._iso(NOW - timedelta(days=59)), keep),
        )
        connection.commit()
    monkeypatch.setattr(repo_mod, "_utcnow", lambda: NOW)
    repository.statements.clear()
    removed = repository.prune_scheduled_history(retain_days=30, now=NOW, batch_size=2)
    assert removed == len(expired)
    # Five rows in batches of two: three short write transactions, and the
    # candidate scan itself runs outside any of them.
    assert sum(statement == "BEGIN IMMEDIATE" for statement in repository.statements) == 3
    with repository._connect() as connection:
        remaining = [row[0] for row in connection.execute("SELECT job_id FROM ai_jobs")]
        orphans = connection.execute(
            "SELECT COUNT(*) FROM ai_job_sources WHERE job_id NOT IN (SELECT job_id FROM ai_jobs)"
        ).fetchone()[0]
    assert remaining == [keep]
    assert orphans == 0


def test_history_prune_rechecks_rows_that_changed_after_selection(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(repo_mod, "_utcnow", lambda: NOW - timedelta(days=60))
    repository = AIJobRepository(tmp_path / "ai-jobs.db")
    jobs = [_news_job(repository, news_id) for news_id in range(1, 4)]
    with repository._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET status='completed',completed_at=?",
            (repo_mod._iso(NOW - timedelta(days=59)),),
        )
        connection.commit()
    monkeypatch.setattr(repo_mod, "_utcnow", lambda: NOW)
    repository.ensure_initialized()
    original_connect = repository._connect
    opened = 0

    @contextmanager
    def connect_then_requeue() -> Iterator[sqlite3.Connection]:
        nonlocal opened
        opened += 1
        with original_connect() as connection:
            if opened == 2:
                # A retry re-queues one job between the scan and its batch.
                connection.execute("UPDATE ai_jobs SET status='pending' WHERE job_id=?", (jobs[1],))
                connection.commit()
            yield connection

    monkeypatch.setattr(repository, "_connect", connect_then_requeue)
    removed = repository.prune_scheduled_history(retain_days=30, now=NOW, batch_size=10)
    monkeypatch.setattr(repository, "_connect", original_connect)
    assert opened == 2
    assert removed == 2
    with repository._connect() as connection:
        assert [row[0] for row in connection.execute("SELECT job_id FROM ai_jobs")] == [jobs[1]]
