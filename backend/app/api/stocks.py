from __future__ import annotations

import asyncio
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import threading
import time
from typing import Annotated, Any, Optional
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import httpx
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.access import (
    current_request_is_owner,
    public_snapshot_unavailable,
    request_account_session,
    require_same_origin_json,
)
from app.data_paths import get_data_paths
from app.personal_config import get_personal_config
from app.public_home_snapshot import (
    PUBLIC_HOME_INDEX_SYMBOLS,
    PUBLIC_HOME_RESOURCE_SPECS,
    breakout_lead_chart_parameters,
    public_home_resource_parameters,
    read_owner_public_home_entry_async,
    read_public_home_resource,
    read_public_home_resource_async,
)
from app.services.yfinance_batch import download_in_bounded_batches
from app.services.market_calendar import early_close_minutes, is_trading_day
from app.services.symbols import quote_symbol
from app.services.watchlist_trend import daily_trend
from app.services.request_security import request_client_ip
from app.stock_pull_snapshot import (
    STOCK_PULL_RESOURCE_FRESH_SECONDS,
    read_stock_pull_resource,
    validate_stock_pull_payload,
    write_stock_pull_resources,
)


router = APIRouter(prefix="/api/stocks", tags=["stocks"])
logger = logging.getLogger(__name__)


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
_endpoint_lock_users: dict[str, int] = {}
_endpoint_refresh_tasks: dict[str, asyncio.Task[None]] = {}
_endpoint_refresh_retry_after: dict[str, float] = {}
_ENDPOINT_PURGE_THRESHOLD = 2048
_ENDPOINT_MAX_ENTRIES = 2048
_ENDPOINT_REFRESH_FAILURE_COOLDOWN_SECONDS = 60
_STOCK_PULL_BLOCKING_MAX_WORKERS = 2
_stock_pull_blocking_executor = ThreadPoolExecutor(
    max_workers=_STOCK_PULL_BLOCKING_MAX_WORKERS,
    thread_name_prefix="stock-pull",
)
_stock_pull_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_PUBLIC_STOCK_PULL_TICKER_COOLDOWN_SECONDS = 60
_PUBLIC_STOCK_PULL_CLIENT_WINDOW_SECONDS = 5 * 60
_PUBLIC_STOCK_PULL_CLIENT_LIMIT = 6
_PUBLIC_STOCK_PULL_MAX_CLIENTS = 2_048
_PUBLIC_STOCK_PULL_MAX_TICKERS = 8_192
_public_stock_pull_ticker_deadlines: dict[str, float] = {}
_public_stock_pull_recent: dict[str, deque[float]] = {}


def _prune_public_stock_pull_limits(now: float) -> None:
    for key in [
        key
        for key, deadline in _public_stock_pull_ticker_deadlines.items()
        if deadline <= now
    ]:
        _public_stock_pull_ticker_deadlines.pop(key, None)

    cutoff = now - _PUBLIC_STOCK_PULL_CLIENT_WINDOW_SECONDS
    for client_id, attempts in list(_public_stock_pull_recent.items()):
        while attempts and attempts[0] <= cutoff:
            attempts.popleft()
        if not attempts:
            _public_stock_pull_recent.pop(client_id, None)

    if len(_public_stock_pull_ticker_deadlines) > _PUBLIC_STOCK_PULL_MAX_TICKERS:
        overflow = len(_public_stock_pull_ticker_deadlines) - _PUBLIC_STOCK_PULL_MAX_TICKERS
        for key, _deadline in sorted(
            _public_stock_pull_ticker_deadlines.items(),
            key=lambda item: item[1],
        )[:overflow]:
            _public_stock_pull_ticker_deadlines.pop(key, None)
    if len(_public_stock_pull_recent) > _PUBLIC_STOCK_PULL_MAX_CLIENTS:
        overflow = len(_public_stock_pull_recent) - _PUBLIC_STOCK_PULL_MAX_CLIENTS
        for client_id, _attempts in sorted(
            _public_stock_pull_recent.items(),
            key=lambda item: item[1][-1] if item[1] else float("-inf"),
        )[:overflow]:
            _public_stock_pull_recent.pop(client_id, None)


def _reserve_public_stock_pull(client_id: str, ticker: str) -> None:
    """Reserve one real provider refresh before its first await."""

    now = time.monotonic()
    _prune_public_stock_pull_limits(now)
    deadline = _public_stock_pull_ticker_deadlines.get(ticker)
    if deadline is not None and deadline > now:
        retry_after = max(1, math.ceil(deadline - now))
        raise HTTPException(
            status_code=429,
            detail={
                "code": "stock_pull_cooldown",
                "message": f"{ticker} 刚刚更新过，请稍后再试",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    attempts = _public_stock_pull_recent.setdefault(client_id, deque())
    if len(attempts) >= _PUBLIC_STOCK_PULL_CLIENT_LIMIT:
        retry_after = max(
            1,
            math.ceil(
                attempts[0]
                + _PUBLIC_STOCK_PULL_CLIENT_WINDOW_SECONDS
                - now
            ),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "stock_pull_rate_limited",
                "message": "手动拉取过于频繁，请稍后再试",
                "retry_after_seconds": retry_after,
            },
            headers={"Retry-After": str(retry_after)},
        )

    attempts.append(now)
    _public_stock_pull_ticker_deadlines[ticker] = (
        now + _PUBLIC_STOCK_PULL_TICKER_COOLDOWN_SECONDS
    )


def _is_public_snapshot_unavailable(error: HTTPException) -> bool:
    detail = error.detail
    return bool(
        error.status_code == 503
        and isinstance(detail, dict)
        and detail.get("code") == "public_snapshot_unavailable"
    )

def _lock_for(key: str) -> asyncio.Lock:
    lock = _endpoint_locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _endpoint_locks[key] = lock
    _endpoint_lock_users[key] = _endpoint_lock_users.get(key, 0) + 1
    return lock

def _release_lock(key: str, lock: asyncio.Lock) -> None:
    remaining = _endpoint_lock_users.get(key, 1) - 1
    if remaining > 0:
        _endpoint_lock_users[key] = remaining
        return
    _endpoint_lock_users.pop(key, None)
    if key not in _endpoint_cache and _endpoint_locks.get(key) is lock:
        _endpoint_locks.pop(key, None)

def _maybe_purge_endpoint_cache(now: float) -> None:
    """Bound memory: per-ticker keys (stock:/chart:/logo:) accumulate forever
    in a long-lived process otherwise."""
    if len(_endpoint_cache) < _ENDPOINT_PURGE_THRESHOLD and len(_endpoint_locks) < _ENDPOINT_PURGE_THRESHOLD:
        return
    for key in [k for k, entry in _endpoint_cache.items() if entry.stale_until <= now]:
        _endpoint_cache.pop(key, None)
    if len(_endpoint_cache) >= _ENDPOINT_MAX_ENTRIES:
        remove_count = len(_endpoint_cache) - _ENDPOINT_MAX_ENTRIES + 1
        for key, _ in sorted(
            _endpoint_cache.items(),
            key=lambda item: item[1].stale_until,
        )[:remove_count]:
            _endpoint_cache.pop(key, None)
    for key in [k for k in _endpoint_locks if k not in _endpoint_cache and _endpoint_lock_users.get(k, 0) == 0]:
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


async def _reuse_fresh_public_home_entry(
    key: str,
    resource: str,
    parameters: dict[str, Any],
    *,
    fresh_for_seconds: float,
) -> Any | None:
    """Hydrate an owner cold cache from a newer exact worker snapshot."""

    if get_personal_config().access.mode != "password":
        return None
    now = time.time()
    current = _usable_hit(key, now)
    if current is not None and current.expires_at > now:
        return _cache_result(current, stale=False)
    disk_entry = await read_owner_public_home_entry_async(
        resource,
        parameters=parameters,
        fresh_for_seconds=fresh_for_seconds,
        now=now,
    )
    if disk_entry is None:
        return _cache_result(current, stale=True) if current is not None else None
    saved_at = float(disk_entry["saved_at"])
    if current is not None and current.fetched_at > saved_at:
        return _cache_result(current, stale=True)
    spec = PUBLIC_HOME_RESOURCE_SPECS[resource]
    hydrated = _EndpointCacheEntry(
        expires_at=saved_at + fresh_for_seconds,
        stale_until=saved_at + spec.max_age,
        fetched_at=saved_at,
        value=disk_entry["payload"],
    )
    _endpoint_cache[key] = hydrated
    return _cache_result(hydrated, stale=not bool(disk_entry["fresh"]))


async def _hydrate_stock_pull_resource(
    ticker: str,
    resource: str,
    key: str,
) -> _EndpointCacheEntry | None:
    """Hydrate the process cache from an explicit owner's durable snapshot."""

    now = time.time()
    current = _usable_hit(key, now)
    if current is not None and current.expires_at > now:
        return current
    saved = await asyncio.to_thread(
        read_stock_pull_resource,
        ticker,
        resource,
        now=now,
    )
    if saved is None:
        return current
    saved_at = float(saved["saved_at"])
    entry = _EndpointCacheEntry(
        expires_at=saved_at + STOCK_PULL_RESOURCE_FRESH_SECONDS[resource],
        stale_until=saved_at + int(saved["max_age"]),
        fetched_at=saved_at,
        value=saved["payload"],
    )
    if current is None or entry.fetched_at > current.fetched_at:
        _endpoint_cache[key] = entry
        return entry
    return current


async def _cached_endpoint(
    key: str,
    ttl: int,
    loader,
    *,
    stale_ttl: int | None = None,
    allow_refresh: bool = True,
):
    stale_ttl = ttl if stale_ttl is None else max(0, stale_ttl)
    now = time.time()
    hit = _usable_hit(key, now)
    if hit and hit.expires_at > now:
        return _cache_result(hit, stale=False)
    if not allow_refresh:
        if hit is not None:
            return _cache_result(hit, stale=True)
        raise public_snapshot_unavailable(key)
    # Serialize cold-cache fills per key
    lock = _lock_for(key)
    try:
        async with lock:
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
    finally:
        _release_lock(key, lock)


def _run_endpoint_success_callback(key: str, callback, value: Any, fetched_at: float) -> None:
    if callback is None:
        return
    try:
        callback(value, fetched_at)
    except Exception as exc:
        # Persistence is a recovery aid. A disk error must never turn a valid
        # market-data refresh into a failed API response.
        logger.warning("Endpoint success callback failed for %s: %s", key, exc)


async def _load_and_store_endpoint(
    key: str,
    ttl: int,
    max_age: int,
    loader,
    on_success=None,
) -> _EndpointCacheEntry:
    """Load one entry under its key lock and atomically publish the result."""
    lock = _lock_for(key)
    try:
        async with lock:
            now = time.time()
            hit = _usable_hit(key, now)
            if hit is not None and hit.expires_at > now:
                return hit

            value = await loader()
            fetched_at = time.time()
            _maybe_purge_endpoint_cache(fetched_at)
            entry = _EndpointCacheEntry(
                expires_at=fetched_at + ttl,
                stale_until=fetched_at + max(ttl, max_age),
                fetched_at=fetched_at,
                value=value,
            )
            _endpoint_cache[key] = entry
            _endpoint_refresh_retry_after.pop(key, None)
            _run_endpoint_success_callback(key, on_success, value, fetched_at)
            return entry
    finally:
        _release_lock(key, lock)


async def _force_replace_endpoint(
    key: str,
    ttl: int,
    max_age: int,
    loader,
) -> _EndpointCacheEntry:
    """Replace one cached market resource for an explicit owner pull.

    A normal GET intentionally serves a fresh value or bounded stale value.
    The manual action has different semantics: it must contact the configured
    providers even when the cache is fresh. Concurrent manual requests still
    share the first completed refresh so one click cannot fan out into a herd.
    """

    started_at = time.time()
    lock = _lock_for(key)
    try:
        async with lock:
            # Another refresh that started before us may have completed while
            # this request waited for the key lock. Reuse that newly fetched
            # provider result instead of immediately calling the provider again.
            hit = _usable_hit(key, time.time())
            if hit is not None and hit.fetched_at >= started_at:
                return hit

            value = await loader()
            fetched_at = time.time()
            _maybe_purge_endpoint_cache(fetched_at)
            entry = _EndpointCacheEntry(
                expires_at=fetched_at + ttl,
                stale_until=fetched_at + max(ttl, max_age),
                fetched_at=fetched_at,
                value=value,
            )
            _endpoint_cache[key] = entry
            _endpoint_refresh_retry_after.pop(key, None)
            return entry
    finally:
        _release_lock(key, lock)


async def _refresh_endpoint_in_background(
    key: str,
    ttl: int,
    max_age: int,
    loader,
    on_success=None,
) -> None:
    """Refresh one stale entry without making the requesting client wait."""
    await _load_and_store_endpoint(
        key,
        ttl,
        max_age,
        loader,
        on_success,
    )


def _finish_endpoint_refresh(key: str, task: asyncio.Task[None]) -> None:
    if _endpoint_refresh_tasks.get(key) is not task:
        if not task.cancelled():
            task.exception()
        return
    _endpoint_refresh_tasks.pop(key, None)
    if task.cancelled():
        return
    error = task.exception()
    if error is not None:
        _endpoint_refresh_retry_after[key] = (
            time.time() + _ENDPOINT_REFRESH_FAILURE_COOLDOWN_SECONDS
        )
        logger.warning("Background endpoint refresh failed for %s: %s", key, error)
    else:
        _endpoint_refresh_retry_after.pop(key, None)


def _schedule_endpoint_refresh(
    key: str,
    ttl: int,
    max_age: int,
    loader,
    on_success=None,
) -> bool:
    current = _endpoint_refresh_tasks.get(key)
    if current is not None:
        if not current.done():
            return True
        # A request may arrive after the task completed but before its queued
        # done callback ran. Process the result now so a fast failure cannot
        # slip through the cooldown and start another provider request.
        _finish_endpoint_refresh(key, current)
    now = time.time()
    retry_after = _endpoint_refresh_retry_after.get(key)
    if retry_after is not None:
        if retry_after > now:
            return False
        _endpoint_refresh_retry_after.pop(key, None)
    task = asyncio.create_task(
        _refresh_endpoint_in_background(
            key,
            ttl,
            max_age,
            loader,
            on_success,
        ),
        name=f"endpoint-refresh:{key}",
    )
    _endpoint_refresh_tasks[key] = task
    task.add_done_callback(
        lambda completed, refresh_key=key: _finish_endpoint_refresh(
            refresh_key,
            completed,
        )
    )
    return True


async def _stale_while_revalidate_endpoint(
    key: str,
    ttl: int,
    max_age: int,
    loader,
    on_success=None,
    *,
    allow_refresh: bool = True,
):
    """Return bounded stale data immediately while a single refresh runs."""
    max_age = max(ttl, max(0, max_age))
    now = time.time()
    hit = _usable_hit(key, now)
    if hit is not None and hit.expires_at > now:
        return _cache_result(hit, stale=False)
    if hit is not None:
        if not allow_refresh:
            result = _cache_result(hit, stale=True)
            if isinstance(result, dict):
                result["stale_reason"] = "public_snapshot_only"
                result["stale_age_seconds"] = round(
                    max(now - hit.fetched_at, 0.0),
                    1,
                )
            return result
        refreshing = _schedule_endpoint_refresh(
            key,
            ttl,
            max_age,
            loader,
            on_success,
        )
        result = _cache_result(hit, stale=True)
        if isinstance(result, dict):
            result["stale_reason"] = (
                "background_refresh_pending"
                if refreshing
                else "upstream_refresh_failed"
            )
            result["stale_age_seconds"] = round(max(now - hit.fetched_at, 0.0), 1)
        return result

    if not allow_refresh:
        raise public_snapshot_unavailable(key)

    # A cold request has no safe snapshot to serve, so it must wait for one
    # initial fill. Completion time starts both the fresh and bounded-stale
    # windows; concurrent cold requests share the same key lock.
    entry = await _load_and_store_endpoint(
        key,
        ttl,
        max_age,
        loader,
        on_success,
    )
    return _cache_result(entry, stale=False)


from app.services.utils import sanitize as _sanitize

_LOGO_MEDIA_TYPES = {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}
_LOGO_MAX_BYTES = 512 * 1024
_LOGO_NOT_FOUND_TTL = 60 * 60
_LOGO_SUCCESS_TTL = 24 * 60 * 60
_LOGO_NOT_FOUND = {"not_found": True}
_LOGO_ALLOWED_HOSTS = frozenset(
    {
        "financialmodelingprep.com",
        "static2.finnhub.io",
        "eodhd.com",
        "logo.clearbit.com",
    }
)
_LOGO_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,15}$")


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
    if (
        not _LOGO_TICKER_PATTERN.fullmatch(normalized)
        or normalized.endswith((".", "-"))
        or ".." in normalized
        or "--" in normalized
    ):
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
    async with httpx.AsyncClient(follow_redirects=False, timeout=6.0, headers=headers) as client:
        for url in _logo_urls(symbol):
            current = url
            for _redirect in range(4):
                if not _safe_logo_url(current):
                    break
                try:
                    async with client.stream("GET", current) as resp:
                        if resp.status_code in {301, 302, 303, 307, 308}:
                            location = resp.headers.get("location")
                            if not location:
                                break
                            current = urljoin(str(resp.url), location)
                            continue
                        media_type = (
                            resp.headers.get("content-type", "")
                            .split(";", 1)[0]
                            .strip()
                            .lower()
                        )
                        content_length = resp.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > _LOGO_MAX_BYTES:
                                    break
                            except ValueError:
                                pass
                        if resp.status_code != 200 or media_type not in _LOGO_MEDIA_TYPES:
                            break
                        chunks: list[bytes] = []
                        size = 0
                        async for chunk in resp.aiter_bytes():
                            size += len(chunk)
                            if size > _LOGO_MAX_BYTES:
                                chunks = []
                                break
                            chunks.append(chunk)
                        content = b"".join(chunks)
                        if len(content) > 64:
                            return {
                                "content": content,
                                "media_type": media_type,
                                "source": current,
                            }
                        break
                except Exception:
                    break
    raise HTTPException(status_code=404, detail="Company logo not found")


