from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
import math
import time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx
import yfinance as yf
from fastapi import APIRouter, HTTPException, Query, Response


router = APIRouter(prefix="/api/stocks", tags=["stocks"])


# ──────────────────────────────────────────────────────────────────────────────
# Server-side TTL cache
# Backstop against Yahoo rate-limiting + much faster page loads.
# Returns stale on errors for a bounded period so a flaky API does not nuke the
# UI without allowing old financial data to masquerade as live indefinitely.
# ──────────────────────────────────────────────────────────────────────────────


@dataclass
class _EndpointCacheEntry:
    expires_at: float
    stale_until: float
    fetched_at: float
    value: Any


_endpoint_cache: dict[str, _EndpointCacheEntry] = {}
# Per-key lock prevents thundering herd: concurrent requests for the same
# cold key would otherwise all kick off their own yfinance fetch.
_endpoint_locks: dict[str, asyncio.Lock] = {}
_ENDPOINT_PURGE_THRESHOLD = 2048

def _lock_for(key: str) -> asyncio.Lock:
    lock = _endpoint_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _endpoint_locks[key] = lock
    return lock

def _maybe_purge_endpoint_cache(now: float) -> None:
    """Bound memory: per-ticker keys (stock:/chart:/logo:) accumulate forever
    in a long-lived process otherwise."""
    if len(_endpoint_cache) < _ENDPOINT_PURGE_THRESHOLD and len(_endpoint_locks) < _ENDPOINT_PURGE_THRESHOLD:
        return
    for key in [k for k, entry in _endpoint_cache.items() if entry.stale_until <= now]:
        _endpoint_cache.pop(key, None)
    for key in [k for k, lock in _endpoint_locks.items() if k not in _endpoint_cache and not lock.locked()]:
        _endpoint_locks.pop(key, None)

def _cache_result(entry: _EndpointCacheEntry, *, stale: bool) -> Any:
    value = entry.value
    if not isinstance(value, dict) or isinstance(value.get("content"), (bytes, bytearray)):
        return value
    result = dict(value)
    is_stale = stale or bool(result.get("_stale"))
    result["_stale"] = is_stale
    result.setdefault(
        "as_of",
        datetime.fromtimestamp(entry.fetched_at, timezone.utc).isoformat(),
    )
    if is_stale:
        result["source_status"] = "degraded"
        result.setdefault("stale_reason", "upstream_refresh_failed")
    else:
        result.setdefault("source_status", "active")
    return result


def _usable_hit(key: str, now: float) -> _EndpointCacheEntry | None:
    hit = _endpoint_cache.get(key)
    if hit and hit.stale_until <= now:
        _endpoint_cache.pop(key, None)
        return None
    return hit


async def _cached_endpoint(key: str, ttl: int, loader, *, stale_ttl: int | None = None):
    stale_ttl = ttl if stale_ttl is None else max(0, stale_ttl)
    now = time.time()
    hit = _usable_hit(key, now)
    if hit and hit.expires_at > now:
        return _cache_result(hit, stale=False)
    # Serialize cold-cache fills per key
    async with _lock_for(key):
        # Re-check after acquiring lock (another waiter may have filled it)
        now = time.time()
        hit = _usable_hit(key, now)
        if hit and hit.expires_at > now:
            return _cache_result(hit, stale=False)
        try:
            value = await loader()
        except Exception:
            if hit and now <= hit.stale_until:
                return _cache_result(hit, stale=True)
            raise
        _maybe_purge_endpoint_cache(now)
        entry = _EndpointCacheEntry(
            expires_at=now + ttl,
            stale_until=now + ttl + stale_ttl,
            fetched_at=now,
            value=value,
        )
        _endpoint_cache[key] = entry
        return _cache_result(entry, stale=False)


from app.services.utils import sanitize as _sanitize

_LOGO_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}


def _website_host(website: str | None) -> str | None:
    if not website:
        return None
    try:
        parsed = urlparse(website if "://" in website else f"https://{website}")
        host = (parsed.netloc or "").lower().split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        if "." not in host:
            return None
        return host
    except Exception:
        return None


