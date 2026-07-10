from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from app.config import Settings, get_settings
from app.services import yahoo

PROVIDER = "Yahoo/yfinance"


def yahoo_options_is_enabled(settings: Settings | None = None) -> bool:
    cfg = settings or get_settings()
    return bool(cfg.yahoo_options_enabled) and int(cfg.yahoo_options_enrich_limit or 0) > 0


def _safe_float(value: Any, ndigits: int = 4) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number):
            return None
        return round(number, ndigits)
    except Exception:
        return None


def _clamp(value: float | int | None, lo: float = 0.0, hi: float = 100.0, default: float = 50.0) -> float:
    if value is None:
        return default
    try:
        number = float(value)
    except Exception:
        return default
    if not math.isfinite(number):
        return default
    return max(lo, min(hi, number))


def _sum(values: list[Any]) -> float:
    total = 0.0
    for value in values:
        number = _safe_float(value, 4)
        if number is not None and number > 0:
            total += number
    return total


def _weighted_average(values: list[Any], weights: list[Any]) -> float | None:
    numerator = 0.0
    denominator = 0.0
    for value, weight in zip(values, weights):
        number = _safe_float(value, 6)
        w = _safe_float(weight, 4) or 0.0
        if number is None or number <= 0 or w <= 0:
            continue
        numerator += number * w
        denominator += w
    if denominator <= 0:
        return None
    return round(numerator / denominator, 4)


def _parse_expiration(value: str, now: datetime | None = None) -> tuple[str, float] | None:
    try:
        expiry = yahoo.option_expiry_metrics(value, now=now)
    except (TypeError, ValueError):
        return None
    dte = float(expiry["dte"])
    if dte <= 0:
        return None
    return value, round(dte, 6)


def _pick_expiration(expirations: list[str], settings: Settings) -> tuple[str, float] | None:
    parsed = [item for value in expirations if (item := _parse_expiration(value))]
    if not parsed:
        return None

    target = int(settings.yahoo_option_target_dte or 30)
    min_dte = int(settings.yahoo_option_min_dte or 14)
    max_dte = int(settings.yahoo_option_max_dte or 60)
    preferred = [item for item in parsed if min_dte <= item[1] <= max_dte]
    candidates = preferred or parsed
    return min(candidates, key=lambda item: abs(item[1] - target))


def _sort_value(row: dict[str, Any], key: str) -> float:
    value = _safe_float(row.get(key), 4)
    return value if value is not None else float("-inf")


def _build_option_pool(rows: list[dict[str, Any]], limit: int, display_top: int) -> list[dict[str, Any]]:
    if limit >= len(rows):
        return list(rows)

    selected: dict[str, dict[str, Any]] = {}

    def add(row: dict[str, Any]) -> None:
        symbol = str(row.get("ticker") or "").upper()
        if symbol and len(selected) < limit:
            selected.setdefault(symbol, row)

    primary_quota = min(limit, max(display_top * 2, display_top, 20))
    for row in sorted(rows, key=lambda item: _sort_value(item, "final_score"), reverse=True)[:primary_quota]:
        add(row)

    buckets = [
        sorted(rows, key=lambda item: _sort_value(item, "score_short"), reverse=True),
        sorted(rows, key=lambda item: _sort_value(item, "score_mid"), reverse=True),
        sorted(rows, key=lambda item: _sort_value(item, "rel_volume"), reverse=True),
        sorted(rows, key=lambda item: _sort_value(item, "avg_dollar_volume_20d"), reverse=True),
        sorted(rows, key=lambda item: _sort_value(item, "return_5d"), reverse=True),
    ]
    cursor = 0
    while len(selected) < limit and cursor < len(rows):
        progressed = False
        for bucket in buckets:
            if cursor < len(bucket):
                before = len(selected)
                add(bucket[cursor])
                progressed = progressed or len(selected) > before
        if not progressed and cursor >= max(len(bucket) for bucket in buckets):
            break
        cursor += 1

    return list(selected.values())


