from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
import weakref
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import StrictBool, Field

from app.json_validation import canonical_json_text
from app.api.ai import (
    _create_job,
    _job_repository,
    _require_manual_analysis_enabled,
    _require_runtime_capability,
    _require_stock_analysis_enabled,
)
from app.config import get_settings
from app.services.ai_jobs import runtime as ai_job_runtime
from app.services.runtime_settings import get_effective_runtime_settings, RuntimeSettingsStorageError
from app.services.utils import sanitize as _sanitize
from app.access import (
    current_request_is_owner,
    public_snapshot_unavailable,
    require_same_origin_action,
    request_is_owner_session,
)
from app.personal_config import get_personal_config
from app.services.http_read_cache import respond_with_snapshot, snapshot_version_key
from app.public_home_snapshot import (
    public_home_resource_parameters,
    read_owner_public_home_entry_async,
    read_public_home_resource_async,
)
from app.services.ai_jobs.models import StrictModel
from app.services.scoring import compute_market_scores, compute_stock_scores
from app.services.symbols import quote_symbol
from app.services.signal_context import CONTEXT_BLOCK_KEYS, build_signal_context
from app.services.signals import (
    cached_stock_signals,
    compute_market_signals,
    compute_stock_signals,
)
from app.stock_data_reads import read_latest_stock_resource as read_stock_pull_resource

router = APIRouter(prefix="/api/signals", tags=["signals"])
logger = logging.getLogger(__name__)
_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9._-]{0,10}|[A-Z0-9][A-Z0-9._-]{0,11})$"
)


class SignalAnalysisJobCreateRequest(StrictModel):
    force: StrictBool = False
    retry_of_job_id: str | None = Field(default=None, pattern=r"^aij_[a-f0-9]{32}$")


# 进程内按票合并证据采集，仓库事务另行保证跨进程的共享任务与账号额度。
# 弱引用字典让空闲的锁随用随收，不随历史票数增长。
_SIGNAL_ANALYSIS_LOCKS: "weakref.WeakValueDictionary[str, asyncio.Lock]" = (
    weakref.WeakValueDictionary()
)


def _signal_analysis_lock(symbol: str) -> asyncio.Lock:
    lock = _SIGNAL_ANALYSIS_LOCKS.get(symbol)
    if lock is None:
        lock = asyncio.Lock()
        _SIGNAL_ANALYSIS_LOCKS[symbol] = lock
    return lock


def today_str() -> str:
    return datetime.now(timezone.utc).isoformat()


#: 证据包（不含哈希字段）允许的最大规范化字节数。低于入队与提交共用的
#: 个股专用240KB上限（按转义后计），留出封装余量；
#: 超限时按 CONTEXT_BLOCK_KEYS 顺序整块丢弃并在 context_status 里标注。
_EVIDENCE_MAX_BYTES = 220_000


def _canonical_evidence_bytes(evidence: dict) -> bytes:
    return canonical_json_text(evidence).encode("utf-8")


def _signal_analysis_payload(
    symbol: str,
    signals: dict,
    scores: dict,
    *,
    context: dict | None = None,
    evidence_as_of: str | None = None,
    evidence_source: str = "live",
    evidence_stale: bool = False,
) -> dict:
    observed_at = (
        evidence_as_of
        or datetime.now(timezone.utc).date().isoformat()
    )
    evidence = {
        "ticker": symbol,
        "signals": _sanitize(signals),
        "scores": _sanitize(scores),
        "as_of": observed_at,
        "evidence_as_of": observed_at,
        "evidence_source": evidence_source,
        "evidence_stale": evidence_stale,
    }
    if context is not None:
        blocks = context.get("blocks") or {}
        context_status = dict(context.get("status") or {})
        for key in CONTEXT_BLOCK_KEYS:
            block = blocks.get(key)
            if block is not None:
                evidence[key] = _sanitize(block)
        context_tickers = [
            str(ticker)
            for ticker in context.get("context_tickers") or []
            if isinstance(ticker, str) and ticker
        ]
        if context_tickers:
            evidence["context_tickers"] = context_tickers
        evidence["context_status"] = context_status
        for key in CONTEXT_BLOCK_KEYS:
            if len(_canonical_evidence_bytes(evidence)) <= _EVIDENCE_MAX_BYTES:
                break
            if key in evidence:
                del evidence[key]
                context_status[key] = "omitted_size"
                if key == "recent_news":
                    # context_tickers 只来自新闻块；块丢了还留着它，会把模型
                    # 看不到的代码并进结果校验的 allowed_codes。
                    evidence.pop("context_tickers", None)
    canonical = _canonical_evidence_bytes(evidence)
    return {
        **evidence,
        "evidence_hash": hashlib.sha256(canonical).hexdigest(),
    }


