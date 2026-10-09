from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
import threading

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository

CLAUDE = "claude-haiku-5-5"
LEGACY = "gpt-5.6-terra"


def _create(repo, index, *, model=CLAUDE, lane="manual", priority=50):
    version, digest = runtime.schema_identity("earnings_impact", model=model)
    job, _ = repo.create_job(
        job_type="earnings_impact", payload={"ticker": "AAPL", "name": f"issuer-{index}"},
        model=model, reasoning="xhigh" if model == CLAUDE else "max",
        execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version, schema_sha256=digest, max_queued=50,
        submission_source=lane, priority=priority,
    )
    return job


def _claim(repo, owner, *, limit=4):
    return repo.claim_due(owner, lease_seconds=600, max_concurrency=limit)


def _start(repo, row, owner, *, limit=4, tokens=10_000_000):
    return repo.mark_submission_started(
        row["job_id"], owner, max_concurrency=limit, daily_token_limit=tokens,
    )


def _snapshot(repo, *, model=CLAUDE, lane="manual", limit=4, now=None):
    return repo.budget_snapshot(
        model=model, lane=lane, max_concurrency=limit, daily_limit=0,
        daily_budget_usd=0, cooldown_seconds=0, now=now,
    )


def test_four_mixed_lane_claude_submissions_share_one_limit(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    for index in range(4):
        _create(repo, index, lane="manual" if index % 2 == 0 else "scheduled")
        row = _claim(repo, f"owner-{index}")
        assert _start(repo, row, f"owner-{index}") == "started"
        snapshot = _snapshot(repo, lane="scheduled")
        assert snapshot["active_jobs_count"] == index + 1
        assert snapshot["concurrency_limit"] == 4
        assert snapshot["concurrency_available"] is (index < 3)
    _create(repo, 5)
    assert _claim(repo, "fifth") is None
    assert _snapshot(repo)["concurrency_available"] is False


def test_atomic_thread_race_admits_exactly_four(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    for index in range(8):
        _create(repo, index, lane="manual" if index % 2 == 0 else "scheduled")
    claimed = [(_claim(repo, f"owner-{index}"), f"owner-{index}") for index in range(8)]
    assert len({row["job_id"] for row, _ in claimed}) == 8
    barrier = threading.Barrier(8)
    def start(item):
        row, owner = item
        barrier.wait(timeout=10)
        return _start(repo, row, owner)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(start, claimed))
    assert outcomes.count("started") == 4
    assert outcomes.count("concurrency_limit") == 4
    with sqlite3.connect(repo.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs WHERE submission_started_at IS NOT NULL").fetchone()[0] == 4
    assert _snapshot(repo)["active_jobs_count"] == 4


def test_atomic_threads_also_preserve_daily_token_reservations(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_task_token_reservation", lambda job_type, *, model=None: 40_000)
    repo = AIJobRepository(tmp_path / "jobs.db")
    for index in range(6):
        _create(repo, index, lane="manual" if index % 2 == 0 else "scheduled")
    claimed = [(_claim(repo, f"owner-{index}"), f"owner-{index}") for index in range(6)]
    barrier = threading.Barrier(6)
    def start(item):
        row, owner = item
        barrier.wait(timeout=10)
        return _start(repo, row, owner, tokens=120_000)
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(start, claimed))
    assert outcomes.count("started") == 3
    assert outcomes.count("daily_token_limit") == 3
    assert _snapshot(repo)["token_budget_used_tokens"] == 120_000


def test_recent_unknown_still_occupies_global_slot_and_expires(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    for index in range(4):
        _create(repo, index, lane="scheduled")
        row = _claim(repo, f"owner-{index}")
        assert _start(repo, row, f"owner-{index}") == "started"
        repo.fail(row["job_id"], f"owner-{index}", "submission_outcome_unknown")
    now = storage._utcnow()
    assert _snapshot(repo, now=now)["active_jobs_count"] == 4
    _create(repo, 5)
    assert _claim(repo, "fifth") is None
    later = now + timedelta(seconds=901)
    assert _snapshot(repo, now=later)["active_jobs_count"] == 0
    monkeypatch.setattr(storage, "_utcnow", lambda: later)
    replacement = _claim(repo, "after-hold")
    assert replacement is not None
    assert _start(repo, replacement, "after-hold") == "started"


def test_legacy_unknown_with_response_id_keeps_original_long_window(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0, model=LEGACY, lane="scheduled")
    row = _claim(repo, "legacy")
    assert _start(repo, row, "legacy") == "started"
    repo.link_background_response(row["job_id"], "legacy", "resp_test")
    repo.fail(row["job_id"], "legacy", "submission_outcome_unknown")
    later = storage._utcnow() + timedelta(seconds=901)
    assert _snapshot(repo, now=later)["active_jobs_count"] == 1
    assert _snapshot(repo, now=later + timedelta(days=1))["active_jobs_count"] == 0


@pytest.mark.parametrize("terminal", ["failed", "completed", "cancelled"])
def test_confirmed_terminal_releases_capacity(tmp_path, terminal):
    repo = AIJobRepository(tmp_path / "jobs.db")
    active = []
    for index in range(4):
        _create(repo, index)
        row = _claim(repo, f"owner-{index}")
        assert _start(repo, row, f"owner-{index}") == "started"
        active.append((row, f"owner-{index}"))
    row, owner = active[0]
    if terminal == "failed":
        repo.fail(row["job_id"], owner, "provider_failed")
    elif terminal == "completed":
        repo.complete(row["job_id"], owner, {}, {
            "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1,
            "reasoning_tokens": 0, "total_tokens": 2,
        })
    else:
        repo.mark_cancelled(row["job_id"], owner)
    assert _snapshot(repo)["active_jobs_count"] == 3
    _create(repo, 5)
    replacement = _claim(repo, "replacement")
    assert _start(repo, replacement, "replacement") == "started"


def test_same_job_cannot_be_claimed_or_submitted_twice(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0)
    row = _claim(repo, "first")
    assert _claim(repo, "second") is None
    assert _start(repo, row, "first") == "started"
    with pytest.raises(RuntimeError, match="ai_job_not_submittable"):
        _start(repo, row, "first")
    assert _snapshot(repo)["active_jobs_count"] == 1


def test_migrated_paid_pending_row_keeps_slot_and_cannot_restart(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0)
    row = _claim(repo, "first")
    assert _start(repo, row, "first") == "started"
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET status='pending' WHERE job_id=?", (row["job_id"],))
    assert _snapshot(repo)["active_jobs_count"] == 1
    with pytest.raises(RuntimeError, match="ai_job_not_submittable"):
        _start(repo, row, "first")


def test_openai_shares_one_provider_slot_across_lanes_with_global_limit_four(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0, model=LEGACY, lane="manual")
    row = _claim(repo, "first")
    assert _start(repo, row, "first") == "started"
    for index, lane in enumerate(["manual", "scheduled"], 1):
        _create(repo, index, model=LEGACY, lane=lane)
        assert _claim(repo, lane) is None
        snapshot = _snapshot(repo, model=LEGACY, lane=lane)
        assert snapshot["concurrency_limit"] == 4
        assert snapshot["active_jobs_count"] == 1
        assert not snapshot["concurrency_available"]
    assert _snapshot(repo)["concurrency_available"]


def test_legacy_openai_occupant_counts_toward_global_four(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0, model=LEGACY, lane="manual")
    row = _claim(repo, "legacy")
    assert _start(repo, row, "legacy") == "started"
    for index in range(3):
        _create(repo, index + 2, lane="scheduled")
        row = _claim(repo, f"claude-{index}")
        assert _start(repo, row, f"claude-{index}") == "started"
    assert _snapshot(repo)["active_jobs_count"] == 4
    _create(repo, 5)
    assert _claim(repo, "fifth") is None


def test_full_claude_global_capacity_also_blocks_openai(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    active = []
    for index in range(4):
        _create(repo, index, lane="scheduled")
        row = _claim(repo, f"owner-{index}")
        assert _start(repo, row, f"owner-{index}") == "started"
        active.append(row["job_id"])
    legacy = _create(repo, 6, model=LEGACY, priority=100)
    assert _claim(repo, "legacy") is None
    # A freed global slot admits OpenAI; its earlier block does not starve it.
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET status='completed' WHERE job_id=?", (active[0],))
    row = _claim(repo, "legacy")
    assert row["job_id"] == legacy["job_id"]
    assert _start(repo, row, "legacy") == "started"
    assert _snapshot(repo)["active_jobs_count"] == 4


def test_optional_concurrency_default_is_one_globally_across_models_and_lanes(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0, model=LEGACY)
    row = repo.claim_due("first", 600)
    assert repo.mark_submission_started(row["job_id"], "first") == "started"
    _create(repo, 1, model=LEGACY)
    assert repo.claim_due("second", 600) is None
    _create(repo, 2, model=LEGACY, lane="scheduled")
    _create(repo, 3, model=CLAUDE, lane="scheduled")
    assert repo.claim_due("scheduled", 600) is None
    assert _snapshot(repo, limit=1)["concurrency_limit"] == 1
    assert not _snapshot(repo, limit=1)["concurrency_available"]


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_configured_lower_claude_limits_apply_to_both_lanes(tmp_path, limit):
    repo = AIJobRepository(tmp_path / "jobs.db")
    for index in range(limit):
        _create(repo, index, lane="manual" if index % 2 == 0 else "scheduled")
        row = _claim(repo, f"owner-{index}", limit=limit)
        assert _start(repo, row, f"owner-{index}", limit=limit) == "started"
    _create(repo, 9, lane="scheduled")
    assert _claim(repo, "extra", limit=limit) is None
    assert _snapshot(repo, limit=limit)["concurrency_limit"] == limit
    assert not _snapshot(repo, limit=limit)["concurrency_available"]


def test_simultaneous_claims_for_one_job_have_one_owner(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _create(repo, 0)
    barrier = threading.Barrier(6)
    def claim(index):
        barrier.wait(timeout=10)
        return _claim(repo, f"owner-{index}")
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(claim, range(6)))
    assert sum(row is not None for row in rows) == 1


@pytest.mark.parametrize("limit", [0, 5, True, 1.5])
def test_invalid_concurrency_rejected_before_claim(tmp_path, limit):
    repo = AIJobRepository(tmp_path / "jobs.db")
    with pytest.raises(ValueError, match="max_concurrency"):
        repo.claim_due("owner", 600, max_concurrency=limit)
