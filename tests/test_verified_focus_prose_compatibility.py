from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path

import pytest

from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import (
    RESULT_VALIDATION_CONTRACT_VERSION,
    VerifiedMarketFocusResult,
    _translate_verified_focus_prose,
    validate_market_focus_evidence,
    validate_result,
)
from test_verified_focus_contract import verified_payload_result


def fixture():
    payload, result, evidence = verified_payload_result()
    result["dominant_events"] = [{"event_group_id": "event_good", "summary": "公司发布产品。", "affected_sectors": ["半导体"]}]
    result["focus_ticker_assessments"] = [{
        "ticker": "NVDA", "catalyst_bias": 25, "confidence": 65, "horizon": "days",
        "supporting_event_ids": ["event_good"], "conflicting_event_ids": [],
        "summary": "公司发布新产品。", "risks": ["交付进度仍需观察。"], "insufficient_evidence": False,
    }]
    return payload, result, evidence


PROSE_PATHS = [
    ("title_zh",), ("summary_zh",), ("headline_summary",), ("market_summary",),
    ("market_uncertainties", 0), ("affected_sectors", 0),
    ("dominant_events", 0, "summary"), ("dominant_events", 0, "affected_sectors", 0),
    ("focus_ticker_assessments", 0, "summary"), ("focus_ticker_assessments", 0, "risks", 0),
    ("event_verifications", 0, "title_zh"), ("event_verifications", 0, "summary_zh"),
    ("event_verifications", 0, "affected_sectors", 0),
]


def put(value, path, text):
    for part in path[:-1]:
        value = value[part]
    value[path[-1]] = text


def get(value, path):
    for part in path:
        value = value[part]
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("path", PROSE_PATHS)
@pytest.mark.parametrize(("text", "expected"), [
    ("截至as_of，事件仍待公布。", "截至分析截止时点，事件仍待公布。"),
    ("catalyst_bias只描述证据倾向。", "催化因素倾向评分只描述证据倾向。"),
    ("带宽分别为8Gbps和100Gbps。", "带宽分别为8吉比特每秒和100吉比特每秒。"),
    ("主要分类。(6)公司类事件：公司已披露合作。", "主要分类。（六）公司类事件：公司已披露合作。"),
])
def test_each_known_family_is_localized_only_on_explicit_prose_paths(path, text, expected):
    payload, raw, evidence = fixture()
    put(raw, path, text)
    before = copy.deepcopy((payload, raw, evidence))
    normalized = validate_result("market_focus", json.dumps(raw), payload)
    validate_market_focus_evidence(normalized, payload, evidence)
    assert get(normalized, path) == expected
    assert (payload, raw, evidence) == before
    assert normalized["cycle_id"] == raw["cycle_id"]
    assert normalized["as_of"] == raw["as_of"]
    assert normalized["input_hash"] == raw["input_hash"]
    assert normalized["focus_ticker_assessments"][0]["catalyst_bias"] == 25
    assert [e["evidence_refs"] for e in normalized["event_verifications"]] == [e["evidence_refs"] for e in raw["event_verifications"]]


@pytest.mark.parametrize("text", [
    "as_of股票上涨。", "catalyst_bias代码上涨。", "Gbps股票上涨。",
    "8Gbps股票上涨。", "股票代码为8Gbps。", "代码为catalyst_bias。",
    "as_of公司股价上涨。", "证券as_of涨停。", "(700)公司宣布交易。",
    "(6)公司宣布交易。", "(6)公司类事件股份上涨。", "股票(6)公司类事件：上涨。",
    "截至as_of，市场 will strongly improve。", "截至as_of，市場仍待確認。",
    "传输速率8GbpsExtra已公布。", "截至as_of_extra，事件仍待公布。",
    "未知cycle_id显示事件仍待公布。", "截至as_of，ZZZZ股票上涨。",
])
def test_english_security_identifiers_and_unbound_codes_still_fail(text):
    payload, raw, _ = fixture()
    raw["summary_zh"] = text
    with pytest.raises(ValueError):
        validate_result("market_focus", json.dumps(raw), payload)


@pytest.mark.parametrize("text", [
    "as_of股票上涨。", "catalyst_bias代码上涨。", "Gbps股票上涨。",
    "8Gbps股票上涨。", "股票代码为8Gbps。", "代码为catalyst_bias。",
    "as_of公司股价上涨。", "(700)公司宣布交易。", "(6)公司宣布交易。",
    "(6)公司类事件股份上涨。", "股票(6)公司类事件：上涨。",
    "字段prefix_as_of和catalyst_bias_extra未定义。",
    "链接https://www.reuters.com/as_of/8Gbps?catalyst_bias=25保持不变。",
    "链接https://www.reuters.com/新闻。as_of；8Gbps?catalyst_bias=25 保持不变。",
])
def test_protected_contexts_are_not_rewritten(text):
    assert _translate_verified_focus_prose(text) == text


def test_valid_output_and_protected_values_are_unchanged():
    payload, raw, evidence = fixture()
    raw["headline_summary"] = "（六）公司类事件：仅描述已核实事实。"
    source_url = "https://www.nvidia.com/as_of/100Gbps?catalyst_bias=25"
    raw["event_verifications"][0]["evidence_refs"][0]["url"] = source_url
    evidence[0]["url"] = source_url
    payload["events"][0]["source_snapshot"] = {"raw_title": "source as_of catalyst_bias 8Gbps", "source_url": source_url}
    before = copy.deepcopy((payload, raw, evidence))
    normalized = validate_result("market_focus", json.dumps(raw), payload)
    validate_market_focus_evidence(normalized, payload, evidence)
    assert normalized == raw
    assert (payload, raw, evidence) == before