def _normalize_ticker(ticker: str) -> str:
    symbol = quote_symbol(ticker)
    if not _TICKER_PATTERN.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    return symbol


def _trend_bias(signals: dict) -> dict:
    raw_values = {
        "relative_strength_spy": (signals.get("relative_strength_spy") or {}).get(
            "value"
        ),
        "macd_hist": (signals.get("macd_hist") or {}).get("value"),
        "rsi14": (signals.get("rsi14") or {}).get("value"),
    }
    values: dict[str, float] = {}
    for key, value in raw_values.items():
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            values[key] = number

    missing = [key for key in raw_values if key not in values]
    coverage = len(values) / len(raw_values)
    if len(values) < 2:
        return {
            "score": None,
            "label": "数据不足",
            "status": "insufficient_data",
            "coverage": round(coverage, 4),
            "missing_components": missing,
        }

    deltas = []
    if "relative_strength_spy" in values:
        deltas.append(values["relative_strength_spy"] * 2)
    if "macd_hist" in values:
        deltas.append(values["macd_hist"] * 100)
    if "rsi14" in values:
        deltas.append((values["rsi14"] - 50) * 0.4)
    # Preserve the established three-component result when all inputs exist;
    # when one is missing, renormalize only across the two observed inputs.
    score = round(max(0, min(100, 50 + sum(deltas) * (3 / len(deltas)))))
    label = "偏多" if score >= 58 else ("偏空" if score <= 42 else "中性")
    return {
        "score": score,
        "label": label,
        "status": "active" if not missing else "degraded",
        "coverage": round(coverage, 4),
        "missing_components": missing,
    }


async def _build_market_signals_payload() -> dict:
    signals = await asyncio.to_thread(compute_market_signals)
    if not isinstance(signals, dict):
        raise RuntimeError("Market signals payload is unavailable")
    cleaned = dict(signals)
    cleaned.pop("_cached", None)
    return _sanitize(
        {
            "signals": cleaned,
            "scores": compute_market_scores(cleaned),
            "as_of": today_str(),
        }
    )


async def _owner_stock_signal_evidence(
    symbol: str,
) -> tuple[dict, dict | None, str | None]:
    """Use a fresh durable resource first, then live data with durable fallback."""

    saved = await asyncio.to_thread(
        read_stock_pull_resource,
        symbol,
        "signals",
    )
    if saved is not None and saved["fresh"]:
        return dict(saved["payload"]), saved, saved.get("source", "manual_pull")
    try:
        live = await asyncio.to_thread(compute_stock_signals, symbol)
        if not isinstance(live, dict):
            raise RuntimeError("Stock signals payload is unavailable")
        return dict(live), saved, None
    except Exception:
        if saved is None:
            raise
        return dict(saved["payload"]), saved, saved.get("source", "manual_pull")


