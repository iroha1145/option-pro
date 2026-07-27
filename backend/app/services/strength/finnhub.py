from __future__ import annotations

import math
import time
from typing import Any

import httpx

from app.config import Settings, get_settings

OPTION_DATA_SOURCE_CANDIDATES = [
    {
        "name": "MarketData.app",
        "url": "https://www.marketdata.app/docs/api/",
        "access": "Free Forever",
        "note": "100 daily credits; delayed options pricing; useful for option chain slices.",
    },
    {
        "name": "Tradier",
        "url": "https://docs.tradier.com/reference/brokerage-api-markets-get-options-chains.md",
        "access": "Developer account",
        "note": "Option chains, expirations, strikes, quotes, greeks/IV via ORATS.",
    },
    {
        "name": "tastytrade",
        "url": "https://tastytrade.com/api/",
        "access": "Brokerage login",
        "note": "Real-time quotes and option chains through the open API.",
    },
    {
        "name": "Alpha Vantage",
        "url": "https://www.alphavantage.co/documentation/",
        "access": "Free key / premium options",
        "note": "Options endpoints exist; full realtime/history is mostly premium.",
    },
]

_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_TTL_SECONDS = 60 * 60 * 6


def finnhub_is_enabled(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return bool((cfg.finnhub_api_key or "").strip())


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _metric_value(metrics: dict[str, Any], names: list[str]) -> float | None:
    for name in names:
        value = _safe_float(metrics.get(name), 4)
        if value is not None:
            return value
    return None


def _score_from_finnhub_metrics(metrics: dict[str, Any]) -> float | None:
    revenue_growth = _metric_value(metrics, ["revenueGrowthTTMYoy", "revenueGrowth3Y", "revenueGrowth5Y"])
    eps_growth = _metric_value(metrics, ["epsGrowthTTMYoy", "epsGrowth3Y", "epsGrowth5Y"])
    margin = _metric_value(metrics, ["netProfitMarginTTM", "netMarginTTM", "grossMarginTTM"])
    roe = _metric_value(metrics, ["roeTTM", "roeRfy"])
    current_ratio = _metric_value(metrics, ["currentRatioAnnual", "currentRatioQuarterly"])
    debt_to_equity = _metric_value(metrics, ["totalDebt/totalEquityAnnual", "totalDebt/totalEquityQuarterly"])

    if all(value is None for value in (revenue_growth, eps_growth, margin, roe, current_ratio, debt_to_equity)):
        return None

    score = 50.0
    if revenue_growth is not None:
        score += max(-10, min(16, revenue_growth * 0.55))
    if eps_growth is not None:
        score += max(-8, min(14, eps_growth * 0.35))
    if margin is not None:
        score += max(-6, min(12, margin * 0.45))
    if roe is not None:
        score += max(-5, min(12, roe * 0.35))
    if current_ratio is not None:
        score += 4 if current_ratio >= 1.5 else (-4 if current_ratio < 0.8 else 0)
    if debt_to_equity is not None:
        score += 4 if debt_to_equity <= 0.8 else (-6 if debt_to_equity > 2.5 else 0)
    return round(max(0.0, min(100.0, score)), 1)


def _cached_get(client: httpx.Client, url: str, params: dict[str, Any], cache_key: str) -> dict[str, Any]:
    now = time.time()
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    response = client.get(url, params=params)
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        data = {}
    _CACHE[cache_key] = (now + _TTL_SECONDS, data)
    return data


def _empty_status(status: str = "not_configured", message: str | None = None) -> dict[str, Any]:
    return {
        "provider": "Finnhub",
        "status": status,
        "configured": False,
        "enriched": 0,
        "message": message or "FINNHUB_API_KEY 未配置，基本面增强已跳过",
    }


def enrich_rows_with_finnhub(rows: list[dict[str, Any]], settings: Settings | None = None) -> dict[str, Any]:
    cfg = settings or get_settings()
    token = (cfg.finnhub_api_key or "").strip()
    if not token:
        return _empty_status()

    limit = max(0, min(int(cfg.finnhub_enrich_limit or 0), len(rows)))
    if limit <= 0:
        return _empty_status("disabled", "FINNHUB_ENRICH_LIMIT 为 0，已跳过 Finnhub 增强")

    base_url = str(cfg.finnhub_base_url).rstrip("/")
    timeout = min(float(cfg.request_timeout or 20.0), 8.0)
    enriched = 0
    failed = 0

    try:
        with httpx.Client(timeout=timeout, headers={"X-Finnhub-Token": token}) as client:
            for row in rows[:limit]:
                symbol = str(row.get("ticker") or "").upper().strip()
                if not symbol:
                    continue
                try:
                    payload = _cached_get(
                        client,
                        f"{base_url}/stock/metric",
                        {"symbol": symbol, "metric": "all"},
                        f"finnhub:metric:{symbol}",
                    )
                    metrics = payload.get("metric") if isinstance(payload.get("metric"), dict) else {}
                    if not metrics:
                        failed += 1
                        continue

                    quality_score = _score_from_finnhub_metrics(metrics)
                    row["finnhub_metrics"] = {
                        "market_cap": _metric_value(metrics, ["marketCapitalization"]),
                        "pe_ttm": _metric_value(metrics, ["peTTM", "peNormalizedAnnual"]),
                        "beta": _metric_value(metrics, ["beta"]),
                        "revenue_growth_ttm_yoy": _metric_value(metrics, ["revenueGrowthTTMYoy"]),
                        "eps_growth_ttm_yoy": _metric_value(metrics, ["epsGrowthTTMYoy"]),
                        "profit_margin_ttm": _metric_value(metrics, ["netProfitMarginTTM", "netMarginTTM"]),
                        "roe_ttm": _metric_value(metrics, ["roeTTM", "roeRfy"]),
                        "fundamental_quality_score": quality_score,
                    }
                    if quality_score is not None:
                        row["fundamental_score"] = quality_score
                        row.setdefault("breakdown", {})["fundamental"] = quality_score
                        # data_quality 是价格/量能因子的真实覆盖率（ranking
                        # confidence）。基本面补充不填补任何价格因子，禁止
                        # 借此把整行「数据质量」抬到高位掩盖缺失。
                    row.setdefault("data_sources", {})["fundamentals"] = "Finnhub"
                    enriched += 1
                except Exception:
                    failed += 1
    except Exception as exc:
        return {
            "provider": "Finnhub",
            "status": "degraded",
            "configured": True,
            "enriched": enriched,
            "failed": max(failed, 1),
            "message": f"Finnhub 请求失败，扫描已降级：{exc.__class__.__name__}",
        }

    status = "active" if enriched else "degraded"
    return {
        "provider": "Finnhub",
        "status": status,
        "configured": True,
        "enriched": enriched,
        "failed": failed,
        "message": "Finnhub 基本面增强已启用" if enriched else "Finnhub 已配置，但本次未拿到可用基本面",
    }
