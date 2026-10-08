"""Offline regressions for complete but empty paid market-focus responses."""
import asyncio
import json

import pytest

from app.services.ai_jobs import runtime, worker
from app.services.ai_jobs.repository import AIJobRepository
from test_claude_job_worker import install_stream, message, settings


@pytest.fixture
def focus_payload():
    # Public structural input only; no production news or private account content.
    return {
        "cycle_id": "mfc_local_output_regression", "as_of": "2026-10-08T15:49:39Z",
        "input_hash": "a" * 64, "allowed_event_group_ids": [], "allowed_tickers": [],
        "no_new_material_catalyst": True,
    }


def valid_result(payload):
    return {
        "output_language": "zh-CN",
        **{key: payload[key] for key in ("cycle_id", "as_of", "input_hash")},
        "title_zh": "暂无新的重要催化事件",
        "summary_zh": "本次快照未提供新的重要事件，现有证据不足以判断市场方向。",
        "headline_summary": "输入未提供可引用的新闻事件，因此无法归纳新闻驱动因素。",
        "market_summary": "输入未提供行情变化，无法评估事件与市场表现的关系。",
        "dominant_events": [], "market_uncertainties": ["缺少行情与事件证据。"],
        "affected_sectors": [], "focus_ticker_assessments": [],
        "no_new_material_catalyst": True, "insufficient_context": True,
    }


def test_focus_request_uses_prompt_json_and_new_identity(tmp_path, focus_payload):
    config = settings(tmp_path / "jobs.db")
    prepared = runtime.prepare_claude(config, "market_focus", focus_payload)
    assert prepared.params["output_config"] == {"effort": "xhigh"}
    system = prepared.params["system"][0]
    assert system["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    from app.services.ai_jobs.claude_provider import _PROMPT_JSON_INSTRUCTIONS
    schema = json.loads(system["text"].split(_PROMPT_JSON_INSTRUCTIONS)[1])
    for name in ("cycle_id", "as_of", "input_hash"):
        assert "逐字复制本次输入" in schema["properties"][name]["description"]
        assert name in schema["required"]
    for name in ("title_zh", "summary_zh", "headline_summary", "market_summary"):
        assert "有实质内容的非空简体中文" in schema["properties"][name]["description"]
        assert name in schema["required"]
    assert focus_payload["cycle_id"] in prepared.params["messages"][0]["content"][0]["text"]
    assert prepared.params["max_tokens"] == 65536
    assert prepared.params["thinking"] == {"type": "adaptive"}
    assert prepared.params["tools"] == runtime.claude_tools_for("market_focus", focus_payload)
    assert runtime.schema_identity("market_focus", model=config.openai_model)[1] != (
        "dd5633bfa3b7683ca6c455b4ca4a4c611dec4d34baa2a8c33ff90ced7805ae8b"
    )


@pytest.mark.parametrize("identity_present", [False, True])
def test_empty_end_turn_receipt_stays_failed_and_billed(tmp_path, monkeypatch, focus_payload, identity_present):
    repo = AIJobRepository(tmp_path / "jobs.db")
    version, digest = runtime.schema_identity("market_focus")
    job, _ = repo.create_job(
        job_type="market_focus", payload=focus_payload, model="claude-haiku-5-5",
        reasoning="xhigh", execution_mode="background",
        prompt_version=runtime.PROMPT_VERSIONS["market_focus"],
        schema_version=version, schema_sha256=digest, max_queued=10,
    )
    empty = valid_result(focus_payload)
    for field in ("cycle_id", "as_of", "input_hash", "title_zh", "summary_zh", "headline_summary", "market_summary"):
        empty[field] = ""
    if identity_present:
        empty.update({key: focus_payload[key] for key in ("cycle_id", "as_of", "input_hash")})
    empty.update(no_new_material_catalyst=False, insufficient_context=False, market_uncertainties=[])
    response = message(text=json.dumps(empty), stop_reason="end_turn")
    calls = install_stream(monkeypatch, response)
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "owner")) == 1
    row = repo.get_job(job["job_id"])
    assert row["status"] == "failed"
    assert row["error_code"] == "schema_validation_failed"
    assert row["result_json"] is None
    receipt = repo.get_provider_result(job["job_id"])
    assert receipt["stop_reason"] == "end_turn" and receipt["terminal_error"] is None
    assert receipt["usage"]["total_tokens"] == 700
    assert row["budget_charge_microusd"] == 88
    assert "schema_validation_failed" not in runtime.SCHEDULED_TRANSIENT_AI_ERRORS
    assert asyncio.run(worker.run_once(repo, settings(repo.path), "later")) == 0
    assert len(calls) == 1


