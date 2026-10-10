"""热点分析输出简洁化（2026-10-10）。

生产 06:28Z 那一轮（Sonnet 核实模式）卡片的标题固定为「市场热点分析」，导语、总结、
市场摘要三段一模一样，都是 12 个已证实事件的摘要用换行拼成的 2,311 字，不确定性为空。
这些都出自公开投影 focus_verification.public_focus_result，不是模型原文：旧规则只公开
已证实事件自己的摘要（823c3a74），模型写的全局文字只留在内部。

本文件覆盖：
- 投影按任务自己的 schema 版本分流：新任务公开模型自己的各字段和每个事件的核实结论，
  旧任务保持原来的投影（读取时会重新投影，所以要靠这道闸门让旧周期不变）；
- 新输出的收紧上限：只对按新 schema 版本创建的任务生效，读取历史结果仍用宽松上限；
- 请求里写明的字段分工与目标长度；
- 版本升级：v6 任务的确切上一版身份仍算现行，排队任务照常提交，核实热点列表不会变空。
"""
from __future__ import annotations

import asyncio
import copy
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.focus_verification import public_focus_result
from app.services.ai_jobs.models import (
    CONCISE_FOCUS_SCHEMA_VERSIONS,
    FOCUS_TEXT_LIMITS,
    MARKET_FOCUS_SCHEMA_NAME,
    VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    ConciseMarketFocusResult,
    ConciseVerifiedMarketFocusResult,
    MarketFocusResult,
    VerifiedMarketFocusResult,
    result_model_for,
    validate_result,
)
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from test_verified_focus_contract import verified_payload_result
from test_verified_hotspot_publication import (  # noqa: F401 - pytest fixtures
    complete_verified,
    owner_access,
    project_cycle,
    verified_stack,
)

FIXTURE = Path(__file__).parent / "fixtures" / "market_focus_cycle_20261010.json"
LEGACY_PROMPT = "market-focus-zh-cn-v6"
PUBLIC_KEYS = (
    "output_language", "cycle_id", "as_of", "input_hash", "title_zh", "summary_zh",
    "headline_summary", "market_summary", "dominant_events", "market_uncertainties",
    "affected_sectors", "focus_ticker_assessments", "no_new_material_catalyst",
    "insufficient_context",
)
def production() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def production_payload_result() -> tuple[dict, dict]:
    """Rebuild the internal verified result behind the production projection."""
    data = production()
    public = data["result"]
    rebuild = data["reconstruction"]
    summaries = public["summary_zh"].split("\n")
    verifications = []
    for index, (event_id, title, summary) in enumerate(
        zip(rebuild["event_ids"], rebuild["titles"], summaries, strict=True)
    ):
        sectors = (
            public["dominant_events"][index]["affected_sectors"]
            if index < 8 else rebuild["extra_sectors"][index - 8]
        )
        verifications.append({
            "event_group_id": event_id, "event_group_version": 1, "verdict": "supported",
            "evidence_refs": [{"tool_use_id": f"srv_{index}", "url": f"https://www.reuters.com/proof/{index}",
                               "relation": "supports"}],
            "title_zh": title, "summary_zh": summary, "affected_sectors": sectors,
        })
    for index, verdict in enumerate(rebuild["unsupported_verdicts"]):
        verifications.append({
            "event_group_id": f"evt_synthetic_other_{index}", "event_group_version": 1, "verdict": verdict,
            "evidence_refs": [] if verdict == "unverifiable" else [{
                "tool_use_id": f"srv_other_{index}", "url": f"https://www.reuters.com/denial/{index}",
                "relation": "contradicts",
            }],
            "title_zh": "候选事件未获证实", "summary_zh": "未证实：本次没有取得直接来源。",
            "affected_sectors": ["其他"],
        })
    payload = {
        "cycle_id": public["cycle_id"], "as_of": public["as_of"], "input_hash": public["input_hash"],
        "prepared_revision": data["cycle"]["prepared_revision"], "verification_version": "web-evidence-v1",
        "allowed_event_group_ids": [entry["event_group_id"] for entry in verifications],
        "allowed_tickers": sorted({item["ticker"] for item in public["focus_ticker_assessments"]}),
        "events": [
            {"event_group_id": entry["event_group_id"], "event_group_version": 1}
            for entry in verifications
        ],
    }
    result = {key: copy.deepcopy(public[key]) for key in PUBLIC_KEYS}
    result["event_verifications"] = verifications
    return payload, result


