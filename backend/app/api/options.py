from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import math
import time
from typing import Literal

import yfinance as yf
from fastapi import APIRouter, HTTPException, Query

from app.access import current_request_is_owner, public_snapshot_unavailable
from app.services import yahoo
from app.services.cache import cache
from app.services.utils import sanitize
from app.public_home_snapshot import (
    read_owner_public_home_entry_async,
    read_public_home_resource_async,
)
from app.personal_config import get_personal_config

router = APIRouter(prefix="/api/options", tags=["options"])
POPULAR_TICKERS = ["NVDA", "TSLA", "AAPL", "AMD", "AMZN", "META", "MSFT", "SPY", "QQQ", "GOOGL"]

_UNUSUAL_TTL = 120  # seconds; the scan costs ~30 Yahoo calls, never run it per request
_UNUSUAL_FAILURE_TTL = 30
_UNUSUAL_FAILURE_MAX_KEYS = 128
_unusual_failure_deadlines: dict[str, float] = {}


def _unusual_key(option_type: str, min_vol_oi: float) -> str:
    return f"unusual:{option_type}:{min_vol_oi}"


def _finite(value, *, minimum: float | None = None) -> float | None:
    number = yahoo._safe_float(value)
    if number is None or (minimum is not None and number < minimum):
        return None
    return number


def _moneyness(side: str, strike: float, underlying_price: float | None) -> str:
    if underlying_price is None or underlying_price <= 0:
        return "unavailable"
    if strike == underlying_price:
        return "atm"
    if side == "call":
        return "otm" if strike > underlying_price else "itm"
    return "otm" if strike < underlying_price else "itm"


def _in_the_money(
    side: str,
    strike: float,
    underlying_price: float | None,
    provider_value,
) -> bool | None:
    if underlying_price is not None and underlying_price > 0:
        if side == "call":
            return strike < underlying_price
        return strike > underlying_price
    if isinstance(provider_value, bool):
        return provider_value
    if type(provider_value).__name__ == "bool_":
        return bool(provider_value)
    return None


def _failure_cooldown(key: str) -> int:
    now = time.monotonic()
    for expired in [
        item_key
        for item_key, deadline in _unusual_failure_deadlines.items()
        if deadline <= now
    ]:
        _unusual_failure_deadlines.pop(expired, None)
    deadline = _unusual_failure_deadlines.get(key)
    return max(0, math.ceil(deadline - now)) if deadline is not None else 0


def _record_failure(key: str) -> None:
    now = time.monotonic()
    _unusual_failure_deadlines[key] = now + _UNUSUAL_FAILURE_TTL
    if len(_unusual_failure_deadlines) > _UNUSUAL_FAILURE_MAX_KEYS:
        oldest = min(_unusual_failure_deadlines, key=_unusual_failure_deadlines.get)
        _unusual_failure_deadlines.pop(oldest, None)


def _cooldown_error(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Yahoo options data is temporarily cooling down",
        headers={"Retry-After": str(retry_after)},
    )


@router.get("/unusual")
async def unusual_activity(
    type: Literal["all", "call", "put"] = "all",
    min_vol_oi: float = Query(1.0, ge=0),
):
    """Scan popular tickers for unusual options activity, parallel per ticker.

    Cached (with a per-key lock) — previously every request re-scanned
    10 tickers x (expirations + 2 chains) against Yahoo with no cache at all.
    """
    key = _unusual_key(type, min_vol_oi)
    owner = current_request_is_owner()
    if not owner:
        cached = cache.get(key)
        if cached is None:
            now = time.time()
            cached = await read_public_home_resource_async(
                "unusual",
                parameters={"type": type, "min_vol_oi": float(min_vol_oi)},
                now=now,
            )
        if cached is None:
            raise public_snapshot_unavailable(key)
        return cached
    cached = cache.get(key)
    if cached is not None:
        return cached
    config = get_personal_config()
    if (
        config.access.mode == "password"
        and type == "all"
        and float(min_vol_oi) == 1.0
    ):
        now = time.time()
        interval = float(config.public_home.unusual_seconds)
        disk_entry = await read_owner_public_home_entry_async(
            "unusual",
            parameters={"type": "all", "min_vol_oi": 1.0},
            fresh_for_seconds=interval,
            now=now,
        )
        if disk_entry is not None:
            remaining = max(
                1,
                int(float(disk_entry["saved_at"]) + interval - now),
            )
            return cache.set(key, disk_entry["payload"], remaining)
    retry_after = _failure_cooldown(key)
    if retry_after > 0:
        raise _cooldown_error(retry_after)

    async def produce():
        # Waiting callers re-check after the cache lock is acquired.  When the
        # leader fails, followers therefore reuse the short negative result
        # instead of each launching the same expensive provider scan.
        locked_retry_after = _failure_cooldown(key)
        if locked_retry_after > 0:
            raise _cooldown_error(locked_retry_after)
        try:
            return await _unusual_activity_impl(type, min_vol_oi)
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            _record_failure(key)
            raise HTTPException(
                status_code=503,
                detail=exc.detail,
                headers={"Retry-After": str(_UNUSUAL_FAILURE_TTL)},
            ) from exc

    payload = await cache.get_or_set(key, _UNUSUAL_TTL, produce)
    _unusual_failure_deadlines.pop(key, None)
    return payload


