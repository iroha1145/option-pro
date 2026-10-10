"""热点核验的来源绑定放宽（2026-10-10）。

生产周期 aij_af48b5a12957427c9f0cad78c5a533b9（Sonnet 核实，schema
market_focus_verified_zh_cn_v2）整轮报 market_focus_verification_evidence_ambiguous：
33 个没有调用编号的引用里，32 个按网址唯一命中，1 个是模型把 fool.com 的长网址截断了，
回执里找不到同一网址。夹具按这轮的形状合成（20 个事件，其中 11 个已证实、9 个无法核实；
114 次成功调用，113 次搜索、1 次抓取），网址除截断的那条外都是合成的。

新规则：引用先精确匹配，同一网址多次调用取回执里的第一次，精确匹配不到时找「回执网址以
引用网址为前缀」的唯一一条；仍对不上的引用丢弃；判定失去所需关系的引用后降为
unverifiable。输入绑定（每个输入事件版本恰好一条核验）仍然严格。只改校验代码，
提示词、结构和任务身份都不变。
"""

from __future__ import annotations

import asyncio
import copy
import json

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.models import (
    VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    validate_market_focus_evidence,
    validate_result,
)
from app.services.ai_jobs.repository import AIJobRepository
from app.tools import recover_ai_schema_results as recovery
from test_luna_news_web_fallback import settings as recovery_settings
from test_verified_focus_contract import verified_payload_result

FOOL_CITED = (
    "https://www.fool.com/coverage/stock-market-today/2026/10/09/"
    "stock-market-today-oct-9-at-and-t-slides-on-spacex"
)
FOOL_RECEIPT = FOOL_CITED + "-spectrum-deal-threat/?source=iedfolrf0000001"


def receipt(call_id: str, url: str, tool: str = "web_search") -> dict:
    return {"tool_use_id": call_id, "tool_name": tool, "status": "success", "url": url,
            "title": "Published source", "content_sha256": "b" * 64}


def cite(url: str, relation: str = "supports", call_id: str | None = None) -> dict:
    return {"tool_use_id": call_id, "url": url, "relation": relation}


def incident() -> tuple[dict, dict, list[dict]]:
    """The failed cycle's shape: 11 supported events cite 33 sources without call ids."""
    payload, result, _ = verified_payload_result()
    events = [{"event_group_id": f"evt_{index:02d}", "event_group_version": 1, "title_zh": "候选事件"}
              for index in range(20)]
    payload.update(events=events, allowed_event_group_ids=[event["event_group_id"] for event in events])
    evidence = [receipt(f"srv_search_{index:03d}", f"https://www.reuters.com/markets/story-{index:03d}")
                for index in range(112)]
    evidence.append(receipt("srv_search_fool", FOOL_RECEIPT))
    evidence.append(receipt("srv_fetch_000", "https://www.sec.gov/Archives/edgar/data/1/filing.htm", "web_fetch"))
    verifications = []
    for index, event in enumerate(events):
        supported = index < 11
        refs = [cite(f"https://www.reuters.com/markets/story-{index * 3 + offset:03d}") for offset in range(3)] if supported else []
        verifications.append({
            "event_group_id": event["event_group_id"], "event_group_version": 1,
            "verdict": "supported" if supported else "unverifiable", "evidence_refs": refs,
            "title_zh": "事件已获来源证实" if supported else "事件未能核实",
            "summary_zh": "公司发布新产品。已证实：来源公布了发布信息。推断：影响仍需观察。" if supported
            else "未证实：本次没有取得直接来源。",
            "affected_sectors": ["半导体"],
        })
    # The one citation the model truncated.
    verifications[5]["evidence_refs"][2] = cite(FOOL_CITED)
    result.update(dominant_events=[], focus_ticker_assessments=[], event_verifications=verifications)
    return payload, result, evidence


def checked(payload: dict, result: dict) -> dict:
    return validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload)


def bind(payload: dict, result: dict, evidence: list[dict]) -> dict:
    data = checked(payload, result)
    validate_market_focus_evidence(data, payload, evidence)
    return data


def test_the_failed_production_shape_now_binds_every_citation():
    payload, result, evidence = incident()
    assert len(evidence) == 114
    assert sum(len(entry["evidence_refs"]) for entry in result["event_verifications"]) == 33
    data = bind(payload, result, evidence)
    verdicts = [entry["verdict"] for entry in data["event_verifications"]]
    assert verdicts == ["supported"] * 11 + ["unverifiable"] * 9
    refs = [ref for entry in data["event_verifications"] for ref in entry["evidence_refs"]]
    assert len(refs) == 33 and all(ref["tool_use_id"] for ref in refs)
    assert data["event_verifications"][5]["evidence_refs"][2] == {
        "tool_use_id": "srv_search_fool", "url": FOOL_RECEIPT, "relation": "supports",
    }