def concise_payload_result() -> tuple[dict, dict, list]:
    """A normal new verified result whose every field is within its target."""
    payload, result, evidence = verified_payload_result()
    result.update(
        title_zh="英伟达发布新产品",
        headline_summary="英伟达发布新一代产品，公开来源已证实；另一条并购传闻被来源否认。",
        summary_zh="已证实事件集中在半导体。新产品的交付节奏决定影响大小。并购传闻被否认，不纳入判断。",
        market_summary="输入的市场广度偏弱，新产品消息对半导体板块的支撑有限。",
        market_uncertainties=["新产品的交付时间尚未公布。"],
        dominant_events=[{
            "event_group_id": "event_good",
            "summary": "英伟达发布新一代产品。已证实：公司官网公布了发布信息。推断：交付顺利时可能提振板块情绪。",
            "affected_sectors": ["半导体"],
        }],
        focus_ticker_assessments=[{
            "ticker": "NVDA", "catalyst_bias": 25, "confidence": 65, "horizon": "days",
            "supporting_event_ids": ["event_good"], "conflicting_event_ids": [],
            "summary": "已证实的新产品发布构成正面催化，交付节奏仍需观察。",
            "risks": ["交付进度可能推迟。"], "insufficient_evidence": False,
        }],
    )
    result["event_verifications"][0].update(
        title_zh="英伟达发布新一代产品",
        summary_zh="英伟达发布新一代产品。已证实：公司官网公布了发布信息。推断：交付顺利时可能提振板块情绪。",
    )
    return payload, result, evidence


def haiku_payload_result() -> tuple[dict, dict]:
    payload, result, _ = concise_payload_result()
    payload.pop("verification_version")
    payload.pop("events")
    result.pop("event_verifications")
    return payload, result


def text_of(length: int) -> str:
    return ("市场继续关注公司后续披露的进展" * 200)[:length]


def set_path(value, path, text):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = text


# ---------------------------------------------------------------------------
# 生产证据与重建
# ---------------------------------------------------------------------------


def test_production_cycle_published_one_joined_text_three_times():
    public = production()["result"]
    joined = public["summary_zh"]
    assert public["headline_summary"] == public["market_summary"] == joined
    assert len(joined) == 2311
    lines = joined.split("\n")
    assert len(lines) == 12
    assert [event["summary"] for event in public["dominant_events"]] == lines[:8]
    assert public["title_zh"] == "市场热点分析"
    assert public["market_uncertainties"] == []


def test_rebuilt_production_result_still_reads_under_the_lenient_model():
    payload, result = production_payload_result()
    checked = validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload)
    assert checked["headline_summary"] == result["headline_summary"]
    for legacy in (None, "market_focus_verified_zh_cn_v1"):
        assert validate_result(
            "market_focus", json.dumps(result, ensure_ascii=False), payload, schema_version=legacy,
        )["summary_zh"] == result["summary_zh"]


# ---------------------------------------------------------------------------
# 投影按任务的 schema 版本分流
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema_version", [None, "market_focus_verified_zh_cn_v1"])
def test_old_jobs_keep_the_published_projection_exactly(schema_version):
    payload, result = production_payload_result()
    checked = validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload)
    public = public_focus_result(checked, schema_version=schema_version, verified_at="2026-10-10T06:28:17Z")
    assert public == production()["result"]


