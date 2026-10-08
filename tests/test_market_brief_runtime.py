from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from app.services.market_brief import claude_runtime as runtime
from app.services.market_brief import errors
from app.services.market_brief.runner import BriefRunConfig

CONFIG = BriefRunConfig()
SAMPLE_RESULT = json.loads(
    (Path(__file__).parent / "fixtures" / "market_brief_sample.json").read_text(encoding="utf-8")
)["brief"]["result"]


def usage(
    *,
    input_tokens: int = 1000,
    output_tokens: int = 500,
    cache_write: int = 0,
    cache_1h: int = 0,
    cache_5m: int = 0,
    cache_read: int = 0,
    searches: int = 0,
    fetches: int = 0,
    iterations: Any = None,
) -> SimpleNamespace:
    namespace = SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_write,
        cache_read_input_tokens=cache_read,
        cache_creation=SimpleNamespace(ephemeral_1h_input_tokens=cache_1h, ephemeral_5m_input_tokens=cache_5m),
        server_tool_use=SimpleNamespace(web_search_requests=searches, web_fetch_requests=fetches),
    )
    if iterations is not None:
        namespace.iterations = iterations
    return namespace


def message(
    content: list[Any],
    *,
    stop_reason: str = "end_turn",
    message_usage: SimpleNamespace | None = None,
    stop_details: Any = None,
    message_id: str = "msg_1",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=message_id,
        model="claude-opus-5-5",
        content=content,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=message_usage or usage(),
    )


def text(value: str) -> SimpleNamespace:
    return SimpleNamespace(type="text", text=value)


def json_text(value: Any = None) -> SimpleNamespace:
    return text(json.dumps(SAMPLE_RESULT if value is None else value, ensure_ascii=False))


class FakeStream:
    def __init__(self, outcome: Any) -> None:
        self._outcome = outcome

    def __enter__(self) -> FakeStream:
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None

    def get_final_message(self) -> Any:
        return self._outcome


class FakeMessages:
    def __init__(self, outcomes: list[Any]) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def stream(self, **kwargs: Any) -> FakeStream:
        self.calls.append(kwargs)
        return FakeStream(self.outcomes.pop(0))


class FakeClient:
    def __init__(self, outcomes: list[Any]) -> None:
        self.messages = FakeMessages(outcomes)
        self.beta = SimpleNamespace(messages=FakeMessages(outcomes))


def request(config: BriefRunConfig = CONFIG) -> dict[str, Any]:
    return runtime.build_request(config=config, system_text="系统提示词" * 200, user_text="证据")


