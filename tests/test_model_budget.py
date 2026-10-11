from __future__ import annotations

import json
import multiprocessing
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.services.ai_jobs import repository as repository_module, runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.model_budget import (
    DailyBudgetExceeded, HAIKU_MODEL, OPUS_MODEL, SharedModelBudget, usd_to_microusd,
)

NOW = datetime(2026, 10, 8, 13, 45, 0, 500000, tzinfo=timezone.utc)


def _job(repository, *, ticker="AAPL", model=HAIKU_MODEL):
    version, digest = runtime.schema_identity("earnings_impact")
    created, _ = repository.create_job(
        job_type="earnings_impact", payload={"ticker": ticker, "name": ticker},
        model=model, reasoning="xhigh" if model == HAIKU_MODEL else "max",
        execution_mode="background", prompt_version="shared-budget-test",
        schema_version=version, schema_sha256=digest, max_queued=100,
    )
    return created["job_id"]


def _seed_charge(repository, amount, *, ticker="AAPL", model=HAIKU_MODEL, at=NOW, unknown=False):
    ident = _job(repository, ticker=ticker, model=model)
    with sqlite3.connect(repository.path) as conn:
        conn.execute(
            """UPDATE ai_jobs SET status='failed',submission_started_at=?,submitted_at=?,
               budget_charge_microusd=?,error_code=? WHERE job_id=?""",
            (repository_module._iso(at), repository_module._iso(at), amount,
             "submission_outcome_unknown" if unknown else "schema_validation_failed", ident),
        )
    return ident


def _claim(repository, ident, owner="owner"):
    with sqlite3.connect(repository.path) as conn:
        conn.execute("UPDATE ai_jobs SET lease_owner=?,lease_expires_at=? WHERE job_id=?",
                     (owner, repository_module._iso(NOW + timedelta(days=1)), ident))


def _mark(repository, ident, *, limit=9.5, start=None, tokens=10_000_000, owner="owner", enforce=True):
    return repository.mark_submission_started(
        ident, owner, daily_token_limit=tokens, max_concurrency=4,
        shared_daily_budget_usd=limit, shared_budget_start_at=start,
        shared_budget_enforce_limit=enforce,
    )