def _logo_symbol_variants(symbol: str) -> list[str]:
    normalized = symbol.strip().upper()
    if normalized.startswith("US."):
        normalized = normalized[3:]
    normalized = "".join(c for c in normalized if c.isalnum() or c in {".", "-"})
    if not normalized:
        return []
    variants = [normalized]
    if "." in normalized:
        variants.append(normalized.replace(".", "-"))
    return list(dict.fromkeys(variants))


def _logo_urls(symbol: str, website: str | None = None) -> list[str]:
    candidates = []
    for variant in _logo_symbol_variants(symbol):
        candidates.extend([
            f"https://financialmodelingprep.com/image-stock/{variant}.png",
            f"https://static2.finnhub.io/file/publicdatany/finnhubimage/stock_logo/{variant}.png",
            f"https://eodhd.com/img/logos/US/{variant}.png",
        ])
    host = _website_host(website)
    if host:
        candidates.append(f"https://logo.clearbit.com/{host}")
    return list(dict.fromkeys(candidates))


async def _fetch_company_logo(symbol: str) -> dict[str, Any]:
    headers = {
        "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
        "User-Agent": "Mozilla/5.0 (compatible; OptixPro/1.0)",
    }
    async with httpx.AsyncClient(follow_redirects=True, timeout=6.0, headers=headers) as client:
        for url in _logo_urls(symbol):
            try:
                resp = await client.get(url)
            except Exception:
                continue
            media_type = resp.headers.get("content-type", "").split(";")[0].strip().lower()
            if resp.status_code == 200 and media_type in _LOGO_MEDIA_TYPES and len(resp.content) > 64:
                return {"content": resp.content, "media_type": media_type, "source": url}
    raise HTTPException(status_code=404, detail="Company logo not found")


