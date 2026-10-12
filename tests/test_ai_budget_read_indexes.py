from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository


NOW = datetime(2026, 10, 12, 2, 25, tzinfo=timezone.utc)
INDEXES = ("idx_ai_jobs_submission_started", "idx_ai_jobs_credit_failure")


def seed(repo, *, status, submitted, completed=None, error=None):
    version, digest = runtime.schema_identity("earnings_impact")
    public, _ = repo.create_job(
        job_type="earnings_impact", payload={"ticker": "AAPL", "name": "Apple"},
        model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
        prompt_version=str(submitted), schema_version=version, schema_sha256=digest,
        max_queued=20,
    )
    with sqlite3.connect(repo.path) as conn:
        conn.execute(
            """UPDATE ai_jobs SET status=?,submission_started_at=?,completed_at=?,
               updated_at=?,error_code=?,usage_total_tokens=500,
               budget_charge_microusd=100 WHERE job_id=?""",
            (status, storage._iso(submitted), storage._iso(completed) if completed else None,
             storage._iso(completed or submitted), error, public["job_id"]),
        )


@pytest.mark.parametrize("scenario", ["empty", "terminal", "unknown", "unknown_expired", "credit", "recovered", "cross_day"])
def test_budget_indexes_preserve_full_snapshot_and_rows(tmp_path, scenario):
    repo = AIJobRepository(tmp_path / "jobs.db")
    repo.initialize()
    if scenario != "empty":
        submitted = NOW - timedelta(days=1) if scenario == "cross_day" else NOW - timedelta(minutes=20)
        if scenario == "unknown":
            submitted = NOW - timedelta(minutes=5)
        seed(repo, status="failed" if scenario in {"unknown", "unknown_expired", "credit", "recovered"} else "completed",
             submitted=submitted, completed=NOW - timedelta(minutes=10),
             error="submission_outcome_unknown" if scenario in {"unknown", "unknown_expired"} else
             "provider_credit_exhausted" if scenario in {"credit", "recovered"} else None)
    if scenario == "recovered":
        seed(repo, status="completed", submitted=NOW - timedelta(minutes=5), completed=NOW - timedelta(minutes=1))
    with sqlite3.connect(repo.path) as conn:
        for name in INDEXES:
            conn.execute(f"DROP INDEX {name}")
        conn.execute("DELETE FROM ai_job_schema WHERE version=?", (storage._BUDGET_READ_INDEX_VERSION,))
        original_rows = conn.execute("SELECT * FROM ai_jobs ORDER BY job_id").fetchall()
        registry = conn.execute("SELECT * FROM ai_job_schema ORDER BY version").fetchall()
    kwargs = dict(daily_limit=0, daily_budget_usd=0, now=NOW, cooldown_seconds=30, lane="manual", max_concurrency=4)
    before = repo.budget_snapshot(**kwargs)
    repo.initialize()
    repo.initialize()
    assert repo.budget_snapshot(**kwargs) == before
    assert before["submitted_jobs"] == (0 if scenario in {"empty", "cross_day"} else 2 if scenario == "recovered" else 1)
    assert before["provider_credit_exhausted"] is (scenario == "credit")
    assert before["active_jobs_count"] == (1 if scenario == "unknown" else 0)
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT * FROM ai_jobs ORDER BY job_id").fetchall() == original_rows
        assert conn.execute("SELECT * FROM ai_job_schema WHERE version<>? ORDER BY version", (storage._BUDGET_READ_INDEX_VERSION,)).fetchall() == registry
        assert conn.execute("SELECT checksum FROM ai_job_schema WHERE version=?", (storage._BUDGET_READ_INDEX_VERSION,)).fetchone() == (storage._BUDGET_READ_INDEX_CHECKSUM,)
        assert set(INDEXES) <= {row[1] for row in conn.execute("PRAGMA index_list(ai_jobs)")}


def test_budget_read_migration_rejects_checksum_mismatch(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    repo.initialize()
    with sqlite3.connect(repo.path) as conn:
        conn.execute("UPDATE ai_job_schema SET checksum='corrupt' WHERE version=?", (storage._BUDGET_READ_INDEX_VERSION,))
    with pytest.raises(RuntimeError, match="ai_job_budget_read_index_checksum_mismatch"):
        repo.initialize()
