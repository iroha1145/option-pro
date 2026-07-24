"""Restart-safe snapshots created by an owner's explicit stock-data pull."""

from __future__ import annotations

from collections import OrderedDict
import copy
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import threading
import time
from typing import Any, Mapping

from app.data_paths import get_data_paths


STOCK_PULL_SNAPSHOT_VERSION = 1
STOCK_PULL_SNAPSHOT_MAX_BYTES = 16 * 1024 * 1024
STOCK_PULL_SNAPSHOT_MAX_TICKERS = 64
STOCK_PULL_MAX_CLOCK_SKEW_SECONDS = 5 * 60
STOCK_PULL_RESOURCE_FRESH_SECONDS = {
    "overview": 60,
    "daily_chart": 5 * 60,
    "signals": 5 * 60,
}
STOCK_PULL_RESOURCE_MAX_AGE_SECONDS = {
    "overview": 24 * 60 * 60,
    "daily_chart": 7 * 24 * 60 * 60,
    "signals": 7 * 24 * 60 * 60,
}

_TICKER_PATTERN = re.compile(
    r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$"
)
_SIGNAL_REQUIRED_KEYS = {"rsi14", "return_20d", "macd_hist"}
_snapshot_lock = threading.RLock()
_SNAPSHOT_DOCUMENT_CACHE_MAX_PATHS = 8
_snapshot_document_cache: OrderedDict[
    str,
    tuple[tuple[int, int, int], dict[str, dict[str, dict[str, Any]]]],
] = OrderedDict()


def stock_pull_snapshot_path(path: Path | None = None) -> Path:
    return path or (get_data_paths().root / "stock-pull-snapshots-v1.json")


def _snapshot_cache_key(path: Path) -> str:
    return os.path.abspath(os.fspath(path))


def _path_has_symlink_boundary(path: Path) -> bool:
    """Reject the target and its containing directory when either is a link."""

    try:
        return path.is_symlink() or path.parent.is_symlink()
    except OSError:
        return True


def _regular_file_identity(value: os.stat_result) -> tuple[int, int, int] | None:
    if not stat.S_ISREG(value.st_mode):
        return None
    return (int(value.st_ino), int(value.st_mtime_ns), int(value.st_size))


def _path_file_identity(path: Path) -> tuple[int, int, int] | None:
    try:
        return _regular_file_identity(os.stat(path, follow_symlinks=False))
    except OSError:
        return None


def _cache_document(
    path: Path,
    identity: tuple[int, int, int],
    entries: dict[str, dict[str, dict[str, Any]]],
) -> None:
    key = _snapshot_cache_key(path)
    _snapshot_document_cache[key] = (identity, entries)
    _snapshot_document_cache.move_to_end(key)
    while len(_snapshot_document_cache) > _SNAPSHOT_DOCUMENT_CACHE_MAX_PATHS:
        _snapshot_document_cache.popitem(last=False)


def _cached_document(
    path: Path,
    identity: tuple[int, int, int],
) -> dict[str, dict[str, dict[str, Any]]] | None:
    key = _snapshot_cache_key(path)
    cached = _snapshot_document_cache.get(key)
    if cached is None or cached[0] != identity:
        if cached is not None:
            _snapshot_document_cache.pop(key, None)
        return None
    _snapshot_document_cache.move_to_end(key)
    return cached[1]


def _drop_cached_document(path: Path) -> None:
    _snapshot_document_cache.pop(_snapshot_cache_key(path), None)


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite_json(value: str) -> None:
    raise ValueError(f"non-finite JSON value: {value}")


def _finite_number(
    value: Any,
    *,
    positive: bool = False,
    allow_zero: bool = True,
) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    number = float(value)
    if not math.isfinite(number):
        return False
    if positive:
        return number > 0
    return allow_zero or number != 0