KNOWN_TICKERS = {
    "NVDA": "Nvidia Corp", "TSLA": "Tesla Inc", "AAPL": "Apple Inc", "AMD": "AMD Inc",
    "AMZN": "Amazon.com", "META": "Meta Platforms", "MSFT": "Microsoft Corp", "GOOGL": "Alphabet Inc",
    "SPY": "SPDR S&P 500 ETF", "QQQ": "Invesco QQQ Trust", "TSM": "Taiwan Semiconductor",
    "AVGO": "Broadcom Inc", "ASML": "ASML Holdings", "MU": "Micron Technology", "INTC": "Intel Corp",
    "ARM": "Arm Holdings", "QCOM": "Qualcomm", "CRM": "Salesforce", "ADBE": "Adobe Inc",
    "ORCL": "Oracle Corp", "NFLX": "Netflix", "DIS": "Disney", "BABA": "Alibaba",
    "LLY": "Eli Lilly", "XOM": "Exxon Mobil", "CVX": "Chevron", "JPM": "JPMorgan Chase",
    "V": "Visa Inc", "MA": "Mastercard", "BAC": "Bank of America",
    "NOW": "ServiceNow", "SNOW": "Snowflake", "PLTR": "Palantir", "NET": "Cloudflare",
    "PANW": "Palo Alto Networks", "CRWD": "CrowdStrike", "MRVL": "Marvell Technology",
    "TXN": "Texas Instruments", "LRCX": "Lam Research", "KLAC": "KLA Corp", "AMAT": "Applied Materials",
    # Finance
    "GS": "Goldman Sachs", "MS": "Morgan Stanley", "C": "Citigroup", "BLK": "BlackRock",
    "SCHW": "Charles Schwab", "AXP": "American Express", "WFC": "Wells Fargo",
    # Healthcare
    "UNH": "UnitedHealth", "JNJ": "Johnson & Johnson", "PFE": "Pfizer", "ABBV": "AbbVie",
    "AMGN": "Amgen", "GILD": "Gilead Sciences", "MRNA": "Moderna", "NVO": "Novo Nordisk",
    "VRTX": "Vertex Pharma", "REGN": "Regeneron",
    # Consumer / Retail
    "WMT": "Walmart", "COST": "Costco", "TGT": "Target", "HD": "Home Depot", "LOW": "Lowe's",
    "NKE": "Nike", "SBUX": "Starbucks", "MCD": "McDonald's", "PEP": "PepsiCo", "KO": "Coca-Cola",
    "PG": "Procter & Gamble", "ABNB": "Airbnb", "BKNG": "Booking Holdings",
    # Industrials / Transport
    "BA": "Boeing", "CAT": "Caterpillar", "DE": "Deere", "UPS": "UPS", "FDX": "FedEx",
    "GE": "GE Aerospace", "HON": "Honeywell", "RTX": "RTX Corp", "LMT": "Lockheed Martin",
    # Tech / Internet
    "UBER": "Uber", "SHOP": "Shopify", "XYZ": "Block Inc", "COIN": "Coinbase",
    "SNAP": "Snap Inc", "PINS": "Pinterest", "RBLX": "Roblox", "U": "Unity Software",
    "DDOG": "Datadog", "MDB": "MongoDB", "ZS": "Zscaler", "OKTA": "Okta",
    "TEAM": "Atlassian", "TWLO": "Twilio", "HUBS": "HubSpot",
    # Energy
    "COP": "ConocoPhillips", "SLB": "Schlumberger", "EOG": "EOG Resources",
    "MPC": "Marathon Petroleum", "OXY": "Occidental Petroleum", "DVN": "Devon Energy",
    # EV / Auto
    "RIVN": "Rivian", "LCID": "Lucid Motors", "F": "Ford", "GM": "General Motors",
    # Chinese ADRs
    "PDD": "PDD Holdings", "JD": "JD.com", "BIDU": "Baidu", "NIO": "NIO Inc",
    "LI": "Li Auto", "XPEV": "XPeng", "BILI": "Bilibili", "TME": "Tencent Music",
    # ETFs
    "IWM": "Russell 2000 ETF", "DIA": "Dow Jones ETF", "XLK": "Tech ETF",
    "XLF": "Financials ETF", "XLE": "Energy ETF", "XLV": "Healthcare ETF",
    "ARKK": "ARK Innovation ETF", "SOXX": "Semiconductor ETF", "GLD": "Gold ETF",
    "TLT": "20Y Treasury ETF", "HYG": "High Yield Bond ETF",
    "PSKY": "Paramount Skydance", "WULF": "TeraWulf",
}


@router.get("/watchlist")
async def watchlist():
    try:
        return await _cached_endpoint("watchlist", 300, _build_watchlist, stale_ttl=900)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Yahoo watchlist data is currently unavailable") from exc


async def _build_watchlist():
    from app.services.sectors import SECTORS

    all_tickers = []
    for sec in SECTORS.values():
        all_tickers.extend(sec["tickers"])
    all_tickers = list(dict.fromkeys(all_tickers))

    # Fetch daily closes once and derive both card prices and sparklines from
    # the same batch. This avoids one fast_info request per ticker on cold load.
    def _fetch_quotes():
        try:
            import yfinance as yf_mod
            df = yf_mod.download(
                tickers=" ".join(all_tickers),
                period="7d",
                interval="1d",
                group_by="ticker",
                threads=True,
                progress=False,
                auto_adjust=False,
                session=getattr(__import__("app.services.yahoo", fromlist=["_yf_session"]),
                                "_yf_session", None),
            )
            from app.services.zh_names import get_zh_name
            quotes = {}
            for t in all_tickers:
                try:
                    frame = None
                    if getattr(df.columns, "nlevels", 1) > 1 and t in df.columns.get_level_values(0):
                        frame = df[t]
                    elif len(all_tickers) == 1:
                        frame = df
                    if frame is None or frame.empty:
                        continue
                    close_col = "Close" if "Close" in frame.columns else "Adj Close"
                    closes = [float(c) for c in frame[close_col].dropna().tolist() if math.isfinite(float(c))]
                    if not closes:
                        continue
                    price = closes[-1]
                    prev = closes[-2] if len(closes) > 1 else price
                    quotes[t] = {
                        "ticker": t,
                        "name": get_zh_name(t) or t,
                        "price": round(price, 2),
                        "change_percent": round((price - prev) / prev * 100, 2) if prev else 0,
                        "spark": [round(c, 2) for c in closes[-7:]],
                    }
                except Exception:
                    continue
            return quotes
        except Exception:
            return {}

    price_map = await asyncio.to_thread(_fetch_quotes)

    # If yfinance limited us hard, less than 30% succeeded — treat as failure
    # so the cache returns the previous (stale) snapshot instead of an empty one.
    success_ratio = len(price_map) / max(len(all_tickers), 1)
    if success_ratio < 0.3:
        raise RuntimeError(f"watchlist mostly failed ({len(price_map)}/{len(all_tickers)} succeeded)")

    groups = []
    for sec_id, sec in SECTORS.items():
        items = [price_map[t] for t in sec["tickers"] if t in price_map]
        if items:
            groups.append({"id": sec_id, "name": sec["name"], "stocks": items})

    return _sanitize({
        "groups": groups,
        "attempted": len(all_tickers),
        "succeeded": len(price_map),
        "failed": len(all_tickers) - len(price_map),
        "data_limited": success_ratio < 1.0,
        "source_status": "active" if success_ratio == 1.0 else "degraded",
    })


