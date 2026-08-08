from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timezone

import yfinance as yf
from fastapi import APIRouter, HTTPException, Request

from app.access import current_request_is_owner, public_snapshot_unavailable
from app.public_home_snapshot import (
    PUBLIC_HOME_INDEX_SYMBOLS,
    public_home_resource_parameters,
    read_owner_public_home_entry_async,
    read_public_home_resource_async,
)
from app.personal_config import get_personal_config
from app.services.cache import cache as _shared_cache
from app.services.http_read_cache import respond_with_snapshot, snapshot_version_key
from app.services.market_calendar import (
    ET,
    early_close_minutes as _early_close_minutes,
    is_trading_day as _is_trading_day,
    market_datetime as _market_datetime,
    market_holidays as _market_holidays,
    next_trading_day as _next_trading_day,
)

router = APIRouter(prefix="/api/market", tags=["market"])

# Symbols served by the lightweight /indices batch endpoint (ticker bar).
INDEX_SYMBOLS = list(PUBLIC_HOME_INDEX_SYMBOLS)


@router.get("/indices")
async def market_indices(request: Request):
    """Batch quote endpoint for the frontend index ticker bar.

    One request returns all index quotes via fast_info — the old path made the
    frontend call /api/stocks/{ticker} five times, each triggering yfinance's
    slow full `.info` scrape.
    """
    key = "market:indices"
    owner = current_request_is_owner()
    cache_control = "private, max-age=30, stale-while-revalidate=120"
    if not owner:
        # Anonymous readers get the published snapshot only. The fingerprint
        # cache makes this a stat-level read, and keeping the path off the
        # owner's process-cache key means a visitor can never observe the
        # owner's live rebuild before the worker publishes it.
        now = time.time()
        payload = await read_public_home_resource_async(
            "indices",
            parameters=public_home_resource_parameters("indices", now=now),
            now=now,
        )
        if payload is None:
            raise public_snapshot_unavailable(key)
        return await respond_with_snapshot(
            request,
            payload,
            version_key=snapshot_version_key(
                "indices", "public", payload.get("snapshot_saved_at")
            ),
            cache_control=cache_control,
        )
    config = get_personal_config()
    if config.access.mode == "password":
        # Owner ordinary reads follow the worker snapshot too (fresh or
        # stale-labelled); a cold API process must not fall into a provider
        # rebuild just because the owner opened the page. The live rebuild
        # stays available below only when no snapshot exists at all.
        now = time.time()
        interval = float(config.public_home.indices_seconds)
        disk_entry = await read_owner_public_home_entry_async(
            "indices",
            parameters=public_home_resource_parameters("indices", now=now),
            fresh_for_seconds=interval,
            now=now,
        )
        if disk_entry is not None:
            return await respond_with_snapshot(
                request,
                disk_entry["payload"],
                version_key=snapshot_version_key(
                    "indices",
                    "owner",
                    disk_entry["saved_at"],
                    bool(disk_entry["fresh"]),
                ),
                cache_control=cache_control,
            )
    owner_key = f"{key}:owner-live"
    return await _shared_cache.get_or_set(owner_key, 60, _build_indices)


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


@router.get("/cta")
async def market_cta(request: Request):
    """CTA 趋势资金代理估算（大盘分析页）。

    读取语义与 /market/indices 完全同款：匿名与普通账号只读 worker 发布的
    公开快照（指纹 ETag、绝不触发供应商请求）；password 模式下 owner 普通
    读也走磁盘快照，只有快照完全缺失时才现算兜底。
    """

    key = "market:cta"
    owner = current_request_is_owner()
    cache_control = "private, max-age=60, stale-while-revalidate=300"
    now = time.time()
    parameters = public_home_resource_parameters("cta_trend", now=now)
    if not owner:
        payload = await read_public_home_resource_async(
            "cta_trend", parameters=parameters, now=now,
        )
        if payload is None:
            raise public_snapshot_unavailable(key)
        return await respond_with_snapshot(
            request,
            payload,
            version_key=snapshot_version_key(
                "cta_trend", "public", payload.get("snapshot_saved_at")
            ),
            cache_control=cache_control,
        )
    config = get_personal_config()
    if config.access.mode == "password":
        disk_entry = await read_owner_public_home_entry_async(
            "cta_trend",
            parameters=parameters,
            fresh_for_seconds=float(config.public_home.cta_seconds),
            now=now,
        )
        if disk_entry is not None:
            return await respond_with_snapshot(
                request,
                disk_entry["payload"],
                version_key=snapshot_version_key(
                    "cta_trend",
                    "owner",
                    disk_entry["saved_at"],
                    bool(disk_entry["fresh"]),
                ),
                cache_control=cache_control,
            )
    return await _shared_cache.get_or_set("market:cta:owner-live", 300, _build_cta_trend)


