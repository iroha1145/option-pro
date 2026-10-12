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
        job_type="signal_analysis", payload=overrides.pop("payload", {**payload(), "ticker": ticker}),
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
    assert restarted.stock_analysis_quota("account-a")["remaining"] == 9
    reused, created = create(restarted, force_retry=True)
    assert not created and reused["job_id"] == row["job_id"]
    for n in range(9):
        assert create(restarted, ticker=f"STOCK{n}", force_retry=True)[1]
    with pytest.raises(StockAnalysisQuotaExceeded):
        create(restarted, ticker="MSFT", force_retry=True)
    assert "account-a" not in row["payload_json"]


def test_two_devices_cannot_spend_same_account_on_different_tickers(repo):
    for n in range(9):
        create(repo, ticker=f"STOCK{n}")
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
        assert connection.execute("SELECT count(*) FROM ai_jobs").fetchone()[0] == 10
        assert connection.execute("SELECT count(*) FROM ai_stock_analysis_allowances").fetchone()[0] == 10


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
    assert repo.stock_analysis_quota("account-a")["remaining"] == 9
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    assert repo.stock_analysis_quota("account-a")["remaining"] == 10
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
    assert repo.stock_analysis_quota("account-b")["remaining"] == 10


def test_completed_sharing_compares_timestamp_instants_not_iso_spelling(repo):
    row, _ = create(repo)
    complete(repo, row)
    # Stored Z and period +00:00 represent the same exact close.
    reused, created = create(repo, account="account-b")
    assert not created and reused["job_id"] == row["job_id"]
    assert repo.stock_analysis_quota("account-b")["remaining"] == 10


def test_same_saved_evidence_can_receive_new_research_after_reset(repo, monkeypatch):
    row, _ = create(repo)
    complete(repo, row)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    refreshed, created = create(repo)
    assert created and refreshed["job_id"] != row["job_id"]
    assert repo.stock_analysis_quota("account-a")["remaining"] == 9


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
    for n in range(9):
        create(repo, ticker=f"STOCK{n}")
    with pytest.raises(StockAnalysisQuotaExceeded):
        create(repo, force_retry=True, stock_analysis_retry_job_id=second["job_id"])
    third, created = create(repo, account="account-b")
    assert created and third["job_id"] != second["job_id"]
    assert third["execution_number"] == 3
    assert repo.stock_analysis_quota("account-b")["remaining"] == 9


def test_naive_time_is_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        stock_analysis_period(datetime(2026, 10, 11))


def identity():
    version, digest = runtime.schema_identity("signal_analysis", model=runtime.SONNET_MODEL)
    return dict(model=runtime.SONNET_MODEL, reasoning="xhigh",
                prompt_version=runtime.PROMPT_VERSIONS["signal_analysis"],
                schema_version=version, schema_sha256=digest)


def test_legacy_allowance_migration_preserves_consumption_and_foreign_keys(repo):
    row, _ = create(repo)
    with repo._connect() as connection:
        old = dict(connection.execute("SELECT * FROM ai_stock_analysis_allowances").fetchone())
        connection.execute("DROP TABLE ai_stock_analysis_allowances")
        for statement in storage._statements(storage._STOCK_QUOTA_SCHEMA_SQL):
            connection.execute(statement)
        connection.execute("""INSERT INTO ai_stock_analysis_allowances
            (account_id,period_start,reset_at,job_id,created_at) VALUES(?,?,?,?,?)""",
            tuple(old[key] for key in ["account_id", "period_start", "reset_at", "job_id", "created_at"]))
        connection.execute("DELETE FROM ai_job_schema WHERE version=?", (storage._STOCK_QUOTA_V2_SCHEMA_VERSION,))
        connection.commit()
    migrated = AIJobRepository(repo.path)
    assert migrated.stock_analysis_quota("account-a")["used"] == 1
    assert migrated.stock_analysis_quota("account-a")["remaining"] == 9
    with migrated._connect() as connection:
        restored = dict(connection.execute("SELECT * FROM ai_stock_analysis_allowances").fetchone())
        assert restored == {**old, "use_number": 1}
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT checksum FROM ai_job_schema WHERE version=?", (storage._STOCK_QUOTA_SCHEMA_VERSION,)).fetchone()[0] == storage._STOCK_QUOTA_SCHEMA_CHECKSUM
        connection.execute("DELETE FROM ai_jobs WHERE job_id=?", (row["job_id"],))
        connection.commit()
    assert AIJobRepository(repo.path).stock_analysis_quota("account-a")["used"] == 1
    for n in range(9):
        assert create(migrated, ticker=f"NEW{n}")[1]
    with pytest.raises(StockAnalysisQuotaExceeded):
        create(migrated, ticker="ELEVENTH")