@router.get("/search")
async def search_stocks(q: str = Query(..., min_length=1, max_length=50)):
    q_upper = q.upper().strip()
    q_lower = q.lower().strip()
    from app.services.zh_names import NAMES

    def fuzzy(query, text):
        """Check if all chars of query appear in text in order (fuzzy match)."""
        it = iter(text.lower())
        return all(c in it for c in query.lower())

    # 1) Local dictionary — exact substring + fuzzy match
    exact, fuzzy_results = [], []
    all_tickers = {**KNOWN_TICKERS}
    for t, (zh, _) in NAMES.items():
        if t not in all_tickers:
            all_tickers[t] = zh

    for ticker, name in all_tickers.items():
        zh_entry = NAMES.get(ticker)
        zh_name = zh_entry[0] if zh_entry else ""
        zh_desc = zh_entry[1] if zh_entry else ""
        search_text = f"{ticker} {name} {zh_name} {zh_desc}".lower()

        if q_upper == ticker or q_lower == name.lower():
            exact.insert(0, {"ticker": ticker, "name": zh_name or name, "name_en": name, "market": "stocks", "type": "CS"})
        elif q_upper in ticker or q_lower in search_text:
            exact.append({"ticker": ticker, "name": zh_name or name, "name_en": name, "market": "stocks", "type": "CS"})
        elif len(q_lower) >= 2 and (fuzzy(q_lower, ticker) or fuzzy(q_lower, name) or fuzzy(q_lower, zh_name)):
            fuzzy_results.append({"ticker": ticker, "name": zh_name or name, "name_en": name, "market": "stocks", "type": "CS"})

    results = exact + fuzzy_results
    if results:
        return _sanitize(results[:12])

    # 2) Fallback: try yfinance for completely unknown tickers
    def _yf_search():
        try:
            tk = yf.Ticker(q_upper)
            info = tk.info
            name = info.get("shortName", "")
            if name and info.get("regularMarketPrice"):
                return [{"ticker": q_upper, "name": name, "market": "stocks", "type": info.get("quoteType", "CS")}]
        except Exception:
            pass
        return []
    yf_results = await asyncio.to_thread(_yf_search)
    return _sanitize(yf_results[:10])