def _safe_logo_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            return False
        hostname = parsed.hostname.strip("[]").lower().rstrip(".")
        if hostname not in _LOGO_ALLOWED_HOSTS:
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return True
        return not (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
        )
    except Exception:
        return False


async def _cached_company_logo(
    symbol: str,
    *,
    allow_refresh: bool = True,
) -> dict[str, Any]:
    variants = _logo_symbol_variants(symbol)
    if not variants:
        raise HTTPException(status_code=404, detail="Invalid ticker")
    canonical_symbol = variants[0]
    key = f"logo:{canonical_symbol}"
    now = time.time()
    hit = _usable_hit(key, now)
    if hit is not None and hit.expires_at > now:
        if hit.value == _LOGO_NOT_FOUND:
            raise HTTPException(status_code=404, detail="Company logo not found")
        return hit.value
    if not allow_refresh:
        raise public_snapshot_unavailable(key)

    lock = _lock_for(key)
    try:
        async with lock:
            now = time.time()
            hit = _usable_hit(key, now)
            if hit is not None and hit.expires_at > now:
                if hit.value == _LOGO_NOT_FOUND:
                    raise HTTPException(status_code=404, detail="Company logo not found")
                return hit.value
            try:
                value = await _fetch_company_logo(canonical_symbol)
                ttl = _LOGO_SUCCESS_TTL
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                value = dict(_LOGO_NOT_FOUND)
                ttl = _LOGO_NOT_FOUND_TTL
            _maybe_purge_endpoint_cache(now)
            _endpoint_cache[key] = _EndpointCacheEntry(
                expires_at=now + ttl,
                stale_until=now + ttl,
                fetched_at=now,
                value=value,
            )
            if value == _LOGO_NOT_FOUND:
                raise HTTPException(status_code=404, detail="Company logo not found")
            return value
    finally:
        _release_lock(key, lock)


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


_STOCK_DIRECTORY_VERSION = 1
_STOCK_DIRECTORY_CACHE_KEY = "stock-symbol-directory"
_STOCK_DIRECTORY_FRESH_TTL_SECONDS = 24 * 60 * 60
_STOCK_DIRECTORY_MAX_AGE_SECONDS = 45 * 24 * 60 * 60
_STOCK_DIRECTORY_MAX_BYTES = 16 * 1024 * 1024
_STOCK_DIRECTORY_MAX_RECORDS = 50_000
_STOCK_DIRECTORY_PATH = (
    get_data_paths().watchlist_snapshot.parent / "stock-symbol-directory-v1.json"
)
_STOCK_DIRECTORY_TICKER_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,31}$")
_stock_directory_snapshot_observed: tuple[str, tuple[int, int, int]] | None = None


def _clean_stock_directory_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    items = value.get("items")
    if (
        not isinstance(items, list)
        or not items
        or len(items) > _STOCK_DIRECTORY_MAX_RECORDS
    ):
        return None

    cleaned_items: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, dict):
            return None
        ticker = raw.get("ticker")
        name = raw.get("name")
        market = str(raw.get("market") or "").strip().lower()
        locale = str(raw.get("locale") or "").strip().lower()
        if (
            not isinstance(ticker, str)
            or not _STOCK_DIRECTORY_TICKER_PATTERN.fullmatch(ticker)
            or ticker in seen
            or not isinstance(name, str)
            or not name.strip()
            or raw.get("active") is not True
            or market != "stocks"
            or locale != "us"
        ):
            return None
        seen.add(ticker)
        cleaned_items.append(
            {
                "ticker": ticker,
                "name": name.strip(),
                "market": market,
                "type": str(raw.get("type") or ""),
                "primary_exchange": str(raw.get("primary_exchange") or ""),
                "locale": locale,
                "currency_symbol": str(raw.get("currency_symbol") or ""),
                "active": True,
            }
        )
    return {
        "items": cleaned_items,
        "count": len(cleaned_items),
        "provider": "Massive",
    }


def _read_stock_directory_snapshot(
    path: Path,
    *,
    now: float,
) -> _EndpointCacheEntry | None:
    """Read the bounded provider symbol directory without following symlinks."""

    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_STOCK_DIRECTORY_MAX_BYTES + 1)
        if not raw or len(raw) > _STOCK_DIRECTORY_MAX_BYTES:
            return None
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
        if (
            not isinstance(document, dict)
            or document.get("version") != _STOCK_DIRECTORY_VERSION
        ):
            return None
        saved_at = document.get("saved_at")
        if (
            isinstance(saved_at, bool)
            or not isinstance(saved_at, (int, float))
            or not math.isfinite(float(saved_at))
        ):
            return None
        saved_at = float(saved_at)
        if (
            saved_at <= 0
            or saved_at > now
            or saved_at + _STOCK_DIRECTORY_MAX_AGE_SECONDS <= now
        ):
            return None
        payload = _clean_stock_directory_payload(document.get("payload"))
        if payload is None:
            return None
        return _EndpointCacheEntry(
            expires_at=saved_at + _STOCK_DIRECTORY_FRESH_TTL_SECONDS,
            stale_until=saved_at + _STOCK_DIRECTORY_MAX_AGE_SECONDS,
            fetched_at=saved_at,
            value=payload,
        )
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None


