from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from pydantic import SecretStr

from app.services.ai_jobs import claude_provider as provider


def _prepared(**overrides):
    settings = SimpleNamespace(
        anthropic_api_key=SecretStr("unit-test-key"), openai_timeout_seconds=75,
    )
    return provider.prepare_message(
        settings,
        instructions="Stable instructions",
        input_text="Private task input",
        schema=overrides.get("schema", {
            "type": "object", "properties": {"answer": {"type": "string"}},
            "required": ["answer"], "additionalProperties": False,
        }),
        max_tokens=4096,
        tools=overrides.get("tools"),
    )


def _message(*, text='{"answer":"ok"}', stop_reason="end_turn", **usage):
    return anthropic.types.Message(
        id="msg_test", model=provider.MODEL, role="assistant", type="message",
        content=[{"type": "text", "text": text}], stop_reason=stop_reason,
        usage={"input_tokens": 10, "output_tokens": 20, **usage},
    )


def test_prepare_exact_model_cache_and_transformed_schema():
    schema = {
        "type": "object", "properties": {
            "answer": {"type": "integer", "minimum": 1, "maximum": 10},
        }, "required": ["answer"],
    }
    original = copy.deepcopy(schema)
    prepared = _prepared(schema=schema)
    params = prepared.params
    assert params["model"] == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "adaptive"}
    assert params["output_config"]["effort"] == "xhigh"
    transformed = params["output_config"]["format"]["schema"]
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert "minimum" not in transformed["properties"]["answer"]
    assert "minimum: 1" in transformed["properties"]["answer"]["description"]
    assert transformed["additionalProperties"] is False
    assert schema == original
    assert params["system"] == [{
        "type": "text", "text": "Stable instructions",
        "cache_control": {"type": "ephemeral", "ttl": "5m"},
    }]
    assert params["messages"] == [{
        "role": "user", "content": [{"type": "text", "text": "Private task input"}],
    }]
    assert params["max_tokens"] == 4096
    assert not {"tools", "citations", "cache_control", "temperature"} & params.keys()
    assert "unit-test-key" not in repr(prepared)


def test_prepare_missing_anthropic_key_never_uses_openai_key(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated")
    with pytest.raises(RuntimeError, match="ai_not_configured"):
        provider.prepare_message(
            SimpleNamespace(anthropic_api_key=SecretStr("  "), openai_timeout_seconds=75),
            instructions="system", input_text="input", schema={}, max_tokens=100,
        )


def _sse_events():
    return [
        {"type": "message_start", "message": {
            "id": "msg_stream", "type": "message", "role": "assistant",
            "model": provider.MODEL, "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 10, "output_tokens": 1},
        }},
        {"type": "content_block_start", "index": 0,
         "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": '{"answer":'}},
        {"type": "content_block_delta", "index": 0,
         "delta": {"type": "text_delta", "text": '"ok"}'}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    ]


def _sse(events):
    return "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)


def _transport_client(monkeypatch, handler):
    clients = []
    options = []
    def factory(**kwargs):
        options.append(kwargs)
        client = anthropic.AsyncAnthropic(
            **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        clients.append(client)
        return client
    monkeypatch.setattr(provider, "AsyncAnthropic", factory)
    return clients, options


def test_stream_uses_sdk_accumulation_and_callback_and_closes_client(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(_sse_events()))
    clients, options = _transport_client(monkeypatch, handler)
    seen = []
    async def started(message_id):
        seen.append(message_id)
    message = asyncio.run(provider.stream_message(_prepared(), on_message_start=started))
    assert seen == ["msg_stream"]
    assert provider.response_text(message) == '{"answer":"ok"}'
    assert message.usage.output_tokens == 20
    assert message.stop_reason == "end_turn"
    assert len(requests) == 1
    assert str(requests[0].url) == "https://api.anthropic.com/v1/messages"
    request_data = json.loads(requests[0].content)
    assert request_data["stream"] is True
    assert request_data["output_config"]["effort"] == "xhigh"
    assert options[0]["max_retries"] == 0
    assert options[0]["timeout"].read == 75
    assert options[0]["timeout"].connect == 30
    assert clients[0].is_closed()


def test_http_failure_is_not_retried(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "busy"}})
    clients, _ = _transport_client(monkeypatch, handler)
    with pytest.raises(anthropic.APIStatusError) as caught:
        asyncio.run(provider.stream_message(_prepared()))
    assert caught.value.status_code == 503
    assert len(requests) == 1
    assert clients[0].is_closed()