@router.get("/{ticker}/signals")
async def stock_signals(ticker: str):
    """Compute RSI, MACD, EMA/SMA signals from 100d daily data."""

    symbol = ticker.upper()

    def _safe_number(value: Any) -> float | None:
        try:
            f = float(value)
            return f if math.isfinite(f) else None
        except Exception:
            return None

    def _compute():
        try:
            tk = yf.Ticker(symbol)
            hist = tk.history(period="100d")
            if hist.empty or len(hist) < 50:
                raise RuntimeError(f"Insufficient price history for {symbol}")

            close = hist["Close"]
            volume = hist["Volume"]

            # RSI(14)
            delta = close.diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            avg_gain = _safe_number(gain.iloc[-1])
            avg_loss = _safe_number(loss.iloc[-1])
            if avg_gain is None or avg_loss is None:
                raise RuntimeError(f"RSI unavailable for {symbol}")
            if avg_loss == 0:
                current_rsi = 100.0 if avg_gain > 0 else 50.0
            elif avg_gain == 0:
                current_rsi = 0.0
            else:
                current_rsi = 100 - (100 / (1 + avg_gain / avg_loss))

            # MACD
            ema12 = close.ewm(span=12, adjust=False).mean()
            ema26 = close.ewm(span=26, adjust=False).mean()
            macd_line = ema12 - ema26
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            histogram = macd_line - signal_line
            macd_val = _safe_number(histogram.iloc[-1]) or 0.0
            macd_prev = _safe_number(histogram.iloc[-2]) or 0.0

            # EMAs
            ema20 = _safe_number(close.ewm(span=20, adjust=False).mean().iloc[-1])
            sma50 = _safe_number(close.rolling(50).mean().iloc[-1])
            price = _safe_number(close.iloc[-1])
            if ema20 is None or sma50 is None or price is None:
                raise RuntimeError(f"Technical indicators unavailable for {symbol}")

            # Volume
            avg_vol = _safe_number(volume.rolling(20).mean().iloc[-1]) or 0.0
            cur_vol = _safe_number(volume.iloc[-1]) or 0.0
            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else 1.0

            signals = {
                "rsi": {
                    "value": round(current_rsi, 1),
                    "signal": "oversold" if current_rsi < 30 else "overbought" if current_rsi > 70 else "neutral",
                    "label": "RSI(14)",
                },
                "macd": {
                    "value": round(macd_val, 4),
                    "signal": "bullish" if macd_val > 0 and macd_prev <= 0 else "bearish" if macd_val < 0 and macd_prev >= 0 else ("bullish" if macd_val > 0 else "bearish"),
                    "label": "MACD",
                },
                "ema20": {
                    "value": round(ema20, 2),
                    "signal": "above" if price > ema20 else "below",
                    "label": "EMA(20)",
                },
                "sma50": {
                    "value": round(sma50, 2),
                    "signal": "above" if price > sma50 else "below",
                    "label": "SMA(50)",
                },
                "volume": {
                    "value": round(vol_ratio, 2),
                    "signal": "spike" if vol_ratio > 2 else "high" if vol_ratio > 1.5 else "normal",
                    "label": "Volume",
                },
            }

            # Score: 0-100
            score = 50
            if current_rsi < 30:
                score += 15
            elif current_rsi > 70:
                score -= 15
            elif current_rsi < 40:
                score += 5
            elif current_rsi > 60:
                score -= 5
            if macd_val > 0:
                score += 15
            else:
                score -= 15
            if price > ema20:
                score += 10
            else:
                score -= 10
            if price > sma50:
                score += 10
            else:
                score -= 10
            score = max(0, min(100, score))

            tags = [
                "MOMENTUM" if abs(current_rsi - 50) > 15 else None,
                "TREND" if sma50 and abs(price - sma50) / sma50 > 0.05 else None,
                "VOLUME" if vol_ratio > 1.5 else None,
            ]

            return {
                "ticker": symbol,
                "price": round(price, 2),
                "score": score,
                "overall": "bullish" if score >= 60 else "bearish" if score <= 40 else "neutral",
                "signals": signals,
                "tags": [tag for tag in tags if tag],
            }
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Price provider unavailable for {symbol}") from exc

    try:
        result = await asyncio.to_thread(_compute)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _sanitize({**result, "source_status": "active"})


@router.get("/{ticker}/logo")
async def stock_logo(ticker: str):
    symbol = ticker.upper().strip()
    if not _logo_symbol_variants(symbol):
        raise HTTPException(status_code=404, detail="Invalid ticker")
    logo = await _cached_endpoint(f"logo:{symbol}", 24 * 60 * 60, lambda: _fetch_company_logo(symbol))
    return Response(
        content=logo["content"],
        media_type=logo["media_type"],
        headers={
            "Cache-Control": "public, max-age=86400",
            "X-Logo-Source": logo["source"],
        },
    )


