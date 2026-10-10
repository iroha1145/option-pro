"""Verified hotspot reads stop re-checking the same paid result (2026-10-10).

After #244 went live, every read of /api/catalysts/hotspots ran the full
Chinese check over the cycle's paid result again: production measured 1.6–1.8 s
per read, 2.2 s of a 2.4 s profile inside validate_result. The local store here
carries the production cycle of 2026-10-10 (tests/fixtures/
market_focus_cycle_20261010.json): 20 events, three 2,311-character texts,
8 dominant events and 6 stock assessments.
"""

from __future__ import annotations

import copy
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import models as models_module
from app.services.ai_jobs import repository as repository_module
from app.services.ai_jobs import runtime
from app.services.ai_jobs.models import validate_result
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts import personal_service as personal_module
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from test_catalyst_local_intelligence import _apply_news, _news_change, _stack
from test_market_focus_concise_20261010 import LEGACY_PROMPT, _rewrite_identity, production_payload_result
from test_personal_catalyst_service import _service


PRODUCTION_TICKERS = ("AAPL", "NVDA", "SBUX", "T", "TMUS", "VZ")
HEADLINES = (
    "Apple unveils redesigned iPhone lineup at autumn launch",
    "Starbucks names new chief operating officer amid turnaround",
    "Verizon expands fiber footprint across Northeast markets",
    "Rural coverage auction awarded to wireless challenger",
    "Telecom giant raises dividend after subscriber growth",
    "Nvidia supplier warns of packaging capacity constraints",
    "Federal regulators review satellite broadband merger proposal",
    "Coffee bean futures jump on Brazilian frost concerns",
    "Smartphone shipments rebound in emerging Asian economies",
    "Cell tower landlords slide after spectrum sale announcement",
    "Cloud computing demand lifts data center construction spending",
    "Retail investors pile into telecom exchange traded funds",
    "Semiconductor equipment orders climb for third straight month",
    "Wireless carriers trim promotional discounts before holidays",
    "Labor union reaches tentative contract with coffee chain baristas",
    "Streaming bundle price increases test consumer loyalty",
    "Chipmaker earnings preview highlights artificial intelligence backlog",
    "Broadband subsidy program faces funding shortfall in Congress",
    "App store commission ruling prompts developer pricing changes",
    "Mobile network outage disrupts emergency calls in several states",
)


@pytest.fixture(autouse=True)
def _owner_request_context():
    with request_owner_access_context(True):
        yield


def complete_production(ai, cycle) -> dict:
    """Complete the cycle's job with the production result, mapped onto its events."""

    row = ai.get_job(cycle["job_id"])
    payload = json.loads(row["payload_json"])
    _, production = production_payload_result()
    events = payload["events"]
    verifications = production["event_verifications"]
    assert len(events) == len(verifications) == 20
    ids = {entry["event_group_id"]: event["event_group_id"] for entry, event in zip(verifications, events)}
    result = copy.deepcopy(production)
    result.update(cycle_id=payload["cycle_id"], as_of=payload["as_of"], input_hash=payload["input_hash"])
    evidence = []
    for entry, event in zip(result["event_verifications"], events):
        entry["event_group_id"] = event["event_group_id"]
        entry["event_group_version"] = event["event_group_version"]
        for ref in entry["evidence_refs"]:
            evidence.append({
                "tool_use_id": ref["tool_use_id"], "tool_name": "web_fetch", "status": "success",
                "url": ref["url"], "title": "Published source", "content_sha256": "b" * 64,
            })
    for event in result["dominant_events"]:
        event["event_group_id"] = ids[event["event_group_id"]]
    for assessment in result["focus_ticker_assessments"]:
        assessment["supporting_event_ids"] = [ids[value] for value in assessment["supporting_event_ids"]]
        assessment["conflicting_event_ids"] = [ids[value] for value in assessment["conflicting_event_ids"]]
    result = validate_result("market_focus", json.dumps(result, ensure_ascii=False), payload)
    usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10,
             "reasoning_tokens": 0, "total_tokens": 110,
             "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
             "cache_creation_1h_input_tokens": 0}
    receipt = {"provider": "anthropic", "model": "claude-sonnet-5-5", "id": "msg_" + cycle["cycle_id"],
               "output_text": json.dumps(result, ensure_ascii=False, indent=1), "stop_reason": "end_turn",
               "terminal_error": None, "usage": usage, "evidence_sources": [],
               "tool_evidence_version": "v1", "tool_evidence": evidence}
    owner = "owner_" + cycle["cycle_id"]
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == row["job_id"]
    assert ai.mark_submission_started(row["job_id"], owner, daily_limit=4) == "started"
    ai.record_provider_result(row["job_id"], owner, receipt)
    ai.complete(row["job_id"], owner, result, usage)
    return result