def test_stream_error_after_partial_text_propagates(monkeypatch):
    events = _sse_events()[:3] + [{
        "type": "error", "error": {"type": "overloaded_error", "message": "stream interrupted"},
    }]
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(events))
    clients, _ = _transport_client(monkeypatch, handler)
    with pytest.raises(anthropic.APIError, match="stream interrupted"):
        asyncio.run(provider.stream_message(_prepared()))
    assert len(requests) == 1
    assert clients[0].is_closed()


def test_callback_failure_propagates_and_closes(monkeypatch):
    clients, _ = _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_sse_events()),
    ))
    failure = RuntimeError("receipt persistence failed")
    async def started(message_id):
        raise failure
    with pytest.raises(RuntimeError) as caught:
        asyncio.run(provider.stream_message(_prepared(), on_message_start=started))
    assert caught.value is failure
    assert clients[0].is_closed()


@pytest.mark.parametrize("event_count", [3, 6])
def test_premature_eof_never_returns_partial_message(monkeypatch, event_count):
    clients, _ = _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_sse_events()[:event_count]),
    ))
    with pytest.raises(RuntimeError, match="provider_stream_incomplete"):
        asyncio.run(provider.stream_message(_prepared()))
    assert clients[0].is_closed()


def test_cancellation_propagates_and_closes(monkeypatch):
    clients, _ = _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_sse_events()),
    ))
    async def started(message_id):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.stream_message(_prepared(), on_message_start=started))
    assert clients[0].is_closed()


def test_fresh_clients_follow_changed_secret_timeout_and_ignore_base_url_env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://unrelated.invalid")
    clients, options = _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_sse_events()),
    ))
    first = _prepared()
    second = provider.prepare_message(
        SimpleNamespace(anthropic_api_key=SecretStr("rotated-test-key"), openai_timeout_seconds=150),
        instructions="system", input_text="input", schema={"type": "object"}, max_tokens=100,
    )
    asyncio.run(provider.stream_message(first))
    asyncio.run(provider.stream_message(second))
    assert clients[0] is not clients[1]
    assert options[1]["api_key"] == "rotated-test-key"
    assert options[1]["timeout"].read == 150
    assert all(option["base_url"] == provider.BASE_URL for option in options)
    assert all(client.is_closed() for client in clients)


def test_response_text_excludes_thinking_and_redacted_blocks():
    message = _message()
    message.content = [
        anthropic.types.ThinkingBlock(type="thinking", thinking="private reasoning", signature="signature"),
        anthropic.types.TextBlock(type="text", text='{"answer":'),
        anthropic.types.RedactedThinkingBlock(type="redacted_thinking", data="redacted"),
        anthropic.types.TextBlock(type="text", text='"ok"}'),
    ]
    assert provider.response_text(message) == '{"answer":"ok"}'


