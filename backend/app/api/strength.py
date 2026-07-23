from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from app.access import current_request_is_owner, public_snapshot_unavailable
from app.data_paths import get_data_paths
from app.services.sectors import SECTORS
from app.services.strength.scanner import (
    PROFILES,
    TIMEFRAMES,
    UNIVERSES,
    market_strength,
    profiles,
    sector_strength,
    stock_strength,
)
from app.services.utils import sanitize

router = APIRouter(prefix="/api/strength", tags=["strength"])

_STRENGTH_SNAPSHOT_VERSION = 1
_STRENGTH_SNAPSHOT_TTL_SECONDS = 26 * 60 * 60
# Backward-compatible public name used by existing diagnostics and tests.
STRENGTH_CACHE_TTL_SECONDS = _STRENGTH_SNAPSHOT_TTL_SECONDS
_STRENGTH_SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024
_STRENGTH_SNAPSHOT_VARIANT_LIMIT = 24
_STRENGTH_SNAPSHOT_PATH = get_data_paths().strength_snapshot
DEFAULT_STRENGTH_SCAN_PARAMETERS: dict[str, Any] = {
    "universe": "themes",
    "timeframe": "all",
    "profile": "balanced",
    "top": 20,
    "sector_id": None,
    "min_price": 5.0,
    "min_avg_dollar_volume": 10_000_000.0,
    "include_options": True,
}


def normalize_strength_scan_parameters(value: Any) -> dict[str, Any]:
    """Return the one canonical, bounded representation used by API and worker."""

    if not isinstance(value, dict) or set(value) != set(DEFAULT_STRENGTH_SCAN_PARAMETERS):
        raise ValueError("strength scan parameters are incomplete")
    universe = value.get("universe")
    timeframe = value.get("timeframe")
    profile = value.get("profile")
    top = value.get("top")
    sector_id = value.get("sector_id")
    min_price = value.get("min_price")
    min_avg_dollar_volume = value.get("min_avg_dollar_volume")
    include_options = value.get("include_options")
    if universe not in UNIVERSES:
        raise ValueError("strength scan universe is invalid")
    if timeframe not in TIMEFRAMES:
        raise ValueError("strength scan timeframe is invalid")
    if profile not in PROFILES:
        raise ValueError("strength scan profile is invalid")
    if isinstance(top, bool) or not isinstance(top, int) or not 5 <= top <= 120:
        raise ValueError("strength scan top is invalid")
    if sector_id == "":
        sector_id = None
    if sector_id is not None and (
        not isinstance(sector_id, str) or sector_id not in SECTORS
    ):
        raise ValueError("strength scan sector is invalid")
    for name, item in (
        ("min_price", min_price),
        ("min_avg_dollar_volume", min_avg_dollar_volume),
    ):
        if (
            isinstance(item, bool)
            or not isinstance(item, (int, float))
            or not math.isfinite(float(item))
            or float(item) < 0
        ):
            raise ValueError(f"strength scan {name} is invalid")
    if not isinstance(include_options, bool):
        raise ValueError("strength scan include_options is invalid")
    return {
        "universe": str(universe),
        "timeframe": str(timeframe),
        "profile": str(profile),
        "top": int(top),
        "sector_id": sector_id,
        "min_price": 0.0 if float(min_price) == 0 else float(min_price),
        "min_avg_dollar_volume": (
            0.0
            if float(min_avg_dollar_volume) == 0
            else float(min_avg_dollar_volume)
        ),
        "include_options": include_options,
    }


async def _public_strength_snapshot() -> dict[str, Any]:
    """Read the worker-produced default snapshot without running a scan."""

    parameters = DEFAULT_STRENGTH_SCAN_PARAMETERS
    return await scan(
        universe=str(parameters["universe"]),
        timeframe=str(parameters["timeframe"]),
        profile=str(parameters["profile"]),
        top=int(parameters["top"]),
        sector_id=parameters["sector_id"],
        min_price=float(parameters["min_price"]),
        min_avg_dollar_volume=float(parameters["min_avg_dollar_volume"]),
        include_options=bool(parameters["include_options"]),
    )