def _valid_json_tree(
    value: Any,
    *,
    depth: int = 0,
    budget: list[int] | None = None,
) -> bool:
    if budget is None:
        budget = [100_000]
    budget[0] -= 1
    if budget[0] < 0 or depth > 12:
        return False
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, str):
        return len(value) <= 262_144
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return math.isfinite(float(value))
    if isinstance(value, list):
        return len(value) <= 10_000 and all(
            _valid_json_tree(item, depth=depth + 1, budget=budget)
            for item in value
        )
    if isinstance(value, dict):
        return len(value) <= 2_048 and all(
            isinstance(key, str)
            and len(key) <= 256
            and _valid_json_tree(item, depth=depth + 1, budget=budget)
            for key, item in value.items()
        )
    return False


def _clean_overview(ticker: str, payload: Any) -> dict[str, Any] | None:
    if (
        not isinstance(payload, dict)
        or payload.get("ticker") != ticker
        or not _finite_number(payload.get("price"), positive=True)
    ):
        return None
    cleaned = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "_stale",
            "source_status",
            "stale_age_seconds",
            "stale_reason",
        }
    }
    return cleaned if _valid_json_tree(cleaned) else None


def _clean_daily_chart(ticker: str, payload: Any) -> dict[str, Any] | None:
    if (
        not isinstance(payload, dict)
        or payload.get("ticker") != ticker
        or payload.get("range") != "1d"
        or payload.get("price_adjustment") != "raw"
    ):
        return None
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars or len(bars) > 2_000:
        return None
    last_timestamp = -1
    for bar in bars:
        if not isinstance(bar, dict):
            return None
        timestamp = bar.get("t")
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp <= last_timestamp
            or not all(
                _finite_number(bar.get(field), positive=True)
                for field in ("o", "h", "l", "c")
            )
            or not _finite_number(bar.get("v"), allow_zero=True)
            or float(bar["v"]) < 0
            or float(bar["l"]) > min(float(bar["o"]), float(bar["c"]))
            or float(bar["h"]) < max(float(bar["o"]), float(bar["c"]))
        ):
            return None
        last_timestamp = timestamp
    cleaned = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "_stale",
            "stale_age_seconds",
            "stale_reason",
        }
    }
    return cleaned if _valid_json_tree(cleaned) else None


def _clean_signals(_ticker: str, payload: Any) -> dict[str, Any] | None:
    if not isinstance(payload, dict) or not _SIGNAL_REQUIRED_KEYS.issubset(payload):
        return None
    for key in _SIGNAL_REQUIRED_KEYS:
        metric = payload.get(key)
        if not isinstance(metric, dict):
            return None
        value = metric.get("value")
        if value is not None and not _finite_number(value, allow_zero=True):
            return None
    cleaned = {
        key: value
        for key, value in payload.items()
        if key != "_cached"
    }
    return cleaned if _valid_json_tree(cleaned) else None


def _clean_resource(
    ticker: str,
    resource: str,
    payload: Any,
) -> dict[str, Any] | None:
    if resource == "overview":
        return _clean_overview(ticker, payload)
    if resource == "daily_chart":
        return _clean_daily_chart(ticker, payload)
    if resource == "signals":
        return _clean_signals(ticker, payload)
    return None


def validate_stock_pull_payload(
    ticker: str,
    resource: str,
    payload: Any,
) -> dict[str, Any] | None:
    """Return the bounded canonical payload or None without mutating storage."""

    symbol = ticker.upper().strip()
    if not _TICKER_PATTERN.fullmatch(symbol):
        return None
    return _clean_resource(symbol, resource, payload)


