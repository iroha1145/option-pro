from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import anthropic
import pytest

from app.services.ai_jobs import claude_provider, runtime as haiku
from app.services.market_brief import claude_runtime as opus, errors
from app.services.market_brief.runner import BriefRunConfig
from app.services.market_brief.store import BriefStore, _record_from_document
from app.services.model_budget import SharedModelBudget
from test_market_brief_runtime import FakeClient, message, usage, json_text, request, _status_error
from test_market_brief_runner import Client, SAMPLE_RESULT, reply, run


def zero_usage():
    return {key: 0 for key in opus._USAGE_KEYS}


@pytest.mark.parametrize("field", opus._COST_USAGE_KEYS)
@pytest.mark.parametrize("bad", [None, -1, True, "0"])
def test_missing_or_invalid_opus_cost_counter_is_unknown(field, bad):
    value = {**zero_usage(), field: bad}
    assert opus.cost_microusd(value) is None


def test_true_zero_is_distinct_from_missing_usage():
    assert opus.cost_microusd(zero_usage()) == 0
    assert opus.cost_microusd({}) is None
    assert opus._round_usage(None)["input_tokens"] is None
    assert opus.cost_microusd({**zero_usage(), "cache_read_input_tokens": 1}) == 1


@pytest.mark.parametrize("missing", ["cache_read_input_tokens", "input_tokens", "output_tokens"])
def test_complete_opus_stream_can_have_unknown_accounting(missing):
    raw = usage()
    delattr(raw, missing)
    result = opus.invoke(FakeClient([message([json_text()], message_usage=raw)]), request(), config=BriefRunConfig())
    assert result.error_code is None and result.parsed is not None
    assert not result.usage_complete and result.cost_microusd is None
    assert result.request_rounds[0]["stream_complete"] is True
    assert result.request_rounds[0]["accounting_complete"] is False


def test_search_call_without_billing_counter_keeps_unknown():
    raw = usage()
    del raw.server_tool_use.web_search_requests
    blocks = [SimpleNamespace(type="server_tool_use", id="s1", name="web_search"),
              SimpleNamespace(type="web_search_tool_result", tool_use_id="s1"), json_text()]
    result = opus.invoke(FakeClient([message(blocks, message_usage=raw)]), request(), config=BriefRunConfig())
    assert result.parsed is not None and not result.usage_complete
    assert result.usage["web_search_requests"] is None
    assert result.cost_microusd is None
    raw_no_call = usage()
    del raw_no_call.server_tool_use.web_search_requests
    known = opus.invoke(FakeClient([message([json_text()], message_usage=raw_no_call)]), request(), config=BriefRunConfig())
    assert known.usage_complete and known.usage["web_search_requests"] == 0


def test_stale_iterations_do_not_replace_new_final_counts_or_release_hold():
    raw = usage(input_tokens=100, output_tokens=90, iterations=[{
        "type": "message", "input_tokens": 100, "output_tokens": 1,
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
    }])
    result = opus.invoke(FakeClient([message([json_text()], message_usage=raw)]), request(), config=BriefRunConfig())
    assert result.usage["output_tokens"] == 90
    assert not result.usage_complete and result.cost_microusd is None


def test_partial_ttl_is_conservatively_priceable():
    raw = usage(cache_write=100)
    raw.cache_creation = {"ephemeral_5m_input_tokens": 40}
    result = opus.invoke(FakeClient([message([json_text()], message_usage=raw)]), request(), config=BriefRunConfig())
    assert result.usage_complete
    assert result.cost_microusd == 4000 + 10000 + 200 + 480
    assert result.usage["cache_creation_1h_input_tokens"] is None


@pytest.mark.parametrize("field", ["cached_input_tokens", "web_search_requests"])
def test_haiku_missing_charge_counter_keeps_original_reservation(field):
    value = {"input_tokens": 10, "output_tokens": 20, "cached_input_tokens": 0,
             "cache_creation_input_tokens": 0, "cache_creation_1h_input_tokens": 0,
             "cache_creation_5m_input_tokens": 0, "web_search_requests": 0}
    value[field] = None
    assert haiku.settled_usage_cost_microusd("earnings_impact", value, fallback_microusd=700000, model="claude-haiku-5-5") == 700000


