from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import repository as repository_module
from app.services.ai_jobs.models import validate_result
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from app.services.catalysts.personal_service import PersonalCatalystService
from test_catalyst_local_intelligence import _apply_news, _delete_change, _news_change, _stack
from test_verified_focus_contract import verified_payload_result


@pytest.fixture(autouse=True)
def owner_access():
    with request_owner_access_context(True):
        yield


@pytest.fixture
def verified_stack(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now]
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock[0])
    monkeypatch.setattr(repository_module, "_utcnow", lambda: clock[0])
    monkeypatch.setattr(local_module, "macro_conditions_context", lambda: None)
    etl, ai, original = _stack(tmp_path)
    intelligence = LocalCatalystIntelligence(
        original.db_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"),
        news_model="gpt-5.6-luna", news_reasoning="max",
        focus_model="claude-sonnet-5-5", focus_reasoning="xhigh",
    )
    intelligence.initialize()
    changes = [
        _news_change(1, 11, available_at=now - timedelta(minutes=10),
                     title="Nvidia launches Blackwell chip platform", sources=("Reuters", "Bloomberg")),
        _news_change(2, 12, available_at=now - timedelta(minutes=9),
                     title="Regulator rejects pharmaceutical merger proposal", tickers=("AMD",), sources=("Reuters", "CNBC")),
    ]
    for change in changes:
        change["news"]["url"] = "https://www.reuters.com/news/" + str(change["news_id"])
    _apply_news(etl, changes, as_of=now - timedelta(minutes=8))
    revision = intelligence.reconcile()["prepared_revision"]
    return etl, ai, intelligence, revision, clock


def complete_verified(ai, cycle, *, verdicts=None, evidence_missing=False, claimed_owner=None, null_ids=False):
    row = ai.get_job(cycle["job_id"])
    payload = json.loads(row["payload_json"])
    _, result, _ = verified_payload_result()
    result.update(cycle_id=payload["cycle_id"], as_of=payload["as_of"], input_hash=payload["input_hash"])
    result["event_verifications"] = []
    evidence = []
    for index, event in enumerate(payload["events"]):
        verdict = (verdicts or ["supported", "contradicted"])[index % len(verdicts or ["supported", "contradicted"])]
        refs = []
        if verdict != "unverifiable":
            refs = [{"tool_use_id": f"srv_{index}", "url": f"https://www.reuters.com/proof/{index}",
                     "relation": "supports" if verdict == "supported" else "contradicts"}]
            evidence.append({"tool_use_id": f"srv_{index}", "tool_name": "web_fetch", "status": "success",
                             "url": refs[0]["url"], "title": "Published source" if verdict == "supported" else "False merger",
                             "content_sha256": "b" * 64})
        result["event_verifications"].append({
            "event_group_id": event["event_group_id"], "event_group_version": event["event_group_version"],
            "verdict": verdict, "evidence_refs": refs,
            "title_zh": "新产品发布" if verdict == "supported" else "虚假并购",
            "summary_zh": "公司发布新产品，交付进展仍需观察。" if verdict == "supported" else "虚假并购消息已被否认。",
            "affected_sectors": ["半导体"] if verdict == "supported" else ["虚假行业"],
        })
    result = validate_result("market_focus", json.dumps(result), payload)
    usage = {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10,
             "reasoning_tokens": 0, "total_tokens": 110,
             "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
             "cache_creation_1h_input_tokens": 0}
    if null_ids:
        for event in result["event_verifications"]:
            for ref in event["evidence_refs"]:
                ref["tool_use_id"] = None
    receipt = {"provider": "anthropic", "model": "claude-sonnet-5-5", "id": "msg_" + cycle["cycle_id"],
               "output_text": json.dumps(result, ensure_ascii=False), "stop_reason": "end_turn", "terminal_error": None,
               "usage": usage, "evidence_sources": [], "tool_evidence_version": "v1",
               "tool_evidence": [] if evidence_missing else evidence}
    owner = claimed_owner or "owner_" + cycle["cycle_id"]
    if claimed_owner is None:
        claimed = ai.claim_due(owner, lease_seconds=60)
        assert claimed["job_id"] == row["job_id"]
        assert ai.mark_submission_started(row["job_id"], owner, daily_limit=4) == "started"
    ai.record_provider_result(row["job_id"], owner, receipt)
    if null_ids:
        from app.services.ai_jobs.models import validate_market_focus_evidence
        validate_market_focus_evidence(result, payload, evidence)
    ai.complete(row["job_id"], owner, result, usage)
    return result, receipt


def project_cycle(intelligence, ai, cycle_id):
    service = PersonalCatalystService.__new__(PersonalCatalystService)
    service.ai_repository = ai
    return service._project_focus_cycle(intelligence.market_focus_cycle(cycle_id))


