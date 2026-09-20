from __future__ import annotations

import asyncio
from collections import deque
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.access import (
    public_snapshot_unavailable,
    require_same_origin_request,
)
from app.data_paths import get_data_paths
from app.services import massive, yahoo
from app.services.request_security import request_client_ip
from app.services.sectors import SECTORS
from app.services.snapshot_read_cache import FingerprintedFileCache
from app.services.zh_names import get_zh_name

router = APIRouter(prefix="/api/sectors", tags=["sectors"])

# Simple TTL cache shared by sector endpoints (10 min — IV ranks change slowly).
# Per-key locks prevent thundering herd: without them, concurrent cold-cache
# requests would each kick off a full sector scan.
_cache: dict[str, tuple[float, float, Any]] = {}
_locks: dict[str, asyncio.Lock] = {}
_MAX_STALE_SECONDS = 60 * 60
_REAL_OPTION_PROVIDERS = frozenset({"Yahoo/yfinance", "MarketData.app"})
_SECTOR_IV_SNAPSHOT_VERSION = 1
_SECTOR_IV_SNAPSHOT_MAX_BYTES = 512 * 1024
_SECTOR_IV_SNAPSHOT_DIR = get_data_paths().root / "sector-iv-snapshots-v1"
_PUBLIC_SECTOR_IV_CLIENT_WINDOW_SECONDS = 5 * 60
_PUBLIC_SECTOR_IV_CLIENT_LIMIT = 8
_PUBLIC_SECTOR_IV_MAX_CLIENTS = 2048
_public_sector_iv_recent: dict[str, deque[float]] = {}


def _lock_for(key: str) -> asyncio.Lock:
    lock = _locks.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[key] = lock
    return lock


def _reserve_public_sector_iv(client_id: str) -> None:
    now = time.monotonic()
    cutoff = now - _PUBLIC_SECTOR_IV_CLIENT_WINDOW_SECONDS
    recent = _public_sector_iv_recent.setdefault(client_id, deque())
    while recent and recent[0] <= cutoff:
        recent.popleft()
    if len(recent) >= _PUBLIC_SECTOR_IV_CLIENT_LIMIT:
        retry_after = max(
            1,
            math.ceil(
                recent[0]
                + _PUBLIC_SECTOR_IV_CLIENT_WINDOW_SECONDS
                - now
            ),
        )
        raise HTTPException(
            status_code=429,
            detail={
                "code": "sector_iv_rate_limited",
                "message": "板块期权拉取较频繁，请稍后重试",
            },
            headers={"Retry-After": str(retry_after)},
        )
    recent.append(now)
    if len(_public_sector_iv_recent) > _PUBLIC_SECTOR_IV_MAX_CLIENTS:
        removable = [
            candidate
            for candidate in _public_sector_iv_recent
            if candidate != client_id
        ]
        oldest_client = min(
            removable or list(_public_sector_iv_recent),
            key=lambda candidate: (
                _public_sector_iv_recent[candidate][-1]
                if _public_sector_iv_recent[candidate]
                else float("-inf")
            ),
        )
        _public_sector_iv_recent.pop(oldest_client, None)


def _with_cache_status(value: Any, *, fetched_at: float, cache_stale: bool) -> Any:
    if not isinstance(value, dict):
        return value
    result = dict(value)
    stale = cache_stale or bool(result.get("_stale"))
    result["_stale"] = stale
    result.setdefault("as_of", datetime.fromtimestamp(fetched_at, timezone.utc).isoformat())
    if cache_stale:
        result["source_status"] = "stale"
        result["stale_age_seconds"] = round(max(time.time() - fetched_at, 0.0), 1)
    else:
        result.setdefault("source_status", "active")
    return result


