from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from app.services.claude_cache_diagnostics import DiagnosticsStore, MAX_RECORDS, normalize, verdict
from app.services import claude_cache_diagnostics as diagnostics_module
from app.services.ai_jobs import claude_provider as haiku
from app.services.market_brief import claude_runtime as opus
from app.services.market_brief.runner import BriefRunConfig


def drain():
    done = threading.Event()
    assert diagnostics_module._submit(done.set)
    assert done.wait(2), "diagnostics worker did not finish test writes"


@pytest.fixture(autouse=True)
def _drain_background_writes():
    drain()
    yield
    drain()


def write(store, current="msg_one", previous=None, diagnostics=None, cached=0, complete=True, task="market_brief"):
    store.record(task=task, model="claude-opus-5-5", previous=previous, current=current,
                 diagnostics=diagnostics, usage={"cache_read_input_tokens": cached,
                 "cache_creation_input_tokens": 100}, complete=complete)


@pytest.mark.parametrize("reason", ["model_changed", "system_changed", "tools_changed", "messages_changed",
                                   "previous_message_not_found", "unavailable", "private prompt text"])
def test_reason_whitelist_and_estimate(reason):
    result = normalize({"cache_miss_reason": {"type": reason, "cache_missed_input_tokens": 91,
                                            "secret": "prompt"}, "secret": "input"})
    assert result["reason"] == (reason if reason != "private prompt text" else "unknown")
    assert result["estimated_missed_input_tokens"] == (91 if reason.endswith("_changed") else None)
    assert "prompt" not in json.dumps(result)


def test_three_states_and_usage_verdicts(tmp_path):
    store = DiagnosticsStore(tmp_path)
    write(store, diagnostics=None, cached=100)
    assert verdict(store.read()["records"][-1]) == "baseline"
    write(store, current="msg_two", previous="msg_one", diagnostics={"cache_miss_reason": None})
    assert verdict(store.read()["records"][-1]) == "pending"
    write(store, current="msg_three", previous="msg_two", diagnostics=None)
    assert verdict(store.read()["records"][-1]) == "no_cache_read_observed"
    write(store, current="msg_four", previous="msg_three", diagnostics=None, cached=80)
    assert verdict(store.read()["records"][-1]) == "cache_read_observed"
    write(store, current="msg_five", previous="msg_four", diagnostics={"cache_miss_reason": {"type": "previous_message_not_found"}})
    assert verdict(store.read()["records"][-1]) == "comparison_unavailable"
    write(store, current="msg_six", previous="msg_five", diagnostics={"cache_miss_reason": {"type": "system_changed", "cache_missed_input_tokens": 12}})
    assert verdict(store.read()["records"][-1]) == "prefix_changed"
    write(store, current="msg_seven", previous="msg_six", diagnostics=None, cached=100, complete=False)
    assert verdict(store.read()["records"][-1]) == "usage_incomplete"


def test_restart_bound_and_completion_order(tmp_path):
    store = DiagnosticsStore(tmp_path)
    for index in range(MAX_RECORDS + 3):
        write(store, current=f"msg_{index}")
    assert len(store.read()["records"]) == MAX_RECORDS
    # An older concurrent response completing late must not replace the latest message_start.
    write(store, current="msg_201", cached=20)
    assert DiagnosticsStore(tmp_path).previous("market_brief") == "msg_202"
    command = "from app.services.claude_cache_diagnostics import DiagnosticsStore; print(DiagnosticsStore().previous('market_brief'))"
    result = subprocess.run([sys.executable, "-c", command], env=os.environ | {
        "DATA_DIR": str(tmp_path), "PYTHONPATH": str(Path(__file__).parents[1] / "backend")},
        text=True, capture_output=True, check=True)
    assert result.stdout.strip() == "msg_202"


def test_nonblocking_lock_corruption_and_unwritable_storage(tmp_path, monkeypatch):
    store = DiagnosticsStore(tmp_path)
    write(store)
    with store.path().with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        write(store, current="msg_locked")
    assert store.previous("market_brief") == "msg_one"
    store.path().write_text("broken json")
    assert store.previous("market_brief") is None
    write(store, current="msg_recovered")
    assert store.previous("market_brief") == "msg_recovered"
    write(DiagnosticsStore(tmp_path / "absent"))
    monkeypatch.setattr("app.services.claude_cache_diagnostics.tempfile.mkstemp", lambda **_: (_ for _ in ()).throw(PermissionError()))
    write(store, current="msg_permission")
    assert store.previous("market_brief") == "msg_recovered"