def test_pending_is_not_public_then_mixed_result_publishes_atomically(verified_stack):
    _, ai, intelligence, revision, clock = verified_stack
    assert len(intelligence._prepared_hotspots(limit=20)["items"]) == 2
    assert intelligence.hotspots(limit=20)["items"] == []
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    row = ai.get_job(cycle["job_id"])
    payload = json.loads(row["payload_json"])
    assert row["model"] == "claude-sonnet-5-5"
    assert payload["verification_version"] == "web-evidence-v1"
    assert payload["events"][0]["source_snapshot"]["source_url"].startswith("https://www.reuters.com/")
    raw, receipt = complete_verified(ai, cycle)
    # The worker's result alone cannot publish prepared hotspots.
    assert intelligence.hotspots(limit=20)["items"] == []
    public_job = ai.public(ai.get_job(cycle["job_id"]))
    assert "虚假" not in json.dumps(public_job["result"], ensure_ascii=False)
    assert "False merger" not in json.dumps(public_job["evidence_sources"])
    intelligence.reconcile()
    hotspots = intelligence.hotspots(limit=20)
    assert len(hotspots["items"]) == 1
    assert hotspots["items"][0]["representative_title"] == "新产品发布"
    cycle_public = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert cycle_public["verification_status"] == "verified"
    assert "虚假" not in json.dumps(cycle_public, ensure_ascii=False)
    assert "event_verifications" not in json.dumps(cycle_public)
    # Raw receipt, original mixed prose and charges are retained internally.
    stored = ai.get_job(cycle["job_id"])
    assert json.loads(stored["result_json"]) == raw
    assert json.loads(stored["provider_result_json"]) == receipt
    assert stored["budget_charge_microusd"] > 0
    with intelligence._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_verified_focus_publications").fetchone()[0] == 1
    intelligence.reconcile()
    with intelligence._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_verified_focus_publications").fetchone()[0] == 1


