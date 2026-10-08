"""一次研判 = 组装证据包 → 一次 Claude 请求（含服务端工具循环）→ 校验 → 落盘。

worker 任务与命令行工具只调用 ``run_brief``；Anthropic 客户端经 ``client_factory`` 注入，
测试里用假客户端。``BriefRunConfig`` 由 personal.toml [market_brief] 映射而来。
"""

from __future__ import annotations

import copy
import hashlib
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence

from pydantic import ValidationError

from app.failure_diagnostics import record_fallback_failure

from . import errors
from .claude_runtime import build_request, cost_microusd, invoke, make_client, request_summary
from .evidence import EvidencePack, build_evidence
from .prompt import PROMPT_VERSION, build_system_prompt, build_user_message
from .schema import SCHEMA_VERSION, BriefSlot, BriefTrigger, MarketBriefResult
from .store import BriefRunRecord, BriefStore, new_run_id

# 校验失败时可以整项删除的列表（* 代表列表下标）；其余位置的失败都算标量失败。
_REMOVABLE_LISTS: frozenset[tuple[Any, ...]] = frozenset({
    ("sectors",),
    ("key_news",),
    ("watch_items",),
    ("invalidators",),
    ("internals", "points"),
    ("macro_check", "points"),
    ("internals", "evidence_ids"),
    ("macro_check", "evidence_ids"),
    ("sectors", "*", "evidence_ids"),
    ("key_news", "*", "tickers"),
})
_SOFT_REMOVAL_ROUNDS = 5
_DETAIL_CHARS = 500
_WARNING_MESSAGE_CHARS = 160


@dataclass(frozen=True)
class BriefRunConfig:
    model: str = "claude-opus-5-5"
    effort: str = "xhigh"
    max_output_tokens: int = 48_000
    max_continuations: int = 4
    output_token_ceiling: int = 160_000
    web_search_max_uses: int = 10
    web_fetch_max_uses: int = 8
    web_fetch_max_content_tokens: int = 12_000
    code_execution_tool: bool = False
    refusal_fallback: bool = False
    request_timeout_seconds: float = 1500.0
    evidence_max_bytes: int = 56_000
    prompt_cache_ttl: str = "5m"
    # 结构化输出与网页工具并用没有文档背书（文档写明结构化输出与引用不兼容，而网页搜索结果
    # 自带引用）。若首跑被 400 拒绝，关掉它：请求不带 output_config.format，改由系统提示词
    # 附上 JSON Schema，解析与校验流程不变。
    structured_output: bool = True


@dataclass(frozen=True)
class _Validation:
    result: dict[str, Any] | None
    warnings: tuple[str, ...]
    error_detail: str | None


def _path_text(path: Sequence[Any]) -> str:
    text = ""
    for part in path:
        text += f"[{part}]" if isinstance(part, int) else (f".{part}" if text else str(part))
    return text or "<root>"


def _pattern(path: Sequence[Any]) -> tuple[Any, ...]:
    return tuple("*" if isinstance(part, int) else part for part in path)


def _locate(document: Any, path: Sequence[Any]) -> Any:
    target = document
    for part in path:
        target = target[part]
    return target


def _plan_repairs(
    problems: Sequence[Mapping[str, Any]],
) -> tuple[list[tuple[tuple[Any, ...], str, Any]], list[str]] | None:
    """把 pydantic 错误归到可删除的列表项或可截断的列表；有任何标量失败时返回 None。

    返回的操作按路径由深到浅排列（同一列表先删项再截断）：先动内层列表，
    外层列表删项造成的下标移动就不会让内层路径指错对象。
    """

    removals: dict[tuple[Any, ...], set[int]] = {}
    truncations: dict[tuple[Any, ...], int] = {}
    notes: list[str] = []
    for problem in problems:
        location = tuple(problem.get("loc") or ())
        message = str(problem.get("msg") or "")[:_WARNING_MESSAGE_CHARS]
        limit = (problem.get("ctx") or {}).get("max_length")
        if problem.get("type") == "too_long" and _pattern(location) in _REMOVABLE_LISTS and isinstance(limit, int):
            # transform_schema 把 maxItems 挪进了描述，API 不强制条数；多出来的尾部截掉。
            truncations[location] = min(limit, truncations.get(location, limit))
            notes.append(f"truncated {_path_text(location)} to {limit} items")
            continue
        index = next(
            (
                position
                for position in range(len(location) - 1, -1, -1)
                if isinstance(location[position], int) and _pattern(location[:position]) in _REMOVABLE_LISTS
            ),
            None,
        )
        if index is None:
            return None
        removals.setdefault(location[:index], set()).add(location[index])
        notes.append(f"removed {_path_text(location[: index + 1])}: {message}")
    operations: list[tuple[tuple[Any, ...], str, Any]] = [
        (path, "remove", sorted(indexes, reverse=True)) for path, indexes in removals.items()
    ]
    operations.extend((path, "truncate", limit) for path, limit in truncations.items())
    operations.sort(key=lambda item: (-len(item[0]), item[1] != "remove"))
    return operations, notes


