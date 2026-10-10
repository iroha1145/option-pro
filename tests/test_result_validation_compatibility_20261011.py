"""Local v4 -> v5 compatibility; no provider or production writes."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import runtime
from app.services.catalysts import local_intelligence as local_module
from test_catalyst_local_intelligence import _apply_news, _finish_job, _news_change, _news_result, _stack
from test_hotspots_read_cache_20261010 import as_visitor, production_cycle
from test_personal_catalyst_service import _service


V4_NEWS = ("news_impact_zh_cn_v6", "68b3095ba0f47e559a5a7edd3daf6b091350546961ce398368684143bbb76a4a")
V4_FOCUS = ("market_focus_verified_zh_cn_v2", "aae4f97495d407b14dfbb685ca2b5d8cc82c70f8bfb4eae8938cec719d8e696d")


@pytest.fixture(autouse=True)
def owner_context():
    with request_owner_access_context(True):
        yield


@pytest.mark.parametrize(("task", "model", "identity"), [
    ("news_impact", runtime.OFFICIAL_CLAUDE_MODEL, V4_NEWS),
    ("market_focus", runtime.SONNET_MODEL, V4_FOCUS),
])
def test_exact_v4_identity_survives_validation_change_but_not_policy_change(task, model, identity, monkeypatch):
    prompt = runtime.PROMPT_VERSIONS[task]
    assert runtime.schema_identity_current(task, prompt, *identity, model=model)
    assert not runtime.schema_identity_current(task, prompt, identity[0], "0" * 64, model=model)
    monkeypatch.setitem(runtime.CLAUDE_TASK_MAX_OUTPUT_TOKENS, task, 16384)
    assert not runtime.schema_identity_current(task, prompt, *identity, model=model)


@pytest.mark.parametrize("old_outcome", ["accepted", "rejected"])
def test_v4_news_publication_or_rejection_is_reaudited_without_another_paid_job(tmp_path, old_outcome):
    etl, ai, intelligence = _stack(tmp_path)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    _apply_news(etl, [_news_change(1, 9101, available_at=now - timedelta(minutes=10))], as_of=now - timedelta(minutes=9))
    intelligence.reconcile()
    job = intelligence.request_analysis(9101, force=False)
    result = _news_result(news_id=9101, change_sequence=1, content_hash="hash-9101-1")
    result["title_zh"] = "苹果iPad mini销量改善，ROE仍保持稳定。"
    _finish_job(ai, job["job_id"], result)
    before = ai.get_job(job["job_id"])
    raw = local_module._json(result)
    digest = hashlib.sha256(raw.encode()).hexdigest()
    old_contract = local_module.NEWS_RESULT_CONTRACT_ID.replace("simplified-chinese-v5", "simplified-chinese-v4")
    with sqlite3.connect(ai.path) as conn:
        conn.execute("UPDATE ai_jobs SET schema_version=?,schema_sha256=? WHERE job_id=?", (*V4_NEWS, job["job_id"]))
    with sqlite3.connect(intelligence.db_path) as conn:
        conn.execute("""INSERT INTO catalyst_local_analysis_result_audit
            (job_id,contract_id,result_sha256,outcome,reason,result_json,result_available_at,verified_at,observed_at)
            VALUES(?,?,?,?,?,?,?,?,?)""", (job["job_id"], old_contract, digest, old_outcome, None, raw, before["completed_at"], None, before["completed_at"]))
    intelligence.reconcile()
    detail = intelligence.news(9101, as_of=now + timedelta(minutes=1))
    assert detail["item"]["analysis"]["title_zh"] == result["title_zh"]
    reused = intelligence.request_analysis(9101, force=False)
    assert reused["job_id"] == job["job_id"]
    assert reused["cached"] is True
    with sqlite3.connect(ai.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1
    with sqlite3.connect(intelligence.db_path) as conn:
        assert conn.execute("SELECT outcome FROM catalyst_local_analysis_result_audit WHERE job_id=? AND contract_id=?", (job["job_id"], local_module.NEWS_RESULT_CONTRACT_ID)).fetchone()[0] == "accepted"
    after = ai.get_job(job["job_id"])
    for name in ("result_json", "provider_result_json", "usage_total_tokens", "budget_charge_microusd"):
        assert after[name] == before[name]


def test_v4_focus_publication_remains_visible_without_paid_resubmission(production_cycle):
    ai, intelligence, cycle, clock = production_cycle
    before = ai.get_job(cycle["job_id"])
    with sqlite3.connect(ai.path) as conn:
        conn.execute("UPDATE ai_jobs SET schema_version=?,schema_sha256=? WHERE job_id=?", (*V4_FOCUS, cycle["job_id"]))
        count = conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0]
    with sqlite3.connect(intelligence.db_path) as conn:
        conn.execute("UPDATE catalyst_local_focus_result_audit SET contract_id=REPLACE(contract_id,'simplified-chinese-v5','simplified-chinese-v4') WHERE job_id=? AND contract_id=?", (cycle["job_id"], local_module.FOCUS_RESULT_CONTRACT_ID))
    intelligence.reconcile()
    page = as_visitor(_service("manual", engine=intelligence, repository=ai).hotspots, limit=8, now=clock[0])
    assert page["status"] == "active"
    assert len(page["items"]) == 8
    with sqlite3.connect(ai.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == count
    after = ai.get_job(cycle["job_id"])
    for name in ("result_json", "provider_result_json", "usage_total_tokens", "budget_charge_microusd"):
        assert after[name] == before[name]


def test_pending_v4_news_keeps_its_job_and_submits_once(tmp_path, monkeypatch):
    from app.services.ai_jobs.repository import AIJobRepository
    from test_ai_analysis_fixes_20261010 import _pending_luna_job, _submit_pending
    from test_luna_cost_20261010 import luna_payload

    repository = AIJobRepository(tmp_path / "jobs.db")
    old_identity = ("news_impact_zh_cn_v6", "135ee2bf6c3460581e209ae15caf8c6a1479362d0290d3e86bfff1a1a84fa1fc")
    job_id = _pending_luna_job(repository, luna_payload(), old_identity)
    submitted = _submit_pending(repository, monkeypatch)
    assert len(submitted) == 1
    row = repository.get_job(job_id)
    assert row["status"] == "queued"
    assert row["openai_response_id"] == "resp_policy_transition"
    assert row["schema_sha256"] == old_identity[1]
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1
