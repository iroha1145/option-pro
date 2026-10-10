"""新闻身份只比对 news_id 与 change_sequence（2026-10-10）。

content_hash 是 64 位十六进制摘要，模型照抄时会漏位、多位或改错一位。生产样本
（tests/fixtures/ai_round3_failures_20261010.json 的 identity_mismatch 组，4 条
Luna 回执）的 news_id 和 change_sequence 都对，只有摘要抄错，因此被判
news_identity_mismatch。现在结果一律改用载荷里的摘要；提示词保留「原样复制」
的要求但不再校验，任务身份不变。历史上因此失败的行可以用找回工具按现行规则
重验落库。
"""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import RECOVERABLE_FAILURE_CODES, AIJobRepository
from app.services.catalysts.local_intelligence import _news_result_identity_matches
from app.tools import recover_ai_schema_results as recovery
from test_ai_analysis_fixes_20261010 import _pending_luna_job
from test_ai_jobs_zh_contract import _news_payload, _news_result
from test_luna_news_web_fallback import settings as luna_settings

SAMPLES = json.loads(
    (Path(__file__).parent / "fixtures" / "ai_round3_failures_20261010.json").read_text(encoding="utf-8")
)["identity_mismatch"]


@pytest.fixture(autouse=True)
def owner_context():
    from app.access import request_owner_access_context

    with request_owner_access_context(True):
        yield


def _mistyped(value: str, how: str) -> str:
    if how == "drop_two":
        return value[:-2]
    if how == "add_one":
        return value + "0"
    if how == "change_one":
        return ("1" if value[0] != "1" else "2") + value[1:]
    return "another-revision"


@pytest.mark.parametrize("how", ["drop_two", "add_one", "change_one", "unrelated"])
def test_a_mistyped_hash_is_replaced_by_the_payload_value(how):
    payload = _news_payload()
    result = _news_result()
    result["content_hash"] = _mistyped(payload["content_hash"], how)
    checked = validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)
    assert checked["content_hash"] == payload["content_hash"]
    # The published analysis binds to the exact local news revision.
    assert _news_result_identity_matches(checked, payload)


@pytest.mark.parametrize("field", ["news_id", "change_sequence"])
@pytest.mark.parametrize("hash_matches", [True, False])
def test_news_id_or_change_sequence_mismatch_is_still_rejected(field, hash_matches):
    payload = _news_payload()
    result = _news_result()
    result[field] += 1
    if not hash_matches:
        result["content_hash"] = _mistyped(payload["content_hash"], "drop_two")
    with pytest.raises(ValueError, match="news_identity_mismatch"):
        validate_result("news_impact", json.dumps(result, ensure_ascii=False), payload)


def test_the_rule_moves_neither_the_prompt_nor_the_task_identity():
    # 选择「保留提示词要求但不校验」：改提示词会移动任务身份。
    request = runtime.build_runtime_request("news_impact", {}, model=runtime.LUNA_MODEL)
    assert "news_id、change_sequence和content_hash必须原样复制" in request.instructions
    assert runtime.schema_identity("news_impact", model=runtime.LUNA_MODEL) == runtime.LUNA_WEB_NEWS_IDENTITY
    assert runtime.schema_identity("news_impact", model=runtime.OFFICIAL_OPENAI_MODEL) == runtime.TERRA_NEWS_IDENTITY


def _submitted_luna_job(repo: AIJobRepository, sample: dict) -> str:
    job_id = _pending_luna_job(repo, sample["payload"], runtime.LUNA_ALWAYS_WEB_NEWS_IDENTITY)
    assert repo.claim_due("owner", 60)["job_id"] == job_id
    assert repo.mark_submission_started(job_id, "owner", max_concurrency=4) == "started"
    repo.link_background_response(job_id, "owner", sample["receipt"]["id"])
    repo.record_openai_result(job_id, "owner", sample["receipt"])
    return job_id


@pytest.mark.parametrize("sample", SAMPLES, ids=lambda sample: sample["job_id"])
def test_a_production_receipt_with_a_mistyped_hash_is_stored_with_the_payload_hash(tmp_path, sample):
    repo = AIJobRepository(tmp_path / "news.db")
    job_id = _submitted_luna_job(repo, sample)
    output = json.loads(sample["receipt"]["output_text"])
    assert output["content_hash"] != sample["payload"]["content_hash"]

    asyncio.run(worker._finish_claude_receipt(repo, repo.get_job(job_id), "owner", deepcopy(sample["receipt"])))

    row = repo.get_job(job_id)
    assert row["status"] == "completed" and row["error_code"] is None
    stored = json.loads(row["result_json"])
    assert stored["content_hash"] == sample["payload"]["content_hash"]
    assert (stored["news_id"], stored["change_sequence"]) == (output["news_id"], output["change_sequence"])
    # The paid receipt keeps what the model actually wrote.
    assert repo.get_provider_result(job_id)["output_text"] == sample["receipt"]["output_text"]