def test_numeric_quantities_dates_and_scalar_fields_are_not_recomputed():
    payload, raw, _ = fixture()
    raw["summary_zh"] = "速率为0.8Gbps，报价42.36元，比例0.8%，日期为2026年10月9日。"
    normalized = validate_result("market_focus", json.dumps(raw), payload)
    assert normalized["summary_zh"] == "速率为0.8吉比特每秒，报价42.36元，比例0.8%，日期为2026年10月9日。"
    assert normalized["focus_ticker_assessments"][0]["catalyst_bias"] == 25
    assert normalized["focus_ticker_assessments"][0]["confidence"] == 65


@pytest.mark.parametrize(("path", "value"), [
    (("as_of",), "as_of"), (("as_of",), "2026-99-99T00:00:00Z"),
    (("focus_ticker_assessments", 0, "ticker"), "catalyst_bias"),
    (("focus_ticker_assessments", 0, "catalyst_bias"), "25"),
    (("event_verifications", 0, "verdict"), "as_of"),
])
def test_structured_identity_and_numeric_errors_are_not_fixed_by_prose_normalizer(path, value):
    payload, raw, _ = fixture()
    put(raw, path, value)
    before = copy.deepcopy(raw)
    assert get(VerifiedMarketFocusResult.translate_known_prose(raw), path) == value
    with pytest.raises(ValueError):
        validate_result("market_focus", json.dumps(raw), payload)
    assert raw == before


def test_unknown_fields_are_not_recursively_normalized_or_allowed():
    payload, raw, _ = fixture()
    raw["unexpected"] = {"summary": "as_of与catalyst_bias以及8Gbps均不得改写。"}
    before = copy.deepcopy(raw)
    translated = VerifiedMarketFocusResult.translate_known_prose(raw)
    assert translated["unexpected"] == before["unexpected"]
    with pytest.raises(ValueError):
        validate_result("market_focus", json.dumps(raw), payload)
    assert raw == before


def test_historical_focus_contract_does_not_gain_new_normalization():
    payload, raw, _ = fixture()
    payload.pop("verification_version")
    raw.pop("event_verifications")
    raw["summary_zh"] = "截至as_of，事件仍待公布。"
    with pytest.raises(ValueError):
        validate_result("market_focus", json.dumps(raw), payload)
    assert RESULT_VALIDATION_CONTRACT_VERSION == "simplified-chinese-v4"


def test_prompt_clarifications_are_only_for_verified_sonnet_focus():
    sonnet = runtime.build_runtime_request("market_focus", {}, model=runtime.SONNET_MODEL).instructions
    haiku = runtime.build_runtime_request("market_focus", {}, model=runtime.OFFICIAL_CLAUDE_MODEL).instructions
    luna = runtime.build_runtime_request("news_impact", {}, model=runtime.LUNA_MODEL).instructions
    for rule in ("分析截止时点", "吉比特每秒", "（六）公司类事件", "核心主体、时间、数值和统计口径", "另一个较弱事实"):
        assert rule in sonnet
        assert rule not in haiku
        assert rule not in luna


@pytest.mark.skipif(not os.environ.get("SONNET_SAVED_RECEIPT_PATH"), reason="Optional local saved receipt; no production fixture is checked in")
def test_local_saved_receipt_replays_structure_without_mutating_receipt_bindings_or_fees():
    path = Path(os.environ["SONNET_SAVED_RECEIPT_PATH"])
    before_bytes = path.read_bytes()
    job = json.loads(before_bytes)
    before_job = digest(job)
    payload = job["payload_json"]
    receipt = job["provider_result_json"]
    raw = json.loads(receipt["output_text"])

    def protected(result):
        return {
            "cycle_id": result["cycle_id"], "as_of": result["as_of"], "input_hash": result["input_hash"],
            "events": [(e["event_group_id"], e["event_group_version"], e["verdict"], e["evidence_refs"]) for e in result["event_verifications"]],
            "assessments": [{k: v for k, v in item.items() if k not in {"summary", "risks"}} for item in result["focus_ticker_assessments"]],
        }

    normalized = validate_result("market_focus", receipt["output_text"], payload)
    assert digest(protected(normalized)) == digest(protected(raw))
    # The existing evidence validator resolves null server IDs from unique
    # source URLs. That separate projection never changes the stored receipt.
    bound = copy.deepcopy(normalized)
    validate_market_focus_evidence(bound, payload, receipt["tool_evidence"])
    actual_pairs = {(e["tool_use_id"], e["url"]) for e in receipt["tool_evidence"] if e["status"] == "success"}
    old_refs = [ref for e in normalized["event_verifications"] for ref in e["evidence_refs"]]
    new_refs = [ref for e in bound["event_verifications"] for ref in e["evidence_refs"]]
    assert len(old_refs) == len(new_refs)
    for old, new in zip(old_refs, new_refs, strict=True):
        assert digest((old["url"], old["relation"])) == digest((new["url"], new["relation"]))
        assert old["tool_use_id"] is None or digest(old["tool_use_id"]) == digest(new["tool_use_id"])
        assert (new["tool_use_id"], new["url"]) in actual_pairs
    assert digest(job) == before_job  # Includes original charge, usage, output and tool receipts.
    assert hashlib.sha256(path.read_bytes()).digest() == hashlib.sha256(before_bytes).digest()
    # Passing this test proves format and provenance compatibility, not factual
    # correctness. It deliberately never publishes or changes any verdict.
