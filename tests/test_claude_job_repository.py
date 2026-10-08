from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import timedelta

import pytest

from app.services.ai_jobs import repository as storage, runtime
from app.services.ai_jobs.repository import AIJobRepository

CLAUDE = "claude-haiku-5-5"
LEGACY = "gpt-5.6-terra"


@pytest.fixture
def accounting(monkeypatch):
    calls = []

    def reserve(job_type, *, model=None):
        calls.append(("reserve", model))
        return 900 if model == CLAUDE else 1800

    def tokens(job_type, *, model=None):
        calls.append(("tokens", model))
        return 1000 if model == CLAUDE else 2000

    def settle(job_type, usage, *, fallback_microusd, model=None):
        calls.append(("settle", model))
        return 90 if model == CLAUDE else 180

    monkeypatch.setattr(runtime, "budget_reservation_microusd", reserve)
    monkeypatch.setattr(runtime, "token_reservation", tokens)
    monkeypatch.setattr(runtime, "settled_usage_cost_microusd", settle)
    return calls


def job(repo, model=CLAUDE):
    version, digest = runtime.schema_identity("earnings_impact")
    result = repo.create_job(
        job_type="earnings_impact", payload={"ticker": "AAPL", "name": "Apple"},
        model=model, reasoning="xhigh" if model == CLAUDE else "max",
        execution_mode="background", prompt_version="repository-test-v1",
        schema_version=version, schema_sha256=digest, max_queued=20,
    )
    # create_job returns the public job and an idempotency indicator.
    return result[0] if isinstance(result, tuple) else result


def started(tmp_path, accounting, model=CLAUDE):
    repo = AIJobRepository(tmp_path / "jobs.sqlite3")
    created = job(repo, model)
    claimed = repo.claim_due("owner", 600)
    assert claimed["job_id"] == created["job_id"]
    assert repo.mark_submission_started(
        claimed["job_id"], "owner", daily_limit=100, daily_budget_usd=100,
        daily_token_limit=1_000_000, cooldown_seconds=0,
    ) == "started"
    return repo, claimed["job_id"]


def receipt():
    return {
        "provider": "anthropic", "model": CLAUDE, "id": "msg_test",
        "output_text": '{"raw":"最终结果"}', "stop_reason": "end_turn",
        "terminal_error": None, "evidence_sources": [],
        "usage": {
            "input_tokens": 160, "cached_input_tokens": 30,
            "cache_creation_input_tokens": 50,
            "cache_creation_5m_input_tokens": 40,
            "cache_creation_1h_input_tokens": 10,
            "output_tokens": 20, "reasoning_tokens": None, "total_tokens": 180,
        },
    }


def test_v4_migration_preserves_registry_and_history(tmp_path, accounting):
    repo = AIJobRepository(tmp_path / "jobs.sqlite3")
    created = job(repo, LEGACY)
    original = repo.get_job(created["job_id"])
    checksum = hashlib.sha256((storage._SCHEMA_REGISTRY_SQL + storage._AI_JOBS_V4_TABLE_SQL + storage._AI_JOBS_INDEX_SQL).encode()).hexdigest()
    with sqlite3.connect(repo.path) as conn:
        for field in storage._CLAUDE_COLUMNS:
            conn.execute(f"ALTER TABLE ai_jobs DROP COLUMN {field}")
        conn.execute("DELETE FROM ai_job_schema WHERE version='ai-jobs-v5'")
        conn.execute("INSERT INTO ai_job_schema VALUES('ai-jobs-v4',?,?)", (checksum, storage._iso()))
    repo.initialize()
    repo.initialize()
    migrated = repo.get_job(created["job_id"])
    assert {k: v for k, v in migrated.items() if k not in storage._CLAUDE_COLUMNS} == {
        k: v for k, v in original.items() if k not in storage._CLAUDE_COLUMNS
    }
    with sqlite3.connect(repo.path) as conn:
        assert conn.execute("SELECT checksum FROM ai_job_schema WHERE version='ai-jobs-v4'").fetchone()[0] == checksum
        assert conn.execute("SELECT COUNT(*) FROM ai_job_schema WHERE version='ai-jobs-v5'").fetchone()[0] == 1


