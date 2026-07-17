from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException

from app.access import current_request_is_owner, public_snapshot_unavailable
from app.services import yahoo
from app.services.sectors import SECTORS
from app.services.zh_names import get_zh_name

router = APIRouter(prefix="/api/sectors", tags=["sectors"])

# Simple TTL cache shared by sector endpoints (10 min — IV ranks change slowly).
# Per-key locks prevent thundering herd: without them, concurrent cold-cache
# requests would each kick off a full sector scan.
_cache: dict[str, tuple[float, float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_MAX_STALE_SECONDS = 60 * 60


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _with_cache_status(value: Any, *, fetched_at: float, cache_stale: bool) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    stale = cache_stale or bool(result.get("_stale"))
    result["_stale"] = stale
    result.setdefault("as_of", datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())
    if cache_stale:
        result["source_status"] = "stale"
        result["stale_age_seconds"] = round(max(time.time() - fetched_at, 0.0), 1)
    else:
        result.setdefault("source_status", "active")
    return result


async def _cached(
    key: str,
    ttl: int,
    loader,
    *,
    max_stale_seconds: int = _MAX_STALE_SECONDS,
    allow_refresh: bool = True,
):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=False)
    if not allow_refresh:
        if hit and now - hit[1] <= max(0, max_stale_seconds):
            return _with_cache_status(
                hit[2],
                fetched_at=hit[1],
                cache_stale=True,
            )
        raise public_snapshot_unavailable(key)
    async with _lock_for(key):
        now = time.time()
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=False)
        try:
            value = await loader()
        except Exception:
            if hit:
                stale_age = now - hit[1]
                if stale_age <= max(0, max_stale_seconds):
                    return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=True)
                _cache.pop(key, None)
            raise
        if isinstance(value, dict) and value.get("source_status") == "insufficient_data":
            if hit:
                stale_age = now - hit[1]
                if stale_age <= max(0, max_stale_seconds):
                    return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=True)
                _cache.pop(key, None)
                raise RuntimeError(f"Sector data source unavailable and stale cache exceeded {max_stale_seconds}s")
            return _with_cache_status(value, fetched_at=now, cache_stale=False)
        fetched_at = time.time()
        _cache[key] = (fetched_at + ttl, fetched_at, value)
        return _with_cache_status(value, fetched_at=fetched_at, cache_stale=False)


def ensure_sector(sector_id: str) -> None:
    if sector_id not in SECTORS:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_id}")


@router.get("")
async def list_sectors():
    return {"sectors": [{"id": id_, "name": data["name"], "tickers": data["tickers"]} for id_, data in SECTORS.items()]}


