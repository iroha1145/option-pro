from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import JSONResponse
from pydantic import StrictBool

from app.api.ai import (
    _create_job,
    _job_repository,
    _require_manual_analysis_enabled,
    _require_runtime_capability,
)
from app.api.stocks import _sanitize
from app.services.ai_jobs.models import StrictModel
from app.services.scoring import compute_market_scores, compute_stock_scores
from app.services.signals import compute_market_signals, compute_stock_signals

router = APIRouter(prefix="/api/signals", tags=["signals"])
logger = logging.getLogger(__name__)
_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9._-]{0,10}|[A-Z0-9][A-Z0-9._-]{0,11})$"
)


class SignalAnalysisJobCreateRequest(StrictModel):
    force: StrictBool = False


def today_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _signal_analysis_payload(
    symbol: str,
    signals: dict,
    scores: dict,
) -> dict:
    evidence = {
        "ticker": symbol,
        "signals": _sanitize(signals),
        "scores": _sanitize(scores),
    }
    canonical = json.dumps(
        evidence,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        **evidence,
        "as_of": datetime.now(timezone.utc).date().isoformat(),
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


@router.get("/market")
async def market_signals():
    """Full market top/bottom analysis with market-level indicators."""
    try:
        signals = await asyncio.to_thread(compute_market_signals)
        cached = bool(isinstance(signals, dict) and signals.pop("_cached", False))
        scores = compute_market_scores(signals)
        return _sanitize({"signals": signals, "scores": scores, "as_of": today_str(), "_cached": cached})
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
        signals = await asyncio.to_thread(compute_stock_signals, symbol)
        cached = bool(isinstance(signals, dict) and signals.pop("_cached", False))
        scores = compute_stock_scores(signals)
        trend = _trend_bias(signals)
        return _sanitize({
            "ticker": symbol,
            "signals": signals,
            "scores": scores,
            "trend_bias_score": trend["score"],
            "trend_bias_label": trend["label"],
            "trend_bias_status": trend["status"],
            "trend_bias_coverage": trend["coverage"],
            "trend_bias_missing_components": trend["missing_components"],
            "as_of": today_str(),
            "_cached": cached,
        })
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Stock signal calculation failed (%s)", type(exc).__name__)
        raise HTTPException(503, "Stock signals are currently unavailable") from exc


@router.post("/stock/{ticker}/ai-analysis")
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
    try:
        signals = await asyncio.to_thread(compute_stock_signals, symbol)
        if isinstance(signals, dict):
            signals.pop("_cached", None)
        scores = compute_stock_scores(signals)
        row, created = _create_job(
            "signal_analysis",
            _signal_analysis_payload(symbol, signals, scores),
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