@pytest.fixture
def production_cycle(tmp_path, monkeypatch):
    """Sonnet verification on, one verified cycle carrying the production result.

    The production job ran before the concise contract (prompt v6), so the job
    keeps that identity and its historical text limits.
    """

    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now]
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock[0])
    monkeypatch.setattr(repository_module, "_utcnow", lambda: clock[0])
    monkeypatch.setattr(local_module, "macro_conditions_context", lambda: None)
    etl, ai, original = _stack(tmp_path, canonical_tickers=PRODUCTION_TICKERS)
    intelligence = LocalCatalystIntelligence(
        original.db_path, ai, mode="manual", canonical_tickers=PRODUCTION_TICKERS,
        news_model="gpt-5.6-luna", news_reasoning="max",
        focus_model="claude-sonnet-5-5", focus_reasoning="xhigh",
    )
    intelligence.initialize()
    changes = []
    for index, headline in enumerate(HEADLINES):
        change = _news_change(
            index + 1, 101 + index, available_at=now - timedelta(minutes=30 - index),
            title=headline, summary=" ".join([f"{headline}."] * 4), sources=("Reuters", "Bloomberg"),
            tickers=(PRODUCTION_TICKERS[index % len(PRODUCTION_TICKERS)],),
        )
        change["news"]["url"] = f"https://www.reuters.com/news/{101 + index}"
        changes.append(change)
    _apply_news(etl, changes, as_of=now - timedelta(minutes=5))
    revision = intelligence.reconcile()["prepared_revision"]
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    _rewrite_identity(ai, cycle["job_id"], LEGACY_PROMPT, runtime.LEGACY_VERIFIED_FOCUS_IDENTITY)
    complete_production(ai, cycle)
    intelligence.reconcile()
    return ai, intelligence, cycle, clock


def counting_validator(monkeypatch, module) -> list[str]:
    """Count real validations behind ``module.validate_result`` from a cold cache."""

    calls: list[str] = []
    original = models_module.validate_result

    def counting(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(module, "validate_result", counting)
    models_module._result_verdicts.clear()
    return calls


def as_visitor(read, **kwargs):
    with request_owner_access_context(False):
        return read(**kwargs)


def test_second_hotspot_read_reuses_the_checked_paid_result(production_cycle, monkeypatch):
    ai, intelligence, _cycle, clock = production_cycle
    service = _service("manual", engine=intelligence, repository=ai)
    calls = counting_validator(monkeypatch, local_module)

    first = as_visitor(service.hotspots, limit=8, now=clock[0])
    cold = len(calls)
    second = as_visitor(service.hotspots, limit=8, now=clock[0])

    # The stored result and the receipt's output text are different bytes;
    # each is checked once per process, and never again on later reads.
    assert cold == 2
    assert len(calls) == cold
    assert second == first
    assert first["status"] == "active"
    assert len(first["items"]) == 8
    assert {item["verification_status"] for item in first["items"]} == {"verified"}
    _, production = production_payload_result()
    supported = {entry["title_zh"] for entry in production["event_verifications"] if entry["verdict"] == "supported"}
    assert {item["representative_title"] for item in first["items"]} <= supported


@pytest.mark.parametrize("change", ["reformatted", "edited"])
def test_changed_paid_result_bytes_are_checked_again(production_cycle, monkeypatch, change):
    ai, intelligence, cycle, clock = production_cycle
    service = _service("manual", engine=intelligence, repository=ai)
    calls = counting_validator(monkeypatch, local_module)
    before = as_visitor(service.hotspots, limit=8, now=clock[0])
    as_visitor(service.hotspots, limit=8, now=clock[0])
    assert len(calls) == 2
    stored = json.loads(ai.get_job(cycle["job_id"])["result_json"])
    if change == "edited":
        stored["event_verifications"][0]["summary_zh"] = "这是未付费核实的新摘要。"
    with ai._connect() as connection:
        connection.execute(
            "UPDATE ai_jobs SET result_json=? WHERE job_id=?",
            (json.dumps(stored, ensure_ascii=False, indent=2), cycle["job_id"]),
        )
        connection.commit()

    after = as_visitor(service.hotspots, limit=8, now=clock[0])

    # New bytes miss the cache and are checked; the unchanged receipt is not.
    assert len(calls) == 3
    if change == "reformatted":
        assert after == before
    else:
        # A remembered acceptance never stands in for edited bytes.
        assert after["items"] == []
        assert after["status"] == "empty"


def test_focus_cycle_poll_checks_each_paid_result_once(production_cycle, monkeypatch):
    ai, intelligence, cycle, clock = production_cycle
    service = _service("manual", engine=intelligence, repository=ai)
    calls = counting_validator(monkeypatch, personal_module)

    first = as_visitor(service.latest_market_focus_cycle, now=clock[0])
    cold = len(calls)
    second = as_visitor(service.latest_market_focus_cycle, now=clock[0])

    # One poll projects the current cycle twice, as the cycle and as the latest
    # successful one: its result is checked once, and not again on the next poll.
    assert first["cycle"]["cycle_id"] == first["latest_successful_cycle"]["cycle_id"] == cycle["cycle_id"]
    assert first["cycle"]["result"] is not None
    assert cold == 1
    assert len(calls) == cold
    assert second == first