def test_haiku_saved_usage_preserves_unknown_components_and_search_counter():
    raw = SimpleNamespace(input_tokens=10, output_tokens=20, cache_creation_input_tokens=0,
                          cache_read_input_tokens=None, cache_creation=None,
                          output_tokens_details=None, server_tool_use=None)
    content = [SimpleNamespace(type="server_tool_use", id="s", name="web_search"),
               SimpleNamespace(type="web_search_tool_result", tool_use_id="s")]
    value = claude_provider.response_usage(SimpleNamespace(usage=raw, content=content))
    assert value["input_tokens"] is None and value["total_tokens"] is None
    assert value["output_tokens"] == 20 and value["web_search_requests"] is None


def test_shared_budget_first_round_block_does_not_send_network(tmp_path):
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    assert budget.reserve_brief_request("prior", 1, 4_000_000)
    client = Client(reply(SAMPLE_RESULT))
    record, _ = run(BriefStore(tmp_path / "market-brief"), client,
                    config=BriefRunConfig(shared_daily_budget_usd=9.5, budget_path=budget.path))
    assert record.error_code == errors.DAILY_BUDGET_USD_REACHED
    assert not client.calls and record.cost_microusd == 0 and record.usage_complete
    assert budget.snapshot()["used_microusd"] == 4_000_000


def test_shared_budget_blocks_continuation_after_true_cost_exceeds_allowance(tmp_path):
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    first = message([json_text()], stop_reason="pause_turn", message_usage=usage(input_tokens=1_700_000, output_tokens=100))
    client = Client(first, reply(SAMPLE_RESULT))
    store = BriefStore(tmp_path / "market-brief")
    record, _ = run(store, client, config=BriefRunConfig(shared_daily_budget_usd=9.5, budget_path=budget.path))
    assert len(client.calls) == 1 and record.error_code == errors.DAILY_BUDGET_USD_REACHED
    assert record.cost_microusd == 6_802_000 and record.usage_complete
    assert record.raw_output_text and record.usage["input_tokens"] == 1_700_000
    assert budget.snapshot()["used_microusd"] == 6_802_000
    assert (store.root / "request-rounds" / record.run_id / "1.json").exists()