async def _cached(
    key: str,
    ttl: int,
    loader,
    *,
    max_stale_seconds: int = _MAX_STALE_SECONDS,
    allow_refresh: bool = True,
):
    now = time.time()
    hit = _cache.get(key)
    if hit and hit[0] > now:
        return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=False)
    if not allow_refresh:
        if hit and now - hit[1] <= max(0, max_stale_seconds):
            return _with_cache_status(
                hit[2],
                fetched_at=hit[1],
                cache_stale=True,
            )
        raise public_snapshot_unavailable(key)
    async with _lock_for(key):
        now = time.time()
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=False)
        try:
            value = await loader()
        except Exception:
            if hit:
                stale_age = now - hit[1]
                if stale_age <= max(0, max_stale_seconds):
                    return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=True)
                _cache.pop(key, None)
            raise
        if isinstance(value, dict) and value.get("source_status") == "insufficient_data":
            if hit:
                stale_age = now - hit[1]
                if stale_age <= max(0, max_stale_seconds):
                    return _with_cache_status(hit[2], fetched_at=hit[1], cache_stale=True)
                _cache.pop(key, None)
                raise RuntimeError(f"Sector data source unavailable and stale cache exceeded {max_stale_seconds}s")
            return _with_cache_status(value, fetched_at=now, cache_stale=False)
        fetched_at = time.time()
        _cache[key] = (fetched_at + ttl, fetched_at, value)
        return _with_cache_status(value, fetched_at=fetched_at, cache_stale=False)


def ensure_sector(sector_id: str) -> None:
    if sector_id not in SECTORS:
        raise HTTPException(status_code=404, detail=f"Unknown sector: {sector_id}")


@router.get("")
async def list_sectors():
    return {"sectors": [{"id": id_, "name": data["name"], "tickers": data["tickers"]} for id_, data in SECTORS.items()]}