def test_cache_and_quota_share_one_close_snapshot(repo, monkeypatch):
    first, _ = create(repo)
    complete(repo, first)
    clocks = iter([at("2026-10-12T19:59:59.999999Z"), at("2026-10-12T20:00:00Z")])
    monkeypatch.setattr(storage, "_utcnow", lambda: next(clocks))
    before = repo.stock_analysis_snapshot("account-a", "AMD", **identity())
    after = repo.stock_analysis_snapshot("account-a", "AMD", **identity())
    assert before["quota"]["remaining"] == 9
    assert before["cached_job"]["job_id"] == first["job_id"]
    assert after["quota"]["remaining"] == 10
    assert after["cached_job"] is None and after["shared_job"] is None
    assert before["quota"]["reset_at"] == after["quota"]["period_start"]


def test_running_job_crosses_close_but_late_completed_cache_keeps_creation_period(repo, monkeypatch):
    first, _ = create(repo)
    monkeypatch.setattr(storage, "_utcnow", lambda: at("2026-10-12T20:00:00Z"))
    snapshot = repo.stock_analysis_snapshot("account-b", "AMD", **identity())
    assert snapshot["shared_job"]["job_id"] == first["job_id"]
    assert snapshot["cached_job"] is None
    reused, created = create(repo, account="account-b")
    assert not created and reused["job_id"] == first["job_id"]
    assert repo.stock_analysis_quota("account-b")["remaining"] == 10
    complete(repo, first)
    snapshot = repo.stock_analysis_snapshot("account-b", "AMD", **identity())
    assert snapshot["shared_job"] is None and snapshot["cached_job"] is None
    assert repo.latest_completed("signal_analysis", "AMD")["job_id"] == first["job_id"]
    refreshed, created = create(repo, account="account-b")
    assert created and refreshed["job_id"] != first["job_id"]
    assert repo.stock_analysis_quota("account-b")["remaining"] == 9


def _process_create(database, ticker, gate, output, account="account-a"):
    storage._utcnow = lambda: at("2026-10-09T20:00:00Z")
    gate.wait(10)
    try:
        row, created = create(AIJobRepository(database), ticker=ticker, account=account)
        output.put((row["job_id"], created))
    except StockAnalysisQuotaExceeded:
        output.put(("denied", False))