def _load_validated_document(
    path: Path,
) -> dict[str, dict[str, dict[str, Any]]]:
    """Read and structurally validate one immutable on-disk document version."""

    try:
        if _path_has_symlink_boundary(path):
            _drop_cached_document(path)
            return {}
        flags = os.O_RDONLY
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags)
        try:
            visible = os.fstat(descriptor)
            identity = _regular_file_identity(visible)
            if (
                identity is None
                or identity[2] <= 0
                or identity[2] > STOCK_PULL_SNAPSHOT_MAX_BYTES
            ):
                _drop_cached_document(path)
                return {}
            # The descriptor may still refer to the prior inode after an
            # external atomic replacement. Never reuse its cached document
            # unless the pathname still identifies that same file version.
            if _path_file_identity(path) != identity:
                _drop_cached_document(path)
                return {}
            cached = _cached_document(path, identity)
            if cached is not None:
                return cached
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                raw = handle.read(STOCK_PULL_SNAPSHOT_MAX_BYTES + 1)
        finally:
            os.close(descriptor)
        if not raw or len(raw) > STOCK_PULL_SNAPSHOT_MAX_BYTES:
            _drop_cached_document(path)
            return {}
        # Do not cache a document read from an inode that was replaced while
        # the read was in progress. The next call will load the current file.
        if _path_file_identity(path) != identity:
            _drop_cached_document(path)
            return {}
        document = json.loads(
            raw.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_non_finite_json,
        )
        if (
            not isinstance(document, dict)
            or document.get("version") != STOCK_PULL_SNAPSHOT_VERSION
            or not isinstance(document.get("entries"), dict)
            or len(document["entries"]) > STOCK_PULL_SNAPSHOT_MAX_TICKERS
        ):
            _cache_document(path, identity, {})
            return {}

        cleaned_entries: dict[str, dict[str, dict[str, Any]]] = {}
        for ticker, raw_resources in document["entries"].items():
            if (
                not isinstance(ticker, str)
                or not _TICKER_PATTERN.fullmatch(ticker)
                or not isinstance(raw_resources, dict)
            ):
                continue
            resources: dict[str, dict[str, Any]] = {}
            for resource, raw_entry in raw_resources.items():
                max_age = STOCK_PULL_RESOURCE_MAX_AGE_SECONDS.get(resource)
                if max_age is None or not isinstance(raw_entry, dict):
                    continue
                saved_at = raw_entry.get("saved_at")
                if (
                    isinstance(saved_at, bool)
                    or not isinstance(saved_at, (int, float))
                    or not math.isfinite(float(saved_at))
                ):
                    continue
                saved_at = float(saved_at)
                if saved_at <= 0:
                    continue
                payload = _clean_resource(
                    ticker,
                    resource,
                    raw_entry.get("payload"),
                )
                if payload is None:
                    continue
                resources[resource] = {
                    "saved_at": saved_at,
                    "payload": payload,
                }
            if resources:
                cleaned_entries[ticker] = resources
        _cache_document(path, identity, cleaned_entries)
        return cleaned_entries
    except (
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
        json.JSONDecodeError,
    ):
        _drop_cached_document(path)
        return {}


def _read_document(
    path: Path,
    *,
    now: float,
    keep_expired: bool,
) -> dict[str, dict[str, dict[str, Any]]]:
    validated = _load_validated_document(path)
    visible_entries: dict[str, dict[str, dict[str, Any]]] = {}
    for ticker, resources in validated.items():
        visible_resources: dict[str, dict[str, Any]] = {}
        for resource, entry in resources.items():
            saved_at = float(entry["saved_at"])
            max_age = STOCK_PULL_RESOURCE_MAX_AGE_SECONDS[resource]
            if (
                saved_at > now + STOCK_PULL_MAX_CLOCK_SKEW_SECONDS
                or (not keep_expired and saved_at + max_age <= now)
            ):
                continue
            visible_resources[resource] = entry
        if visible_resources:
            visible_entries[ticker] = visible_resources
    return visible_entries