async def _sector_iv_rows(sector_id: str) -> list[dict[str, Any]]:
    """Fetch price + ATM IV for every sector ticker IN PARALLEL.

    The old implementation looped tickers sequentially in one thread and even
    called the very slow yfinance `.info` scrape just for a display name —
    a 14-ticker sector took 30-60s cold. Names now come from the local
    zh_names dictionary, and per-ticker work runs in a bounded thread pool.
    """
    sector = SECTORS[sector_id]
    sem = asyncio.Semaphore(8)
    massive_prices: dict[str, float] = {}

    # IV itself needs a real option chain, which Stocks Starter does not
    # provide.  The accompanying stock price can still use Massive first in
    # one bounded batch; unsupported symbols or plan failures fall back to
    # Yahoo below without discarding the Yahoo IV result.
    if massive.configured():
        massive_symbols = {
            ticker: symbol
            for ticker in sector["tickers"]
            if (symbol := massive.to_symbol(ticker)) is not None
            and not symbol.startswith("I:")
        }
        if massive_symbols:
            try:
                snapshots = await asyncio.to_thread(
                    massive.snapshot_batch,
                    list(massive_symbols.values()),
                )
            except massive.MassiveError:
                snapshots = {}
            for ticker, symbol in massive_symbols.items():
                snapshot = snapshots.get(symbol)
                if not isinstance(snapshot, dict):
                    continue
                minute_quote = massive.snapshot_minute_quote(snapshot)
                if minute_quote is not None:
                    massive_prices[ticker] = minute_quote[0]

    def _one(ticker: str) -> dict[str, Any] | None:
        try:
            snapshot = yahoo.get_stock_iv_snapshot(ticker)
            iv = snapshot.get("atm_iv")
            if iv is None:
                return {"ticker": ticker, "iv": None, "source_status": "insufficient_data"}
            price = massive_prices.get(ticker)
            price_provider = "Massive" if price is not None else "Yahoo/yfinance"
            if price is None:
                try:
                    price = yahoo.get_last_price(ticker)
                except Exception:
                    price = None
                    price_provider = None
            return {
                "ticker": ticker,
                "name": get_zh_name(ticker) or ticker,
                "price": round(price, 2) if price is not None else None,
                "price_provider": price_provider if price is not None else None,
                "iv": iv,
                "_stale": bool(snapshot.get("_stale")),
                "as_of": snapshot.get("as_of"),
                "source_status": snapshot.get("source_status") or "active",
                "provider": "Yahoo/yfinance",
            }
        except Exception:
            return {"ticker": ticker, "iv": None, "source_status": "error"}

    async def bounded(ticker: str):
        async with sem:
            return await asyncio.to_thread(_one, ticker)

    results = await asyncio.gather(*[bounded(t) for t in sector["tickers"]], return_exceptions=True)
    return [r for r in results if isinstance(r, dict)]


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rank_iv_rows(sector_id: str, rows: list[dict[str, Any]]) -> dict[str, Any]:
    sector = SECTORS[sector_id]
    valid_rows = [row for row in rows if (iv := _finite_number(row.get("iv"))) is not None and 0 < iv <= 10]
    iv_values = [float(row["iv"]) for row in valid_rows]

    rankings = []
    for row in valid_rows:
        iv = float(row["iv"])
        if len(iv_values) == 1:
            # 单个样本没有可辩护的板块内分位——50.0 会被当成「真的居中」。
            # 绝对 IV 照常给出，分位留空由界面渲染「—」。
            sector_rank = None
        else:
            below = sum(1 for candidate in iv_values if candidate < iv)
            tied = sum(1 for candidate in iv_values if candidate == iv)
            sector_rank = (below + (tied - 1) / 2) / (len(iv_values) - 1) * 100
        atm_iv_percent = round(iv * 100, 1)
        rankings.append({
            "ticker": row["ticker"],
            "name": row.get("name") or row["ticker"],
            "price": row.get("price"),
            "price_provider": row.get("price_provider"),
            "atm_iv_percent": atm_iv_percent,
            "sector_iv_rank": round(sector_rank, 1) if sector_rank is not None else None,
            "iv_rank": None,
            "iv_percentile": None,
            # Deprecated aliases retain their true unit: current absolute IV%.
            "iv_pct": atm_iv_percent,
            "iv_current": atm_iv_percent,
            "iv_change_30d": None,
            "_stale": bool(row.get("_stale")),
            "as_of": row.get("as_of"),
        })
    rankings.sort(
        key=lambda ranking: (
            ranking["sector_iv_rank"] if ranking["sector_iv_rank"] is not None else -1.0,
            ranking["atm_iv_percent"],
        ),
        reverse=True,
    )

    total = len(sector["tickers"])
    successful_symbols = {row["ticker"] for row in valid_rows}
    failed_symbols = [ticker for ticker in sector["tickers"] if ticker not in successful_symbols]
    stale = any(bool(row.get("_stale")) for row in valid_rows)
    source_status = "insufficient_data" if not rankings else ("stale" if stale else ("degraded" if len(rankings) < total else "active"))
    as_of_values = [str(row.get("as_of")) for row in valid_rows if row.get("as_of")]
    return {
        "sector_id": sector_id,
        "sector_name": sector["name"],
        "rankings": rankings,
        "data_limited": len(rankings) < total,
        "source_status": source_status,
        "_stale": stale,
        "as_of": min(as_of_values, key=lambda value: _source_timestamp(value) or 0) if as_of_values else None,
        "success_count": len(rankings),
        "requested_count": total,
        "success_rate": round(len(rankings) / total * 100, 1) if total else 0.0,
        "failed_symbols": failed_symbols,
        "providers": sorted(
            {
                str(row.get("provider"))
                for row in valid_rows
                if row.get("provider")
            }
        ),
    }


async def _iv_ranking_payload(sector_id: str) -> dict:
    rows = await _sector_iv_rows(sector_id)
    return _rank_iv_rows(sector_id, rows)