def _contracts_near_price(contracts: list[dict[str, Any]], price: float | None, window_pct: float) -> list[dict[str, Any]]:
    if not price or price <= 0:
        return contracts
    lower = price * (1 - max(0.02, window_pct))
    upper = price * (1 + max(0.02, window_pct))
    filtered = [
        contract for contract in contracts
        if (strike := _safe_float(contract.get("strike"), 4)) is not None and lower <= strike <= upper
    ]
    return filtered or contracts


def _contract_side(contract: dict[str, Any]) -> str:
    return str(contract.get("type") or contract.get("side") or "").lower()


def _load_raw_metrics(row: dict[str, Any], settings: Settings) -> dict[str, Any] | None:
    symbol = str(row.get("ticker") or "").upper().strip()
    if not symbol:
        return None

    expiration_snapshot = yahoo.get_expirations_snapshot(symbol)
    expirations = expiration_snapshot.get("expirations") or []
    selected_expiration = _pick_expiration(expirations, settings)
    if not selected_expiration:
        return None

    expiration, dte = selected_expiration
    chain = yahoo.get_option_chain(symbol, expiration)
    calls = chain.get("calls") if isinstance(chain.get("calls"), list) else []
    puts = chain.get("puts") if isinstance(chain.get("puts"), list) else []
    contracts = calls + puts
    if not contracts:
        return None

    price = _safe_float(row.get("price"), 4) or _safe_float(chain.get("underlying_price"), 4)
    window = float(settings.yahoo_option_strike_window_pct or 0.16)
    near_contracts = _contracts_near_price(contracts, price, window)

    volumes = [contract.get("volume") for contract in near_contracts]
    open_interest = [contract.get("open_interest") for contract in near_contracts]
    iv_values = [contract.get("implied_volatility") for contract in near_contracts]
    weights = volumes if _sum(volumes) > 0 else open_interest

    call_contracts = [contract for contract in near_contracts if _contract_side(contract) == "call"]
    put_contracts = [contract for contract in near_contracts if _contract_side(contract) == "put"]
    call_volume = _sum([contract.get("volume") for contract in call_contracts])
    put_volume = _sum([contract.get("volume") for contract in put_contracts])
    call_oi = _sum([contract.get("open_interest") for contract in call_contracts])
    put_oi = _sum([contract.get("open_interest") for contract in put_contracts])
    total_volume = _sum(volumes)
    total_oi = _sum(open_interest)
    avg_iv = _weighted_average(iv_values, weights)

    premium_flow = 0.0
    for contract in near_contracts:
        volume = _safe_float(contract.get("volume"), 4) or 0.0
        option_price = (
            _safe_float(contract.get("last_price"), 4)
            or _safe_float(contract.get("mid"), 4)
            or _safe_float(contract.get("midpoint"), 4)
            or 0.0
        )
        if volume > 0 and option_price > 0:
            premium_flow += volume * option_price * 100

    alerts = chain.get("alerts") if isinstance(chain.get("alerts"), list) else []
    stale = bool(expiration_snapshot.get("_stale") or chain.get("_stale"))
    as_of_values = [
        str(value)
        for value in (expiration_snapshot.get("as_of"), chain.get("as_of"))
        if value
    ]
    return {
        "ticker": symbol,
        "expiration": expiration,
        "dte": dte,
        "contracts": len(near_contracts),
        "total_volume": int(total_volume),
        "total_open_interest": int(total_oi),
        "premium_flow": round(premium_flow, 0),
        "call_volume": int(call_volume),
        "put_volume": int(put_volume),
        "call_open_interest": int(call_oi),
        "put_open_interest": int(put_oi),
        "iv_average": avg_iv,
        "unusual_count": len(alerts),
        "underlying_price": price,
        "_stale": stale,
        "as_of": min(as_of_values) if as_of_values else None,
        "source_status": "stale" if stale else "active",
    }


