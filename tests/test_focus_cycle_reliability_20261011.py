"""热点周期可靠性（2026-10-11）。

当天最后三轮 Sonnet 核实热点全部失败，热点条因此只剩「未核验」：

- 03:01Z aij_28555a65：模型把 64 位十六进制的 input_hash 抄错，在字段规则
  ``^[0-9a-f]{64}$`` 上整轮作废（schema_validation_failed）。现在 input_hash
  一律在字段校验前改用载荷值；cycle_id 与 as_of 仍比对。提示词和任务身份都不变，
  找回工具可按现行规则重验当时失败的回执。
- 05:02Z aij_b4dc17c2：累计输出 66,912（思考 32,217）超过 65,536 的上限。Sonnet
  热点的上限改为单次请求上限 128,000（任务身份随之改变，上一版身份照常放行）；
  以 end_turn 结束的完整答案不再因累计超额被丢弃。
- 00:01Z aij_2b4c1afd：第二轮请求里出现 RuntimeError，记成结果未知。失败细节现在
  写明是哪一步；联网工具的本地上限比 max_uses 高一次，与财报一致。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx2
import pytest
from pydantic import SecretStr

from app.access import request_owner_access_context
from app.services.ai_jobs import claude_provider as provider, runtime, worker
from app.services.ai_jobs.models import (
    VERIFIED_MARKET_FOCUS_SCHEMA_NAME,
    InvalidJobPayloadError,
    validate_market_focus_evidence,
    validate_result,
)
from app.services.ai_jobs.repository import RECOVERABLE_FAILURE_CODES, AIJobRepository
from app.services.catalysts.local_intelligence import _market_focus_result_identity_matches
from app.tools import recover_ai_schema_results as recovery
from test_ai_jobs_zh_contract import _market_focus_payload, _market_focus_result
from test_claude_job_worker import settings as base_settings
from test_claude_market_focus_output import valid_result
from test_claude_provider import _call, _search_result, _sse, _tool_events, _transport_client
from test_claude_tool_evidence import _focus_job, _round
from test_luna_news_web_fallback import settings as recovery_settings
from test_verified_focus_contract import verified_payload_result
from test_verified_hotspot_publication import verified_stack  # noqa: F401 - pytest fixture

# The production value is shown truncated in the error; the middle is synthetic.
PAYLOAD_HASH = "b89b73245cb3d9e0cb7527f7" + "3c1e9f02a7d4b58e6" + "684a2dab4f4f8b51d8a64f6"
PRODUCTION_ERROR = (
    "1 validation error for VerifiedMarketFocusResult\ninput_hash\n"
    "  String should match pattern '^[0-9a-f]{64}$'"
)


@pytest.fixture(autouse=True)
def owner_context():
    with request_owner_access_context(True):
        yield


def _mistyped(value: str, how: str) -> str:
    if how == "drop_one":  # The production shape: 63 characters fail the pattern.
        return value[:30] + value[31:]
    if how == "add_one":
        return value + "0"
    if how == "non_hex":
        return value[:10] + "g" + value[11:]
    if how == "upper":
        return value.upper()
    if how == "other_hex":  # Well formed but wrong: market_focus_input_hash_mismatch before.
        return "c" * 64
    raise AssertionError(how)


def _verified() -> tuple[dict, dict, list[dict]]:
    payload, result, evidence = verified_payload_result()
    payload["input_hash"] = result["input_hash"] = PAYLOAD_HASH
    return payload, result, evidence


def _checked(result: dict, payload: dict, **options) -> dict:
    return validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload, **options)


# --- 03:01Z: input_hash comes from the payload ------------------------------


@pytest.mark.parametrize("schema_version", [None, VERIFIED_MARKET_FOCUS_SCHEMA_NAME])
@pytest.mark.parametrize("how", ["drop_one", "add_one", "non_hex", "upper", "other_hex", "missing"])
def test_a_mistyped_input_hash_is_replaced_by_the_payload_value(how, schema_version):
    payload, result, evidence = _verified()
    if how == "missing":
        del result["input_hash"]
    else:
        result["input_hash"] = _mistyped(PAYLOAD_HASH, how)
    checked = _checked(result, payload, schema_version=schema_version)
    assert checked["input_hash"] == PAYLOAD_HASH
    validate_market_focus_evidence(checked, payload, evidence)
    # Publication compares the stored result with the cycle input directly.
    assert _market_focus_result_identity_matches(checked, payload)


def test_the_unverified_contract_takes_the_payload_hash_too():
    payload = _market_focus_payload(input_hash=PAYLOAD_HASH)
    result = _market_focus_result()
    result["input_hash"] = _mistyped(PAYLOAD_HASH, "drop_one")
    assert _checked(result, payload)["input_hash"] == PAYLOAD_HASH


@pytest.mark.parametrize(("field", "value", "error"), [
    ("cycle_id", "mfc_another_cycle", "market_focus_cycle_mismatch"),
    ("as_of", "2026-10-09T00:00:01Z", "market_focus_as_of_mismatch"),
])
def test_the_cycle_and_its_time_still_bind_the_result(field, value, error):
    payload, result, _ = _verified()
    result["input_hash"] = _mistyped(PAYLOAD_HASH, "drop_one")
    result[field] = value
    with pytest.raises(ValueError, match=error):
        _checked(result, payload)


def test_an_invalid_payload_hash_is_still_an_invalid_payload():
    payload, result, _ = _verified()
    payload["input_hash"] = "not-a-sha256"
    with pytest.raises(InvalidJobPayloadError, match="input_hash_invalid") as raised:
        _checked(result, payload)
    assert worker._public_error(raised.value, submitted=True, response_id="msg") == "invalid_job_payload"


def test_the_hash_rule_moves_neither_the_prompt_nor_the_schema(monkeypatch):
    request = runtime.build_runtime_request("market_focus", {}, model=runtime.SONNET_MODEL)
    assert "cycle_id、as_of和input_hash必须原样复制" in request.instructions
    assert runtime.schema_identity("market_focus", model="claude-haiku-5-5") == runtime.FOCUS_CLAUDE_IDENTITY
    assert runtime.schema_identity("market_focus", model="gpt-5.6-terra") == runtime.FOCUS_OPENAI_IDENTITY
    # The Sonnet identity moved with its output ceiling alone (see below).
    monkeypatch.setattr(runtime, "SONNET_FOCUS_MAX_OUTPUT_TOKENS", 65_536)
    assert runtime.schema_identity("market_focus", model=runtime.SONNET_MODEL) == runtime.VERIFIED_FOCUS_65K_IDENTITY


def _receipt(job_id: str, result: dict, evidence: list[dict]) -> dict:
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


def test_the_worker_completes_a_paid_round_with_a_mistyped_hash(tmp_path):
    payload, result, evidence = _verified()
    result["input_hash"] = _mistyped(PAYLOAD_HASH, "drop_one")
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = _started_focus_job(repo, payload)
    paid = _receipt(job_id, result, evidence)
    repo.record_provider_result(job_id, "owner", paid)
    asyncio.run(worker._finish_claude_receipt(repo, repo.get_job(job_id), "owner", paid))
    row = repo.get_job(job_id)
    assert row["status"] == "completed" and row["error_code"] is None
    assert row["schema_version"] == VERIFIED_MARKET_FOCUS_SCHEMA_NAME
    assert json.loads(row["result_json"])["input_hash"] == PAYLOAD_HASH
    # The paid receipt keeps the model's own text.
    assert json.loads(repo.get_provider_result(job_id)["output_text"])["input_hash"] != PAYLOAD_HASH


def test_the_recovery_tool_restores_rounds_that_failed_on_the_hash(tmp_path, monkeypatch, capsys):
    assert "market_focus_input_hash_mismatch" in RECOVERABLE_FAILURE_CODES
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_ids = []
    for how, code, detail in (
        ("drop_one", "schema_validation_failed", PRODUCTION_ERROR),
        ("other_hex", "market_focus_input_hash_mismatch", None),
    ):
        payload, result, evidence = _verified()
        payload["cycle_id"] = result["cycle_id"] = "mfc_" + how
        result["input_hash"] = _mistyped(PAYLOAD_HASH, how)
        job_id = _started_focus_job(repo, payload)
        paid = _receipt(job_id, result, evidence)
        repo.record_provider_result(job_id, "owner", paid)
        repo.fail(job_id, "owner", code, usage=paid["usage"], detail=detail)
        job_ids.append(job_id)
    before = {job_id: repo.get_job(job_id) for job_id in job_ids}
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    assert sorted(repo.recoverable_receipt_failures(since=since, job_types=["market_focus"])) == sorted(job_ids)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must reuse the stored receipt")

    monkeypatch.setattr(runtime, "retrieve", forbidden)
    monkeypatch.setattr(recovery, "get_settings", lambda: recovery_settings(repo.path))
    arguments = ["--failed-since", since.isoformat(), "--job-type", "market_focus"]
    assert recovery.main(arguments) == 0
    assert [item["status"] for item in json.loads(capsys.readouterr().out)] == ["validated"] * 2
    assert recovery.main([*arguments, "--apply"]) == 0
    assert [item["status"] for item in json.loads(capsys.readouterr().out)] == ["recovered"] * 2
    for job_id in job_ids:
        row = repo.get_job(job_id)
        assert row["status"] == "completed" and row["error_code"] is None and row["error_detail"] is None
        assert json.loads(row["result_json"])["input_hash"] == PAYLOAD_HASH
        for key in ("budget_charge_microusd", "usage_output_tokens", "provider_result_json", "anthropic_message_id"):
            assert row[key] == before[job_id][key]


def test_a_recovered_round_publishes_its_verified_hotspots(verified_stack, monkeypatch):  # noqa: F811
    _etl, ai, intelligence, revision, _clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    row = ai.get_job(cycle["job_id"])
    payload = json.loads(row["payload_json"])
    _, result, _ = verified_payload_result()
    result.update(cycle_id=payload["cycle_id"], as_of=payload["as_of"],
                  input_hash=_mistyped(payload["input_hash"], "drop_one"))
    result["event_verifications"], evidence = [], []
    for index, event in enumerate(payload["events"]):
        url = f"https://www.reuters.com/proof/{index}"
        evidence.append({"tool_use_id": f"srv_{index}", "tool_name": "web_fetch", "status": "success",
                         "url": url, "title": "Published source", "content_sha256": "b" * 64})
        result["event_verifications"].append({
            "event_group_id": event["event_group_id"], "event_group_version": event["event_group_version"],
            "verdict": "supported", "title_zh": "新产品发布", "summary_zh": "公司发布新产品，交付进展仍需观察。",
            "evidence_refs": [{"tool_use_id": f"srv_{index}", "url": url, "relation": "supports"}],
            "affected_sectors": ["半导体"],
        })
    paid = {**_receipt(row["job_id"], result, evidence), "id": "msg_" + cycle["cycle_id"]}
    assert ai.claim_due("owner_focus", lease_seconds=60)["job_id"] == row["job_id"]
    assert ai.mark_submission_started(row["job_id"], "owner_focus", daily_limit=4) == "started"
    ai.record_provider_result(row["job_id"], "owner_focus", paid)
    # What the 03:01Z round looked like: paid, then rejected at the field pattern.
    ai.fail(row["job_id"], "owner_focus", "schema_validation_failed", usage=paid["usage"], detail=PRODUCTION_ERROR)
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []

    monkeypatch.setattr(recovery, "get_settings", lambda: recovery_settings(ai.path))
    assert asyncio.run(recovery.recover([row["job_id"]], apply=True))[0]["status"] == "recovered"
    intelligence.reconcile()
    items = intelligence.hotspots(limit=20)["items"]
    assert len(items) == len(payload["events"]) == 2
    assert {item["representative_title"] for item in items} == {"新产品发布"}


# --- 05:02Z: the Sonnet focus output ceiling ---------------------------------


def _sonnet_settings(path):
    return base_settings(path).model_copy(update={
        "openai_market_focus_model": runtime.SONNET_MODEL, "openai_market_focus_reasoning": "xhigh",
    })


def test_sonnet_focus_requests_the_model_output_ceiling(tmp_path):
    payload, _, _ = _verified()
    config = runtime.settings_for_job(_sonnet_settings(tmp_path / "jobs.db"), "market_focus")
    prepared = runtime.prepare_claude(config, "market_focus", payload)
    assert prepared.params["model"] == runtime.SONNET_MODEL
    assert prepared.params["max_tokens"] == runtime.SONNET_FOCUS_MAX_OUTPUT_TOKENS == 128_000
    # Haiku focus and the other Claude tasks keep 65,536.
    assert runtime.max_output_tokens_for("market_focus", model="claude-haiku-5-5") == 65_536
    assert runtime.max_output_tokens_for("earnings_impact", model="claude-haiku-5-5") == 65_536
    # Input takes the rest of the fixed 1M-token admission allowance: 872,000
    # input tokens at $4/M, 128,000 output tokens at $10/M, 12 searches at $0.01
    # ($4.51 under the old 65,536 ceiling).
    assert runtime.budget_reservation_microusd("market_focus", model=runtime.SONNET_MODEL) == 4_888_000


def test_a_queued_job_from_the_65k_identity_is_sent_with_the_new_ceiling(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    payload = {"cycle_id": "mfc_queued_65k", "as_of": "2026-10-11T05:02:00Z", "input_hash": "a" * 64,
               "allowed_event_group_ids": [], "allowed_tickers": [],
               "verification_version": "web-evidence-v1", "events": []}
    version, digest = runtime.VERIFIED_FOCUS_65K_IDENTITY
    job, _ = repo.create_job(
        job_type="market_focus", payload=payload, model=runtime.SONNET_MODEL, reasoning="xhigh",
        execution_mode="background", prompt_version=runtime.PROMPT_VERSIONS["market_focus"],
        schema_version=version, schema_sha256=digest, max_queued=10,
    )
    answer = json.dumps({**valid_result(payload), "event_verifications": []}, ensure_ascii=False)
    sent = []

    async def stream(prepared, *, on_message_start, tool_counts):
        sent.append(prepared.params["max_tokens"])
        await on_message_start("msg_1")
        return _round("msg_1", "end_turn", [{"type": "text", "text": answer}])

    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, _sonnet_settings(repo.path), "owner"))
    row = repo.get_job(job["job_id"])
    assert row["status"] == "completed", row
    assert sent == [128_000]
    assert (row["schema_version"], row["schema_sha256"]) == runtime.VERIFIED_FOCUS_65K_IDENTITY


def _conversation(monkeypatch, rounds):
    sent = []

    async def stream(prepared, *, on_message_start, tool_counts):
        sent.append(prepared.params["max_tokens"])
        message = rounds[len(sent) - 1]
        await on_message_start(message.id)
        return message

    async def noop(*_args, **_kwargs):
        pass

    monkeypatch.setattr(provider, "stream_message", stream)
    prepared = provider.PreparedMessage(
        params={"model": runtime.SONNET_MODEL, "max_tokens": 100, "messages": [{"role": "user", "content": "input"}]},
        api_key="test", timeout_seconds=1, diagnostic_task="ai_jobs:market_focus",
    )
    receipt = asyncio.run(provider.stream_conversation(prepared, on_message_start=noop, on_progress=noop)).receipt
    return receipt, sent


def test_a_finished_answer_past_the_ceiling_is_kept(monkeypatch):
    # Server tool iterations let one request write past its max_tokens:
    # aij_b4dc17c2 reported 66,912 output tokens under a 65,536 ceiling.
    receipt, sent = _conversation(monkeypatch, [_round("msg_1", output=60), _round("msg_2", "end_turn", output=60)])
    assert sent == [100, 40]
    assert receipt["terminal_error"] is None and receipt["output_text"] == '{"answer":"done"}'
    assert receipt["usage"]["output_tokens"] == 120


def test_no_request_is_sent_past_the_ceiling(monkeypatch):
    receipt, sent = _conversation(monkeypatch, [_round("msg_1", output=120), _round("msg_2", "end_turn")])
    assert sent == [100]
    assert receipt["terminal_error"] == "provider_incomplete_max_output_tokens" and receipt["output_text"] == ""


def test_a_max_tokens_stop_still_fails(monkeypatch):
    # Sonnet 5.5 rejects an assistant prefill, so a cut-off JSON cannot be continued.
    receipt, sent = _conversation(monkeypatch, [_round("msg_1", output=60), _round("msg_2", "max_tokens", output=60)])
    assert sent == [100, 40]
    assert receipt["terminal_error"] == "provider_incomplete_max_output_tokens" and receipt["output_text"] == ""


# --- 00:01Z: tool limits and the unknown-outcome detail -----------------------


def _sonnet_focus_prepared():
    settings = SimpleNamespace(openai_model=runtime.SONNET_MODEL, openai_reasoning="xhigh",
                               anthropic_api_key=SecretStr("test"), openai_timeout_seconds=900)
    return provider.prepare_message(
        settings, instructions="focus rules", input_text="focus evidence",
        schema={"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        max_tokens=runtime.SONNET_FOCUS_MAX_OUTPUT_TOKENS, job_type="market_focus",
        tools=runtime.claude_tools_for("market_focus", {}, model=runtime.SONNET_MODEL), output_mode="prompt_json",
    )


def _searches(count: int) -> list[dict]:
    blocks = []
    for index in range(count):
        blocks.append(_call("web_search", f"srv_{index}"))
        blocks.append(_search_result(f"srv_{index}") if index < 12 else {
            "type": "web_search_tool_result", "tool_use_id": f"srv_{index}",
            "content": {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"},
        })
    return [*blocks, {"type": "text", "text": "{}"}]


def _serve(monkeypatch, blocks):
    _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_tool_events(blocks)),
    ))


def test_the_first_search_past_max_uses_gets_the_provider_answer(monkeypatch):
    # The provider answers a 13th search with max_uses_exceeded itself; the
    # client used to abort the paid stream at that call, billing unknown.
    assert {tool["name"]: tool.get("max_uses") for tool in _sonnet_focus_prepared().params["tools"]}["web_search"] == 12
    _serve(monkeypatch, _searches(13))
    counts = {"web_search": 0, "web_fetch": 0, "code": 0, "total": 0}
    message = asyncio.run(provider.stream_message(_sonnet_focus_prepared(), tool_counts=counts))
    assert counts["web_search"] == 13
    assert provider.response_terminal_error(message) is None


def test_a_second_extra_search_still_stops_the_stream(monkeypatch):
    _serve(monkeypatch, _searches(14))
    with pytest.raises(RuntimeError, match="provider_tool_limit_exceeded"):
        asyncio.run(provider.stream_message(_sonnet_focus_prepared()))


@pytest.mark.parametrize(("failure", "detail"), [
    (RuntimeError("provider_tool_limit_exceeded"),
     "Claude stream outcome unknown (RuntimeError: provider_tool_limit_exceeded)"),
    (RuntimeError(), "Claude stream outcome unknown (RuntimeError)"),
])
def test_an_unknown_stream_outcome_names_the_failing_step(tmp_path, monkeypatch, failure, detail):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job, _payload, config = _focus_job(repo)
    calls = []

    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(prepared)
        await on_message_start(f"msg_{len(calls)}")
        if len(calls) == 2:
            raise failure
        return _round("msg_1")

    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, config, "owner"))
    row = repo.get_job(job["job_id"])
    assert row["error_code"] == "submission_outcome_unknown"
    assert row["error_detail"] == detail
    # The aborted request may have been billed: round one plus a full
    # reservation stay held, the 00:01Z shape (about $0.67 + $4.51 then).
    assert row["budget_charge_microusd"] > runtime.budget_reservation_microusd("market_focus", model=runtime.SONNET_MODEL)