def _sector_iv_snapshot_path(sector_id: str) -> Path:
    if sector_id not in SECTORS:
        raise ValueError("sector snapshot id is invalid")
    return _SECTOR_IV_SNAPSHOT_DIR / f"{sector_id}.json"


def _reject_duplicate_json_keys(
    pairs: list[tuple[str, Any]],
) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _clean_sector_iv_snapshot_payload(
    sector_id: str,
    value: Any,
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or value.get("sector_id") != sector_id:
        return None
    rankings = value.get("rankings")
    if not isinstance(rankings, list) or not rankings:
        return None
    allowed_tickers = set(SECTORS[sector_id]["tickers"])
    seen: set[str] = set()
    for raw in rankings:
        if not isinstance(raw, dict):
            return None
        ticker = raw.get("ticker")
        if (
            not isinstance(ticker, str)
            or ticker not in allowed_tickers
            or ticker in seen
        ):
            return None
        seen.add(ticker)
        atm_iv = _finite_number(raw.get("atm_iv_percent"))
        sector_rank = _finite_number(raw.get("sector_iv_rank"))
        price = raw.get("price")
        if (
            atm_iv is None
            or atm_iv <= 0
            or atm_iv > 1000
            or (raw.get("sector_iv_rank") is not None and (sector_rank is None or not 0 <= sector_rank <= 100))
            or (raw.get("sector_iv_rank") is None and len(rankings) != 1)
            or (
                price is not None
                and (
                    (price_number := _finite_number(price)) is None
                    or price_number <= 0
                )
            )
        ):
            return None
    success_count = value.get("success_count")
    requested_count = value.get("requested_count")
    if (
        isinstance(success_count, bool)
        or not isinstance(success_count, int)
        or success_count != len(rankings)
        or isinstance(requested_count, bool)
        or not isinstance(requested_count, int)
        or requested_count != len(allowed_tickers)
    ):
        return None
    failed_symbols = value.get("failed_symbols")
    if (
        not isinstance(failed_symbols, list)
        or any(
            not isinstance(symbol, str) or symbol not in allowed_tickers
            for symbol in failed_symbols
        )
    ):
        return None
    try:
        json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except (TypeError, ValueError):
        return None
    return dict(value)


def _write_sector_iv_snapshot(
    sector_id: str,
    payload: Any,
    *,
    saved_at: float,
    snapshot_origin: str = "owner_live",
) -> None:
    target = _sector_iv_snapshot_path(sector_id)
    cleaned = _clean_sector_iv_snapshot_payload(sector_id, payload)
    if cleaned is None:
        raise ValueError("sector snapshot payload is invalid")
    if (
        isinstance(saved_at, bool)
        or not math.isfinite(saved_at)
        or saved_at <= 0
    ):
        raise ValueError("sector snapshot saved_at is invalid")
    if snapshot_origin not in {"owner_live", "public_live", "worker"}:
        raise ValueError("sector snapshot origin is invalid")
    encoded = json.dumps(
        {
            "version": _SECTOR_IV_SNAPSHOT_VERSION,
            "saved_at": saved_at,
            "sector_id": sector_id,
            "snapshot_origin": snapshot_origin,
            "payload": cleaned,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _SECTOR_IV_SNAPSHOT_MAX_BYTES:
        raise ValueError("sector snapshot exceeds the size limit")

    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=target.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


# Validated per-sector IV snapshots keyed by file identity (atomic publish
# swaps the inode). The sector catalog is bounded, so one entry per sector.
_sector_iv_documents = FingerprintedFileCache(
    "sector_iv",
    max_paths=32,
    max_bytes=32 * _SECTOR_IV_SNAPSHOT_MAX_BYTES,
)


def _parse_sector_iv_document(
    raw: bytes,
    *,
    sector_id: str,
    observed: float,
) -> dict[str, Any] | None:
    if not raw:
        return None
    document = json.loads(
        raw.decode("utf-8"),
        object_pairs_hook=_reject_duplicate_json_keys,
        parse_constant=_reject_non_finite_json,
    )
    if (
        not isinstance(document, dict)
        or document.get("version") != _SECTOR_IV_SNAPSHOT_VERSION
        or document.get("sector_id") != sector_id
    ):
        return None
    saved_at = document.get("saved_at")
    if (
        isinstance(saved_at, bool)
        or not isinstance(saved_at, (int, float))
        or not math.isfinite(float(saved_at))
        or float(saved_at) <= 0
        or float(saved_at) > observed
    ):
        return None
    payload = _clean_sector_iv_snapshot_payload(
        sector_id,
        document.get("payload"),
    )
    if payload is None:
        return None
    snapshot_origin = document.get("snapshot_origin", "legacy")
    if snapshot_origin not in {"owner_live", "public_live", "worker", "legacy"}:
        return None
    return {
        "saved_at": float(saved_at),
        "payload": payload,
        "snapshot_origin": snapshot_origin,
    }


def _read_sector_iv_snapshot(
    sector_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    target = _sector_iv_snapshot_path(sector_id)
    observed = time.time() if now is None else float(now)
    try:
        if target.is_symlink():
            return None
        document = _sector_iv_documents.read(
            target,
            lambda raw: _parse_sector_iv_document(
                raw, sector_id=sector_id, observed=observed
            ),
            now=observed,
            max_bytes=_SECTOR_IV_SNAPSHOT_MAX_BYTES,
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
    if document is None:
        return None

    saved_at = float(document["saved_at"])
    # Copy before decorating: the parsed document is shared by the cache.
    payload = dict(document["payload"])
    source_at = _source_timestamp(payload.get("as_of"))
    from app.services.sector_iv_refresh import MAX_STALE_SECONDS, refresh_interval
    if source_at is None or source_at > observed + 60 or observed - source_at > MAX_STALE_SECONDS:
        return None
    stale = observed - source_at >= refresh_interval(observed) or bool(payload.get("_stale"))
    payload.update(
        {
            "_cached": True,
            "_stale": stale,
            "snapshot_source": "sector_snapshot",
            "snapshot_origin": document["snapshot_origin"],
            "snapshot_saved_at": datetime.fromtimestamp(
                saved_at,
                timezone.utc,
            ).isoformat(),
            "cache_ttl_seconds": refresh_interval(observed),
        }
    )
    if stale:
        payload["source_status"] = "stale"
        payload["stale_reason"] = (
            "sector_snapshot_expired"
            if observed - source_at >= refresh_interval(observed)
            else "provider_stale"
        )
        payload["stale_age_seconds"] = round(
            max(observed - source_at, 0.0),
            1,
        )
    return payload


def _strength_option_row(
    raw: Any,
    *,
    snapshot_as_of: str,
    snapshot_stale: bool,
) -> dict[str, Any] | None:
    """Project one provider-backed option row from the persisted strength scan."""

    if not isinstance(raw, dict):
        return None
    ticker = str(raw.get("ticker") or "").upper().strip()
    context = raw.get("option_context")
    if not ticker or not isinstance(context, dict):
        return None
    provider = str(context.get("provider") or "").strip()
    if provider not in _REAL_OPTION_PROVIDERS:
        return None
    status = str(
        context.get("source_status") or context.get("status") or "active"
    ).lower()
    if status in {
        "disabled",
        "error",
        "not_configured",
        "skipped",
        "unavailable",
    }:
        return None

    iv_percent = _finite_number(context.get("atm_iv_percent"))
    if iv_percent is None:
        iv_average = _finite_number(context.get("iv_average"))
        if iv_average is not None:
            iv_percent = iv_average * 100 if iv_average <= 10 else iv_average
    if iv_percent is None or iv_percent <= 0 or iv_percent > 1000:
        return None

    price = _finite_number(raw.get("price"))
    stale = snapshot_stale or bool(context.get("_stale")) or status == "stale"
    return {
        "ticker": ticker,
        "name": str(raw.get("name") or get_zh_name(ticker) or ticker),
        "price": round(price, 2) if price is not None and price > 0 else None,
        "iv": iv_percent / 100,
        "_stale": stale,
        "as_of": str(context.get("as_of") or snapshot_as_of),
        "source_status": "stale" if stale else status,
        "provider": provider,
    }


def _read_strength_sector_iv_snapshot(
    sector_id: str,
    *,
    now: float | None = None,
) -> dict[str, Any] | None:
    """Read the worker-owned Strength snapshot; never refresh or write it."""

    from app.api import strength as strength_api

    observed = time.time() if now is None else float(now)
    parameters = dict(strength_api.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    snapshot = strength_api._read_strength_snapshot(
        strength_api._strength_snapshot_path(parameters),
        parameters=parameters,
        now=observed,
    )
    if snapshot is None:
        return None

    strength_payload = snapshot.get("payload")
    if not isinstance(strength_payload, dict):
        return None
    snapshot_as_of = str(
        strength_payload.get("as_of")
        or datetime.fromtimestamp(
            float(snapshot["saved_at"]),
            timezone.utc,
        ).isoformat()
    )
    source_rows = strength_payload.get("option_snapshots")
    if not isinstance(source_rows, list):
        source_rows = strength_payload.get("rows")
    if not isinstance(source_rows, list):
        source_rows = strength_payload.get("results")
    if not isinstance(source_rows, list):
        return None

    snapshot_stale = bool(snapshot.get("stale"))
    projected = [
        row
        for raw in source_rows
        if (
            row := _strength_option_row(
                raw,
                snapshot_as_of=snapshot_as_of,
                snapshot_stale=snapshot_stale,
            )
        )
        is not None
    ]
    sector_tickers = set(SECTORS[sector_id]["tickers"])
    by_ticker = {row["ticker"]: row for row in projected}
    rows = [
        by_ticker.get(ticker)
        or {
            "ticker": ticker,
            "iv": None,
            "source_status": "insufficient_data",
        }
        for ticker in SECTORS[sector_id]["tickers"]
    ]
    payload = _rank_iv_rows(sector_id, rows)
    saved_at = float(snapshot["saved_at"])
    payload.update(
        {
            "_cached": True,
            "snapshot_source": "strength_worker",
            "snapshot_saved_at": datetime.fromtimestamp(
                saved_at,
                timezone.utc,
            ).isoformat(),
            "cache_ttl_seconds": strength_api.STRENGTH_CACHE_TTL_SECONDS,
            "providers": sorted(
                {
                    str(row.get("provider"))
                    for row in projected
                    if row.get("ticker") in sector_tickers and row.get("provider")
                }
            ),
        }
    )
    if not payload["rankings"]:
        payload["as_of"] = snapshot_as_of
    if snapshot_stale:
        payload["_stale"] = True
        if payload["rankings"]:
            payload["source_status"] = "stale"
        payload["stale_reason"] = "strength_worker_snapshot_expired"
        payload["stale_age_seconds"] = round(max(observed - saved_at, 0.0), 1)
    return payload


def _source_timestamp(value: Any) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None
        return parsed.timestamp()
    except (ValueError, TypeError, OverflowError):
        return None


def _bounded_snapshot(payload: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
    from app.services.sector_iv_refresh import MAX_STALE_SECONDS, refresh_interval

    if not payload or not payload.get("rankings"):
        return None
    source_at = _source_timestamp(payload.get("as_of"))
    if source_at is None or source_at > now + 60 or now - source_at > MAX_STALE_SECONDS:
        return None
    result = dict(payload)
    if now - source_at >= refresh_interval(now) or result.get("_stale"):
        result.update(_stale=True, source_status="stale",
                      stale_age_seconds=round(max(now - source_at, 0), 1))
    return result


async def _request_iv_payload(
    sector_id: str,
    *,
    public_client_id: str | None = None,
    visitor_live_allowed: bool = False,
) -> dict[str, Any]:
    """Read saved data and register bounded demand; HTTP never calls providers.

    Legacy keyword arguments remain accepted for internal callers. Access level
    does not change sector IV visibility or refresh eligibility.
    """
    from app.services.sector_iv_refresh import default_store

    now = time.time()
    snapshot = _bounded_snapshot(_read_sector_iv_snapshot(sector_id, now=now), now)
    # A dedicated full scan (even with missing symbols) takes precedence over
    # the unrelated top-N strength selection. A fallback never suppresses work.
    fallback = None if snapshot else _bounded_snapshot(
        _read_strength_sector_iv_snapshot(sector_id, now=now), now,
    )
    store = default_store()
    if snapshot is None or snapshot.get("_stale"):
        refresh = await asyncio.to_thread(store.request, sector_id)
    else:
        refresh = await asyncio.to_thread(store.status, sector_id)
    # Publication and queue state commit happen in the same worker-held
    # transaction. The first file read can race just ahead of that commit,
    # then the state read can observe completion while the response still holds
    # the old payload. Re-read after either state path closes that handoff.
    observed = time.time()
    published = _bounded_snapshot(
        _read_sector_iv_snapshot(sector_id, now=observed),
        observed,
    )
    if published is not None:
        snapshot = published
    payload = snapshot or fallback or _rank_iv_rows(sector_id, [])
    if not payload.get("rankings"):
        payload["as_of"] = None
    return {**payload, "refresh": refresh}


@router.post("/{sector_id}/iv-refresh", dependencies=[Depends(require_same_origin_request)])
async def iv_refresh(sector_id: str, request: Request):
    from app.services.sector_iv_refresh import default_store

    ensure_sector(sector_id)
    _reserve_public_sector_iv(request_client_ip(request))
    refresh = await asyncio.to_thread(default_store().request, sector_id)
    return JSONResponse(
        {"sector_id": sector_id, "refresh": refresh},
        status_code=202 if refresh["status"] in {"queued", "running"} else 200,
    )


@router.get("/{sector_id}/iv-ranking")
async def iv_ranking(sector_id: str, request: Request):
    ensure_sector(sector_id)
    try:
        return await _request_iv_payload(sector_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Sector options data is currently unavailable") from exc


@router.get("/{sector_id}/heatmap")
async def heatmap(sector_id: str, request: Request):
    ensure_sector(sector_id)
    # Reuse the iv-ranking cache — the heatmap is a projection of the same
    # data, so visiting both views costs one scan instead of two.
    try:
        payload = await _request_iv_payload(sector_id)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Sector options data is currently unavailable") from exc
    data = [
        {
            "ticker": item["ticker"],
            "atm_iv_percent": item["atm_iv_percent"],
            "sector_iv_rank": item["sector_iv_rank"],
            "iv_percentile": None,
        }
        for item in payload.get("rankings", [])
    ]
    return {
        "sector_id": sector_id,
        "sector_name": payload.get("sector_name", SECTORS[sector_id]["name"]),
        "data": data,
        "rankings": data,
        "data_limited": payload.get("data_limited", False),
        "source_status": payload.get("source_status"),
        "_stale": payload.get("_stale", False),
        "as_of": payload.get("as_of"),
        "success_count": payload.get("success_count", len(data)),
        "requested_count": payload.get("requested_count", len(SECTORS[sector_id]["tickers"])),
        "failed_symbols": payload.get("failed_symbols", []),
        "snapshot_source": payload.get("snapshot_source"),
        "snapshot_origin": payload.get("snapshot_origin"),
        "snapshot_saved_at": payload.get("snapshot_saved_at"),
        "providers": payload.get("providers", []),
        "refresh": payload["refresh"],
    }