@router.get("/{ticker}")
async def stock_overview(ticker: str):
    try:
        return await _cached_endpoint(f"stock:{ticker.upper()}", 300, lambda: _stock_overview_impl(ticker))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Yahoo stock data is currently unavailable") from exc


async def _stock_overview_impl(ticker: str):
    def _work():
        symbol = ticker.upper()
        tk = yf.Ticker(symbol)
        info = tk.info
        fi = tk.fast_info
        last_price = float(fi.last_price)
        prev_close = float(fi.previous_close)
        from app.services.zh_names import get_zh_info
        zh = get_zh_info(symbol)
        website = info.get("website")
        logo_urls = _logo_urls(symbol, website)
        return {
            "ticker": symbol,
            "name": zh.get("name_zh") or info.get("shortName", symbol),
            "name_en": info.get("shortName", symbol),
            "website": website,
            "logo_url": logo_urls[0] if logo_urls else None,
            "logo_urls": logo_urls,
            "price": round(last_price, 2),
            "change": round(last_price - prev_close, 2),
            "change_percent": round((last_price - prev_close) / prev_close * 100, 2) if prev_close else 0,
            "volume": int(fi.last_volume) if fi.last_volume else None,
            "market_cap": float(fi.market_cap) if fi.market_cap else None,
            "prev_close": round(prev_close, 2),
            "high": info.get("dayHigh"),
            "low": info.get("dayLow"),
            "open": info.get("open"),
            "description": zh.get("description_zh") or info.get("longBusinessSummary", ""),
            "description_en": info.get("longBusinessSummary", ""),
            "sic_description": info.get("industry", ""),
            "pe_ratio": info.get("trailingPE"),
            "dividend_yield": info.get("dividendYield"),
            "year_high": info.get("fiftyTwoWeekHigh"),
            "year_low": info.get("fiftyTwoWeekLow"),
        }

    return _sanitize(await asyncio.to_thread(_work))


def _compute_ema(data, period):
    if len(data) < period:
        return []
    k = 2 / (period + 1)
    result = []
    prev = sum(data[:period]) / period
    result.append(prev)
    for i in range(period, len(data)):
        prev = data[i] * k + prev * (1 - k)
        result.append(prev)
    return result


def _compute_sma(data, period):
    if len(data) < period:
        return []
    result = []
    s = sum(data[:period])
    result.append(s / period)
    for i in range(period, len(data)):
        s += data[i] - data[i - period]
        result.append(s / period)
    return result


# Keep chart data live-ish. Personal dashboard traffic is low, and a 5-minute
# cap prevents the current candle from feeling stale during active sessions.
_CHART_TTL = {"5m": 300, "15m": 300, "1h": 300, "1d": 300, "1w": 300}
_NEW_YORK_TZ = ZoneInfo("America/New_York")


def _normalize_extended_quote_bar(
    bar: dict[str, Any],
    reference_close: float | None = None,
) -> dict[str, Any] | None:
    """Yahoo extended-hours bars often carry quote-only high/low spikes.

    With zero reported volume, treat the bar as a quote path and draw only
    open/close. This keeps pre/post-market movement without letting bad
    high/low ticks flatten the whole chart scale.
    """
    if int(bar.get("v") or 0) > 0:
        return bar
    body_high = max(float(bar["o"]), float(bar["c"]))
    body_low = min(float(bar["o"]), float(bar["c"]))
    if body_low <= 0:
        return None
    if (body_high - body_low) / body_low > 0.08:
        return None
    if reference_close and reference_close > 0:
        ref_move = max(abs(float(bar["o"]) / reference_close - 1), abs(float(bar["c"]) / reference_close - 1))
        if ref_move > 0.20:
            return None
    bar["h"] = round(body_high, 2)
    bar["l"] = round(body_low, 2)
    bar["quote_only"] = True
    return bar