def _pct_rank(metrics: list[dict[str, Any]], key: str) -> dict[str, float]:
    values = sorted((_safe_float(item.get(key), 6) for item in metrics if _safe_float(item.get(key), 6) is not None))
    if not values:
        return {}
    if len(values) == 1:
        return {item["ticker"]: 50.0 for item in metrics if _safe_float(item.get(key), 6) is not None}
    denom = max(len(values) - 1, 1)
    ranks: dict[str, float] = {}
    for item in metrics:
        value = _safe_float(item.get(key), 6)
        if value is None:
            continue
        below = sum(1 for candidate in values if candidate <= value)
        ranks[item["ticker"]] = round((below - 1) / denom * 100, 1)
    return ranks


def _score_metrics(raw_metrics: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    ranks = {
        "total_volume": _pct_rank(raw_metrics, "total_volume"),
        "total_open_interest": _pct_rank(raw_metrics, "total_open_interest"),
        "premium_flow": _pct_rank(raw_metrics, "premium_flow"),
        "iv_average": _pct_rank(raw_metrics, "iv_average"),
        "unusual_count": _pct_rank(raw_metrics, "unusual_count"),
    }
    scored: dict[str, dict[str, Any]] = {}
    for metrics in raw_metrics:
        ticker = metrics["ticker"]
        avg_iv = _safe_float(metrics.get("iv_average"), 6)
        call_volume = _safe_float(metrics.get("call_volume"), 4) or 0.0
        put_volume = _safe_float(metrics.get("put_volume"), 4) or 0.0
        total_volume = _safe_float(metrics.get("total_volume"), 4) or 0.0
        total_oi = _safe_float(metrics.get("total_open_interest"), 4) or 0.0
        volume_rank = ranks["total_volume"].get(ticker, 35.0 if total_volume else 25.0)
        oi_rank = ranks["total_open_interest"].get(ticker, 35.0 if total_oi else 25.0)
        premium_rank = ranks["premium_flow"].get(ticker, 35.0)
        option_pool_iv_rank = ranks["iv_average"].get(ticker, 50.0)
        unusual_rank = ranks["unusual_count"].get(ticker, 50.0)
        iv_abs_score = _clamp((avg_iv or 0.35) * 100 * 1.2, 20, 95)
        imbalance = abs(math.log((call_volume + 1) / (put_volume + 1)))
        imbalance_score = _clamp(50 + imbalance * 12, 50, 90)
        option_heat = (
            volume_rank * .32 +
            oi_rank * .22 +
            premium_rank * .18 +
            unusual_rank * .12 +
            iv_abs_score * .10 +
            imbalance_score * .06
        )
        if total_volume <= 0 and total_oi <= 0:
            option_heat = min(option_heat, 42.0)

        put_call_volume = round(put_volume / call_volume, 2) if call_volume > 0 else (None if put_volume == 0 else 99.0)
        put_call_oi = (
            round((_safe_float(metrics.get("put_open_interest"), 4) or 0.0) / (_safe_float(metrics.get("call_open_interest"), 4) or 1.0), 2)
            if (_safe_float(metrics.get("call_open_interest"), 4) or 0.0) > 0
            else None
        )
        iv_label = "高IV" if avg_iv is not None and avg_iv >= 0.65 else ("低IV" if avg_iv is not None and avg_iv <= 0.28 else "中性IV")
        scored[ticker] = {
            **metrics,
            "option_heat_score": round(_clamp(option_heat), 1),
            "atm_iv_percent": round(avg_iv * 100, 1) if avg_iv is not None else None,
            "option_pool_iv_rank": option_pool_iv_rank,
            # Historical IV rank/percentile is unavailable from a single chain snapshot.
            "iv_rank": None,
            "iv_percentile": None,
            "iv_label": iv_label,
            "put_call_volume": put_call_volume,
            "put_call_open_interest": put_call_oi,
            "source_status": metrics.get("source_status") or "active",
            "provider": PROVIDER,
            "confidence": "broad_screen",
            "provider_note": "Yahoo/yfinance 只提供期权链快照，无法判断真实买卖方向",
            "warning": None,
        }
    return scored


def _apply_metrics(row: dict[str, Any], metrics: dict[str, Any]) -> None:
    old_score = _safe_float(row.get("option_heat_score"), 1) or 50.0
    new_score = _safe_float(metrics.get("option_heat_score"), 1) or old_score
    option_weight = _safe_float(row.get("option_score_weight"), 4) or 0.06
    row["option_heat_score"] = new_score
    row["option_context"] = metrics
    row.setdefault("data_sources", {})["options"] = PROVIDER
    row.setdefault("breakdown", {})["option_heat"] = new_score
    if "final_score" in row:
        row["final_score"] = round(_clamp(float(row["final_score"]) + (new_score - old_score) * option_weight), 1)
        row["strength_score"] = row["final_score"]
    row["warnings"] = [w for w in (row.get("warnings") or []) if "期权热度" not in str(w)]
    if metrics.get("warning"):
        row.setdefault("warnings", []).append(str(metrics["warning"]))
    if new_score >= 68:
        row["tags"] = list(dict.fromkeys([*(row.get("tags") or []), "期权活跃"]))[:6]


def enrich_rows_with_yahoo_options(
    rows: list[dict[str, Any]],
    *,
    display_top: int,
    settings: Settings | None = None,
) -> dict[str, Any]:
    cfg = settings or get_settings()
    if not cfg.yahoo_options_enabled:
        return {
            "provider": PROVIDER,
            "status": "disabled",
            "configured": True,
            "enriched": 0,
            "message": "YAHOO_OPTIONS_ENABLED=false，Yahoo 期权粗筛已关闭",
        }

    if not rows:
        return {
            "provider": PROVIDER,
            "status": "skipped",
            "configured": True,
            "enriched": 0,
            "message": "基础筛选暂无候选，Yahoo 期权粗筛未运行",
        }

    configured_limit = max(0, int(cfg.yahoo_options_enrich_limit or 0))
    if configured_limit <= 0:
        return {
            "provider": PROVIDER,
            "status": "disabled",
            "configured": True,
            "enriched": 0,
            "message": "YAHOO_OPTIONS_ENRICH_LIMIT 为 0，Yahoo 期权粗筛已跳过",
        }
    display_cap = max(int(display_top * 1.5), display_top + 10, 20)
    limit = min(configured_limit, display_cap, len(rows))

    option_pool = _build_option_pool(rows, limit, display_top)
    raw_metrics: list[dict[str, Any]] = []
    failed = 0
    hard_failures = 0
    failure_limit = max(1, int(cfg.yahoo_options_failure_limit or 8))

    for row in option_pool:
        try:
            metrics = _load_raw_metrics(row, cfg)
            if metrics:
                raw_metrics.append(metrics)
            else:
                failed += 1
        except Exception:
            failed += 1
            hard_failures += 1
            if hard_failures >= failure_limit and not raw_metrics:
                break

    scored_metrics = _score_metrics(raw_metrics)
    enriched = 0
    for row in option_pool:
        symbol = str(row.get("ticker") or "").upper().strip()
        metrics = scored_metrics.get(symbol)
        if not metrics:
            continue
        _apply_metrics(row, metrics)
        enriched += 1

    status = "active" if enriched else "degraded"
    coverage = round(enriched / len(rows) * 100, 1) if rows else 0.0
    return {
        "provider": PROVIDER,
        "status": status,
        "configured": True,
        "enriched": enriched,
        "failed": failed,
        "candidate_pool": len(option_pool),
        "screened_count": len(rows),
        "coverage": coverage,
        "target_dte": int(cfg.yahoo_option_target_dte or 30),
        "strike_window_pct": float(cfg.yahoo_option_strike_window_pct or 0.16),
        "message": (
            f"Yahoo/yfinance 已对 {enriched}/{len(option_pool)} 个宽候选池标的完成期权粗筛"
            if enriched
            else "Yahoo/yfinance 期权粗筛未拿到可用期权链，期权热度已降级"
        ),
    }
