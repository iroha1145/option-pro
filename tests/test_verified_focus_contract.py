from __future__ import annotations

import copy
import json

import pytest

from app.services.ai_jobs.focus_verification import public_focus_result, public_focus_sources
from app.services.ai_jobs.models import (
    MarketFocusResult, VerifiedMarketFocusResult, result_model_for,
    validate_market_focus_evidence, validate_result,
)


def verified_payload_result():
    payload = {
        "cycle_id": "mfc_verified_test", "as_of": "2026-10-09T00:00:00Z", "input_hash": "a" * 64,
        "prepared_revision": 1, "verification_version": "web-evidence-v1",
        "allowed_event_group_ids": ["event_good", "event_bad"], "allowed_tickers": ["NVDA"],
        "events": [
            {"event_group_id": "event_good", "event_group_version": 2, "title_zh": "新产品发布"},
            {"event_group_id": "event_bad", "event_group_version": 1, "title_zh": "虚假并购"},
        ],
    }
    result = {
        "output_language": "zh-CN", "cycle_id": payload["cycle_id"], "as_of": payload["as_of"], "input_hash": payload["input_hash"],
        "title_zh": "虚假并购影响市场", "summary_zh": "虚假并购影响市场。", "headline_summary": "虚假并购影响市场。",
        "market_summary": "虚假并购影响市场。", "dominant_events": [], "market_uncertainties": ["虚假并购风险"],
        "affected_sectors": ["虚假行业"], "focus_ticker_assessments": [], "no_new_material_catalyst": False,
        "insufficient_context": False,
        "event_verifications": [
            {"event_group_id": "event_good", "event_group_version": 2, "verdict": "supported",
             "evidence_refs": [{"tool_use_id": "srv_good", "url": "https://www.nvidia.com/news", "relation": "supports"}],
             "title_zh": "新产品发布", "summary_zh": "公司发布新产品，交付进展仍需观察。", "affected_sectors": ["半导体"]},
            {"event_group_id": "event_bad", "event_group_version": 1, "verdict": "contradicted",
             "evidence_refs": [{"tool_use_id": "srv_bad", "url": "https://www.reuters.com/denial", "relation": "contradicts"}],
             "title_zh": "虚假并购", "summary_zh": "虚假并购消息已被否认。", "affected_sectors": ["虚假行业"]},
        ],
    }
    evidence = [
        {"tool_use_id": "srv_good", "tool_name": "web_fetch", "status": "success", "url": "https://www.nvidia.com/news", "title": "Product release", "content_sha256": "b" * 64},
        {"tool_use_id": "srv_bad", "tool_name": "web_search", "status": "success", "url": "https://www.reuters.com/denial", "title": "False merger", "content_sha256": "c" * 64},
    ]
    return payload, result, evidence


def test_new_contract_is_selected_only_by_explicit_payload_or_new_model():
    payload, result, evidence = verified_payload_result()
    assert result_model_for("market_focus", model="claude-sonnet-5-5") is VerifiedMarketFocusResult
    assert result_model_for("market_focus", model="claude-haiku-5-5") is MarketFocusResult
    assert result_model_for("market_focus", payload={}, model="claude-sonnet-5-5") is MarketFocusResult
    checked = validate_result("market_focus", json.dumps(result), payload)
    validate_market_focus_evidence(checked, payload, evidence)
    payload.pop("verification_version")
    result.pop("event_verifications")
    assert validate_result("market_focus", json.dumps(result), payload)["title_zh"] == result["title_zh"]


