"""Native Claude Messages transport; job lifetime and validation live upstream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
import hashlib
import ipaddress
import json
import time
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from anthropic import AsyncAnthropic, Timeout, transform_schema
from anthropic.types import CacheCreation, Message

from app.services.claude_cache_diagnostics import BackgroundDiagnosticsStore, MISSING, diagnostic_field

MODEL = "claude-haiku-5-5"
EFFORT = "xhigh"
BASE_URL = "https://api.anthropic.com"
_CODE_TOOLS = {"code_execution", "bash_code_execution", "text_editor_code_execution"}
_RESULT_TYPES = {
    "web_search": "web_search_tool_result",
    "web_fetch": "web_fetch_tool_result",
    "code_execution": "code_execution_tool_result",
    "bash_code_execution": "bash_code_execution_tool_result",
    "text_editor_code_execution": "text_editor_code_execution_tool_result",
}
_TOOL_INSTRUCTIONS = (
    "\n只在需要核对公开事实、补全正文或复杂计算时使用对应工具。"
    "工具使用完成后，按指定 JSON 格式返回最终分析；不要在 JSON 结构之外添加说明。"
)

_PROMPT_JSON_INSTRUCTIONS = (
    "\n最终回答只输出一个完整JSON对象，不输出Markdown代码块、前言、解释或引用标记。"
    "全部工具完成后才输出最终JSON，不要提前输出中间分析。"
    "必须遵守下列结构定义并填写真正的分析内容，禁止空白和占位符：\n"
)


@dataclass(frozen=True)
class PreparedMessage:
    params: dict[str, Any]
    api_key: str = field(repr=False)
    timeout_seconds: float
    diagnostic_task: str = "ai_jobs:unknown"


def _output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Preserve fixed values without adding regex grammar complexity."""
    source = deepcopy(schema)

    def normalize(value: Any) -> None:
        if isinstance(value, dict):
            if "const" in value:
                value["enum"] = [value.pop("const")]
            for child in value.values():
                normalize(child)
        elif isinstance(value, list):
            for child in value:
                normalize(child)

    normalize(source)
    return transform_schema(source)


