from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.api.stocks import _sanitize
from app.services import ai_analysis
from app.services.scoring import compute_market_scores, compute_stock_scores
from app.services.signals import compute_market_signals, compute_stock_signals
from app.services.request_security import request_client_ip

router = APIRouter(prefix="/api/signals", tags=["signals"])
logger = logging.getLogger(__name__)
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.^_-]{0,11}$")


def today_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip(request: Request) -> str:
    return request_client_ip(request)


def _fingerprint(request: Request) -> str:
    return hashlib.md5(_client_ip(request).encode()).hexdigest()[:12]


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
async def stock_ai_analysis(ticker: str, request: Request):
    """LLM confidence analysis on computed signals. Triggered only by explicit user action."""
    try:
        fp = _fingerprint(request)
        symbol = _normalize_ticker(ticker)
        signals = await asyncio.to_thread(compute_stock_signals, symbol)
        if isinstance(signals, dict):
            signals.pop("_cached", None)
        scores = compute_stock_scores(signals)
        # 60s ceiling — fallback to programmatic-only response if AI hangs
        _AI_TIMEOUT_S = 60
        try:
            llm_result = await asyncio.wait_for(
                asyncio.to_thread(ai_analysis.analyze_signals, symbol, signals, scores, fp),
                timeout=_AI_TIMEOUT_S
            )
        except asyncio.TimeoutError:
            llm_result = {
                "asset": symbol,
                "dominant_regime": "ai_timeout",
                "summary": f"AI 分析超时（>{_AI_TIMEOUT_S}秒），仅展示程序化分数",
                "top_risk_confidence": scores.get("top_score"),
                "bottom_opportunity_confidence": scores.get("bottom_score"),
                "dip_buy_quality": scores.get("dip_buy_quality"),
                "data_quality": scores.get("data_quality"),
                "final_bias": "insufficient_data",
                "error": "ai_timeout",
            }
        return _sanitize({**llm_result, "raw_signals": signals, "raw_scores": scores, "as_of": today_str()})
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("AI signal analysis failed (%s)", type(exc).__name__)
        raise HTTPException(503, "AI signal analysis is currently unavailable") from exc