def test_the_recovery_tool_restores_rows_that_failed_on_the_hash(tmp_path, monkeypatch, capsys):
    assert "news_identity_mismatch" in RECOVERABLE_FAILURE_CODES
    repo = AIJobRepository(tmp_path / "news.db")
    job_ids = []
    for sample in SAMPLES:
        job_id = _submitted_luna_job(repo, sample)
        repo.fail(job_id, "owner", "news_identity_mismatch", usage=sample["receipt"]["usage"],
                  detail=sample["production_error"])
        job_ids.append(job_id)
    before = {job_id: repo.get_job(job_id) for job_id in job_ids}
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert repo.recoverable_receipt_failures(since=since) == job_ids

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must reuse the stored receipt")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(runtime, "submit_background", forbidden)
    monkeypatch.setattr(recovery, "get_settings", lambda: luna_settings(repo.path))

    assert recovery.main(["--failed-since", since.isoformat(), "--job-type", "news_impact"]) == 0
    assert [item["status"] for item in json.loads(capsys.readouterr().out)] == ["validated"] * len(SAMPLES)
    assert all(repo.get_job(job_id)["status"] == "failed" for job_id in job_ids)

    assert recovery.main(["--failed-since", since.isoformat(), "--apply"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert [item["status"] for item in applied] == ["recovered"] * len(SAMPLES)
    for job_id, sample in zip(job_ids, SAMPLES):
        row = repo.get_job(job_id)
        assert row["status"] == "completed" and row["error_code"] is None and row["error_detail"] is None
        assert json.loads(row["result_json"])["content_hash"] == sample["payload"]["content_hash"]
        for key in ("budget_charge_microusd", "usage_output_tokens", "provider_result_json", "openai_response_id"):
            assert row[key] == before[job_id][key]
    # Nothing is left to recover, and a second run changes nothing.
    assert recovery.main(["--failed-since", since.isoformat(), "--apply"]) == 1
    assert json.loads(capsys.readouterr().out) == []


def test_a_recovered_news_job_publishes_its_analysis_to_the_feed(tmp_path, monkeypatch):
    from anthropic.types import Message, Usage

    from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
    from test_catalyst_local_intelligence import _apply_news, _news_change, _stack
    from test_catalyst_local_intelligence import _news_result as engine_news_result

    etl, repo, initial = _stack(tmp_path)
    service = LocalCatalystIntelligence(initial.db_path, repo, mode="manual", canonical_tickers=("NVDA",))
    service.initialize()
    now = datetime.now(timezone.utc)
    _apply_news(etl, [_news_change(1, 900, available_at=now - timedelta(minutes=2))], as_of=now)
    service.reconcile()
    job = service.request_analysis(900, force=False)
    row = repo.get_job(job["job_id"])
    payload = json.loads(row["payload_json"])
    raw = engine_news_result(news_id=900, change_sequence=1, content_hash=payload["content_hash"][:-2])

    # The paid answer failed the old identity check and was kept as a receipt.
    assert repo.claim_due("owner", 60)["job_id"] == job["job_id"]
    assert repo.mark_submission_started(job["job_id"], "owner", daily_limit=4) == "started"
    repo.record_provider_result(job["job_id"], "owner", runtime.claude_receipt(Message(
        id="msg_news_hash", type="message", role="assistant", model=row["model"],
        stop_reason="end_turn", stop_sequence=None,
        content=[{"type": "text", "text": json.dumps(raw, ensure_ascii=False)}],
        usage=Usage(input_tokens=100, output_tokens=100,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )))
    repo.fail(job["job_id"], "owner", "news_identity_mismatch")
    service.reconcile()
    assert service.feed(as_of=datetime.now(timezone.utc), limit=10)["items"][0]["analysis"] is None

    monkeypatch.setattr(recovery, "get_settings", lambda: luna_settings(repo.path))
    assert asyncio.run(recovery.recover([job["job_id"]], apply=True))[0]["status"] == "recovered"
    service.reconcile()
    analysis = service.feed(as_of=datetime.now(timezone.utc), limit=10)["items"][0]["analysis"]
    assert analysis is not None
    assert json.loads(repo.get_job(job["job_id"])["result_json"])["content_hash"] == payload["content_hash"]
