from __future__ import annotations

import asyncio
from datetime import datetime
import math
import logging
import re
import sqlite3
import time

from fastapi import APIRouter, HTTPException, Query, Request

from app.access import (
    current_request_is_owner,
    public_snapshot_unavailable,
    request_allows_visitor_live_pulls,
)
from app.failure_diagnostics import record_fallback_failure
from app.services import yahoo
from app.services.cache import cache
from app.services.option_capability import (
    canonicalize_option_symbol,
    is_declared_unsupported,
    unsupported_payload,
)
from app.services.utils import sanitize
from app.services.yahoo_option_io import YahooOptionBusy, YahooOptionTimeout
from app.public_option_data import (
    read_option_snapshot,
    option_preparation_status,
    option_snapshot_payload_valid,
    request_option_snapshot,
    write_option_snapshot,
)

router = APIRouter(prefix="/api/options", tags=["options"])
logger = logging.getLogger(__name__)
_OPTION_EXPIRATIONS_TTL = 15 * 60
_OPTION_CHAIN_TTL = 10 * 60
_OPTION_FAILURE_TTL = 30
_OPTION_FAILURE_MAX_KEYS = 256
_OPTION_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$"
)
_option_failure_cache: dict[str, tuple[float, int, object]] = {}


def _option_failure_error(key: str) -> HTTPException | None:
    now = time.monotonic()
    for expired in [
        item_key
        for item_key, (deadline, _status_code, _detail) in _option_failure_cache.items()
        if deadline <= now
    ]:
        _option_failure_cache.pop(expired, None)
    failure = _option_failure_cache.get(key)
    if failure is None:
        return None
    deadline, status_code, detail = failure
    retry_after = max(1, math.ceil(deadline - now))
    return HTTPException(
        status_code=status_code,
        detail=detail,
        headers={"Retry-After": str(retry_after)},
    )


def _record_option_failure(
    key: str,
    *,
    status_code: int,
    detail: object,
) -> HTTPException:
    now = time.monotonic()
    _option_failure_cache[key] = (
        now + _OPTION_FAILURE_TTL,
        status_code,
        detail,
    )
    if len(_option_failure_cache) > _OPTION_FAILURE_MAX_KEYS:
        oldest = min(
            _option_failure_cache,
            key=lambda item_key: _option_failure_cache[item_key][0],
        )
        _option_failure_cache.pop(oldest, None)
    failure = _option_failure_error(key)
    assert failure is not None
    return failure


def _option_symbol(ticker: str) -> str:
    raw = ticker.upper().strip()
    if not _OPTION_TICKER_PATTERN.fullmatch(raw):
        raise HTTPException(
            status_code=400,
            detail={
                "code": "invalid_ticker",
                "message": "股票代码格式无效",
            },
        )
    return canonicalize_option_symbol(raw)


def _validate_expiration_date(expiration: str, *, failure_key: str) -> None:
    try:
        parsed = datetime.strptime(expiration, "%Y-%m-%d")
    except ValueError:
        raise _record_option_failure(
            failure_key,
            status_code=400,
            detail={
                "code": "invalid_option_expiration",
                "message": "期权到期日无效",
            },
        )
    if parsed.strftime("%Y-%m-%d") != expiration:
        raise _record_option_failure(
            failure_key,
            status_code=400,
            detail={
                "code": "invalid_option_expiration",
                "message": "期权到期日无效",
            },
        )


async def _cached_yahoo_option_resource(
    key: str,
    ttl: int,
    loader,
    *,
    allow_live: bool,
):
    """Load one Yahoo option resource behind shared cache and failure cooling.

    Only owners or explicitly enabled visitor pulls may populate a cold key.
    Other visitors can reuse an unexpired result without spending provider
    budget. ``cache.get_or_set`` coalesces authorized callers for the same key.
    """

    cached = cache.get(key)
    if cached is not None:
        return cached

    parts = key.split(":", 3)
    symbol = parts[2]
    expiration = parts[3] if len(parts) == 4 else ""
    saved = await asyncio.to_thread(read_option_snapshot, symbol, expiration)
    if not allow_live:
        try:
            pending = await asyncio.to_thread(request_option_snapshot, symbol, expiration)
        except (OSError, ValueError, RuntimeError, sqlite3.Error):
            pending = False
        if saved is not None:
            return saved
        if pending:
            try:
                preparation = await asyncio.to_thread(option_preparation_status, symbol, expiration)
            except (OSError, ValueError, sqlite3.Error):
                preparation = {"status": "pending", "retry_after_seconds": 30}
            cooling = preparation["status"] == "cooling"
            raise HTTPException(
                status_code=503,
                detail={"code": "yahoo_options_unavailable" if cooling else "public_option_snapshot_pending", "retryable": True,
                        "message": "期权数据暂未取得，后台将继续重试" if cooling else "后台正在准备期权数据，稍后可重新读取"},
                headers={"Retry-After": str(preparation["retry_after_seconds"])},
            )
        raise public_snapshot_unavailable(key)

    if saved is not None and not saved["cache_stale"]:
        return saved

    failure = _option_failure_error(key)
    if failure is not None:
        if saved is not None:
            return {**saved, "cache_stale": True, "_stale": True, "source_status": "stale"}
        raise failure

    async def produce():
        locked_failure = _option_failure_error(key)
        if locked_failure is not None:
            raise locked_failure
        try:
            payload = await asyncio.to_thread(loader)
            if not option_snapshot_payload_valid(symbol, expiration, payload):
                raise ValueError("provider returned an unusable option snapshot")
            if not expiration and not payload["expirations"] and saved and saved["expirations"]:
                raise ValueError("empty discovery cannot replace a usable saved expiration list")
            return payload
        except ValueError as exc:
            # Provider parse/format errors are not user expiration mistakes.
            raise _record_option_failure(
                key,
                status_code=503,
                detail={
                    "code": "yahoo_options_unavailable",
                    "message": "Yahoo/yfinance 期权数据暂不可用",
                },
            ) from exc
        except HTTPException as exc:
            raise _record_option_failure(
                key,
                status_code=exc.status_code,
                detail=exc.detail,
            ) from exc
        except (YahooOptionBusy, YahooOptionTimeout) as exc:
            raise _record_option_failure(
                key,
                status_code=503,
                detail={
                    "code": "yahoo_options_unavailable",
                    "message": "Yahoo/yfinance 期权数据暂不可用",
                },
            ) from exc
        except Exception as exc:
            raise _record_option_failure(
                key,
                status_code=503,
                detail={
                    "code": "yahoo_options_unavailable",
                    "message": "Yahoo/yfinance 期权数据暂不可用",
                },
            ) from exc

    try:
        payload = await cache.get_or_set(key, ttl, produce)
    except HTTPException:
        if saved is not None:
            return {**saved, "cache_stale": True, "_stale": True, "source_status": "stale"}
        raise
    # Persist successful authorized reads too. The worker and HTTP process use
    # the same bounded store, so one process restart cannot erase public data.
    try:
        await asyncio.to_thread(write_option_snapshot, symbol, sanitize(payload), expiration)
    except (OSError, ValueError, RuntimeError, sqlite3.Error):
        # A storage failure must not turn a valid authorized response into a
        # failed provider query; periodic worker preparation will retry it.
        logger.warning("Could not persist public option snapshot for %s", symbol, exc_info=True)
    _option_failure_cache.pop(key, None)
    return payload