def _scalar_detail(problems: Sequence[Mapping[str, Any]]) -> str:
    parts = [f"{_path_text(tuple(item.get('loc') or ()))}: {item.get('msg')}" for item in problems[:5]]
    text = "; ".join(parts)
    return text if len(text) <= _DETAIL_CHARS else text[: _DETAIL_CHARS - 1] + "…"


def _check_references(result: dict[str, Any], pack: EvidencePack) -> list[str]:
    """引用检查：未知 evidence id 剔除；key_news 引用未知 id 整条剔除；代码不在 allowed_codes 剔除。"""

    warnings: list[str] = []

    def keep_known(path: str, values: list[str]) -> list[str]:
        unknown = [value for value in values if value not in pack.evidence_ids]
        if unknown:
            warnings.append(f"dropped unknown evidence ids at {path}: {', '.join(unknown)}")
        return [value for value in values if value in pack.evidence_ids]

    for section in ("internals", "macro_check"):
        result[section]["evidence_ids"] = keep_known(f"{section}.evidence_ids", result[section]["evidence_ids"])
    for index, sector in enumerate(result["sectors"]):
        sector["evidence_ids"] = keep_known(f"sectors[{index}].evidence_ids", sector["evidence_ids"])
    kept_news = []
    for index, item in enumerate(result["key_news"]):
        if item["evidence_id"] not in pack.evidence_ids:
            warnings.append(f"removed key_news[{index}]: unknown evidence id {item['evidence_id']}")
            continue
        unknown = [ticker for ticker in item["tickers"] if ticker not in pack.allowed_codes]
        if unknown:
            warnings.append(f"dropped tickers outside allowed_codes at key_news[{index}]: {', '.join(unknown)}")
            item["tickers"] = [ticker for ticker in item["tickers"] if ticker in pack.allowed_codes]
        kept_news.append(item)
    result["key_news"] = kept_news
    return warnings


def validate_result(text: str, pack: EvidencePack) -> _Validation:
    """契约与简体中文校验 + 软剔除 + 引用检查。

    列表项（板块、新闻、观察项、反证、各段要点与引用）失败时删掉该项重验，最多 5 轮，
    每次删除记一条 warning；标量字段失败直接判 schema_validation_failed。
    """

    context = {"allowed_codes": sorted(pack.allowed_codes), "source_texts": list(pack.source_texts)}
    warnings: list[str] = []
    candidate = text
    working: Any = None
    model: MarketBriefResult | None = None
    for attempt in range(_SOFT_REMOVAL_ROUNDS + 1):
        try:
            model = MarketBriefResult.model_validate_json(candidate, context=context)
            break
        except ValidationError as exc:
            problems = exc.errors(include_url=False)
            plan = _plan_repairs(problems) if attempt < _SOFT_REMOVAL_ROUNDS else None
            if plan is None:
                return _Validation(None, tuple(warnings), _scalar_detail(problems))
            if working is None:
                working = json.loads(text)
            operations, notes = plan
            for path, kind, argument in operations:
                items = _locate(working, path)
                if kind == "remove":
                    for index in argument:
                        del items[index]
                else:
                    del items[argument:]
            warnings.extend(notes)
            candidate = json.dumps(working, ensure_ascii=False)
    assert model is not None
    result = model.model_dump(mode="json")
    reference_warnings = _check_references(result, pack)
    if reference_warnings:
        warnings.extend(reference_warnings)
        # 剔除只会缩短列表；再验一遍，保证落盘的结果本身符合契约。
        result = MarketBriefResult.model_validate(result, context=context).model_dump(mode="json")
    return _Validation(result, tuple(warnings), None)


def _evidence_insufficient(pack: EvidencePack) -> bool:
    # 指数与市场内部结构（均线信号、市场状态）全缺时没有可写的大盘判断。
    return all(pack.block_missing(block) for block in ("indices", "market_signals", "market_regime"))


def _aware(now: datetime) -> datetime:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return now.astimezone(timezone.utc)