def test_new_jobs_publish_the_model_fields_and_each_verdict():
    payload, result, _ = concise_payload_result()
    result["focus_ticker_assessments"].append({
        **result["focus_ticker_assessments"][0], "ticker": "AMD",
        "supporting_event_ids": [], "conflicting_event_ids": ["event_bad"],
    })
    payload["allowed_tickers"] = ["NVDA", "AMD"]
    checked = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload,
        schema_version=VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    )
    public = public_focus_result(
        checked, schema_version=VERIFIED_MARKET_FOCUS_SCHEMA_NAME, verified_at="2026-10-10T07:00:00Z",
    )

    fields = ("title_zh", "headline_summary", "summary_zh", "market_summary",
              "market_uncertainties", "dominant_events", "affected_sectors")
    assert {field: public[field] for field in fields} == {field: checked[field] for field in fields}
    assert len({public["title_zh"], public["headline_summary"], public["summary_zh"], public["market_summary"]}) == 4
    assert public["event_verifications"] == [
        {"event_group_id": "event_good", "verdict": "supported", "verified_at": "2026-10-10T07:00:00Z"},
        {"event_group_id": "event_bad", "verdict": "contradicted", "verified_at": "2026-10-10T07:00:00Z"},
    ]
    # 个股评估仍只发布全部引用都已证实的那些。
    assert [item["ticker"] for item in public["focus_ticker_assessments"]] == ["NVDA"]
    # 证据引用（工具编号、网址）不进公开结果；来源仍走 public_focus_sources。
    text = json.dumps(public, ensure_ascii=False)
    for internal in ("evidence_refs", "tool_use_id", "srv_good", "https://"):
        assert internal not in text
    assert set(public) == set(production()["result"]) | {"event_verifications"}


def test_the_production_cycle_under_the_new_contract_carries_every_verdict():
    payload, result = production_payload_result()
    checked = validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload)
    public = public_focus_result(checked, schema_version=VERIFIED_MARKET_FOCUS_SCHEMA_NAME)
    verdicts = [entry["verdict"] for entry in public["event_verifications"]]
    assert verdicts == ["supported"] * 12 + ["unverifiable"] * 7 + ["contradicted"]
    assert public["headline_summary"] == checked["headline_summary"]
    # 同样的三段要成为新输出，在完成时就会因超长被拒，见下一节。


# ---------------------------------------------------------------------------
# 新输出的收紧上限
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("schema_version", "verified"), [
    (VERIFIED_MARKET_FOCUS_SCHEMA_NAME, True),
    (MARKET_FOCUS_SCHEMA_NAME, False),
])
def test_the_production_triple_is_rejected_as_new_output(schema_version, verified):
    payload, result = production_payload_result()
    if not verified:
        payload.pop("verification_version")
        payload.pop("events")
        result.pop("event_verifications")
    raw = json.dumps(result, ensure_ascii=False)
    with pytest.raises(ValidationError) as caught:
        validate_result("market_focus", raw, payload, schema_version=schema_version)
    too_long = {
        error["loc"][0] for error in caught.value.errors() if error["type"] == "string_too_long"
    }
    assert {"summary_zh", "headline_summary", "market_summary"} <= too_long
    # 同一份结果作为历史结果读取时照常通过，不会把旧周期整轮隐藏。
    assert validate_result("market_focus", raw, payload)["summary_zh"] == result["summary_zh"]


LIMITED_PATHS = [
    (("title_zh",), "title"),
    (("headline_summary",), "headline"),
    (("summary_zh",), "summary"),
    (("market_summary",), "market"),
    (("dominant_events", 0, "summary"), "event_summary"),
    (("focus_ticker_assessments", 0, "summary"), "assessment_summary"),
    (("focus_ticker_assessments", 0, "risks", 0), "risk"),
    (("market_uncertainties", 0), "uncertainty"),
    (("event_verifications", 0, "title_zh"), "title"),
    (("event_verifications", 0, "summary_zh"), "event_summary"),
]


@pytest.mark.parametrize(("path", "limit"), LIMITED_PATHS)
def test_each_tightened_limit_rejects_one_character_over(path, limit):
    payload, result, _ = concise_payload_result()
    ceiling = FOCUS_TEXT_LIMITS[limit]
    set_path(result, path, text_of(ceiling))
    assert validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload,
        schema_version=VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    )
    set_path(result, path, text_of(ceiling + 1))
    raw = json.dumps(result, ensure_ascii=False)
    with pytest.raises(ValidationError, match="string_too_long|at most"):
        validate_result("market_focus", raw, payload, schema_version=VERIFIED_MARKET_FOCUS_SCHEMA_NAME)
    assert validate_result("market_focus", raw, payload)


@pytest.mark.parametrize("verified", [True, False])
def test_normal_length_new_output_passes(verified):
    if verified:
        payload, result, _ = concise_payload_result()
        schema_version = VERIFIED_MARKET_FOCUS_SCHEMA_NAME
    else:
        payload, result = haiku_payload_result()
        schema_version = MARKET_FOCUS_SCHEMA_NAME
    checked = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload, schema_version=schema_version,
    )
    assert checked["title_zh"] == "英伟达发布新产品"
    assert checked["dominant_events"][0]["summary"].startswith("英伟达发布新一代产品。已证实：")


