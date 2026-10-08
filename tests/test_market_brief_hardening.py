from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import sqlite3
import time
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from app.services.market_brief import claude_runtime as runtime, errors
from app.services.market_brief.runner import BriefRunConfig, run_brief
from app.services.market_brief.store import AdmissionRejected, BriefRunRecord, BriefStore
from test_market_brief_runner import SAMPLE_RESULT, Client, make_pack, reply

DAY = date(2026, 10, 8)
NOW = datetime(2026, 10, 8, 13, tzinfo=timezone.utc)
CONFIG = BriefRunConfig()


def request(config=CONFIG):
    return runtime.build_request(config=config, system_text="系统指令", user_text="公开证据")


def events(*, stopped=True, output=10, blocks=None):
    blocks = blocks if blocks is not None else [{"type": "text", "text": json.dumps(SAMPLE_RESULT, ensure_ascii=False)}]
    result = [{"type": "message_start", "message": {
        "id": "msg_test", "type": "message", "role": "assistant", "model": CONFIG.model,
        "content": [], "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 0, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
    }}]
    for index, block in enumerate(blocks):
        result.append({"type": "content_block_start", "index": index, "content_block": block})
        result.append({"type": "content_block_stop", "index": index})
    result.append({"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": output}})
    if stopped:
        result.append({"type": "message_stop"})
    return result


def encode(items):
    return "".join(f"event: {item['type']}\ndata: {json.dumps(item)}\n\n" for item in items).encode()


def sdk_client(handler):
    return anthropic.AsyncAnthropic(
        api_key="test-key", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def test_missing_message_stop_is_unknown_even_with_complete_end_turn_json():
    client = sdk_client(lambda req: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=encode(events(stopped=False))))
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.PROVIDER_STREAM_INCOMPLETE
    assert not result.usage_complete and result.parsed is None
    assert result.usage["input_tokens"] == 12 and result.usage["output_tokens"] == 10
    assert client.is_closed()


@pytest.mark.parametrize("flowing", [False, True])
def test_absolute_deadline_cancels_silent_or_continuously_active_socket(flowing):
    class NeverEnding(httpx2.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            yield encode(events()[:1])
            while True:
                await asyncio.sleep(0.01 if flowing else 10)
                if flowing:
                    yield b'event: ping\ndata: {"type":"ping"}\n\n'

        async def aclose(self):
            self.closed = True

    stream = NeverEnding()
    client = sdk_client(lambda req: httpx2.Response(200, headers={"content-type": "text/event-stream"}, stream=stream))
    config = replace(CONFIG, request_timeout_seconds=0.08)
    began = time.monotonic()
    result = runtime.invoke(client, request(config), config=config)
    assert time.monotonic() - began < 1.0
    assert result.error_code == errors.SUBMISSION_OUTCOME_UNKNOWN
    assert not result.usage_complete and result.usage["input_tokens"] == 12
    assert stream.closed and client.is_closed()


def test_header_timeout_sends_once_and_submission_is_durable_before_transport(tmp_path):
    store = BriefStore(tmp_path)
    calls = []

    def handler(req):
        with sqlite3.connect(tmp_path / "admissions.sqlite3") as db:
            assert db.execute("SELECT submitted_at FROM admissions").fetchone()[0]
        calls.append(req)
        raise httpx2.ReadTimeout("response header lost", request=req)

    client = sdk_client(handler)
    record = run_brief(slot="pre_open", trading_date=DAY, trigger="manual", store=store,
                       config=CONFIG, api_key="test", now=NOW,
                       evidence_builder=lambda **kwargs: make_pack(), client_factory=lambda *_: client)
    assert len(calls) == 1 and not record.usage_complete
    assert record.error_code == errors.SUBMISSION_OUTCOME_UNKNOWN and record.cost_microusd is None
    assert store.runs_on(NOW.date()) == 1
    repeated = run_brief(slot="pre_open", trading_date=DAY, trigger="manual", store=store,
                         config=CONFIG, api_key="test", now=NOW,
                         evidence_builder=lambda **kwargs: pytest.fail("unknown slot was replayed"))
    assert repeated.error_code == errors.MARKET_BRIEF_IN_PROGRESS
    # An independent slot remains available despite the first slot's quarantine.
    other = run_brief(slot="post_close", trading_date=DAY, trigger="manual", store=store,
                     config=CONFIG, api_key="test", now=NOW,
                     evidence_builder=lambda **kwargs: make_pack(), client_factory=lambda *_: Client(reply(SAMPLE_RESULT)))
    assert other.status == "completed" and store.runs_on(NOW.date()) == 2


@pytest.mark.parametrize("extra", [0, 1])
def test_each_continuation_reserves_only_remaining_output_and_settles_full_usage(extra):
    config = replace(CONFIG, max_output_tokens=80, output_token_ceiling=100)
    client = Client(reply("working", stop_reason="pause_turn", output_tokens=80),
                    reply(SAMPLE_RESULT, output_tokens=20 + extra))
    result = runtime.invoke(client, request(config), config=config)
    assert [item["max_tokens"] for item in client.calls] == [80, 20]
    assert result.usage["output_tokens"] == 100 + extra
    assert result.error_code == (errors.BUDGET_EXCEEDED if extra else None)


def test_final_json_before_tool_is_not_published_and_unpaired_tools_are_rejected():
    before = {"type": "text", "text": json.dumps(SAMPLE_RESULT)}
    call = {"type": "server_tool_use", "id": "srv_one", "name": "web_search", "input": {"query": "q"}}
    tool_result = {"type": "web_search_tool_result", "tool_use_id": "srv_one", "content": []}
    for blocks, code in [([before, call], errors.PROVIDER_INVALID_TOOL_RESPONSE),
                         ([before, call, tool_result], errors.OUTPUT_NOT_JSON)]:
        client = sdk_client(lambda req: httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=encode(events(blocks=blocks))))
        result = runtime.invoke(client, request(), config=CONFIG)
        assert result.error_code == code and result.parsed is None


