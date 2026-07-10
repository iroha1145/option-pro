from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.strength.scanner import (
    PROFILES,
    TIMEFRAMES,
    UNIVERSES,
    market_strength,
    profiles,
    scan_strength,
    sector_strength,
    stock_strength,
)
from app.services.utils import sanitize

router = APIRouter(prefix="/api/strength", tags=["strength"])


@router.get("/scan")
async def scan(
    universe: str = Query("themes", pattern="^(themes)$"),
    timeframe: str = Query("all", pattern="^(short|mid|long|all)$"),
    profile: str = Query("balanced", pattern="^(conservative|balanced|aggressive)$"),
    top: int = Query(30, ge=5, le=120),
    sector_id: Optional[str] = Query(None),
    min_price: float = Query(5.0, ge=0),
    min_avg_dollar_volume: float = Query(10_000_000, ge=0),
    cache_ttl: int = Query(900, ge=300, le=900),
) -> dict[str, Any]:
    """Run the Strength Radar scan across the current theme universe.

    ``cache_ttl`` is seconds, clamped by FastAPI to 5-15 minutes.
    """
    if universe not in UNIVERSES or timeframe not in TIMEFRAMES or profile not in PROFILES:
        raise HTTPException(status_code=400, detail="Invalid screener parameters")
    try:
        payload = await scan_strength(
            universe=universe,
            timeframe=timeframe,
            profile=profile,
            top=top,
            sector_id=sector_id,
            min_price=min_price,
            min_avg_dollar_volume=min_avg_dollar_volume,
            ttl=cache_ttl,
        )
        return sanitize(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/stocks/{ticker}")
async def stock(ticker: str, profile: str = Query("balanced", pattern="^(conservative|balanced|aggressive)$")) -> dict[str, Any]:
    try:
        return sanitize(await stock_strength(ticker, profile=profile))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Ticker not found in theme universe: {ticker}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/sectors")
async def sectors(period: str = Query("3mo", pattern="^(1mo|3mo|6mo)$")) -> dict[str, Any]:
    try:
        return sanitize(await sector_strength(period=period))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/market")
async def market() -> dict[str, Any]:
    try:
        return sanitize(await market_strength())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/profiles")
async def list_profiles() -> dict[str, Any]:
    return sanitize(profiles())