def strength_scan_parameters_hash(parameters: dict[str, Any]) -> str:
    canonical = normalize_strength_scan_parameters(parameters)
    encoded = json.dumps(
        canonical,
        ensure_ascii=True,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("ascii")
    return hashlib.sha256(encoded).hexdigest()[:20]


def _strength_snapshot_path(
    parameters: dict[str, Any],
    *,
    base_path: Path | None = None,
) -> Path:
    canonical = normalize_strength_scan_parameters(parameters)
    base = base_path or _STRENGTH_SNAPSHOT_PATH
    if _parameters_match(
        canonical,
        DEFAULT_STRENGTH_SCAN_PARAMETERS,
        exact=True,
    ):
        return base
    digest = strength_scan_parameters_hash(canonical)
    return base.with_name(f"{base.stem}-{digest}{base.suffix}")


def _prune_strength_snapshot_variants(
    base_path: Path,
    *,
    preserve: Path,
    keep: int = _STRENGTH_SNAPSHOT_VARIANT_LIMIT,
) -> None:
    """Bound variant storage without ever deleting the compatibility snapshot."""

    limit = max(1, min(int(keep), _STRENGTH_SNAPSHOT_VARIANT_LIMIT))
    variants: list[tuple[Path, int]] = []
    pattern = f"{base_path.stem}-*{base_path.suffix}"
    variant_name = re.compile(
        rf"^{re.escape(base_path.stem)}-[0-9a-f]{{20}}{re.escape(base_path.suffix)}$"
    )
    try:
        candidates = list(base_path.parent.glob(pattern))
    except OSError:
        return
    for candidate in candidates:
        try:
            if (
                not variant_name.fullmatch(candidate.name)
                or candidate.is_symlink()
                or not candidate.is_file()
            ):
                continue
            modified_at = candidate.stat().st_mtime_ns
        except OSError:
            continue
        variants.append((candidate, modified_at))
    variants.sort(
        key=lambda item: (
            item[0] == preserve,
            item[1],
            item[0].name,
        ),
        reverse=True,
    )
    for candidate, _modified_at in variants[limit:]:
        try:
            candidate.unlink()
        except OSError:
            continue


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for key, value in pairs:
        if key in output:
            raise ValueError(f"duplicate JSON key: {key}")
        output[key] = value
    return output


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


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


def _parameter_value_matches(actual: Any, expected: Any) -> bool:
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return isinstance(actual, bool) and actual is expected
    if isinstance(expected, str):
        return isinstance(actual, str) and actual == expected
    if isinstance(expected, int):
        return (
            not isinstance(actual, bool)
            and isinstance(actual, int)
            and actual == expected
        )
    if isinstance(expected, float):
        return (
            not isinstance(actual, bool)
            and isinstance(actual, (int, float))
            and math.isfinite(float(actual))
            and float(actual) == expected
        )
    return False


def _parameters_match(
    value: Any,
    expected: dict[str, Any],
    *,
    exact: bool,
) -> bool:
    if not isinstance(value, dict):
        return False
    if exact and set(value) != set(expected):
        return False
    return all(
        key in value and _parameter_value_matches(value[key], expected_value)
        for key, expected_value in expected.items()
    )


def _scan_parameters(
    *,
    universe: str,
    timeframe: str,
    profile: str,
    top: int,
    sector_id: str | None,
    min_price: float,
    min_avg_dollar_volume: float,
    include_options: bool,
) -> dict[str, Any]:
    return normalize_strength_scan_parameters({
        "universe": universe,
        "timeframe": timeframe,
        "profile": profile,
        "top": top,
        "sector_id": sector_id,
        "min_price": float(min_price),
        "min_avg_dollar_volume": float(min_avg_dollar_volume),
        "include_options": include_options,
    })


def _clean_strength_snapshot_payload(
    value: Any,
    parameters: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not _is_finite_json_tree(value):
        return None
    payload_parameters = {
        key: item
        for key, item in parameters.items()
        if key != "include_options"
    }
    if not _parameters_match(value.get("params"), payload_parameters, exact=False):
        return None
    rows = value.get("rows")
    results = value.get("results")
    count = value.get("count")
    if (
        not isinstance(value.get("as_of"), str)
        or not value["as_of"].strip()
        or not isinstance(rows, list)
        or not isinstance(results, list)
        or rows != results
        or isinstance(count, bool)
        or not isinstance(count, int)
        or count < 0
        or count != len(rows)
    ):
        return None
    return dict(value)


def _read_strength_snapshot(
    path: Path,
    *,
    parameters: dict[str, Any],
    now: float,
) -> dict[str, Any] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            raw = handle.read(_STRENGTH_SNAPSHOT_MAX_BYTES + 1)
        if not raw or len(raw) > _STRENGTH_SNAPSHOT_MAX_BYTES:
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
            or version != _STRENGTH_SNAPSHOT_VERSION
            or not _parameters_match(
                document.get("parameters"),
                parameters,
                exact=True,
            )
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
        if saved_at <= 0 or saved_at > now:
            return None
        payload = _clean_strength_snapshot_payload(
            document.get("payload"),
            parameters,
        )
        if payload is None:
            return None
        return {
            "saved_at": saved_at,
            "stale": saved_at + _STRENGTH_SNAPSHOT_TTL_SECONDS <= now,
            "payload": payload,
        }
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        return None


def _write_strength_snapshot(
    path: Path,
    *,
    parameters: dict[str, Any],
    payload: Any,
    saved_at: float,
    base_path: Path | None = None,
) -> None:
    parameters = normalize_strength_scan_parameters(parameters)
    base = base_path or (
        path
        if _parameters_match(
            parameters,
            DEFAULT_STRENGTH_SCAN_PARAMETERS,
            exact=True,
        )
        else _STRENGTH_SNAPSHOT_PATH
    )
    if path != _strength_snapshot_path(parameters, base_path=base):
        raise ValueError("strength snapshot path does not match its parameters")
    cleaned = _clean_strength_snapshot_payload(payload, parameters)
    if cleaned is None:
        raise ValueError("strength snapshot payload is invalid")
    if not math.isfinite(saved_at) or saved_at <= 0:
        raise ValueError("strength snapshot saved_at is invalid")
    encoded = json.dumps(
        {
            "version": _STRENGTH_SNAPSHOT_VERSION,
            "saved_at": saved_at,
            "parameters": dict(parameters),
            "payload": cleaned,
        },
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > _STRENGTH_SNAPSHOT_MAX_BYTES:
        raise ValueError("strength snapshot exceeds the size limit")

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
        _prune_strength_snapshot_variants(base, preserve=path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


@router.get("/scan")
async def scan(
    universe: str = Query("themes", pattern="^(themes)$"),
    timeframe: str = Query("all", pattern="^(short|mid|long|all)$"),
    profile: str = Query("balanced", pattern="^(conservative|balanced|aggressive)$"),
    top: int = Query(20, ge=5, le=120),
    sector_id: Optional[str] = Query(None),
    min_price: float = Query(5.0, ge=0),
    min_avg_dollar_volume: float = Query(10_000_000, ge=0),
    include_options: bool = True,
) -> dict[str, Any]:
    """Read a matching Strength Radar snapshot produced by the worker."""
    if universe not in UNIVERSES or timeframe not in TIMEFRAMES or profile not in PROFILES:
        raise HTTPException(status_code=400, detail="Invalid screener parameters")
    try:
        parameters = _scan_parameters(
            universe=universe,
            timeframe=timeframe,
            profile=profile,
            top=top,
            sector_id=sector_id,
            min_price=min_price,
            min_avg_dollar_volume=min_avg_dollar_volume,
            include_options=include_options,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail={"code": "strength_parameters_invalid"},
        ) from exc
    now = time.time()
    snapshot = _read_strength_snapshot(
        _strength_snapshot_path(parameters),
        parameters=parameters,
        now=now,
    )
    if snapshot is None:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "strength_snapshot_unavailable",
                "status": "unavailable",
                "message": "强势雷达后台快照暂不可用",
            },
        )
    saved_at = float(snapshot["saved_at"])
    stale = bool(snapshot["stale"])
    payload = dict(snapshot["payload"])
    payload.update(
        {
            "_cached": True,
            "_stale": stale,
            "source_status": "stale" if stale else "active",
            "cache_ttl_seconds": _STRENGTH_SNAPSHOT_TTL_SECONDS,
            "cache_expires_at": datetime.fromtimestamp(
                saved_at + _STRENGTH_SNAPSHOT_TTL_SECONDS,
                timezone.utc,
            ).isoformat(),
            "snapshot_source": "worker",
            "snapshot_saved_at": datetime.fromtimestamp(
                saved_at,
                timezone.utc,
            ).isoformat(),
        }
    )
    if stale:
        payload.update(
            {
                "stale_reason": "worker_snapshot_expired",
                "stale_age_seconds": round(max(now - saved_at, 0.0), 1),
            }
        )
    return sanitize(payload)


@router.get("/stocks/{ticker}")
async def stock(ticker: str, profile: str = Query("balanced", pattern="^(conservative|balanced|aggressive)$")) -> dict[str, Any]:
    if not current_request_is_owner():
        if profile != DEFAULT_STRENGTH_SCAN_PARAMETERS["profile"]:
            raise public_snapshot_unavailable(f"strength:stock:{ticker}:{profile}")
        payload = await _public_strength_snapshot()
        symbol = ticker.upper().strip()
        for row in payload.get("rows") or payload.get("results") or []:
            if isinstance(row, dict) and row.get("ticker") == symbol:
                return sanitize({
                    "as_of": payload.get("as_of"),
                    "ticker": symbol,
                    "row": row,
                    "market_regime": payload.get("market_regime"),
                    "_cached": True,
                    "snapshot_source": "worker",
                })
        raise HTTPException(
            status_code=404,
            detail=f"Ticker not found in public snapshot: {ticker}",
        )
    try:
        return sanitize(await stock_strength(ticker, profile=profile))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Ticker not found in theme universe: {ticker}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/sectors")
async def sectors(period: str = Query("3mo", pattern="^(1mo|3mo|6mo)$")) -> dict[str, Any]:
    if not current_request_is_owner():
        payload = await _public_strength_snapshot()
        selected_key = f"avg_return_{period}"
        rows = []
        for raw in payload.get("sectors") or []:
            if not isinstance(raw, dict):
                continue
            selected_return = raw.get(selected_key)
            rows.append({
                **raw,
                "period": period,
                "avg_return": selected_return,
                "avg_return_period": selected_return,
            })
        rows.sort(
            key=lambda row: (
                row.get("avg_return") is not None,
                row.get("avg_return")
                if row.get("avg_return") is not None
                else float("-inf"),
                row.get("avg_strength") or 0,
            ),
            reverse=True,
        )
        return sanitize({
            "as_of": payload.get("as_of"),
            "period": period,
            "sectors": rows,
            "count": len(rows),
            "_cached": True,
            "snapshot_source": "worker",
        })
    try:
        return sanitize(await sector_strength(period=period))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/market")
async def market() -> dict[str, Any]:
    if not current_request_is_owner():
        payload = await _public_strength_snapshot()
        regime = payload.get("market_regime")
        if not isinstance(regime, dict):
            raise public_snapshot_unavailable("strength:market")
        return sanitize({
            "as_of": payload.get("as_of"),
            "market_regime": regime,
            "_cached": True,
            "snapshot_source": "worker",
            "cache_ttl_seconds": payload.get("cache_ttl_seconds"),
            "cache_expires_at": payload.get("cache_expires_at"),
        })
    try:
        return sanitize(await market_strength())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Strength data is currently unavailable") from exc


@router.get("/profiles")
async def list_profiles() -> dict[str, Any]:
    return sanitize(profiles())