@pytest.mark.parametrize("saved_stop_reason", ["end_turn", "tool_use"])
def test_receipt_is_durable_private_idempotent_and_settles_once(tmp_path, accounting, saved_stop_reason):
    repo, ident = started(tmp_path, accounting)
    saved = {**receipt(), "stop_reason": saved_stop_reason}
    repo.link_anthropic_message(ident, "owner", "msg_test")
    repo.record_provider_result(ident, "owner", saved)
    repo.record_provider_result(ident, "owner", saved)
    reopened = AIJobRepository(repo.path)
    assert reopened.get_provider_result(ident) == saved
    row = reopened.get_job(ident)
    assert row["openai_response_id"] is None
    assert row["anthropic_message_id"] == "msg_test"
    assert row["usage_total_tokens"] == 180
    assert row["budget_charge_microusd"] == 90
    assert accounting.count(("settle", CLAUDE)) == 1
    public = repo.public(row)
    assert "provider_result_json" not in public
    assert "anthropic_message_id" not in public
    assert public["usage"]["cache_creation_input_tokens"] == 50
    assert public["usage"]["input_tokens"] == 160


@pytest.mark.parametrize("change", [
    {"id": "msg_other"}, {"model": LEGACY}, {"output_text": "different"},
    {"stop_reason": "max_tokens"}, {"thinking": "private"},
])
def test_receipt_cannot_replace_identity_or_terminal_result(tmp_path, accounting, change):
    repo, ident = started(tmp_path, accounting)
    repo.record_provider_result(ident, "owner", receipt())
    altered = {**receipt(), **change}
    with pytest.raises((ValueError, RuntimeError)):
        repo.record_provider_result(ident, "owner", altered)
    assert repo.get_provider_result(ident) == receipt()


@pytest.mark.parametrize("failure", ["owner", "expired", "model"])
def test_lost_lease_or_wrong_model_cannot_write_receipt(tmp_path, accounting, failure):
    repo, ident = started(tmp_path, accounting, LEGACY if failure == "model" else CLAUDE)
    owner = "wrong" if failure == "owner" else "owner"
    if failure == "expired":
        with sqlite3.connect(repo.path) as conn:
            conn.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE job_id=?", (storage._iso(storage._utcnow() - timedelta(seconds=1)), ident))
    with pytest.raises(RuntimeError):
        repo.link_anthropic_message(ident, owner, "msg_test")
    with pytest.raises(RuntimeError):
        repo.record_provider_result(ident, owner, receipt())
    assert repo.get_provider_result(ident) is None


@pytest.mark.parametrize("terminal", ["fail", "cancel"])
def test_unconfirmed_claude_failure_retains_budget_and_tokens(tmp_path, accounting, terminal):
    repo, ident = started(tmp_path, accounting)
    repo.link_anthropic_message(ident, "owner", "msg_test")
    if terminal == "fail":
        repo.fail(ident, "owner", "submission_outcome_unknown")
    else:
        repo.mark_cancelled(ident, "owner")
    row = repo.get_job(ident)
    assert row["budget_charge_microusd"] == 900
    assert row["usage_total_tokens"] is None
    assert storage._daily_tokens_used([row]) == 1000
    if terminal == "fail":
        with repo._connect() as conn:
            assert repo._lane_occupant(conn, lane="manual", now_dt=storage._utcnow() + timedelta(seconds=901), unknown_submission_hold_seconds=86400) is None


@pytest.mark.parametrize("terminal", ["fail", "cancel", "complete"])
def test_terminal_publish_preserves_receipt_usage(tmp_path, accounting, terminal):
    repo, ident = started(tmp_path, accounting)
    repo.record_provider_result(ident, "owner", receipt())
    if terminal == "fail":
        repo.fail(ident, "owner", "schema_validation_failed")
    elif terminal == "cancel":
        repo.mark_cancelled(ident, "owner")
    else:
        repo.complete(ident, "owner", {"saved": True}, {})
    row = repo.get_job(ident)
    assert row["usage_cache_creation_input_tokens"] == 50
    assert row["usage_cache_creation_5m_input_tokens"] == 40
    assert row["usage_cache_creation_1h_input_tokens"] == 10
    assert row["usage_total_tokens"] == 180
    assert row["budget_charge_microusd"] == 90


def test_legacy_accounting_uses_saved_model(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting, LEGACY)
    repo.complete(ident, "owner", {"saved": True}, {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})
    assert repo.get_job(ident)["budget_charge_microusd"] == 180
    assert ("reserve", LEGACY) in accounting
    assert ("tokens", LEGACY) in accounting
    assert ("settle", LEGACY) in accounting