def test_all_unverifiable_completes_and_does_not_fall_back(verified_stack):
    _, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["unverifiable"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    public = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert public["status"] == "completed"
    assert public["result"]["summary_zh"] == "当前暂无可展示热点。"
    assert public["evidence_sources"] == []


def test_missing_tool_receipt_cannot_publish_even_if_job_was_marked_complete(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    raw, _ = complete_verified(ai, cycle, evidence_missing=True)
    assert ai.public(ai.get_job(cycle["job_id"]))["result"] is None
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    public = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert public["result"] is None
    assert public["status"] == "failed"
    assert json.loads(ai.get_job(cycle["job_id"])["result_json"]) == raw


def test_changed_event_cannot_reuse_support_but_unchanged_event_remains(verified_stack):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"]
    clock[0] += timedelta(minutes=5)
    _apply_news(etl, [_news_change(3, 11, available_at=clock[0] - timedelta(seconds=5),
                                  title="Nvidia cancels Blackwell platform launch", sources=("Reuters", "Bloomberg"))], as_of=clock[0])
    new_revision = intelligence.reconcile()["prepared_revision"]
    assert new_revision > revision
    # The changed news cannot inherit its old verdict; the other original
    # event still has the exact same version and remains independently valid.
    remaining = intelligence.hotspots(limit=20)["items"]
    assert [item["representative_news_id"] for item in remaining] == [12]
    assert remaining[0]["prepared_revision"] == revision


def test_newer_rejection_withdraws_previous_support_and_keeps_history(verified_stack):
    _, ai, intelligence, revision, clock = verified_stack
    first = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, first, verdicts=["supported"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"]
    clock[0] += timedelta(minutes=2)
    second = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    complete_verified(ai, second, verdicts=["contradicted"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    assert project_cycle(intelligence, ai, first["cycle_id"])["result"]["dominant_events"]


def test_public_job_rejects_receipt_identity_mismatch(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle)
    row = ai.get_job(cycle["job_id"])
    row["anthropic_message_id"] = "msg_wrong"
    assert ai.public(row)["result"] is None


def test_anonymous_cycle_uses_same_verified_projection(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle)
    intelligence.reconcile()
    service = PersonalCatalystService.__new__(PersonalCatalystService)
    service.ai_repository = ai
    with request_owner_access_context(False):
        public = service._project_focus_cycle_for_access(intelligence.market_focus_cycle(cycle["cycle_id"]), include_owner_state=False)
        assert public["verification_status"] == "verified"
        assert "虚假" not in json.dumps(public, ensure_ascii=False)
        assert "_validation" not in json.dumps(public)
        assert len(intelligence.hotspots(limit=20)["items"]) == 1


def test_paid_haiku_news_and_legacy_cycle_survive_new_model_configuration(verified_stack):
    from test_catalyst_local_intelligence import _finish_job, _focus_result, _news_result
    _, ai, intelligence, revision, clock = verified_stack
    historical = LocalCatalystIntelligence(intelligence.db_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"))
    news_job = historical.request_analysis(11, force=False)
    assert news_job["model"] == "claude-haiku-5-5"
    _finish_job(ai, news_job["job_id"], _news_result(news_id=11, change_sequence=1, content_hash="hash-11-1"))
    historical.reconcile()
    prepared = historical.hotspot_status()["prepared_revision"]
    cycle = historical.request_market_focus_cycle(expected_prepared_revision=prepared)
    _finish_job(ai, cycle["job_id"], _focus_result(ai, cycle))
    historical.reconcile()
    original_result = ai.get_job(news_job["job_id"])["result_json"]
    intelligence.reconcile()
    assert intelligence.analysis_job(news_job["job_id"])["result"] is not None
    duplicate = intelligence.request_analysis(11, force=False)
    assert duplicate["job_id"] == news_job["job_id"]
    assert ai.get_job(news_job["job_id"])["result_json"] == original_result
    legacy_cycle = project_cycle(intelligence, ai, cycle["cycle_id"])
    assert legacy_cycle["verification_status"] == "legacy_unverified"
    assert legacy_cycle["result"] is not None
    assert intelligence.hotspots(limit=20)["items"] == []
    # A genuinely new news request uses the new type-specific model.
    new_job = intelligence.request_analysis(12, force=False)
    assert (new_job["model"], new_job["reasoning"]) == ("gpt-5.6-luna", "max")


def test_unsent_retired_haiku_job_is_replaced_by_scheduled_luna(verified_stack):
    _, ai, intelligence, _, clock = verified_stack
    historical = LocalCatalystIntelligence(intelligence.db_path, ai, mode="scheduled", canonical_tickers=("NVDA", "AMD"))
    old = historical.request_analysis(11, force=False, submission_source="scheduled")
    assert intelligence.request_analysis(11, force=False)["job_id"] == old["job_id"]
    owner = "retire_unsent_configuration"
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == old["job_id"]
    # Exact pre-submission transition used by the worker after route mismatch.
    ai.fail(old["job_id"], owner, "runtime_configuration_changed")
    assert ai.get_job(old["job_id"])["submission_started_at"] is None
    clock[0] += timedelta(hours=2)
    intelligence.mode = "scheduled"
    intelligence.reconcile()
    intelligence.run_scheduled(now=clock[0])
    with ai._connect() as connection:
        rows = connection.execute("SELECT * FROM ai_jobs WHERE job_type='news_impact' AND json_extract(payload_json,'$.news_id')=11 ORDER BY created_at").fetchall()
    assert len(rows) == 2
    assert (rows[0]["model"], rows[0]["status"]) == ("claude-haiku-5-5", "failed")
    assert (rows[1]["model"], rows[1]["reasoning"], rows[1]["status"]) == ("gpt-5.6-luna", "max", "pending")
    assert intelligence.request_analysis(11, force=False)["job_id"] == rows[1]["job_id"]


def test_sent_haiku_configuration_failure_is_never_scheduled_again(verified_stack):
    _, ai, intelligence, _, clock = verified_stack
    historical = LocalCatalystIntelligence(intelligence.db_path, ai, mode="scheduled", canonical_tickers=("NVDA", "AMD"))
    old = historical.request_analysis(11, force=False, submission_source="scheduled")
    owner = "sent_configuration_unknown"
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == old["job_id"]
    assert ai.mark_submission_started(old["job_id"], owner, daily_limit=4) == "started"
    ai.link_anthropic_message(old["job_id"], owner, "msg_paid_unknown")
    ai.fail(old["job_id"], owner, "runtime_configuration_changed")
    clock[0] += timedelta(hours=2)
    intelligence.mode = "scheduled"
    intelligence.reconcile()
    intelligence.run_scheduled(now=clock[0])
    with ai._connect() as connection:
        count = connection.execute("SELECT COUNT(*) FROM ai_jobs WHERE job_type='news_impact' AND json_extract(payload_json,'$.news_id')=11").fetchone()[0]
    assert count == 1


def test_unknown_paid_outcome_is_not_reissued_by_manual_request(verified_stack):
    _, ai, intelligence, _, _ = verified_stack
    historical = LocalCatalystIntelligence(intelligence.db_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"))
    old = historical.request_analysis(11, force=False)
    owner = "unknown_paid_outcome"
    assert ai.claim_due(owner, lease_seconds=60)["job_id"] == old["job_id"]
    assert ai.mark_submission_started(old["job_id"], owner, daily_limit=4) == "started"
    ai.fail(old["job_id"], owner, "submission_outcome_unknown")
    for force in (False, True):
        assert intelligence.request_analysis(11, force=force)["job_id"] == old["job_id"]
    with ai._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1


@pytest.mark.parametrize("openai_key,anthropic_key", [("", "anthropic-test"), ("openai-test", "")])
def test_news_and_focus_provider_readiness_are_independent(openai_key, anthropic_key):
    from pydantic import SecretStr
    from app.services.catalysts.errors import CatalystError
    from test_personal_catalyst_service import _service
    service = _service("manual")
    service.ai_settings.openai_news_model = "gpt-5.6-luna"
    service.ai_settings.openai_news_reasoning = "max"
    service.ai_settings.openai_market_focus_model = "claude-sonnet-5-5"
    service.ai_settings.openai_market_focus_reasoning = "xhigh"
    service.ai_settings.openai_api_key = SecretStr(openai_key)
    service.ai_settings.anthropic_api_key = SecretStr(anthropic_key)
    news = service.analysis_availability(job_type="news_impact")
    focus = service.hotspot_status()["analysis_availability"]
    assert news["configured"] is bool(openai_key)
    assert focus["configured"] is bool(anthropic_key)
    assert news["configured_by_job"] == {"news_impact": bool(openai_key), "market_focus": bool(anthropic_key)}
    if not openai_key:
        with pytest.raises(CatalystError, match="当前无法开始新闻分析"):
            service.request_analysis(101, force=False)
        service.request_market_focus_cycle(expected_prepared_revision=3)
    else:
        with pytest.raises(CatalystError, match="当前无法开始热点分析"):
            service.request_market_focus_cycle(expected_prepared_revision=3)
        service.request_analysis(101, force=False)


def test_failure_between_projection_and_cycle_commit_recovers_without_new_job(verified_stack, monkeypatch):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle)
    original = intelligence._publish_verified_focus
    def interrupted(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated_publication_interruption")
    monkeypatch.setattr(intelligence, "_publish_verified_focus", interrupted)
    with pytest.raises(RuntimeError, match="simulated_publication_interruption"):
        intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    with intelligence._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM catalyst_local_verified_focus_publications").fetchone()[0] == 0
    monkeypatch.setattr(intelligence, "_publish_verified_focus", original)
    intelligence.reconcile()
    assert len(intelligence.hotspots(limit=20)["items"]) == 1
    with ai._connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM ai_jobs").fetchone()[0] == 1


def test_slow_older_success_cannot_override_newer_rejection(verified_stack):
    _, ai, intelligence, revision, clock = verified_stack
    baseline = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, baseline, verdicts=["supported"])
    intelligence.reconcile()
    initial_as_of = clock[0]
    clock[0] += timedelta(minutes=2)
    newer = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    complete_verified(ai, newer, verdicts=["contradicted"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    assert len(intelligence.hotspots(limit=20, now=initial_as_of)["items"]) == 2
    clock[0] += timedelta(seconds=30)
    # The current enqueue guard permits only one active focus cycle. Simulate
    # delayed restoration/publication of an older immutable input, without
    # weakening that guard or starting another provider request.
    with intelligence._connect() as connection:
        connection.execute(
            "UPDATE catalyst_local_verified_focus_publications SET published_at=? WHERE cycle_id=?",
            (clock[0].isoformat().replace("+00:00", "Z"), baseline["cycle_id"]),
        )
        connection.commit()
    assert intelligence.hotspots(limit=20)["items"] == []


def test_resolved_null_server_ids_publish_while_original_receipt_is_retained(verified_stack):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    normalized, original_receipt = complete_verified(ai, cycle, null_ids=True)
    assert json.loads(original_receipt["output_text"])["event_verifications"][0]["evidence_refs"][0]["tool_use_id"] is None
    assert normalized["event_verifications"][0]["evidence_refs"][0]["tool_use_id"] == "srv_0"
    intelligence.reconcile()
    assert len(intelligence.hotspots(limit=20)["items"]) == 1
    assert json.loads(ai.get_job(cycle["job_id"])["provider_result_json"]) == original_receipt


def _delayed_older_focus_pair(verified_stack):
    _, ai, intelligence, revision, clock = verified_stack
    older = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, older, verdicts=["supported"])
    intelligence.reconcile()
    clock[0] += timedelta(minutes=2)
    newer = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    complete_verified(ai, newer, verdicts=["contradicted"])
    intelligence.reconcile()
    clock[0] += timedelta(seconds=30)
    delayed = clock[0].isoformat().replace("+00:00", "Z")
    with intelligence._connect() as connection:
        connection.execute("UPDATE catalyst_local_focus_cycles SET completed_at=? WHERE cycle_id=?", (delayed, older["cycle_id"]))
        connection.execute("UPDATE catalyst_local_verified_focus_publications SET published_at=? WHERE cycle_id=?", (delayed, older["cycle_id"]))
        connection.commit()
    return ai, intelligence, revision, clock, older, newer


@pytest.mark.parametrize("owner", [False, True])
def test_latest_cycle_and_strip_keep_newer_rejection_after_old_completion_delay(verified_stack, owner):
    from test_personal_catalyst_service import _service
    ai, intelligence, _, clock, older, newer = _delayed_older_focus_pair(verified_stack)
    service = _service("manual", engine=intelligence, repository=ai)
    with request_owner_access_context(owner):
        latest = service.latest_market_focus_cycle(now=clock[0], include_owner_state=owner)
        for key in ("cycle", "latest_successful_cycle"):
            assert latest[key]["cycle_id"] == newer["cycle_id"]
            assert latest[key]["result"]["dominant_events"] == []
            assert latest[key]["is_historical"] is False
            assert latest[key]["verification_status"] == "verified"
        previous = latest["previous_successful_cycle"]
        assert previous["cycle_id"] == older["cycle_id"]
        assert previous["is_historical"] is True
        assert previous["verification_status"] == "verified"
        assert len(previous["result"]["dominant_events"]) == 2
        exact_history = service.market_focus_cycle(older["cycle_id"])
        assert exact_history["is_historical"] is True
        assert exact_history["verification_status"] == "verified"
        assert intelligence.hotspots(limit=20)["items"] == []


def test_failed_owner_attempt_falls_back_to_newer_rejection_not_late_old_support(verified_stack):
    from test_catalyst_local_intelligence import _fail_job
    from test_personal_catalyst_service import _service
    ai, intelligence, revision, clock, older, rejected = _delayed_older_focus_pair(verified_stack)
    clock[0] += timedelta(minutes=2)
    attempted = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    _fail_job(ai, attempted["job_id"])
    intelligence.reconcile()
    service = _service("manual", engine=intelligence, repository=ai)
    latest = service.latest_market_focus_cycle(now=clock[0], include_owner_state=True)
    assert latest["cycle"]["cycle_id"] == attempted["cycle_id"]
    assert latest["cycle"]["status"] == "failed"
    for key in ("latest_successful_cycle", "previous_successful_cycle"):
        assert latest[key]["cycle_id"] == rejected["cycle_id"]
        assert latest[key]["result"]["dominant_events"] == []
        assert latest[key]["is_historical"] is True
    with request_owner_access_context(False):
        public = service.latest_market_focus_cycle(now=clock[0], include_owner_state=False)
    assert public["cycle"]["cycle_id"] == rejected["cycle_id"]
    assert public["cycle"]["result"]["dominant_events"] == []
    assert public["cycle"]["is_historical"] is True
    assert public["previous_successful_cycle"]["cycle_id"] == older["cycle_id"]
    assert public["previous_successful_cycle"]["is_historical"] is True


def test_owner_poll_publication_updates_latest_successful_in_same_response(verified_stack):
    from test_personal_catalyst_service import _service
    _, ai, intelligence, revision, clock = verified_stack
    older = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, older, verdicts=["supported"])
    intelligence.reconcile()
    clock[0] += timedelta(minutes=2)
    newer = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    complete_verified(ai, newer, verdicts=["contradicted"])
    # Paid output is durable, but the local publisher has not run yet.
    service = _service("manual", engine=intelligence, repository=ai)
    latest = service.latest_market_focus_cycle(now=clock[0], include_owner_state=True)
    assert latest["cycle"]["cycle_id"] == newer["cycle_id"]
    assert latest["latest_successful_cycle"]["cycle_id"] == newer["cycle_id"]
    assert latest["latest_successful_cycle"]["result"]["dominant_events"] == []
    assert latest["previous_successful_cycle"]["cycle_id"] == older["cycle_id"]
    assert latest["previous_successful_cycle"]["is_historical"] is True


def test_legacy_empty_snapshot_uses_creation_order_without_losing_recorded_identity(verified_stack):
    from test_personal_catalyst_service import _service
    ai, intelligence, _, clock, _, newer = _delayed_older_focus_pair(verified_stack)
    with intelligence._connect() as connection:
        connection.execute("UPDATE catalyst_local_focus_cycles SET snapshot_as_of='' WHERE cycle_id=?", (newer["cycle_id"],))
        connection.commit()
    service = _service("manual", engine=intelligence, repository=ai)
    with request_owner_access_context(False):
        latest = service.latest_market_focus_cycle(now=clock[0], include_owner_state=False)
    assert latest["cycle"]["cycle_id"] == newer["cycle_id"]
    assert latest["latest_successful_cycle"]["cycle_id"] == newer["cycle_id"]
    assert latest["cycle"]["result"]["dominant_events"] == []
    assert latest["cycle"]["snapshot_as_of"] == latest["cycle"]["result"]["as_of"]
    assert latest["cycle"]["verification_status"] == "verified"


def test_legacy_missing_hotspot_identity_remains_missing_in_verification_input():
    from collections import defaultdict
    row = defaultdict(lambda: None, {
        "prepared_revision": 1, "event_group_id": "legacy", "event_group_version": 1,
        "hot_score": 48, "source_count": 1, "representative_news_id": 1,
        "reasons_json": "[]",
    })
    projected = LocalCatalystIntelligence._project_hotspot_rows([row], prepared_at=None)[0]
    source = projected["_verification_source"]
    assert source["change_sequence"] is None
    assert source["content_hash"] is None


def test_injected_route_fallback_preserves_explicit_models_and_rejects_bad_effort():
    from types import SimpleNamespace
    service = PersonalCatalystService.__new__(PersonalCatalystService)
    service.settings = SimpleNamespace(model="claude-haiku-5-5", reasoning="xhigh")
    service.ai_settings = SimpleNamespace(openai_model="gpt-5.6-luna")
    assert service._analysis_identity() == ("gpt-5.6-luna", "max")
    service.ai_settings = SimpleNamespace(openai_market_focus_model="claude-sonnet-5-5")
    assert service._analysis_identity() == ("claude-haiku-5-5", "xhigh")
    assert service._analysis_identity("market_focus") == ("claude-sonnet-5-5", "xhigh")
    service.ai_settings.openai_market_focus_reasoning = "max"
    with pytest.raises(ValueError, match="runtime_configuration_invalid"):
        service._analysis_identity("market_focus")


def add_unrelated_news(etl, intelligence, clock):
    clock[0] += timedelta(minutes=2)
    change = _news_change(3, 13, available_at=clock[0] - timedelta(seconds=5),
                          title="Court opens separate investigation into energy pricing", sources=("Reuters", "Bloomberg"))
    change["news"]["url"] = "https://www.reuters.com/news/13"
    _apply_news(etl, [change], as_of=clock[0])
    return intelligence.reconcile()["prepared_revision"]


def test_unrelated_revision_preserves_paid_event_identity_and_original_verification_time(verified_stack):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    before = intelligence.hotspots(limit=20)["items"]
    saved_job = copy.deepcopy(ai.get_job(cycle["job_id"]))
    assert len(before) == 2
    current = add_unrelated_news(etl, intelligence, clock)
    assert current > revision
    assert len(intelligence._prepared_hotspots(limit=20)["items"]) == 3
    after = intelligence.hotspots(limit=20)["items"]
    assert {item["event_group_id"]: item for item in after} == {item["event_group_id"]: item for item in before}
    assert {item["verified_at"] for item in after} == {saved_job["completed_at"]}
    assert {item["verification_as_of"] for item in after} == {json.loads(saved_job["payload_json"])["as_of"]}
    assert ai.get_job(cycle["job_id"]) == saved_job


def test_older_cycle_finishing_after_unrelated_revision_still_publishes_its_current_events(verified_stack):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    current = add_unrelated_news(etl, intelligence, clock)
    assert current > revision
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    assert {item["representative_news_id"] for item in intelligence.hotspots(limit=20)["items"]} == {11, 12}


@pytest.mark.parametrize("negative", ["contradicted", "unverifiable"])
def test_cross_revision_new_negative_withdraws_support_even_if_old_publication_arrives_late(verified_stack, negative):
    etl, ai, intelligence, revision, clock = verified_stack
    old = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, old, verdicts=["supported"])
    intelligence.reconcile()
    before = clock[0]
    current = add_unrelated_news(etl, intelligence, clock)
    new = intelligence.request_market_focus_cycle(expected_prepared_revision=current)
    complete_verified(ai, new, verdicts=[negative])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    assert len(intelligence.hotspots(limit=20, now=before)["items"]) == 2
    clock[0] += timedelta(minutes=1)
    with intelligence._connect() as connection:
        connection.execute("UPDATE catalyst_local_verified_focus_publications SET published_at=? WHERE cycle_id=?",
                           (local_module._iso(clock[0]), old["cycle_id"]))
        connection.commit()
    assert intelligence.hotspots(limit=20)["items"] == []


def test_same_id_new_version_never_inherits_old_paid_support(verified_stack):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    original = next(item for item in intelligence.hotspots(limit=20)["items"] if item["representative_news_id"] == 11)
    clock[0] += timedelta(minutes=2)
    change = _news_change(3, 11, available_at=clock[0] - timedelta(seconds=5),
                          title="Nvidia launches Blackwell chip platform", summary="Updated timing and source details", sources=("Reuters", "Bloomberg"))
    _apply_news(etl, [change], as_of=clock[0])
    intelligence.reconcile()
    current = next(item for item in intelligence._prepared_hotspots(limit=20)["items"] if item["representative_news_id"] == 11)
    assert current["event_group_id"] == original["event_group_id"]
    assert current["event_group_version"] > original["event_group_version"]
    assert [item["representative_news_id"] for item in intelligence.hotspots(limit=20)["items"]] == [12]


@pytest.mark.parametrize("removed", ["deleted", "outside_window"])
def test_removed_current_event_never_uses_historical_support(verified_stack, removed):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    if removed == "deleted":
        clock[0] += timedelta(minutes=2)
        _apply_news(etl, [_delete_change(3, 11, available_at=clock[0])], as_of=clock[0])
    else:
        clock[0] += timedelta(hours=73)
    intelligence.reconcile()
    actual = {item["representative_news_id"] for item in intelligence.hotspots(limit=20)["items"]}
    assert actual == ({12} if removed == "deleted" else set())


@pytest.mark.parametrize("mutation", ["event_hash", "projection_summary", "bound_summary", "receipt", "cycle_input", "source_snapshot"])
def test_publication_reuse_rejects_changed_hash_projection_or_paid_binding(verified_stack, mutation):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    add_unrelated_news(etl, intelligence, clock)
    assert len(intelligence.hotspots(limit=20)["items"]) == 2
    if mutation == "receipt":
        job = ai.get_job(cycle["job_id"])
        receipt = json.loads(job["provider_result_json"])
        receipt["id"] = "msg_unrelated_paid_response"
        with ai._connect() as connection:
            connection.execute("UPDATE ai_jobs SET provider_result_json=? WHERE job_id=?", (json.dumps(receipt), cycle["job_id"]))
            connection.commit()
    else:
        with intelligence._connect() as connection:
            if mutation == "event_hash":
                connection.execute("UPDATE catalyst_local_event_groups SET input_hash=? WHERE representative_news_id=11", ("f" * 64,))
            elif mutation == "projection_summary":
                row = connection.execute("SELECT hotspot_items_json FROM catalyst_local_verified_focus_publications WHERE cycle_id=?", (cycle["cycle_id"],)).fetchone()
                items = json.loads(row[0]); items[0]["summary_zh"] = "这是未付费核实的新摘要。"
                connection.execute("UPDATE catalyst_local_verified_focus_publications SET hotspot_items_json=? WHERE cycle_id=?", (json.dumps(items), cycle["cycle_id"]))
            elif mutation == "bound_summary":
                row = connection.execute("SELECT result_json FROM catalyst_local_focus_cycles WHERE cycle_id=?", (cycle["cycle_id"],)).fetchone()
                result = json.loads(row[0]); result["event_verifications"][0]["summary_zh"] = "这是未付费核实的新摘要。"
                connection.execute("UPDATE catalyst_local_focus_cycles SET result_json=? WHERE cycle_id=?", (json.dumps(result), cycle["cycle_id"]))
                connection.execute("UPDATE catalyst_local_verified_focus_publications SET result_sha256=? WHERE cycle_id=?", (local_module._sha(result), cycle["cycle_id"]))
            else:
                row = connection.execute("SELECT payload_json FROM catalyst_local_focus_cycles WHERE cycle_id=?", (cycle["cycle_id"],)).fetchone()
                payload = json.loads(row[0])
                if mutation == "cycle_input": payload["input_hash"] = "e" * 64
                else: payload["events"][0]["source_snapshot"]["news_id"] = 999
                connection.execute("UPDATE catalyst_local_focus_cycles SET payload_json=? WHERE cycle_id=?", (json.dumps(payload), cycle["cycle_id"]))
            connection.commit()
    assert intelligence.hotspots(limit=20)["items"] == []


def test_legacy_projection_without_derived_hash_is_still_bound_to_original_revision(verified_stack):
    etl, ai, intelligence, revision, clock = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    with intelligence._connect() as connection:
        row = connection.execute("SELECT hotspot_items_json FROM catalyst_local_verified_focus_publications WHERE cycle_id=?", (cycle["cycle_id"],)).fetchone()
        items = json.loads(row[0])
        for item in items: item.pop("event_input_hash")
        connection.execute("UPDATE catalyst_local_verified_focus_publications SET hotspot_items_json=? WHERE cycle_id=?", (json.dumps(items), cycle["cycle_id"]))
        connection.commit()
    add_unrelated_news(etl, intelligence, clock)
    assert len(intelligence.hotspots(limit=20)["items"]) == 2


def test_receipt_reads_and_validation_are_deduplicated_per_cycle(verified_stack, monkeypatch):
    _, ai, intelligence, revision, _ = verified_stack
    cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, cycle, verdicts=["supported"])
    intelligence.reconcile()
    reads, validations = [], []
    original_read = intelligence._ai_job_snapshot
    original_validate = intelligence._verified_focus_paid_result
    def read(**kwargs):
        reads.append(kwargs)
        return original_read(**kwargs)
    def validate(job, payload):
        validations.append(job["job_id"])
        return original_validate(job, payload)
    monkeypatch.setattr(intelligence, "_ai_job_snapshot", read)
    monkeypatch.setattr(intelligence, "_verified_focus_paid_result", validate)
    assert len(intelligence.hotspots(limit=20)["items"]) == 2
    assert len(reads) == 2
    assert reads[0]["input_only"] is True
    assert set(reads[0]["job_ids"]) == {cycle["job_id"]}
    assert not reads[1].get("input_only", False)
    assert set(reads[1]["job_ids"]) == {cycle["job_id"]}
    assert validations == [cycle["job_id"]]


def test_newer_cycle_without_an_event_does_not_withdraw_that_events_paid_support(verified_stack, monkeypatch):
    etl, ai, intelligence, revision, clock = verified_stack
    old = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, old, verdicts=["supported"])
    intelligence.reconcile()
    current = add_unrelated_news(etl, intelligence, clock)
    # Model inputs are bounded to the leading twenty events. Simulate a newer
    # cycle selecting only the unrelated candidate, keeping the full revision.
    original = intelligence._hotspots_for_revision
    def selected(revision, *, limit):
        row, items = original(revision, limit=limit)
        return row, [item for item in items if item["representative_news_id"] == 13]
    with monkeypatch.context() as patch:
        patch.setattr(intelligence, "_hotspots_for_revision", selected)
        new = intelligence.request_market_focus_cycle(expected_prepared_revision=current)
    complete_verified(ai, new, verdicts=["supported"])
    intelligence.reconcile()
    assert {item["representative_news_id"] for item in intelligence.hotspots(limit=20)["items"]} == {11, 12, 13}


@pytest.mark.parametrize("mutation", [
    "empty_verdicts", "missing_verdict", "verdict_identity", "malformed_result", "null_result", "empty_result",
    "result_sha", "publication_input_hash", "cycle_input_hash", "publication_job", "cycle_job", "malformed_payload",
    "publication_revision", "publication_snapshot", "missing_publication",
    "missing_input_event", "missing_verification_version",
])
def test_damaged_latest_covered_rejection_never_revives_older_support(verified_stack, mutation):
    etl, ai, intelligence, revision, clock = verified_stack
    older = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, older, verdicts=["supported"])
    intelligence.reconcile()
    before_newer = clock[0]
    assert len(intelligence.hotspots(limit=20)["items"]) == 2
    current = add_unrelated_news(etl, intelligence, clock)
    newer = intelligence.request_market_focus_cycle(expected_prepared_revision=current)
    complete_verified(ai, newer, verdicts=["contradicted"])
    intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == []
    original_job = copy.deepcopy(ai.get_job(newer["job_id"]))
    with intelligence._connect() as connection:
        row = connection.execute("SELECT result_json FROM catalyst_local_focus_cycles WHERE cycle_id=?", (newer["cycle_id"],)).fetchone()
        result = json.loads(row[0])
        if mutation in {"empty_verdicts", "missing_verdict", "verdict_identity"}:
            if mutation == "empty_verdicts": result["event_verifications"] = []
            elif mutation == "missing_verdict":
                # Drop an event with a prior positive, not only the new event.
                old_id = json.loads(ai.get_job(older["job_id"])["payload_json"])["events"][0]["event_group_id"]
                result["event_verifications"] = [event for event in result["event_verifications"] if event["event_group_id"] != old_id]
            else:
                for event in result["event_verifications"]: event["event_group_id"] = "evt_wrong_identity"
            connection.execute("UPDATE catalyst_local_focus_cycles SET result_json=? WHERE cycle_id=?", (json.dumps(result), newer["cycle_id"]))
        elif mutation in {"malformed_result", "null_result", "empty_result"}:
            damaged = {"malformed_result": "{broken", "null_result": None, "empty_result": "{}"}[mutation]
            connection.execute("UPDATE catalyst_local_focus_cycles SET result_json=? WHERE cycle_id=?", (damaged, newer["cycle_id"]))
        elif mutation == "result_sha":
            connection.execute("UPDATE catalyst_local_verified_focus_publications SET result_sha256=? WHERE cycle_id=?", ("d" * 64, newer["cycle_id"]))
        elif mutation == "publication_input_hash":
            connection.execute("UPDATE catalyst_local_verified_focus_publications SET input_hash=? WHERE cycle_id=?", ("e" * 64, newer["cycle_id"]))
        elif mutation == "cycle_input_hash":
            connection.execute("UPDATE catalyst_local_focus_cycles SET input_hash=? WHERE cycle_id=?", ("e" * 64, newer["cycle_id"]))
        elif mutation == "publication_revision":
            connection.execute("UPDATE catalyst_local_verified_focus_publications SET prepared_revision=? WHERE cycle_id=?", (999999, newer["cycle_id"]))
        elif mutation == "publication_snapshot":
            connection.execute("UPDATE catalyst_local_verified_focus_publications SET snapshot_as_of=? WHERE cycle_id=?", (local_module._iso(before_newer - timedelta(hours=1)), newer["cycle_id"]))
        elif mutation == "missing_publication":
            connection.execute("DELETE FROM catalyst_local_verified_focus_publications WHERE cycle_id=?", (newer["cycle_id"],))
        elif mutation == "publication_job":
            connection.execute("UPDATE catalyst_local_verified_focus_publications SET job_id=? WHERE cycle_id=?", (older["job_id"], newer["cycle_id"]))
        elif mutation == "cycle_job":
            connection.execute("UPDATE catalyst_local_focus_cycles SET job_id=? WHERE cycle_id=?", ("aij_wrong_binding", newer["cycle_id"]))
        elif mutation in {"missing_input_event", "missing_verification_version"}:
            payload = json.loads(original_job["payload_json"])
            if mutation == "missing_input_event":
                old_id = json.loads(ai.get_job(older["job_id"])["payload_json"])["events"][0]["event_group_id"]
                payload["events"] = [event for event in payload["events"] if event["event_group_id"] != old_id]
            else:
                payload.pop("verification_version")
            connection.execute("UPDATE catalyst_local_focus_cycles SET payload_json=? WHERE cycle_id=?", (json.dumps(payload), newer["cycle_id"]))
        else:
            connection.execute("UPDATE catalyst_local_focus_cycles SET payload_json=? WHERE cycle_id=?", ("{broken", newer["cycle_id"]))
        connection.commit()
    assert intelligence.hotspots(limit=20)["items"] == []
    # Point-in-time reads before the newer cycle still see its genuine predecessor.
    assert len(intelligence.hotspots(limit=20, now=before_newer)["items"]) == 2
    assert ai.get_job(newer["job_id"]) == original_job


@pytest.mark.parametrize("state", ["queued", "in_progress"])
def test_ordinary_unfinished_newer_cycle_does_not_hide_verified_events(verified_stack, state):
    _, ai, intelligence, revision, clock = verified_stack
    older = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, older, verdicts=["supported"])
    intelligence.reconcile()
    before = intelligence.hotspots(limit=20)["items"]
    clock[0] += timedelta(minutes=2)
    newer = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    if state == "in_progress":
        claimed = ai.claim_due("new-focus", lease_seconds=60)
        assert claimed["job_id"] == newer["job_id"]
        assert ai.mark_submission_started(newer["job_id"], "new-focus", daily_limit=4) == "started"
        intelligence.reconcile()
    assert intelligence.hotspots(limit=20)["items"] == before


def test_coverage_reads_only_inputs_and_hashes_each_recent_cycle_once(verified_stack, monkeypatch):
    _, ai, intelligence, revision, clock = verified_stack
    first = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
    complete_verified(ai, first, verdicts=["supported"])
    intelligence.reconcile()
    clock[0] += timedelta(minutes=2)
    second = intelligence.request_market_focus_cycle(expected_prepared_revision=revision, force=True)
    complete_verified(ai, second, verdicts=["supported"])
    intelligence.reconcile()
    checked, snapshots = [], []
    original_check = intelligence._focus_input_is_intact
    original_snapshot = intelligence._ai_job_snapshot
    def check(cycle, job):
        checked.append(cycle["cycle_id"])
        return original_check(cycle, job)
    def snapshot(**kwargs):
        result = original_snapshot(**kwargs)
        snapshots.append((kwargs, result))
        return result
    monkeypatch.setattr(intelligence, "_focus_input_is_intact", check)
    monkeypatch.setattr(intelligence, "_ai_job_snapshot", snapshot)
    assert len(intelligence.hotspots(limit=20)["items"]) == 2
    assert sorted(checked) == sorted([first["cycle_id"], second["cycle_id"]])
    assert len(snapshots) == 2
    light_args, light = snapshots[0]
    assert light_args["input_only"] is True
    assert set(light) == {first["job_id"], second["job_id"]}
    for item in light.values():
        assert set(item) == {"job_id", "job_type", "status", "payload_json", "submission_source"}
    full_args, full = snapshots[1]
    assert not full_args.get("input_only", False)
    assert set(full) == {second["job_id"]}
    assert "provider_result_json" in full[second["job_id"]]