def test_binding_a_bound_result_again_changes_nothing():
    # Publication re-binds the stored result and the paid receipt and compares them.
    payload, result, evidence = incident()
    result["event_verifications"][0]["evidence_refs"] = [cite("https://www.reuters.com/markets/missing")]
    once = bind(payload, result, evidence)
    twice = copy.deepcopy(once)
    validate_market_focus_evidence(twice, payload, evidence)
    assert twice == once
    assert once["event_verifications"][0]["verdict"] == "unverifiable"


def test_a_url_called_twice_binds_to_the_first_call():
    payload, result, evidence = incident()
    evidence.append(receipt("srv_search_again", "https://www.reuters.com/markets/story-000"))
    data = bind(payload, result, evidence)
    assert data["event_verifications"][0]["evidence_refs"][0]["tool_use_id"] == "srv_search_000"


def test_a_prefix_matching_several_receipts_is_dropped():
    payload, result, evidence = incident()
    evidence.append(receipt("srv_search_fool_2", FOOL_CITED + "-and-tmobile/"))
    data = bind(payload, result, evidence)
    refs = data["event_verifications"][5]["evidence_refs"]
    assert [ref["url"] for ref in refs] == [
        "https://www.reuters.com/markets/story-015", "https://www.reuters.com/markets/story-016",
    ]
    # Two other supporting sources remain, so the event stays supported.
    assert data["event_verifications"][5]["verdict"] == "supported"


@pytest.mark.parametrize("cited", [
    "https://www.fool.com",
    "https://www.fool.com/",
    "https://fool.com/coverage/stock-market-today/2026/10/09/stock-market-today-oct-9-at-and-t-slides-on-spacex",
    "http://www.fool.com/coverage/stock-market-today/2026/10/09/stock-market-today-oct-9-at-and-t-slides-on-spacex",
    "https://www.fool.com/coverage/stock-market-today/2026/10/09/stock-market-today-oct-9-at-and-t-slides-on-spacex-and-more",
])
def test_a_bare_domain_or_another_origin_never_prefix_matches(cited):
    payload, result, evidence = incident()
    result["event_verifications"][5]["evidence_refs"] = [cite(cited)]
    data = bind(payload, result, evidence)
    assert data["event_verifications"][5]["evidence_refs"] == []
    assert data["event_verifications"][5]["verdict"] == "unverifiable"


def test_losing_the_only_support_downgrades_that_event_alone():
    payload, result, evidence = incident()
    result["event_verifications"][0]["evidence_refs"] = [cite("https://www.reuters.com/markets/not-retrieved")]
    result["event_verifications"][1]["evidence_refs"][0] = cite("https://www.reuters.com/markets/not-retrieved")
    data = bind(payload, result, evidence)
    verdicts = [entry["verdict"] for entry in data["event_verifications"]]
    assert verdicts == ["unverifiable"] + ["supported"] * 10 + ["unverifiable"] * 9
    assert data["event_verifications"][0]["evidence_refs"] == []
    assert len(data["event_verifications"][1]["evidence_refs"]) == 2


def test_a_contradiction_without_a_contradicting_source_is_downgraded():
    payload, result, evidence = incident()
    entry = result["event_verifications"][2]
    entry["verdict"] = "contradicted"
    entry["evidence_refs"] = [cite("https://www.reuters.com/markets/story-006", "supports")]
    data = bind(payload, result, evidence)
    assert data["event_verifications"][2]["verdict"] == "unverifiable"
    # The bound citation itself is kept.
    assert data["event_verifications"][2]["evidence_refs"][0]["tool_use_id"] == "srv_search_006"


def test_duplicate_citations_are_kept_once():
    payload, result, evidence = incident()
    refs = result["event_verifications"][0]["evidence_refs"]
    refs.append(cite("https://www.reuters.com/markets/story-000"))
    refs.append(cite("https://WWW.REUTERS.COM/markets/story-000#top", call_id="srv_search_000"))
    data = bind(payload, result, evidence)
    assert [ref["tool_use_id"] for ref in data["event_verifications"][0]["evidence_refs"]] == [
        "srv_search_000", "srv_search_001", "srv_search_002",
    ]


def test_a_citation_with_a_wrong_call_id_is_dropped():
    payload, result, evidence = incident()
    result["event_verifications"][3]["evidence_refs"][0]["tool_use_id"] = "invented_call"
    result["event_verifications"][3]["evidence_refs"][1]["tool_use_id"] = "srv_search_000"  # another URL's call
    data = bind(payload, result, evidence)
    assert [ref["url"] for ref in data["event_verifications"][3]["evidence_refs"]] == [
        "https://www.reuters.com/markets/story-011",
    ]
    assert data["event_verifications"][3]["verdict"] == "supported"


