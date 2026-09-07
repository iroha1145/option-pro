from __future__ import annotations

from datetime import datetime, time as datetime_time, timedelta, timezone
import math
import threading
from typing import Any
from zoneinfo import ZoneInfo

from app.services.market_calendar import options_close_minutes
from app.services.quote_quality import (
    estimated_premium,
    inverted_iv_acceptable,
    option_mark,
    vendor_iv,
)

import logging
import warnings as _warnings
import yfinance as yf

_log = logging.getLogger(__name__)

# Use curl_cffi browser-impersonation session to dodge Yahoo's rate limits.
# Falls back to default session if curl_cffi isn't available.
try:
    from curl_cffi import requests as _cffi_requests
    _yf_session = _cffi_requests.Session(impersonate="chrome")
except Exception as _e:
    _yf_session = None
    _log.warning("curl_cffi unavailable, yfinance will use default session: %s", _e)

# Monkey-patch yf.Ticker so ALL call sites get our impersonating session.
# Verify the patched __init__ signature still matches what yfinance expects;
# if upstream changes the signature, we'd silently break — bail loudly instead.
if _yf_session is not None:
    import inspect as _inspect
    try:
        _sig = _inspect.signature(yf.Ticker.__init__)
        _params = list(_sig.parameters.keys())
        # Expect at least (self, ticker, session=...) — bail if shape changed
        if "session" not in _params:
            _warnings.warn(
                f"yfinance {yf.__version__} Ticker.__init__ no longer accepts 'session' "
                "kwarg; skipping monkey-patch. Update yahoo.py for new API."
            )
            _yf_session = None
    except Exception:
        pass  # if we can't introspect, try the patch anyway

if _yf_session is not None:
    _orig_init = yf.Ticker.__init__
    def _patched_init(self, ticker, session=None, **kwargs):
        if session is None:
            session = _yf_session
        _orig_init(self, ticker, session=session, **kwargs)
    yf.Ticker.__init__ = _patched_init

# Simple in-memory cache for yfinance data. Values are
# (expires_at, fetched_at, data) so stale fallbacks have a hard age limit and
# can disclose their provenance to API callers.
_cache: dict[str, tuple[datetime, datetime, Any]] = {}
_cache_lock = threading.RLock()
_key_locks: dict[str, threading.Lock] = {}
_key_lock_users: dict[str, int] = {}
_CACHE_PURGE_THRESHOLD = 1024
_MAX_STALE_SECONDS = 30 * 60

NEW_YORK_TZ = ZoneInfo("America/New_York")
_SECONDS_PER_YEAR = 365.0 * 24 * 60 * 60
STOCK_IV_METHOD_VERSION = 2
STOCK_IV_TARGET_DTE = 30.0
PRICING_ASSUMPTIONS = {
    "risk_free_rate": 0.05,
    "risk_free_rate_kind": "model_assumption",
    "dividend_yield": None,
    "dividend_yield_kind": "unmodeled",
    "day_count": "calendar_365",
    "model": "european_black_scholes",
}