@router.get("/{ticker}/chart")
async def stock_chart(ticker: str, range: str = Query("1d", pattern="^(5m|15m|1h|1d|1w)$")):
    try:
        return await _cached_endpoint(
            f"chart:{ticker.upper()}:{range}",
            _CHART_TTL.get(range, 600),
            lambda: _stock_chart_impl(ticker, range),
        )
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Yahoo chart data is currently unavailable") from exc


async def _stock_chart_impl(ticker: str, range: str):
    def _work():
        # Buttons = K-line intervals (周期), fetch plenty of data for scrolling
        # (yf_period, yf_interval, prepost, visible_bars)
        # visible_bars = how many bars to show initially (user can scroll left for more)
        config = {
            "5m":  ("5d",   "5m",  True,  80),    # 5分钟K线, fetch 5 days
            "15m": ("1mo",  "15m", True,  80),     # 15分钟K线, fetch 1 month
            "1h":  ("3mo",  "1h",  True,  80),     # 1小时K线, fetch 3 months
            "1d":  ("2y",   "1d",  False, 120),    # 日K线, fetch 2 years
            "1w":  ("5y",   "1wk", False, 104),    # 周K线, fetch 5 years
        }
        yf_period, interval, prepost, visible = config.get(range, ("1y", "1d", False, 120))

        tk = yf.Ticker(ticker.upper())
        hist = tk.history(period=yf_period, interval=interval, prepost=prepost)
        if hist.empty:
            return {"bars": [], "ema20": [], "sma50": []}

        raw_bars = []
        for idx, row in hist.iterrows():
            t = int(idx.timestamp())
            try:
                o, h, l, c = (
                    round(float(row["Open"]), 2),
                    round(float(row["High"]), 2),
                    round(float(row["Low"]), 2),
                    round(float(row["Close"]), 2),
                )
            except Exception:
                continue
            if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
                continue
            try:
                volume_raw = float(row.get("Volume", 0))
                v = max(0, int(volume_raw)) if math.isfinite(volume_raw) else 0
            except Exception:
                v = 0
            bar = {"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}
            if prepost:
                dt = idx.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_NEW_YORK_TZ)
                else:
                    dt = dt.astimezone(_NEW_YORK_TZ)
                bar["_ny_min"] = dt.hour * 60 + dt.minute
            raw_bars.append(bar)

        # For intraday with prepost: keep valid extended-hours bars and tag
        # them so the frontend can visually distinguish pre/post-market.
        if prepost:
            filtered = []
            last_close = None
            for b in raw_bars:
                hour_min = b.pop("_ny_min", None)
                if hour_min is None:
                    continue
                is_regular = 570 <= hour_min < 960  # 9:30 to 16:00 ET
                has_valid_price = all(math.isfinite(float(b[k])) and float(b[k]) > 0 for k in ("o", "h", "l", "c"))
                if not has_valid_price:
                    continue
                if is_regular:
                    b["session"] = "regular"
                    filtered.append(b)
                    last_close = float(b["c"])
                else:
                    b["ext"] = True
                    b["session"] = "pre" if hour_min < 570 else "post"
                    cleaned = _normalize_extended_quote_bar(b, last_close)
                    if cleaned is not None:
                        filtered.append(cleaned)
                        last_close = float(cleaned["c"])
            bars = filtered
        else:
            bars = raw_bars

        # Compute EMA/SMA on FULL fetch window (more data = smoother lines)
        closes = [b["c"] for b in bars]
        times = [b["t"] for b in bars]

        ema20 = _compute_ema(closes, 20)
        sma50 = _compute_sma(closes, 50)

        ema20_data = [{"time": times[i + len(closes) - len(ema20)], "value": round(v, 2)} for i, v in enumerate(ema20)]
        sma50_data = [{"time": times[i + len(closes) - len(sma50)], "value": round(v, 2)} for i, v in enumerate(sma50)]

        # Send ALL data to frontend — let TradingView handle scrolling
        # visible tells frontend how many bars to show initially
        return {"bars": bars, "ema20": ema20_data, "sma50": sma50_data, "visible": visible}

    return _sanitize(await asyncio.to_thread(_work))
