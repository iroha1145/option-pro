"""热点接口的热请求不再随已核验周期数变慢（2026-10-11）。

生产上 GET /api/catalysts/hotspots 的热请求从 0.25 秒涨到 0.51 秒：每多一轮已核验的
热点周期，每次读取就多一份付费回执的完整核对，排名查询也多一份按「周期 × 候选事件」
重复解析的周期输入。这里用计数而不是挂钟钉住三处改动：

- 付费结果核对（_verified_focus_paid_result）按全部输入的确切内容在进程内记住，
  失败也记住；第二次读取不再核对回执和证据。
- 排名查询里周期输入的 JSON 只按周期读一次，不再按「周期 × 候选」逐对读。
- 来源网址的规范化（_public_source_url）按确切文本记住。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from app.access import request_owner_access_context
from app.services.ai_jobs import claude_provider
from app.services.ai_jobs import repository as repository_module
from app.services.ai_jobs import runtime
from app.services.ai_jobs.repository import AIJobRepository
from app.services.catalysts import local_intelligence as local_module
from app.services.catalysts.local_intelligence import LocalCatalystIntelligence
from test_catalyst_local_intelligence import _apply_news, _news_change, _stack
from test_verified_focus_contract import verified_payload_result
from test_verified_hotspot_publication import complete_verified

Intelligence = LocalCatalystIntelligence
TITLES = (
    "Nvidia launches Blackwell chip platform",
    "Regulator rejects pharmaceutical merger proposal",
    "Copper miners rally on Chilean supply outage",
    "Airline unions approve new pilot contract",
    "Coffee futures climb after Brazilian frost",
    "Shipping rates jump through the Panama canal",
    "Insurers brace for hurricane season losses",
    "Satellite operator wins broadband spectrum auction",
    "Brewery recalls bottles over glass fragments",
    "Railroad strike talks resume in Chicago",
)


@pytest.fixture(autouse=True)
def owner_access():
    with request_owner_access_context(True):
        yield


def _visitor(read, **kwargs):
    with request_owner_access_context(False):
        return read(**kwargs)


@pytest.fixture
def verified_rounds(tmp_path, monkeypatch):
    """``build(rounds)``: one verified cycle per round, each the newest to cover its own two stories."""

    now = datetime.now(timezone.utc).replace(microsecond=0)
    clock = [now - timedelta(hours=24)]
    monkeypatch.setattr(local_module, "_utc_now", lambda: clock[0])
    monkeypatch.setattr(repository_module, "_utcnow", lambda: clock[0])
    monkeypatch.setattr(local_module, "macro_conditions_context", lambda: None)
    etl, ai, original = _stack(tmp_path, canonical_tickers=("NVDA", "AMD"))
    intelligence = LocalCatalystIntelligence(
        original.db_path, ai, mode="manual", canonical_tickers=("NVDA", "AMD"),
        news_model="gpt-5.6-luna", news_reasoning="max",
        focus_model="claude-sonnet-5-5", focus_reasoning="xhigh",
    )
    intelligence.initialize()

    def build(rounds: int) -> list[dict]:
        cycles = []
        for index in range(rounds):
            clock[0] += timedelta(hours=2)
            fresh = (2 * index + 11, 2 * index + 12)
            changes = []
            for offset, news_id in enumerate(fresh):
                change = _news_change(
                    news_id, news_id, available_at=clock[0] - timedelta(minutes=5 - offset),
                    title=TITLES[news_id - 11], sources=("Reuters", "Bloomberg"),
                    tickers=(("NVDA", "AMD")[offset],),
                )
                change["news"]["url"] = f"https://www.reuters.com/news/{news_id}"
                changes.append(change)
            _apply_news(etl, changes, as_of=clock[0])
            revision = intelligence.reconcile()["prepared_revision"]
            # Bound each round's input to its own stories, so every round stays
            # the newest one covering some candidate.
            whole = intelligence._hotspots_for_revision

            def own_stories(revision, *, limit, **kwargs):
                row, items = whole(revision, limit=limit, **kwargs)
                return row, [item for item in items if item["representative_news_id"] in fresh]

            with monkeypatch.context() as patch:
                patch.setattr(intelligence, "_hotspots_for_revision", own_stories)
                cycle = intelligence.request_market_focus_cycle(expected_prepared_revision=revision)
            complete_verified(ai, cycle, verdicts=["supported"])
            intelligence.reconcile()
            cycles.append(cycle)
        clock[0] += timedelta(minutes=10)
        return cycles

    return ai, intelligence, clock, build


def _count_checks(monkeypatch) -> dict[str, int]:
    """Receipt and evidence checks behind the paid result, from a cold memo."""

    counts = {"receipt": 0, "evidence": 0}
    receipt = AIJobRepository._provider_receipt_json
    evidence = local_module.validate_market_focus_evidence

    def counted_receipt(value):
        counts["receipt"] += 1
        return receipt(value)

    def counted_evidence(*args, **kwargs):
        counts["evidence"] += 1
        return evidence(*args, **kwargs)

    monkeypatch.setattr(AIJobRepository, "_provider_receipt_json", staticmethod(counted_receipt))
    monkeypatch.setattr(local_module, "validate_market_focus_evidence", counted_evidence)
    local_module._focus_paid_results.clear()
    return counts


@pytest.mark.parametrize("rounds", [1, 3])
def test_a_second_read_checks_no_paid_receipt_again(verified_rounds, monkeypatch, rounds):
    _ai, intelligence, clock, build = verified_rounds
    build(rounds)
    counts = _count_checks(monkeypatch)

    first = _visitor(intelligence.hotspots, limit=20, now=clock[0])
    cold = dict(counts)
    second = _visitor(intelligence.hotspots, limit=20, now=clock[0])

    # Every round is the newest to cover its stories, so each is checked once:
    # the stored result and the receipt text each bind their evidence.
    assert cold == {"receipt": rounds, "evidence": 2 * rounds}
    assert counts == cold
    assert second == first
    assert len(first["items"]) == 2 * rounds
    assert {item["verification_status"] for item in first["items"]} == {"verified"}


def test_cycle_inputs_are_read_once_per_cycle_not_per_candidate(verified_rounds, monkeypatch):
    _ai, intelligence, clock, build = verified_rounds
    build(4)
    calls: list[str] = []
    real_connect = sqlite3.connect

    class Counting(sqlite3.Connection):
        def create_function(self, name, narg, func, **kwargs):
            if name == "focus_input_intact":
                original = func

                def func(cycle_id):
                    calls.append(cycle_id)
                    return original(cycle_id)

            return super().create_function(name, narg, func, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", lambda *args, **kwargs: real_connect(*args, factory=Counting, **kwargs))
    result = _visitor(intelligence.hotspots, limit=20, now=clock[0])

    # Eight candidates and four cycles: the coverage test used to run for every
    # cycle and candidate pair, 36 calls for these 32 pairs.
    assert len(result["items"]) == 8
    assert len(calls) == 4
    assert len(set(calls)) == 4


def test_changed_receipt_bytes_are_checked_again_and_a_rejection_stays(verified_rounds, monkeypatch):
    ai, intelligence, clock, build = verified_rounds
    first_cycle, second_cycle = build(2)
    second_payload = json.loads(ai.get_job(second_cycle["job_id"])["payload_json"])
    second_events = {event["event_group_id"] for event in second_payload["events"]}
    counts = _count_checks(monkeypatch)
    before = _visitor(intelligence.hotspots, limit=20, now=clock[0])
    assert len(before["items"]) == 4 and counts["receipt"] == 2

    def rewrite_receipt(change):
        row = ai.get_job(first_cycle["job_id"])
        receipt = json.loads(row["provider_result_json"])
        change(receipt)
        with ai._connect() as connection:
            connection.execute(
                "UPDATE ai_jobs SET provider_result_json=? WHERE job_id=?",
                (json.dumps(receipt, ensure_ascii=False, indent=2), first_cycle["job_id"]),
            )
            connection.commit()

    # Same receipt, different bytes: checked again and still accepted.
    rewrite_receipt(lambda receipt: None)
    assert _visitor(intelligence.hotspots, limit=20, now=clock[0]) == before
    assert counts["receipt"] == 3
    # A receipt for another paid response: checked again and rejected; only
    # the second round's stories remain, and later reads keep the rejection
    # without checking the receipt once more.
    rewrite_receipt(lambda receipt: receipt.update(id="msg_unrelated_paid_response"))
    after = _visitor(intelligence.hotspots, limit=20, now=clock[0])
    again = _visitor(intelligence.hotspots, limit=20, now=clock[0])
    assert counts["receipt"] == 4
    assert after == again
    assert {item["event_group_id"] for item in after["items"]} == second_events
    assert {item["verification_status"] for item in after["items"]} == {"verified"}


def _paid_job() -> tuple[dict, dict]:
    payload, result, evidence = verified_payload_result()
    receipt = {
        "provider": "anthropic", "model": runtime.SONNET_MODEL, "id": "msg_paid",
        "output_text": json.dumps(result, ensure_ascii=False, indent=1), "stop_reason": "end_turn",
        "terminal_error": None, "evidence_sources": [], "tool_evidence_version": "v1", "tool_evidence": evidence,
        "usage": {"input_tokens": 100, "cached_input_tokens": 0, "output_tokens": 10, "reasoning_tokens": 0,
                  "total_tokens": 110, "cache_creation_input_tokens": 0, "cache_creation_5m_input_tokens": 0,
                  "cache_creation_1h_input_tokens": 0},
    }
    job = {"result_json": json.dumps(result, ensure_ascii=False), "provider_result_json": json.dumps(receipt),
           "model": runtime.SONNET_MODEL, "anthropic_message_id": "msg_paid"}
    return job, payload


@pytest.fixture
def counted_check(monkeypatch) -> list[str]:
    calls: list[str] = []
    check = Intelligence._check_focus_paid_result

    def counted(job, payload):
        calls.append(str(job.get("anthropic_message_id")))
        return check(job, payload)

    monkeypatch.setattr(Intelligence, "_check_focus_paid_result", staticmethod(counted))
    local_module._focus_paid_results.clear()
    return calls


def test_a_remembered_result_equals_the_checked_one_and_is_a_private_copy(counted_check):
    job, payload = _paid_job()
    first = Intelligence._verified_focus_paid_result(job, payload)
    direct = Intelligence._check_focus_paid_result(job, payload)
    counted_check.clear()
    second = Intelligence._verified_focus_paid_result(job, payload)
    assert counted_check == []
    assert first == second == direct
    first["event_verifications"][0]["verdict"] = "contradicted"
    first["event_verifications"].clear()
    second["title_zh"] = "调用方改写"
    assert Intelligence._verified_focus_paid_result(job, payload) == direct
    assert counted_check == []


@pytest.mark.parametrize("change", ["receipt_byte", "receipt_identity", "result_bytes", "model", "message_id", "payload"])
def test_any_changed_input_is_checked_again(counted_check, change):
    job, payload = _paid_job()
    accepted = Intelligence._verified_focus_paid_result(job, payload)
    changed_job, changed_payload = dict(job), json.loads(json.dumps(payload))
    if change == "receipt_byte":
        # Same content: one more space between two fields.
        changed_job["provider_result_json"] = job["provider_result_json"].replace(", ", ",  ", 1)
    elif change == "receipt_identity":
        receipt = json.loads(job["provider_result_json"])
        receipt["id"] = "msg_other"
        changed_job["provider_result_json"] = json.dumps(receipt)
    elif change == "result_bytes":
        changed_job["result_json"] = json.dumps(json.loads(job["result_json"]), ensure_ascii=False, indent=2)
    elif change == "model":
        changed_job["model"] = "claude-haiku-5-5"
    elif change == "message_id":
        changed_job["anthropic_message_id"] = "msg_other"
    else:
        changed_payload["events"][0]["event_group_version"] = 3
    if change in {"receipt_byte", "result_bytes"}:
        assert Intelligence._verified_focus_paid_result(changed_job, changed_payload) == accepted
    else:
        with pytest.raises(ValueError):
            Intelligence._verified_focus_paid_result(changed_job, changed_payload)
    assert len(counted_check) == 2


@pytest.mark.parametrize(("change", "error"), [
    ("missing_receipt", "market_focus_verification_receipt_missing"),
    ("other_response", "market_focus_verification_receipt_mismatch"),
    ("invalid_receipt", "ai_job_provider_receipt_invalid"),
])
def test_a_remembered_rejection_is_raised_again(counted_check, change, error):
    job, payload = _paid_job()
    receipt = json.loads(job["provider_result_json"])
    if change == "missing_receipt":
        job["provider_result_json"] = None
    elif change == "other_response":
        receipt["id"] = "msg_other"
        job["provider_result_json"] = json.dumps(receipt)
    else:
        receipt["provider"] = "unknown"
        job["provider_result_json"] = json.dumps(receipt)
    raised = []
    for _ in range(3):
        with pytest.raises(ValueError, match=error) as failure:
            Intelligence._verified_focus_paid_result(job, payload)
        raised.append(failure.value)
    assert len(counted_check) == 1
    assert {type(value) for value in raised} == {ValueError}
    assert {value.args for value in raised} == {(error,)}


def test_inputs_without_an_exact_key_are_checked_every_time(counted_check):
    job, payload = _paid_job()
    job["provider_result_json"] = json.loads(job["provider_result_json"])  # not text
    for _ in range(2):
        with pytest.raises(ValueError, match="market_focus_verification_receipt_missing"):
            Intelligence._verified_focus_paid_result(job, payload)
    job, payload = _paid_job()
    payload["events"][0]["hot_score"] = float("nan")  # no canonical JSON form
    outcomes = []
    for _ in range(2):
        try:
            outcomes.append(Intelligence._verified_focus_paid_result(job, payload))
        except (KeyError, TypeError, ValueError) as error:
            outcomes.append((type(error), error.args))
    assert outcomes[0] == outcomes[1]
    assert len(counted_check) == 4
    assert not local_module._focus_paid_results


def test_the_memo_is_bounded(counted_check, monkeypatch):
    monkeypatch.setattr(local_module, "_FOCUS_PAID_RESULT_ENTRIES", 2)
    job, payload = _paid_job()
    for message_id in ("msg_a", "msg_b", "msg_c"):
        with pytest.raises(ValueError):
            Intelligence._verified_focus_paid_result({**job, "anthropic_message_id": message_id}, payload)
    assert len(local_module._focus_paid_results) == 2
    with pytest.raises(ValueError):
        Intelligence._verified_focus_paid_result({**job, "anthropic_message_id": "msg_a"}, payload)
    assert counted_check == ["msg_a", "msg_b", "msg_c", "msg_a"]


class _Text(str):
    pass


@pytest.mark.parametrize("value", [
    pytest.param("https://www.reuters.com/markets/us/story-2026-10-11/", id="article"),
    pytest.param("HTTPS://WWW.Reuters.COM:443/a?b=1#fragment", id="case_port_fragment"),
    pytest.param("https://xn--fsq.com/path", id="punycode"),
    pytest.param("https://例子.com/新闻", id="unicode_host"),
    pytest.param("http://127.0.0.1/admin", id="loopback"),
    pytest.param("https://8.8.8.8/dns", id="public_address"),
    pytest.param("https://localhost/x", id="localhost"),
    pytest.param("https://user:pass@example.org/x", id="credentials"),
    pytest.param("ftp://example.org/file", id="scheme"),
    pytest.param("https://exa mple.org/x", id="space"),
    pytest.param("https://example.org:99999/x", id="bad_port"),
    pytest.param("https://" + "a" * 2050 + ".com/", id="too_long"),
    pytest.param("", id="empty"),
])
def test_remembered_source_urls_match_the_parse(value):
    claude_provider._public_source_url_text.cache_clear()
    expected = claude_provider._parse_public_source_url(value)
    assert claude_provider._public_source_url(value) == expected
    assert claude_provider._public_source_url(value) == expected
    assert claude_provider._public_source_url_text.cache_info().hits == 1
    # Anything that is not exactly str is parsed every time.
    assert claude_provider._public_source_url(_Text(value)) == expected
    assert claude_provider._public_source_url_text.cache_info().hits == 1
    assert claude_provider._public_source_url(None) is None
    assert claude_provider._public_source_url(["https://www.reuters.com/"]) is None
    assert claude_provider._public_source_url_text.cache_info().maxsize == 4096