def template(index=1, *, slot="pre_open", trigger="manual"):
    return BriefRunRecord(run_id=f"mb_20261008_{slot}_{index:08x}", slot=slot, trading_date=DAY,
                          trigger=trigger, status="failed", started_at=NOW, completed_at=NOW,
                          model=CONFIG.model, effort=CONFIG.effort, cost_microusd=0)


def test_daily_admission_applies_to_scheduled_runs_and_counts_open_start(tmp_path):
    store = BriefStore(tmp_path)
    record = template()
    with store.admission(record, daily_max_runs=1):
        assert store.runs_on(NOW.date()) == 1
        store.write_run(record)
    assert store.runs_on(NOW.date()) == 1  # admission + index are not double counted
    with pytest.raises(AdmissionRejected, match="daily_run_limit_reached"):
        with store.admission(template(2, slot="post_close", trigger="scheduled"), daily_max_runs=1):
            pytest.fail("scheduled budget bypass")


def _crash_after_submission(root):
    store = BriefStore(Path(root))
    record = template()
    with store.admission(record, daily_max_runs=6):
        store.mark_submitted(record.run_id)
        os._exit(7)


def test_crashed_process_retains_daily_charge_unknown_slot_and_releases_real_run_lock(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    process = ctx.Process(target=_crash_after_submission, args=(str(tmp_path),))
    process.start()
    process.join(15)
    assert process.exitcode == 7
    store = BriefStore(tmp_path)
    store.recover_interrupted()
    recovered = store.latest_record(status=None)
    assert recovered.error_code == errors.SUBMISSION_OUTCOME_UNKNOWN
    assert recovered.cost_microusd is None and not recovered.usage_complete
    assert store.runs_on(NOW.date()) == 1
    with pytest.raises(AdmissionRejected):
        with store.admission(template(2), daily_max_runs=6):
            pytest.fail("same unknown slot replayed")
    with store.admission(template(3, slot="post_close"), daily_max_runs=6):
        store.write_run(template(3, slot="post_close"))
    assert store.runs_on(NOW.date()) == 2


def _race_admission(root, index, barrier, release, outcomes):
    store = BriefStore(Path(root))
    record = template(index)
    barrier.wait(15)
    try:
        with store.admission(record, daily_max_runs=1):
            outcomes.put("admitted")
            release.wait(15)
            store.write_run(record)
    except AdmissionRejected as exc:
        outcomes.put(str(exc))


def test_multiple_processes_cannot_overlap_paid_admission(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    barrier, release, outcomes = ctx.Barrier(4), ctx.Event(), ctx.Queue()
    processes = [ctx.Process(target=_race_admission, args=(str(tmp_path), i, barrier, release, outcomes)) for i in range(1, 5)]
    for process in processes:
        process.start()
    try:
        results = [outcomes.get(timeout=20) for _ in processes]
        assert results.count("admitted") == 1
        assert results.count(errors.MARKET_BRIEF_IN_PROGRESS) == 3
    finally:
        release.set()
        for process in processes:
            process.join(15)
            if process.is_alive():
                process.kill()
                process.join()
    assert all(process.exitcode == 0 for process in processes)
    assert BriefStore(tmp_path).runs_on(NOW.date()) == 1


def test_durable_success_receipt_survives_publication_failure(tmp_path, monkeypatch):
    store = BriefStore(tmp_path)
    original = store._write_json

    def fail_index(path, document):
        if path == store.index_path:
            raise OSError("disk write interrupted")
        return original(path, document)

    monkeypatch.setattr(store, "_write_json", fail_index)
    with pytest.raises(OSError):
        run_brief(slot="pre_open", trading_date=DAY, trigger="manual", store=store,
                  config=CONFIG, api_key="test", now=NOW,
                  evidence_builder=lambda **kwargs: make_pack(), client_factory=lambda *_: Client(reply(SAMPLE_RESULT)))
    receipt = json.loads(next(store.runs_dir.glob("*.json")).read_text())["record"]
    assert receipt["status"] == "completed" and receipt["result"] == SAMPLE_RESULT
    monkeypatch.setattr(store, "_write_json", original)
    store.recover_interrupted()
    assert store.latest_public()["brief"]["result"] == SAMPLE_RESULT
    assert store.runs_on(NOW.date()) == 1


def test_cli_cannot_bypass_shared_daily_admission(tmp_path, monkeypatch, capsys):
    from pydantic import SecretStr
    from app.personal_config import MarketBriefConfig
    from app.services.market_brief import runner, store as storage
    from app.tools import market_brief_run as cli

    monkeypatch.setattr("app.personal_config.get_personal_config", lambda: SimpleNamespace(market_brief=MarketBriefConfig(daily_max_runs=1)))
    monkeypatch.setattr("app.config.get_settings", lambda: SimpleNamespace(anthropic_api_key=SecretStr("test")))
    monkeypatch.setattr(storage, "get_data_paths", lambda: SimpleNamespace(root=tmp_path))
    monkeypatch.setattr(runner, "build_evidence", lambda **kwargs: make_pack())
    clients = []

    def factory(*args):
        client = Client(reply(SAMPLE_RESULT))
        clients.append(client)
        return client

    monkeypatch.setattr(runner, "make_client", factory)
    assert cli.main(["--slot", "post_close", "--date", str(DAY)]) == 0
    assert cli.main(["--slot", "post_close", "--date", str(DAY)]) == 1
    assert len(clients) == 1 and len(clients[0].calls) == 1
    assert "daily_run_limit_reached" in capsys.readouterr().out


def test_worker_replayed_action_reads_paid_receipt_even_after_daily_limit(tmp_path):
    from app.personal_config import MarketBriefConfig
    from app.worker.tasks import MarketBriefTask
    from pydantic import SecretStr

    store = BriefStore(tmp_path)
    calls = []

    def controlled_runner(**kwargs):
        def factory(*args):
            client = Client(reply(SAMPLE_RESULT))
            calls.append(client)
            return client
        return run_brief(**kwargs, evidence_builder=lambda **kw: make_pack(), client_factory=factory)

    task = MarketBriefTask(
        "test", settings=SimpleNamespace(market_brief_configured=True, anthropic_api_key=SecretStr("test")),
        personal_config=SimpleNamespace(market_brief=MarketBriefConfig(daily_max_runs=1)),
        store_factory=lambda: store, runner=controlled_runner, now=lambda: NOW,
    )
    action = {"request_id": "action_durable", "details": {"parameters": {"slot": "pre_open"}}}
    first = asyncio.run(task.run_for_actions([action]))
    # Simulate worker action-table recovery after the report was saved but the
    # manual action's completion could not be committed before process exit.
    second = asyncio.run(task.run_for_actions([action]))
    assert first.error_code is None and second.error_code is None
    assert first.details["run_id"] == second.details["run_id"]
    assert len(calls) == 1 and store.runs_on(NOW.date()) == 1
    assert second.details["action_completions"][0]["succeeded"]