def test_shared_unknown_round_retains_full_allowance_and_paid_output(tmp_path):
    raw = usage()
    del raw.cache_read_input_tokens
    client = Client(message([json_text()], message_usage=raw))
    store = BriefStore(tmp_path / "market-brief")
    record, _ = run(store, client, config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert record.status == "completed" and record.result is not None
    assert not record.usage_complete and record.cost_microusd is None
    budget = SharedModelBudget(store.root.parent / "ai-jobs.db", 9.5)
    assert budget.snapshot()["used_microusd"] == 6_060_000
    saved = store.latest_record()
    assert saved is not None and not saved.usage_complete and saved.result == record.result


def test_explicit_request_rejection_releases_only_its_own_round(tmp_path):
    client = FakeClient([_status_error(anthropic.BadRequestError, 400)])
    record, _ = run(BriefStore(tmp_path / "market-brief"), client,
                    config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert record.error_code == errors.PROVIDER_REQUEST_REJECTED
    assert record.usage_complete and record.cost_microusd == 0
    assert SharedModelBudget(tmp_path / "ai-jobs.db", 9.5).snapshot()["used_microusd"] == 0
    assert record.request_rounds[0]["confirmed_unbilled"] is True


def test_duplicate_round_reservation_never_authorizes_network():
    client = FakeClient([message([json_text()])])
    def repeated(index, maximum):
        raise opus.RequestAdmissionRejected(errors.SUBMISSION_OUTCOME_UNKNOWN)
    result = opus.invoke(client, request(), config=BriefRunConfig(), before_request=repeated)
    assert not client.messages.calls and not result.usage_complete


def test_legacy_paid_record_with_missing_usage_does_not_default_complete():
    document = {
        "run_id": "mb_20261008_post_close_12345678", "slot": "post_close", "trading_date": "2026-10-08",
        "trigger": "manual", "status": "completed", "started_at": "2026-10-08T00:00:00Z",
        "completed_at": "2026-10-08T00:01:00Z", "model": "claude-opus-5-5", "effort": "xhigh",
        "usage": {}, "cost_microusd": 0,
    }
    assert not _record_from_document(document).usage_complete


def test_allowance_and_cutoff_are_forwarded_without_repricing_old_rounds(tmp_path):
    assert opus.request_budget_reservation_microusd(BriefRunConfig()) == 6_060_000
    assert opus.request_budget_reservation_microusd(BriefRunConfig(), max_tokens=100) == 5_102_000
    assert opus.request_budget_reservation_microusd(BriefRunConfig(prompt_cache_ttl="1h")) == 9_060_000
    start = datetime.now(timezone.utc) - timedelta(seconds=1)
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    assert budget.reserve_brief_request("old", 1, 9_000_000, now=start - timedelta(seconds=1))
    client = Client(reply(SAMPLE_RESULT))
    record, _ = run(BriefStore(tmp_path / "market-brief"), client, config=BriefRunConfig(
        shared_daily_budget_usd=9.5, shared_budget_start_at=start, budget_path=budget.path))
    assert record.status == "completed" and len(client.calls) == 1
    snapshot = SharedModelBudget(budget.path, 9.5, accounting_start_at=start).snapshot()
    assert snapshot["used_microusd"] == record.cost_microusd


def test_saved_per_round_receipt_settles_after_crash_without_network(tmp_path):
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    run_id = "mb_20261008_post_close_abcdef12"
    assert budget.reserve_brief_request(run_id, 1, 6_060_000)
    store = BriefStore(tmp_path / "market-brief")
    receipt = {"round_index": 1, "request_id": "msg_paid", "usage": {**zero_usage(), "output_tokens": 100},
               "accounting_complete": True, "stream_complete": True, "confirmed_unbilled": False,
               "cost_microusd": 2_000}
    store.write_request_round(run_id, receipt)
    assert budget.snapshot()["used_microusd"] == 6_060_000
    assert store.reconcile_request_rounds(budget) == 1
    assert budget.snapshot()["used_microusd"] == 2_000
    assert store.reconcile_request_rounds(budget) == 0
    assert not budget.reserve_brief_request(run_id, 1, 6_060_000)


def test_each_round_keeps_its_utc_day_when_a_run_crosses_midnight(tmp_path):
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    instants = [datetime(2026, 10, 8, 23, 59, tzinfo=timezone.utc), datetime(2026, 10, 9, 0, 1, tzinfo=timezone.utc)]
    run_id = "mb_20261008_post_close_abcdef12"
    def admit(index, ceiling):
        assert budget.reserve_brief_request(run_id, index, opus.request_budget_reservation_microusd(BriefRunConfig(), ceiling), now=instants[index - 1])
    def settle(metadata):
        budget.settle_brief_request(run_id, metadata["round_index"], cost_microusd=metadata["cost_microusd"],
                                   accounting_complete=metadata["accounting_complete"])
    result = opus.invoke(FakeClient([message([json_text()], stop_reason="pause_turn"), message([json_text()])]),
                         request(), config=BriefRunConfig(), before_request=admit, on_round_result=settle)
    assert result.usage_complete and len(result.request_rounds) == 2
    first = budget.snapshot(instants[0])["used_microusd"]
    second = budget.snapshot(instants[1])["used_microusd"]
    assert first == result.request_rounds[0]["cost_microusd"]
    assert second == result.request_rounds[1]["cost_microusd"]
    assert first + second == result.cost_microusd


def test_later_http_rejection_does_not_release_an_earlier_paid_round(tmp_path):
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    client = FakeClient([message([json_text()], stop_reason="pause_turn"), _status_error(anthropic.BadRequestError, 400)])
    record, _ = run(BriefStore(tmp_path / "market-brief"), client, config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert len(client.messages.calls) == 2 and record.error_code == errors.PROVIDER_REQUEST_REJECTED
    assert record.request_rounds[0]["cost_microusd"] > 0
    assert record.request_rounds[1]["confirmed_unbilled"] and record.request_rounds[1]["cost_microusd"] == 0
    assert budget.snapshot()["used_microusd"] == record.request_rounds[0]["cost_microusd"] == record.cost_microusd


def test_missing_stream_usage_never_becomes_zero_cost_on_restart(tmp_path):
    client = FakeClient([message([json_text()], message_usage=SimpleNamespace())])
    store = BriefStore(tmp_path / "market-brief")
    record, _ = run(store, client, config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert record.status == "completed" and not record.usage_complete and record.cost_microusd is None
    budget = SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)
    assert budget.snapshot()["used_microusd"] == 6_060_000
    assert store.reconcile_request_rounds(budget) == 0
    assert budget.snapshot()["used_microusd"] == 6_060_000


@pytest.mark.parametrize("updated_iterations", [False, True])
def test_sdk_stream_iteration_snapshots_are_not_reused_after_new_totals(updated_iterations):
    import httpx2
    from test_market_brief_hardening import events, encode, sdk_client
    rows = events(output=90)
    initial = {"type": "message", "input_tokens": 12, "output_tokens": 0,
               "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}
    rows[0]["message"]["usage"]["iterations"] = [initial]
    if updated_iterations:
        rows[-2]["usage"]["iterations"] = [{**initial, "output_tokens": 90}]
    client = sdk_client(lambda req: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=encode(rows)))
    result = opus.invoke(client, request(), config=BriefRunConfig())
    assert result.usage["input_tokens"] == 12 and result.usage["output_tokens"] == 90
    assert result.stream_complete and result.parsed is not None
    assert result.usage_complete is updated_iterations
    assert result.cost_microusd == (1848 if updated_iterations else None)


def test_rounded_cost_is_sum_of_per_round_ttl_estimates_not_repriced_aggregate():
    first = usage(input_tokens=0, output_tokens=0, cache_write=1000, cache_5m=1000)
    second = usage(input_tokens=0, output_tokens=0, cache_write=500)
    second.cache_creation = None
    result = opus.invoke(FakeClient([
        message([json_text()], stop_reason="pause_turn", message_usage=first),
        message([json_text()], message_usage=second),
    ]), request(), config=BriefRunConfig())
    assert result.usage_complete and result.cost_microusd == 9000
    assert [item["cost_microusd"] for item in result.request_rounds] == [5000, 4000]
    assert opus.cost_microusd(result.usage) == 12000


def test_round_receipt_is_idempotent_and_conflicting_amount_is_rejected(tmp_path):
    store = BriefStore(tmp_path / "market-brief")
    run_id = "mb_20261008_post_close_abcdef12"
    value = {"round_index": 1, "request_id": "msg", "usage": zero_usage(), "accounting_complete": True,
             "stream_complete": True, "confirmed_unbilled": False, "cost_microusd": 0}
    path = store.write_request_round(run_id, value)
    assert store.write_request_round(run_id, value) == path
    assert json.loads(path.read_text())["version"] == 1
    with pytest.raises(RuntimeError, match="receipt conflict"):
        store.write_request_round(run_id, {**value, "cost_microusd": 100})


def test_durable_submission_marker_failure_releases_unsent_request(tmp_path, monkeypatch):
    store = BriefStore(tmp_path / "market-brief")
    client = Client(reply(SAMPLE_RESULT))
    def failure(*args):
        raise OSError("local marker write failed before SDK call")
    monkeypatch.setattr(store, "mark_submitted", failure)
    with pytest.raises(OSError):
        run(store, client, config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert not client.calls
    assert SharedModelBudget(tmp_path / "ai-jobs.db", 9.5).snapshot()["used_microusd"] == 0


@pytest.mark.parametrize("processing_step", ["_accounting_valid", "_round_usage"])
def test_local_processing_failure_after_paid_response_keeps_reservation(tmp_path, monkeypatch, processing_step):
    store = BriefStore(tmp_path / "market-brief")
    client = FakeClient([message([json_text()])])
    failure = RuntimeError("local receipt processing failed after full paid response")
    def broken(*args, **kwargs):
        raise failure
    monkeypatch.setattr(opus, processing_step, broken)
    with pytest.raises(RuntimeError) as caught:
        run(store, client, config=BriefRunConfig(shared_daily_budget_usd=9.5))
    assert caught.value is failure
    assert len(client.messages.calls) == 1
    assert SharedModelBudget(tmp_path / "ai-jobs.db", 9.5).snapshot()["used_microusd"] == 6_060_000
    saved_rounds = list((store.root / "request-rounds").glob("*/*.json"))
    assert len(saved_rounds) == 1
    receipt = json.loads(saved_rounds[0].read_text())
    assert receipt["stream_complete"] is True
    assert receipt["accounting_complete"] is False
    assert receipt["confirmed_unbilled"] is False
    assert receipt["cost_microusd"] is None
    assert store.reconcile_request_rounds(SharedModelBudget(tmp_path / "ai-jobs.db", 9.5)) == 0
