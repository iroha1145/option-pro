"""Anthropic Messages API 调用：一次研判 = 一次请求（含服务端工具循环）。

- 请求走流式并取 ``get_final_message()``：max_tokens 超过 16K 时 SDK 要求流式。
- 服务端工具循环到上限会以 ``pause_turn`` 停下；把本轮 content 原样作为 assistant 消息
  追加后重发即可续跑（不加「继续」之类的用户消息，思考块也必须原样回传）。
- 供应商错误与模型侧停止原因都折成 ``InvocationResult.error_code``，不向上抛：
  已经完成的轮次照样计入用量与费用。只有程序错误才会抛出。
"""

from __future__ import annotations

import json
import time
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import anthropic

from . import errors
from .schema import MarketBriefResult

# Opus 5.5 价格常量（每百万 token 的微美元；web_search 为每次的微美元）。
# 核对日期 2026-10-08，来源 platform.claude.com pricing。
PRICE_INPUT_PER_MTOK = 4_000_000
PRICE_CACHE_WRITE_1H_PER_MTOK = 8_000_000
PRICE_CACHE_WRITE_5M_PER_MTOK = 5_000_000
PRICE_CACHE_READ_PER_MTOK = 200_000
PRICE_OUTPUT_PER_MTOK = 20_000_000
PRICE_WEB_SEARCH_EACH = 10_000

REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
# 剩余墙钟预算低于这个秒数就不再发下一次请求：一轮带网页工具的续跑通常要几分钟，
# 发出去也跑不完，只会被 worker 的任务超时整体打断。预算很小（试跑）时按预算的四分之一。
MIN_REQUEST_SECONDS = 120.0
_CACHE_TTLS = frozenset({"5m", "1h"})
_EXTERNAL_URL_LIMIT = 40
_EXTERNAL_QUERY_LIMIT = 20
_DETAIL_CHARS = 500
_USAGE_KEYS = (
    "input_tokens",
    "output_tokens",
    "cache_creation_input_tokens",
    "cache_creation_1h_input_tokens",
    "cache_creation_5m_input_tokens",
    "cache_read_input_tokens",
    "web_search_requests",
    "web_fetch_requests",
)


@dataclass(frozen=True)
class InvocationResult:
    text: str | None
    parsed: dict[str, Any] | None
    error_code: str | None
    error_detail: str | None
    stop_reason: str | None
    continuation_count: int
    usage: dict[str, int]
    external_sources: tuple[dict[str, Any], ...]
    model: str | None
    request_ids: tuple[str, ...]


def make_client(api_key: str, timeout: float) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)


def output_schema() -> dict[str, Any]:
    """结构化输出用的 JSON Schema（SDK 把长度、条数等 API 不支持的约束移进描述）。"""

    return anthropic.transform_schema(MarketBriefResult.model_json_schema())