async def _sector_iv_rows(sector_id: str) -> list[dict[str, Any]]:
    """Fetch price + ATM IV for every sector ticker IN PARALLEL.

    The old implementation looped tickers sequentially in one thread and even
    called the very slow yfinance `.info` scrape just for a display name —
    a 14-ticker sector took 30-60s cold. Names now come from the local
    zh_names dictionary, and per-ticker work runs in a bounded thread pool.
    """
    sector = SECTORS[sector_id]
    sem = asyncio.Semaphore(8)

    def _one(ticker: str) -> dict[str, Any] | None:
        try:
            snapshot = yahoo.get_stock_iv_snapshot(ticker)
            iv = snapshot.get("atm_iv")
            if iv is None:
                return {"ticker": ticker, "iv": None, "source_status": "insufficient_data"}
            price = yahoo.get_last_price(ticker)
            return {
                "ticker": ticker,
                "name": get_zh_name(ticker) or ticker,
                "price": round(price, 2) if price is not None else None,
                "iv": iv,
                "_stale": bool(snapshot.get("_stale")),
                "as_of": snapshot.get("as_of"),
                "source_status": snapshot.get("source_status") or "active",
            }
        except Exception:
            return {"ticker": ticker, "iv": None, "source_status": "error"}

    async def bounded(ticker: str):
        async with sem:
            return await asyncio.to_thread(_one, ticker)

    results = await asyncio.gather(*[bounded(t) for t in sector["tickers"]], return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


async def _iv_ranking_payload(sector_id: str) -> dict:
    sector = SECTORS[sector_id]
    rows = await _sector_iv_rows(sector_id)
    valid_rows = [row for row in rows if row.get("iv") is not None]
    iv_values = [float(row["iv"]) for row in valid_rows]

    rankings = []
    for row in valid_rows:
        iv = float(row["iv"])
        if len(iv_values) == 1:
            sector_rank = 50.0
        else:
            below = sum(1 for candidate in iv_values if candidate < iv)
            tied = sum(1 for candidate in iv_values if candidate == iv)
            sector_rank = (below + (tied - 1) / 2) / (len(iv_values) - 1) * 100
        atm_iv_percent = round(iv * 100, 1)
        rankings.append({
            "ticker": row["ticker"],
            "name": row.get("name") or row["ticker"],
            "price": row.get("price"),
            "atm_iv_percent": atm_iv_percent,
            "sector_iv_rank": round(sector_rank, 1),
            "iv_rank": None,
            "iv_percentile": None,
            # Deprecated aliases retain their true unit: current absolute IV%.
            "iv_pct": atm_iv_percent,
            "iv_current": atm_iv_percent,
            "iv_change_30d": None,
            "_stale": bool(row.get("_stale")),
            "as_of": row.get("as_of"),
        })
    rankings.sort(
        key=lambda ranking: (ranking["sector_iv_rank"], ranking["atm_iv_percent"]),
        reverse=True,
    )

    total = len(sector["tickers"])
    failed_symbols = [row.get("ticker") for row in rows if row.get("iv") is None and row.get("ticker")]
    stale = any(bool(row.get("_stale")) for row in valid_rows)
    source_status = "insufficient_data" if not rankings else ("stale" if stale else ("degraded" if len(rankings) < total else "active"))
    as_of_values = [str(row.get("as_of")) for row in valid_rows if row.get("as_of")]
    return {
        "sector_id": sector_id,
        "sector_name": sector["name"],
        "rankings": rankings,
        "data_limited": len(rankings) < total,
        "source_status": source_status,
        "_stale": stale,
        "as_of": min(as_of_values) if as_of_values else datetime.now(timezone.utc).isoformat(),
        "success_count": len(rankings),
        "requested_count": total,
        "success_rate": round(len(rankings) / total * 100, 1) if total else 0.0,
        "failed_symbols": failed_symbols,
    }


@router.get("/{sector_id}/iv-ranking")
async def iv_ranking(sector_id: str):
    ensure_sector(sector_id)
    try:
        return await _cached(
            f"iv:{sector_id}",
            600,
            lambda: _iv_ranking_payload(sector_id),
            allow_refresh=current_request_is_owner(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Sector options data is currently unavailable") from exc


@router.get("/{sector_id}/heatmap")
async def heatmap(sector_id: str):
    ensure_sector(sector_id)
    # Reuse the iv-ranking cache — the heatmap is a projection of the same
    # data, so visiting both views costs one scan instead of two.
    try:
        payload = await _cached(
            f"iv:{sector_id}",
            600,
            lambda: _iv_ranking_payload(sector_id),
            allow_refresh=current_request_is_owner(),
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Sector options data is currently unavailable") from exc
    data = [
        {
            "ticker": item["ticker"],
            "atm_iv_percent": item["atm_iv_percent"],
            "sector_iv_rank": item["sector_iv_rank"],
            "iv_percentile": None,
        }
        for item in payload.get("rankings", [])
    ]
    return {
        "sector_id": sector_id,
        "sector_name": payload.get("sector_name", SECTORS[sector_id]["name"]),
        "data": data,
        "rankings": data,
        "data_limited": payload.get("data_limited", False),
        "source_status": payload.get("source_status"),
        "_stale": payload.get("_stale", False),
        "as_of": payload.get("as_of"),
        "success_count": payload.get("success_count", len(data)),
        "requested_count": payload.get("requested_count", len(SECTORS[sector_id]["tickers"])),
        "failed_symbols": payload.get("failed_symbols", []),
    }