def _load_expirations_snapshot(symbol: str) -> dict:
    snapshot = yahoo.get_expirations_snapshot(symbol)
    expirations = snapshot.get("expirations")
    if not isinstance(expirations, list):
        raise RuntimeError("Yahoo returned no option expirations")
    if snapshot.get("options_status") == "unsupported_by_provider":
        return snapshot
    if not expirations:
        snapshot = {
            **snapshot,
            "options_status": snapshot.get("options_status") or "empty_unconfirmed",
            "retryable": True,
            "ticker": symbol,
            "provider": snapshot.get("provider") or "Yahoo/yfinance",
        }
    return snapshot


@router.get("/{ticker}/expirations")
async def expirations(ticker: str, request: Request = None):
    symbol = _option_symbol(ticker)
    if is_declared_unsupported(symbol):
        return unsupported_payload(symbol)
    key = f"options:expirations:{symbol}"

    try:
        snapshot = await _cached_yahoo_option_resource(
            key,
            _OPTION_EXPIRATIONS_TTL,
            lambda: _load_expirations_snapshot(symbol),
            allow_live=(
                current_request_is_owner()
                or request_allows_visitor_live_pulls(request)
            ),
        )
        return {
            **snapshot,
            "ticker": symbol,
            "provider": "Yahoo/yfinance",
        }
    except HTTPException:
        raise
    except Exception as e:
        record_fallback_failure("options_expirations_unavailable", e, symbol=symbol)
        raise HTTPException(
            status_code=503,
            detail="Yahoo options data is currently unavailable",
        ) from e


@router.get("/{ticker}/chain")
async def option_chain(
    ticker: str,
    expiration: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    request: Request = None,
):
    symbol = _option_symbol(ticker)
    if is_declared_unsupported(symbol):
        return {
            **unsupported_payload(symbol),
            "expiration": expiration,
            "underlying_price": None,
            "calls": [],
            "puts": [],
            "alerts": [],
        }
    key = f"options:chain:{symbol}:{expiration}"
    _validate_expiration_date(expiration, failure_key=key)
    allow_live = current_request_is_owner() or request_allows_visitor_live_pulls(request)

    try:
        from app.services.utils import sanitize as _sanitize

        # A chain is validated before it enters this cache and has its own TTL.
        # Its expiration-list entry may have been loaded earlier and already
        # expired; serving the still-fresh chain must not require another pull.
        payload = cache.get(key)
        if payload is None:
            saved = await asyncio.to_thread(read_option_snapshot, symbol, expiration)
            if saved is not None and (not allow_live or not saved["cache_stale"]):
                if not allow_live:
                    try:
                        await asyncio.to_thread(request_option_snapshot, symbol, expiration)
                    except (OSError, ValueError, RuntimeError, sqlite3.Error):
                        logger.warning("Could not request public option preparation for %s", symbol, exc_info=True)
                payload = saved
        if payload is None:
            expiration_snapshot = await _cached_yahoo_option_resource(
                f"options:expirations:{symbol}",
                _OPTION_EXPIRATIONS_TTL,
                lambda: _load_expirations_snapshot(symbol),
                allow_live=allow_live,
            )
            available_expirations = expiration_snapshot.get("expirations")
            if (
                not isinstance(available_expirations, list)
                or expiration not in available_expirations
            ):
                raise _record_option_failure(
                    key,
                    status_code=400,
                    detail={
                        "code": "invalid_option_expiration",
                        "message": "该股票没有这个期权到期日",
                    },
                )
            payload = await _cached_yahoo_option_resource(
                key,
                _OPTION_CHAIN_TTL,
                lambda: yahoo.get_option_chain(symbol, expiration),
                allow_live=allow_live,
            )
        return _sanitize(
            {
                **payload,
                "provider": "Yahoo/yfinance",
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        record_fallback_failure("options_chain_unavailable", e, symbol=symbol)
        raise HTTPException(status_code=503, detail="Yahoo options data is currently unavailable") from e