def prepare_message(
    settings: Any,
    *,
    instructions: str,
    input_text: str,
    schema: dict,
    max_tokens: int,
    tools: list[dict] | None = None,
    output_mode: Literal["native_json", "prompt_json"] = "native_json",
    job_type: str | None = None,
) -> PreparedMessage:
    if output_mode not in {"native_json", "prompt_json"}:
        raise ValueError("provider_output_mode_invalid")
    key = settings.anthropic_api_key.get_secret_value().strip()
    if not key:
        raise RuntimeError("ai_not_configured")
    output_config: dict[str, Any] = {
        "effort": str(getattr(settings, "openai_reasoning", EFFORT)),
        "format": {"type": "json_schema", "schema": _output_schema(schema)},
    }
    params: dict[str, Any] = {
        "model": str(getattr(settings, "openai_model", MODEL)),
        "max_tokens": max_tokens,
        "thinking": {"type": "adaptive"},
        "output_config": output_config,
        "system": [{
            "type": "text",
            "text": instructions + (_TOOL_INSTRUCTIONS if tools else ""),
            "cache_control": {"type": "ephemeral", "ttl": "5m"},
        }],
        "messages": [{
            "role": "user",
            "content": [{"type": "text", "text": input_text}],
        }],
    }
    if tools:
        params["tools"] = deepcopy(tools)
        params["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    if output_mode == "prompt_json":
        output_format = params["output_config"].pop("format")
        params["system"][0]["text"] += _PROMPT_JSON_INSTRUCTIONS + json.dumps(
            output_format["schema"], ensure_ascii=False, separators=(",", ":"),
        )
    return PreparedMessage(
        api_key=key,
        timeout_seconds=float(settings.openai_timeout_seconds),
        params=params,
        diagnostic_task=f"ai_jobs:{job_type}" if job_type else "ai_jobs:unknown",
    )


async def stream_message(
    prepared: PreparedMessage,
    *,
    on_message_start: Callable[[str], Awaitable[None]] | None = None,
    tool_counts: dict[str, int] | None = None,
) -> Message:
    # A fresh client avoids retaining rotated secrets or connections from another
    # event loop. Create it lazily so abandoned preparation owns no open sockets.
    client = AsyncAnthropic(
        api_key=prepared.api_key,
        base_url=BASE_URL,
        timeout=Timeout(prepared.timeout_seconds, connect=30.0),
        max_retries=0,
    )
    store = BackgroundDiagnosticsStore()
    diagnostics = MISSING
    diagnostic_started_at = time.time()
    try:
        previous = await store.previous(prepared.diagnostic_task)
        async with client.messages.stream(**{**prepared.params, "diagnostics": {"previous_message_id": previous}}) as stream:
            completed = False
            cache_creation_tokens = None
            cache_creation_details = None
            counts = tool_counts if tool_counts is not None else {"web_search": 0, "web_fetch": 0, "code": 0, "total": 0}
            async for event in stream:
                if event.type == "message_start":
                    current = event.message.id
                    diagnostic_started_at = time.time()
                    diagnostics = diagnostic_field(event.message, "diagnostics")
                    store.record(task=prepared.diagnostic_task, model=prepared.params["model"],
                                 previous=previous, current=current, diagnostics=diagnostics,
                                 usage=event.message.usage, complete=False, started_at=diagnostic_started_at)
                    cache_creation_tokens = event.message.usage.cache_creation_input_tokens
                    cache_creation_details = deepcopy(event.message.usage.cache_creation)
                    if on_message_start is not None:
                        await on_message_start(event.message.id)
                elif event.type == "message_delta":
                    # SDK 1.12.1 updates cumulative creation totals but retains
                    # the initial TTL breakdown. Never combine different snapshots.
                    creation = event.usage.cache_creation_input_tokens
                    details = getattr(event.usage, "cache_creation", None)
                    if details is not None:
                        cache_creation_details = CacheCreation.model_validate(details)
                    elif creation is not None and creation != cache_creation_tokens:
                        cache_creation_details = None
                    if creation is not None:
                        cache_creation_tokens = creation
                elif event.type == "message_stop":
                    completed = True
                elif event.type == "content_block_start" and event.content_block.type == "server_tool_use":
                    name = event.content_block.name
                    group = "code" if name in _CODE_TOOLS else name
                    if group not in counts or group == "total":
                        raise RuntimeError("provider_unknown_server_tool")
                    counts[group] += 1
                    counts["total"] += 1
                    network_limit = 12 if prepared.params["model"] == "claude-sonnet-5-5" and prepared.diagnostic_task == "ai_jobs:market_focus" else 1
                    if counts[group] > (2 if group == "code" else network_limit) or counts["total"] > network_limit * 2 + 2:
                        raise RuntimeError("provider_tool_limit_exceeded")
            # SDK accumulation can retain a snapshot even after a premature EOF.
            if not completed:
                raise RuntimeError("provider_stream_incomplete")
            message = await stream.get_final_message()
            message.usage.cache_creation = cache_creation_details
            store.record(task=prepared.diagnostic_task, model=prepared.params["model"],
                         previous=previous, current=message.id, diagnostics=diagnostics,
                         usage=message.usage, complete=True, started_at=diagnostic_started_at)
            return message
    finally:
        # Cleanup must not replace a transport failure or cancellation with a
        # close error, nor turn a completed paid response into another attempt.
        try:
            await client.close()
        except Exception:
            pass


def response_text(message: Message) -> str:
    blocks = message.content
    if _has_tools(message):
        if response_terminal_error(message) is not None:
            return ""
        # Tool loops can include explanatory text before or between calls. Only
        # text after the final native call/result belongs to the JSON response.
        final_tool_index = max(
            index for index, block in enumerate(blocks)
            if block.type == "server_tool_use" or block.type.endswith("_tool_result")
        )
        blocks = blocks[final_tool_index + 1:]
    return "".join(block.text for block in blocks if block.type == "text")


def _has_tools(message: Message) -> bool:
    return any(
        block.type in {"tool_use", "server_tool_use"} or block.type.endswith("_tool_result")
        for block in message.content
    )


def _server_pairs(message: Message) -> tuple[dict[str, str], str | None]:
    pending: dict[str, str] = {}
    completed: dict[str, str] = {}
    for block in message.content:
        if block.type == "server_tool_use":
            if block.name not in _RESULT_TYPES or block.id in pending or block.id in completed:
                return completed, "provider_invalid_tool_response"
            pending[block.id] = block.name
        elif block.type.endswith("_tool_result"):
            name = pending.pop(block.tool_use_id, None)
            if name is None or _RESULT_TYPES[name] != block.type:
                return completed, "provider_invalid_tool_response"
            completed[block.tool_use_id] = name
    return completed, "provider_incomplete_tool_result" if pending else None


def response_usage(message: Message) -> dict[str, int | None]:
    usage = message.usage
    def known(value: Any) -> int | None:
        return value if type(value) is int and value >= 0 else None

    creation = known(usage.cache_creation_input_tokens)
    cached = known(usage.cache_read_input_tokens)
    # Optional cache counts default to None in the SDK. Preserve that distinction
    # in the returned fields while treating unreported caching as zero in sums.
    raw_input = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    if type(output_tokens) is not int or output_tokens < 0:
        output_tokens = None
    input_tokens = raw_input + creation + cached if all(
        type(value) is int and value >= 0 for value in (raw_input, creation, cached)
    ) else None
    cache_details = usage.cache_creation
    output_details = usage.output_tokens_details
    result = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_creation_input_tokens": creation,
        "cache_creation_5m_input_tokens": (
            known(cache_details.ephemeral_5m_input_tokens) if cache_details else None
        ),
        "cache_creation_1h_input_tokens": (
            known(cache_details.ephemeral_1h_input_tokens) if cache_details else None
        ),
        "output_tokens": output_tokens,
        "reasoning_tokens": known(output_details.thinking_tokens) if output_details else None,
        "total_tokens": input_tokens + output_tokens if input_tokens is not None and output_tokens is not None else None,
    }
    server_usage = usage.server_tool_use
    completed, _ = _server_pairs(message)
    used_search = any(
        block.type == "server_tool_use" and block.name == "web_search"
        for block in message.content
    )
    used_fetch = any(
        block.type == "server_tool_use" and block.name == "web_fetch"
        for block in message.content
    )
    # Absence of a call in a fully received message proves zero; a call with no
    # billing counter is unknown, even when its result and final JSON are valid.
    for key, used in (("web_search_requests", used_search), ("web_fetch_requests", used_fetch)):
        value = getattr(server_usage, key, None)
        result[key] = 0 if value is None and not used else known(value)
    result["code_execution_requests"] = sum(name in _CODE_TOOLS for name in completed.values())
    return result


