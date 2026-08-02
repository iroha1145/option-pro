from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import StrictBool

from app.api.ai import (
    _create_job,
    _job_repository,
    _require_manual_analysis_enabled,
    _require_runtime_capability,
)
from app.api.stocks import _sanitize
from app.access import (
    current_request_is_owner,
    public_snapshot_unavailable,
    require_same_origin_action,
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
from app.services.signal_context import CONTEXT_BLOCK_KEYS, build_signal_context
from app.services.signals import (
    cached_stock_signals,
    compute_market_signals,
    compute_stock_signals,
)
from app.stock_pull_snapshot import read_stock_pull_resource

router = APIRouter(prefix="/api/signals", tags=["signals"])
logger = logging.getLogger(__name__)
_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9._-]{0,10}|[A-Z0-9][A-Z0-9._-]{0,11})$"
)


class SignalAnalysisJobCreateRequest(StrictModel):
    force: StrictBool = False


def today_str() -> str:
    return datetime.now(timezone.utc).isoformat()


#: 证据包（不含哈希字段）允许的最大规范化字节数。低于 runtime 的
#: 60KB(_MAX_UNTRUSTED_JSON_BYTES) 与仓库的 64KB 上限，留出封装余量；
#: 超限时按 CONTEXT_BLOCK_KEYS 顺序整块丢弃并在 context_status 里标注。
_EVIDENCE_MAX_BYTES = 52_000


def _canonical_evidence_bytes(evidence: dict) -> bytes:
    return json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


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
    canonical = _canonical_evidence_bytes(evidence)
    return {
        **evidence,
        "evidence_hash": hashlib.sha256(canonical).hexdigest(),
    }


def _normalize_ticker(ticker: str) -> str:
    symbol = ticker.upper().strip()
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
    """Use a fresh manual pull first, then live data with durable fallback."""

    saved = await asyncio.to_thread(
        read_stock_pull_resource,
        symbol,
        "signals",
    )
    if saved is not None and saved["fresh"]:
        return dict(saved["payload"]), saved, "manual_pull"
    try:
        live = await asyncio.to_thread(compute_stock_signals, symbol)
        if not isinstance(live, dict):
            raise RuntimeError("Stock signals payload is unavailable")
        return dict(live), saved, None
    except Exception:
        if saved is None:
            raise
        return dict(saved["payload"]), saved, "manual_pull"


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
            signals = cached_stock_signals(symbol)
            if signals is None and saved is not None:
                signals = dict(saved["payload"])
                snapshot_source = "manual_pull"
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


@router.post(
    "/stock/{ticker}/ai-analysis",
    dependencies=[Depends(require_same_origin_action)],
)
async def stock_ai_analysis(
    ticker: str,
    request: SignalAnalysisJobCreateRequest = Body(
        default_factory=SignalAnalysisJobCreateRequest
    ),
):
    """Snapshot deterministic evidence and queue the paid model work."""

    _require_manual_analysis_enabled()
    _require_runtime_capability()
    symbol = _normalize_ticker(ticker)
    # 证据包含动态上下文块后，同票重复提交的 request_hash 几乎必然不同，
    # 仓库级哈希去重挡不住重复付费。非 force 提交先复用在跑任务。
    active = _job_repository().active_for_ticker("signal_analysis", symbol)
    if active is not None and not request.force:
        public = _job_repository().public(active, cached=False)
        return JSONResponse(
            public,
            status_code=202,
            headers={
                "Location": f"/api/ai/jobs/{active['job_id']}",
                "Retry-After": "2",
            },
        )
    try:
        signals, saved, snapshot_source = (
            await _owner_stock_signal_evidence(symbol)
        )
        evidence_stale = bool(
            snapshot_source == "manual_pull"
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
            if snapshot_source == "manual_pull" and saved is not None
            else datetime.now(timezone.utc).isoformat()
        )
        context = await build_signal_context(symbol)
        evidence_kwargs = {
            "evidence_as_of": evidence_as_of,
            "evidence_source": snapshot_source or "live",
            "evidence_stale": False,
        }
        try:
            row, created = _create_job(
                "signal_analysis",
                _signal_analysis_payload(
                    symbol,
                    signals,
                    scores,
                    context=context,
                    **evidence_kwargs,
                ),
                force_retry=request.force,
            )
        except ValueError as exc:
            if str(exc) != "ai_job_payload_too_large":
                raise
            # 上下文块的兜底裁剪之后核心信号仍超限才会走到这里；退回
            # 无上下文证据，保住手动分析本身可用。
            row, created = _create_job(
                "signal_analysis",
                _signal_analysis_payload(symbol, signals, scores, **evidence_kwargs),
                force_retry=request.force,
            )
    except ValueError as exc:
        if str(exc) == "ai_job_payload_too_large":
            raise HTTPException(
                status_code=413,
                detail="Signal snapshot is too large for AI analysis",
            ) from exc
        raise
    public = _job_repository().public(
        row,
        cached=(not created and row["status"] == "completed"),
    )
    return JSONResponse(
        public,
        status_code=202,
        headers={
            "Location": f"/api/ai/jobs/{row['job_id']}",
            "Retry-After": "2",
        },
    )
