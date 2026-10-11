"""Account allowance transactions and exchange-close boundaries."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import threading

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.stock_analysis_quota import StockAnalysisQuotaExceeded, stock_analysis_period
from test_stock_ai_model import payload, result, message


def at(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@pytest.mark.parametrize("now,start,reset", [
    ("2026-10-09T19:59:59Z", "2026-10-08T20:00:00Z", "2026-10-09T20:00:00Z"),
    ("2026-10-09T20:00:00Z", "2026-10-09T20:00:00Z", "2026-10-12T20:00:00Z"),
    ("2026-10-11T04:00:00Z", "2026-10-09T20:00:00Z", "2026-10-12T20:00:00Z"),
    ("2026-11-27T17:59:59Z", "2026-11-25T21:00:00Z", "2026-11-27T18:00:00Z"),
    ("2026-11-27T18:00:00Z", "2026-11-27T18:00:00Z", "2026-11-30T21:00:00Z"),
    ("2026-03-08T18:00:00Z", "2026-03-06T21:00:00Z", "2026-03-09T20:00:00Z"),
    ("2026-11-01T18:00:00Z", "2026-10-30T20:00:00Z", "2026-11-02T21:00:00Z"),
])
def test_period_tracks_exact_nyse_close(now, start, reset):
    period = stock_analysis_period(at(now))
    assert period.start == at(start)
    assert period.reset_at == at(reset)


def create(repo, account="account-a", ticker="AMD", **overrides):
    model = overrides.pop("model", runtime.SONNET_MODEL)
    version, digest = runtime.schema_identity("signal_analysis", model=model)
    return repo.create_job(
        job_type="signal_analysis", payload={**payload(), "ticker": ticker},
        model=model, reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["signal_analysis"],
        schema_version=version, schema_sha256=digest, max_queued=100,
        stock_analysis_account_id=account, **overrides,
    )


def complete(repo, row):
    assert repo.claim_due("quota-worker", 60)["job_id"] == row["job_id"]
    repo.mark_submission_started(row["job_id"], "quota-worker", daily_limit=0)
    receipt = runtime.claude_receipt(message())
    repo.record_provider_result(row["job_id"], "quota-worker", receipt)
    repo.complete(row["job_id"], "quota-worker", result(), receipt["usage"])


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-09T20:00:00Z"))
    return AIJobRepository(tmp_path / "quota.db")


def test_allowance_survives_restart_and_force_cannot_bypass(repo):
    row, created = create(repo)
    assert created
    restarted = AIJobRepository(repo.path)
    assert restarted.stock_analysis_quota("account-a")["remaining"] == 0
    reused, created = create(restarted, force_retry=True)
    assert not created and reused["job_id"] == row["job_id"]
    with pytest.raises(StockAnalysisQuotaExceeded):
        create(restarted, ticker="MSFT", force_retry=True)
    assert "account-a" not in row["payload_json"]


def test_two_devices_cannot_spend_same_account_on_different_tickers(repo):
    repo.ensure_initialized()
    barrier = threading.Barrier(2)
    def competing(ticker):
        separate_connection = AIJobRepository(repo.path)
        barrier.wait()
        try:
            return create(separate_connection, ticker=ticker)[0]["job_id"]
        except StockAnalysisQuotaExceeded:
            return "denied"
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(competing, ["AMD", "MSFT"]))
    assert answers.count("denied") == 1
    with repo._connect() as connection:
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM ai_stock_analysis_allowances").fetchone()[0] == 1


def test_two_accounts_share_same_ticker_atomically_and_only_creator_spends(repo):
    repo.ensure_initialized()
    barrier = threading.Barrier(2)
    def competing(account):
        barrier.wait()
        return create(AIJobRepository(repo.path), account=account)
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(competing, ["account-a", "account-b"]))
    assert len({row["job_id"] for row, _ in answers}) == 1
    assert sum(created for _, created in answers) == 1
    assert sum(repo.stock_analysis_quota(key)["used"] for key in ["account-a", "account-b"]) == 1


def test_reset_allows_a_new_stock_after_next_close(repo, monkeypatch):
    create(repo)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T19:59:59Z"))
    assert repo.stock_analysis_quota("account-a")["remaining"] == 0
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    assert repo.stock_analysis_quota("account-a")["remaining"] == 1
    assert create(repo, ticker="MSFT")[1]


def test_failed_admission_does_not_spend_and_old_model_cannot_be_shared(repo):
    old, _ = create(repo, account=None, model="claude-haiku-5-5")
    current, created = create(repo)
    assert created and current["job_id"] != old["job_id"]
    with pytest.raises(ValueError, match="background_execution_required"):
        repo.create_job(job_type="signal_analysis", payload=payload(), model=runtime.SONNET_MODEL,
                        reasoning="xhigh", execution_mode="sync", prompt_version="invalid",
                        schema_version="invalid", schema_sha256="invalid", max_queued=1,
                        stock_analysis_account_id="account-b")
    assert repo.stock_analysis_quota("account-b")["remaining"] == 1


def test_completed_sharing_compares_timestamp_instants_not_iso_spelling(repo):
    row, _ = create(repo)
    complete(repo, row)
    # Stored Z and period +00:00 represent the same exact close.
    reused, created = create(repo, account="account-b")
    assert not created and reused["job_id"] == row["job_id"]
    assert repo.stock_analysis_quota("account-b")["remaining"] == 1


def test_same_saved_evidence_can_receive_new_research_after_reset(repo, monkeypatch):
    row, _ = create(repo)
    complete(repo, row)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    refreshed, created = create(repo)
    assert created and refreshed["job_id"] != row["job_id"]
    assert repo.stock_analysis_quota("account-a")["remaining"] == 0


def test_report_just_before_close_does_not_count_as_new_period(repo, monkeypatch):
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-09T19:59:59.999999Z"))
    first, _ = create(repo)
    complete(repo, first)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-09T20:00:00Z"))
    next_row, created = create(repo, account="account-b")
    assert created and next_row["job_id"] != first["job_id"]


def test_previous_period_completed_report_does_not_block_current_failed_retry(repo, monkeypatch):
    first, _ = create(repo)
    complete(repo, first)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    second, created = create(repo)
    assert created
    with repo._connect() as connection:
        connection.execute("UPDATE ai_jobs SET status='failed',error_code='provider_empty_response' WHERE job_id=?",
                           (second["job_id"],))
        connection.commit()
    with pytest.raises(StockAnalysisQuotaExceeded):
        create(repo, force_retry=True)
    third, created = create(repo, account="account-b")
    assert created and third["job_id"] != second["job_id"]
    assert third["execution_number"] == 3
    assert repo.stock_analysis_quota("account-b")["remaining"] == 0


def test_naive_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        stock_analysis_period(datetime(2026, 10, 11))