def test_read_models_keep_their_historical_limits():
    assert result_model_for("market_focus", model=runtime.SONNET_MODEL) is VerifiedMarketFocusResult
    assert result_model_for("market_focus", model="claude-haiku-5-5") is MarketFocusResult
    assert result_model_for("market_focus", model=runtime.SONNET_MODEL, concise=True) is ConciseVerifiedMarketFocusResult
    assert result_model_for("market_focus", model="claude-haiku-5-5", concise=True) is ConciseMarketFocusResult
    lenient = MarketFocusResult.model_json_schema()["properties"]
    assert lenient["headline_summary"]["maxLength"] == 3000
    assert lenient["title_zh"]["maxLength"] == 500
    assert CONCISE_FOCUS_SCHEMA_VERSIONS == {MARKET_FOCUS_SCHEMA_NAME, VERIFIED_MARKET_FOCUS_SCHEMA_NAME}


# ---------------------------------------------------------------------------
# 请求里的字段分工
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("model", "payload", "schema_name"), [
    ("claude-haiku-5-5", {}, MARKET_FOCUS_SCHEMA_NAME),
    (runtime.SONNET_MODEL, {"verification_version": "web-evidence-v1"}, VERIFIED_MARKET_FOCUS_SCHEMA_NAME),
])
def test_requests_state_each_field_job_and_carry_the_tightened_limits(model, payload, schema_name):
    request = runtime.build_runtime_request("market_focus", payload, model=model)
    assert request.schema_name == schema_name
    for sentence in (
        "title_zh写本轮主线，不超过30字",
        "headline_summary写一两句导语，不超过120字",
        "summary_zh写市场层面的结论与限制，三到五句，不超过500字，不得重复导语",
        "market_summary只写输入市场状态（如市场广度、宏观环境）与事件的关系，不超过300字",
        "dominant_events每条summary不超过220字，先写一句标题式短句",
        "focus_ticker_assessments每条summary不超过160字，risks每条不超过40字",
        "market_uncertainties每条不超过60字，存在不确定性时必须列出",
        "不同字段之间不得整段重复",
    ):
        assert sentence in request.instructions
    schema = runtime.claude_output_schema("market_focus", request.schema)
    fields = schema["properties"]
    definitions = schema["$defs"]
    expected = [
        (fields["title_zh"], "title", "不超过30字"),
        (fields["headline_summary"], "headline", "不超过120字"),
        (fields["summary_zh"], "summary", "不超过500字"),
        (fields["market_summary"], "market", "不超过300字"),
    ]
    for field, limit, target in expected:
        assert field["maxLength"] == FOCUS_TEXT_LIMITS[limit]
        assert target in field["description"]
    assert fields["market_uncertainties"]["items"]["maxLength"] == FOCUS_TEXT_LIMITS["uncertainty"]
    assert "不超过60字" in fields["market_uncertainties"]["description"]
    event = definitions["ConciseMarketFocusDominantEvent"]["properties"]["summary"]
    assert event["maxLength"] == FOCUS_TEXT_LIMITS["event_summary"]
    assert "不超过220字" in event["description"]
    assessment = definitions["ConciseMarketFocusTickerAssessment"]["properties"]
    assert assessment["summary"]["maxLength"] == FOCUS_TEXT_LIMITS["assessment_summary"]
    assert assessment["risks"]["items"]["maxLength"] == FOCUS_TEXT_LIMITS["risk"]
    verified = model == runtime.SONNET_MODEL
    assert ("ConciseFocusEventVerification" in definitions) is verified
    assert ("event_verifications每条title_zh写成不超过30字的事件标题" in request.instructions) is verified
    assert ("会原样公开展示：结论只能依据supported事件" in request.instructions) is verified
    if verified:
        entry = definitions["ConciseFocusEventVerification"]["properties"]
        assert entry["title_zh"]["maxLength"] == FOCUS_TEXT_LIMITS["title"]
        assert entry["summary_zh"]["maxLength"] == FOCUS_TEXT_LIMITS["event_summary"]
        assert "不超过220字" in entry["summary_zh"]["description"]