def response_terminal_error(message: Message) -> str | None:
    if message.stop_reason == "max_tokens":
        return "provider_incomplete_max_output_tokens"
    if message.stop_reason == "refusal":
        return "provider_refusal"
    if any(block.type == "tool_use" for block in message.content):
        return "provider_unknown_client_tool"
    if message.stop_reason != "end_turn":
        return "provider_incomplete"
    _, error = _server_pairs(message)
    if error is not None:
        return error
    blocks = message.content
    if _has_tools(message):
        final_tool_index = max(
            index for index, block in enumerate(blocks)
            if block.type == "server_tool_use" or block.type.endswith("_tool_result")
        )
        blocks = blocks[final_tool_index + 1:]
    if not any(block.type == "text" and block.text.strip() for block in blocks):
        return "provider_empty_response"
    return None


def _public_source_url(value: Any) -> str | None:
    if not isinstance(value, str) or len(value) > 2048 or any(ord(char) <= 32 for char in value):
        return None
    try:
        parts = urlsplit(value)
        host = parts.hostname
        if parts.scheme.lower() not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
            return None
        port = parts.port
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            labels = host.split(".")
            if len(labels) < 2 or labels[-1].isdigit() or any(
                not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
                or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label)
                for label in labels
            ):
                return None
            if labels[-1] in {"localhost", "local", "internal", "test", "invalid", "example", "onion", "home", "lan"}:
                return None
        else:
            if not address.is_global or address.is_multicast or address.is_reserved or getattr(address, "ipv4_mapped", None):
                return None
        netloc = f"[{host}]" if ":" in host else host
        if port is not None:
            netloc += f":{port}"
        return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))
    except (ValueError, UnicodeError):
        return None