def test_usage_cache_and_thinking_counts_are_not_double_counted():
    usage = provider.response_usage(_message(
        cache_creation_input_tokens=300, cache_read_input_tokens=500,
        cache_creation={"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 100},
        output_tokens_details={"thinking_tokens": 15},
    ))
    assert usage == {
        "input_tokens": 810, "cached_input_tokens": 500,
        "cache_creation_input_tokens": 300, "cache_creation_5m_input_tokens": 200,
        "cache_creation_1h_input_tokens": 100, "output_tokens": 20,
        "reasoning_tokens": 15, "total_tokens": 830,
    }


def test_usage_missing_fields_remain_unknown_and_zero_is_preserved():
    missing = provider.response_usage(_message())
    assert missing["cached_input_tokens"] is None
    assert missing["cache_creation_input_tokens"] is None
    assert missing["reasoning_tokens"] is None
    assert missing["input_tokens"] == 10
    assert missing["total_tokens"] == 30
    zero = provider.response_usage(_message(cache_read_input_tokens=0, cache_creation_input_tokens=0))
    assert zero["cached_input_tokens"] == 0
    assert zero["cache_creation_input_tokens"] == 0
    assert zero["total_tokens"] == 30


@pytest.mark.parametrize(("stop_reason", "text", "error"), [
    ("end_turn", '{"answer":"ok"}', None),
    ("end_turn", "  ", "provider_empty_response"),
    ("max_tokens", "partial", "provider_incomplete_max_output_tokens"),
    ("refusal", "refused", "provider_refusal"),
    ("tool_use", "", "provider_incomplete"),
    ("pause_turn", "", "provider_incomplete"),
    ("stop_sequence", "", "provider_incomplete"),
    (None, "", "provider_incomplete"),
])
def test_terminal_stop_reasons(stop_reason, text, error):
    assert provider.response_terminal_error(_message(stop_reason=stop_reason, text=text)) == error


def _tools():
    return [
        {"type": "web_search_20260318", "name": "web_search", "max_uses": 1, "allowed_callers": ["direct"]},
        {"type": "web_fetch_20260318", "name": "web_fetch", "max_uses": 1, "max_content_tokens": 8000,
         "citations": {"enabled": False}, "allowed_callers": ["direct"]},
        {"type": "code_execution_20260120", "name": "code_execution"},
    ]


def _call(name="web_search", call_id="srv_search"):
    return {"type": "server_tool_use", "id": call_id, "name": name, "input": {"query": "public facts"}}


def _search_result(call_id="srv_search", *, urls=None):
    return {"type": "web_search_tool_result", "tool_use_id": call_id, "content": [
        {"type": "web_search_result", "title": "Official source", "url": url, "encrypted_content": "opaque"}
        for url in (urls or ["https://www.anthropic.com/news"])
    ]}


def _final(name=provider.RESULT_TOOL_NAME, call_id="tool_final"):
    return {"type": "tool_use", "id": call_id, "name": name, "input": {"answer": "ok"}}


def _tool_events(blocks, *, stop_reason="tool_use", usage=None):
    start = _sse_events()[0]
    start["message"]["usage"].update(usage or {})
    events = [start]
    for index, block in enumerate(blocks):
        start_block = copy.deepcopy(block)
        if block["type"] in {"tool_use", "server_tool_use"}:
            start_block["input"] = {}
        events.append({"type": "content_block_start", "index": index, "content_block": start_block})
        if block["type"] in {"tool_use", "server_tool_use"}:
            events.append({"type": "content_block_delta", "index": index, "delta": {
                "type": "input_json_delta", "partial_json": json.dumps(block["input"]),
            }})
        events.append({"type": "content_block_stop", "index": index})
    events += [
        {"type": "message_delta", "delta": {"stop_reason": stop_reason, "stop_sequence": None}, "usage": {"output_tokens": 20}},
        {"type": "message_stop"},
    ]
    return events


def _stream_tools(monkeypatch, blocks, *, stop_reason="tool_use", usage=None):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(
            _tool_events(blocks, stop_reason=stop_reason, usage=usage),
        ))
    clients, options = _transport_client(monkeypatch, handler)
    message = asyncio.run(provider.stream_message(_prepared(tools=_tools())))
    assert len(requests) == 1
    assert clients[0].is_closed()
    return message, requests, options


def test_native_search_uses_strict_final_tool_and_sdk_stream(monkeypatch):
    tools = _tools()
    original = copy.deepcopy(tools)
    prepared = _prepared(tools=tools)
    assert prepared.params["output_config"] == {"effort": "xhigh"}
    assert prepared.params["tool_choice"] == {"type": "auto", "disable_parallel_tool_use": True}
    final = prepared.params["tools"][-1]
    assert final["name"] == "record_analysis" and final["strict"] is True
    assert final["allowed_callers"] == ["direct"]
    assert final["input_schema"]["additionalProperties"] is False
    assert prepared.params["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    assert "record_analysis" in prepared.params["system"][0]["text"]
    assert tools == original
    message, requests, _ = _stream_tools(monkeypatch, [
        {"type": "text", "text": "Intermediate explanation"},
        _call(), _search_result(), _final(),
    ], usage={"server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 0}})
    body = json.loads(requests[0].content)
    assert body["tools"][:3] == tools
    assert "format" not in body["output_config"]
    assert provider.response_text(message) == '{"answer":"ok"}'
    assert provider.response_terminal_error(message) is None
    assert provider.response_sources(message) == [{
        "title": "Official source", "url": "https://www.anthropic.com/news", "type": "web_search",
    }]
    usage = provider.response_usage(message)
    assert usage["web_search_requests"] == 1
    assert usage["web_fetch_requests"] == 0
    assert usage["code_execution_requests"] == 0


@pytest.mark.parametrize(("blocks", "error"), [
    ([_call(), _search_result()], "provider_invalid_final_tool"),
    ([_final(), _final(call_id="tool_second")], "provider_invalid_final_tool"),
    ([_final(name="unknown_client_tool")], "provider_unknown_client_tool"),
    ([_call(), _final()], "provider_incomplete_tool_result"),
    ([_search_result(), _final()], "provider_invalid_tool_response"),
    ([_call(), _search_result(), _search_result(), _final()], "provider_invalid_tool_response"),
])
def test_invalid_tool_completions_are_not_published(monkeypatch, blocks, error):
    message, _, _ = _stream_tools(monkeypatch, blocks)
    assert provider.response_terminal_error(message) == error
    assert provider.response_text(message) == ""


def test_tools_requested_without_final_tool_does_not_accept_plain_text(monkeypatch):
    message, _, _ = _stream_tools(monkeypatch, [{"type": "text", "text": '{"answer":"not-final"}'}], stop_reason="end_turn")
    assert provider.response_terminal_error(message) == "provider_incomplete"
    assert provider.response_text(message) == ""


@pytest.mark.parametrize("stop_reason", ["pause_turn", "max_tokens", "refusal"])
def test_tool_terminal_interruptions_are_not_continued_or_published(monkeypatch, stop_reason):
    message, requests, _ = _stream_tools(monkeypatch, [_call(), _search_result()], stop_reason=stop_reason)
    assert provider.response_terminal_error(message) is not None
    assert provider.response_text(message) == ""
    assert len(requests) == 1


@pytest.mark.parametrize(("name", "count"), [("web_search", 2), ("web_fetch", 2), ("bash_code_execution", 3)])
def test_native_tool_limit_stops_stream_without_retry(monkeypatch, name, count):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=_sse(
            _tool_events([_call(name, f"srv_{index}") for index in range(count)]),
        ))
    clients, _ = _transport_client(monkeypatch, handler)
    with pytest.raises(RuntimeError, match="provider_tool_limit_exceeded"):
        asyncio.run(provider.stream_message(_prepared(tools=_tools())))
    assert len(requests) == 1
    assert clients[0].is_closed()


def test_code_limit_is_shared_by_all_native_code_variants(monkeypatch):
    blocks = [
        _call("bash_code_execution", "srv_bash"),
        _call("text_editor_code_execution", "srv_editor"),
        _call("code_execution", "srv_python"),
    ]
    clients, _ = _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(_tool_events(blocks)),
    ))
    with pytest.raises(RuntimeError, match="provider_tool_limit_exceeded"):
        asyncio.run(provider.stream_message(_prepared(tools=_tools())))
    assert clients[0].is_closed()