# ---------------------------------------------------------------------------
# 完成路径按任务自己的 schema 版本校验
# ---------------------------------------------------------------------------


def long_receipt(result: dict) -> dict:
    return {
        "provider": "anthropic", "model": runtime.SONNET_MODEL, "id": "msg_concise",
        "output_text": json.dumps(result, ensure_ascii=False), "stop_reason": "end_turn",
        "terminal_error": None, "usage": {"input_tokens": 1, "output_tokens": 1},
        "evidence_sources": [], "tool_evidence_version": "v1",
    }


@pytest.mark.parametrize(("job_type", "schema_version", "rejected"), [
    ("market_focus", VERIFIED_MARKET_FOCUS_SCHEMA_NAME, True),
    ("market_focus", "market_focus_verified_zh_cn_v1", False),
    ("market_focus", None, False),
    ("news_impact", VERIFIED_MARKET_FOCUS_SCHEMA_NAME, False),
])
def test_new_output_limits_follow_the_job_schema_version(job_type, schema_version, rejected):
    payload, result, _ = concise_payload_result()
    result["headline_summary"] = text_of(FOCUS_TEXT_LIMITS["headline"] + 1)
    receipt = long_receipt(result)
    # The ordinary completion check still accepts it; only the job's own
    # contract decides whether the tightened limits apply on top.
    assert runtime.receipt_result(receipt, "market_focus", payload)["headline_summary"] == result["headline_summary"]
    job = {"job_type": job_type, "schema_version": schema_version}
    if rejected:
        with pytest.raises(ValidationError, match="string_too_long|at most"):
            runtime.enforce_new_output_limits(job, receipt["output_text"], payload)
    else:
        assert runtime.enforce_new_output_limits(job, receipt["output_text"], payload) is None


class _RecordingRepository:
    def __init__(self):
        self.calls: list[tuple[str, tuple, dict]] = []

    def fail(self, *args, **kwargs):
        self.calls.append(("fail", args, kwargs))

    def complete(self, *args, **kwargs):
        self.calls.append(("complete", args, kwargs))


@pytest.mark.parametrize(("schema_version", "outcome"), [
    (VERIFIED_MARKET_FOCUS_SCHEMA_NAME, "fail"),
    ("market_focus_verified_zh_cn_v1", "complete"),
])
def test_worker_completion_passes_the_job_schema_version(schema_version, outcome):
    payload, result, evidence = concise_payload_result()
    result["summary_zh"] = text_of(FOCUS_TEXT_LIMITS["summary"] + 1)
    receipt = {**long_receipt(result), "tool_evidence": evidence}
    job = {
        "job_id": "job_concise", "job_type": "market_focus", "schema_version": schema_version,
        "payload_json": json.dumps(payload, ensure_ascii=False),
    }
    repository = _RecordingRepository()
    asyncio.run(worker._finish_claude_receipt(repository, job, "owner", receipt))
    assert [call[0] for call in repository.calls] == [outcome]
    if outcome == "fail":
        assert "string_too_long" in repository.calls[0][2]["detail"]


@pytest.mark.parametrize(("schema_version", "outcome"), [
    (MARKET_FOCUS_SCHEMA_NAME, "fail"),
    ("market_focus_zh_cn_v5", "complete"),
])
def test_openai_worker_completion_passes_the_job_schema_version(schema_version, outcome):
    payload, result = haiku_payload_result()
    result["summary_zh"] = text_of(FOCUS_TEXT_LIMITS["summary"] + 1)
    response = SimpleNamespace(
        status="completed", id="resp_concise", model="gpt-5.6-terra", output=[], refusal=None,
        output_text=json.dumps(result, ensure_ascii=False),
        usage=SimpleNamespace(input_tokens=1, output_tokens=1, total_tokens=2),
    )
    job = {
        "job_id": "job_concise", "job_type": "market_focus", "schema_version": schema_version,
        "model": "gpt-5.6-terra", "payload_json": json.dumps(payload, ensure_ascii=False),
    }
    repository = _RecordingRepository()
    asyncio.run(worker._finish_response(repository, None, job, "owner", response))
    assert [call[0] for call in repository.calls] == [outcome]
    if outcome == "fail":
        assert "string_too_long" in repository.calls[0][2]["detail"]