def read_stock_pull_resource(
    ticker: str,
    resource: str,
    *,
    path: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    symbol = ticker.upper().strip()
    if (
        not _TICKER_PATTERN.fullmatch(symbol)
        or resource not in STOCK_PULL_RESOURCE_MAX_AGE_SECONDS
    ):
        return None
    observed_at = time.time() if now is None else float(now)
    target = stock_pull_snapshot_path(path)
    with _snapshot_lock:
        entry = (
            _read_document(
                target,
                now=observed_at,
                keep_expired=False,
            )
            .get(symbol, {})
            .get(resource)
        )
    if entry is None:
        return None
    saved_at = float(entry["saved_at"])
    return {
        # The validated document cache is shared. Callers may remove transport
        # fields from their response copy, so never expose the cached tree.
        "payload": copy.deepcopy(entry["payload"]),
        "saved_at": saved_at,
        "fresh": (
            saved_at + STOCK_PULL_RESOURCE_FRESH_SECONDS[resource]
            > observed_at
        ),
        "max_age": STOCK_PULL_RESOURCE_MAX_AGE_SECONDS[resource],
    }


def write_stock_pull_resources(
    ticker: str,
    resources: Mapping[str, tuple[Any, float]],
    *,
    path: Path | None = None,
    now: float | None = None,
) -> set[str]:
    symbol = ticker.upper().strip()
    if not _TICKER_PATTERN.fullmatch(symbol):
        raise ValueError("stock pull ticker is invalid")
    observed_at = time.time() if now is None else float(now)
    cleaned_updates: dict[str, dict[str, Any]] = {}
    for resource, item in resources.items():
        if (
            resource not in STOCK_PULL_RESOURCE_MAX_AGE_SECONDS
            or not isinstance(item, tuple)
            or len(item) != 2
        ):
            raise ValueError("stock pull resource is invalid")
        payload, saved_at = item
        if (
            isinstance(saved_at, bool)
            or not isinstance(saved_at, (int, float))
            or not math.isfinite(float(saved_at))
            or float(saved_at) <= 0
            or float(saved_at) > observed_at + STOCK_PULL_MAX_CLOCK_SKEW_SECONDS
        ):
            raise ValueError("stock pull saved_at is invalid")
        cleaned = validate_stock_pull_payload(symbol, resource, payload)
        if cleaned is None:
            raise ValueError(f"stock pull {resource} payload is invalid")
        cleaned_updates[resource] = {
            "saved_at": float(saved_at),
            "payload": cleaned,
        }
    if not cleaned_updates:
        return set()

    target = stock_pull_snapshot_path(path)
    with _snapshot_lock:
        if _path_has_symlink_boundary(target):
            raise ValueError(
                "stock pull snapshot path must not cross a symlink"
            )
        entries = copy.deepcopy(
            _read_document(
                target,
                now=observed_at,
                keep_expired=False,
            )
        )
        existing = dict(entries.get(symbol, {}))
        existing.update(cleaned_updates)
        entries[symbol] = existing

        if len(entries) > STOCK_PULL_SNAPSHOT_MAX_TICKERS:
            newest = sorted(
                entries.items(),
                key=lambda item: max(
                    float(resource["saved_at"])
                    for resource in item[1].values()
                ),
                reverse=True,
            )[:STOCK_PULL_SNAPSHOT_MAX_TICKERS]
            entries = dict(newest)

        encoded = json.dumps(
            {
                "version": STOCK_PULL_SNAPSHOT_VERSION,
                "entries": entries,
            },
            allow_nan=False,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > STOCK_PULL_SNAPSHOT_MAX_BYTES:
            raise ValueError("stock pull snapshot exceeds the size limit")

        target.parent.mkdir(parents=True, exist_ok=True)
        if _path_has_symlink_boundary(target):
            raise ValueError(
                "stock pull snapshot path must not cross a symlink"
            )
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
            if _path_has_symlink_boundary(target):
                raise ValueError(
                    "stock pull snapshot path must not cross a symlink"
                )
            os.replace(temporary_name, target)
            directory_flags = os.O_RDONLY
            if hasattr(os, "O_DIRECTORY"):
                directory_flags |= os.O_DIRECTORY
            if hasattr(os, "O_CLOEXEC"):
                directory_flags |= os.O_CLOEXEC
            directory_descriptor = os.open(target.parent, directory_flags)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
            identity = _path_file_identity(target)
            if identity is None:
                raise ValueError("stock pull snapshot is not a regular file")
            _cache_document(target, identity, entries)
        except BaseException:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
    return set(cleaned_updates)


__all__ = [
    "STOCK_PULL_RESOURCE_FRESH_SECONDS",
    "STOCK_PULL_RESOURCE_MAX_AGE_SECONDS",
    "read_stock_pull_resource",
    "stock_pull_snapshot_path",
    "validate_stock_pull_payload",
    "write_stock_pull_resources",
]
