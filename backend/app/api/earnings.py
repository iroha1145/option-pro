from __future__ import annotations

import asyncio
from datetime import date, datetime
import math
from typing import Any
from zoneinfo import ZoneInfo

import yfinance as yf
from fastapi import APIRouter, HTTPException

from app.services.cache import cache

router = APIRouter(prefix="/api/earnings", tags=["earnings"])

EARNINGS_TICKERS = [
    # Magnificent 7
    "NVDA", "TSLA", "AAPL", "AMZN", "META", "MSFT", "GOOGL",
    # Semiconductors
    "AMD", "AVGO", "TSM", "ASML", "MU", "INTC", "ARM", "QCOM", "MRVL", "AMAT", "LRCX", "KLAC",
    # Software / Cloud
    "CRM", "ORCL", "ADBE", "NOW", "SNOW", "PLTR", "NET", "PANW", "CRWD",
    # Consumer / Media
    "NFLX", "DIS", "BABA", "COST", "WMT", "TGT", "NKE", "SBUX", "MCD",
    # Finance
    "JPM", "GS", "MS", "V", "MA", "BAC", "C", "BLK",
    # Biotech / Pharma
    "LLY", "NVO", "ABBV", "AMGN", "GILD", "MRNA", "PFE", "JNJ", "UNH",
    # Energy
    "XOM", "CVX", "COP", "SLB",
    # Industrials / Others
    "BA", "CAT", "DE", "UPS", "FDX",
    # Chinese ADRs
    "PDD", "JD", "BIDU", "NIO", "LI", "XPEV",
]


from app.services.utils import sanitize as _sanitize

MARKET_TZ = ZoneInfo("America/New_York")
MAX_EARNINGS_LOOKAHEAD_DAYS = 180


def _to_optional_float(value: Any) -> float | None:
    try:
        f = float(value)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return None


def _first(value: Any) -> Any:
    """Return the first item from list-like calendar fields; pass scalars through."""
    if value is None:
        return None
    try:
        if hasattr(value, "iloc"):
            return value.iloc[0] if len(value) else None
        if isinstance(value, (list, tuple)):
            return value[0] if value else None
    except Exception:
        return None
    return value


def _market_today() -> date:
    return datetime.now(MARKET_TZ).date()


def _coerce_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(MARKET_TZ)
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if value > 1_000_000:
                return datetime.fromtimestamp(value, MARKET_TZ).date()
        except Exception:
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw or raw.lower() in {"nan", "nat", "none", "null", "-"}:
            return None
        if raw.isdigit():
            return _coerce_date(int(raw))
        try:
            return date.fromisoformat(raw[:10])
        except Exception:
            return None
    return None


def _collect_dates(value: Any) -> list[date]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        dates: list[date] = []
        for item in value:
            dates.extend(_collect_dates(item))
        return dates
    if hasattr(value, "tolist") and not isinstance(value, (str, bytes)):
        try:
            return _collect_dates(value.tolist())
        except Exception:
            pass
    parsed = _coerce_date(value)
    return [parsed] if parsed else []


def _calendar_get(calendar: Any, key: str) -> Any:
    if calendar is None:
        return None
    try:
        if hasattr(calendar, "get"):
            value = calendar.get(key)
            if value is not None:
                return value
    except Exception:
        pass
    try:
        if hasattr(calendar, "loc"):
            return calendar.loc[key]
    except Exception:
        return None
    return None


def _earnings_dates_from_table(ticker_obj: yf.Ticker) -> list[date]:
    table = None
    try:
        if hasattr(ticker_obj, "get_earnings_dates"):
            table = ticker_obj.get_earnings_dates(limit=12)
    except Exception:
        table = None
    if table is None:
        try:
            table = ticker_obj.earnings_dates
        except Exception:
            table = None
    if table is None:
        return []
    try:
        return _collect_dates(list(table.index))
    except Exception:
        return []