# ---------------------------------------------------------------------------
# 版本升级与在途周期
# ---------------------------------------------------------------------------


def test_identity_bump_allows_only_the_exact_previous_identities():
    assert runtime.PROMPT_VERSIONS["market_focus"] == "market-focus-zh-cn-v7"
    assert runtime.FOCUS_READABLE_PROMPT_VERSIONS == {"market-focus-zh-cn-v7", LEGACY_PROMPT}
    pairs = (
        ("claude-haiku-5-5", runtime.FOCUS_CLAUDE_IDENTITY, runtime.LEGACY_FOCUS_CLAUDE_IDENTITY),
        (runtime.SONNET_MODEL, runtime.VERIFIED_FOCUS_IDENTITY, runtime.LEGACY_VERIFIED_FOCUS_IDENTITY),
        ("gpt-5.6-terra", runtime.FOCUS_OPENAI_IDENTITY, runtime.LEGACY_FOCUS_OPENAI_IDENTITY),
    )
    for model, current, legacy in pairs:
        assert runtime.schema_identity("market_focus", model=model) == current
        assert runtime.schema_identity_current("market_focus", LEGACY_PROMPT, *legacy, model=model)
        assert not runtime.schema_identity_current(
            "market_focus", LEGACY_PROMPT, legacy[0], "0" * 64, model=model,
        )
    # 上一版身份只对应自己的模型。
    assert not runtime.schema_identity_current(
        "market_focus", LEGACY_PROMPT, *runtime.LEGACY_FOCUS_CLAUDE_IDENTITY, model=runtime.SONNET_MODEL,
    )


def test_identity_predecessors_stop_at_the_next_policy_change(monkeypatch):
    monkeypatch.setitem(runtime.CLAUDE_TASK_MAX_OUTPUT_TOKENS, "market_focus", 16_384)
    monkeypatch.setitem(runtime.AI_TASK_MAX_OUTPUT_TOKENS, "market_focus", 16_384)
    for model, legacy in (
        (runtime.SONNET_MODEL, runtime.LEGACY_VERIFIED_FOCUS_IDENTITY),
        ("claude-haiku-5-5", runtime.LEGACY_FOCUS_CLAUDE_IDENTITY),
        ("gpt-5.6-terra", runtime.LEGACY_FOCUS_OPENAI_IDENTITY),
    ):
        assert not runtime.schema_identity_current("market_focus", LEGACY_PROMPT, *legacy, model=model)


def _focus_row(prompt_version: str, identity: tuple[str, str]) -> dict:
    return {
        "job_id": "job_focus", "job_type": "market_focus", "status": "completed",
        "model": runtime.SONNET_MODEL, "reasoning": "xhigh", "execution_mode": "background",
        "prompt_version": prompt_version, "schema_version": identity[0], "schema_sha256": identity[1],
        "result_json": "{}",
    }


def _engine_stub():
    stub = SimpleNamespace(model=runtime.LUNA_MODEL, focus_model=runtime.SONNET_MODEL)
    stub._has_current_job_identity = (
        lambda row, **kwargs: LocalCatalystIntelligence._has_current_job_identity(stub, row, **kwargs)
    )
    stub._public_job = lambda row: {"job_id": row["job_id"]}
    return stub


@pytest.mark.parametrize(("prompt_version", "identity", "current", "recoverable"), [
    ("market-focus-zh-cn-v7", runtime.VERIFIED_FOCUS_IDENTITY, True, True),
    (LEGACY_PROMPT, runtime.LEGACY_VERIFIED_FOCUS_IDENTITY, True, True),
    # 升级前就不算现行的 v6 任务，升级后也不会被重新放行。
    (LEGACY_PROMPT, ("market_focus_verified_zh_cn_v1", "0" * 64), False, False),
    # 更早的提示词家族照旧只走「已完成可恢复」一条路。
    ("market-focus-zh-cn-v5", runtime.LEGACY_VERIFIED_FOCUS_IDENTITY, False, True),
])
def test_engine_identity_gates_after_the_bump(prompt_version, identity, current, recoverable):
    stub = _engine_stub()
    row = _focus_row(prompt_version, identity)
    assert LocalCatalystIntelligence._has_current_job_identity(stub, row, expected_type="market_focus") is current
    public = LocalCatalystIntelligence._recoverable_completed_focus_public_job(stub, row)
    assert (public is not None) is recoverable


