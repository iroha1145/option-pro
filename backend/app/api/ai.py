from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from datetime import date
from typing import Annotated, Literal, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from app.api.stocks import _sanitize
from app.services import ai_analysis

logger = logging.getLogger(__name__)

_MAX_AI_BODY_BYTES = 64 * 1024
_TRUST_PROXY_HEADERS = os.environ.get("TRUST_PROXY_HEADERS", "").strip().lower() in {"1", "true", "yes"}


class _BoundedBodyRoute(APIRoute):
    """Reject oversized AI bodies before Pydantic/model processing."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def bounded_handler(request: Request):
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    length = int(content_length)
                    if length < 0:
                        raise ValueError
                    if length > _MAX_AI_BODY_BYTES:
                        raise HTTPException(status_code=413, detail="AI request body exceeds 64 KiB")
                except ValueError as exc:
                    raise HTTPException(status_code=400, detail="Invalid Content-Length") from exc
            # Content-Length is optional (for example with chunked transfer), so
            # enforce the limit while reading instead of buffering an unbounded
            # body first. Starlette reuses ``request._body`` when the endpoint
            # later asks for JSON, keeping this compatible with normal parsing.
            chunks: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _MAX_AI_BODY_BYTES:
                    raise HTTPException(status_code=413, detail="AI request body exceeds 64 KiB")
                chunks.append(chunk)
            request._body = b"".join(chunks)
            return await original_handler(request)

        return bounded_handler


router = APIRouter(prefix="/api/ai", tags=["ai"], route_class=_BoundedBodyRoute)

Ticker = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=12,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9.\-^]*$",
    ),
]
ShortText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=160)]
Expiration = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=10, pattern=r"^(?:\d{4}-\d{2}-\d{2})?$"),
]


def _client_ip(request: Request) -> str:
    if _TRUST_PROXY_HEADERS:
        return (
            request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "unknown")
        )
    return request.client.host if request.client else "unknown"


def _fingerprint(request: Request) -> str:
    """Generate a fingerprint from client IP to distinguish different users."""
    return hashlib.sha256(_client_ip(request).encode()).hexdigest()[:12]


class AlertItem(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    strike: float = Field(ge=0, le=10_000_000)
    type: Literal["call", "put"]
    expiration: Expiration = ""
    dte: Optional[int] = Field(default=None, ge=0, le=3660)
    volume: int = Field(ge=0, le=2_000_000_000)
    open_interest: int = Field(default=0, ge=0, le=2_000_000_000)
    last_price: Optional[float] = Field(default=None, ge=0, le=10_000_000)
    implied_volatility: Optional[float] = Field(default=None, ge=0, le=100)
    premium_flow: Optional[float] = Field(default=None, ge=0, le=1_000_000_000_000_000)
    vol_oi_ratio: Optional[float] = Field(default=None, ge=0, le=2_000_000_000)
    reasons: list[ShortText] = Field(default_factory=list, max_length=8)
    signal: Optional[Literal["bullish", "bearish", "mixed", "unknown"]] = None
    inferred_direction: Optional[Literal["bullish", "bearish", "mixed", "unknown"]] = None
    direction_note: Annotated[str, StringConstraints(max_length=300)] = ""

    @field_validator("expiration")
    @classmethod
    def validate_expiration(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value


class AlertsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)

    ticker: Ticker
    alerts: list[AlertItem] = Field(default_factory=list, max_length=10)
    underlying_price: float = Field(default=0, ge=0, le=10_000_000)
    expiration: Expiration = ""

    @field_validator("ticker")
    @classmethod
    def normalize_ticker(cls, value: str) -> str:
        return value.upper()

    @field_validator("expiration")
    @classmethod
    def validate_expiration(cls, value: str) -> str:
        if value:
            date.fromisoformat(value)
        return value


@router.post("/analyze-alerts")
async def analyze_alerts(req: AlertsRequest, request: Request):
    try:
        fp = _fingerprint(request)
        result = await asyncio.to_thread(
            ai_analysis.analyze_option_alerts,
            req.ticker,
            [alert.model_dump(mode="json") for alert in req.alerts],
            req.underlying_price,
            req.expiration,
            fp,
        )
        return _sanitize(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI alert analysis endpoint failed")
        raise HTTPException(500, "AI analysis unavailable") from exc


@router.get("/earnings-correlation")
async def earnings_correlation(request: Request):
    try:
        fp = _fingerprint(request)
        from app.api.earnings import upcoming_earnings
        data = await upcoming_earnings()
        earnings = data.get("earnings", []) if isinstance(data, dict) else []
        result = await asyncio.to_thread(ai_analysis.analyze_earnings_correlation, earnings, fp)
        return _sanitize(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI earnings correlation endpoint failed")
        raise HTTPException(500, "AI analysis unavailable") from exc


@router.get("/earnings-impact/{ticker}")
async def earnings_impact(
    ticker: Annotated[Ticker, Path(description="US-listed ticker symbol")],
    request: Request,
):
    """Per-company earnings impact: which other companies will this report move?"""
    try:
        fp = _fingerprint(request)
        # Find the company's earnings info
        from app.api.earnings import upcoming_earnings
        data = await upcoming_earnings()
        earnings = data.get("earnings", []) if isinstance(data, dict) else []
        target = next((e for e in earnings if e.get("ticker", "").upper() == ticker.upper()), None)
        if not target:
            # Use bare ticker — AI can still reason about generic company
            target = {"ticker": ticker.upper(), "name": ticker.upper(),
                      "sector": "", "earnings_date": "", "eps_estimate": None}
        result = await asyncio.to_thread(
            ai_analysis.analyze_single_earnings_impact, target, fp
        )
        return _sanitize(result)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("AI earnings impact endpoint failed")
        raise HTTPException(500, "AI analysis unavailable") from exc