@router.get("/market")
async def market_signals(request: Request):
    """Read the worker-published market-signal snapshot without blocking GETs."""
    try:
        owner = current_request_is_owner()
        config = get_personal_config()
        now = time.time()
        parameters = public_home_resource_parameters("market_signals", now=now)
        cache_control = "private, max-age=30, stale-while-revalidate=120"
        if owner:
            disk_entry = await read_owner_public_home_entry_async(
                "market_signals",
                parameters=parameters,
                fresh_for_seconds=float(config.public_home.signals_seconds),
                now=now,
            )
            if disk_entry is not None:
                payload = dict(disk_entry["payload"])
                payload["_cached"] = True
                payload["snapshot_source"] = "worker"
                return await respond_with_snapshot(
                    request,
                    _sanitize(payload),
                    version_key=snapshot_version_key(
                        "market_signals",
                        "owner",
                        disk_entry["saved_at"],
                        bool(disk_entry["fresh"]),
                    ),
                    cache_control=cache_control,
                )
            # Private-network installations do not run PublicHomeTask. Keep
            # their owner-only live path, while password-mode production is
            # normally served from the restart-safe worker snapshot.
            if config.access.mode != "password":
                return await _build_market_signals_payload()
        else:
            payload = await read_public_home_resource_async(
                "market_signals",
                parameters=parameters,
                now=now,
            )
            if payload is not None:
                payload["_cached"] = True
                payload["snapshot_source"] = "worker"
                return await respond_with_snapshot(
                    request,
                    _sanitize(payload),
                    version_key=snapshot_version_key(
                        "market_signals",
                        "public",
                        payload.get("snapshot_saved_at"),
                    ),
                    cache_control=cache_control,
                )
        raise public_snapshot_unavailable("signals:market")
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Market signal calculation failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Market signals are currently unavailable") from exc