def _rewrite_identity(ai, job_id: str, prompt_version: str, identity: tuple[str, str]) -> None:
    with sqlite3.connect(ai.path) as connection:
        connection.execute(
            "UPDATE ai_jobs SET prompt_version=?,schema_version=?,schema_sha256=? WHERE job_id=?",
            (prompt_version, *identity, job_id),
        )


def test_a_v6_job_from_before_the_bump_still_publishes_verified_hotspots(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    row = ai.get_job(cycle["job_id"])
    assert (row["prompt_version"], row["schema_version"]) == (
        "market-focus-zh-cn-v7", VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    )
    _rewrite_identity(ai, cycle["job_id"], LEGACY_PROMPT, runtime.LEGACY_VERIFIED_FOCUS_IDENTITY)
    complete_verified(ai, cycle)
    intelligence.reconcile()
    hotspots = intelligence.hotspots(limit=20)
    assert [item["representative_title"] for item in hotspots["items"]] == ["新产品发布"]
    public = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert public["verification_status"] == "verified"
    # 旧任务保持原来的投影：只拼已证实事件的摘要，没有核实结论。
    assert public["result"]["title_zh"] == "市场热点分析"
    assert public["result"]["headline_summary"] == public["result"]["summary_zh"] == "公司发布新产品，交付进展仍需观察。"
    assert "event_verifications" not in public["result"]


def test_a_v6_job_with_an_unlisted_identity_is_still_retired(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    _rewrite_identity(ai, cycle["job_id"], LEGACY_PROMPT, ("market_focus_verified_zh_cn_v1", "0" * 64))
    complete_verified(ai, cycle)
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    public = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert public["status"] == "failed"
    assert public["error_code"] == "runtime_configuration_changed"


@pytest.mark.parametrize(("legacy", "recovered"), [(False, False), (True, True)])
def test_paid_result_recovery_applies_the_same_limits_as_completion(tmp_path, legacy, recovered):
    from anthropic.types import Message, Usage

    from app.services.ai_jobs.repository import AIJobRepository
    from test_ai_jobs_zh_contract import _market_focus_payload, _market_focus_result

    repository = AIJobRepository(tmp_path / "jobs.db")
    payload = _market_focus_payload()
    identity = (
        runtime.LEGACY_FOCUS_CLAUDE_IDENTITY if legacy
        else runtime.schema_identity("market_focus", model="claude-haiku-5-5")
    )
    row, _ = repository.create_job(
        job_type="market_focus", payload=payload,
        model="claude-haiku-5-5", reasoning="xhigh", execution_mode="background",
        prompt_version=LEGACY_PROMPT if legacy else runtime.PROMPT_VERSIONS["market_focus"],
        schema_version=identity[0], schema_sha256=identity[1], max_queued=10,
    )
    job_id = row["job_id"]
    repository.claim_due("owner", 60)
    repository.mark_submission_started(job_id, "owner", daily_limit=0)
    result = _market_focus_result()
    result["headline_summary"] = text_of(FOCUS_TEXT_LIMITS["headline"] + 1)
    raw = json.dumps(result, ensure_ascii=False)
    repository.record_provider_result(job_id, "owner", runtime.claude_receipt(Message(
        id="msg_concise_recovery", type="message", role="assistant",
        model="claude-haiku-5-5", stop_reason="end_turn", stop_sequence=None,
        content=[{"type": "text", "text": raw}],
        usage=Usage(input_tokens=100, output_tokens=100,
                    cache_read_input_tokens=0, cache_creation_input_tokens=0),
    )))
    repository.fail(job_id, "owner", "schema_validation_failed")
    lenient = validate_result("market_focus", raw, payload)
    if recovered:
        repository.recover_schema_validation_failure(job_id, "msg_concise_recovery", lenient)
        assert repository.get_job(job_id)["status"] == "completed"
    else:
        with pytest.raises(ValidationError):
            repository.recover_schema_validation_failure(job_id, "msg_concise_recovery", lenient)
        assert repository.get_job(job_id)["status"] == "failed"


def complete_concise(ai, cycle):
    """Record a concise v2 result for the cycle's job the way the worker would."""
    row = ai.get_job(cycle["job_id"])
    payload = json.loads(row["payload_json"])
    _, result, _ = concise_payload_result()
    result.update(cycle_id=payload["cycle_id"], as_of=payload["as_of"], input_hash=payload["input_hash"])
    result["dominant_events"] = []
    result["focus_ticker_assessments"] = []
    verdicts = ["supported", "contradicted"]
    result["event_verifications"], evidence = [], []
    for index, event in enumerate(payload["events"]):
        verdict = verdicts[index % 2]
        url = f"https://www.reuters.com/proof/{index}"
        result["event_verifications"].append({
            "event_group_id": event["event_group_id"], "event_group_version": event["event_group_version"],
            "verdict": verdict,
            "evidence_refs": [{"tool_use_id": f"srv_{index}", "url": url,
                               "relation": "supports" if verdict == "supported" else "contradicts"}],
            "title_zh": "新产品发布" if verdict == "supported" else "并购传闻",
            "summary_zh": "公司发布新产品。已证实：官网公布了发布信息。推断：交付顺利时可能提振板块。"
            if verdict == "supported" else "并购传闻被否认。未证实：来源否认了这笔交易。推断：不影响判断。",
            "affected_sectors": ["半导体"],
        })
        evidence.append({"tool_use_id": f"srv_{index}", "tool_name": "web_fetch", "status": "success",
                         "url": url, "title": "Published source", "content_sha256": "b" * 64})
    result["dominant_events"] = [{
        "event_group_id": payload["events"][0]["event_group_id"],
        "summary": "公司发布新产品。已证实：官网公布了发布信息。推断：交付顺利时可能提振板块。",
        "affected_sectors": ["半导体"],
    }]
    result = validate_result(
        "market_focus", json.dumps(result, ensure_ascii=False), payload, schema_version=row["schema_version"],
    )
    usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10,
             "reasoning_tokens": 0, "total_tokens": 110,
             "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
             "cache_creation_1h_input_tokens": 0}
    receipt = {"provider": "anthropic", "model": "claude-sonnet-5-5", "id": "msg_" + cycle["cycle_id"],
               "output_text": json.dumps(result, ensure_ascii=False), "stop_reason": "end_turn", "terminal_error": None,
               "usage": usage, "evidence_sources": [], "tool_evidence_version": "v1", "tool_evidence": evidence}
    owner = "owner_" + cycle["cycle_id"]
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == row["job_id"]
    assert ai.mark_submission_started(row["job_id"], owner, daily_limit=4) == "started"
    ai.record_provider_result(row["job_id"], owner, receipt)
    ai.complete(row["job_id"], owner, result, usage)
    return result


def test_a_v7_cycle_publishes_the_model_fields_with_verdicts(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    result = complete_concise(ai, cycle)
    intelligence.reconcile()
    job = ai.get_job(cycle["job_id"])
    assert job["schema_version"] == VERIFIED_MARKET_FOCUS_SCHEMA_NAME
    # 热点列表照旧只用已证实事件。
    assert [item["representative_title"] for item in intelligence.hotspots(limit=20)["items"]] == ["新产品发布"]

    expected_verdicts = [
        {"event_group_id": entry["event_group_id"], "verdict": entry["verdict"], "verified_at": job["completed_at"]}
        for entry in result["event_verifications"]
    ]
    cycle_public = project_cycle(intelligence, ai, cycle["cycle_id"])
    job_public = ai.public(job)
    with intelligence._connect() as connection:
        stored = json.loads(connection.execute(
            "SELECT public_result_json FROM catalyst_local_verified_focus_publications WHERE cycle_id=?",
            (cycle["cycle_id"],),
        ).fetchone()[0])
    for public in (cycle_public["result"], job_public["result"], stored):
        assert public["title_zh"] == result["title_zh"] == "英伟达发布新产品"
        assert public["headline_summary"] == result["headline_summary"]
        assert public["summary_zh"] == result["summary_zh"]
        assert public["market_summary"] == result["market_summary"]
        assert public["market_uncertainties"] == result["market_uncertainties"]
        assert public["dominant_events"] == result["dominant_events"]
        assert public["event_verifications"] == expected_verdicts
        assert "evidence_refs" not in json.dumps(public)
    assert "_projection" not in json.dumps(cycle_public)