def test_final_tool_input_cannot_be_called_by_another_server_tool(monkeypatch):
    final = _final()
    final["caller"] = {"type": "code_execution_20260120", "tool_id": "srv_code"}
    message, _, _ = _stream_tools(monkeypatch, [final])
    assert provider.response_terminal_error(message) == "provider_invalid_final_tool"
    assert provider.response_text(message) == ""


def test_native_fetch_and_code_usage_and_sources(monkeypatch):
    blocks = [
        _call("web_fetch", "srv_fetch"),
        {"type": "web_fetch_tool_result", "tool_use_id": "srv_fetch", "content": {
            "type": "web_fetch_result", "url": "https://www.anthropic.com/docs", "content": {
                "type": "document", "title": "Fetched document", "source": {
                    "type": "text", "media_type": "text/plain", "data": "Private fetched body",
                },
            },
        }},
        _call("bash_code_execution", "srv_code"),
        {"type": "bash_code_execution_tool_result", "tool_use_id": "srv_code", "content": {
            "type": "bash_code_execution_result", "stdout": "private calculations", "stderr": "", "return_code": 0, "content": [],
        }},
        _final(),
    ]
    message, _, _ = _stream_tools(monkeypatch, blocks, usage={
        "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 1},
    })
    assert provider.response_terminal_error(message) is None
    assert provider.response_usage(message)["code_execution_requests"] == 1
    assert provider.response_sources(message) == [{
        "title": "Fetched document", "url": "https://www.anthropic.com/docs", "type": "web_fetch",
    }]
    assert "Private fetched body" not in json.dumps(provider.response_sources(message))


def test_missing_native_usage_counts_default_to_zero(monkeypatch):
    message, _, _ = _stream_tools(monkeypatch, [_call(), _search_result(), _final()], usage={
        "server_tool_use": {"web_search_requests": 1},
    })
    usage = provider.response_usage(message)
    assert usage["web_search_requests"] == 1
    assert usage["web_fetch_requests"] == 0
    assert usage["code_execution_requests"] == 0


def test_sources_are_bounded_deduplicated_and_reject_private_or_unsafe_urls(monkeypatch):
    urls = [
        "http://127.0.0.1/admin", "https://user:password@example.org/private",
        "file:///etc/passwd", "https://localhost/a", "http://10.0.0.1/a",
        "http://[::1]/a", "https://host.internal/a", "http://127.1/a",
        "https://www.anthropic.com/news", "https://www.anthropic.com/news#fragment",
    ] + [f"https://www.anthropic.com/source/{index}" for index in range(20)]
    message, _, _ = _stream_tools(monkeypatch, [_call(), _search_result(urls=urls), _final()])
    sources = provider.response_sources(message)
    assert len(sources) == 10
    assert len({entry["url"] for entry in sources}) == 10
    assert all(entry["url"].startswith("https://www.anthropic.com/") for entry in sources)