@router.get("/stock/{ticker}")
async def stock_signals(ticker: str):
    """Full stock top/bottom analysis with stock-level indicators."""
    try:
        symbol = _normalize_ticker(ticker)
        owner = current_request_is_owner()
        saved: dict | None = None
        snapshot_source: str | None = None
        if owner:
            signals, saved, snapshot_source = (
                await _owner_stock_signal_evidence(symbol)
            )
        else:
            saved = await asyncio.to_thread(
                read_stock_pull_resource,
                symbol,
                "signals",
            )
            if saved is not None:
                signals = dict(saved["payload"])
                snapshot_source = saved.get("source", "manual_pull")
            else:
                signals = cached_stock_signals(symbol)
        if signals is None:
            raise public_snapshot_unavailable(f"signals:stock:{symbol}")
        signals = dict(signals)
        cached = bool(isinstance(signals, dict) and signals.pop("_cached", False))
        scores = compute_stock_scores(signals)
        trend = _trend_bias(signals)
        snapshot_saved_at = (
            datetime.fromtimestamp(
                float(saved["saved_at"]),
                timezone.utc,
            ).isoformat()
            if snapshot_source is not None and saved is not None
            else None
        )
        payload = {
            "ticker": symbol,
            "signals": signals,
            "scores": scores,
            "trend_bias_score": trend["score"],
            "trend_bias_label": trend["label"],
            "trend_bias_status": trend["status"],
            "trend_bias_coverage": trend["coverage"],
            "trend_bias_missing_components": trend["missing_components"],
            "as_of": snapshot_saved_at or today_str(),
            "_cached": cached or snapshot_source is not None,
        }
        if snapshot_source is not None and saved is not None:
            payload.update(
                {
                    "snapshot_source": snapshot_source,
                    "snapshot_saved_at": snapshot_saved_at,
                    "_stale": not bool(saved["fresh"]),
                }
            )
        return _sanitize(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Stock signal calculation failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Stock signals are currently unavailable") from exc


def _analysis_account_id(http_request: Request) -> str:
    from app.api.accounts import require_personal_account
    return str(require_personal_account(http_request).user_id)


def _signal_analysis_identity() -> dict:
    settings = ai_job_runtime.settings_for_job(get_settings(), "signal_analysis")
    schema_version, schema_sha256 = ai_job_runtime.schema_identity("signal_analysis", model=settings.openai_model)
    return {
        "model": settings.openai_model,
        "reasoning": settings.openai_reasoning,
        "prompt_version": ai_job_runtime.PROMPT_VERSIONS["signal_analysis"],
        "schema_version": schema_version,
        "schema_sha256": schema_sha256,
    }


def _public_signal_analysis(row: dict, *, cached: bool = False) -> dict:
    from app.services.stock_report_visuals import stock_report_visuals
    public = _job_repository().public(row, cached=cached)
    public["visuals"] = stock_report_visuals(row) if public.get("result") is not None else None
    if not current_request_is_owner():
        public["error_detail"] = None
        public["cancellable"] = False
    return public


@router.get("/stock/{ticker}/ai-analysis")
async def get_stock_ai_analysis(ticker: str, http_request: Request):
    account_id = _analysis_account_id(http_request)
    symbol = _normalize_ticker(ticker)
    repository = _job_repository()
    snapshot = await asyncio.to_thread(repository.stock_analysis_snapshot, account_id, symbol, **_signal_analysis_identity())
    quota, shared = snapshot["quota"], snapshot["shared_job"]
    current = snapshot["current_job"]
    latest = await asyncio.to_thread(repository.latest_completed, "signal_analysis", symbol)
    active = shared if shared and shared["status"] in {"pending", "queued", "in_progress"} else None
    if snapshot["cached_job"]:
        latest = snapshot["cached_job"]
    try:
        enabled = bool(get_effective_runtime_settings().ai.stock_analysis_enabled)
    except RuntimeSettingsStorageError as exc:
        raise HTTPException(status_code=503, detail={"code": "runtime_settings_unavailable"}) from exc
    return {
        "schema_version": "stock-analysis-view-v1",
        "ticker": symbol,
        "enabled": enabled,
        "can_generate": enabled and quota["remaining"] > 0 and current is None,
        "can_retry": enabled and quota["remaining"] > 0 and shared is None and current is not None and repository.stock_analysis_retryable(current),
        "current_job": _public_signal_analysis(current) if current else None,
        "quota": quota,
        "cache_reset_at": quota["reset_at"],
        "latest_job_cached": snapshot["cached_job"] is not None,
        "active_job": _public_signal_analysis(active) if active else None,
        "latest_job": _public_signal_analysis(latest, cached=snapshot["cached_job"] is not None) if latest else None,
    }


@router.post(
    "/stock/{ticker}/ai-analysis",
    dependencies=[Depends(require_same_origin_action)],
)
async def stock_ai_analysis(
    ticker: str,
    http_request: Request,
    request: SignalAnalysisJobCreateRequest = Body(
        default_factory=SignalAnalysisJobCreateRequest
    ),
):
    """Snapshot deterministic evidence and queue the paid model work."""

    account_id = _analysis_account_id(http_request)
    _require_stock_analysis_enabled()
    _require_runtime_capability("signal_analysis")
    if request.force and not request_is_owner_session(http_request):
        raise HTTPException(status_code=403, detail={"code": "owner_access_required", "message": "仅站主可强制重试"})
    symbol = _normalize_ticker(ticker)
    async with _signal_analysis_lock(symbol):
        return await _queue_signal_analysis(symbol, force=request.force, account_id=account_id, retry_of_job_id=request.retry_of_job_id)


async def _queue_signal_analysis(symbol: str, *, force: bool, account_id: str, retry_of_job_id: str | None = None) -> JSONResponse:
    # 先复用同股票、同模型配置的共享报告；最终额度与创建在仓库事务内重查。
    # 同步仓库调用在线程中运行，写锁等待不会阻塞其他请求。
    repository = _job_repository()
    snapshot = await asyncio.to_thread(repository.stock_analysis_snapshot, account_id, symbol, **_signal_analysis_identity())
    quota, active = snapshot["quota"], snapshot["shared_job"]
    receipt = snapshot["current_job"]
    if active is None and receipt is not None and (
        retry_of_job_id != receipt["job_id"] or not repository.stock_analysis_retryable(receipt)
    ):
        active = receipt
    if retry_of_job_id is not None and receipt is None:
        raise HTTPException(409, detail={"code": "stock_analysis_retry_not_current"})
    if active is not None:
        public = _public_signal_analysis(active, cached=active["status"] == "completed")
        public["quota"] = quota
        return JSONResponse(
            public,
            status_code=202,
            headers={
                "Location": f"/api/ai/jobs/{active['job_id']}",
                "Retry-After": "2",
            },
        )
    if quota["remaining"] == 0:
        raise HTTPException(status_code=429, detail={
            "code": "stock_analysis_daily_limit",
            "message": "本时段的个股分析额度已用完，美股收盘后恢复",
            "reset_at": quota["reset_at"], "quota": quota,
        })
    if retry_of_job_id is not None:
        row, created = await asyncio.to_thread(
            _create_job, "signal_analysis", json.loads(receipt["payload_json"]),
            force_retry=True, stock_analysis_account_id=account_id,
            stock_analysis_retry_job_id=retry_of_job_id,
        )
        public = _public_signal_analysis(row, cached=not created and row["status"] == "completed")
        public["quota"] = await asyncio.to_thread(repository.stock_analysis_quota, account_id)
        return JSONResponse(public, status_code=202, headers={"Location": f"/api/ai/jobs/{row['job_id']}", "Retry-After": "2"})
    try:
        signals, saved, snapshot_source = (
            await _owner_stock_signal_evidence(symbol)
        )
        evidence_stale = bool(
            snapshot_source is not None
            and saved is not None
            and not saved["fresh"]
        )
        if evidence_stale:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "stale_signal_evidence",
                    "message": "技术信号已过期，请先手动拉取最新行情后再分析",
                    "ticker": symbol,
                },
            )
        signals.pop("_cached", None)
        scores = compute_stock_scores(signals)
        evidence_as_of = (
            datetime.fromtimestamp(
                float(saved["saved_at"]),
                timezone.utc,
            ).isoformat()
            if snapshot_source is not None and saved is not None
            else datetime.now(timezone.utc).isoformat()
        )
        context = await build_signal_context(symbol)
        evidence_kwargs = {
            "evidence_as_of": evidence_as_of,
            "evidence_source": snapshot_source or "live",
            "evidence_stale": False,
        }
        try:
            row, created = await asyncio.to_thread(
                _create_job,
                "signal_analysis",
                _signal_analysis_payload(
                    symbol,
                    signals,
                    scores,
                    context=context,
                    **evidence_kwargs,
                ),
                force_retry=force,
                stock_analysis_account_id=account_id,
            )
        except ValueError as exc:
            if str(exc) != "ai_job_payload_too_large":
                raise
            # 上下文块的兜底裁剪之后核心信号仍超限才会走到这里；退回
            # 无上下文证据，保住手动分析本身可用。
            row, created = await asyncio.to_thread(
                _create_job,
                "signal_analysis",
                _signal_analysis_payload(symbol, signals, scores, **evidence_kwargs),
                force_retry=force,
                stock_analysis_account_id=account_id,
            )
    except ValueError as exc:
        if str(exc) == "ai_job_payload_too_large":
            raise HTTPException(
                status_code=413,
                detail="Signal snapshot is too large for AI analysis",
            ) from exc
        raise
    public = _public_signal_analysis(
        row,
        cached=(not created and row["status"] == "completed"),
    )
    public["quota"] = await asyncio.to_thread(repository.stock_analysis_quota, account_id)
    return JSONResponse(
        public,
        status_code=202,
        headers={
            "Location": f"/api/ai/jobs/{row['job_id']}",
            "Retry-After": "2",
        },
    )
