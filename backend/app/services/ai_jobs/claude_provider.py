"""Native Claude Messages transport; job lifetime and validation live upstream."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from dataclasses import dataclass, field
import ipaddress
import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from anthropic import AsyncAnthropic, Timeout, transform_schema
from anthropic.types import CacheCreation, Message

MODEL = "claude-haiku-5-5"
EFFORT = "xhigh"
BASE_URL = "https://api.anthropic.com"
RESULT_TOOL_NAME = "record_analysis"
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
    "必须最后仅调用一次 record_analysis 返回最终分析；不要让中间说明成为最终结果。"
)


@dataclass(frozen=True)
class PreparedMessage:
    params: dict[str, Any]
    api_key: str = field(repr=False)
    timeout_seconds: float


def prepare_message(
    settings: Any,
    *,
    instructions: str,
    input_text: str,
    schema: dict,
    max_tokens: int,
    tools: list[dict] | None = None,
) -> PreparedMessage:
    key = settings.anthropic_api_key.get_secret_value().strip()
    if not key:
        raise RuntimeError("ai_not_configured")
    output_config: dict[str, Any] = {"effort": EFFORT}
    transformed = transform_schema(schema)
    if not tools:
        output_config["format"] = {"type": "json_schema", "schema": transformed}
    params: dict[str, Any] = {
        "model": MODEL,
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
        params["tools"] = deepcopy(tools) + [{
            "name": RESULT_TOOL_NAME,
            "description": "Return the final validated analysis exactly once after any required server tools finish.",
            "input_schema": transformed,
            "strict": True,
            "allowed_callers": ["direct"],
        }]
        params["tool_choice"] = {"type": "auto", "disable_parallel_tool_use": True}
    return PreparedMessage(
        api_key=key,
        timeout_seconds=float(settings.openai_timeout_seconds),
        params=params,
    )


async def stream_message(
    prepared: PreparedMessage,
    *,
    on_message_start: Callable[[str], Awaitable[None]] | None = None,
) -> Message:
    # A fresh client avoids retaining rotated secrets or connections from another
    # event loop. Create it lazily so abandoned preparation owns no open sockets.
    client = AsyncAnthropic(
        api_key=prepared.api_key,
        base_url=BASE_URL,
        timeout=Timeout(prepared.timeout_seconds, connect=30.0),
        max_retries=0,
    )
    try:
        async with client.messages.stream(**prepared.params) as stream:
            completed = False
            cache_creation_tokens = None
            cache_creation_details = None
            counts = {"web_search": 0, "web_fetch": 0, "code": 0, "total": 0}
            async for event in stream:
                if event.type == "message_start":
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
                    if counts[group] > (2 if group == "code" else 1) or counts["total"] > 4:
                        raise RuntimeError("provider_tool_limit_exceeded")
            # SDK accumulation can retain a snapshot even after a premature EOF.
            if not completed:
                raise RuntimeError("provider_stream_incomplete")
            message = await stream.get_final_message()
            message.usage.cache_creation = cache_creation_details
            # A response with no tool blocks otherwise cannot reveal that this
            # request required a structured final tool rather than plain text.
            if prepared.params.get("tools"):
                object.__setattr__(message, "_analysis_result_tool_required", True)
            return message
    finally:
        # Cleanup must not replace a transport failure or cancellation with a
        # close error, nor turn a completed paid response into another attempt.
        try:
            await client.close()
        except Exception:
            pass


def response_text(message: Message) -> str:
    if _has_tools(message):
        if message.stop_reason != "tool_use":
            return ""
        result, error = _structured_tool_result(message)
        if error is not None or result is None:
            return ""
        return json.dumps(result.input, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "".join(block.text for block in message.content if block.type == "text")


def _has_tools(message: Message) -> bool:
    return bool(getattr(message, "_analysis_result_tool_required", False)) or any(
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


def _structured_tool_result(message: Message) -> tuple[Any | None, str | None]:
    client_tools = [block for block in message.content if block.type == "tool_use"]
    if any(block.name != RESULT_TOOL_NAME for block in client_tools):
        return None, "provider_unknown_client_tool"
    if len(client_tools) != 1:
        return None, "provider_invalid_final_tool"
    result = client_tools[0]
    if not isinstance(result.input, dict) or getattr(getattr(result, "caller", None), "type", "direct") != "direct":
        return None, "provider_invalid_final_tool"
    # Finishing before another tool runs would make this result premature.
    if any(block.type != "text" for block in message.content[message.content.index(result) + 1:]):
        return None, "provider_invalid_final_tool"
    _, error = _server_pairs(message)
    return (result, None) if error is None else (None, error)


def response_usage(message: Message) -> dict[str, int | None]:
    usage = message.usage
    creation = usage.cache_creation_input_tokens
    cached = usage.cache_read_input_tokens
    # Optional cache counts default to None in the SDK. Preserve that distinction
    # in the returned fields while treating unreported caching as zero in sums.
    input_tokens = usage.input_tokens + (creation or 0) + (cached or 0)
    cache_details = usage.cache_creation
    output_details = usage.output_tokens_details
    result = {
        "input_tokens": input_tokens,
        "cached_input_tokens": cached,
        "cache_creation_input_tokens": creation,
        "cache_creation_5m_input_tokens": (
            cache_details.ephemeral_5m_input_tokens if cache_details else None
        ),
        "cache_creation_1h_input_tokens": (
            cache_details.ephemeral_1h_input_tokens if cache_details else None
        ),
        "output_tokens": usage.output_tokens,
        "reasoning_tokens": output_details.thinking_tokens if output_details else None,
        "total_tokens": input_tokens + usage.output_tokens,
    }
    if _has_tools(message) or usage.server_tool_use is not None:
        server_usage = usage.server_tool_use
        completed, _ = _server_pairs(message)
        result.update({
            "web_search_requests": (getattr(server_usage, "web_search_requests", 0) or 0),
            "web_fetch_requests": (getattr(server_usage, "web_fetch_requests", 0) or 0),
            "code_execution_requests": sum(name in _CODE_TOOLS for name in completed.values()),
        })
    return result


def response_terminal_error(message: Message) -> str | None:
    if message.stop_reason == "max_tokens":
        return "provider_incomplete_max_output_tokens"
    if message.stop_reason == "refusal":
        return "provider_refusal"
    if _has_tools(message):
        if message.stop_reason != "tool_use":
            return "provider_incomplete"
        _, error = _structured_tool_result(message)
        return error
    if message.stop_reason != "end_turn":
        return "provider_incomplete"
    if not response_text(message).strip():
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