def build_request(*, config: Any, system_text: str, user_text: str) -> dict[str, Any]:
    """拼出 messages.stream 的关键字参数。

    不传 thinking（Opus 5.5 自适应思考常开，显式关闭会 400），不传 temperature（会 400）。
    缓存：系统提示词上一个显式断点（TTL 取配置，默认 1 小时），顶层自动缓存只用默认的
    5 分钟——网页工具会在工具结果后自动插入 5 分钟缓存写入，续跑请求里若顶层自动断点仍是
    1 小时，就会出现「1 小时条目排在 5 分钟条目之后」，这种顺序会被拒；1 小时断点在前、
    5 分钟尾巴在后则是文档允许的组合。续跑都在几分钟内发生，5 分钟够用。
    """

    ttl = config.prompt_cache_ttl
    if ttl not in _CACHE_TTLS:
        raise ValueError(f"prompt_cache_ttl must be one of {sorted(_CACHE_TTLS)}")
    cache_control = {"type": "ephemeral", "ttl": ttl}
    tools: list[dict[str, Any]] = [
        {
            "type": "web_search_20260209",
            "name": "web_search",
            "max_uses": config.web_search_max_uses,
            "user_location": {"type": "approximate", "country": "US", "timezone": "America/New_York"},
        },
        {
            "type": "web_fetch_20260209",
            "name": "web_fetch",
            "max_uses": config.web_fetch_max_uses,
            "max_content_tokens": config.web_fetch_max_content_tokens,
        },
    ]
    if config.code_execution_tool:
        # 默认关：官方文档说明 20260209 版网页工具的动态过滤已内建代码执行，
        # 再并列声明独立的代码执行工具会出现第二个执行环境，让模型困惑。
        tools.append({"type": "code_execution_20260521", "name": "code_execution"})
    output_config: dict[str, Any] = {"effort": config.effort}
    if config.structured_output:
        output_config["format"] = {"type": "json_schema", "schema": output_schema()}
    request: dict[str, Any] = {
        "model": config.model,
        "max_tokens": config.max_output_tokens,
        "system": [{"type": "text", "text": system_text, "cache_control": dict(cache_control)}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": user_text}]}],
        "tools": tools,
        "output_config": output_config,
        "cache_control": {"type": "ephemeral"},
    }
    if config.refusal_fallback:
        request["betas"] = [REFUSAL_FALLBACK_BETA]
        request["fallbacks"] = "default"
    return request


def request_summary(request: Mapping[str, Any]) -> dict[str, Any]:
    """请求参数摘要（不含提示词正文），写进运行记录并在命令行发送前打印，便于排查 400。"""

    return {
        "model": request.get("model"),
        "max_tokens": request.get("max_tokens"),
        "effort": (request.get("output_config") or {}).get("effort"),
        "output_format": ((request.get("output_config") or {}).get("format") or {}).get("type"),
        "tools": [
            {key: tool[key] for key in ("type", "name", "max_uses", "max_content_tokens") if key in tool}
            for tool in request.get("tools") or []
        ],
        "system_cache_control": (request.get("system") or [{}])[0].get("cache_control"),
        "cache_control": request.get("cache_control"),
        "betas": list(request.get("betas") or []),
        "fallbacks": request.get("fallbacks"),
        "thinking": request.get("thinking"),
        "system_chars": sum(len(block.get("text") or "") for block in request.get("system") or []),
        "user_chars": sum(
            len(part.get("text") or "")
            for message in request.get("messages") or []
            for part in message.get("content") or []
            if isinstance(part, Mapping)
        ),
    }


def _field(value: Any, name: str) -> Any:
    # SDK 对象与原始 dict 都可能出现（未建模的字段如 usage.iterations 以 dict 保留）。
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _round_usage(usage: Any) -> Counter[str]:
    """单轮用量。有 iterations 时以它为准逐项求和，不再叠加顶层数字（否则重复计算）：
    启用某些服务端步骤（压缩、回退等）时，顶层用量不含这些步骤。"""

    totals: Counter[str] = Counter()
    if usage is None:
        return totals
    iterations = _field(usage, "iterations")
    entries: Sequence[Any] = iterations if isinstance(iterations, (list, tuple)) and iterations else [usage]
    for entry in entries:
        for key in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"):
            totals[key] += _int(_field(entry, key))
        creation = _field(entry, "cache_creation")
        if creation is not None:
            totals["cache_creation_1h_input_tokens"] += _int(_field(creation, "ephemeral_1h_input_tokens"))
            totals["cache_creation_5m_input_tokens"] += _int(_field(creation, "ephemeral_5m_input_tokens"))
    server = _field(usage, "server_tool_use")
    if server is not None:
        totals["web_search_requests"] += _int(_field(server, "web_search_requests"))
        totals["web_fetch_requests"] += _int(_field(server, "web_fetch_requests"))
    return totals


def cost_microusd(usage: Mapping[str, Any]) -> int:
    """按 Opus 5.5 价格估算费用（微美元，四舍五入）。

    缓存写入按 TTL 明细分别计价：网页搜索会在工具结果后自动插入 5 分钟缓存写入，
    即使请求设置的是 1 小时。没有 TTL 明细的部分按 1 小时价计（偏保守）。
    """

    def tokens(key: str) -> int:
        return _int(usage.get(key))

    creation_1h = tokens("cache_creation_1h_input_tokens")
    creation_5m = tokens("cache_creation_5m_input_tokens")
    unattributed = max(0, tokens("cache_creation_input_tokens") - creation_1h - creation_5m)
    scaled = (
        tokens("input_tokens") * PRICE_INPUT_PER_MTOK
        + (creation_1h + unattributed) * PRICE_CACHE_WRITE_1H_PER_MTOK
        + creation_5m * PRICE_CACHE_WRITE_5M_PER_MTOK
        + tokens("cache_read_input_tokens") * PRICE_CACHE_READ_PER_MTOK
        + tokens("output_tokens") * PRICE_OUTPUT_PER_MTOK
    )
    return (scaled + 500_000) // 1_000_000 + tokens("web_search_requests") * PRICE_WEB_SEARCH_EACH


def _external_sources(rounds: Sequence[Any]) -> tuple[dict[str, Any], ...]:
    """所有轮次里用过的网页来源（按 URL 去重）与搜索词。

    结构化输出拿不到正文引用，这里记录的是模型检索与抓取过的全部来源。
    """

    by_url: dict[str, dict[str, Any]] = {}
    queries: dict[str, dict[str, Any]] = {}

    def add_url(url: Any, title: Any, via: str) -> None:
        if not isinstance(url, str) or not url.strip():
            return
        key = url.strip()
        entry = by_url.get(key)
        if entry is None:
            if len(by_url) >= _EXTERNAL_URL_LIMIT:
                return
            by_url[key] = {"url": key, "title": title if isinstance(title, str) and title.strip() else None, "via": via}
        else:
            if entry["title"] is None and isinstance(title, str) and title.strip():
                entry["title"] = title
            # 先在搜索结果里出现、后来又被抓取全文的页面，按「抓取」记：它更可能是正文依据。
            if via == "web_fetch":
                entry["via"] = "web_fetch"

    for message in rounds:
        for block in _field(message, "content") or []:
            kind = _field(block, "type")
            if kind == "server_tool_use":
                name = _field(block, "name")
                tool_input = _field(block, "input")
                if not isinstance(tool_input, Mapping):
                    continue
                query = tool_input.get("query")
                if name == "web_search" and isinstance(query, str) and query.strip():
                    if query not in queries and len(queries) < _EXTERNAL_QUERY_LIMIT:
                        queries[query] = {"query": query.strip(), "via": "web_search"}
                elif name == "web_fetch":
                    add_url(tool_input.get("url"), None, "web_fetch")
            elif kind == "web_search_tool_result":
                content = _field(block, "content")
                if isinstance(content, (list, tuple)):
                    for result in content:
                        add_url(_field(result, "url"), _field(result, "title"), "web_search")
            elif kind == "web_fetch_tool_result":
                content = _field(block, "content")
                if _field(content, "type") == "web_fetch_result":
                    add_url(_field(content, "url"), _field(_field(content, "content"), "title"), "web_fetch")
    return tuple(by_url.values()) + tuple(queries.values())


def _final_text(message: Any) -> tuple[str | None, dict[str, Any] | None]:
    """最终一轮的文本与解析结果。

    先取最后一个非空文本块解析；不成再试全部文本块拼接（结构化 JSON 被拆成多块时）。
    搜索前的过渡文字可能落在早先的文本块里，所以不先拼接。
    """

    texts = [
        text
        for block in _field(message, "content") or []
        if _field(block, "type") == "text" and isinstance((text := _field(block, "text")), str) and text.strip()
    ]
    if not texts:
        return None, None
    candidates = [texts[-1], "".join(texts)]
    # 关闭结构化输出时模型偶尔会套一层 ```json 代码块；剥掉后再试。
    candidates += [unfenced for text in candidates if (unfenced := _strip_code_fence(text)) is not None]
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return candidate, parsed
    return "".join(texts), None


def _strip_code_fence(text: str) -> str | None:
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```") and len(stripped) > 6):
        return None
    inner = stripped[3:-3]
    newline = inner.find("\n")
    return inner[newline + 1 :] if newline >= 0 else inner


def _clip(text: str) -> str:
    return text if len(text) <= _DETAIL_CHARS else text[: _DETAIL_CHARS - 1] + "…"


def _status_error(exc: anthropic.APIStatusError) -> tuple[str, str]:
    """把 HTTP 状态与流内错误事件（状态码为 200，类型在响应体里）统一映射。"""

    status = exc.status_code
    kind = exc.type
    detail = _clip(f"status={status} type={kind} request_id={exc.request_id} {exc.message}")
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)) or kind in {
        "authentication_error",
        "permission_error",
        "billing_error",
    }:
        return errors.PROVIDER_AUTH_FAILED, detail
    if isinstance(exc, anthropic.RateLimitError) or kind == "rate_limit_error":
        return errors.PROVIDER_RATE_LIMITED, detail
    if isinstance(exc, anthropic.BadRequestError):
        return errors.PROVIDER_REQUEST_REJECTED, detail
    if status >= 500 or kind in {"api_error", "overloaded_error", "timeout_error"}:
        return errors.PROVIDER_SERVER_ERROR, detail
    return errors.PROVIDER_REQUEST_REJECTED, detail


def invoke(
    client: Any,
    request: Mapping[str, Any],
    *,
    config: Any,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> InvocationResult:
    """发出请求并处理 pause_turn 续跑；返回最终文本、解析结果、用量与来源。

    ``config.request_timeout_seconds`` 是整次运行（含续跑）的墙钟预算：``deadline`` 是它在
    ``clock``（单调时钟）上的截止点，缺省从现在起算；runner 传入从运行开始起算的截止点，
    证据组装的耗时也算在内。每次请求前检查剩余预算，不足就停下记 run_deadline_exceeded；
    单次请求的 SDK 超时取剩余预算与 request_timeout_seconds 的较小值。
    """

    budget = float(config.request_timeout_seconds)
    ends_at = deadline if deadline is not None else clock() + budget
    minimum_remaining = min(MIN_REQUEST_SECONDS, budget / 4)
    endpoint = client.beta.messages if config.refusal_fallback else client.messages
    messages: list[Any] = list(request["messages"])
    rounds: list[Any] = []
    usage: Counter[str] = Counter()
    continuations = 0

    def finish(
        *,
        error_code: str | None = None,
        error_detail: str | None = None,
        text: str | None = None,
        parsed: dict[str, Any] | None = None,
    ) -> InvocationResult:
        last = rounds[-1] if rounds else None
        return InvocationResult(
            text=text,
            parsed=parsed,
            error_code=error_code,
            error_detail=error_detail,
            stop_reason=_field(last, "stop_reason"),
            continuation_count=continuations,
            usage={key: int(usage.get(key, 0)) for key in _USAGE_KEYS} | {"rounds": len(rounds)},
            external_sources=_external_sources(rounds),
            model=_field(last, "model"),
            request_ids=tuple(str(_field(item, "id")) for item in rounds if _field(item, "id")),
        )

    try:
        while True:
            remaining = ends_at - clock()
            if remaining < minimum_remaining:
                return finish(
                    error_code=errors.RUN_DEADLINE_EXCEEDED,
                    error_detail=(
                        f"remaining={max(remaining, 0.0):.0f}s before request {len(rounds) + 1}, "
                        f"run budget {budget:.0f}s"
                    ),
                )
            # 流式请求的 SDK 超时作用于每次读取而不是总时长：它挡住卡死的连接，
            # 真正的总时长上限靠上面每轮之前的预算检查。
            with endpoint.stream(**{**request, "messages": messages}, timeout=min(remaining, budget)) as stream:
                message = stream.get_final_message()
            rounds.append(message)
            usage.update(_round_usage(_field(message, "usage")))
            if _field(message, "stop_reason") != "pause_turn":
                break
            if usage["output_tokens"] > config.output_token_ceiling:
                return finish(
                    error_code=errors.BUDGET_EXCEEDED,
                    error_detail=f"output_tokens={usage['output_tokens']} ceiling={config.output_token_ceiling}",
                )
            if continuations >= config.max_continuations:
                return finish(
                    error_code=errors.CONTINUATION_LIMIT,
                    error_detail=f"still paused after {continuations} continuations",
                )
            # 追加而不是替换：连续的 assistant 消息由 API 合并成同一轮，前几轮的内容都要保留。
            messages = [*messages, {"role": "assistant", "content": _field(message, "content")}]
            continuations += 1
    except anthropic.APIStatusError as exc:
        code, detail = _status_error(exc)
        return finish(error_code=code, error_detail=detail)
    except anthropic.APIConnectionError as exc:
        # 含 APITimeoutError（连接失败与读超时都归为供应商暂不可用）。
        return finish(error_code=errors.PROVIDER_UNAVAILABLE, error_detail=_clip(f"{type(exc).__name__}: {exc}"))

    stop_reason = _field(message, "stop_reason")
    if stop_reason == "refusal":
        details = _field(message, "stop_details")
        category = _field(details, "category")
        explanation = _field(details, "explanation")
        text, _parsed = _final_text(message)
        return finish(
            error_code=errors.PROVIDER_REFUSAL,
            error_detail=_clip(f"category={category} {explanation or ''}".strip()),
            text=text,
        )
    if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
        text, _parsed = _final_text(message)
        return finish(error_code=errors.OUTPUT_TRUNCATED, error_detail=f"stop_reason={stop_reason}", text=text)
    if stop_reason not in {"end_turn", "stop_sequence"}:
        text, _parsed = _final_text(message)
        return finish(error_code=errors.UNEXPECTED_STOP_REASON, error_detail=f"stop_reason={stop_reason}", text=text)
    text, parsed = _final_text(message)
    if parsed is None:
        # 结构化输出与网页工具并用没有文档背书：结尾不是 JSON 时保留原文，不重试。
        return finish(
            error_code=errors.OUTPUT_NOT_JSON,
            error_detail="no_text_block" if text is None else "final text is not a JSON object",
            text=text,
        )
    return finish(text=text, parsed=parsed)


__all__ = [
    "InvocationResult",
    "MIN_REQUEST_SECONDS",
    "PRICE_CACHE_READ_PER_MTOK",
    "PRICE_CACHE_WRITE_1H_PER_MTOK",
    "PRICE_CACHE_WRITE_5M_PER_MTOK",
    "PRICE_INPUT_PER_MTOK",
    "PRICE_OUTPUT_PER_MTOK",
    "PRICE_WEB_SEARCH_EACH",
    "REFUSAL_FALLBACK_BETA",
    "build_request",
    "cost_microusd",
    "invoke",
    "make_client",
    "output_schema",
    "request_summary",
]