async def _unusual_activity_impl(type: str, min_vol_oi: float):
    def _scan_one(symbol: str):
        """Sync work for a single ticker — runs in its own thread."""
        rows = []
        try:
            t = yf.Ticker(symbol)
            exps = list(t.options[:2])
            if not exps:
                return {"symbol": symbol, "ok": False, "rows": [], "reason": "no_expirations"}
            try:
                price = _finite(t.fast_info.last_price, minimum=0.0)
                if price is not None and price <= 0:
                    price = None
            except Exception:
                price = None
            usable_chains = 0
            chain_failures = 0
            for exp in exps:
                try:
                    chain = t.option_chain(exp)
                except Exception:
                    chain_failures += 1
                    continue
                if chain.calls.empty and chain.puts.empty:
                    chain_failures += 1
                    continue
                usable_chains += 1
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
                        strike = _finite(row.get("strike"), minimum=0.0)
                        if strike is None or strike <= 0:
                            continue
                        lp = _finite(row.get("lastPrice"), minimum=0.0)
                        iv = _finite(row.get("impliedVolatility"), minimum=0.0)
                        premium = (
                            _finite(lp * vol * 100, minimum=0.0)
                            if lp is not None
                            else None
                        )
                        rows.append({
                            "ticker": symbol,
                            "contract_ticker": row.get("contractSymbol", ""),
                            "contract_type": side,
                            "type": side,
                            "strike": strike,
                            "expiration": exp,
                            "volume": vol,
                            "open_interest": oi,
                            "oi": oi,
                            "vol_oi_ratio": round(ratio, 2),
                            "vol_oi": round(ratio, 2),
                            "premium": round(premium, 2) if premium is not None else None,
                            "last_price": lp,
                            "implied_volatility": iv,
                            "underlying_price": price,
                            "in_the_money": _in_the_money(
                                side,
                                strike,
                                price,
                                row.get("inTheMoney"),
                            ),
                            "moneyness": _moneyness(side, strike, price),
                            "direction": None,
                            "direction_confidence": 0,
                            "direction_status": "unavailable_without_trade_side",
                            # Compatibility fields no longer pretend that the
                            # contract type reveals buyer/seller direction.
                            "signal": "unknown",
                            "inferred_direction": "unknown",
                            "direction_deprecated": True,
                        })
        except Exception:
            return {"symbol": symbol, "ok": False, "rows": []}
        if usable_chains == 0:
            return {"symbol": symbol, "ok": False, "rows": [], "reason": "empty_chains"}
        return {
            "symbol": symbol,
            "ok": True,
            "rows": rows,
            "limited": chain_failures > 0,
        }

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
    partial_symbols = []
    for symbol, result in zip(POPULAR_TICKERS, per_ticker):
        if isinstance(result, dict) and result.get("ok"):
            succeeded += 1
            results.extend(result.get("rows") or [])
            if result.get("limited"):
                partial_symbols.append(symbol)
        else:
            failed_symbols.append(symbol)
    if succeeded == 0:
        raise HTTPException(
            status_code=503,
            detail="Yahoo options data is currently unavailable",
        )
    results.sort(key=lambda r: (r["vol_oi_ratio"], r.get("premium") or 0), reverse=True)
    return sanitize({
        "results": results[:50],
        "data_limited": bool(failed_symbols or partial_symbols),
        "source_status": (
            "active" if not failed_symbols and not partial_symbols else "degraded"
        ),
        "attempted": len(POPULAR_TICKERS),
        "succeeded": succeeded,
        "failed_symbols": failed_symbols,
        "partial_symbols": partial_symbols,
        "as_of": datetime.now(timezone.utc).isoformat(),
    })


@router.get("/{ticker}/expirations")
async def expirations(ticker: str):
    if not current_request_is_owner():
        cached = yahoo.get_cached_expirations_snapshot(ticker)
        if cached is None:
            raise public_snapshot_unavailable(f"options:expirations:{ticker.upper()}")
        return {"ticker": ticker.upper(), **cached}
    try:
        snapshot = await asyncio.to_thread(yahoo.get_expirations_snapshot, ticker)
        return {"ticker": ticker.upper(), **snapshot}
    except Exception as e:
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable") from e


@router.get("/{ticker}/chain")
async def option_chain(ticker: str, expiration: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")):
    if not current_request_is_owner():
        cached = yahoo.get_cached_option_chain(ticker, expiration)
        if cached is None:
            raise public_snapshot_unavailable(
                f"options:chain:{ticker.upper()}:{expiration}"
            )
        from app.api.stocks import _sanitize
        return _sanitize(cached)
    try:
        from app.api.stocks import _sanitize
        return _sanitize(await asyncio.to_thread(yahoo.get_option_chain, ticker, expiration))
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid option expiration") from e
    except Exception as e:
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable") from e