def run_brief(
    *,
    slot: BriefSlot,
    trading_date: date,
    trigger: BriefTrigger,
    store: BriefStore,
    config: BriefRunConfig,
    api_key: str,
    now: datetime | None = None,
    client_factory: Callable[[str, float], Any] | None = None,
    evidence_builder: Callable[..., Any] | None = None,
    on_request: Callable[[Mapping[str, Any]], None] | None = None,
) -> BriefRunRecord:
    """同步函数（阻塞网络 I/O），worker 里放线程调用。

    成功与失败都会写入 store（status=completed/failed，error_code 见 errors.py），
    并返回同一条记录；不抛供应商异常，只在程序错误时抛（抛之前先记一条 runtime_error）。
    ``on_request`` 在发送前收到请求参数摘要（命令行用它打印，便于排查 400）。
    """

    if slot not in ("pre_open", "post_close"):
        raise ValueError(f"unknown market brief slot: {slot!r}")
    if trigger not in ("scheduled", "manual"):
        raise ValueError(f"unknown market brief trigger: {trigger!r}")
    if not api_key:
        raise ValueError(errors.ANTHROPIC_API_KEY_MISSING)
    started_at = _aware(now) if now is not None else datetime.now(timezone.utc)
    started = time.monotonic()
    run_id = new_run_id(trading_date, slot)
    collected: dict[str, Any] = {}

    def finish(**fields: Any) -> BriefRunRecord:
        duration = round(time.monotonic() - started, 3)
        record = BriefRunRecord(
            run_id=run_id,
            slot=slot,
            trading_date=trading_date,
            trigger=trigger,
            started_at=started_at,
            completed_at=started_at + timedelta(seconds=duration),
            model=config.model,
            effort=config.effort,
            duration_seconds=duration,
            **{**collected, **fields},
        )
        store.write_run(record)
        return record

    try:
        pack: EvidencePack = (evidence_builder or build_evidence)(
            slot=slot,
            trading_date=trading_date,
            now=started_at,
            store=store,
            max_bytes=config.evidence_max_bytes,
        )
        collected.update(evidence=pack.payload, evidence_bytes=pack.bytes, coverage=pack.coverage)
        if _evidence_insufficient(pack):
            return finish(
                status="failed",
                error_code=errors.EVIDENCE_UNAVAILABLE,
                error_detail="indices, market_signals and market_regime are all missing",
            )
        system_text = build_system_prompt(config)
        request = build_request(
            config=config,
            system_text=system_text,
            user_text=build_user_message(pack, slot=slot, trading_date=trading_date, now=started_at),
        )
        request_meta = {
            **request_summary(request),
            "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
            # 系统提示词与工具一起构成缓存前缀；摘要变了说明前缀变了，缓存读取会归零。
            "system_sha256": hashlib.sha256(system_text.encode("utf-8")).hexdigest()[:16],
            "evidence_bytes": pack.bytes,
            "missing_blocks": copy.deepcopy(pack.coverage.get("missing_blocks") or []),
            "request_timeout_seconds": config.request_timeout_seconds,
        }
        collected["request_meta"] = request_meta
        if on_request is not None:
            on_request(request_meta)
        client = (client_factory or make_client)(api_key, config.request_timeout_seconds)
        # 预算从运行开始起算：worker 的任务超时管的是整次 run_brief，证据组装也在其中。
        invocation = invoke(client, request, config=config, deadline=started + config.request_timeout_seconds)
        collected.update(
            raw_output_text=invocation.text,
            external_sources=invocation.external_sources,
            usage=invocation.usage,
            cost_microusd=cost_microusd(invocation.usage),
            continuation_count=invocation.continuation_count,
        )
        if invocation.error_code is not None:
            return finish(status="failed", error_code=invocation.error_code, error_detail=invocation.error_detail)
        assert invocation.text is not None
        validation = validate_result(invocation.text, pack)
        if validation.result is None:
            return finish(
                status="failed",
                error_code=errors.SCHEMA_VALIDATION_FAILED,
                error_detail=validation.error_detail,
                validation_warnings=validation.warnings,
            )
        return finish(status="completed", result=validation.result, validation_warnings=validation.warnings)
    except Exception as exc:
        # 程序错误：先留一条 runtime_error 让状态接口看得见，再原样抛给调用方。
        try:
            finish(status="failed", error_code=errors.RUNTIME_ERROR, error_detail=type(exc).__name__)
        except Exception as record_error:
            record_fallback_failure("market_brief_runtime_record", record_error)
        raise


__all__ = ["BriefRunConfig", "run_brief", "validate_result"]
