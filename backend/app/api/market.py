from __future__ import annotations

import asyncio
import math
from datetime import datetime, timezone

import yfinance as yf
from fastapi import APIRouter, HTTPException

from app.services.cache import cache as _shared_cache
from app.services.market_calendar import (
    ET,
    early_close_minutes as _early_close_minutes,
    is_trading_day as _is_trading_day,
    market_datetime as _market_datetime,
    market_holidays as _market_holidays,
    next_trading_day as _next_trading_day,
)

router = APIRouter(prefix="/api/market", tags=["market"])

# Symbols served by the lightweight /indices batch endpoint (ticker bar).
INDEX_SYMBOLS = ["^GSPC", "^IXIC", "^DJI", "^N225", "000001.SS"]


@router.get("/indices")
async def market_indices():
    """Batch quote endpoint for the frontend index ticker bar.

    One request returns all index quotes via fast_info — the old path made the
    frontend call /api/stocks/{ticker} five times, each triggering yfinance's
    slow full `.info` scrape.
    """
    return await _shared_cache.get_or_set("market:indices", 60, _build_indices)


async def _build_indices():
    def _one(symbol: str):
        try:
            fi = yf.Ticker(symbol).fast_info
            price = float(fi.last_price)
            if not math.isfinite(price) or price <= 0:
                raise ValueError("non-finite index price")
            try:
                previous_close = getattr(fi, "previous_close", None)
                prev = float(previous_close) if previous_close is not None else price
                if not math.isfinite(prev) or prev <= 0:
                    prev = price
            except Exception:
                # Some yfinance fast_info properties raise independently. A
                # missing previous close should not discard a valid live price.
                prev = price
            return {
                "symbol": symbol,
                "price": round(price, 2),
                "change_percent": round((price - prev) / prev * 100, 2) if prev else 0,
            }
        except Exception:
            return {"symbol": symbol, "price": None, "change_percent": None}

    results = await asyncio.gather(*[asyncio.to_thread(_one, s) for s in INDEX_SYMBOLS])
    succeeded = sum(result.get("price") is not None for result in results)
    if succeeded == 0:
        raise HTTPException(status_code=503, detail="Yahoo index data is currently unavailable")
    return {
        "indices": list(results),
        "attempted": len(INDEX_SYMBOLS),
        "succeeded": succeeded,
        "data_limited": succeeded < len(INDEX_SYMBOLS),
        "source_status": "active" if succeeded == len(INDEX_SYMBOLS) else "degraded",
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/status")
async def market_status():
    """Determine US market status from current time (no external API needed)."""
    def _compute():
        et = datetime.now(ET)
        today = et.date()
        weekday = et.weekday()  # 0=Mon, 6=Sun
        hour, minute = et.hour, et.minute
        t = hour * 60 + minute
        holiday = _market_holidays(today.year).get(today)
        early_close = _early_close_minutes(today)

        if weekday >= 5:
            market = "closed"
            phase = "weekend"
        elif holiday:
            market = "closed"
            phase = "holiday"
        elif t < 4 * 60:
            market = "closed"
            phase = "overnight"
        elif t < 9 * 60 + 30:
            market = "pre-market"
            phase = "pre-market"
        elif early_close and t >= early_close:
            if t < 20 * 60:
                market = "after-hours"
                phase = "after-hours"
            else:
                market = "closed"
                phase = "overnight"
        elif t < 16 * 60:
            market = "open"
            phase = "regular"
        elif t < 20 * 60:
            market = "after-hours"
            phase = "after-hours"
        else:
            market = "closed"
            phase = "overnight"

        next_open: datetime | None = None
        next_close: datetime | None = None
        if market == "open":
            next_close = _market_datetime(today, early_close or 16 * 60)
        elif phase in {"pre-market", "overnight"} and _is_trading_day(today) and t < 9 * 60 + 30:
            next_open = _market_datetime(today, 9 * 60 + 30)
        else:
            next_day = _next_trading_day(today)
            next_open = _market_datetime(next_day, 9 * 60 + 30)

        return {
            "market": market,
            "phase": phase,
            "holiday": holiday,
            "early_close": bool(early_close),
            "next_open": next_open.isoformat() if next_open else None,
            "next_close": next_close.isoformat() if next_close else None,
            "server_time": et.isoformat(),
            "exchanges": {
                "nasdaq": market if market in ("open", "closed") else "extended",
                "nyse": market if market in ("open", "closed") else "extended",
            },
        }

    return await asyncio.to_thread(_compute)