def test_invalid_usage_and_unbounded_receipt_are_rejected(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["usage"]["total_tokens"] += 1
    with pytest.raises(ValueError, match="usage_invalid"):
        repo.record_provider_result(ident, "owner", value)
    value = receipt()
    value["output_text"] = "x" * (2 * storage._MAX_RESULT_JSON_BYTES)
    with pytest.raises(ValueError, match="receipt_too_large"):
        repo.record_provider_result(ident, "owner", value)


def test_saved_receipt_can_be_reclaimed_after_worker_restart(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting)
    repo.record_provider_result(ident, "owner", receipt())
    with sqlite3.connect(repo.path) as conn:
        conn.execute("UPDATE ai_jobs SET lease_expires_at=? WHERE job_id=?", (storage._iso(storage._utcnow() - timedelta(seconds=1)), ident))
    reopened = AIJobRepository(repo.path)
    recovered = reopened.claim_due("replacement", 600)
    assert recovered["job_id"] == ident
    assert reopened.get_provider_result(ident) == receipt()
    reopened.complete(ident, "replacement", {"saved": True}, {})
    assert reopened.get_job(ident)["attempt_count"] == 1


@pytest.mark.parametrize("terminal", ["fail", "cancel", "backfill"])
def test_legacy_terminal_and_backfill_accounting_use_saved_model(tmp_path, accounting, terminal):
    repo, ident = started(tmp_path, accounting, LEGACY)
    usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
    if terminal == "fail":
        repo.fail(ident, "owner", "provider_failed", usage=usage)
    elif terminal == "cancel":
        repo.mark_cancelled(ident, "owner", usage=usage)
    else:
        repo.complete(ident, "owner", {"saved": True}, usage)
        with sqlite3.connect(repo.path) as conn:
            conn.execute("UPDATE ai_jobs SET budget_charge_microusd=0 WHERE job_id=?", (ident,))
        accounting.clear()
        repo.initialize()
        assert ("reserve", LEGACY) in accounting
    assert repo.get_job(ident)["budget_charge_microusd"] == 180
    assert ("settle", LEGACY) in accounting


def test_failed_receipt_transaction_does_not_publish_partial_result(tmp_path, accounting, monkeypatch):
    repo, ident = started(tmp_path, accounting)

    def unavailable(*args, **kwargs):
        raise RuntimeError("accounting_unavailable")

    monkeypatch.setattr(runtime, "settled_usage_cost_microusd", unavailable)
    with pytest.raises(RuntimeError, match="accounting_unavailable"):
        repo.record_provider_result(ident, "owner", receipt())
    row = repo.get_job(ident)
    assert row["provider_result_json"] is None
    assert row["usage_total_tokens"] is None
    assert row["budget_charge_microusd"] == 900


def test_optional_cache_usage_remains_unknown_in_receipt(tmp_path, accounting, monkeypatch):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["usage"].update({
        "cached_input_tokens": None, "cache_creation_input_tokens": None,
        "cache_creation_5m_input_tokens": None, "cache_creation_1h_input_tokens": None,
    })

    def conservative(job_type, usage, *, fallback_microusd, model=None):
        assert usage["cached_input_tokens"] is None
        return fallback_microusd

    monkeypatch.setattr(runtime, "settled_usage_cost_microusd", conservative)
    repo.record_provider_result(ident, "owner", value)
    repo.complete(ident, "owner", {"saved": True}, {})
    row = repo.get_job(ident)
    assert row["usage_cached_input_tokens"] is None
    assert row["budget_charge_microusd"] == 900
    assert repo.get_provider_result(ident)["usage"]["cached_input_tokens"] is None


def test_budget_snapshot_minimum_uses_requested_model(tmp_path, accounting, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.sqlite3")
    models = []

    def minimum(*, model=None):
        models.append(model)
        return 102_400 if model == LEGACY else 135_168

    monkeypatch.setattr(runtime, "minimum_token_reservation", minimum)
    legacy = repo.budget_snapshot(daily_limit=0, daily_budget_usd=0, daily_token_limit=102_400, model=LEGACY)
    current = repo.budget_snapshot(daily_limit=0, daily_budget_usd=0, daily_token_limit=102_400)
    assert legacy["token_budget_available"] is True
    assert current["token_budget_available"] is False
    assert models == [LEGACY, None]


def test_tool_receipt_publishes_bounded_sources_and_usage_without_raw_content(tmp_path, accounting, monkeypatch):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["evidence_sources"] = [
        {"title": "Company filing", "url": "https://www.sec.gov/Archives/report", "type": "web_search"},
        {"title": "News", "url": "https://www.reuters.com/markets/", "type": "web_fetch"},
    ]
    value["usage"].update(web_search_requests=1, web_fetch_requests=1, code_execution_requests=None)

    def settle(job_type, usage, *, fallback_microusd, model=None):
        assert usage["web_search_requests"] == 1
        assert usage["web_fetch_requests"] == 1
        assert usage["code_execution_requests"] is None
        return 10_090  # Actual tool fees may exceed the admission reservation.

    monkeypatch.setattr(runtime, "settled_usage_cost_microusd", settle)
    repo.record_provider_result(ident, "owner", value)
    repo.fail(ident, "owner", "schema_validation_failed")
    row = repo.get_job(ident)
    assert row["budget_charge_microusd"] == 10_090
    public = repo.public(row)
    assert public["evidence_sources"] == value["evidence_sources"]
    assert public["usage"]["web_search_requests"] == 1
    assert public["usage"]["code_execution_requests"] is None
    serialized = json.dumps(public)
    for private in ("provider_result_json", "output_text", "msg_test", "thinking"):
        assert private not in serialized


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "javascript:alert(1)", "https://user:secret@example.com/",
    "http://127.0.0.1/", "http://10.0.0.1/", "http://[::1]/", "http://169.254.169.254/",
    "http://localhost/", "https://api.internal/", "http://127.1/",
    "https://example.com:99999/", "https://example.com/\nsecret", "https://example.com/" + "x" * 2048,
])
def test_receipt_rejects_nonpublic_or_malformed_sources(tmp_path, accounting, url):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["evidence_sources"] = [{"title": "Source", "url": url, "type": "web_search"}]
    with pytest.raises(ValueError, match="sources_invalid"):
        repo.record_provider_result(ident, "owner", value)
    assert repo.get_provider_result(ident) is None