def _write_stock_directory_snapshot(
    path: Path,
    *,
    payload: Any,
    saved_at: float,
) -> None:
    cleaned = _clean_stock_directory_payload(payload)
    if cleaned is None:
        raise ValueError("stock symbol directory payload is invalid")
    if not math.isfinite(saved_at) or saved_at <= 0:
        raise ValueError("stock symbol directory saved_at is invalid")
    encoded = json.dumps(
        {
            "version": _STOCK_DIRECTORY_VERSION,
            "saved_at": saved_at,
            "payload": cleaned,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _STOCK_DIRECTORY_MAX_BYTES:
        raise ValueError("stock symbol directory exceeds the size limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _stock_directory_snapshot_identity(path: Path) -> tuple[int, int, int]:
    try:
        item = path.lstat()
    except FileNotFoundError:
        return (0, 0, 0)
    except OSError:
        return (-1, 0, 0)
    return (int(item.st_ino), int(item.st_mtime_ns), int(item.st_size))


def _load_stock_directory_snapshot(now: float) -> None:
    global _stock_directory_snapshot_observed

    path_key = str(_STOCK_DIRECTORY_PATH)
    observed = (
        path_key,
        _stock_directory_snapshot_identity(_STOCK_DIRECTORY_PATH),
    )
    if observed == _stock_directory_snapshot_observed:
        return
    _stock_directory_snapshot_observed = observed
    entry = _read_stock_directory_snapshot(_STOCK_DIRECTORY_PATH, now=now)
    if entry is None:
        return
    current = _endpoint_cache.get(_STOCK_DIRECTORY_CACHE_KEY)
    if current is None or entry.fetched_at > current.fetched_at:
        _endpoint_cache[_STOCK_DIRECTORY_CACHE_KEY] = entry


def _persist_stock_directory(payload: Any, saved_at: float) -> None:
    _write_stock_directory_snapshot(
        _STOCK_DIRECTORY_PATH,
        payload=payload,
        saved_at=saved_at,
    )


async def _build_stock_directory() -> dict[str, Any]:
    from app.services import massive as massive_provider

    if not massive_provider.configured():
        raise massive_provider.MassiveError(
            "MASSIVE_API_KEY is not configured",
            code="not_configured",
        )
    items = await asyncio.to_thread(massive_provider.reference_tickers)
    payload = {
        "items": items,
        "count": len(items),
        "provider": "Massive",
    }
    if _clean_stock_directory_payload(payload) is None:
        raise RuntimeError("Massive returned an incomplete stock symbol directory")
    return payload


async def _stock_directory(*, allow_refresh: bool) -> dict[str, Any] | None:
    now = time.time()
    _load_stock_directory_snapshot(now)
    try:
        return await _stale_while_revalidate_endpoint(
            _STOCK_DIRECTORY_CACHE_KEY,
            _STOCK_DIRECTORY_FRESH_TTL_SECONDS,
            _STOCK_DIRECTORY_MAX_AGE_SECONDS,
            _build_stock_directory,
            _persist_stock_directory,
            allow_refresh=allow_refresh,
        )
    except HTTPException as exc:
        if _is_public_snapshot_unavailable(exc):
            return None
        raise
    except Exception:
        hit = _usable_hit(_STOCK_DIRECTORY_CACHE_KEY, time.time())
        return _cache_result(hit, stale=True) if hit is not None else None


async def _refresh_stock_directory() -> dict[str, Any]:
    """Wait for a fresh directory and surface provider failures to the worker.

    Search requests may serve a bounded stale snapshot while refreshing in the
    background.  The daily maintenance task has a different contract: it must
    not report success until the provider refresh has completed and the new
    snapshot has been persisted.
    """

    now = time.time()
    _load_stock_directory_snapshot(now)
    background = _endpoint_refresh_tasks.get(_STOCK_DIRECTORY_CACHE_KEY)
    if background is not None:
        await background
    entry = await _load_and_store_endpoint(
        _STOCK_DIRECTORY_CACHE_KEY,
        _STOCK_DIRECTORY_FRESH_TTL_SECONDS,
        _STOCK_DIRECTORY_MAX_AGE_SECONDS,
        _build_stock_directory,
    )
    # The shared endpoint cache deliberately treats persistence as best-effort.
    # The directory worker is the durability boundary, so its write must be
    # awaited and any disk failure must reach the supervisor.
    await asyncio.to_thread(
        _persist_stock_directory,
        entry.value,
        entry.fetched_at,
    )
    return _cache_result(entry, stale=False)


_WATCHLIST_MAX_TICKERS = 100
_WATCHLIST_QUERY_MAX_LENGTH = 4096
_WATCHLIST_FRESH_TTL_SECONDS = 5 * 60
_WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS = 24 * 60 * 60
_WATCHLIST_TARGETED_STALE_TTL_SECONDS = 15 * 60
_WATCHLIST_LATEST_INTERVAL = "5m"
_WATCHLIST_MARKET_TIMEZONE = ZoneInfo("America/New_York")
_WATCHLIST_SYMBOL_TIMEZONES = {
    "^N225": "Asia/Tokyo",
    "^HSI": "Asia/Hong_Kong",
    "^FTSE": "Europe/London",
    "^GDAXI": "Europe/Berlin",
    "^FCHI": "Europe/Paris",
    "^STOXX50E": "Europe/Berlin",
    "^AXJO": "Australia/Sydney",
    "^KS11": "Asia/Seoul",
    "^TWII": "Asia/Taipei",
    "^BSESN": "Asia/Kolkata",
    "^NSEI": "Asia/Kolkata",
}
_WATCHLIST_SUFFIX_TIMEZONES = (
    (".TWO", "Asia/Taipei"),
    (".HK", "Asia/Hong_Kong"),
    (".SS", "Asia/Shanghai"),
    (".SZ", "Asia/Shanghai"),
    (".TW", "Asia/Taipei"),
    (".KS", "Asia/Seoul"),
    (".KQ", "Asia/Seoul"),
    (".AX", "Australia/Sydney"),
    (".NZ", "Pacific/Auckland"),
    (".SI", "Asia/Singapore"),
    (".NS", "Asia/Kolkata"),
    (".BO", "Asia/Kolkata"),
    (".JK", "Asia/Jakarta"),
    (".KL", "Asia/Kuala_Lumpur"),
    (".BK", "Asia/Bangkok"),
    (".VN", "Asia/Ho_Chi_Minh"),
    (".PA", "Europe/Paris"),
    (".AS", "Europe/Amsterdam"),
    (".BR", "Europe/Brussels"),
    (".DE", "Europe/Berlin"),
    (".MI", "Europe/Rome"),
    (".MC", "Europe/Madrid"),
    (".SW", "Europe/Zurich"),
    (".ST", "Europe/Stockholm"),
    (".OL", "Europe/Oslo"),
    (".CO", "Europe/Copenhagen"),
    (".HE", "Europe/Helsinki"),
    (".VI", "Europe/Vienna"),
    (".IR", "Europe/Dublin"),
    (".LS", "Europe/Lisbon"),
    (".L", "Europe/London"),
    (".T", "Asia/Tokyo"),
    (".TO", "America/Toronto"),
    (".V", "America/Toronto"),
    (".SA", "America/Sao_Paulo"),
    (".MX", "America/Mexico_City"),
)
_WATCHLIST_PROVIDER_CLOSE_WORKERS = 4
_WATCHLIST_PROVIDER_CLOSE_BATCH_TIMEOUT_SECONDS = 12.0
_WATCHLIST_PROVIDER_CLOSE_SUCCESS_TTL_SECONDS = 5 * 60
_WATCHLIST_PROVIDER_CLOSE_FAILURE_TTL_SECONDS = 30
_WATCHLIST_PROVIDER_CLOSE_CACHE_MAX_ENTRIES = 512
_watchlist_provider_close_executor = ThreadPoolExecutor(
    max_workers=_WATCHLIST_PROVIDER_CLOSE_WORKERS,
    thread_name_prefix="watchlist-previous-close",
)
_watchlist_provider_close_lock = threading.RLock()
_watchlist_provider_close_cache: dict[str, tuple[float, float | None]] = {}
_watchlist_provider_close_inflight: dict[str, Any] = {}
_WATCHLIST_SNAPSHOT_VERSION = 1
_WATCHLIST_SNAPSHOT_MAX_BYTES = 2 * 1024 * 1024
_WATCHLIST_SNAPSHOT_PATH = get_data_paths().watchlist_snapshot
_WATCHLIST_SNAPSHOT_PARAMETERS = {"tickers": None}
_WATCHLIST_SNAPSHOT_TRANSPORT_FIELDS = frozenset(
    {
        "_stale",
        "as_of",
        "source_status",
        "stale_age_seconds",
        "stale_reason",
    }
)
_watchlist_snapshot_load_attempted = False
_watchlist_snapshot_observed: tuple[str, tuple[int, int, int]] | None = None
# Owner-path identity gate: (path, file identity, snapshot fetched_at).
_watchlist_owner_snapshot_observed: tuple[str, tuple[int, int, int], float] | None = None
_WATCHLIST_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$"
)


def _watchlist_market_timezone(ticker: str) -> ZoneInfo:
    explicit = _WATCHLIST_SYMBOL_TIMEZONES.get(ticker)
    if explicit:
        return ZoneInfo(explicit)
    if ticker.endswith("=F"):
        return ZoneInfo("America/Chicago")
    for suffix, timezone_name in _WATCHLIST_SUFFIX_TIMEZONES:
        if ticker.endswith(suffix):
            return ZoneInfo(timezone_name)
    return _WATCHLIST_MARKET_TIMEZONE


def _watchlist_requires_provider_previous_close(ticker: str) -> bool:
    # Index and futures daily bars can skip sessions or use a settlement value
    # that differs from Yahoo's published previous-close baseline.
    return ticker.startswith("^") or ticker.endswith("=F")


def _fetch_watchlist_provider_previous_close(
    ticker: str,
    *,
    session: Any,
) -> float | None:
    try:
        instrument = yf.Ticker(ticker, session=session)
        instrument.history(
            period="1d",
            interval=_WATCHLIST_LATEST_INTERVAL,
            prepost=True,
            actions=False,
            auto_adjust=False,
            timeout=10,
        )
        metadata = instrument.history_metadata or {}
        for key in ("previousClose", "chartPreviousClose"):
            value = metadata.get(key)
            try:
                previous_close = float(value)
            except (TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(previous_close) and previous_close > 0:
                return previous_close
    except Exception:
        pass
    return None


def _cache_watchlist_provider_previous_close(ticker: str, future: Any) -> None:
    try:
        previous_close = future.result()
    except Exception:
        previous_close = None
    now = time.monotonic()
    ttl = (
        _WATCHLIST_PROVIDER_CLOSE_SUCCESS_TTL_SECONDS
        if previous_close is not None
        else _WATCHLIST_PROVIDER_CLOSE_FAILURE_TTL_SECONDS
    )
    with _watchlist_provider_close_lock:
        if _watchlist_provider_close_inflight.get(ticker) is future:
            _watchlist_provider_close_inflight.pop(ticker, None)
        _watchlist_provider_close_cache[ticker] = (now + ttl, previous_close)
        if len(_watchlist_provider_close_cache) > _WATCHLIST_PROVIDER_CLOSE_CACHE_MAX_ENTRIES:
            remove_count = (
                len(_watchlist_provider_close_cache)
                - _WATCHLIST_PROVIDER_CLOSE_CACHE_MAX_ENTRIES
            )
            for expired_ticker, _ in sorted(
                _watchlist_provider_close_cache.items(),
                key=lambda item: item[1][0],
            )[:remove_count]:
                _watchlist_provider_close_cache.pop(expired_ticker, None)


def _fetch_watchlist_provider_previous_closes(
    tickers: list[str],
    *,
    session: Any,
) -> dict[str, float]:
    if not tickers:
        return {}

    unique_tickers = list(dict.fromkeys(tickers))
    deadline = time.monotonic() + _WATCHLIST_PROVIDER_CLOSE_BATCH_TIMEOUT_SECONDS
    previous_closes: dict[str, float] = {}
    waiting: dict[str, Any] = {}
    pending: list[str] = []

    now = time.monotonic()
    with _watchlist_provider_close_lock:
        for ticker in unique_tickers:
            cached = _watchlist_provider_close_cache.get(ticker)
            if cached is not None and cached[0] > now:
                if cached[1] is not None:
                    previous_closes[ticker] = cached[1]
                continue
            if cached is not None:
                _watchlist_provider_close_cache.pop(ticker, None)
            future = _watchlist_provider_close_inflight.get(ticker)
            if future is not None:
                waiting[ticker] = future
            else:
                pending.append(ticker)

    while pending or waiting:
        with _watchlist_provider_close_lock:
            available_slots = max(
                0,
                _WATCHLIST_PROVIDER_CLOSE_WORKERS
                - len(_watchlist_provider_close_inflight),
            )
            for ticker in pending[:available_slots]:
                future = _watchlist_provider_close_executor.submit(
                    _fetch_watchlist_provider_previous_close,
                    ticker,
                    session=session,
                )
                _watchlist_provider_close_inflight[ticker] = future
                waiting[ticker] = future
                future.add_done_callback(
                    lambda completed, symbol=ticker: _cache_watchlist_provider_previous_close(
                        symbol,
                        completed,
                    )
                )
            if available_slots:
                del pending[:available_slots]

        if not waiting:
            # Other requests own all global worker slots. Returning the cached
            # subset keeps this request bounded; missing symbols fail closed.
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        done, _ = wait(
            set(waiting.values()),
            timeout=remaining,
            return_when=FIRST_COMPLETED,
        )
        if not done:
            break
        for ticker, future in list(waiting.items()):
            if future not in done:
                continue
            try:
                previous_close = future.result()
            except Exception:
                previous_close = None
            # Do not rely on the worker-thread callback to free the global
            # slot. The waiter can observe a completed future before its
            # callback runs and would otherwise stop with pending symbols.
            _cache_watchlist_provider_previous_close(ticker, future)
            if previous_close is not None:
                previous_closes[ticker] = previous_close
            waiting.pop(ticker, None)
    return previous_closes


def _is_finite_json_tree(value: Any, *, depth: int = 0) -> bool:
    if depth > 64:
        return False
    if value is None or isinstance(value, (bool, str, int)):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, list):
        return all(_is_finite_json_tree(item, depth=depth + 1) for item in value)
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and _is_finite_json_tree(item, depth=depth + 1)
            for key, item in value.items()
        )
    return False


def _is_finite_number(value: Any, *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return False
    return math.isfinite(number) and (not positive or number > 0)


def _clean_watchlist_snapshot_payload(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    groups = value.get("groups")
    succeeded = value.get("succeeded")
    if not isinstance(groups, list) or not groups:
        return None
    if (
        isinstance(succeeded, bool)
        or not isinstance(succeeded, int)
        or succeeded <= 0
    ):
        return None
    cleaned = {
        key: item
        for key, item in value.items()
        if key not in _WATCHLIST_SNAPSHOT_TRANSPORT_FIELDS
    }
    if not _is_finite_json_tree(cleaned):
        return None

    group_ids: set[str] = set()
    tickers: set[str] = set()
    for group in groups:
        if not isinstance(group, dict):
            return None
        group_id = group.get("id")
        group_name = group.get("name")
        stocks = group.get("stocks")
        if (
            not isinstance(group_id, str)
            or not group_id.strip()
            or group_id in group_ids
            or not isinstance(group_name, str)
            or not group_name.strip()
            or not isinstance(stocks, list)
            or not stocks
        ):
            return None
        group_ids.add(group_id)
        group_tickers: set[str] = set()
        for stock in stocks:
            if not isinstance(stock, dict):
                return None
            ticker = stock.get("ticker")
            name = stock.get("name")
            spark = stock.get("spark")
            if (
                not isinstance(ticker, str)
                or not _WATCHLIST_TICKER_PATTERN.fullmatch(ticker)
                or ticker in group_tickers
                or not isinstance(name, str)
                or not name.strip()
                or not _is_finite_number(stock.get("price"), positive=True)
                or not _is_finite_number(stock.get("change_percent"))
                or not isinstance(spark, list)
                or not spark
                or len(spark) > 7
                or not all(_is_finite_number(point, positive=True) for point in spark)
            ):
                return None
            group_tickers.add(ticker)
            tickers.add(ticker)
    if len(tickers) != succeeded:
        return None
    return cleaned


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _read_watchlist_snapshot(
    path: Path,
    *,
    now: float,
) -> _EndpointCacheEntry | None:
    """Read a bounded restart snapshot, failing closed on any anomaly."""
    try:
        if path.is_symlink():
            return None
        if not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_WATCHLIST_SNAPSHOT_MAX_BYTES + 1)
        if not raw or len(raw) > _WATCHLIST_SNAPSHOT_MAX_BYTES:
            return None
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
        if not isinstance(document, dict):
            return None
        version = document.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != _WATCHLIST_SNAPSHOT_VERSION
        ):
            return None
        if document.get("parameters") != _WATCHLIST_SNAPSHOT_PARAMETERS:
            return None
        saved_at = document.get("saved_at")
        if (
            isinstance(saved_at, bool)
            or not isinstance(saved_at, (int, float))
            or not math.isfinite(float(saved_at))
        ):
            return None
        saved_at = float(saved_at)
        if (
            saved_at <= 0
            or saved_at > now
            or saved_at + _WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS <= now
        ):
            return None
        payload = _clean_watchlist_snapshot_payload(document.get("payload"))
        if payload is None:
            return None
        return _EndpointCacheEntry(
            # A restart snapshot is deliberately stale even when recently
            # saved, so the first request schedules a live refresh.
            expires_at=now,
            stale_until=saved_at + _WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS,
            fetched_at=saved_at,
            value=payload,
        )
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None


def _write_watchlist_snapshot(
    path: Path,
    *,
    payload: Any,
    saved_at: float,
) -> None:
    cleaned = _clean_watchlist_snapshot_payload(payload)
    if cleaned is None:
        raise ValueError("watchlist snapshot payload is incomplete")
    if not math.isfinite(saved_at) or saved_at <= 0:
        raise ValueError("watchlist snapshot saved_at is invalid")
    encoded = json.dumps(
        {
            "version": _WATCHLIST_SNAPSHOT_VERSION,
            "saved_at": saved_at,
            "parameters": _WATCHLIST_SNAPSHOT_PARAMETERS,
            "payload": cleaned,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _WATCHLIST_SNAPSHOT_MAX_BYTES:
        raise ValueError("watchlist snapshot exceeds the size limit")

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _persist_watchlist_snapshot(payload: Any, saved_at: float) -> None:
    _write_watchlist_snapshot(
        _WATCHLIST_SNAPSHOT_PATH,
        payload=payload,
        saved_at=saved_at,
    )


def _watchlist_snapshot_identity(path: Path) -> tuple[int, int, int]:
    try:
        item = path.lstat()
    except FileNotFoundError:
        return (0, 0, 0)
    except OSError:
        return (-1, 0, 0)
    return (int(item.st_ino), int(item.st_mtime_ns), int(item.st_size))


def _load_watchlist_snapshot_once(now: float) -> None:
    """Load a newer worker snapshot without relabeling old memory as fresh."""

    global _watchlist_snapshot_load_attempted, _watchlist_snapshot_observed
    path_key = str(_WATCHLIST_SNAPSHOT_PATH)
    # Some callers deliberately disable disk loading after seeding an in-memory
    # cache. Preserve that contract when no observation exists for this path.
    if _watchlist_snapshot_load_attempted and (
        _watchlist_snapshot_observed is None
        or _watchlist_snapshot_observed[0] != path_key
    ):
        return
    identity = _watchlist_snapshot_identity(_WATCHLIST_SNAPSHOT_PATH)
    observed = (path_key, identity)
    if _watchlist_snapshot_load_attempted and observed == _watchlist_snapshot_observed:
        return
    _watchlist_snapshot_load_attempted = True
    _watchlist_snapshot_observed = observed
    entry = _read_watchlist_snapshot(_WATCHLIST_SNAPSHOT_PATH, now=now)
    if entry is None:
        return
    current = _endpoint_cache.get("watchlist")
    if current is None or entry.fetched_at > current.fetched_at:
        _endpoint_cache["watchlist"] = entry


def _load_watchlist_snapshot_for_owner(now: float) -> _EndpointCacheEntry | None:
    """Reuse a worker generation without launching the same Yahoo batch."""

    global _watchlist_owner_snapshot_observed
    config = get_personal_config()
    if config.access.mode != "password":
        return None
    interval = float(config.public_home.watchlist_seconds)
    current = _usable_hit("watchlist", now)
    # Identity gate (same as the visitor path): while the snapshot file is
    # unchanged and the memory entry already reflects that generation or a
    # newer live refresh, skip the full read+validate.
    path_key = str(_WATCHLIST_SNAPSHOT_PATH)
    identity = _watchlist_snapshot_identity(_WATCHLIST_SNAPSHOT_PATH)
    observed = _watchlist_owner_snapshot_observed
    if (
        observed is not None
        and observed[0] == path_key
        and observed[1] == identity
        and current is not None
        and current.fetched_at >= observed[2]
    ):
        return current
    entry = _read_watchlist_snapshot(_WATCHLIST_SNAPSHOT_PATH, now=now)
    if entry is None:
        return None
    _watchlist_owner_snapshot_observed = (path_key, identity, entry.fetched_at)
    entry.expires_at = entry.fetched_at + interval
    if current is None or entry.fetched_at > current.fetched_at or (
        entry.fetched_at == current.fetched_at and current.expires_at <= now
    ):
        _endpoint_cache["watchlist"] = entry
        return entry
    return current


def _parse_watchlist_tickers(raw: str | None) -> list[str] | None:
    """Normalize an optional comma-separated watchlist query.

    ``None`` means the caller wants the original full watchlist. An explicit
    but empty value is rejected so a malformed targeted request cannot
    accidentally trigger the substantially larger full-universe download.
    """
    if raw is None:
        return None
    if len(raw) > _WATCHLIST_QUERY_MAX_LENGTH:
        raise HTTPException(status_code=400, detail="Watchlist query is too long")

    parts = raw.split(",")
    normalized: list[str] = []
    seen: set[str] = set()
    for part in parts:
        ticker = part.strip().upper()
        if not ticker or not _WATCHLIST_TICKER_PATTERN.fullmatch(ticker):
            raise HTTPException(status_code=400, detail=f"Invalid ticker: {part.strip() or '(empty)'}")
        if ticker in seen:
            continue
        seen.add(ticker)
        normalized.append(ticker)
        if len(normalized) > _WATCHLIST_MAX_TICKERS:
            raise HTTPException(
                status_code=400,
                detail=f"A maximum of {_WATCHLIST_MAX_TICKERS} tickers is allowed",
            )
    return normalized


def _watchlist_cache_key(tickers: list[str] | None) -> str:
    if tickers is None:
        return "watchlist"
    canonical_set = ",".join(sorted(tickers))
    digest = hashlib.sha256(canonical_set.encode("ascii")).hexdigest()
    return f"watchlist:set:{digest}"


async def _with_watchlist_daily_trends(payload: Any) -> Any:
    """Attach at most 30 cached daily bars, with no per-card provider request.

    This also enriches persisted watchlists created before the trend field
    existed. The original quote/spark cache is never mutated. Durable manual
    pulls share a parsed document cache, so all symbols reuse one disk read.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("groups"), list):
        return payload

    def project() -> dict[str, Any]:
        now = time.time()
        trends: dict[str, Any] = {}
        groups = []
        for group in payload["groups"]:
            if not isinstance(group, dict) or not isinstance(group.get("stocks"), list):
                groups.append(group)
                continue
            rows = []
            for row in group.get("stocks", []):
                symbol = row.get("ticker", "")
                if symbol not in trends:
                    entry = _endpoint_cache.get(f"chart:{symbol}:1d:raw")
                    if entry is not None and entry.stale_until <= now:
                        entry = None
                    chart = entry.value if entry is not None else None
                    persisted = read_stock_pull_resource(symbol, "daily_chart", now=now)
                    if persisted is not None and (entry is None or persisted["saved_at"] > entry.fetched_at):
                        chart = persisted["payload"]
                    if chart is None:
                        # The worker's public focus / breakout charts are also
                        # valid daily caches, including before any manual pull.
                        chart = read_public_home_resource(
                            "focus_chart",
                            parameters={"ticker": symbol, "range": "1d", "adjustment": "raw"},
                            now=now,
                        )
                    if chart is None:
                        try:
                            parameters = breakout_lead_chart_parameters(symbol)
                        except ValueError:
                            parameters = None
                        if parameters is not None:
                            chart = read_public_home_resource("breakout_lead_chart", parameters=parameters, now=now)
                    trends[symbol] = daily_trend(chart, market_timezone=_watchlist_market_timezone(symbol))
                rows.append({**row, "daily_trend": trends[symbol]} if trends[symbol] is not None else row)
            groups.append({**group, "stocks": rows})
        return {**payload, "groups": groups}

    return await asyncio.to_thread(project)


@router.get("/watchlist")
async def watchlist(
    tickers: Annotated[
        Optional[str],
        Query(max_length=_WATCHLIST_QUERY_MAX_LENGTH),
    ] = None,
):
    requested_tickers = _parse_watchlist_tickers(tickers)
    cache_key = _watchlist_cache_key(requested_tickers)
    allow_refresh = current_request_is_owner()
    # Keep the zero-argument loader for the original endpoint. Besides
    # preserving behavior, this remains compatible with tests and callers
    # that replace ``_build_watchlist`` with a zero-argument function.
    loader = (
        _build_watchlist
        if requested_tickers is None
        else lambda: _build_watchlist(requested_tickers)
    )
    try:
        if requested_tickers is None:
            now = time.time()
            password_snapshot_reuse = (
                allow_refresh
                and get_personal_config().access.mode == "password"
            )
            if password_snapshot_reuse:
                owner_entry = _load_watchlist_snapshot_for_owner(now)
                if owner_entry is not None:
                    result = _cache_result(
                        owner_entry,
                        stale=owner_entry.expires_at <= now,
                    )
                    if (
                        isinstance(result, dict)
                        and owner_entry.expires_at <= now
                    ):
                        result["stale_reason"] = (
                            "worker_snapshot_awaiting_refresh"
                        )
                        result["stale_age_seconds"] = round(
                            max(now - owner_entry.fetched_at, 0.0),
                            1,
                        )
                    return await _with_watchlist_daily_trends(result)
            else:
                _load_watchlist_snapshot_once(now)
            result = await _stale_while_revalidate_endpoint(
                cache_key,
                _WATCHLIST_FRESH_TTL_SECONDS,
                _WATCHLIST_MAX_SNAPSHOT_AGE_SECONDS,
                loader,
                _persist_watchlist_snapshot,
                allow_refresh=allow_refresh,
            )
            return await _with_watchlist_daily_trends(result)
        result = await _cached_endpoint(
            cache_key,
            _WATCHLIST_FRESH_TTL_SECONDS,
            loader,
            stale_ttl=_WATCHLIST_TARGETED_STALE_TTL_SECONDS,
            allow_refresh=allow_refresh,
        )
        return await _with_watchlist_daily_trends(result)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Yahoo watchlist data is currently unavailable") from exc


async def _build_watchlist(requested_tickers: list[str] | None = None):
    from app.services.sectors import SECTORS

    if requested_tickers is None:
        all_tickers = []
        for sec in SECTORS.values():
            all_tickers.extend(sec["tickers"])
        all_tickers = list(dict.fromkeys(all_tickers))
    else:
        all_tickers = requested_tickers

    # Daily bars provide the seven-session sparkline and previous official
    # close. A second, still-batched intraday request provides the latest
    # regular/extended-hours price. Keeping both calls batched avoids the old
    # one-fast_info-request-per-ticker cold-load penalty without presenting a
    # previous daily close as a freshly fetched quote.
    def _massive_daily_frame(closes):
        """Massive 日线收盘 → yfinance 形状帧(MultiIndex 列,naive 日期索引)。"""
        import pandas as pd

        columns = {}
        for ticker, points in closes.items():
            index = [
                datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)
                .astimezone(_watchlist_market_timezone(ticker))
                .replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
                for stamp, _ in points
            ]
            columns[(ticker, "Close")] = pd.Series(
                [price for _, price in points], index=pd.DatetimeIndex(index)
            )
        if not columns:
            return None
        frame = pd.DataFrame(columns)
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        return frame.sort_index()

    def _massive_latest_frame(symbol_by_ticker, snaps):
        """Massive 快照最新分钟条 → yfinance 形状帧;无分钟条的标的进 missing。"""
        import pandas as pd

        columns = {}
        missing = []
        for ticker, symbol in symbol_by_ticker.items():
            row = snaps.get(symbol)
            minute = (row or {}).get("minute") or {}
            price = minute.get("c")
            stamp = minute.get("t")
            if price is None or not isinstance(stamp, (int, float)) or stamp <= 0:
                missing.append(ticker)
                continue
            index = pd.DatetimeIndex(
                [datetime.fromtimestamp(stamp / 1000, tz=timezone.utc)]
            )
            columns[(ticker, "Close")] = pd.Series([price], index=index)
        if not columns:
            return None, missing
        frame = pd.DataFrame(columns)
        frame.columns = pd.MultiIndex.from_tuples(frame.columns)
        return frame.sort_index(), missing

    def _fetch_quotes():
        try:
            import pandas as pd
            import yfinance as yf_mod
            session = getattr(
                __import__("app.services.yahoo", fromlist=["_yf_session"]),
                "_yf_session",
                None,
            )
            # 主源 Massive:日线(spark/涨跌基准)+ 批量快照(最新价);
            # 任一环节失败或个别标的缺失,仅该部分回落 Yahoo,行为与旧链一致。
            from app.services import massive as massive_provider

            massive_daily = None
            massive_latest = None
            yahoo_daily_tickers = list(all_tickers)
            yahoo_latest_tickers = list(all_tickers)
            if massive_provider.configured():
                try:
                    closes, daily_missing = massive_provider.watchlist_daily_closes(
                        all_tickers
                    )
                    if closes:
                        massive_daily = _massive_daily_frame(closes)
                        yahoo_daily_tickers = list(daily_missing)
                        symbol_by_ticker = {
                            ticker: massive_provider.to_symbol(ticker)
                            for ticker in closes
                        }
                        try:
                            snaps = massive_provider.snapshot_batch(
                                sorted(set(symbol_by_ticker.values()))
                            )
                            frame, latest_missing = _massive_latest_frame(
                                symbol_by_ticker, snaps
                            )
                            if frame is not None:
                                massive_latest = frame
                                yahoo_latest_tickers = sorted(
                                    set(daily_missing) | set(latest_missing)
                                )
                        except massive_provider.MassiveError:
                            pass  # 快照计划不含/限流:最新价整体走 Yahoo
                except massive_provider.MassiveError:
                    massive_daily = None
                    massive_latest = None
                    yahoo_daily_tickers = list(all_tickers)
                    yahoo_latest_tickers = list(all_tickers)

            daily_df = (
                download_in_bounded_batches(
                    yf_mod.download,
                    tickers=yahoo_daily_tickers,
                    period="7d",
                    interval="1d",
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                    session=session,
                )
                if yahoo_daily_tickers
                else pd.DataFrame()
            )
            if massive_daily is not None:
                daily_df = (
                    massive_daily
                    if getattr(daily_df, "empty", True)
                    else pd.concat([massive_daily, daily_df], axis=1)
                )
            latest_df = (
                download_in_bounded_batches(
                    yf_mod.download,
                    tickers=yahoo_latest_tickers,
                    period="1d",
                    interval=_WATCHLIST_LATEST_INTERVAL,
                    prepost=True,
                    group_by="ticker",
                    progress=False,
                    auto_adjust=False,
                    session=session,
                )
                if yahoo_latest_tickers
                else pd.DataFrame()
            )
            if massive_latest is not None:
                latest_df = (
                    massive_latest
                    if getattr(latest_df, "empty", True)
                    else pd.concat([massive_latest, latest_df], axis=1)
                )
            provider_previous_closes = _fetch_watchlist_provider_previous_closes(
                [
                    ticker
                    for ticker in all_tickers
                    if _watchlist_requires_provider_previous_close(ticker)
                ],
                session=session,
            )
            from app.services.zh_names import get_zh_name

            def frame_for(dataset, ticker):
                if dataset is None or getattr(dataset, "empty", True):
                    return None
                columns = getattr(dataset, "columns", None)
                if getattr(columns, "nlevels", 1) > 1:
                    level_zero = columns.get_level_values(0)
                    if ticker in level_zero:
                        return dataset[ticker]
                    level_one = columns.get_level_values(1)
                    if ticker in level_one:
                        return dataset.xs(ticker, axis=1, level=1)
                    return None
                return dataset if len(all_tickers) == 1 else None

            def finite_closes(frame, market_timezone):
                if frame is None or frame.empty:
                    return []
                close_col = "Close" if "Close" in frame.columns else "Adj Close"
                if close_col not in frame.columns:
                    return []
                points = []
                for index, value in frame[close_col].items():
                    try:
                        price = float(value)
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if not math.isfinite(price) or price <= 0:
                        continue
                    raw_dt = index.to_pydatetime() if hasattr(index, "to_pydatetime") else index
                    if not isinstance(raw_dt, datetime):
                        continue
                    if raw_dt.tzinfo is None:
                        market_dt = raw_dt.replace(tzinfo=market_timezone)
                    else:
                        market_dt = raw_dt.astimezone(market_timezone)
                    points.append((market_dt, price))
                return points

            def session_name(market_dt, market_timezone):
                if market_timezone.key != _WATCHLIST_MARKET_TIMEZONE.key:
                    return "exchange_session"
                minute = market_dt.hour * 60 + market_dt.minute
                if minute < 9 * 60 + 30:
                    return "pre_market"
                if minute < 16 * 60:
                    return "regular"
                return "post_market"

            quotes = {}
            quote_times = []
            quote_market_datetimes = {}
            for t in all_tickers:
                try:
                    market_timezone = _watchlist_market_timezone(t)
                    daily = finite_closes(frame_for(daily_df, t), market_timezone)
                    latest = finite_closes(frame_for(latest_df, t), market_timezone)
                    if not daily or not latest:
                        continue

                    quote_dt, price = latest[-1]
                    daily_dt, daily_close = daily[-1]
                    overnight_contract = t.endswith("=F")
                    next_futures_session = (
                        overnight_contract
                        and daily_dt.date().toordinal() == quote_dt.date().toordinal() + 1
                        and quote_dt.weekday() in {0, 1, 2, 3, 6}
                        and quote_dt.hour >= 17
                    )
                    daily_is_current = (
                        daily_dt.date() == quote_dt.date() or next_futures_session
                    )
                    if daily_dt.date() > quote_dt.date() and not daily_is_current:
                        # The intraday batch is older than the available daily
                        # bar and therefore cannot truthfully be called latest.
                        continue

                    if daily_is_current:
                        if len(daily) < 2:
                            continue
                        daily_previous_close = daily[-2][1]
                        spark = [point[1] for point in daily[-7:]]
                        spark[-1] = price
                    else:
                        daily_previous_close = daily_close
                        spark = [point[1] for point in daily[-6:]] + [price]

                    if _watchlist_requires_provider_previous_close(t):
                        previous_close = provider_previous_closes.get(t)
                        if previous_close is None:
                            # A daily fallback would silently change the
                            # provider's published index/futures baseline.
                            continue
                        previous_close_source = "provider_metadata"
                    else:
                        previous_close = daily_previous_close
                        previous_close_source = "daily_close"

                    quote_times.append(quote_dt.astimezone(timezone.utc))
                    quote_market_datetimes[t] = quote_dt
                    quotes[t] = {
                        "ticker": t,
                        "name": get_zh_name(t) or t,
                        "price": round(price, 2),
                        "change": round(price - previous_close, 2),
                        "change_percent": round(
                            (price - previous_close) / previous_close * 100,
                            2,
                        ),
                        "spark": [round(point, 2) for point in spark[-7:]],
                        "quote_as_of": quote_dt.astimezone(timezone.utc).isoformat(),
                        "quote_session": session_name(quote_dt, market_timezone),
                        "previous_close_source": previous_close_source,
                    }
                except Exception:
                    continue
            # Compare trading dates only within the U.S. equity/ETF session.
            # The universe also contains RMS.PA and futures; comparing their
            # exchange dates to New York equities would create a predictable
            # false warning every morning. Before 09:30 ET, a U.S. symbol with
            # no pre-market print is also legitimately still at the prior
            # official close.
            us_quote_datetimes = {
                ticker: quote_dt
                for ticker, quote_dt in quote_market_datetimes.items()
                if "." not in ticker
                and "=" not in ticker
                and not ticker.startswith("^")
            }
            newest_us_quote = max(us_quote_datetimes.values(), default=None)
            us_regular_session_started = bool(
                newest_us_quote
                and newest_us_quote.hour * 60 + newest_us_quote.minute >= 9 * 60 + 30
            )
            delayed_tickers = sorted(
                ticker
                for ticker, quote_dt in us_quote_datetimes.items()
                if us_regular_session_started
                and newest_us_quote is not None
                and quote_dt.date() < newest_us_quote.date()
            )
            for ticker in delayed_tickers:
                quotes[ticker]["quote_delayed"] = True
            return quotes, quote_times, delayed_tickers
        except Exception:
            return {}, [], []

    price_map, quote_times, delayed_tickers = await asyncio.to_thread(_fetch_quotes)

    # If yfinance limited us hard, less than 30% succeeded — treat as failure
    # so the cache returns the previous (stale) snapshot instead of an empty one.
    success_ratio = len(price_map) / max(len(all_tickers), 1)
    if success_ratio < 0.3:
        raise RuntimeError(f"watchlist mostly failed ({len(price_map)}/{len(all_tickers)} succeeded)")

    groups = []
    if requested_tickers is None:
        # Preserve the full-watchlist response exactly, including symbols that
        # intentionally appear in more than one sector.
        for sec_id, sec in SECTORS.items():
            items = [price_map[t] for t in sec["tickers"] if t in price_map]
            if items:
                groups.append({"id": sec_id, "name": sec["name"], "stocks": items})
    else:
        # A targeted response contains each requested ticker at most once.
        # Assign known symbols to their first matching sector, then retain
        # arbitrary valid Yahoo symbols in a custom group.
        requested_set = set(requested_tickers)
        assigned: set[str] = set()
        for sec_id, sec in SECTORS.items():
            items = []
            for ticker in sec["tickers"]:
                if ticker in requested_set and ticker in price_map and ticker not in assigned:
                    items.append(price_map[ticker])
                    assigned.add(ticker)
            if items:
                groups.append({"id": sec_id, "name": sec["name"], "stocks": items})
        custom_items = [
            price_map[ticker]
            for ticker in requested_tickers
            if ticker in price_map and ticker not in assigned
        ]
        if custom_items:
            groups.append({"id": "custom", "name": "自定义", "stocks": custom_items})

    return _sanitize({
        "groups": groups,
        "attempted": len(all_tickers),
        "succeeded": len(price_map),
        "failed": len(all_tickers) - len(price_map),
        "failed_tickers": sorted(t for t in all_tickers if t not in price_map),
        "data_limited": success_ratio < 1.0 or bool(delayed_tickers),
        "delayed": len(delayed_tickers),
        "delayed_tickers": delayed_tickers,
        "quote_interval": _WATCHLIST_LATEST_INTERVAL,
        # A collection is only complete through its oldest constituent quote.
        # Keep the newest timestamp separately so the UI cannot hide one stale
        # symbol behind another symbol's recent trade.
        "data_through": min(quote_times).isoformat() if quote_times else None,
        "oldest_quote_at": min(quote_times).isoformat() if quote_times else None,
        "latest_quote_at": max(quote_times).isoformat() if quote_times else None,
        "source_status": (
            "active"
            if success_ratio == 1.0 and not delayed_tickers
            else "degraded"
        ),
    })


@router.get("/search")
async def search_stocks(q: str = Query(..., min_length=1, max_length=50)):
    q_upper = q.upper().strip()
    q_lower = q.lower().strip()
    if not q_upper:
        return []
    from app.services import massive as massive_provider
    from app.services.zh_names import NAMES

    raw_symbol_query = q_upper[3:] if q_upper.startswith("US.") else q_upper
    symbol_query = (
        massive_provider.to_symbol(raw_symbol_query)
        if _STOCK_DIRECTORY_TICKER_PATTERN.fullmatch(raw_symbol_query)
        else None
    ) or raw_symbol_query

    def fuzzy(query, text):
        """Check if all chars of query appear in text in order (fuzzy match)."""
        it = iter(text.lower())
        return all(c in it for c in query.lower())

    # The checked-in names enrich presentation, but they are not the search
    # universe. Massive's persisted reference directory supplies complete
    # active U.S. stock coverage, including symbols outside theme/watch pools.
    candidates: dict[str, dict[str, Any]] = {
        ticker: {
            "ticker": ticker,
            "name_en": name,
            "market": "stocks",
            "type": "CS",
        }
        for ticker, name in KNOWN_TICKERS.items()
    }
    for t, (zh, _) in NAMES.items():
        candidate = candidates.setdefault(
            t,
            {
                "ticker": t,
                "name_en": zh,
                "market": "stocks",
                "type": "CS",
            },
        )
        candidate["name_zh"] = zh

    directory = await _stock_directory(
        allow_refresh=current_request_is_owner(),
    )
    directory_available = isinstance(directory, dict) and bool(
        directory.get("items")
    )
    for raw in (directory or {}).get("items") or []:
        if not isinstance(raw, dict):
            continue
        ticker = str(raw.get("ticker") or "")
        if not _STOCK_DIRECTORY_TICKER_PATTERN.fullmatch(ticker):
            continue
        candidate = candidates.setdefault(ticker, {"ticker": ticker})
        candidate.update(
            {
                "name_en": str(raw.get("name") or ticker),
                "market": str(raw.get("market") or "stocks"),
                "type": str(raw.get("type") or ""),
                "primary_exchange": str(raw.get("primary_exchange") or ""),
            }
        )

    ranked: list[tuple[int, int, str, dict[str, Any]]] = []
    for ticker, candidate in candidates.items():
        name = str(candidate.get("name_en") or ticker)
        zh_entry = NAMES.get(ticker)
        zh_name = str(candidate.get("name_zh") or (zh_entry[0] if zh_entry else ""))
        zh_desc = zh_entry[1] if zh_entry else ""
        search_text = f"{ticker} {name} {zh_name} {zh_desc}".lower()
        if symbol_query == ticker:
            rank = 0
        elif q_lower == name.lower() or (zh_name and q_lower == zh_name.lower()):
            rank = 1
        elif ticker.startswith(symbol_query):
            rank = 2
        elif q_lower in search_text:
            rank = 3
        elif len(q_lower) >= 2 and fuzzy(q_lower, ticker):
            rank = 4
        else:
            continue
        ranked.append(
            (
                rank,
                len(ticker),
                ticker,
                {
                    "ticker": ticker,
                    "name": zh_name or name,
                    "name_en": name,
                    "market": candidate.get("market") or "stocks",
                    "type": candidate.get("type") or "",
                    "primary_exchange": candidate.get("primary_exchange") or "",
                },
            )
        )

    ranked.sort(key=lambda item: (item[0], item[1], item[2]))
    results = [item[3] for item in ranked[:12]]
    if results and directory_available:
        return _sanitize(results)

    # Provider directory outages must not erase exact-symbol lookup. Yahoo is
    # only the final owner-only fallback and never replaces the persisted
    # Massive universe for normal search.
    def _yf_search():
        try:
            tk = yf.Ticker(massive_provider.to_yahoo_symbol(symbol_query))
            info = tk.info
            name = info.get("shortName", "")
            if name and info.get("regularMarketPrice"):
                return [
                    {
                        "ticker": symbol_query,
                        "name": name,
                        "market": "stocks",
                        "type": info.get("quoteType", "CS"),
                    }
                ]
        except Exception:
            pass
        return []

    if not current_request_is_owner():
        if not directory_available:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "stock_directory_unavailable",
                    "message": "The complete stock directory is not available",
                },
            )
        return []

    yf_results = await asyncio.to_thread(_yf_search)
    if yf_results:
        return _sanitize(yf_results[:10])
    if not directory_available:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "stock_directory_unavailable",
                "message": "The complete stock directory is not available",
            },
        )
    return []


async def _build_stock_signals(ticker: str) -> dict[str, Any]:
    """Compute live technical signals without consulting either cache layer."""

    symbol = quote_symbol(ticker)
    if not _WATCHLIST_TICKER_PATTERN.fullmatch(symbol):
        raise ValueError("Invalid ticker symbol")

    def _safe_number(value: Any) -> float | None:
        try:
            f = float(value)
            return f if math.isfinite(f) else None
        except Exception:
            return None

    def _compute():
        try:
            hist = None
            price_provider = "Yahoo/yfinance"
            from app.services import massive as massive_provider

            if massive_provider.configured():
                massive_symbol = massive_provider.to_symbol(symbol)
                if massive_symbol is not None and not massive_symbol.startswith("I:"):
                    # 技术指标需要拆股复权序列;Polygon 语义的 adjusted=false
                    # 会让拆股票的均线/量比在断崖两侧全部失真。
                    hist = _massive_chart_history(
                        massive_provider,
                        massive_symbol,
                        "1d",
                        adjusted=True,
                    )
                    if hist is not None and not hist.empty:
                        price_provider = "Massive"
            if hist is None or hist.empty:
                tk = yf.Ticker(massive_provider.to_yahoo_symbol(symbol))
                hist = tk.history(period="100d")
            if hist.empty or "Close" not in hist.columns or "Volume" not in hist.columns:
                raise RuntimeError(f"Insufficient price history for {symbol}")

            def valid_close(value: Any) -> bool:
                number = _safe_number(value)
                return number is not None and number > 0

            # Yahoo can publish the new session's daily row before it has a
            # close. Keep the last completed bar as the indicator endpoint;
            # otherwise every rolling calculation ends in NaN before the open.
            hist = hist.loc[hist["Close"].map(valid_close)].copy()
            if len(hist) < 50:
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

            # Volume — 20 日均量为 0/NaN（停牌、指数类符号、数据缺口）时
            # 量比不可计算；1.0 占位会显示成「量比 1.00 · 正常」的假读数。
            avg_vol = _safe_number(volume.rolling(20).mean().iloc[-1]) or 0.0
            cur_vol = _safe_number(volume.iloc[-1]) or 0.0
            vol_ratio = cur_vol / avg_vol if avg_vol > 0 else None

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
                "volume": (
                    {
                        "value": round(vol_ratio, 2),
                        "signal": "spike" if vol_ratio > 2 else "high" if vol_ratio > 1.5 else "normal",
                        "label": "Volume",
                    }
                    if vol_ratio is not None
                    else {
                        "value": None,
                        "signal": "unavailable",
                        "label": "Volume",
                    }
                ),
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
                "VOLUME" if vol_ratio is not None and vol_ratio > 1.5 else None,
            ]

            return {
                "ticker": symbol,
                "price": round(price, 2),
                "price_provider": price_provider,
                "score": score,
                "overall": "bullish" if score >= 60 else "bearish" if score <= 40 else "neutral",
                "signals": signals,
                "tags": [tag for tag in tags if tag],
            }
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"Price provider unavailable for {symbol}") from exc

    return await asyncio.to_thread(_compute)


@router.get("/{ticker}/signals")
async def stock_signals(ticker: str):
    """Compute RSI, MACD, EMA/SMA signals from 100d daily data."""

    symbol = quote_symbol(ticker)
    if not _WATCHLIST_TICKER_PATTERN.fullmatch(symbol):
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    owner = current_request_is_owner()
    key = f"technical-signals:{symbol}"
    if owner and symbol == "NVDA":
        disk_result = await _reuse_fresh_public_home_entry(
            key,
            "focus_signals",
            {"ticker": symbol, "period": "100d"},
            fresh_for_seconds=float(
                get_personal_config().public_home.signals_seconds
            ),
        )
        if disk_result is not None:
            return _sanitize(disk_result)
    try:
        result = await _cached_endpoint(
            key,
            300,
            lambda: _build_stock_signals(symbol),
            stale_ttl=900,
            allow_refresh=owner,
        )
    except HTTPException as exc:
        if owner or not _is_public_snapshot_unavailable(exc):
            raise
        now = time.time()
        result = await read_public_home_resource_async(
            "focus_signals",
            parameters={"ticker": symbol, "period": "100d"},
            now=now,
        )
        if result is None:
            raise exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _sanitize(result)


@router.get("/{ticker}/logo")
async def stock_logo(ticker: str):
    symbol = quote_symbol(ticker)
    variants = _logo_symbol_variants(symbol)
    if not variants:
        raise HTTPException(status_code=404, detail="Invalid ticker")
    logo = await _cached_company_logo(
        variants[0],
        allow_refresh=current_request_is_owner(),
    )
    return Response(
        content=logo["content"],
        media_type=logo["media_type"],
        headers={
            "Cache-Control": "public, max-age=86400",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "X-Content-Type-Options": "nosniff",
            "X-Logo-Source": logo["source"],
        },
    )


def _attach_macro_fit(symbol: str, payload: Any) -> Any:
    """Add the stock's macro fit (shadow) to an overview payload.

    This lives on the overview endpoint rather than on /strength/stocks/{t}
    because the drawer always calls the overview, while /strength/stocks/{t}
    only answers for tickers inside the public snapshot's top slice -- it 404s
    for everything else, so sourcing the fit there left AMD, SLB and ~190 other
    tickers reading "no macro read" when the fit was perfectly computable.

    Attached outside the 60s price cache so a fresh macro publication is not
    held behind a quote cache entry, and so the cached value written for
    public_home stays exactly what it was before.

    Shadow only, and per *sector*: the exposure profile belongs to the sector,
    not the stock. Failure degrades to "no read" -- never to a neutral 50, and
    never to an error, because a quote must not fail over an annotation.

    Provenance travels with the reading whenever there is a snapshot to name.
    The drawer shows this fit next to a technical-minus-macro gap that comes from
    the persisted strength scan, and those two can straddle a publication; the
    interface needs to be able to tell that they did.

    Synchronous on purpose -- see ``_attach_macro_fit_async`` for the endpoint
    path, which is where the thread hop belongs.
    """

    if not isinstance(payload, dict):
        return payload
    try:
        from app.services.macro_conditions.linkage import factor_driver
        from app.services.macro_conditions.linkage_reader import load_macro_fit_reader
        from app.services.sectors import primary_sector_id
    except Exception:
        return payload

    blank = {
        "macro_fit_shadow": None,
        "macro_fit_confidence": None,
        "macro_tailwind": None,
        "macro_supporting_factors": [],
        "macro_opposing_factors": [],
    }
    try:
        sector_id = primary_sector_id(symbol)
        if sector_id is None:
            return {**payload, **blank, "macro_shadow_status": "sector_unclassified"}
        reader = load_macro_fit_reader()
        if not reader.available:
            return {**payload, **blank, "macro_shadow_status": str(reader.reason or "unavailable")}
        provenance = reader.provenance()
        fit = reader.fit_for(sector_id)
        if fit.score is None:
            return {
                **payload,
                **blank,
                **provenance,
                "macro_fit_confidence": round(fit.confidence, 4),
                "macro_shadow_status": "exposure_coverage_low",
            }
        return {
            **payload,
            **provenance,
            "macro_fit_shadow": fit.score,
            "macro_fit_confidence": round(fit.confidence, 4),
            "macro_fit_version": fit.version,
            "macro_tailwind": fit.tailwind,
            "macro_supporting_factors": [factor_driver(f) for f in fit.supporting],
            "macro_opposing_factors": [factor_driver(f) for f in fit.opposing],
            "macro_shadow_status": "ok",
        }
    except Exception:
        return {**payload, **blank, "macro_shadow_status": "unavailable"}


async def _attach_macro_fit_async(symbol: str, payload: Any) -> Any:
    """``_attach_macro_fit`` off the event loop.

    The macro read opens SQLite and runs three queries. Cheap when the disk is
    idle, but this is an ``async def`` endpoint: run it inline and a busy disk,
    WAL contention or a stalled volume blocks every unrelated request on the
    loop, not just this one.
    """

    return await asyncio.to_thread(_attach_macro_fit, symbol, payload)


async def _index_overview_from_public_snapshot(
    symbol: str,
    *,
    now: float,
) -> dict[str, Any] | None:
    """访客回退：指数代码从 public_home 的 indices 快照拼最小行情头。

    /market 纸带对访客永远可读，同一份快照顺带满足 /stock/^GSPC 的页面准入——
    手动拉取的 overview 快照只有 24h，过期后指数页不该退回整页空态。
    只填快照真实有的字段（价/涨跌幅/由涨跌幅反推的昨收），其余留空显「—」。
    """

    if symbol not in PUBLIC_HOME_INDEX_SYMBOLS:
        return None
    payload = await read_public_home_resource_async(
        "indices",
        parameters=public_home_resource_parameters("indices", now=now),
        now=now,
    )
    if not isinstance(payload, dict):
        return None
    row = next(
        (
            item
            for item in (payload.get("indices") or [])
            if isinstance(item, dict) and item.get("symbol") == symbol
        ),
        None,
    )
    if row is None:
        return None
    try:
        price = float(row.get("price"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(price) or price <= 0:
        return None
    change_pct_raw = row.get("change_percent")
    change_pct: float | None
    try:
        change_pct = float(change_pct_raw)
        if not math.isfinite(change_pct) or change_pct <= -100:
            change_pct = None
    except (TypeError, ValueError):
        change_pct = None
    prev_close = (
        round(price / (1 + change_pct / 100), 4) if change_pct is not None else None
    )
    return {
        "ticker": symbol,
        "price": price,
        "change_percent": change_pct,
        "prev_close": prev_close,
        "change": round(price - prev_close, 4) if prev_close is not None else None,
        "as_of": payload.get("as_of"),
        "price_provider": "public_home:indices",
        "snapshot_source": "indices",
        "_cached": True,
    }


@router.get("/{ticker}")
async def stock_overview(ticker: str):
    owner = current_request_is_owner()
    symbol = quote_symbol(ticker)
    key = f"stock:{symbol}"
    await _hydrate_stock_pull_resource(symbol, "overview", key)
    if owner and symbol == "NVDA":
        disk_result = await _reuse_fresh_public_home_entry(
            key,
            "focus_overview",
            {"ticker": symbol},
            fresh_for_seconds=float(
                get_personal_config().public_home.overview_seconds
            ),
        )
        if disk_result is not None:
            return await _attach_macro_fit_async(symbol, _sanitize(disk_result))
    try:
        return await _attach_macro_fit_async(
            symbol,
            await _stale_while_revalidate_endpoint(
                key,
                60,
                30 * 60,
                lambda: _stock_overview_impl(symbol),
                allow_refresh=owner,
            ),
        )
    except HTTPException as exc:
        if owner or not _is_public_snapshot_unavailable(exc):
            raise
        now = time.time()
        result = await read_public_home_resource_async(
            "focus_overview",
            parameters={"ticker": symbol},
            now=now,
        )
        if result is None:
            # 指数码（/market 指数卡点开）没有 focus 快照；纸带那份 indices
            # 快照对访客常绿，用它保住页面准入，K线/结构仍由拉取快照供给。
            result = await _index_overview_from_public_snapshot(symbol, now=now)
        if result is None:
            raise exc
        return await _attach_macro_fit_async(symbol, _sanitize(result))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Stock data is currently unavailable") from exc


async def _stock_overview_impl(ticker: str):
    def _finite_quote(value: Any, *, allow_zero: bool = False) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        minimum_ok = number >= 0 if allow_zero else number > 0
        return number if math.isfinite(number) and minimum_ok else None

    def _quote_as_of(value: Any) -> str | None:
        if isinstance(value, datetime):
            stamp = value
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=timezone.utc)
            return stamp.astimezone(timezone.utc).isoformat()
        if isinstance(value, str):
            try:
                stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
            if stamp.tzinfo is None:
                return None
            return stamp.astimezone(timezone.utc).isoformat()
        try:
            epoch = float(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if not math.isfinite(epoch) or epoch <= 0:
            return None
        if epoch >= 1e17:
            epoch /= 1_000_000_000
        elif epoch >= 1e14:
            epoch /= 1_000_000
        elif epoch >= 1e11:
            epoch /= 1_000
        try:
            return datetime.fromtimestamp(epoch, timezone.utc).isoformat()
        except (OverflowError, OSError, ValueError):
            return None

    def _work():
        symbol = quote_symbol(ticker)
        if not _WATCHLIST_TICKER_PATTERN.fullmatch(symbol):
            raise RuntimeError("Invalid ticker symbol")

        last_price: float | None = None
        prev_close: float | None = None
        quote_as_of: str | None = None
        quote_volume: float | None = None
        quote_open: float | None = None
        quote_high: float | None = None
        quote_low: float | None = None
        price_provider: str | None = None

        # Quote fields are fetched from Massive first. Yahoo profile or
        # fundamentals failures must not discard a valid paid-provider quote.
        from app.services import massive as massive_provider

        massive_symbol = massive_provider.to_symbol(symbol)
        if (
            massive_provider.configured()
            and massive_symbol is not None
            and not massive_symbol.startswith("I:")
        ):
            try:
                snapshot = massive_provider.snapshot_batch([massive_symbol]).get(
                    massive_symbol
                )
            except massive_provider.MassiveError:
                snapshot = None
            if snapshot:
                minute = snapshot.get("minute") or {}
                day = snapshot.get("day") or {}
                massive_price = (
                    _finite_quote(minute.get("c"))
                    or _finite_quote(day.get("c"))
                    or _finite_quote(snapshot.get("day_close"))
                )
                if massive_price is not None:
                    last_price = massive_price
                    price_provider = "Massive"
                    prev_close = _finite_quote(snapshot.get("prev_close"))
                    quote_open = _finite_quote(day.get("o"))
                    quote_high = _finite_quote(day.get("h"))
                    quote_low = _finite_quote(day.get("l"))
                    massive_volume = _finite_quote(
                        day.get("v"),
                        allow_zero=True,
                    )
                    if massive_volume is not None:
                        quote_volume = massive_volume
                    quote_as_of = (
                        _quote_as_of(snapshot.get("as_of"))
                        or _quote_as_of(minute.get("t"))
                        or _quote_as_of(day.get("t"))
                        or _quote_as_of(snapshot.get("updated"))
                    )

        info: dict[str, Any] = {}
        fi: Any = None
        try:
            tk = yf.Ticker(massive_provider.to_yahoo_symbol(symbol))
            try:
                raw_info = tk.info
                if isinstance(raw_info, dict):
                    info = raw_info
            except Exception:
                info = {}
            try:
                fi = tk.fast_info
            except Exception:
                fi = None
        except Exception:
            pass

        def _fast_value(name: str) -> Any:
            if fi is None:
                return None
            try:
                return getattr(fi, name)
            except Exception:
                return None

        yahoo_price = _finite_quote(_fast_value("last_price"))
        yahoo_previous = _finite_quote(_fast_value("previous_close"))
        if last_price is None and yahoo_price is not None:
            last_price = yahoo_price
            price_provider = "Yahoo/yfinance"
        if prev_close is None:
            prev_close = yahoo_previous
        if quote_volume is None:
            quote_volume = _finite_quote(
                _fast_value("last_volume"),
                allow_zero=True,
            )
        if quote_open is None:
            quote_open = _finite_quote(info.get("open"))
        if quote_high is None:
            quote_high = _finite_quote(info.get("dayHigh"))
        if quote_low is None:
            quote_low = _finite_quote(info.get("dayLow"))
        if quote_as_of is None:
            quote_as_of = _quote_as_of(info.get("regularMarketTime"))
        if last_price is None or price_provider is None:
            raise RuntimeError(f"Price provider unavailable for {symbol}")

        from app.services.zh_names import get_zh_info
        zh = get_zh_info(symbol)
        website = info.get("website")
        logo_urls = _logo_urls(symbol, website)
        change = last_price - prev_close if prev_close is not None else None
        change_percent = (
            change / prev_close * 100
            if change is not None and prev_close is not None
            else None
        )
        market_cap = _finite_quote(_fast_value("market_cap"))
        return {
            "ticker": symbol,
            "name": (
                zh.get("name_zh")
                or info.get("shortName")
                or KNOWN_TICKERS.get(symbol)
                or symbol
            ),
            "name_en": info.get("shortName") or KNOWN_TICKERS.get(symbol) or symbol,
            "website": website,
            "logo_url": logo_urls[0] if logo_urls else None,
            "logo_urls": logo_urls,
            # Preserve provider precision here. Presentation layers can choose
            # the appropriate decimals without turning a sub-dollar quote into
            # a different price from the raw K-line shown on the same page.
            "price": last_price,
            "change": change,
            "change_percent": change_percent,
            "volume": int(quote_volume) if quote_volume is not None else None,
            "market_cap": market_cap,
            "prev_close": prev_close,
            "high": quote_high,
            "low": quote_low,
            "open": quote_open,
            "as_of": quote_as_of,
            "price_provider": price_provider,
            "profile_provider": "Yahoo/yfinance" if info else None,
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
_CHART_MAX_AGE = {
    "5m": 30 * 60,
    "15m": 60 * 60,
    "1h": 6 * 60 * 60,
    "1d": 24 * 60 * 60,
    "1w": 3 * 24 * 60 * 60,
}
_NEW_YORK_TZ = ZoneInfo("America/New_York")
_EXTENDED_QUOTE_OPEN_OUTLIER_RATIO = 0.03
_EXTENDED_QUOTE_CLOSE_REJOIN_RATIO = 0.01


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
    open_price = float(bar["o"])
    close_price = float(bar["c"])
    reference = float(reference_close) if reference_close is not None else None
    if reference is not None and math.isfinite(reference) and reference > 0:
        open_move = abs(open_price / reference - 1)
        close_move = abs(close_price / reference - 1)
        if (
            open_move >= _EXTENDED_QUOTE_OPEN_OUTLIER_RATIO
            and close_move <= _EXTENDED_QUOTE_CLOSE_REJOIN_RATIO
        ):
            # A lone opening quote can be stale even while the bar closes back
            # on the prevailing quote path. Anchor only that anomalous open to
            # the prior accepted close; a close that genuinely moves away from
            # the reference never enters this branch.
            open_price = reference
            bar["o"] = reference
    body_high = max(open_price, close_price)
    body_low = min(open_price, close_price)
    if body_low <= 0:
        return None
    if (body_high - body_low) / body_low > 0.08:
        return None
    if reference is not None and math.isfinite(reference) and reference > 0:
        ref_move = max(abs(open_price / reference - 1), abs(close_price / reference - 1))
        if ref_move > 0.20:
            return None
    # A zero-volume extended-hours row represents a quote path, not traded
    # OHLC. Yahoo may still attach stale or crossed quote extrema to High/Low;
    # keeping those values lets downstream candlestick renderers draw phantom
    # wicks. Preserve the quoted open/close movement, but make the public OHLC
    # envelope match that movement. Bars with reported volume return above and
    # therefore retain every genuine extreme unchanged.
    bar["h"] = body_high
    bar["l"] = body_low
    bar["quote_only"] = True
    return bar


@router.get("/{ticker}/chart")
async def stock_chart(
    ticker: str,
    range: str = Query("1d", pattern="^(5m|15m|1h|1d|1w)$"),
    adjustment: str = Query("raw", pattern="^(raw|adjusted)$"),
):
    owner = current_request_is_owner()
    symbol = quote_symbol(ticker)
    key = f"chart:{symbol}:{range}:{adjustment}"
    if range == "1d" and adjustment == "raw":
        await _hydrate_stock_pull_resource(
            symbol,
            "daily_chart",
            key,
        )
    if owner and symbol == "NVDA" and range == "1d" and adjustment == "raw":
        disk_result = await _reuse_fresh_public_home_entry(
            key,
            "focus_chart",
            {"ticker": symbol, "range": range, "adjustment": adjustment},
            fresh_for_seconds=float(
                get_personal_config().public_home.chart_seconds
            ),
        )
        if disk_result is not None:
            return _sanitize(disk_result)
    try:
        return await _stale_while_revalidate_endpoint(
            key,
            _CHART_TTL.get(range, 600),
            _CHART_MAX_AGE.get(range, 60 * 60),
            lambda: _load_stock_chart(symbol, range, adjustment),
            allow_refresh=owner,
        )
    except HTTPException as exc:
        if owner or not _is_public_snapshot_unavailable(exc):
            raise
        now = time.time()
        result = await read_public_home_resource_async(
            "focus_chart",
            parameters={
                "ticker": symbol,
                "range": range,
                "adjustment": adjustment,
            },
            now=now,
        )
        if result is None:
            try:
                breakout_parameters = breakout_lead_chart_parameters(symbol)
            except ValueError:
                breakout_parameters = None
            if (
                breakout_parameters is not None
                and range == "1d"
                and adjustment == "raw"
            ):
                result = await read_public_home_resource_async(
                    "breakout_lead_chart",
                    parameters=breakout_parameters,
                    now=now,
                )
        if result is None:
            raise exc
        return _sanitize(result)
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Stock chart data is currently unavailable") from exc


async def _load_stock_chart(
    ticker: str,
    range_key: str,
    adjustment: str,
) -> dict[str, Any]:
    """Load a chart without publishing an empty daily refresh.

    A provider can transiently answer with no daily aggregates. Treat that as
    a failed refresh so stale process data, including a durable manual-pull
    snapshot hydrated above, remains the last usable value.
    """

    # Capture BEFORE the provider call. A fetch spanning an interval boundary
    # may still contain its earlier partial bar when the response arrives.
    observed_at = datetime.now(timezone.utc)
    payload = await _stock_chart_impl(ticker, range_key, adjustment)
    if range_key == "1d":
        bars = payload.get("bars") if isinstance(payload, dict) else None
        if not isinstance(bars, list) or not bars:
            raise RuntimeError("Daily stock chart provider returned no bars")
    if range_key in {"5m", "15m", "1h"} and isinstance(payload, dict):
        bars = payload.get("bars") if isinstance(payload.get("bars"), list) else []
        from app.services.technical.chart_analysis import assemble_intraday_analysis, mark_intraday_closed

        marked_bars = mark_intraday_closed(bars, range_key, now=observed_at)
        payload = {**payload, "bars": marked_bars}
        try:
            payload["chart_analysis"] = assemble_intraday_analysis(
                marked_bars,
                ticker=ticker,
                chart_range=range_key,
                adjustment=adjustment,
                now=observed_at,
            )
        except Exception:
            payload["chart_analysis"] = None
    return payload


# 结构分析与图表共享同一份 raw 日线（同一 chart:{T}:1d:raw 缓存键）：
# 叠加线必须落在用户正看着的那套蜡烛上，换一份数据源就会画错位。
_TECHNICAL_TTL = 600
_TECHNICAL_MAX_AGE = 24 * 60 * 60


async def _guest_daily_chart_snapshot(symbol: str) -> dict[str, Any] | None:
    """Guest fallback: same public-home resources the chart route serves."""

    now = time.time()
    result = await read_public_home_resource_async(
        "focus_chart",
        parameters={"ticker": symbol, "range": "1d", "adjustment": "raw"},
        now=now,
    )
    if result is None:
        try:
            breakout_parameters = breakout_lead_chart_parameters(symbol)
        except ValueError:
            breakout_parameters = None
        if breakout_parameters is not None:
            result = await read_public_home_resource_async(
                "breakout_lead_chart",
                parameters=breakout_parameters,
                now=now,
            )
    return result if isinstance(result, dict) else None


async def _spy_closes_by_date(owner: bool) -> dict[str, float] | None:
    """SPY raw daily closes keyed by NY session date. Missing SPY omits RS, never fabricates."""

    try:
        spy = await _stale_while_revalidate_endpoint(
            "chart:SPY:1d:raw",
            _CHART_TTL.get("1d", 600),
            _CHART_MAX_AGE.get("1d", 24 * 60 * 60),
            lambda: _load_stock_chart("SPY", "1d", "raw"),
            allow_refresh=owner,
        )
    except Exception:
        return None
    bars = spy.get("bars") if isinstance(spy, dict) else None
    if not bars:
        return None
    from app.services.technical.structure import clean_series

    series = clean_series(bars)
    if series is None:
        return None
    out: dict[str, float] = {}
    for day, close in zip(series["dates"], series["closes"]):
        if close and close > 0:
            out[str(day)] = float(close)
    return out or None


async def _load_stock_technical(symbol: str, owner: bool) -> dict[str, Any]:
    from app.services.technical.structure import compute_technical_structure

    await _hydrate_stock_pull_resource(symbol, "daily_chart", f"chart:{symbol}:1d:raw")
    chart = await _stale_while_revalidate_endpoint(
        f"chart:{symbol}:1d:raw",
        _CHART_TTL.get("1d", 600),
        _CHART_MAX_AGE.get("1d", 24 * 60 * 60),
        lambda: _load_stock_chart(symbol, "1d", "raw"),
        allow_refresh=owner,
    )
    bars = chart.get("bars") if isinstance(chart, dict) else None
    spy_closes = await _spy_closes_by_date(owner)
    # 结构计算是纯 CPU（≈500 根日线几毫秒），但仍不占事件循环。
    result = await asyncio.to_thread(
        compute_technical_structure,
        bars or [],
        ticker=symbol,
        spy_closes=spy_closes,
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Not enough daily bars for technical structure",
        )
    payload: dict[str, Any] = {
        "ticker": symbol,
        "as_of": chart.get("as_of") if isinstance(chart, dict) else None,
        # raw 日线口径：与 K 线图完全同源；指标在除权日附近以图上所见为准
        "basis": "raw_daily",
        **result,
    }
    analysis = payload.get("chart_analysis")
    if isinstance(analysis, dict):
        analysis["ticker"] = symbol
        analysis["range"] = "1d"
        analysis["adjustment"] = "raw"
    return payload


@router.get("/{ticker}/technical")
async def stock_technical(ticker: str):
    """K-line structure + indicators computed from the same daily bars the
    chart endpoint serves (base band, invalidation, swings, RSI/MACD family)."""

    owner = current_request_is_owner()
    symbol = quote_symbol(ticker)
    key = f"technical:{symbol}"
    try:
        return await _stale_while_revalidate_endpoint(
            key,
            _TECHNICAL_TTL,
            _TECHNICAL_MAX_AGE,
            lambda: _load_stock_technical(symbol, owner),
            allow_refresh=owner,
        )
    except HTTPException as exc:
        if owner or not _is_public_snapshot_unavailable(exc):
            raise
        # 访客冷缓存：先吃手动拉取的日线快照（与 chart 路由同一 hydrate 通路——
        # 访客拉取完，K 线和结构必须同源同现），再退公开快照；现算不回源、不写缓存。
        from app.services.technical.structure import compute_technical_structure

        chart_key = f"chart:{symbol}:1d:raw"
        await _hydrate_stock_pull_resource(symbol, "daily_chart", chart_key)
        pulled = _usable_hit(chart_key, time.time())
        chart = pulled.value if pulled is not None and isinstance(pulled.value, dict) else None
        if chart is None:
            chart = await _guest_daily_chart_snapshot(symbol)
        bars = chart.get("bars") if isinstance(chart, dict) else None
        if not bars:
            raise exc
        result = await asyncio.to_thread(compute_technical_structure, bars, ticker=symbol)
        if result is None:
            raise exc
        payload = _sanitize({
            "ticker": symbol,
            "as_of": chart.get("as_of") if isinstance(chart, dict) else None,
            "basis": "raw_daily",
            **result,
        })
        analysis = payload.get("chart_analysis")
        if isinstance(analysis, dict):
            analysis["ticker"] = symbol
            analysis["range"] = "1d"
            analysis["adjustment"] = "raw"
        return payload


_MASSIVE_CHART_WINDOWS = {
    # range → (multiplier, timespan, 回看天数);窗口略宽于 Yahoo period,余量无害
    "5m": (5, "minute", 10),
    "15m": (15, "minute", 45),
    "1h": (1, "hour", 100),
    "1d": (1, "day", 750),
    "1w": (1, "week", 1850),
}


def _massive_chart_history(provider, symbol: str, range_key: str, adjusted: bool):
    """Massive 聚合条 → yfinance.history 同形状 DataFrame(失败返回 None)。"""
    import pandas as pd

    window = _MASSIVE_CHART_WINDOWS.get(range_key)
    if window is None:
        return None
    multiplier, timespan, lookback_days = window
    end_day = datetime.now(timezone.utc).astimezone(_NEW_YORK_TZ).date()
    start_day = end_day - timedelta(days=lookback_days)
    try:
        bars = provider.ticker_range(
            symbol,
            multiplier,
            timespan,
            start_day.isoformat(),
            end_day.isoformat(),
            adjusted=adjusted,
        )
    except provider.MassiveError:
        return None
    if not bars:
        return None
    index = []
    rows = []
    for bar in bars:
        stamp = bar.get("t")
        if not isinstance(stamp, (int, float)) or stamp <= 0:
            continue
        index.append(datetime.fromtimestamp(stamp / 1000, tz=timezone.utc))
        rows.append(
            {
                "Open": bar.get("o"),
                "High": bar.get("h"),
                "Low": bar.get("l"),
                "Close": bar.get("c"),
                "Volume": bar.get("v") or 0,
            }
        )
    if not rows:
        return None
    return pd.DataFrame(rows, index=pd.DatetimeIndex(index))


async def _stock_chart_impl(ticker: str, range: str, adjustment: str = "raw"):
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
        symbol = quote_symbol(ticker)
        auto_adjust = adjustment == "adjusted"

        def response_metadata(
            *,
            source_status: str,
            bars: list[dict[str, Any]],
            price_provider: str,
        ) -> dict[str, Any]:
            fetched_at = datetime.now(timezone.utc).isoformat()
            last_bar_at = (
                datetime.fromtimestamp(int(bars[-1]["t"]), timezone.utc).isoformat()
                if bars
                else None
            )
            return {
                "ticker": symbol,
                "range": range,
                "period": yf_period,
                "interval": interval,
                "exchange_timezone": "America/New_York",
                "price_adjustment": adjustment,
                "include_extended_hours": prepost,
                "moving_average_scope": "regular_session_only",
                "as_of": fetched_at,
                "last_bar_at": last_bar_at,
                "source_status": source_status,
                "price_provider": price_provider,
                "visible": visible,
            }

        # 主源 Massive:同形状历史帧;失败/未配置/不支持的代码回落 Yahoo。
        from app.services import massive as massive_provider

        hist = None
        price_provider = "Yahoo/yfinance"
        try:
            if massive_provider.configured():
                massive_symbol = massive_provider.to_symbol(symbol)
                if massive_symbol is not None:
                    # Massive 的 adjusted=false 是 Polygon 语义:连拆股都不复权,
                    # 历史K线在拆股日会出现假断崖。始终按拆股复权取数,与 Yahoo
                    # auto_adjust=False(拆股已调、分红不调)的 raw 语义对齐。
                    hist = _massive_chart_history(
                        massive_provider, massive_symbol, range, adjusted=True
                    )
                    if hist is not None and not hist.empty:
                        price_provider = "Massive"
        except Exception:
            hist = None
        if hist is None or hist.empty:
            tk = yf.Ticker(massive_provider.to_yahoo_symbol(symbol))
            hist = tk.history(
                period=yf_period,
                interval=interval,
                prepost=prepost,
                auto_adjust=auto_adjust,
            )
        if hist.empty:
            return {
                **response_metadata(
                    source_status="empty",
                    bars=[],
                    price_provider=price_provider,
                ),
                "bars": [],
                "ema20": [],
                "sma50": [],
            }

        raw_bars_by_time: dict[int, dict[str, Any]] = {}
        for idx, row in hist.iterrows():
            try:
                dt = idx.to_pydatetime()
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=_NEW_YORK_TZ)
                t = int(dt.timestamp())
                o = float(row["Open"])
                h = float(row["High"])
                l = float(row["Low"])
                c = float(row["Close"])
            except Exception:
                continue
            if not all(math.isfinite(v) and v > 0 for v in (o, h, l, c)):
                continue
            if l > min(o, c) or h < max(o, c) or l > h:
                continue
            try:
                volume_raw = float(row.get("Volume", 0))
                if math.isfinite(volume_raw) and volume_raw < 0:
                    continue
                v = int(volume_raw) if math.isfinite(volume_raw) else 0
            except Exception:
                v = 0
            bar = {
                "t": t,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "ext": False,
                "quote_only": False,
            }
            if prepost:
                ny_dt = dt.astimezone(_NEW_YORK_TZ)
                bar["_ny_min"] = ny_dt.hour * 60 + ny_dt.minute
            else:
                bar["session"] = "regular"
            # Yahoo can occasionally return duplicate timestamps after a data
            # repair. Keep the last valid row and sort once below so the public
            # contract is strictly increasing and deterministic.
            raw_bars_by_time[t] = bar

        raw_bars = [raw_bars_by_time[t] for t in sorted(raw_bars_by_time)]

        # For intraday with prepost: keep valid extended-hours bars and tag
        # them so the frontend can visually distinguish pre/post-market.
        if prepost:
            filtered = []
            last_close = None
            for b in raw_bars:
                hour_min = b.pop("_ny_min", None)
                if hour_min is None:
                    continue
                day = datetime.fromtimestamp(b["t"], tz=_NEW_YORK_TZ).date()
                close_minute = early_close_minutes(day) or 16 * 60
                is_regular = is_trading_day(day) and 570 <= hour_min < close_minute
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

        # Extended-hours quotes do not belong in regular-session indicators.
        # Daily and weekly bars are tagged regular above, so the same rule is
        # explicit and consistent for every interval.
        indicator_bars = [b for b in bars if b.get("session") == "regular"]
        closes = [b["c"] for b in indicator_bars]
        times = [b["t"] for b in indicator_bars]

        ema20 = _compute_ema(closes, 20)
        sma50 = _compute_sma(closes, 50)

        ema20_data = [{"time": times[i + len(closes) - len(ema20)], "value": v} for i, v in enumerate(ema20)]
        sma50_data = [{"time": times[i + len(closes) - len(sma50)], "value": v} for i, v in enumerate(sma50)]

        # Send ALL data to frontend — let TradingView handle scrolling
        # visible tells frontend how many bars to show initially
        source_status = "active" if bars else "empty"
        return {
            **response_metadata(
                source_status=source_status,
                bars=bars,
                price_provider=price_provider,
            ),
            "bars": bars,
            "ema20": ema20_data,
            "sma50": sma50_data,
        }

    return _sanitize(await asyncio.to_thread(_work))


async def _run_stock_pull_blocking(function, *args):
    """Run bounded manual-pull CPU/disk work outside the default executor."""

    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        _stock_pull_blocking_executor,
        function,
        *args,
    )


def _finish_stock_pull_task(
    ticker: str,
    task: asyncio.Task[dict[str, Any]],
) -> None:
    if _stock_pull_tasks.get(ticker) is task:
        _stock_pull_tasks.pop(ticker, None)
    if not task.cancelled():
        # Retrieve an exception even when every waiting HTTP client disconnects.
        task.exception()


async def _coalesced_stock_pull(
    ticker: str,
    *,
    public_client_id: str | None = None,
) -> dict[str, Any]:
    """Share one complete in-flight manual pull for the same ticker."""

    task = _stock_pull_tasks.get(ticker)
    # Joining existing provider work does not spend another visitor allowance.
    # The check and task creation below contain no await, so two cold requests
    # cannot both reserve capacity and start duplicate upstream calls.
    if task is None or task.done():
        if public_client_id is not None:
            _reserve_public_stock_pull(public_client_id, ticker)
        task = asyncio.create_task(
            _pull_stock_data_once(ticker),
            name=f"stock-pull:{ticker}",
        )
        _stock_pull_tasks[ticker] = task
        task.add_done_callback(
            lambda completed, symbol=ticker: _finish_stock_pull_task(
                symbol,
                completed,
            )
        )
    # A client disconnect must not cancel the shared provider work while
    # another request is still awaiting the same result.
    return await asyncio.shield(task)


@router.post(
    "/{ticker}/pull",
    dependencies=[Depends(require_same_origin_json)],
)
async def pull_stock_data(
    ticker: str,
    request: Request,
):
    """Refresh overview, daily chart, and derived signals.

    Password-mode visitors may start this bounded same-origin action so a
    Breakout Radar ticker without a saved snapshot can become readable.
    Visitor starts have a per-client budget and a global per-ticker cooldown;
    all callers still share the same in-flight task. The refreshed values are
    published into the exact GET cache keys and a restart-safe snapshot. Each
    resource remains independent: a signal-enrichment failure cannot erase a
    valid quote or daily chart.
    """

    symbol = quote_symbol(ticker)
    if not _WATCHLIST_TICKER_PATTERN.fullmatch(symbol):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_ticker",
                "message": "股票代码格式无效",
            },
        )

    public_client_id: str | None = None
    if not current_request_is_owner():
        # 登录客户按账号限额（换 IP 不重置）；匿名（visitor_live_pulls 开启时
        # 才会到这里）沿用 IP 限额。owner 不限。
        account = request_account_session(request)
        public_client_id = (
            f"acct:{account.user_id}"
            if account is not None
            else request_client_ip(request)
        )

    return await _coalesced_stock_pull(
        symbol,
        public_client_id=public_client_id,
    )


async def _pull_stock_data_once(symbol: str) -> dict[str, Any]:
    overview_key = f"stock:{symbol}"
    chart_key = f"chart:{symbol}:1d:raw"

    async def _load_valid_overview() -> dict[str, Any]:
        payload = await _stock_overview_impl(symbol)
        cleaned = validate_stock_pull_payload(symbol, "overview", payload)
        if cleaned is None:
            raise ValueError("manual stock overview payload is unavailable")
        return cleaned

    async def _load_valid_daily_chart() -> dict[str, Any]:
        payload = await _stock_chart_impl(symbol, "1d", "raw")
        cleaned = validate_stock_pull_payload(symbol, "daily_chart", payload)
        if cleaned is None:
            raise ValueError("manual stock daily chart payload is unavailable")
        return cleaned

    async def _capture_refresh(awaitable):
        try:
            return await awaitable
        except Exception as exc:
            return exc

    overview_result, chart_result = await asyncio.gather(
        _capture_refresh(
            _force_replace_endpoint(
                    overview_key,
                    60,
                    30 * 60,
                    _load_valid_overview,
            )
        ),
        _capture_refresh(
            _force_replace_endpoint(
                    chart_key,
                    _CHART_TTL["1d"],
                    _CHART_MAX_AGE["1d"],
                    _load_valid_daily_chart,
            )
        ),
    )

    async def _build_pulled_signals():
        from app.services import signals as signal_provider

        try:
            # The visible K-line intentionally stays raw. Technical indicators
            # need a separate adjusted history so a split cannot look like a
            # crash or spike. _stock_chart_impl keeps Massive as the primary
            # source and uses Yahoo only when Massive has no usable aggregates.
            chart_payload = await _load_stock_chart(symbol, "1d", "adjusted")
            bars = chart_payload["bars"]

            if isinstance(bars, list) and bars:
                def _compute_from_chart():
                    import pandas as pd

                    frame = pd.DataFrame(
                        {
                            "Open": [bar.get("o") for bar in bars],
                            "High": [bar.get("h") for bar in bars],
                            "Low": [bar.get("l") for bar in bars],
                            "Close": [bar.get("c") for bar in bars],
                            "Volume": [bar.get("v") for bar in bars],
                        },
                        index=pd.DatetimeIndex(
                            [
                                datetime.fromtimestamp(
                                    int(bar["t"]),
                                    timezone.utc,
                                )
                                for bar in bars
                            ]
                        ),
                    )
                    return signal_provider.compute_stock_signals_from_history(
                        symbol,
                        frame,
                        price_provider=(
                            str(chart_payload.get("price_provider"))
                            if chart_payload.get("price_provider")
                            else None
                        ),
                    )

                return await _run_stock_pull_blocking(_compute_from_chart)

        except Exception as exc:
            logger.warning(
                "Adjusted signal history failed for %s (%s); using signal fallback",
                symbol,
                type(exc).__name__,
            )

        # Preserve resource independence. If the independent adjusted history
        # fails, the existing Massive-first signal path gets one honest chance.
        return await _run_stock_pull_blocking(
            signal_provider.compute_stock_signals,
            symbol,
        )

    signals_result = await _capture_refresh(_build_pulled_signals())
    signals_fetched_at = time.time()

    def _failed_resource(name: str, error: Exception) -> dict[str, Any]:
        logger.warning(
            "Manual stock pull failed for %s %s (%s)",
            symbol,
            name,
            type(error).__name__,
        )
        return {
            "status": "failed",
            "error_code": f"{name}_provider_unavailable",
            "persisted": False,
        }

    if isinstance(overview_result, Exception):
        overview_resource = _failed_resource("overview", overview_result)
    else:
        overview_payload = _cache_result(overview_result, stale=False)
        overview_price = (
            overview_payload.get("price")
            if isinstance(overview_payload, dict)
            else None
        )
        overview_available = (
            isinstance(overview_price, (int, float))
            and math.isfinite(float(overview_price))
            and float(overview_price) > 0
        )
        overview_resource = {
            "status": "available" if overview_available else "unavailable",
            "provider": (
                overview_payload.get("price_provider")
                if isinstance(overview_payload, dict)
                else None
            ),
            "as_of": (
                overview_payload.get("as_of")
                if isinstance(overview_payload, dict)
                else None
            )
            or datetime.fromtimestamp(
                overview_result.fetched_at,
                timezone.utc,
            ).isoformat(),
            "persisted": False,
        }

    if isinstance(chart_result, Exception):
        chart_resource = _failed_resource("daily_chart", chart_result)
    else:
        chart_payload = _cache_result(chart_result, stale=False)
        bars = (
            chart_payload.get("bars")
            if isinstance(chart_payload, dict)
            else None
        )
        bar_count = len(bars) if isinstance(bars, list) else 0
        chart_resource = {
            "status": "available" if bar_count > 0 else "unavailable",
            "provider": (
                chart_payload.get("price_provider")
                if isinstance(chart_payload, dict)
                else None
            ),
            "as_of": (
                chart_payload.get("as_of")
                if isinstance(chart_payload, dict)
                else None
            )
            or datetime.fromtimestamp(
                chart_result.fetched_at,
                timezone.utc,
            ).isoformat(),
            "bar_count": bar_count,
            "last_bar_at": (
                chart_payload.get("last_bar_at")
                if isinstance(chart_payload, dict)
                else None
            ),
            "persisted": False,
        }

    if isinstance(signals_result, Exception):
        signals_resource = _failed_resource("signals", signals_result)
    else:
        signal_keys = [
            key
            for key, value in signals_result.items()
            if not key.startswith("_") and isinstance(value, dict)
        ]
        provider_meta = signals_result.get("_price_provider")
        signal_provider = (
            provider_meta.get("value")
            if isinstance(provider_meta, dict)
            else None
        )
        signals_resource = {
            "status": "available" if signal_keys else "unavailable",
            "provider": signal_provider,
            "as_of": datetime.fromtimestamp(
                signals_fetched_at,
                timezone.utc,
            ).isoformat(),
            "metric_count": len(signal_keys),
            "persisted": False,
        }

    resources = {
        "overview": overview_resource,
        "daily_chart": chart_resource,
        "signals": signals_resource,
    }
    available_count = sum(
        resource["status"] == "available"
        for resource in resources.values()
    )
    if available_count == 0:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "stock_pull_failed",
                "message": "真实行情接口暂未返回可用数据，请稍后重试",
                "ticker": symbol,
                "resources": resources,
            },
        )

    persistable: dict[str, tuple[Any, float]] = {}
    if (
        overview_resource["status"] == "available"
        and not isinstance(overview_result, Exception)
    ):
        persistable["overview"] = (
            overview_result.value,
            overview_result.fetched_at,
        )
    if (
        chart_resource["status"] == "available"
        and not isinstance(chart_result, Exception)
    ):
        persistable["daily_chart"] = (
            chart_result.value,
            chart_result.fetched_at,
        )
    if (
        signals_resource["status"] == "available"
        and not isinstance(signals_result, Exception)
    ):
        persistable["signals"] = (
            signals_result,
            signals_fetched_at,
        )

    persistence_status = "completed"
    try:
        persisted = await _run_stock_pull_blocking(
            write_stock_pull_resources,
            symbol,
            persistable,
        )
    except Exception as exc:
        persisted = set()
        persistence_status = "failed"
        logger.warning(
            "Manual stock pull persistence failed for %s (%s)",
            symbol,
            type(exc).__name__,
        )
    for resource_name, resource in resources.items():
        resource["persisted"] = resource_name in persisted

    completed = (
        available_count == len(resources)
        and persistence_status == "completed"
        and len(persisted) == available_count
    )
    return _sanitize(
        {
            "ticker": symbol,
            "status": "completed" if completed else "partial",
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "persistence_status": persistence_status,
            "resources": resources,
        }
    )