def _purge_cache(now: datetime) -> None:
    """Drop expired entries so per-ticker/expiration keys can't pile up forever."""
    if len(_cache) >= _CACHE_PURGE_THRESHOLD:
        for key in [k for k, (_, fetched_at, _) in _cache.items() if (now - fetched_at).total_seconds() > _MAX_STALE_SECONDS]:
            _cache.pop(key, None)
        if len(_cache) >= _CACHE_PURGE_THRESHOLD:
            # Still too big (all entries fresh): drop the oldest-expiring half.
            for key, _ in sorted(_cache.items(), key=lambda item: item[1][0])[: len(_cache) // 2]:
                _cache.pop(key, None)
    for key in [
        key
        for key in _key_locks
        if key not in _cache and _key_lock_users.get(key, 0) == 0
    ]:
        _key_locks.pop(key, None)


def _acquire_key_lock(key: str) -> threading.Lock:
    """Reserve a per-key lock without leaving failed unique keys behind."""
    with _cache_lock:
        key_lock = _key_locks.get(key)
        if key_lock is None:
            key_lock = threading.Lock()
            _key_locks[key] = key_lock
        _key_lock_users[key] = _key_lock_users.get(key, 0) + 1
    key_lock.acquire()
    return key_lock


def _release_key_lock(key: str, key_lock: threading.Lock) -> None:
    key_lock.release()
    with _cache_lock:
        remaining = _key_lock_users.get(key, 1) - 1
        if remaining > 0:
            _key_lock_users[key] = remaining
            return
        _key_lock_users.pop(key, None)
        if key not in _cache and _key_locks.get(key) is key_lock:
            _key_locks.pop(key, None)


def _cache_value(value: Any, metadata: dict[str, Any], with_metadata: bool) -> Any:
    if with_metadata:
        return value, metadata
    if isinstance(value, dict):
        result = dict(value)
        result["_stale"] = bool(metadata["_stale"])
        result.setdefault("as_of", metadata["as_of"])
        if metadata["_stale"]:
            result["source_status"] = "stale"
        else:
            result.setdefault("source_status", metadata["source_status"])
        return result
    return value


def _cached(
    key: str,
    ttl_seconds: int,
    loader,
    *,
    max_stale_seconds: int = _MAX_STALE_SECONDS,
    with_metadata: bool = False,
    is_valid=None,
):
    now = datetime.now(timezone.utc)
    with _cache_lock:
        hit = _cache.get(key)
    if hit and hit[0] > now:
        _, fetched_at, value = hit
        valid = is_valid(value) if is_valid is not None else True
        metadata = {
            "_stale": False,
            "as_of": fetched_at.isoformat(),
            "source_status": "active" if valid else "insufficient_data",
        }
        return _cache_value(value, metadata, with_metadata)

    key_lock = _acquire_key_lock(key)
    try:
        now = datetime.now(timezone.utc)
        with _cache_lock:
            hit = _cache.get(key)
        if hit and hit[0] > now:
            _, fetched_at, value = hit
            valid = is_valid(value) if is_valid is not None else True
            metadata = {
                "_stale": False,
                "as_of": fetched_at.isoformat(),
                "source_status": "active" if valid else "insufficient_data",
            }
            return _cache_value(value, metadata, with_metadata)

        try:
            value = loader()
        except Exception as exc:
            value = None
            load_error = exc
            load_failed = True
        else:
            load_error = None
            load_failed = False

        valid = not load_failed and (is_valid(value) if is_valid is not None else True)
        if not valid and hit:
            _, fetched_at, stale_value = hit
            stale_age = (now - fetched_at).total_seconds()
            stale_valid = is_valid(stale_value) if is_valid is not None else True
            if stale_valid and stale_age <= max(0, int(max_stale_seconds)):
                metadata = {
                    "_stale": True,
                    "as_of": fetched_at.isoformat(),
                    "source_status": "stale",
                    "stale_age_seconds": round(max(stale_age, 0.0), 1),
                }
                return _cache_value(stale_value, metadata, with_metadata)
            with _cache_lock:
                _cache.pop(key, None)
        if load_failed:
            assert load_error is not None
            raise load_error

        fetched_at = datetime.now(timezone.utc)
        # Negative results get a short cache to avoid hammering the provider
        # while still recovering quickly when a transient empty payload clears.
        effective_ttl = ttl_seconds if valid else min(ttl_seconds, 30)
        with _cache_lock:
            _purge_cache(fetched_at)
            _cache[key] = (fetched_at + timedelta(seconds=effective_ttl), fetched_at, value)
        metadata = {
            "_stale": False,
            "as_of": fetched_at.isoformat(),
            "source_status": "active" if valid else "insufficient_data",
        }
        return _cache_value(value, metadata, with_metadata)
    finally:
        _release_key_lock(key, key_lock)


def _read_cached(
    key: str,
    *,
    with_metadata: bool = False,
    is_valid=None,
    max_stale_seconds: int = _MAX_STALE_SECONDS,
) -> Any | None:
    """Read an existing bounded cache entry without invoking its loader."""

    now = datetime.now(timezone.utc)
    with _cache_lock:
        hit = _cache.get(key)
    if hit is None:
        return None
    expires_at, fetched_at, value = hit
    valid = is_valid(value) if is_valid is not None else True
    stale_age = (now - fetched_at).total_seconds()
    if not valid or stale_age > max(0, int(max_stale_seconds)):
        return None
    stale = expires_at <= now
    metadata = {
        "_stale": stale,
        "as_of": fetched_at.isoformat(),
        "source_status": "stale" if stale else "active",
    }
    if stale:
        metadata["stale_age_seconds"] = round(max(stale_age, 0.0), 1)
    return _cache_value(value, metadata, with_metadata)


def option_expiry_metrics(expiration: str, now: datetime | None = None) -> dict[str, Any]:
    """Time remaining to the regular or early US equity-option close."""
    expiry_date = datetime.strptime(expiration, "%Y-%m-%d").date()
    current = now or datetime.now(NEW_YORK_TZ)
    if current.tzinfo is None:
        current = current.replace(tzinfo=NEW_YORK_TZ)
    else:
        current = current.astimezone(NEW_YORK_TZ)
    close_minutes = options_close_minutes(expiry_date) or 16 * 60
    expiration_at = datetime.combine(
        expiry_date,
        datetime_time(close_minutes // 60, close_minutes % 60),
        tzinfo=NEW_YORK_TZ,
    )
    seconds = max((expiration_at - current).total_seconds(), 0.0)
    return {
        "expiration_at": expiration_at.isoformat(),
        "dte": round(seconds / (24 * 60 * 60), 6),
        "time_to_expiry_years": seconds / _SECONDS_PER_YEAR,
    }


def _get_ticker(symbol: str) -> yf.Ticker:
    if _yf_session is not None:
        return yf.Ticker(symbol.upper(), session=_yf_session)
    return yf.Ticker(symbol.upper())


def _get_expirations_cached(ticker: str, *, with_metadata: bool = False) -> Any:
    symbol = ticker.upper()
    return _cached(
        f"expirations:{symbol}",
        300,
        lambda: list(_get_ticker(symbol).options),
        with_metadata=with_metadata,
        is_valid=bool,
    )


def get_expirations(ticker: str) -> list[str]:
    """Get available option expiration dates (compatibility list form)."""
    return _get_expirations_cached(ticker, with_metadata=False)


def get_expirations_snapshot(ticker: str) -> dict[str, Any]:
    """Get expirations with explicit cache freshness metadata."""
    expirations, metadata = _get_expirations_cached(ticker, with_metadata=True)
    return {"expirations": expirations, **metadata}


def get_cached_expirations_snapshot(ticker: str) -> dict[str, Any] | None:
    """Read expiration data already held by this process without refreshing."""

    symbol = ticker.upper()
    cached = _read_cached(
        f"expirations:{symbol}",
        with_metadata=True,
        is_valid=bool,
    )
    if cached is None:
        return None
    expirations, metadata = cached
    return {"expirations": expirations, **metadata}


def _option_moneyness(
    side: str,
    strike: float,
    underlying_price: float | None,
) -> str:
    if underlying_price is None or underlying_price <= 0:
        return "unavailable"
    if strike == underlying_price:
        return "atm"
    if side == "call":
        return "otm" if strike > underlying_price else "itm"
    return "otm" if strike < underlying_price else "itm"


def _option_in_the_money(
    side: str,
    strike: float,
    underlying_price: float | None,
    provider_value: Any,
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


def _deep_otm_fraction(
    side: str,
    strike: float,
    underlying_price: float | None,
) -> float | None:
    if underlying_price is None or underlying_price <= 0:
        return None
    distance = (
        strike - underlying_price
        if side == "call"
        else underlying_price - strike
    )
    if distance <= 0:
        return None
    return distance / underlying_price


def get_option_chain(ticker: str, expiration: str) -> dict[str, Any]:
    """Get full option chain for a ticker and expiration date."""
    symbol = ticker.upper()

    def load() -> dict[str, Any]:
        t = _get_ticker(symbol)

        # Get current stock price
        try:
            price = _safe_float(t.fast_info.last_price)
        except Exception:
            price = None

        chain = t.option_chain(expiration)
        expiry = option_expiry_metrics(expiration)
        dte = expiry["dte"]
        T = expiry["time_to_expiry_years"]

        def _resolve_iv(row, strike, last_price, stock_price, is_call=True):
            """Prefer vendor IV; invert only from a quality bid/ask mid.

            lastPrice may be returned as a last trade but is never treated as
            the current quote used for a silent inversion.
            """
            quoted = vendor_iv(row.get("impliedVolatility"))
            if quoted is not None:
                return quoted, "vendor"
            mark = option_mark(
                bid=row.get("bid"),
                ask=row.get("ask"),
                last=last_price,
                allow_last_for_estimate=False,
            )
            if (
                mark["usable_for_inversion"]
                and stock_price
                and stock_price > 0
                and strike
            ):
                computed = compute_iv(
                    mark["value"], stock_price, strike, T, r=0.05, is_call=is_call
                )
                if inverted_iv_acceptable(computed):
                    return computed, "model_inversion"
            raw = _safe_float(row.get("impliedVolatility"))
            return raw, "vendor_raw" if raw is not None else "missing"

        def _greeks_for(strike, iv, is_call):
            if not (price and price > 0 and iv and iv > 0):
                return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
            return compute_greeks(price, strike, T, 0.05, iv, is_call=is_call)

        calls = []
        # itertuples is 5-10x faster than iterrows; _asdict() keeps dict access.
        for nt in chain.calls.itertuples(index=False):
            row = nt._asdict()
            strike = _safe_float(row.get("strike"))
            if strike is None or strike <= 0:
                continue
            last_price = _safe_float(row.get("lastPrice"))
            iv, iv_source = _resolve_iv(row, strike, last_price, price, is_call=True)
            greeks = _greeks_for(strike, iv, True)
            break_even = strike + last_price if last_price is not None else None
            calls.append(
                {
                    "ticker": row.get("contractSymbol", ""),
                    "type": "call",
                    "strike": strike,
                    "expiration": expiration,
                    "bid": _safe_float(row.get("bid")),
                    "ask": _safe_float(row.get("ask")),
                    "mid": _mid(row.get("bid"), row.get("ask")),
                    "midpoint": _mid(row.get("bid"), row.get("ask")),
                    "last_price": last_price,
                    "change": _safe_float(row.get("change")),
                    "change_percent": _safe_float(row.get("percentChange")),
                    "day_change": _safe_float(row.get("change")),
                    "day_change_percent": _safe_float(row.get("percentChange")),
                    "volume": _safe_int(row.get("volume")),
                    "open_interest": _safe_int(row.get("openInterest")),
                    "implied_volatility": iv,
                    "iv_source": iv_source,
                    "in_the_money": _option_in_the_money(
                        "call", strike, price, row.get("inTheMoney")
                    ),
                    "moneyness": _option_moneyness("call", strike, price),
                    "break_even": break_even,
                    "break_even_price": break_even,
                    **greeks,
                }
            )

        puts = []
        for nt in chain.puts.itertuples(index=False):
            row = nt._asdict()
            strike = _safe_float(row.get("strike"))
            if strike is None or strike <= 0:
                continue
            last_price = _safe_float(row.get("lastPrice"))
            iv, iv_source = _resolve_iv(row, strike, last_price, price, is_call=False)
            greeks = _greeks_for(strike, iv, False)
            break_even = strike - last_price if last_price is not None else None
            puts.append(
                {
                    "ticker": row.get("contractSymbol", ""),
                    "type": "put",
                    "strike": strike,
                    "expiration": expiration,
                    "bid": _safe_float(row.get("bid")),
                    "ask": _safe_float(row.get("ask")),
                    "mid": _mid(row.get("bid"), row.get("ask")),
                    "midpoint": _mid(row.get("bid"), row.get("ask")),
                    "last_price": last_price,
                    "change": _safe_float(row.get("change")),
                    "change_percent": _safe_float(row.get("percentChange")),
                    "day_change": _safe_float(row.get("change")),
                    "day_change_percent": _safe_float(row.get("percentChange")),
                    "volume": _safe_int(row.get("volume")),
                    "open_interest": _safe_int(row.get("openInterest")),
                    "implied_volatility": iv,
                    "iv_source": iv_source,
                    "in_the_money": _option_in_the_money(
                        "put", strike, price, row.get("inTheMoney")
                    ),
                    "moneyness": _option_moneyness("put", strike, price),
                    "break_even": break_even,
                    "break_even_price": break_even,
                    **greeks,
                }
            )

        # ── Detect unusual activity alerts ──
        alerts = []
        all_contracts = [(c, "call") for c in calls] + [(p, "put") for p in puts]
        for contract, side in all_contracts:
            vol = contract.get("volume") or 0
            oi = contract.get("open_interest") or 0
            lp = contract.get("last_price")
            iv = contract.get("implied_volatility")
            strike = contract["strike"]

            reasons = []

            # Rule 1: Volume/OI ratio > 3 (unusual volume relative to open interest)
            if oi > 0 and vol / oi >= 3:
                reasons.append(f"Vol/OI {vol/oi:.1f}x")

            # Rule 2: High absolute volume
            if vol >= 5000:
                reasons.append(f"高成交量 {vol:,}")

            # Rule 3: Estimated notional premium, not cash flow.
            mark = option_mark(
                bid=contract.get("bid"),
                ask=contract.get("ask"),
                last=lp,
                allow_last_for_estimate=True,
            )
            premium = estimated_premium(mark["value"], vol)
            if premium is not None and premium >= 500_000:
                reasons.append(f"估算名义权利金 ${premium:,.0f}（标记价×成交量×100）")

            # Rule 4: Volume high vs open interest — observation only.
            if vol >= 1000 and 0 < oi < 500:
                reasons.append("成交量/未平仓量比值较高；无法据此确认开仓或平仓")

            # Rule 5: Deep OTM with high volume (speculative)
            otm_pct = _deep_otm_fraction(side, strike, price)
            if otm_pct is not None:
                if otm_pct > 0.10 and vol >= 2000:
                    reasons.append(f"深度虚值 ({otm_pct*100:.0f}% OTM)")

            if reasons:
                alerts.append({
                    "strike": strike,
                    "type": side,
                    "expiration": expiration,
                    "dte": dte,
                    "volume": vol,
                    "open_interest": oi,
                    "last_price": lp,
                    "implied_volatility": iv,
                    "premium_flow": round(premium, 0) if premium is not None else None,
                    "premium_basis": mark["basis"],
                    "premium_kind": "estimated_notional",
                    "vol_oi_ratio": round(vol / oi, 2) if oi > 0 else None,
                    "reasons": reasons,
                    "moneyness": contract.get("moneyness")
                    or _option_moneyness(side, strike, price),
                    "direction": None,
                    "direction_confidence": 0,
                    "direction_status": "unavailable_without_trade_side",
                    "signal": "unknown",
                    "inferred_direction": "unknown",
                    "direction_deprecated": True,
                    "direction_note": "缺少成交主动方，无法判断真实交易方向",
                })

        # Sort alerts by premium flow descending
        alerts.sort(key=lambda a: a.get("premium_flow") or 0, reverse=True)

        strikes = sorted(set(c["strike"] for c in calls) | set(p["strike"] for p in puts))
        call_map = {c["strike"]: c for c in calls}
        put_map = {p["strike"]: p for p in puts}
        grouped = {str(s): {"call": call_map.get(s), "put": put_map.get(s)} for s in strikes}

        return {
            "ticker": symbol,
            "expiration": expiration,
            "expiration_at": expiry["expiration_at"],
            "dte": dte,
            "time_to_expiry_years": T,
            "underlying_price": price,
            "strikes": strikes,
            "calls": calls,
            "puts": puts,
            "grouped_by_strike": grouped,
            "alerts": alerts[:10],  # top 10 unusual activity alerts
            "data_limited": False,
            "quote_time_kind": "chain_fetch",
            "per_contract_quote_time": False,
            "pricing_assumptions": dict(PRICING_ASSUMPTIONS),
        }

    return _cached(
        f"chain:{symbol}:{expiration}",
        300,
        load,
        is_valid=lambda value: bool(value.get("calls") or value.get("puts")),
    )


def get_cached_option_chain(
    ticker: str,
    expiration: str,
) -> dict[str, Any] | None:
    """Read an existing option chain without contacting the provider."""

    symbol = ticker.upper()
    return _read_cached(
        f"chain:{symbol}:{expiration}",
        is_valid=lambda value: bool(value.get("calls") or value.get("puts")),
    )


def _empty_stock_iv_payload(**overrides: Any) -> dict[str, Any]:
    payload = {
        "atm_iv": None,
        "iv_method": "missing",
        "iv_side": "call",
        "iv_source": None,
        "target_dte": STOCK_IV_TARGET_DTE,
        "selected_expiration": None,
        "selected_dte": None,
        "expiration_selection": "missing",
        "selected_strike": None,
        "strike_distance": None,
        "strike_fallback_reason": None,
        "provider": "Yahoo/yfinance",
        "inverted_from": None,
        "method_version": STOCK_IV_METHOD_VERSION,
        "pricing_assumptions": dict(PRICING_ASSUMPTIONS),
    }
    payload.update(overrides)
    return payload


def _select_iv_expiration(
    expirations: list[str],
    now: datetime,
) -> tuple[str | None, float | None, str, dict[str, Any] | None]:
    """Target 30-day DTE inside the 20–60 window; earlier expiry wins ties."""

    parsed: list[tuple[str, float, dict[str, Any]]] = []
    for raw in expirations:
        try:
            metrics = option_expiry_metrics(str(raw), now=now)
        except (ValueError, TypeError):
            continue
        dte = metrics["dte"]
        if dte <= 0:
            continue
        parsed.append((str(raw), float(dte), metrics))
    if not parsed:
        return None, None, "missing", None

    def _rank(items: list[tuple[str, float, dict[str, Any]]]):
        return sorted(items, key=lambda item: (abs(item[1] - STOCK_IV_TARGET_DTE), item[0]))

    primary = _rank([item for item in parsed if 20 <= item[1] <= 60])
    if primary:
        chosen = primary[0]
        return chosen[0], chosen[1], "primary_20_60", chosen[2]
    fallback = _rank([item for item in parsed if item[1] > 7])
    if fallback:
        chosen = fallback[0]
        return chosen[0], chosen[1], "fallback_gt_7", chosen[2]
    return None, None, "missing", None


def _call_rows_nearest_strikes(calls, price: float, limit: int = 5) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for nt in calls.itertuples(index=False):
        row = nt._asdict() if hasattr(nt, "_asdict") else dict(nt._asdict())
        strike = _safe_float(row.get("strike"))
        if strike is None or strike <= 0:
            continue
        rows.append({**row, "strike": strike, "_distance": abs(strike - price)})
    rows.sort(key=lambda item: (item["_distance"], item["strike"]))
    return rows[:limit]


def _get_stock_iv_cached(ticker: str, *, with_metadata: bool = False) -> Any:
    """Near-month / target-tenor ATM call IV. Not a constant 30-day interpolation."""
    symbol = ticker.upper()

    def load() -> dict[str, Any]:
        t = _get_ticker(symbol)
        exps = list(t.options or [])
        if not exps:
            return _empty_stock_iv_payload()

        try:
            price = float(t.fast_info.last_price)
        except Exception:
            return _empty_stock_iv_payload()
        if not math.isfinite(price) or price <= 0:
            return _empty_stock_iv_payload()

        now_ny = datetime.now(NEW_YORK_TZ)
        target_exp, selected_dte, selection, expiry = _select_iv_expiration(exps, now_ny)
        if not target_exp or expiry is None:
            return _empty_stock_iv_payload()

        chain = t.option_chain(target_exp)
        candidates = _call_rows_nearest_strikes(chain.calls, price)
        T = expiry["time_to_expiry_years"]
        fallback_reason = None
        for index, row in enumerate(candidates):
            strike = row["strike"]
            quoted = vendor_iv(row.get("impliedVolatility"))
            if quoted is not None:
                return _empty_stock_iv_payload(
                    atm_iv=round(quoted, 4),
                    iv_method="vendor_call",
                    iv_source="vendor",
                    selected_expiration=target_exp,
                    selected_dte=selected_dte,
                    expiration_selection=selection,
                    selected_strike=strike,
                    strike_distance=row["_distance"],
                    strike_fallback_reason=None if index == 0 else "nearest_invalid",
                )
            if index == 0:
                fallback_reason = "nearest_invalid"

        for index, row in enumerate(candidates):
            strike = row["strike"]
            mark = option_mark(
                bid=row.get("bid"),
                ask=row.get("ask"),
                last=row.get("lastPrice"),
                allow_last_for_estimate=False,
            )
            if not mark["usable_for_inversion"]:
                continue
            computed = compute_iv(mark["value"], price, strike, T, r=0.05, is_call=True)
            if inverted_iv_acceptable(computed):
                return _empty_stock_iv_payload(
                    atm_iv=round(float(computed), 4),
                    iv_method="model_inversion",
                    iv_source="model_inversion",
                    inverted_from="quality_mid",
                    selected_expiration=target_exp,
                    selected_dte=selected_dte,
                    expiration_selection=selection,
                    selected_strike=strike,
                    strike_distance=row["_distance"],
                    strike_fallback_reason=fallback_reason
                    if index > 0 or fallback_reason
                    else None,
                )
        return _empty_stock_iv_payload(
            expiration_selection=selection,
            selected_expiration=target_exp,
            selected_dte=selected_dte,
            strike_fallback_reason=fallback_reason or "no_qualified_call",
        )

    return _cached(
        f"stock_iv:v{STOCK_IV_METHOD_VERSION}:{symbol}",
        300,
        load,
        with_metadata=with_metadata,
        is_valid=lambda value: isinstance(value, dict) and value.get("atm_iv") is not None,
    )


def get_stock_iv(ticker: str) -> float | None:
    """Return current ATM IV as a decimal (for example, ``0.325`` = 32.5%)."""
    payload = _get_stock_iv_cached(ticker, with_metadata=False)
    if isinstance(payload, dict):
        iv = payload.get("atm_iv")
        return float(iv) if isinstance(iv, (int, float)) else None
    return payload


def get_stock_iv_snapshot(ticker: str) -> dict[str, Any]:
    """Return current ATM IV together with cache freshness and method metadata."""
    payload, metadata = _get_stock_iv_cached(ticker, with_metadata=True)
    body = dict(payload) if isinstance(payload, dict) else {"atm_iv": payload}
    return {**body, **metadata}


def get_last_price(ticker: str) -> float | None:
    """Get last stock price from yfinance for fallback/sector displays."""
    try:
        return _safe_float(_get_ticker(ticker).fast_info.last_price)
    except Exception:
        return None


def _safe_float(v) -> float | None:
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 4)
    except Exception:
        return None


def _safe_int(v) -> int | None:
    try:
        i = int(float(v))
        return i if i >= 0 else None
    except Exception:
        return None


def _mid(bid, ask) -> float | None:
    b, a = _safe_float(bid), _safe_float(ask)
    if b is not None and a is not None and b > 0 and a > 0:
        return round((b + a) / 2, 4)
    return None


# ── Black-Scholes IV solver (for after-hours when yfinance IV ≈ 0) ──────────

from math import log, sqrt, exp, pi, erf

def _norm_cdf(x):
    return (1 + erf(x / sqrt(2))) / 2

def _norm_pdf(x):
    return exp(-x * x / 2) / sqrt(2 * pi)

def _bs_call(S, K, T, r, sigma):
    if T <= 0 or sigma <= 0:
        return max(S - K, 0)
    d1 = (log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrt(T))
    d2 = d1 - sigma * sqrt(T)
    return S * _norm_cdf(d1) - K * exp(-r * T) * _norm_cdf(d2)

def _bs_price(S, K, T, r, sigma, is_call):
    call = _bs_call(S, K, T, r, sigma)
    return call if is_call else call - S + K * exp(-r * T)


def compute_iv(option_price, S, K, T, r=0.05, is_call=True):
    """Solve IV via Newton's method; fall back to bisection if Newton fails to converge.

    Newton can diverge for deep OTM / near-expiry options where vega → 0.
    Bisection is slower but guaranteed to converge in a bounded sigma range.
    """
    if option_price <= 0 or S <= 0 or K <= 0 or T <= 0:
        return None
    intrinsic = max(S - K, 0) if is_call else max(K - S, 0)
    if option_price < intrinsic * 0.9:
        return None

    # ── Newton's method (fast path) ──
    sigma = 0.3
    for _ in range(50):
        bs = _bs_price(S, K, T, r, sigma, is_call)
        try:
            d1 = (log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrt(T))
            vega = S * sqrt(T) * _norm_pdf(d1)
        except (ValueError, ZeroDivisionError):
            break
        if abs(vega) < 1e-8:
            break  # Newton stuck; try bisection
        step = (bs - option_price) / vega
        sigma -= step
        if sigma <= 0.001 or sigma > 5.0:
            break  # diverged; try bisection
        if abs(bs - option_price) < 0.001:
            return round(sigma, 4)

    # ── Bisection fallback (guaranteed convergence in [lo, hi]) ──
    lo, hi = 0.001, 5.0
    try:
        f_lo = _bs_price(S, K, T, r, lo, is_call) - option_price
        f_hi = _bs_price(S, K, T, r, hi, is_call) - option_price
    except Exception:
        return None
    if f_lo * f_hi > 0:
        return None  # no root in bracket
    for _ in range(60):  # log2(5/0.001) ≈ 12 needed for 0.001 precision
        mid = (lo + hi) / 2
        f_mid = _bs_price(S, K, T, r, mid, is_call) - option_price
        if abs(f_mid) < 0.001:
            return round(mid, 4)
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    # Do not treat a truncated bracket midpoint as a solved IV.
    candidate = (lo + hi) / 2
    residual = abs(_bs_price(S, K, T, r, candidate, is_call) - option_price)
    if residual < 0.001 or residual / max(option_price, 1e-6) < 0.01:
        return round(candidate, 4)
    return None


def compute_greeks(S, K, T, r, sigma, is_call=True):
    """Black-Scholes Greeks. Returns dict with delta, gamma, theta, vega, rho.

    All inputs must be > 0. theta is per-day (not per-year).
    Returns None for any greek that fails to compute.
    """
    if not (S > 0 and K > 0 and T > 0 and sigma > 0):
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
    try:
        sqrtT = sqrt(T)
        d1 = (log(S / K) + (r + sigma * sigma / 2) * T) / (sigma * sqrtT)
        d2 = d1 - sigma * sqrtT
        Nd1 = _norm_cdf(d1)
        Nd2 = _norm_cdf(d2)
        nd1 = _norm_pdf(d1)
        disc = exp(-r * T)
        # Common
        gamma = nd1 / (S * sigma * sqrtT)
        vega = S * nd1 * sqrtT / 100.0  # per 1% IV change
        if is_call:
            delta = Nd1
            theta = (- S * nd1 * sigma / (2 * sqrtT) - r * K * disc * Nd2) / 365.0
            rho = K * T * disc * Nd2 / 100.0
        else:
            delta = Nd1 - 1.0
            theta = (- S * nd1 * sigma / (2 * sqrtT) + r * K * disc * (1 - Nd2)) / 365.0
            rho = -K * T * disc * (1 - Nd2) / 100.0
        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 5),
            "theta": round(theta, 4),
            "vega": round(vega, 4),
            "rho": round(rho, 4),
        }
    except Exception:
        return {"delta": None, "gamma": None, "theta": None, "vega": None, "rho": None}