async def _build_cta_trend():
    """构建 CTA 趋势资金快照（worker 定时调用；owner 无快照时兜底）。

    收盘语义：正式估算只吃已收盘日线；末根未收盘时另做「盘中暂定」标记，
    不进入正式仓位与历史。
    """

    from app.api import stocks
    from app.services.cta.config import INSTRUMENTS, METHOD_VERSION
    from app.services.cta.model import compute_cta_estimate, mark_intraday_crossings
    from app.services.technical.structure import _last_bar_closed

    now_dt = datetime.now(timezone.utc)

    async def _one(inst) -> dict:
        base = {
            "instrument": inst.key,
            "label": inst.label,
            "proxy_symbol": inst.proxy_symbol,
            "proxy_type": inst.proxy_type,
            "index_symbol": inst.index_symbol,
            "calculation_at": now_dt.isoformat(),
        }
        try:
            chart = await stocks._stock_chart_impl(inst.proxy_symbol, "1d", "raw")
        except Exception:
            chart = None
        bars = chart.get("bars") if isinstance(chart, dict) else None
        if not isinstance(bars, list) or not bars:
            return {
                **base,
                "source_status": "unavailable",
                "settlement_confirmed": None,
                "intraday": None,
                "coverage": {"bars": 0, "required": 0},
                "warnings": ["代理标的日线不可用"],
                "position_score": None,
                "previous_position_score": None,
                "flow_score": None,
                "trend_flow": None,
                "volatility_flow": None,
                "state": None,
                "position_label": None,
                "model_agreement": None,
                "submodels": None,
                "volatility": None,
                "trigger_levels": None,
                "scenario_curve": None,
                "history": [],
                "reference_price": None,
                "data_through": None,
            }
        settled = list(bars)
        intraday_bar = None
        try:
            last_epoch = int(settled[-1]["t"])
        except (KeyError, TypeError, ValueError):
            last_epoch = None
        if last_epoch is not None and not _last_bar_closed(last_epoch, now_dt):
            intraday_bar = settled.pop()
        estimate = await asyncio.to_thread(compute_cta_estimate, settled)
        intraday = None
        if intraday_bar is not None:
            try:
                intraday_price = float(intraday_bar["c"])
            except (KeyError, TypeError, ValueError):
                intraday_price = None
            intraday_day = (
                datetime.fromtimestamp(int(intraday_bar["t"]), tz=timezone.utc)
                .astimezone(ET)
                .date()
                .isoformat()
            )
            intraday = mark_intraday_crossings(estimate, intraday_price, intraday_day)
        return {
            **base,
            **estimate,
            "settlement_confirmed": estimate["source_status"] == "active",
            "intraday": intraday,
        }

    rows = list(await asyncio.gather(*[_one(inst) for inst in INSTRUMENTS]))
    active = sum(1 for row in rows if row.get("source_status") == "active")
    return {
        "method_version": METHOD_VERSION,
        "generated_at": now_dt.isoformat(),
        "proxy_note": "etf_trend_proxy",
        "source_status": (
            "active" if active == len(rows) else ("degraded" if active else "unavailable")
        ),
        "instruments": rows,
    }


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