def _status_error(cls: type[anthropic.APIStatusError], status: int, body: Any = None) -> anthropic.APIStatusError:
    response = httpx2.Response(status, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages"))
    return cls(f"Error code: {status}", response=response, body=body)


def test_request_shape_follows_the_documented_parameters() -> None:
    built = request()
    assert built["model"] == "claude-opus-5-5"
    assert built["max_tokens"] == CONFIG.max_output_tokens
    assert "thinking" not in built and "temperature" not in built
    assert built["system"][0]["cache_control"] == {"type": "ephemeral", "ttl": "5m"}
    # 顶层自动缓存固定 5 分钟：1 小时断点在前、5 分钟尾巴在后是允许的顺序，反过来会被拒。
    assert built["cache_control"] == {"type": "ephemeral"}
    assert built["messages"] == [{"role": "user", "content": [{"type": "text", "text": "证据"}]}]
    assert [tool["type"] for tool in built["tools"]] == ["web_search_20260209", "web_fetch_20260209"]
    assert built["tools"][0]["user_location"] == {"type": "approximate", "country": "US", "timezone": "America/New_York"}
    assert built["tools"][0]["max_uses"] == CONFIG.web_search_max_uses
    assert built["tools"][1]["max_content_tokens"] == CONFIG.web_fetch_max_content_tokens
    assert built["output_config"]["effort"] == "xhigh"
    schema = built["output_config"]["format"]
    assert schema["type"] == "json_schema"
    assert schema["schema"]["additionalProperties"] is False
    assert "headline" in schema["schema"]["properties"]
    assert "betas" not in built and "fallbacks" not in built

    with_code = request(replace(CONFIG, code_execution_tool=True, refusal_fallback=True))
    assert with_code["tools"][-1] == {"type": "code_execution_20260521", "name": "code_execution"}
    assert with_code["betas"] == ["server-side-fallback-2026-07-01"]
    assert with_code["fallbacks"] == "default"
    with pytest.raises(ValueError):
        request(replace(CONFIG, prompt_cache_ttl="10m"))


def test_structured_output_can_be_switched_off() -> None:
    from app.services.market_brief.prompt import build_system_prompt

    config = replace(CONFIG, structured_output=False)
    built = request(config)
    assert built["output_config"] == {"effort": "xhigh"}
    assert "## 输出 JSON Schema" not in build_system_prompt(CONFIG)
    prompt = build_system_prompt(config)
    assert "## 输出 JSON Schema" in prompt and '"headline"' in prompt
    assert prompt == build_system_prompt(config)  # 逐字节稳定，缓存前缀不变
    assert "2026" not in prompt


def test_fenced_json_is_accepted() -> None:
    encoded = json.dumps(SAMPLE_RESULT, ensure_ascii=False)
    client = FakeClient([message([text(f"```json\n{encoded}\n```")])])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code is None and result.parsed == SAMPLE_RESULT
    assert result.text == encoded + "\n"


def test_successful_response_is_parsed_with_usage_and_sources() -> None:
    content = [
        SimpleNamespace(type="thinking", thinking="", signature="sig"),
        SimpleNamespace(type="server_tool_use", id="srv_1", name="web_search", input={"query": "PPI release October 2026"}),
        SimpleNamespace(
            type="web_search_tool_result",
            tool_use_id="srv_1",
            content=[
                SimpleNamespace(type="web_search_result", url="https://www.bls.gov/ppi", title="PPI", encrypted_content="x"),
                SimpleNamespace(type="web_search_result", url="https://example.com/a", title="A", encrypted_content="y"),
            ],
        ),
        SimpleNamespace(type="server_tool_use", id="srv_2", name="web_fetch", input={"url": "https://www.bls.gov/ppi"}),
        SimpleNamespace(
            type="web_fetch_tool_result",
            tool_use_id="srv_2",
            content=SimpleNamespace(type="web_fetch_result", url="https://www.bls.gov/ppi", content=SimpleNamespace(title="BLS PPI")),
        ),
        SimpleNamespace(
            type="web_search_tool_result",
            tool_use_id="srv_3",
            content=SimpleNamespace(type="web_search_tool_result_error", error_code="max_uses_exceeded"),
        ),
        text("先核实一下日程。"),
        json_text(),
    ]
    client = FakeClient([message(content, message_usage=usage(cache_write=900, cache_1h=800, cache_5m=100, cache_read=50, searches=2, fetches=1))])
    result = runtime.invoke(client, request(), config=CONFIG)

    assert result.error_code is None
    assert result.parsed == SAMPLE_RESULT
    assert result.stop_reason == "end_turn"
    assert result.continuation_count == 0
    assert result.request_ids == ("msg_1",)
    assert result.usage == {
        "input_tokens": 1000,
        "output_tokens": 500,
        "cache_creation_input_tokens": 900,
        "cache_creation_1h_input_tokens": 800,
        "cache_creation_5m_input_tokens": 100,
        "cache_read_input_tokens": 50,
        "web_search_requests": 2,
        "web_fetch_requests": 1,
        "rounds": 1,
    }
    assert result.external_sources == (
        {"url": "https://www.bls.gov/ppi", "title": "PPI", "via": "web_fetch"},
        {"url": "https://example.com/a", "title": "A", "via": "web_search"},
        {"query": "PPI release October 2026", "via": "web_search"},
    )
    assert len(client.messages.calls) == 1
    assert client.beta.messages.calls == []


def test_pause_turn_resends_the_paused_content_once_then_succeeds() -> None:
    paused_content = [SimpleNamespace(type="server_tool_use", id="srv_1", name="web_search", input={"query": "q"})]
    client = FakeClient([
        message(paused_content, stop_reason="pause_turn", message_id="msg_1", message_usage=usage(output_tokens=300, searches=1)),
        message([json_text()], message_id="msg_2", message_usage=usage(output_tokens=700, searches=2)),
    ])
    built = request()
    result = runtime.invoke(client, built, config=CONFIG)

    assert result.error_code is None and result.parsed == SAMPLE_RESULT
    assert result.continuation_count == 1
    assert result.usage["output_tokens"] == 1000
    assert result.usage["web_search_requests"] == 3
    assert result.usage["rounds"] == 2
    first, second = client.messages.calls
    assert first["messages"] == built["messages"]
    # 续跑：原样追加 assistant 内容，不追加「继续」之类的用户消息；其余参数不变（缓存前缀一致）。
    assert second["messages"] == [*built["messages"], {"role": "assistant", "content": paused_content}]
    assert second["messages"][-1]["content"] is paused_content
    assert {key: value for key, value in second.items() if key not in {"messages", "timeout"}} == {
        key: value for key, value in built.items() if key != "messages"
    }


def ticking(*moments: float) -> Any:
    """按调用顺序返回给定时刻的单调时钟。"""

    values = iter(moments)
    return lambda: next(values)


def test_run_deadline_stops_before_a_continuation() -> None:
    assert errors.RUN_DEADLINE_EXCEEDED in errors.RUN_ERROR_CODES
    client = FakeClient([
        message([text("…")], stop_reason="pause_turn", message_usage=usage(output_tokens=800)),
        message([json_text()]),
    ])
    # 起算 0 秒，第一轮前 0 秒，续跑前已用 1400 秒：1500 秒的预算只剩 100 秒（低于 120 秒）。
    result = runtime.invoke(client, request(), config=CONFIG, clock=ticking(0.0, 0.0, 1400.0))
    assert result.error_code == errors.RUN_DEADLINE_EXCEEDED
    assert "remaining=100s before request 2" in (result.error_detail or "")
    assert len(client.messages.calls) == 1
    assert client.messages.calls[0]["timeout"] == 1500.0
    assert result.usage["output_tokens"] == 800 and result.usage["rounds"] == 1


def test_each_request_timeout_is_capped_by_the_remaining_budget() -> None:
    client = FakeClient([
        message([text("…")], stop_reason="pause_turn"),
        message([json_text()]),
    ])
    result = runtime.invoke(client, request(), config=CONFIG, clock=ticking(0.0, 0.0, 1000.0))
    assert result.error_code is None
    assert [call["timeout"] for call in client.messages.calls] == [1500.0, 500.0]


def test_run_deadline_counts_time_spent_before_the_first_request() -> None:
    # runner 从运行开始起算截止点：证据组装已经用掉预算时，一次请求都不发。
    client = FakeClient([message([json_text()])])
    result = runtime.invoke(client, request(), config=CONFIG, deadline=100.0, clock=lambda: 50.0)
    assert result.error_code == errors.RUN_DEADLINE_EXCEEDED
    assert client.messages.calls == []
    assert result.usage["rounds"] == 0 and result.stop_reason is None


def test_small_budgets_scale_the_minimum_remaining_time() -> None:
    config = replace(CONFIG, request_timeout_seconds=40.0)
    client = FakeClient([message([json_text()])])
    result = runtime.invoke(client, request(config), config=config, clock=ticking(0.0, 25.0))
    # 40 秒预算的门槛是 10 秒；剩 15 秒仍可发出，超时取剩余的 15 秒。
    assert result.error_code is None
    assert client.messages.calls[0]["timeout"] == 15.0


def test_budget_ceiling_stops_before_continuing() -> None:
    client = FakeClient([
        message([text("…")], stop_reason="pause_turn", message_usage=usage(output_tokens=CONFIG.output_token_ceiling + 1)),
        message([json_text()]),
    ])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.BUDGET_EXCEEDED
    assert len(client.messages.calls) == 1
    assert result.usage["output_tokens"] == CONFIG.output_token_ceiling + 1


def test_continuation_limit() -> None:
    config = replace(CONFIG, max_continuations=1)
    client = FakeClient([
        message([text("…")], stop_reason="pause_turn"),
        message([text("…")], stop_reason="pause_turn"),
    ])
    result = runtime.invoke(client, request(config), config=config)
    assert result.error_code == errors.CONTINUATION_LIMIT
    assert result.continuation_count == 1
    assert len(client.messages.calls) == 2


def test_refusal_records_the_category() -> None:
    details = SimpleNamespace(type="refusal", category="cyber", explanation="declined")
    client = FakeClient([message([], stop_reason="refusal", stop_details=details)])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.PROVIDER_REFUSAL
    assert "category=cyber" in (result.error_detail or "")
    assert result.parsed is None


@pytest.mark.parametrize("stop_reason", ["max_tokens", "model_context_window_exceeded"])
def test_truncated_output(stop_reason: str) -> None:
    client = FakeClient([message([text('{"headline": "半')], stop_reason=stop_reason)])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.OUTPUT_TRUNCATED
    assert result.text == '{"headline": "半'


def test_non_json_text_is_kept_without_retry() -> None:
    client = FakeClient([message([text("这不是 JSON")])])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.OUTPUT_NOT_JSON
    assert result.text == "这不是 JSON"
    assert len(client.messages.calls) == 1
    empty = runtime.invoke(FakeClient([message([])]), request(), config=CONFIG)
    assert empty.error_code == errors.OUTPUT_NOT_JSON and empty.error_detail == "no_text_block"


def test_json_split_across_text_blocks_is_joined() -> None:
    encoded = json.dumps(SAMPLE_RESULT, ensure_ascii=False)
    middle = len(encoded) // 2
    client = FakeClient([message([text(encoded[:middle]), text(encoded[middle:])])])
    assert runtime.invoke(client, request(), config=CONFIG).parsed == SAMPLE_RESULT


def test_unexpected_stop_reason() -> None:
    client = FakeClient([message([text("{}")], stop_reason="tool_use")])
    assert runtime.invoke(client, request(), config=CONFIG).error_code == errors.UNEXPECTED_STOP_REASON


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (_status_error(anthropic.AuthenticationError, 401), errors.PROVIDER_AUTH_FAILED),
        (_status_error(anthropic.PermissionDeniedError, 403), errors.PROVIDER_AUTH_FAILED),
        (_status_error(anthropic.RateLimitError, 429), errors.PROVIDER_RATE_LIMITED),
        (_status_error(anthropic.BadRequestError, 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "tools.0: bad"}}), errors.PROVIDER_REQUEST_REJECTED),
        (_status_error(anthropic.NotFoundError, 404), errors.PROVIDER_REQUEST_REJECTED),
        (_status_error(anthropic.InternalServerError, 500), errors.PROVIDER_SERVER_ERROR),
        (_status_error(anthropic.OverloadedError, 529), errors.PROVIDER_SERVER_ERROR),
        # 流内错误事件：HTTP 200，类型在响应体里。
        (_status_error(anthropic.APIStatusError, 200, {"type": "error", "error": {"type": "overloaded_error"}}), errors.PROVIDER_SERVER_ERROR),
        (_status_error(anthropic.APIStatusError, 200, {"type": "error", "error": {"type": "rate_limit_error"}}), errors.PROVIDER_RATE_LIMITED),
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), errors.PROVIDER_UNAVAILABLE),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")), errors.PROVIDER_UNAVAILABLE),
    ],
)
def test_provider_errors_map_to_codes(error: Exception, code: str) -> None:
    result = runtime.invoke(FakeClient([error]), request(), config=CONFIG)
    assert result.error_code == code
    assert result.error_detail