def response_sources(message: Message) -> list[dict[str, str]]:
    """Bounded provenance only; never copy fetched documents or thinking."""
    completed, _ = _server_pairs(message)
    sources: list[dict[str, str]] = []
    seen: set[str] = set()
    def add(title: Any, url: Any, kind: str) -> None:
        normalized = _public_source_url(url)
        if normalized is None or normalized in seen or len(sources) >= 10:
            return
        printable = "".join(char if char.isprintable() else " " for char in str(title or ""))
        clean_title = " ".join(printable.split())[:512]
        sources.append({"title": clean_title, "url": normalized, "type": kind})
        seen.add(normalized)
    for block in message.content:
        if block.type == "web_search_tool_result" and completed.get(block.tool_use_id) == "web_search":
            if isinstance(block.content, list):
                for entry in block.content:
                    add(entry.title, entry.url, "web_search")
        elif block.type == "web_fetch_tool_result" and completed.get(block.tool_use_id) == "web_fetch":
            if block.content.type == "web_fetch_result":
                add(block.content.content.title, block.content.url, "web_fetch")
        elif block.type == "text":
            for citation in block.citations or []:
                url = getattr(citation, "url", None)
                if url is not None:
                    kind = "web_search" if citation.type == "web_search_result_location" else "web_fetch"
                    add(getattr(citation, "title", None), url, kind)
    return sources


def response_tool_evidence(message: Message) -> list[dict[str, str]]:
    """Proof from matched successful server results, never model citations."""
    completed, error = _server_pairs(message)
    if error:
        return []
    evidence: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for block in message.content:
        call_id = getattr(block, "tool_use_id", None)
        name = completed.get(call_id)
        if name == "web_search" and block.type == "web_search_tool_result" and isinstance(block.content, list):
            entries = [(item, getattr(item, "url", None), getattr(item, "title", None)) for item in block.content if getattr(item, "type", None) == "web_search_result"]
        elif name == "web_fetch" and block.type == "web_fetch_tool_result" and getattr(block.content, "type", None) == "web_fetch_result":
            entries = [(block.content, block.content.url, getattr(block.content.content, "title", None))]
        else:
            continue
        for content, url, title in entries:
            normalized = _public_source_url(url)
            if normalized is None or (call_id, normalized) in seen or len(evidence) >= 512:
                continue
            if hasattr(content, "model_dump"):
                raw = content.model_dump(mode="json")
            else:
                raw = vars(content)
            digest = hashlib.sha256(json.dumps(raw, sort_keys=True, ensure_ascii=False, default=str, separators=(",", ":")).encode()).hexdigest()
            evidence.append({"tool_use_id": call_id, "tool_name": name, "status": "success", "url": normalized, "title": " ".join("".join(char if char.isprintable() else " " for char in str(title or "")).split())[:512], "content_sha256": digest})
            seen.add((call_id, normalized))
    return evidence


MAX_FOCUS_ROUNDS = 4
_USAGE_KEYS = (
    "input_tokens", "cached_input_tokens", "cache_creation_input_tokens",
    "cache_creation_5m_input_tokens", "cache_creation_1h_input_tokens",
    "output_tokens", "reasoning_tokens", "total_tokens",
    "web_search_requests", "web_fetch_requests", "code_execution_requests",
)


def sum_round_usage(rounds: list[dict[str, Any]]) -> dict[str, int | None]:
    """Sum complete request receipts; one unknown component stays unknown."""
    total: dict[str, int | None] = dict.fromkeys(_USAGE_KEYS, 0)
    for item in rounds:
        for key in _USAGE_KEYS:
            value = item["usage"].get(key)
            previous = total[key]
            total[key] = previous + value if type(previous) is int and type(value) is int and value >= 0 else None
    return total


@dataclass(frozen=True)
class ConversationResult:
    receipt: dict[str, Any]