def _history(root, *, run_id="mb_old", cost=1_663_375, complete=True, at=NOW, submitted=None):
    root.mkdir(parents=True, exist_ok=True)
    (root / "runs").mkdir(exist_ok=True)
    stamp = repository_module._iso(at)
    document = {
        "record": {"run_id": run_id, "model": OPUS_MODEL, "started_at": stamp,
                   "status": "completed" if complete else "failed",
                   "cost_microusd": cost, "usage_complete": complete,
                   "usage": {"input_tokens": 100_000, "output_tokens": 40_000,
                             "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
                             "web_search_requests": 0}},
    }
    file_name = f"2026-10-08-pre_open-{run_id}.json"
    (root / "runs" / file_name).write_text(json.dumps(document))
    index_path = root / "index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"runs": []}
    index["runs"].append({"run_id": run_id, "started_at": stamp, "file": file_name})
    index_path.write_text(json.dumps(index))
    if submitted is not None:
        with sqlite3.connect(root / "admissions.sqlite3") as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS admissions (
                run_id TEXT PRIMARY KEY, started_at TEXT, submitted_at TEXT,
                trading_date TEXT, slot TEXT, model TEXT, status TEXT)""")
            conn.execute("INSERT INTO admissions VALUES(?,?,?,?,?,?,?)",
                         (run_id, stamp, repository_module._iso(submitted), "2026-10-08", "pre_open", OPUS_MODEL, "completed"))
    return document


def test_existing_haiku_unknowns_and_opus_share_budget_but_terra_is_excluded(tmp_path):
    repo = AIJobRepository(tmp_path / "ai-jobs.db")
    _seed_charge(repo, 1_096_541, ticker="HAI")
    for number in range(6):
        _seed_charge(repo, 757_880, ticker=f"U{number}", unknown=True)
    _seed_charge(repo, 6_662_797, ticker="OLD", model="gpt-5.6-terra")
    root = tmp_path / "market-brief"
    _history(root)
    budget = SharedModelBudget(repo.path, 9.5, root)
    budget.bootstrap_brief_history(NOW)
    result = budget.snapshot(NOW)
    assert result["haiku_charge_microusd"] == 5_643_821
    assert result["opus_charge_microusd"] == 1_663_375
    assert result["used_microusd"] == 13_969_993
    assert result["budget_remaining_usd"] == 0


def test_user_reset_filters_old_costs_without_modifying_old_unknowns(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "ai-jobs.db")
    ident = _seed_charge(repo, 757_880, unknown=True, at=NOW - timedelta(hours=2))
    before = repo.get_job(ident)
    root = tmp_path / "market-brief"
    _history(root, at=NOW - timedelta(hours=1))
    old_budget = SharedModelBudget(repo.path, 9.5, root)
    old_budget.bootstrap_brief_history(NOW)
    fresh = SharedModelBudget(repo.path, 9.5, root, accounting_start_at=NOW)
    assert fresh.snapshot(NOW)["used_microusd"] == 0
    assert fresh.reserve_brief_request("new_run", 0, 1_000_000, NOW)
    assert fresh.snapshot(NOW)["used_microusd"] == 1_000_000
    assert repo.get_job(ident) == before
    assert old_budget.snapshot(NOW)["used_microusd"] == 3_421_255
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM model_budget_bootstrap").fetchone()[0] == 2


def test_reservation_is_unique_and_settlement_survives_restart(tmp_path):
    path = tmp_path / "jobs.db"
    budget = SharedModelBudget(path, "9.50")
    assert budget.reserve_brief_request("run", 0, 2_000_000, NOW)
    assert not SharedModelBudget(path, 9.5).reserve_brief_request("run", 0, 2_000_000, NOW)
    assert budget.snapshot(NOW)["used_microusd"] == 2_000_000
    budget.settle_brief_request("run", 0, cost_microusd=250_000, accounting_complete=True)
    budget.settle_brief_request("run", 0, cost_microusd=250_000, accounting_complete=True)
    assert SharedModelBudget(path, 9.5).snapshot(NOW)["used_microusd"] == 250_000
    with pytest.raises(RuntimeError, match="settlement_conflict"):
        budget.settle_brief_request("run", 0, cost_microusd=200_000, accounting_complete=True)
    budget.settle_brief_request("run", 0, accounting_complete=False)
    assert budget.snapshot(NOW)["used_microusd"] == 250_000


def test_unknown_and_excess_actual_are_never_released_or_clipped(tmp_path):
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5)
    budget.reserve_brief_request("run", 0, 2_000_000, NOW)
    budget.settle_brief_request("run", 0, cost_microusd=100_000, accounting_complete=False)
    assert budget.snapshot(NOW)["used_microusd"] == 2_000_000
    budget.settle_brief_request("run", 0, cost_microusd=10_000_000, accounting_complete=True)
    assert budget.snapshot(NOW)["used_microusd"] == 10_000_000
    assert budget.snapshot(NOW)["budget_available"] is False
    with pytest.raises(DailyBudgetExceeded, match="daily_budget_usd_reached"):
        budget.reserve_brief_request("run", 1, 1, NOW)


def test_explicitly_unbilled_releases_but_cannot_be_replayed(tmp_path):
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5)
    budget.reserve_brief_request("run", 0, 2_000_000, NOW)
    budget.settle_brief_request("run", 0, confirmed_unbilled=True)
    assert budget.snapshot(NOW)["used_microusd"] == 0
    assert not budget.reserve_brief_request("run", 0, 2_000_000, NOW)


def test_each_continuation_reserves_and_keeps_its_utc_day(tmp_path):
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5)
    before = datetime(2026, 10, 8, 23, 59, 59, 999999, tzinfo=timezone.utc)
    after = before + timedelta(microseconds=1)
    budget.reserve_brief_request("run", 0, 2_000_000, before)
    budget.reserve_brief_request("run", 1, 3_000_000, after)
    budget.settle_brief_request("run", 0, cost_microusd=400_000, accounting_complete=True)
    assert budget.snapshot(before)["used_microusd"] == 400_000
    assert budget.snapshot(after)["used_microusd"] == 3_000_000
    assert not budget.reserve_brief_request("run", 0, 2_000_000, after)


def test_future_start_blocks_new_spending_and_snapshot(tmp_path, monkeypatch):
    start = NOW + timedelta(hours=1)
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, accounting_start_at=start)
    assert budget.snapshot(NOW)["budget_available"] is False
    with pytest.raises(DailyBudgetExceeded):
        budget.reserve_brief_request("run", 0, 100_000, NOW)
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(budget.path)
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident, start=start) == "daily_budget_usd_reached"
    assert repo.get_job(ident)["submission_started_at"] is None


def test_shared_dollars_replace_token_gate_and_bootstrap_before_ai_admission(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "ai-jobs.db")
    _history(tmp_path / "market-brief", cost=9_400_000, at=NOW - timedelta(minutes=5))
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident, tokens=102_400) == "daily_budget_usd_reached"
    assert repo.get_job(ident)["submission_started_at"] is None
    # A user-requested new start excludes the old report while preserving it.
    ident = _job(repo, ticker="NEXT")
    _claim(repo, ident)
    assert _mark(repo, ident, start=NOW + timedelta(microseconds=1), tokens=102_400) == "daily_budget_usd_reached"
    ident = _job(repo, ticker="FRESH")
    _claim(repo, ident)
    assert _mark(repo, ident, start=NOW, tokens=102_400) == "started"


def test_shared_budget_allows_haiku_above_old_token_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident, tokens=102_400) == "started"
    snapshot = repo.budget_snapshot(
        daily_limit=0, daily_budget_usd=0, daily_token_limit=102_400,
        shared_daily_budget_usd=9.5, now=NOW, model=HAIKU_MODEL,
    )
    assert snapshot["budget_basis"] == "shared_usd"
    assert snapshot["token_budget_available"] is True
    assert snapshot["budget_available"] is True
    assert snapshot["budget_used_usd"] > 0


@pytest.mark.parametrize("model", [HAIKU_MODEL, "gpt-5.6-terra"])
def test_disabled_shared_budget_preserves_old_token_gate(tmp_path, monkeypatch, model):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = _job(repo, model=model)
    _claim(repo, ident)
    expected = runtime.token_reservation("earnings_impact", model=model)
    result = _mark(repo, ident, limit=0, tokens=102_400)
    assert result == ("daily_token_limit" if expected > 102_400 else "started")


def test_legacy_import_is_idempotent_and_does_not_reimport_new_request_run(tmp_path):
    path = tmp_path / "jobs.db"
    root = tmp_path / "brief"
    budget = SharedModelBudget(path, 9.5)
    budget.reserve_brief_request("new_run", 0, 100_000, NOW)
    _history(root, run_id="new_run", cost=9_000_000)
    _history(root, run_id="old_run", cost=700_000)
    importing = SharedModelBudget(path, 9.5, root)
    assert importing.bootstrap_brief_history(NOW)["imported_count"] == 1
    assert importing.bootstrap_brief_history(NOW)["already_bootstrapped"]
    assert importing.snapshot(NOW)["used_microusd"] == 800_000


def test_unknown_legacy_requires_explicit_reservation_and_is_not_zero(tmp_path):
    root = tmp_path / "brief"
    _history(root, cost=None, complete=False)
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root)
    with pytest.raises(RuntimeError, match="unknown_reservation_required"):
        budget.bootstrap_brief_history(NOW)
    budget.bootstrap_brief_history(NOW, unknown_reservation_microusd=6_060_000)
    assert budget.snapshot(NOW)["used_microusd"] == 6_060_000


def test_history_reads_happen_without_an_ai_write_lock(tmp_path, monkeypatch):
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, tmp_path / "brief")
    original = budget._brief_history

    def read(*args):
        with sqlite3.connect(budget.path, timeout=0.05) as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.rollback()
        return original(*args)

    monkeypatch.setattr(budget, "_brief_history", read)
    budget.bootstrap_brief_history(NOW)


def test_legacy_cutoff_uses_submission_time_not_report_start(tmp_path):
    root = tmp_path / "brief"
    _history(root, at=NOW - timedelta(minutes=5), submitted=NOW + timedelta(seconds=1))
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root, accounting_start_at=NOW)
    assert budget.snapshot(NOW + timedelta(seconds=2))["used_microusd"] == 1_663_375


@pytest.mark.parametrize("value", [True, False, -1, float("nan"), float("inf"), "NaN", "-0.1", "0.0000001", None])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        usd_to_microusd(value)


def test_decimal_conversion_is_exact():
    assert usd_to_microusd(Decimal("9.50")) == 9_500_000
    assert usd_to_microusd(0.1) == 100_000


def _contend(path, run_id, barrier, queue):
    budget = SharedModelBudget(path, 1.5)
    barrier.wait(timeout=20)
    try:
        queue.put(budget.reserve_brief_request(run_id, 0, 2_000_000, NOW))
    except DailyBudgetExceeded:
        queue.put("blocked")


def test_two_processes_cannot_spend_the_same_remaining_budget(tmp_path):
    path = tmp_path / "jobs.db"
    budget = SharedModelBudget(path, 1.5)
    budget.snapshot(NOW)
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [context.Process(target=_contend, args=(path, f"run_{i}", barrier, queue)) for i in range(2)]
    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert outcomes.count(True) == 1
    assert outcomes.count("blocked") == 1
    assert budget.snapshot(NOW)["used_microusd"] == 1_500_000


def _contend_ai(path, ident, barrier, queue):
    repository_module._utcnow = lambda: NOW
    repo = AIJobRepository(path)
    barrier.wait(timeout=20)
    result = _mark(repo, ident, limit=1.5)
    queue.put(True if result == "started" else "blocked")


def test_haiku_and_opus_compete_in_the_same_sqlite_transaction(tmp_path):
    path = tmp_path / "jobs.db"
    repo = AIJobRepository(path)
    ident = _job(repo)
    _claim(repo, ident)
    budget = SharedModelBudget(path, 1.5)
    context = multiprocessing.get_context("spawn")
    barrier, queue = context.Barrier(2), context.Queue()
    processes = [
        context.Process(target=_contend, args=(path, "brief", barrier, queue)),
        context.Process(target=_contend_ai, args=(path, ident, barrier, queue)),
    ]
    for process in processes:
        process.start()
    outcomes = [queue.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    # If Haiku wins first, Opus takes the positive remainder; if Opus wins
    # first, Haiku keeps its original full-reservation rejection policy.
    assert outcomes.count(True) in {1, 2}
    assert budget.snapshot(NOW)["used_microusd"] == 1_500_000


def test_date_cutoff_includes_fractional_second_and_rejects_naive_start(tmp_path):
    start = NOW.replace(microsecond=0)
    repo = AIJobRepository(tmp_path / "jobs.db")
    _seed_charge(repo, 100_000, at=start + timedelta(microseconds=1))
    budget = SharedModelBudget(repo.path, 9.5, accounting_start_at=start)
    assert budget.snapshot(NOW)["used_microusd"] == 100_000
    with pytest.raises(ValueError, match="timezone-aware"):
        SharedModelBudget(repo.path, 9.5, accounting_start_at=start.replace(tzinfo=None))


def test_completed_history_is_not_rescanned_on_every_admission(tmp_path, monkeypatch):
    root = tmp_path / "brief"
    _history(root)
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root)
    budget.bootstrap_brief_history(NOW)

    def unexpected(*args):
        raise AssertionError("history must not be read again for the same UTC window")

    monkeypatch.setattr(budget, "_brief_history", unexpected)
    budget.reserve_brief_request("new", 0, 100_000, NOW)
    budget.snapshot(NOW)


def test_paid_haiku_receipt_survives_missing_usage_without_releasing_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident) == "started"
    reserved = repo.get_job(ident)["budget_charge_microusd"]
    receipt = {
        "provider": "anthropic", "model": HAIKU_MODEL, "id": "msg_paid",
        "output_text": "已付费的完整结果", "stop_reason": "end_turn",
        "terminal_error": None, "evidence_sources": [],
        "usage": {key: None for key in (
            "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
            "cache_creation_input_tokens", "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens",
            "web_search_requests", "web_fetch_requests", "code_execution_requests",
        )},
    }
    repo.record_provider_result(ident, "owner", receipt)
    repo.fail(ident, "owner", "schema_validation_failed")
    assert repo.get_provider_result(ident)["output_text"] == "已付费的完整结果"
    assert repo.get_job(ident)["budget_charge_microusd"] == reserved
    assert SharedModelBudget(repo.path, 9.5).snapshot(NOW)["used_microusd"] == reserved
    receipt["usage"]["total_tokens"] = 0
    with pytest.raises(ValueError, match="usage_invalid"):
        repo._provider_receipt_json(receipt)


def test_shared_haiku_sum_uses_covering_index_without_payload_reads(tmp_path):
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5)
    budget.snapshot(NOW)
    with sqlite3.connect(budget.path) as conn:
        plan = conn.execute(
            """EXPLAIN QUERY PLAN SELECT SUM(budget_charge_microusd) FROM ai_jobs
               WHERE model=? AND submission_started_at>=? AND submission_started_at<?
                 AND submission_started_at<>?""",
            (HAIKU_MODEL, "2026-10-08T13:45:00.500000Z", "2026-10-09T00:00:00.000000Z", "2026-10-08T13:45:00Z"),
        ).fetchall()
    assert any("COVERING INDEX idx_ai_jobs_model_budget_day" in row[3] for row in plan)


def test_exhausted_budget_snapshot_does_not_advertise_room_for_new_work(tmp_path):
    budget = SharedModelBudget(tmp_path / "jobs.db", 1)
    budget.reserve_brief_request("last", 0, 1_000_000, NOW)
    assert budget.snapshot(NOW)["budget_remaining_usd"] == 0
    assert budget.snapshot(NOW)["budget_available"] is False


def test_fractional_reset_excludes_older_whole_second_haiku_timestamp(tmp_path):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _seed_charge(repo, 757_880, ticker="WHOLE", at=NOW.replace(microsecond=0), unknown=True)
    _seed_charge(repo, 100, ticker="EARLY", at=NOW - timedelta(microseconds=1))
    _seed_charge(repo, 200, ticker="EXACT", at=NOW)
    _seed_charge(repo, 300, ticker="LATER", at=NOW + timedelta(microseconds=1))
    budget = SharedModelBudget(repo.path, 9.5, accounting_start_at=NOW)
    assert budget.snapshot(NOW)["used_microusd"] == 500
    snapshot = repo.budget_snapshot(
        daily_limit=0, daily_budget_usd=0, shared_daily_budget_usd=9.5,
        shared_budget_start_at=NOW, now=NOW, model=HAIKU_MODEL,
    )
    assert snapshot["budget_used_usd"] == 0.0005


def test_legacy_opus_started_before_midnight_is_charged_on_submission_day(tmp_path):
    root = tmp_path / "brief"
    started = datetime(2026, 10, 8, 23, 59, 30, tzinfo=timezone.utc)
    submitted = datetime(2026, 10, 9, 0, 0, 1, tzinfo=timezone.utc)
    _history(root, at=started, submitted=submitted)
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root)
    # Querying the report's start day first must not import the next-day cost.
    assert budget.snapshot(started)["used_microusd"] == 0
    assert budget.snapshot(submitted)["used_microusd"] == 1_663_375
    assert budget.snapshot(started)["used_microusd"] == 0


@pytest.mark.parametrize("missing", ["input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "web_search_requests"])
def test_legacy_zero_cost_without_complete_usage_cannot_release_budget(tmp_path, missing):
    root = tmp_path / "brief"
    _history(root, cost=0)
    file = next((root / "runs").glob("*.json"))
    document = json.loads(file.read_text())
    del document["record"]["usage"][missing]
    file.write_text(json.dumps(document))
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root)
    with pytest.raises(RuntimeError, match="unknown_reservation_required"):
        budget.bootstrap_brief_history(NOW)
    budget.bootstrap_brief_history(NOW, unknown_reservation_microusd=6_060_000)
    assert budget.snapshot(NOW)["used_microusd"] == 6_060_000


@pytest.mark.parametrize("invalid", [True, -1, "0", None])
def test_legacy_invalid_usage_or_cost_is_not_treated_as_known_zero(tmp_path, invalid):
    root = tmp_path / "brief"
    _history(root, cost=invalid)
    file = next((root / "runs").glob("*.json"))
    document = json.loads(file.read_text())
    document["record"]["usage"]["input_tokens"] = invalid
    file.write_text(json.dumps(document))
    budget = SharedModelBudget(tmp_path / "jobs.db", 9.5, root)
    with pytest.raises(RuntimeError, match="unknown_reservation_required"):
        budget.bootstrap_brief_history(NOW)


@pytest.mark.parametrize("status_code", [400, 401, 403, 404, 413, 422, 429])
def test_definitive_worker_rejection_remains_free_after_repository_restart(tmp_path, monkeypatch, status_code):
    import asyncio
    from app.config import Settings
    from app.services.ai_jobs import claude_provider, worker

    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    version, digest = runtime.schema_identity("earnings_impact")
    created, _ = repo.create_job(
        job_type="earnings_impact", payload={"ticker": "AAPL", "name": "Apple"},
        model=HAIKU_MODEL, reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["earnings_impact"],
        schema_version=version, schema_sha256=digest, max_queued=10,
    )
    settings = Settings(
        openai_job_db_path=repo.path, anthropic_api_key="test-key",
        openai_model=HAIKU_MODEL, openai_reasoning="xhigh",
        openai_manual_cooldown_seconds=0,
    )

    class Rejected(Exception):
        pass

    async def rejected(*args, **kwargs):
        error = Rejected("definitive rejection")
        error.status_code = status_code
        error.body = {"error": {"type": "invalid_request_error"}}
        raise error

    reported = []
    fail = repo.fail

    def record_failure(*args, **kwargs):
        reported.append(kwargs.get("usage"))
        return fail(*args, **kwargs)

    monkeypatch.setattr(repo, "fail", record_failure)
    monkeypatch.setattr(claude_provider, "stream_message", rejected)
    asyncio.run(worker.run_once(repo, settings, "owner"))
    assert reported and reported[0]["web_search_requests"] == 0
    assert reported[0]["web_fetch_requests"] == 0
    assert reported[0]["code_execution_requests"] == 0
    assert repo.get_job(created["job_id"])["budget_charge_microusd"] == 0
    reopened = AIJobRepository(repo.path)
    reopened.initialize()
    assert reopened.get_job(created["job_id"])["budget_charge_microusd"] == 0
    assert SharedModelBudget(repo.path, 9.5).snapshot(NOW)["used_microusd"] == 0


@pytest.mark.parametrize("searches", [0, 1])
def test_zero_charge_backfill_uses_durable_receipt_tool_usage(tmp_path, monkeypatch, searches):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident) == "started"
    usage = dict.fromkeys((
        "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "total_tokens",
        "cache_creation_input_tokens", "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens",
        "web_search_requests", "web_fetch_requests", "code_execution_requests",
    ), 0)
    usage["web_search_requests"] = searches
    repo.record_provider_result(ident, "owner", {
        "provider": "anthropic", "model": HAIKU_MODEL, "id": "msg_paid",
        "output_text": "已保存结果", "stop_reason": "end_turn", "terminal_error": None,
        "evidence_sources": [], "usage": usage,
    })
    repo.complete(ident, "owner", {"saved": True}, usage)
    with sqlite3.connect(repo.path) as connection:
        connection.execute("UPDATE ai_jobs SET budget_charge_microusd=0 WHERE job_id=?", (ident,))
    reopened = AIJobRepository(repo.path)
    reopened.initialize()
    assert reopened.get_job(ident)["budget_charge_microusd"] == searches * 10_000


@pytest.mark.parametrize("remaining", [1, 200_000, 6_059_999])
def test_opus_positive_balance_allows_initial_and_continuation_without_fixed_minimum(tmp_path, remaining):
    repo = AIJobRepository(tmp_path / "jobs.db")
    _seed_charge(repo, 9_500_000 - remaining)
    budget = SharedModelBudget(repo.path, 9.5)
    assert budget.snapshot(NOW, reservation_microusd=6_060_000)["budget_available"]
    assert budget.reserve_brief_request("run", 1, 6_060_000, NOW)
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT reservation_microusd FROM model_budget_brief_requests").fetchone()[0] == remaining
    assert budget.snapshot(NOW)["used_microusd"] == 9_500_000
    budget.settle_brief_request("run", 1, cost_microusd=0, accounting_complete=True)
    assert budget.reserve_brief_request("run", 2, 6_060_000, NOW)
    budget.settle_brief_request("run", 2, accounting_complete=False)
    assert budget.snapshot(NOW)["opus_unsettled_microusd"] == remaining
    with pytest.raises(DailyBudgetExceeded):
        budget.reserve_brief_request("other", 1, 6_060_000, NOW)
    # Actual provider fees remain authoritative even above a partial hold.
    budget.settle_brief_request("run", 2, cost_microusd=remaining + 10, accounting_complete=True)
    assert budget.snapshot(NOW)["used_microusd"] == 9_500_010


def test_tracking_mode_records_full_over_budget_hold_and_keeps_unknown_day_and_cutoff(tmp_path):
    before = datetime(2026, 10, 8, 23, 59, 59, tzinfo=timezone.utc)
    after = before + timedelta(seconds=2)
    budget = SharedModelBudget(tmp_path / "jobs.db", 1, enforce_limit=False)
    assert budget.reserve_brief_request("run", 1, 6_060_000, before)
    budget.settle_brief_request("run", 1, accounting_complete=False)
    snapshot = budget.snapshot(before)
    assert snapshot["budget_available"] and snapshot["budget_mode"] == "tracking"
    assert snapshot["budget_enforced"] is False and snapshot["budget_remaining_usd"] == 0
    assert snapshot["opus_unsettled_microusd"] == 6_060_000
    assert budget.reserve_brief_request("run", 2, 6_060_000, after)
    assert budget.snapshot(after)["used_microusd"] == 6_060_000
    assert not budget.reserve_brief_request("run", 1, 6_060_000, after)
    cutoff = SharedModelBudget(budget.path, 1, accounting_start_at=after, enforce_limit=False)
    assert cutoff.snapshot(after)["used_microusd"] == 6_060_000
    assert budget.snapshot(before)["used_microusd"] == 6_060_000


def test_haiku_tracking_mode_bypasses_amount_but_keeps_full_reservation(tmp_path, monkeypatch):
    monkeypatch.setattr(repository_module, "_utcnow", lambda: NOW)
    repo = AIJobRepository(tmp_path / "jobs.db")
    _seed_charge(repo, 11_000_000, ticker="ALREADY")
    ident = _job(repo)
    _claim(repo, ident)
    assert _mark(repo, ident, limit=10, enforce=False) == "started"
    held = repo.get_job(ident)["budget_charge_microusd"]
    assert held > 0
    snapshot = repo.budget_snapshot(daily_limit=0, daily_budget_usd=0,
        shared_daily_budget_usd=10, shared_budget_enforce_limit=False, now=NOW, model=HAIKU_MODEL)
    assert snapshot["budget_available"] and snapshot["dollar_budget_available"]
    assert snapshot["budget_mode"] == "tracking" and snapshot["budget_enforced"] is False
    assert snapshot["budget_used_usd"] == (11_000_000 + held) / 1_000_000
    assert snapshot["budget_remaining_usd"] == 0


@pytest.mark.parametrize("enforce", [True, False])
def test_new_opus_unknown_hold_cannot_start_as_zero(tmp_path, enforce):
    budget = SharedModelBudget(tmp_path / "jobs.db", 10, enforce_limit=enforce)
    with pytest.raises(ValueError, match="positive"):
        budget.reserve_brief_request("run", 1, 0, NOW)
    assert budget.snapshot(NOW)["used_microusd"] == 0