def _next_future_date(candidates: list[date], today: date) -> date | None:
    unique = sorted(set(candidates))
    for candidate in unique:
        days = (candidate - today).days
        if 0 <= days <= MAX_EARNINGS_LOOKAHEAD_DAYS:
            return candidate
    return None


@router.get("/upcoming")
async def upcoming_earnings():
    """Fetch real upcoming earnings dates from Yahoo Finance.

    Uses the locked cache helper so concurrent cold-cache requests share ONE
    fetch instead of each firing ~67 tickers worth of yfinance calls
    (thundering herd).
    """
    today = _market_today()
    key = f"earnings:upcoming:{today.isoformat()}"
    return await cache.get_or_set(key, 3600, lambda: _build_upcoming_earnings(today))


async def _build_upcoming_earnings(today: date):
    sem = asyncio.Semaphore(8)

    async def fetch_one(ticker: str):
        def _work():
            try:
                tk = yf.Ticker(ticker)
                try:
                    cal = tk.calendar
                except Exception:
                    cal = None
                calendar_dates = _collect_dates(_calendar_get(cal, "Earnings Date"))
                next_date = _next_future_date(calendar_dates, today)
                table_dates: list[date] = []
                if next_date is None:
                    # The dates table is a slower fallback. Avoid it when the
                    # lightweight calendar already has a usable future date.
                    table_dates = _earnings_dates_from_table(tk)
                    next_date = _next_future_date(table_dates, today)

                source_observed = bool(calendar_dates or table_dates)
                if next_date is None:
                    return {
                        "ticker": ticker,
                        "ok": source_observed,
                        "source_observed": source_observed,
                        "data": None,
                    }
                earnings_date = next_date.isoformat()

                # Full quote-summary info is expensive. Fetch it only after a
                # ticker has been confirmed as an upcoming earnings match.
                try:
                    info_value = tk.info
                    info = info_value if isinstance(info_value, dict) else {}
                except Exception:
                    info = {}
                name = info.get("shortName", ticker)

                return {"ticker": ticker, "ok": True, "source_observed": True, "data": {
                    "ticker": ticker,
                    "name": name,
                    "earnings_date": earnings_date,
                    "days_until": (next_date - today).days,
                    "eps_estimate": _to_optional_float(_first(_calendar_get(cal, "Earnings Average"))),
                    "eps_high": _to_optional_float(_first(_calendar_get(cal, "Earnings High"))),
                    "eps_low": _to_optional_float(_first(_calendar_get(cal, "Earnings Low"))),
                    "revenue_estimate": _to_optional_float(_first(_calendar_get(cal, "Revenue Average"))),
                    "market_cap": _to_optional_float(info.get("marketCap")),
                    "sector": info.get("sector", ""),
                }}
            except Exception:
                return {"ticker": ticker, "ok": False, "source_observed": False, "data": None}

        async with sem:
            return await asyncio.to_thread(_work)

    results = await asyncio.gather(*[fetch_one(t) for t in EARNINGS_TICKERS], return_exceptions=True)
    completed = [r for r in results if isinstance(r, dict)]
    succeeded = [r for r in completed if r.get("ok")]
    failed_symbols = [
        ticker
        for ticker, result in zip(EARNINGS_TICKERS, results)
        if not isinstance(result, dict) or not result.get("ok")
    ]
    if not succeeded:
        raise HTTPException(status_code=503, detail="Yahoo earnings data is currently unavailable")
    earnings = [r["data"] for r in succeeded if isinstance(r.get("data"), dict)]
    earnings.sort(key=lambda x: x.get("earnings_date", "9999"))
    return _sanitize({
        "earnings": earnings,
        "attempted": len(EARNINGS_TICKERS),
        "succeeded": len(succeeded),
        "failed_symbols": failed_symbols,
        "data_limited": bool(failed_symbols),
        "source_status": "active" if not failed_symbols else "degraded",
        "as_of": datetime.now(MARKET_TZ).isoformat(),
    })
