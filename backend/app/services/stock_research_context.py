"""Bounded public stock facts for research; never reads account resources.

Durable overview/daily bars win over provider requests. Fundamentals reuse
Yahoo's process cache and its bounded I/O implementation. Cancellation stops
waiters, while the I/O slots remain held until an abandoned provider finishes.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any

from app.stock_data_reads import read_latest_stock_resource
from app.services import yahoo
from app.services.yahoo_option_io import YahooOptionIO

RESEARCH_BLOCK_KEYS = (
    "company_profile", "price_history", "financials", "analyst_consensus", "earnings_history",
)
_IO = YahooOptionIO(max_in_flight=2, max_queue=8, queue_wait_seconds=0.2, call_timeout_seconds=3)
_CACHE_SECONDS = 6 * 3600


def number(value: Any) -> int | float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def text(value: Any, limit: int = 240) -> str | None:
    return value.strip()[:limit] if isinstance(value, str) and value.strip() else None


def date_value(value: Any) -> str | None:
    if hasattr(value, "isoformat"):
        return value.isoformat()[:10]
    if isinstance(value, str) and len(value) >= 10:
        try:
            return datetime.fromisoformat(value[:10]).date().isoformat()
        except ValueError:
            pass
    return None


def _base(source: str, *, as_of: Any = None, data_through: Any = None, stale: bool = False, status: str = "ok") -> dict[str, Any]:
    return {"source": source, "as_of": as_of, "data_through": data_through, "stale": stale, "status": status}


def _stored(symbol: str, resource: str) -> tuple[dict[str, Any], dict[str, Any]] | None:
    entry = read_latest_stock_resource(symbol, resource)
    if not entry or not isinstance(entry.get("payload"), dict):
        return None
    saved = number(entry.get("saved_at"))
    observed = datetime.fromtimestamp(saved, timezone.utc).isoformat() if saved is not None else None
    return entry["payload"], _base(entry.get("source") or "saved_stock_resource", as_of=observed, stale=not bool(entry.get("fresh")))


def _provider(symbol: str, resource: str, load) -> tuple[Any, dict[str, Any]]:
    value, metadata = yahoo._cached(
        f"stock_research:{symbol}:{resource}", _CACHE_SECONDS,
        lambda: _IO.run(lambda: load(yahoo._get_ticker(symbol))),
        with_metadata=True, is_valid=lambda value: value is not None and (not isinstance(value, dict) or bool(value)) and (not hasattr(value, "empty") or not value.empty),
    )
    return value, _base("Yahoo/yfinance", as_of=metadata.get("as_of"), stale=bool(metadata.get("_stale")))


def _info(symbol: str) -> tuple[dict[str, Any], dict[str, Any]]:
    value, meta = _provider(symbol, "info", lambda ticker: ticker.get_info())
    return value if isinstance(value, dict) else {}, meta


def _not_applicable(symbol: str, kind: str) -> dict[str, Any] | None:
    if symbol.startswith("^"):
        return {**_base("symbol_classification", status="not_applicable"), "reason": "index_has_no_company_fundamentals", "ticker": symbol}
    return None


def company_profile(symbol: str) -> dict[str, Any]:
    skipped = _not_applicable(symbol, "profile")
    if skipped:
        return skipped
    stored = _stored(symbol, "overview")
    if stored:
        raw, meta = stored
        profile = {"name": text(raw.get("name_en") or raw.get("name")), "industry": text(raw.get("industry") or raw.get("sic_description")), "sector": text(raw.get("sector")), "business_description": text(raw.get("description_en") or raw.get("description"), 6000)}
        profile.update({key: number(raw.get(key)) for key in ("market_cap", "pe_ratio", "dividend_yield", "year_high", "year_low")})
        currency = text(raw.get("currency"), 16)
        meta["data_through"] = raw.get("as_of")
    else:
        raw, meta = _info(symbol)
        profile = {"name": text(raw.get("longName") or raw.get("shortName")), "sector": text(raw.get("sector")), "industry": text(raw.get("industry")), "business_description": text(raw.get("longBusinessSummary"), 6000), "country": text(raw.get("country")), "website": text(raw.get("website"))}
        fields = {"market_cap": "marketCap", "pe_ratio": "trailingPE", "forward_pe": "forwardPE", "price_to_book": "priceToBook", "enterprise_value": "enterpriseValue", "dividend_yield": "dividendYield", "year_high": "fiftyTwoWeekHigh", "year_low": "fiftyTwoWeekLow", "beta": "beta", "shares_outstanding": "sharesOutstanding"}
        profile.update({key: number(raw.get(field)) for key, field in fields.items()})
        currency = text(raw.get("currency"), 16)
    if not any(value is not None for value in profile.values()):
        meta["status"] = "unavailable"
    return {**meta, "currency": currency, "units": {"market_cap": "currency units", "dividend_yield": "fraction"}, "profile": profile, "description_truncated": len(str(raw.get("longBusinessSummary") or raw.get("description_en") or raw.get("description") or "")) > 6000}


def price_history(symbol: str) -> dict[str, Any] | None:
    stored = _stored(symbol, "daily_chart")
    if stored is None:
        # Ordinary analysis must not start a second historical market-data pull.
        return None
    raw, meta = stored
    bars = [{key: number(row.get(key)) if key != "t" else row.get(key) for key in ("t", "o", "h", "l", "c", "v")} for row in (raw.get("bars") or [])[-250:] if isinstance(row, dict)]
    if not bars:
        return None
    stamp = number(bars[-1]["t"])
    # Existing chart contract uses Unix seconds; the date represents the bar,
    # not the instant this evidence pack was assembled.
    meta["data_through"] = datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None
    return {**meta, "range": "1y", "interval": "1d", "price_adjustment": raw.get("price_adjustment"), "currency": raw.get("currency"), "units": {"prices": "currency units", "volume": "shares", "t": "unix seconds"}, "bars": bars, "available_points": len(bars), "truncated": len(raw.get("bars") or []) > 250}


_STATEMENT_FIELDS = {
    "income": {"TotalRevenue": "revenue", "GrossProfit": "gross_profit", "OperatingIncome": "operating_income", "NetIncome": "net_income", "DilutedEPS": "eps_diluted", "BasicEPS": "eps_basic", "EBITDA": "ebitda"},
    "balance": {"TotalAssets": "total_assets", "TotalLiabilitiesNetMinorityInterest": "total_liabilities", "StockholdersEquity": "stockholders_equity", "CashAndCashEquivalents": "cash_and_equivalents", "TotalDebt": "total_debt"},
    "cashflow": {"OperatingCashFlow": "operating_cash_flow", "CapitalExpenditure": "capital_expenditure", "FreeCashFlow": "free_cash_flow"},
}


def financials(symbol: str) -> dict[str, Any]:
    skipped = _not_applicable(symbol, "financials")
    if skipped:
        return skipped
    result: dict[str, Any] = {**_base("Yahoo/yfinance", status="unavailable"), "currency": None, "units": {"monetary": "currency units", "eps": "currency per share"}, "quarterly": [], "annual": [], "component_status": {}, "component_as_of": {}}
    deadline = time.monotonic() + 6
    observed: list[str] = []
    periods: list[str] = []
    # Each statement is cached separately; failures preserve the other statements.
    for freq, count in (("quarterly", 8), ("yearly", 4)):
        rows: dict[str, dict[str, Any]] = {}
        for kind, mapping in _STATEMENT_FIELDS.items():
            key = f"{freq}_{kind}"
            if time.monotonic() >= deadline:
                result["component_status"][key] = "timeout"
                continue
            try:
                getter = {"income": "get_income_stmt", "balance": "get_balance_sheet", "cashflow": "get_cashflow"}[kind]
                frame, meta = _provider(symbol, key, lambda ticker, getter=getter, freq=freq: getattr(ticker, getter)(freq=freq))
                if frame is None or frame.empty:
                    result["component_status"][key] = "unavailable"
                    continue
                result["component_status"][key] = "ok"
                result["component_as_of"][key] = meta["as_of"]
                result["stale"] = result["stale"] or meta["stale"]
                if meta["as_of"]:
                    observed.append(meta["as_of"])
                for col in list(frame.columns)[:count]:
                    period = date_value(col)
                    if period is None:
                        continue
                    row = rows.setdefault(period, {"period_end": period})
                    for source, target in mapping.items():
                        row[target] = number(frame.at[source, col]) if source in frame.index else None
            except Exception as error:
                result["component_status"][key] = "timeout" if isinstance(error, TimeoutError) else "unavailable"
        selected = sorted(rows.values(), key=lambda row: row["period_end"], reverse=True)[:count]
        result["quarterly" if freq == "quarterly" else "annual"] = selected
        periods.extend(row["period_end"] for row in selected)
    result["as_of"] = max(observed) if observed else None
    result["data_through"] = max(periods) if periods else None
    if periods:
        result["status"] = "ok" if all(value == "ok" for value in result["component_status"].values()) else "partial"
    # Financial currency is independent from quote currency; never infer USD.
    try:
        if time.monotonic() >= deadline:
            return result
        info, _ = _info(symbol)
        result["currency"] = text(info.get("financialCurrency"), 16)
    except Exception:
        pass
    return result


def analyst_consensus(symbol: str) -> dict[str, Any]:
    skipped = _not_applicable(symbol, "analyst")
    if skipped:
        return skipped
    deadline = time.monotonic() + 6
    info, meta = _info(symbol)
    targets = {key: number(info.get(field)) for key, field in {"low": "targetLowPrice", "mean": "targetMeanPrice", "median": "targetMedianPrice", "high": "targetHighPrice"}.items()}
    result = {**meta, "currency": text(info.get("currency"), 16), "targets": targets, "recommendation": text(info.get("recommendationKey")), "recommendation_mean": number(info.get("recommendationMean")), "analyst_count": number(info.get("numberOfAnalystOpinions")), "eps_estimates": [], "revenue_estimates": [], "component_status": {}}
    for name, getter in (("eps_estimates", "get_earnings_estimate"), ("revenue_estimates", "get_revenue_estimate")):
        if time.monotonic() >= deadline:
            result["component_status"][name] = "timeout"
            continue
        try:
            frame, component_meta = _provider(symbol, name, lambda ticker, getter=getter: getattr(ticker, getter)())
            result["stale"] = result["stale"] or component_meta["stale"]
            result.setdefault("component_as_of", {})[name] = component_meta["as_of"]
            for period, row in frame.head(4).iterrows():
                result[name].append({"period": str(period)[:32], **{field: number(row.get(field)) for field in ("avg", "low", "high", "yearAgoEps", "yearAgoRevenue", "numberOfAnalysts", "growth")}})
            result["component_status"][name] = "ok" if result[name] else "unavailable"
        except Exception as error:
            result["component_status"][name] = "timeout" if isinstance(error, TimeoutError) else "unavailable"
    if not any(value is not None for value in targets.values()) and result["recommendation"] is None and not result["eps_estimates"] and not result["revenue_estimates"]:
        result["status"] = "unavailable"
    elif not all(value == "ok" for value in result["component_status"].values()):
        result["status"] = "partial"
    return result


def earnings_history(symbol: str) -> dict[str, Any]:
    skipped = _not_applicable(symbol, "earnings")
    if skipped:
        return skipped
    frame, meta = _provider(symbol, "earnings_history", lambda ticker: ticker.get_earnings_history())
    items = []
    if frame is not None:
        for period, row in frame.tail(12).iterrows():
            end = date_value(row.get("quarter")) or date_value(period)
            if end is None:
                continue
            items.append({"period_end": end, "eps_actual": number(row.get("epsActual")), "eps_estimate": number(row.get("epsEstimate")), "eps_difference": number(row.get("epsDifference")), "surprise_pct": number(row.get("surprisePercent")), "revenue_actual": number(row.get("revenueActual"))})
    return {**meta, "status": "ok" if items else "unavailable", "data_through": max((row["period_end"] for row in items), default=None), "currency": None, "units": {"eps": "currency per share", "surprise_pct": "provider fraction"}, "items": items}


BLOCK_LOADERS = {key: globals()[key] for key in RESEARCH_BLOCK_KEYS}