def test_bad_request_detail_keeps_the_api_message() -> None:
    error = _status_error(anthropic.BadRequestError, 400, {"type": "error", "error": {"type": "invalid_request_error", "message": "tools.0: bad"}})
    result = runtime.invoke(FakeClient([error]), request(), config=CONFIG)
    assert "status=400" in (result.error_detail or "")
    assert "invalid_request_error" in (result.error_detail or "")


def test_failure_after_a_paused_round_keeps_its_usage() -> None:
    client = FakeClient([
        message([text("…")], stop_reason="pause_turn", message_usage=usage(output_tokens=1234)),
        _status_error(anthropic.InternalServerError, 500),
    ])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.error_code == errors.PROVIDER_SERVER_ERROR
    assert result.usage["output_tokens"] == 1234
    assert result.usage["rounds"] == 1


def test_program_errors_propagate() -> None:
    with pytest.raises(KeyError):
        runtime.invoke(FakeClient([KeyError("bug")]), request(), config=CONFIG)


def test_iterations_replace_top_level_counts() -> None:
    iterations = [
        {"type": "message", "input_tokens": 100, "output_tokens": 10, "cache_creation_input_tokens": 5, "cache_read_input_tokens": 1,
         "cache_creation": {"ephemeral_1h_input_tokens": 5, "ephemeral_5m_input_tokens": 0}},
        {"type": "compaction", "input_tokens": 200, "output_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 2},
    ]
    client = FakeClient([message([json_text()], message_usage=usage(input_tokens=999, output_tokens=999, iterations=iterations, searches=1))])
    result = runtime.invoke(client, request(), config=CONFIG)
    assert result.usage["input_tokens"] == 300
    assert result.usage["output_tokens"] == 30
    assert result.usage["cache_creation_1h_input_tokens"] == 5
    assert result.usage["cache_read_input_tokens"] == 3
    assert result.usage["web_search_requests"] == 1


def test_refusal_fallback_uses_the_beta_endpoint() -> None:
    config = replace(CONFIG, refusal_fallback=True)
    client = FakeClient([message([json_text()])])
    result = runtime.invoke(client, request(config), config=config)
    assert result.error_code is None
    assert client.messages.calls == []
    call = client.beta.messages.calls[0]
    assert call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["fallbacks"] == "default"


def test_cost_uses_opus_prices_and_cache_ttl_split() -> None:
    assert runtime.cost_microusd({"input_tokens": 1_000_000}) == 4_000_000
    assert runtime.cost_microusd({"output_tokens": 1_000_000}) == 20_000_000
    assert runtime.cost_microusd({"cache_read_input_tokens": 1_000_000}) == 200_000
    assert runtime.cost_microusd({
        "cache_creation_input_tokens": 3_000_000,
        "cache_creation_1h_input_tokens": 1_000_000,
        "cache_creation_5m_input_tokens": 1_000_000,
    }) == 8_000_000 + 5_000_000 + 8_000_000  # 没有明细的 1M 按 1 小时价
    assert runtime.cost_microusd({"web_search_requests": 6}) == 60_000
    assert runtime.cost_microusd({"input_tokens": 1}) == 4  # 微美元，四舍五入
    assert runtime.cost_microusd({}) == 0


def test_make_client_sets_timeout_and_retries() -> None:
    client = runtime.make_client("sk-ant-test", 12.5)
    assert client.max_retries == 2
    assert client.timeout == 12.5
