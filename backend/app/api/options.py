from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Literal

import yfinance as yf
from fastapi import APIRouter, HTTPException, Query

from app.services import yahoo
from app.services.cache import cache

router = APIRouter(prefix="/api/options", tags=["options"])
POPULAR_TICKERS = ["NVDA", "TSLA", "AAPL", "AMD", "AMZN", "META", "MSFT", "SPY", "QQQ", "GOOGL"]

_UNUSUAL_TTL = 120  # seconds; the scan costs ~30 Yahoo calls, never run it per request


@router.get("/unusual")
async def unusual_activity(
    type: Literal["all", "call", "put"] = "all",
    min_vol_oi: float = Query(1.0, ge=0),
):
    """Scan popular tickers for unusual options activity, parallel per ticker.

    Cached (with a per-key lock) — previously every request re-scanned
    10 tickers x (expirations + 2 chains) against Yahoo with no cache at all.
    """
    return await cache.get_or_set(
        f"unusual:{type}:{min_vol_oi}",
        _UNUSUAL_TTL,
        lambda: _unusual_activity_impl(type, min_vol_oi),
    )


async def _unusual_activity_impl(type: str, min_vol_oi: float):
    def _scan_one(symbol: str):
        """Sync work for a single ticker — runs in its own thread."""
        rows = []
        try:
            t = yf.Ticker(symbol)
            exps = t.options[:2]
            try:
                price = yahoo._safe_float(t.fast_info.last_price)
            except Exception:
                price = None
            for exp in exps:
                chain = t.option_chain(exp)
                for side, df in [("call", chain.calls), ("put", chain.puts)]:
                    if type != "all" and type != side:
                        continue
                    for _, row in df.iterrows():
                        vol = yahoo._safe_int(row.get("volume")) or 0
                        oi = yahoo._safe_int(row.get("openInterest")) or 0
                        if oi <= 0 or vol <= 0:
                            continue
                        ratio = vol / oi
                        if ratio < min_vol_oi:
                            continue
                        lp = yahoo._safe_float(row.get("lastPrice")) or 0
                        rows.append({
                            "ticker": symbol,
                            "contract_ticker": row.get("contractSymbol", ""),
                            "contract_type": side,
                            "type": side,
                            "strike": float(row["strike"]),
                            "expiration": exp,
                            "volume": vol,
                            "open_interest": oi,
                            "oi": oi,
                            "vol_oi_ratio": round(ratio, 2),
                            "vol_oi": round(ratio, 2),
                            "premium": round(lp * vol * 100, 2) if lp else None,
                            "last_price": lp,
                            "implied_volatility": yahoo._safe_float(row.get("impliedVolatility")),
                            "underlying_price": price,
                            "in_the_money": bool(row.get("inTheMoney", False)),
                        })
        except Exception:
            return {"symbol": symbol, "ok": False, "rows": []}
        return {"symbol": symbol, "ok": True, "rows": rows}

    # 5 in flight at a time — fast but doesn't trip Yahoo's rate limiter
    sem = asyncio.Semaphore(5)
    async def _bounded(s):
        async with sem:
            return await asyncio.to_thread(_scan_one, s)
    per_ticker = await asyncio.gather(
        *[_bounded(s) for s in POPULAR_TICKERS],
        return_exceptions=True,
    )
    results = []
    succeeded = 0
    failed_symbols = []
    for symbol, result in zip(POPULAR_TICKERS, per_ticker):
        if isinstance(result, dict) and result.get("ok"):
            succeeded += 1
            results.extend(result.get("rows") or [])
        else:
            failed_symbols.append(symbol)
    if succeeded == 0:
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable")
    results.sort(key=lambda r: (r["vol_oi_ratio"], r.get("premium") or 0), reverse=True)
    return {
        "results": results[:50],
        "data_limited": bool(failed_symbols),
        "source_status": "active" if not failed_symbols else "degraded",
        "attempted": len(POPULAR_TICKERS),
        "succeeded": succeeded,
        "failed_symbols": failed_symbols,
        "as_of": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/{ticker}/expirations")
async def expirations(ticker: str):
    try:
        snapshot = await asyncio.to_thread(yahoo.get_expirations_snapshot, ticker)
        return {"ticker": ticker.upper(), **snapshot}
    except Exception as e:
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable") from e


@router.get("/{ticker}/chain")
async def option_chain(ticker: str, expiration: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    try:
        from app.api.stocks import _sanitize
        return _sanitize(await asyncio.to_thread(yahoo.get_option_chain, ticker, expiration))
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid option expiration") from e
    except Exception as e:
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable") from e