@pytest.mark.parametrize("change", [
    {"title": "x" * 513}, {"title": "hidden\ncontent"}, {"type": "code_execution"},
    {"type": []}, {"body": "fetched document"},
])
def test_receipt_rejects_source_fields_outside_contract(tmp_path, accounting, change):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["evidence_sources"] = [{"title": "Source", "url": "https://www.sec.gov/", "type": "web_search", **change}]
    with pytest.raises(ValueError, match="sources_invalid"):
        repo.record_provider_result(ident, "owner", value)


@pytest.mark.parametrize("count", [-1, 1.5, True, "1"])
def test_receipt_rejects_invalid_server_tool_counts(tmp_path, accounting, count):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    value["usage"]["web_search_requests"] = count
    with pytest.raises(ValueError, match="usage_invalid"):
        repo.record_provider_result(ident, "owner", value)


def test_old_receipt_missing_evidence_has_empty_default_and_remains_idempotent(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting)
    old = receipt()
    del old["evidence_sources"]
    repo.record_provider_result(ident, "owner", old)
    # Simulate a receipt durably written by the pre-evidence implementation.
    with sqlite3.connect(repo.path) as conn:
        conn.execute("UPDATE ai_jobs SET provider_result_json=? WHERE job_id=?", (json.dumps(old), ident))
    repo.record_provider_result(ident, "owner", receipt())
    assert repo.get_provider_result(ident)["evidence_sources"] == []
    assert repo.public(repo.get_job(ident))["evidence_sources"] == []


def test_receipt_evidence_is_immutable_and_limited_to_ten(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting)
    value = receipt()
    source = {"title": "Source", "url": "https://www.sec.gov/", "type": "web_search"}
    value["evidence_sources"] = [source] * 11
    with pytest.raises(ValueError, match="sources_invalid"):
        repo.record_provider_result(ident, "owner", value)
    value["evidence_sources"] = [source]
    repo.record_provider_result(ident, "owner", value)
    with pytest.raises(RuntimeError, match="result_conflict"):
        repo.record_provider_result(ident, "owner", receipt())


def test_public_hides_tampered_evidence_receipt(tmp_path, accounting):
    repo, ident = started(tmp_path, accounting)
    repo.record_provider_result(ident, "owner", receipt())
    tampered = receipt()
    tampered["evidence_sources"] = [{"title": "Private", "url": "http://127.0.0.1/", "type": "web_fetch"}]
    row = repo.get_job(ident)
    row["provider_result_json"] = json.dumps(tampered)
    public = repo.public(row)
    assert public["evidence_sources"] == []
    assert "127.0.0.1" not in json.dumps(public)