def test_sources_include_actual_citations_without_thinking_or_cited_body():
    message = _message()
    message.content = [anthropic.types.TextBlock(type="text", text="explanation", citations=[{
        "type": "web_search_result_location", "title": "Citation", "url": "https://www.anthropic.com/source",
        "cited_text": "private quoted body", "encrypted_index": "encrypted",
    }])]
    assert provider.response_sources(message) == [{
        "title": "Citation", "url": "https://www.anthropic.com/source", "type": "web_search",
    }]


@pytest.mark.parametrize("final_details", [None, {
    "ephemeral_5m_input_tokens": 14000, "ephemeral_1h_input_tokens": 7796,
}])
def test_server_tool_cumulative_usage_does_not_retain_initial_cache_split(monkeypatch, final_details):
    from app.services.ai_jobs.runtime import settled_usage_cost_microusd

    events = _tool_events([_call(), _search_result(), _final()], usage={
        "input_tokens": 30, "cache_creation_input_tokens": 7721,
        "cache_read_input_tokens": 0, "cache_creation": {
            "ephemeral_5m_input_tokens": 7721, "ephemeral_1h_input_tokens": 0,
        },
    })
    # A server-side tool loop emits cumulative receipts, not per-round additions.
    intermediate = {"type": "message_delta", "delta": {
        "stop_reason": None, "stop_sequence": None,
    }, "usage": {"input_tokens": 70, "cache_creation_input_tokens": 12000,
                 "cache_read_input_tokens": 7721, "output_tokens": 2000}}
    final_usage = {
        "input_tokens": 124, "cache_creation_input_tokens": 21796,
        "cache_read_input_tokens": 23125, "output_tokens": 8759,
        "output_tokens_details": {"thinking_tokens": 8393},
        "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 1},
    }
    if final_details is not None:
        final_usage["cache_creation"] = final_details
    events[-2]["usage"] = final_usage
    events.insert(-2, intermediate)
    # A later output-only receipt must not revive the initial breakdown.
    events.insert(-1, {"type": "message_delta", "delta": {
        "stop_reason": "tool_use", "stop_sequence": None,
    }, "usage": {"output_tokens": 8759}})
    _transport_client(monkeypatch, lambda request: httpx2.Response(
        200, headers={"content-type": "text/event-stream"}, text=_sse(events),
    ))
    message = asyncio.run(provider.stream_message(_prepared(tools=_tools())))
    usage = provider.response_usage(message)
    assert usage["input_tokens"] == 45045
    assert usage["cache_creation_input_tokens"] == 21796
    assert usage["cached_input_tokens"] == 23125
    assert usage["output_tokens"] == 8759
    assert usage["reasoning_tokens"] == 8393
    assert usage["web_search_requests"] == usage["web_fetch_requests"] == 1
    assert usage["cache_creation_5m_input_tokens"] == (14000 if final_details else None)
    assert usage["cache_creation_1h_input_tokens"] == (7796 if final_details else None)
    unknown_cost = settled_usage_cost_microusd("earnings_impact", usage, fallback_microusd=757880)
    known_hour = {**usage, "cache_creation_5m_input_tokens": 0,
                  "cache_creation_1h_input_tokens": 21796}
    hour_cost = settled_usage_cost_microusd("earnings_impact", known_hour, fallback_microusd=757880)
    assert 0 < unknown_cost <= hour_cost < 757880
    if final_details is None:
        assert unknown_cost == hour_cost
    malformed = {**usage, "cache_creation_5m_input_tokens": 7721,
                 "cache_creation_1h_input_tokens": 0}
    assert settled_usage_cost_microusd("earnings_impact", malformed, fallback_microusd=757880) == 757880


def test_output_only_delta_retains_valid_initial_cache_split(monkeypatch):
    message, _, _ = _stream_tools(monkeypatch, [_final()], usage={
        "cache_creation_input_tokens": 300,
        "cache_creation": {"ephemeral_5m_input_tokens": 200, "ephemeral_1h_input_tokens": 100},
    })
    usage = provider.response_usage(message)
    assert usage["cache_creation_5m_input_tokens"] == 200
    assert usage["cache_creation_1h_input_tokens"] == 100