def test_separate_processes_share_last_account_slot(repo):
    import multiprocessing
    for n in range(9):
        create(repo, ticker=f"SEED{n}")
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    processes = [context.Process(target=_process_create, args=(str(repo.path), ticker, gate, output)) for ticker in ["AMD", "MSFT"]]
    for process in processes:
        process.start()
    gate.set()
    answers = [output.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    assert sum(job == "denied" for job, _ in answers) == 1
    assert sum(created for _, created in answers) == 1
    assert repo.stock_analysis_quota("account-a")["used"] == 10


def test_separate_processes_share_ticker_without_double_consumption(repo):
    import multiprocessing
    repo.ensure_initialized()
    context = multiprocessing.get_context("spawn")
    gate, output = context.Event(), context.Queue()
    processes = [context.Process(target=_process_create, args=(str(repo.path), "AMD", gate, output, account)) for account in ["account-a", "account-b"]]
    for process in processes:
        process.start()
    gate.set()
    answers = [output.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(30)
        assert process.exitcode == 0
    assert len({job for job, _ in answers}) == 1
    assert sum(created for _, created in answers) == 1
    assert sum(repo.stock_analysis_quota(account)["used"] for account in ["account-a", "account-b"]) == 1


def test_v2_checksum_mismatch_stops_initialization_without_losing_usage(repo):
    create(repo)
    with repo._connect() as connection:
        connection.execute("UPDATE ai_job_schema SET checksum='unexpected' WHERE version=?", (storage._STOCK_QUOTA_V2_SCHEMA_VERSION,))
        connection.commit()
    with pytest.raises(RuntimeError, match="ai_stock_quota_schema_checksum_mismatch"):
        AIJobRepository(repo.path).initialize()
    with repo._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_stock_analysis_allowances").fetchone()[0] == 1


def fail_stock(repo, row, code='provider_empty_response', response_id=None):
    with repo._connect() as connection:
        connection.execute("UPDATE ai_jobs SET status='failed',error_code=?,openai_response_id=? WHERE job_id=?", (code, response_id, row['job_id']))
        connection.commit()


def test_failed_receipt_survives_restart_and_plain_post_never_spends_again(repo):
    first, _ = create(repo)
    fail_stock(repo, first)
    restarted = AIJobRepository(repo.path)
    snapshot = restarted.stock_analysis_snapshot('account-a', 'AMD', **identity())
    assert snapshot['current_job']['job_id'] == first['job_id']
    assert snapshot['current_job']['error_code'] == 'provider_empty_response'
    reused, created = create(restarted, force_retry=True)
    assert not created and reused['job_id'] == first['job_id']
    assert restarted.stock_analysis_quota('account-a')['used'] == 1


@pytest.mark.parametrize('code,response_id', [('submission_outcome_unknown', None), ('provider_poll_timeout', 'resp_original'), ('schema_validation_failed', 'resp_original')])
def test_unknown_or_recoverable_receipt_blocks_changed_identity_and_other_accounts(repo, monkeypatch, code, response_id):
    first, _ = create(repo)
    fail_stock(repo, first, code, response_id)
    monkeypatch.setattr(storage, '_utcnow', lambda: at('2026-10-12T20:00:00Z'))
    reused, created = create(repo, account='account-b', model='claude-haiku-5-5', stock_analysis_retry_job_id=first['job_id'])
    assert not created and reused['job_id'] == first['job_id']
    assert repo.stock_analysis_quota('account-b')['used'] == 0


def test_concurrent_explicit_retry_spends_once_and_stale_retry_reuses_new_failure(repo):
    first, _ = create(repo)
    fail_stock(repo, first)
    barrier = threading.Barrier(2)
    def retry(_):
        barrier.wait()
        return create(AIJobRepository(repo.path), stock_analysis_retry_job_id=first['job_id'])
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(retry, range(2)))
    assert sum(created for _, created in answers) == 1
    assert len({row['job_id'] for row, _ in answers}) == 1
    second = answers[0][0]
    assert second['retry_of_job_id'] == first['job_id']
    assert repo.stock_analysis_quota('account-a')['used'] == 2
    fail_stock(repo, second)
    stale, created = create(repo, stock_analysis_retry_job_id=first['job_id'])
    assert not created and stale['job_id'] == second['job_id']
    assert repo.stock_analysis_quota('account-a')['used'] == 2


def test_later_valid_report_supersedes_old_unknown_in_next_period(repo, monkeypatch):
    first, _ = create(repo)
    fail_stock(repo, first, 'submission_outcome_unknown')
    # Reproduce pre-fix history: a second task was admitted despite the unknown.
    monkeypatch.setattr(storage, '_utcnow', lambda: at('2026-10-10T20:00:00Z'))
    second, _ = create(repo, account=None, model='claude-haiku-5-5')
    with repo._connect() as connection:
        connection.execute("UPDATE ai_jobs SET model=? WHERE job_id=?", (runtime.SONNET_MODEL, second['job_id']))
        connection.commit()
    complete(repo, second)
    monkeypatch.setattr(storage, '_utcnow', lambda: at('2026-10-12T20:00:00Z'))
    assert repo.stock_analysis_snapshot('account-a', 'AMD', **identity())['current_job'] is None
    assert create(repo, account='account-b')[1]


def test_changed_market_evidence_does_not_bypass_unknown_receipt(repo):
    first, _ = create(repo)
    fail_stock(repo, first, 'submission_outcome_unknown')
    changed = {**payload(), 'evidence_as_of': '2026-10-10T19:00:00Z'}
    reused, created = create(repo, payload=changed)
    assert not created and reused['job_id'] == first['job_id']
    assert repo.stock_analysis_quota('account-a')['used'] == 1


def test_explicit_retry_keeps_receipt_lineage_when_model_configuration_changes(repo):
    first, _ = create(repo)
    fail_stock(repo, first)
    second, created = create(repo, model='claude-haiku-5-5', stock_analysis_retry_job_id=first['job_id'])
    assert created and second['retry_of_job_id'] == first['job_id']
    assert repo.stock_analysis_quota('account-a')['used'] == 2
