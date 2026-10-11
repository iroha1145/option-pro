"""Paid native streams are drained; only further requests need admission."""
import asyncio
import copy
import json

import httpx2
import pytest

from app.services.ai_jobs import claude_provider as provider, worker
from app.services.ai_jobs.repository import AIJobRepository
from test_claude_provider import _call, _sse, _tool_events, _transport_client
from test_stock_ai_model import create, message, result, settings


def tool_blocks(name, count):
    blocks = []
    for index in range(count):
        ident = f"srv_{name}_{index}"
        blocks.append(_call(name, ident))
        if name == "web_search":
            content = {"type": "web_search_tool_result_error", "error_code": "max_uses_exceeded"}
        elif name == "web_fetch":
            content = {"type": "web_fetch_tool_result_error", "error_code": "max_uses_exceeded"}
        else:
            content = {"type": "code_execution_result", "stdout": "ok", "stderr": "", "return_code": 0, "content": []}
        blocks.append({"type": provider._RESULT_TYPES[name], "tool_use_id": ident, "content": content})
    return blocks


@pytest.mark.parametrize("name,count", [("code_execution", 2), ("code_execution", 3), ("web_search", 8), ("web_fetch", 6)])
@pytest.mark.parametrize("stop", ["end_turn", "pause_turn"])
def test_real_sdk_drains_excess_native_operations_and_worker_saves_receipt(tmp_path, monkeypatch, name, count, stop):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    requests = []
    blocks = tool_blocks(name, count) + [{"type": "text", "text": json.dumps(result(), ensure_ascii=False)}]
    events = _tool_events(blocks, stop_reason=stop, usage={
        "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
        "server_tool_use": {"web_search_requests": 6 if name == "web_search" else 0,
                            "web_fetch_requests": 4 if name == "web_fetch" else 0},
    })
    events[0]["message"]["model"] = "claude-sonnet-5-5"
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(events))
    clients, options = _transport_client(monkeypatch, handler)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    row = repo.get_job(job_id)
    receipt = repo.get_provider_result(job_id)
    assert receipt is not None and receipt["usage"]["output_tokens"] == 20
    assert len(receipt["rounds"]) == 1
    assert receipt["usage"]["code_execution_requests"] == (count if name == "code_execution" else 0)
    assert row["status"] == ("completed" if stop == "end_turn" else "failed")
    assert receipt["terminal_error"] == (None if stop == "end_turn" else "provider_tool_limit_exceeded")
    if stop == "end_turn":
        assert json.loads(receipt["output_text"])["report"]["version"] == "stock-report-v1"
    else:
        assert row["error_code"] == "provider_tool_limit_exceeded"
    asyncio.run(worker.run_once(repo, settings(repo.path), "next-worker"))
    assert len(requests) == 1 and options[0]["max_retries"] == 0
    assert clients[0].is_closed()


@pytest.mark.parametrize("search_used,fetch_used,expected_requests", [(2, 1, 2), (6, 0, 1), (0, 4, 1), (7, 0, 1)])
def test_stock_continuation_uses_remaining_provider_allowance(tmp_path, monkeypatch, search_used, fetch_used, expected_requests):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(copy.deepcopy(prepared.params))
        current = message(message_id=f"msg_{len(calls)}", stop="pause_turn" if len(calls) == 1 else "end_turn")
        if len(calls) == 1:
            current.usage.server_tool_use.web_search_requests = search_used
            current.usage.server_tool_use.web_fetch_requests = fetch_used
        await on_message_start(current.id)
        return current
    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    assert len(calls) == expected_requests
    initial_tools = {item["name"]: item for item in calls[0]["tools"]}
    assert initial_tools["web_search"]["max_uses"] == 6
    assert initial_tools["web_fetch"]["max_uses"] == 4
    assert calls[0]["max_tokens"] == 65536
    if expected_requests == 2:
        tools = {item["name"]: item for item in calls[1]["tools"]}
        assert tools["web_search"]["max_uses"] == 6 - search_used
        assert tools["web_fetch"]["max_uses"] == 4 - fetch_used
        assert calls[1]["max_tokens"] == 65436
        assert repo.get_job(job_id)["status"] == "completed"
    else:
        assert repo.get_job(job_id)["error_code"] == "provider_tool_limit_exceeded"


def test_interrupted_stock_stream_stays_unknown_without_retry(tmp_path, monkeypatch):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    events = _tool_events(tool_blocks("code_execution", 3))[:-2]
    events[0]["message"]["model"] = "claude-sonnet-5-5"
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(events))
    _transport_client(monkeypatch, handler)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    assert repo.get_job(job_id)["error_code"] == "submission_outcome_unknown"
    assert repo.get_provider_result(job_id) is None
    asyncio.run(worker.run_once(repo, settings(repo.path), "next-worker"))
    assert len(requests) == 1


@pytest.mark.parametrize("stop", ["pause_turn", "end_turn"])
def test_cumulative_budget_blocks_third_request_including_progress_only_end_turn(tmp_path, monkeypatch, stop):
    repo = AIJobRepository(tmp_path / "jobs.db")
    job_id = create(repo)
    calls = []
    async def stream(prepared, *, on_message_start, tool_counts):
        calls.append(copy.deepcopy(prepared.params))
        current = message(message_id=f"msg_{len(calls)}", stop=stop)
        current.content[0].text = "Still researching"
        current.usage.server_tool_use.web_search_requests = 2 if len(calls) == 1 else 4
        await on_message_start(current.id)
        return current
    monkeypatch.setattr(provider, "stream_message", stream)
    asyncio.run(worker.run_once(repo, settings(repo.path), "offline-worker"))
    receipt = repo.get_provider_result(job_id)
    assert len(calls) == 2
    assert receipt["terminal_error"] == "provider_tool_limit_exceeded"
    assert receipt["usage"]["web_search_requests"] == 6
    assert len(receipt["rounds"]) == 2
    assert next(item for item in calls[1]["tools"] if item["name"] == "web_search")["max_uses"] == 4