async def stream_market_focus(
    prepared: PreparedMessage,
    *,
    on_message_start: Callable[[str], Awaitable[None]],
    on_progress: Callable[..., Awaitable[None]],
) -> ConversationResult:
    """Bound pause-turn continuations under the worker's one absolute deadline.

    Progress is durable before each next request. It deliberately contains no
    thinking or assistant content, so restart cannot replay a paid conversation.
    """
    if prepared.params["model"] != "claude-sonnet-5-5" or prepared.diagnostic_task != "ai_jobs:market_focus":
        raise ValueError("provider_continuation_not_supported")
    original_model = prepared.params["model"]
    ceiling = int(prepared.params["max_tokens"])
    messages = deepcopy(prepared.params["messages"])
    completed: list[Message] = []
    rounds: list[dict[str, Any]] = []
    request_ids: list[str] = []
    counts = {"web_search": 0, "web_fetch": 0, "code": 0, "total": 0}

    async def persist_progress(*, will_continue: bool = False) -> None:
        await on_progress({
            "provider": "anthropic", "model": original_model, "id": request_ids[0],
            "request_ids": list(request_ids), "rounds": deepcopy(rounds),
            "confirmed_usage": sum_round_usage(rounds),
        }, will_continue=will_continue)

    async def started(message_id: str) -> None:
        if not message_id or message_id in request_ids or len(request_ids) >= MAX_FOCUS_ROUNDS:
            raise RuntimeError("provider_invalid_message_identity")
        if not request_ids:
            await on_message_start(message_id)
        request_ids.append(message_id)
        await persist_progress()

    def finish(error: str | None = None) -> ConversationResult:
        combined = completed[-1].model_copy(update={
            "id": request_ids[0],
            "content": [block for message in completed for block in message.content],
        })
        terminal = error or response_terminal_error(combined)
        final_blocks = completed[-1].content
        tool_indices = [index for index, block in enumerate(final_blocks) if block.type == "server_tool_use" or block.type.endswith("_tool_result")]
        if tool_indices:
            final_blocks = final_blocks[max(tool_indices) + 1:]
        final_text = "".join(block.text for block in final_blocks if block.type == "text")
        if not terminal and not final_text.strip():
            terminal = "provider_empty_response"
        return ConversationResult({
            "provider": "anthropic", "model": original_model, "id": request_ids[0],
            "request_ids": list(request_ids), "rounds": deepcopy(rounds),
            "output_text": "" if terminal else final_text,
            "stop_reason": str(combined.stop_reason), "terminal_error": terminal,
            "usage": sum_round_usage(rounds),
            "evidence_sources": response_sources(combined),
            "tool_evidence_version": "v1", "tool_evidence": response_tool_evidence(combined),
        })

    for index in range(MAX_FOCUS_ROUNDS):
        used_output = sum_round_usage(rounds)["output_tokens"]
        if type(used_output) is not int:
            return finish("provider_usage_incomplete")
        remaining = ceiling - used_output
        if remaining <= 0:
            return finish("provider_incomplete_max_output_tokens")
        params = {**prepared.params, "messages": messages, "max_tokens": remaining}
        message = await stream_message(replace(prepared, params=params), on_message_start=started, tool_counts=counts)
        if message.model != original_model or not request_ids or message.id != request_ids[-1]:
            raise RuntimeError("provider_model_or_message_mismatch")
        completed.append(message)
        round_usage = response_usage(message)
        round_usage["code_execution_requests"] = sum(
            block.type == "server_tool_use" and block.name in _CODE_TOOLS for block in message.content
        )
        rounds.append({"id": message.id, "stop_reason": str(message.stop_reason), "usage": round_usage})
        await persist_progress()
        combined = message.model_copy(update={"content": [block for item in completed for block in item.content]})
        _, pair_error = _server_pairs(combined)
        if pair_error and pair_error != "provider_incomplete_tool_result":
            return finish(pair_error)
        consumed_output = sum_round_usage(rounds)["output_tokens"]
        if type(consumed_output) is int and consumed_output > ceiling:
            return finish("provider_incomplete_max_output_tokens")
        if message.stop_reason != "pause_turn":
            return finish()
        # Unknown billing or output accounting cannot authorize another request.
        usage = sum_round_usage(rounds)
        if any(type(usage[key]) is not int for key in ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "web_search_requests", "web_fetch_requests")):
            return finish("provider_usage_incomplete")
        if index == MAX_FOCUS_ROUNDS - 1:
            return finish("provider_continuation_limit")
        # Preserve SDK content verbatim, including thinking signatures and tool
        # blocks. The next request must continue the same assistant turn.
        await persist_progress(will_continue=True)
        messages = [*messages, {"role": "assistant", "content": message.content}]
    raise AssertionError("unreachable")