def test_four_concurrent_writers_produce_only_valid_bounded_state(tmp_path):
    store = DiagnosticsStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: write(store, current=f"msg_{i}"), range(60)))
    rows = store.read()["records"]
    assert 1 <= len(rows) <= 60
    assert len({row["message_id"] for row in rows}) == len(rows)
    assert not list(tmp_path.glob(".claude-cache-*"))


def events(identifier, model, diagnostics, stop="end_turn", cached=0):
    return [
        {"type": "message_start", "message": {"id": identifier, "type": "message", "role": "assistant",
            "model": model, "content": [], "stop_reason": None, "stop_sequence": None,
            "diagnostics": diagnostics, "usage": {"input_tokens": 20, "output_tokens": 0,
                "cache_creation_input_tokens": 100, "cache_read_input_tokens": cached}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": '{"ok":true}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": stop, "stop_sequence": None}, "usage": {"output_tokens": 10}},
        {"type": "message_stop"},
    ]


def response(sequence):
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
        text="".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in sequence))


def test_haiku_real_sdk_two_requests_same_logical_task(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        number = len(calls)
        diagnostics = None if number == 1 else {"cache_miss_reason": {"type": "system_changed", "cache_missed_input_tokens": 45}}
        return response(events(f"msg_haiku{number}", haiku.MODEL, diagnostics, cached=0))
    original = anthropic.AsyncAnthropic
    monkeypatch.setattr(haiku, "AsyncAnthropic", lambda **kwargs: original(**kwargs,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    for instructions in ("first system", "changed system"):
        prepared = haiku.prepare_message(SimpleNamespace(anthropic_api_key=SecretStr("fake"), openai_timeout_seconds=10),
            instructions=instructions, input_text="private input", schema={"type": "object"}, max_tokens=100, job_type="news_impact")
        result = asyncio.run(haiku.stream_message(prepared))
        assert result.usage.output_tokens == 10
    assert [call["diagnostics"] for call in calls] == [{"previous_message_id": None}, {"previous_message_id": "msg_haiku1"}]
    drain()
    rows = DiagnosticsStore(tmp_path).read()["records"]
    assert len(rows) == 2 and rows[-1]["reason"] == "system_changed"
    assert "private input" not in DiagnosticsStore(tmp_path).path().read_text()
    assert all(row["usage_complete"] for row in rows)


@pytest.mark.parametrize("fallback", [False, True])
def test_opus_real_sdk_pause_chain_and_next_invocation(monkeypatch, tmp_path, fallback):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        number = len(calls)
        return response(events(f"msg_opus{number}", "claude-opus-5-5", None,
                               stop="pause_turn" if number == 1 else "end_turn", cached=80 if number > 1 else 0))
    def client():
        return anthropic.AsyncAnthropic(api_key="fake", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    config = replace(BriefRunConfig(), refusal_fallback=fallback)
    request = opus.build_request(config=config, system_text="system", user_text="private input")
    first = opus.invoke(client(), request, config=config)
    second = opus.invoke(client(), request, config=config)
    assert first.error_code is None and first.continuation_count == 1 and first.usage["output_tokens"] == 20
    assert second.error_code is None
    assert [call["diagnostics"] for call in calls] == [{"previous_message_id": None},
        {"previous_message_id": "msg_opus1"}, {"previous_message_id": "msg_opus2"}]
    assert calls[1]["messages"][-1]["role"] == "assistant"
    drain()
    rows = DiagnosticsStore(tmp_path).read()["records"]
    assert [verdict(row) for row in rows] == ["baseline", "cache_read_observed", "cache_read_observed"]
    assert first.usage["cache_read_input_tokens"] == 80


def test_bad_store_does_not_interrupt_real_sdk_request(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "missing"))
    count = []
    def handler(request):
        count.append(request)
        return response(events("msg_safe", haiku.MODEL, None))
    original = anthropic.AsyncAnthropic
    monkeypatch.setattr(haiku, "AsyncAnthropic", lambda **kwargs: original(**kwargs,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    prepared = haiku.prepare_message(SimpleNamespace(anthropic_api_key=SecretStr("fake"), openai_timeout_seconds=10),
        instructions="system", input_text="input", schema={"type": "object"}, max_tokens=100)
    assert asyncio.run(haiku.stream_message(prepared)).id == "msg_safe"
    assert len(count) == 1


def test_four_process_writers_and_read_only_cli(tmp_path):
    environment = os.environ | {"DATA_DIR": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).parents[1] / "backend")}
    script = """import sys
from app.services.claude_cache_diagnostics import DiagnosticsStore
store = DiagnosticsStore()
for i in range(12):
    store.record(task='market_brief', model='claude-opus-5-5', previous=None,
        current=f'msg_p{sys.argv[1]}_{i}', diagnostics=None, usage={}, complete=False)
"""
    processes = [subprocess.Popen([sys.executable, "-c", script, str(i)], env=environment,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for i in range(4)]
    for process in processes:
        out, err = process.communicate(timeout=20)
        assert process.returncode == 0, (out, err)
    store = DiagnosticsStore(tmp_path)
    assert store.read()["available"]
    rows = store.read()["records"]
    assert 1 <= len(rows) <= 48
    before = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in tmp_path.iterdir()}
    result = subprocess.run([sys.executable, "-m", "app.tools.claude_cache_diagnostics", "--limit", "2"],
                            env=environment, capture_output=True, text=True, check=True)
    report = json.loads(result.stdout)
    assert len(report["records"]) == min(2, len(rows))
    assert report["summary"] == {"baseline": len(rows)}
    after = {path.name: (path.stat().st_mtime_ns, path.read_bytes()) for path in tmp_path.iterdir()}
    assert before == after


def test_read_discards_arbitrary_saved_fields_and_oversize_file(tmp_path):
    store = DiagnosticsStore(tmp_path)
    write(store)
    payload = json.loads(store.path().read_text())
    payload["records"][0]["output"] = "private text"
    payload["records"][0]["reason"] = "private reason"
    store.path().write_text(json.dumps(payload))
    assert "private" not in json.dumps(store.read())
    store.path().write_text(" " * 512_001)
    assert store.read() == {"records": [], "available": False}


@pytest.mark.parametrize("diagnostics,expected", [(None, "cache_read_observed"),
    ({"cache_miss_reason": None}, "pending"),
    ({"cache_miss_reason": {"type": "tools_changed", "cache_missed_input_tokens": 12}}, "prefix_changed")])
def test_real_sdk_null_states_are_distinct(monkeypatch, tmp_path, diagnostics, expected):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    write(DiagnosticsStore(tmp_path), current="msg_before", task="ai_jobs:news_impact")
    def handler(request):
        assert json.loads(request.content)["diagnostics"] == {"previous_message_id": "msg_before"}
        return response(events("msg_after", haiku.MODEL, diagnostics, cached=80))
    original = anthropic.AsyncAnthropic
    monkeypatch.setattr(haiku, "AsyncAnthropic", lambda **kwargs: original(**kwargs,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    prepared = haiku.prepare_message(SimpleNamespace(anthropic_api_key=SecretStr("fake"), openai_timeout_seconds=10),
        instructions="system", input_text="input", schema={"type": "object"}, max_tokens=100, job_type="news_impact")
    asyncio.run(haiku.stream_message(prepared))
    drain()
    latest = DiagnosticsStore(tmp_path).read()["records"][-1]
    assert verdict(latest) == expected
    assert latest["cache_read_input_tokens"] == 80


def test_late_completion_after_skipped_start_preserves_latest_chain(tmp_path):
    store = DiagnosticsStore(tmp_path)
    store.record(task="market_brief", model="claude-opus-5-5", previous=None, current="msg_new",
                 diagnostics=None, usage={}, complete=False, started_at=200)
    store.record(task="market_brief", model="claude-opus-5-5", previous=None, current="msg_old",
                 diagnostics=None, usage={}, complete=True, started_at=100)
    assert store.previous("market_brief") == "msg_new"


@pytest.mark.parametrize("provider", ["haiku", "opus"])
@pytest.mark.parametrize("missing", ["diagnostics", "cache_miss_reason"])
def test_real_sdk_missing_diagnostics_is_unknown(monkeypatch, tmp_path, missing, provider):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    task = "ai_jobs:news_impact" if provider == "haiku" else "market_brief"
    write(DiagnosticsStore(tmp_path), current="msg_before", task=task)
    def handler(request):
        sequence = events("msg_missing", haiku.MODEL if provider == "haiku" else "claude-opus-5-5", {}, cached=80)
        if missing == "diagnostics":
            sequence[0]["message"].pop("diagnostics")
        return response(sequence)
    original = anthropic.AsyncAnthropic
    monkeypatch.setattr(haiku, "AsyncAnthropic", lambda **kwargs: original(**kwargs,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler))))
    prepared = haiku.prepare_message(SimpleNamespace(anthropic_api_key=SecretStr("fake"), openai_timeout_seconds=10),
        instructions="system", input_text="input", schema={"type": "object"}, max_tokens=100, job_type="news_impact")
    if provider == "haiku":
        asyncio.run(haiku.stream_message(prepared))
    else:
        client = original(api_key="fake", max_retries=0,
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
        config = BriefRunConfig()
        request = opus.build_request(config=config, system_text="system", user_text="input")
        assert opus.invoke(client, request, config=config).error_code is None
    drain()
    latest = DiagnosticsStore(tmp_path).read()["records"][-1]
    assert latest["diagnostic_state"] == "unknown"
    assert verdict(latest) == "comparison_unavailable"


@pytest.mark.parametrize("target", ["data", "lock", "symlink"])
def test_special_files_are_rejected_without_waiting(tmp_path, target):
    store = DiagnosticsStore(tmp_path)
    if target == "data":
        os.mkfifo(store.path())
    elif target == "lock":
        os.mkfifo(store.path().with_suffix(".lock"))
    else:
        secret = tmp_path / "other-file"
        secret.write_text("untouched")
        store.path().symlink_to(secret)
    started = time.monotonic()
    assert store.read()["available"] is False
    write(store)
    assert time.monotonic() - started < 0.1
    if target == "symlink":
        assert secret.read_text() == "untouched"


def test_slow_replace_does_not_block_opus_deadline_or_event_loop(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    real_replace = os.replace
    def slow_replace(*args):
        time.sleep(0.12)
        return real_replace(*args)
    monkeypatch.setattr(diagnostics_module.os, "replace", slow_replace)
    def handler(request):
        return response(events("msg_slow", "claude-opus-5-5", None))
    client = anthropic.AsyncAnthropic(api_key="fake", max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    config = replace(BriefRunConfig(), request_timeout_seconds=0.05)
    request = opus.build_request(config=config, system_text="system", user_text="input")
    ticks = []
    async def run():
        async def tick():
            await asyncio.sleep(0.015)
            ticks.append(time.monotonic())
        task = asyncio.create_task(tick())
        result = await opus._invoke(client, request, config=config, deadline=None, clock=time.monotonic, on_submission=None)
        await task
        return result
    started = time.monotonic()
    result = asyncio.run(run())
    elapsed = time.monotonic() - started
    assert result.error_code is None
    assert elapsed < 0.10  # old implementation waited for two 120ms replacements
    assert ticks[0] - started < 0.08
    assert diagnostics_module._worker.daemon
    assert diagnostics_module._work.maxsize == 64
    drain()


def test_slow_background_read_is_bounded_and_does_not_delay_shutdown(monkeypatch, tmp_path):
    background = diagnostics_module.BackgroundDiagnosticsStore(tmp_path)
    def slow_read():
        time.sleep(0.12)
        return {"records": [], "available": False}
    monkeypatch.setattr(background.store, "read", slow_read)
    started = time.monotonic()
    assert asyncio.run(background.previous("market_brief")) is None
    assert time.monotonic() - started < 0.08
    drain()


def test_queued_io_keeps_original_data_dir(monkeypatch, tmp_path):
    original_root = tmp_path / "original"
    other_root = tmp_path / "other"
    original_root.mkdir()
    other_root.mkdir()
    monkeypatch.setenv("DATA_DIR", str(original_root))
    background = diagnostics_module.BackgroundDiagnosticsStore()
    gate = threading.Event()
    entered = threading.Event()
    def block():
        entered.set()
        gate.wait(1)
    assert diagnostics_module._submit(block)
    assert entered.wait(1)
    try:
        background.record(task="market_brief", model="claude-opus-5-5", previous=None,
            current="msg_original", diagnostics=None, usage={}, complete=True)
        monkeypatch.setenv("DATA_DIR", str(other_root))
    finally:
        gate.set()
    drain()
    assert DiagnosticsStore(original_root).previous("market_brief") == "msg_original"
    assert not DiagnosticsStore(other_root).path().exists()


def test_invalid_background_root_stays_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", "relative-invalid")
    background = diagnostics_module.BackgroundDiagnosticsStore()
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert asyncio.run(background.previous("market_brief")) is None
    background.record(task="market_brief", model="claude-opus-5-5", previous=None,
        current="msg_disabled", diagnostics=None, usage={}, complete=True)
    drain()
    assert not DiagnosticsStore(tmp_path).path().exists()