@pytest.mark.parametrize("change", ["missing", "duplicate", "version", "unknown"])
def test_the_input_binding_still_fails_the_round(change):
    payload, result, evidence = incident()
    verifications = result["event_verifications"]
    if change == "missing":
        verifications.pop()
    elif change == "duplicate":
        verifications.append(copy.deepcopy(verifications[0]))
    elif change == "version":
        verifications[0]["event_group_version"] = 2
    else:
        verifications[0]["event_group_id"] = "not_input"
    with pytest.raises(ValueError, match="market_focus_verification_event_mismatch"):
        bind(payload, result, evidence)
    # The evidence binding checks it too, for callers that pass a stored result.
    with pytest.raises(ValueError, match="market_focus_verification_event_mismatch"):
        validate_market_focus_evidence(result, payload, evidence)


def test_only_validation_changed_so_the_task_identity_is_the_same():
    assert runtime.schema_identity("market_focus", model=runtime.SONNET_MODEL) == runtime.VERIFIED_FOCUS_IDENTITY
    assert runtime.schema_identity("market_focus", model="claude-haiku-5-5") == runtime.FOCUS_CLAUDE_IDENTITY
    assert runtime.schema_identity("market_focus", model="gpt-5.6-terra") == runtime.FOCUS_OPENAI_IDENTITY


def _receipt_for(job_id: str, result: dict, evidence: list[dict]) -> dict:
    usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10,
             "reasoning_tokens": 0, "total_tokens": 110,
             "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
             "cache_creation_1h_input_tokens": 0}
    return {"provider": "anthropic", "model": runtime.SONNET_MODEL, "id": "msg_" + job_id,
            "output_text": json.dumps(result, ensure_ascii=False), "stop_reason": "end_turn",
            "terminal_error": None, "usage": usage, "evidence_sources": [],
            "tool_evidence_version": "v1", "tool_evidence": evidence}


def _started_focus_job(repo: AIJobRepository, payload: dict) -> str:
    version, digest = runtime.VERIFIED_FOCUS_IDENTITY
    job, _ = repo.create_job(
        job_type="market_focus", payload=payload, model=runtime.SONNET_MODEL, reasoning="xhigh",
        execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["market_focus"],
        schema_version=version, schema_sha256=digest, max_queued=10,
    )
    assert repo.claim_due("owner", 60)["job_id"] == job["job_id"]
    assert repo.mark_submission_started(job["job_id"], "owner", daily_limit=4) == "started"
    return job["job_id"]


def test_the_worker_completes_the_round_with_bound_citations(tmp_path):
    payload, result, evidence = incident()
    result["event_verifications"][0]["evidence_refs"] = [cite("https://www.reuters.com/markets/not-retrieved")]
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = _started_focus_job(repo, payload)
    paid = _receipt_for(job_id, result, evidence)
    repo.record_provider_result(job_id, "owner", paid)
    asyncio.run(worker._finish_claude_receipt(repo, repo.get_job(job_id), "owner", paid))
    row = repo.get_job(job_id)
    assert row["status"] == "completed" and row["schema_version"] == VERIFIED_MARKET_FOCUS_SCHEMA_NAME
    stored = json.loads(row["result_json"])
    assert stored["event_verifications"][0]["verdict"] == "unverifiable"
    assert stored["event_verifications"][5]["evidence_refs"][2]["tool_use_id"] == "srv_search_fool"
    public = repo.public(row)
    assert [entry["verdict"] for entry in public["result"]["event_verifications"]][:2] == ["unverifiable", "supported"]


def test_the_recovery_tool_restores_a_round_that_failed_on_an_ambiguous_citation(tmp_path, monkeypatch, capsys):
    payload, result, evidence = incident()
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = _started_focus_job(repo, payload)
    repo.record_provider_result(job_id, "owner", _receipt_for(job_id, result, evidence))
    repo.fail(job_id, "owner", "schema_validation_failed", detail="market_focus_verification_evidence_ambiguous")
    before = repo.get_job(job_id)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must reuse the stored receipt")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(recovery, "get_settings", lambda: recovery_settings(repo.path))
    assert recovery.main(["--job-id", job_id]) == 0
    assert [item["status"] for item in json.loads(capsys.readouterr().out)] == ["validated"]
    assert repo.get_job(job_id)["status"] == "failed"

    assert recovery.main(["--job-id", job_id, "--apply"]) == 0
    assert [item["status"] for item in json.loads(capsys.readouterr().out)] == ["recovered"]
    row = repo.get_job(job_id)
    assert row["status"] == "completed" and row["error_code"] is None
    stored = json.loads(row["result_json"])
    assert stored["event_verifications"][5]["evidence_refs"][2] == {
        "tool_use_id": "srv_search_fool", "url": FOOL_RECEIPT, "relation": "supports",
    }
    for key in ("budget_charge_microusd", "provider_result_json", "anthropic_message_id"):
        assert row[key] == before[key]
