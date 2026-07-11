from __future__ import annotations

import asyncio
import math
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, HTTPException

from app.services.cache import cache as _shared_cache

router = APIRouter(prefix="/api/market", tags=["market"])

ET = ZoneInfo("America/New_York")

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


def _observed(d: date) -> date:
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + (n - 1) * 7)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    # Anonymous Gregorian algorithm.
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _market_holidays(year: int) -> dict[date, str]:
    holidays = {
        _observed(date(year, 1, 1)): "new_year",
        _nth_weekday(year, 1, 0, 3): "martin_luther_king_jr_day",
        _nth_weekday(year, 2, 0, 3): "presidents_day",
        _easter(year) - timedelta(days=2): "good_friday",
        _last_weekday(year, 5, 0): "memorial_day",
        _observed(date(year, 6, 19)): "juneteenth",
        _observed(date(year, 7, 4)): "independence_day",
        _nth_weekday(year, 9, 0, 1): "labor_day",
        _nth_weekday(year, 11, 3, 4): "thanksgiving",
        _observed(date(year, 12, 25)): "christmas",
    }
    # Observed New Year's Day can fall in the previous year.
    holidays[_observed(date(year + 1, 1, 1))] = "new_year"
    return holidays


def _early_close_minutes(d: date) -> int | None:
    thanksgiving = _nth_weekday(d.year, 11, 3, 4)
    early_dates = {
        thanksgiving + timedelta(days=1),
        date(d.year, 12, 24),
    }
    # If July 4 is a weekday, July 3 is commonly an early close unless it is a weekend.
    july3 = date(d.year, 7, 3)
    if july3.weekday() < 5:
        early_dates.add(july3)
    return 13 * 60 if d in early_dates and d.weekday() < 5 else None


def _is_trading_day(d: date) -> bool:
    return d.weekday() < 5 and d not in _market_holidays(d.year)


def _next_trading_day(start: date, *, include_start: bool = False) -> date:
    candidate = start if include_start else start + timedelta(days=1)
    # Eight calendar days always cover at least one ordinary exchange session;
    # keep a larger defensive bound for unusual adjacent holidays.
    for _ in range(15):
        if _is_trading_day(candidate):
            return candidate
        candidate += timedelta(days=1)
    raise RuntimeError("Unable to determine the next US trading day")


def _market_datetime(d: date, minutes: int) -> datetime:
    return datetime.combine(
        d,
        datetime_time(hour=minutes // 60, minute=minutes % 60),
        tzinfo=ET,
    )


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