def test_substantive_fixture_validates_without_loosening_binding(focus_payload):
    result = valid_result(focus_payload)
    assert runtime.validate_result("market_focus", json.dumps(result), focus_payload) == result
    for field, value, error in (
        ("cycle_id", "different", "market_focus_cycle_mismatch"),
        ("as_of", "2026-10-08T15:50:39Z", "market_focus_as_of_mismatch"),
        ("input_hash", "b" * 64, "market_focus_input_hash_mismatch"),
        ("summary_zh", "English only", None),
    ):
        with pytest.raises(ValueError, match=error):
            runtime.validate_result("market_focus", json.dumps({**result, field: value}), focus_payload)


def test_focus_prompt_prefix_keeps_input_and_reservation_bounds(tmp_path, focus_payload):
    config = settings(tmp_path / "jobs.db")
    prepared = runtime.prepare_claude(config, "market_focus", focus_payload)
    other = runtime.prepare_claude(config, "market_focus", {**focus_payload, "cycle_id": "another-cycle"})
    assert prepared.params["system"] == other.params["system"]
    request = runtime.build_runtime_request("market_focus", focus_payload)
    framing = len(request.input_text.encode()) - runtime.untrusted_json_size(focus_payload)
    size = (len(prepared.params["system"][0]["text"].encode()) + framing
            + len(json.dumps(prepared.params["tools"], ensure_ascii=False).encode())
            + runtime._MAX_UNTRUSTED_JSON_BYTES)
    bound = runtime.max_input_tokens_for("market_focus", model=config.openai_model)
    assert size < bound
    assert bound + prepared.params["max_tokens"] < runtime.CLAUDE_TOOL_TOKEN_RESERVATION
    assert runtime.token_reservation("market_focus", model=config.openai_model) == 1_000_000


def test_nonempty_synthetic_snapshot_preserves_evidence_bindings(tmp_path, focus_payload):
    # Entirely synthetic public-style data, not a production snapshot or factual report.
    payload = {
        **focus_payload, "no_new_material_catalyst": False,
        "allowed_event_group_ids": ["synthetic-event-1"], "allowed_tickers": ["AAPL"],
        "event_groups": [{
            "event_group_id": "synthetic-event-1", "tickers": ["AAPL"],
            "summary": "模拟公告：公司将于下周举行产品发布会，尚未披露销售指引。",
            "published_at": "2026-10-08T15:00:00Z",
        }],
        "market_state": {"as_of": focus_payload["as_of"], "quotes": [
            {"ticker": "AAPL", "price": 200.0, "change_percent": 0.5},
        ]},
    }
    prepared = runtime.prepare_claude(settings(tmp_path / "jobs.db"), "market_focus", payload)
    text = prepared.params["messages"][0]["content"][0]["text"]
    snapshot = json.loads(text.removeprefix("<untrusted_market_focus_snapshot>").removesuffix("</untrusted_market_focus_snapshot>"))
    assert snapshot == payload
    result = {
        **valid_result(payload), "no_new_material_catalyst": False,
        "title_zh": "产品发布安排进入观察范围",
        "summary_zh": "模拟公告提供了发布会时间，但尚无销售指引，无法评估收入影响。",
        "headline_summary": "输入公告称公司下周举行产品发布会，具体产品与商业影响尚不清楚。",
        "market_summary": "模拟行情显示股价上涨百分之零点五，现有证据不能确认与公告存在因果关系。",
        "dominant_events": [{"event_group_id": "synthetic-event-1", "summary": "产品发布会将于下周举行。", "affected_sectors": ["消费电子"]}],
        "market_uncertainties": ["尚未披露产品细节与销售指引。"],
        "affected_sectors": ["消费电子"],
        "focus_ticker_assessments": [{
            "ticker": "AAPL", "catalyst_bias": None, "confidence": 20, "horizon": "uncertain",
            "supporting_event_ids": ["synthetic-event-1"], "conflicting_event_ids": [],
            "summary": "发布安排提供了观察时间，但不足以判断业绩影响方向。",
            "risks": ["产品细节与销售预期尚未披露。"], "insufficient_evidence": True,
        }],
    }
    assert runtime.validate_result("market_focus", json.dumps(result), payload) == result
    for field, replacement, error in (
        ("dominant_events", [{**result["dominant_events"][0], "event_group_id": "unlisted-event"}], "market_focus_event_binding_mismatch"),
        ("focus_ticker_assessments", [{**result["focus_ticker_assessments"][0], "ticker": "MSFT"}], "market_focus_ticker_binding_mismatch"),
    ):
        with pytest.raises(ValueError, match=error):
            runtime.validate_result("market_focus", json.dumps({**result, field: replacement}), payload)