@pytest.mark.parametrize("change", ["missing", "duplicate", "version", "unknown", "missing_source", "fake_call", "code_only", "failed_tool", "missing_digest", "wrong_relation", "private_url"])
def test_invalid_verification_cannot_pass(change):
    payload, result, evidence = verified_payload_result()
    entry = result["event_verifications"][0]
    if change == "missing": result["event_verifications"].pop()
    elif change == "duplicate": result["event_verifications"].append(copy.deepcopy(entry))
    elif change == "version": entry["event_group_version"] += 1
    elif change == "unknown": entry["event_group_id"] = "not_input"
    elif change == "missing_source": evidence.clear()
    elif change == "fake_call": entry["evidence_refs"][0]["tool_use_id"] = "invented"
    elif change == "code_only": evidence[0]["tool_name"] = "code_execution"
    elif change == "failed_tool": evidence[0]["status"] = "error"
    elif change == "missing_digest": evidence[0].pop("content_sha256")
    elif change == "wrong_relation": entry["evidence_refs"][0]["relation"] = "contradicts"
    elif change == "private_url":
        evidence[0]["url"] = entry["evidence_refs"][0]["url"] = "http://127.0.0.1/private"
    with pytest.raises(ValueError):
        checked = validate_result("market_focus", json.dumps(result), payload)
        validate_market_focus_evidence(checked, payload, evidence)


def test_mixed_event_projection_discards_every_global_prose_and_bad_sources():
    payload, result, evidence = verified_payload_result()
    assessment = {"ticker": "NVDA", "catalyst_bias": 30, "confidence": 65, "horizon": "days",
                  "supporting_event_ids": ["event_good"], "conflicting_event_ids": ["event_bad"],
                  "summary": "虚假并购影响公司。", "risks": ["虚假并购风险"], "insufficient_evidence": False}
    result["focus_ticker_assessments"] = [assessment]
    checked = validate_result("market_focus", json.dumps(result), payload)
    validate_market_focus_evidence(checked, payload, evidence)
    public = public_focus_result(checked)
    assert "虚假" not in json.dumps(public, ensure_ascii=False)
    assert public["focus_ticker_assessments"] == []
    assert public["affected_sectors"] == ["半导体"]
    assert public_focus_sources(checked, evidence) == [{"title": "Product release", "url": "https://www.nvidia.com/news", "type": "web_fetch"}]


def test_tool_failure_can_produce_completed_empty_projection_without_fabricated_evidence():
    payload, result, _ = verified_payload_result()
    for event in result["event_verifications"]:
        event.update(verdict="unverifiable", evidence_refs=[])
    checked = validate_result("market_focus", json.dumps(result), payload)
    validate_market_focus_evidence(checked, payload, [])
    public = public_focus_result(checked)
    assert public["dominant_events"] == []
    assert public["no_new_material_catalyst"] is True
    assert public["summary_zh"] == "当前暂无可展示热点。"


def test_canonical_url_matching_does_not_require_model_to_repeat_fragments():
    payload, result, evidence = verified_payload_result()
    result["event_verifications"][0]["evidence_refs"][0]["url"] = "https://WWW.NVIDIA.COM/news#details"
    validate_market_focus_evidence(result, payload, evidence)
    assert len(public_focus_sources(result, evidence)) == 1


def test_null_server_id_resolves_only_from_unique_successful_receipt():
    payload, result, evidence = verified_payload_result()
    ref = result["event_verifications"][0]["evidence_refs"][0]
    ref.update(tool_use_id=None, url="https://WWW.NVIDIA.COM/news#details")
    checked = validate_result("market_focus", json.dumps(result), payload)
    validate_market_focus_evidence(checked, payload, evidence)
    assert checked["event_verifications"][0]["evidence_refs"][0] == {
        "tool_use_id": "srv_good", "url": "https://www.nvidia.com/news", "relation": "supports",
    }
    assert ref["tool_use_id"] is None  # Original model receipt remains unchanged.


@pytest.mark.parametrize("failure", ["ambiguous", "absent", "explicit_wrong"])
def test_null_id_resolution_cannot_guess_or_repair_false_id(failure):
    payload, result, evidence = verified_payload_result()
    ref = result["event_verifications"][0]["evidence_refs"][0]
    ref["tool_use_id"] = None
    if failure == "ambiguous":
        evidence.append({**evidence[0], "tool_use_id": "another_successful_call"})
    elif failure == "absent":
        evidence.pop(0)
    else:
        ref["tool_use_id"] = "explicitly_wrong"
    with pytest.raises(ValueError):
        validate_market_focus_evidence(result, payload, evidence)
