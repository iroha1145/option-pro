from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

from app.api.stocks import _sanitize
from app.services import ai_analysis
from app.services.scoring import compute_market_scores, compute_stock_scores
from app.services.signals import compute_market_signals, compute_stock_signals

router = APIRouter(prefix="/api/signals", tags=["signals"])
logger = logging.getLogger(__name__)
_TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}
_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.^_-]{0,11}$")


def today_str() -> str:
    return datetime.now(timezone.utc).isoformat()


def _client_ip(request: Request) -> str:
    if _TRUST_PROXY_HEADERS:
        return (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
    return request.client.host if request.client else "unknown"


def _fingerprint(request: Request) -> str:
    return hashlib.md5(_client_ip(request).encode()).hexdigest()[:12]


def _normalize_ticker(ticker: str) -> str:
    symbol = ticker.upper().strip()
    if not _TICKER_PATTERN.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    return symbol


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
        logger.exception("Market signal calculation failed")
        raise HTTPException(503, "Market signals are currently unavailable") from exc


@router.get("/stock/{ticker}")
async def stock_signals(ticker: str):
    """Full stock top/bottom analysis with stock-level indicators."""
    try:
        symbol = _normalize_ticker(ticker)
        signals = await asyncio.to_thread(compute_stock_signals, symbol)
        cached = bool(isinstance(signals, dict) and signals.pop("_cached", False))
        scores = compute_stock_scores(signals)
        rsi = (signals.get("rsi14") or {}).get("value")
        macd = (signals.get("macd_hist") or {}).get("value")
        rs = (signals.get("relative_strength_spy") or {}).get("value")
        trend_bias_score = 50 + (float(rs or 0) * 2) + (float(macd or 0) * 100) + ((float(rsi or 50) - 50) * 0.4)
        trend_bias_score = round(max(0, min(100, trend_bias_score)))
        trend_bias_label = "偏多" if trend_bias_score >= 58 else ("偏空" if trend_bias_score <= 42 else "中性")
        return _sanitize({"ticker": symbol, "signals": signals, "scores": scores, "trend_bias_score": trend_bias_score, "trend_bias_label": trend_bias_label, "as_of": today_str(), "_cached": cached})
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Stock signal calculation failed for %s", ticker)
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
        logger.exception("AI signal analysis failed for %s", ticker)
        raise HTTPException(503, "AI signal analysis is currently unavailable") from exc
