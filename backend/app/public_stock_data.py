"""Worker-owned public stock bundles and bounded demand from displayed rows.

HTTP readers may register symbols returned by trusted collection endpoints, but
never fetch providers here. Each stock has its own restart-safe snapshot so this
background coverage cannot evict the owner's manual-pull collection.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import json
import logging
import math
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from app.data_paths import get_data_paths
from app.stock_pull_snapshot import read_stock_pull_resource


logger = logging.getLogger(__name__)
PUBLIC_STOCK_DEMAND_MAX_TICKERS = 2048
PUBLIC_STOCK_DEMAND_TTL_SECONDS = 24 * 60 * 60
PUBLIC_STOCK_DEMAND_WRITE_INTERVAL_SECONDS = 60
PUBLIC_STOCK_INACTIVE_MAX_TICKERS = 512
PUBLIC_STOCK_INACTIVE_MAX_BYTES = 128 * 1024 * 1024
PUBLIC_STOCK_INACTIVE_TTL_SECONDS = 7 * 24 * 60 * 60
_RESOURCES = ("overview", "daily_chart", "signals")
_TICKER = re.compile(r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$")
_METADATA_MAX_BYTES = 4096


def _symbol(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip().upper()
    return value if _TICKER.fullmatch(value) else None


def _symbols(values: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(symbol for value in values if (symbol := _symbol(value))))


def _base(root: Path | None = None) -> Path:
    return (root or get_data_paths().root) / "public-stock-data-v1"


def public_stock_snapshot_path(ticker: str, *, root: Path | None = None) -> Path:
    symbol = _symbol(ticker)
    if symbol is None:
        raise ValueError("invalid public stock ticker")
    return _base(root) / f"{symbol}.json"


def read_public_stock_resource(
    ticker: str,
    resource: str,
    *,
    root: Path | None = None,
    now: float | None = None,
) -> dict[str, Any] | None:
    symbol = _symbol(ticker)
    if symbol is None:
        return None
    observed = time.time() if now is None else float(now)
    entry = read_stock_pull_resource(
        symbol, resource, path=public_stock_snapshot_path(symbol, root=root), now=now,
    )
    if entry is not None:
        metadata = _read_metadata(_base(root) / "status" / f"{symbol}.json") or {}
        priority = metadata.get("priority")
        interval = 5 * 60 if priority in (0, 1) else 30 * 60
        if _market_phase(observed) == "closed":
            interval = max(interval, 6 * 60 * 60)
        entry["fresh_seconds"] = interval
        entry["fresh"] = float(entry["saved_at"]) + interval > observed
    return entry


def _directory(path: Path) -> None:
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError("public stock directory cannot be a symlink")
    path.mkdir(mode=0o750, parents=True, exist_ok=True)
    if not path.is_dir():
        raise ValueError("public stock directory is unavailable")


def _read_metadata(path: Path) -> dict[str, Any] | None:
    if path.parent.is_symlink():
        return None
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > _METADATA_MAX_BYTES:
                return None
            value = json.loads(handle.read(_METADATA_MAX_BYTES + 1))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, UnicodeError):
        return None


def _write_metadata(path: Path, value: Mapping[str, Any]) -> None:
    _directory(path.parent)
    if path.is_symlink():
        raise ValueError("public stock metadata cannot be a symlink")
    encoded = json.dumps(dict(value), allow_nan=False, separators=(",", ":")).encode()
    if len(encoded) > _METADATA_MAX_BYTES:
        raise ValueError("public stock metadata is too large")
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def _demand_lock(root: Path | None):
    directory = _base(root) / "demand"
    _directory(directory)
    fd = os.open(
        directory / ".lock",
        os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("invalid public stock demand lock")
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield directory
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _finite_time(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def _active_demands(directory: Path, now: float, *, prune: bool) -> dict[str, float]:
    result: dict[str, float] = {}
    if directory.is_symlink() or directory.parent.is_symlink():
        return result
    for path in directory.glob("*.json"):
        symbol = _symbol(path.stem)
        value = _read_metadata(path)
        stamp = _finite_time(value.get("requested_at")) if value else None
        if (
            symbol is None or not value or value.get("ticker") != symbol
            or stamp is None or stamp > now + 300
            or stamp + PUBLIC_STOCK_DEMAND_TTL_SECONDS <= now
        ):
            if prune:
                path.unlink(missing_ok=True)
            continue
        result[symbol] = stamp
    return result


def register_public_stock_demand(
    tickers: Iterable[str], *, root: Path | None = None, now: float | None = None,
) -> None:
    """Register only ticker rows already selected by a trusted server endpoint.

    One file per symbol plus a cross-process lock bounds concurrent HTTP writers.
    Default-pool and current-radar coverage never depend on this transient set.
    """
    symbols = _symbols(tickers)
    if not symbols:
        return
    observed = time.time() if now is None else float(now)
    with _demand_lock(root) as directory:
        active = _active_demands(directory, observed, prune=True)
        requested = set(symbols[-PUBLIC_STOCK_DEMAND_MAX_TICKERS:])
        for symbol in requested:
            previous = active.get(symbol)
            if previous is not None and observed - previous < PUBLIC_STOCK_DEMAND_WRITE_INTERVAL_SECONDS:
                continue
            _write_metadata(directory / f"{symbol}.json", {
                "ticker": symbol, "requested_at": observed,
            })
            active[symbol] = observed
        overflow = len(active) - PUBLIC_STOCK_DEMAND_MAX_TICKERS
        if overflow > 0:
            # A newly returned page takes priority over older browsing demand.
            oldest = sorted(active, key=lambda item: (item in requested, active[item], item))
            for symbol in oldest[:overflow]:
                (directory / f"{symbol}.json").unlink(missing_ok=True)


def _iso(value: float | None) -> str | None:
    return datetime.fromtimestamp(value, timezone.utc).isoformat() if value else None


def _market_phase(now: float) -> str:
    from app.services.market_calendar import ET, is_trading_day

    local = datetime.fromtimestamp(now, ET)
    if not is_trading_day(local.date()):
        return "closed"
    minutes = local.hour * 60 + local.minute
    return "active" if 4 * 60 <= minutes < 20 * 60 else "closed"


def read_public_stock_status(
    ticker: str, *, root: Path | None = None, now: float | None = None,
) -> dict[str, Any]:
    symbol = _symbol(ticker)
    if symbol is None:
        raise ValueError("invalid public stock ticker")
    observed = time.time() if now is None else float(now)
    entries = {
        name: read_public_stock_resource(symbol, name, root=root, now=observed)
        for name in _RESOURCES
    }
    available = sum(entry is not None for entry in entries.values())
    metadata = _read_metadata(_base(root) / "status" / f"{symbol}.json") or {}
    stamp = _finite_time(metadata.get("as_of"))
    retry = _finite_time(metadata.get("retry_after"))
    if retry is not None and retry > observed + 24 * 60 * 60:
        retry = None
    status = "ready" if available == len(_RESOURCES) else "partial" if available else "pending"
    if metadata.get("ticker") == symbol and stamp and stamp <= observed + 300:
        if metadata.get("status") == "running" and observed - stamp < 30 * 60:
            status = "running"
        elif metadata.get("status") == "failed" and retry and retry > observed:
            status = "partial" if available else "failed"
        elif metadata.get("status") == "pending" and available < len(_RESOURCES):
            status = "pending"
    else:
        stamp = retry = None
    saved = [float(entry["saved_at"]) for entry in entries.values() if entry]
    return {
        "ticker": symbol, "status": status,
        "as_of": _iso(min(saved) if saved else None),
        "latest_resource_at": _iso(max(saved) if saved else None),
        "last_attempt_at": _iso(stamp),
        "retry_after": _iso(retry) if retry and retry > observed else None,
        "retry_after_seconds": max(0, math.ceil(retry - observed)) if retry else None,
        "resources": {
            name: {
                "available": entry is not None,
                "saved_at": _iso(float(entry["saved_at"])) if entry else None,
                "fresh": bool(entry and entry["fresh"]),
            }
            for name, entry in entries.items()
        },
    }


def _default_tickers() -> list[str]:
    from app.services.watchlist_scope import collection_watchlist_tickers

    return collection_watchlist_tickers()


def _prune_inactive_storage(root: Path | None, protected: set[str], now: float) -> None:
    """Bound historical residue in addition to the currently required symbols."""
    base = _base(root)
    if base.is_symlink() or (base / "status").is_symlink():
        return
    records: dict[str, tuple[list[Path], float, int]] = {}
    for directory in (base, base / "status"):
        for path in directory.glob("*.json"):
            symbol = _symbol(path.stem)
            if symbol is None or symbol in protected:
                continue
            try:
                info = path.stat(follow_symlinks=False)
            except OSError:
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            files, latest, size = records.get(symbol, ([], 0.0, 0))
            records[symbol] = ([*files, path], max(latest, info.st_mtime), size + info.st_size)
    total_bytes = sum(row[2] for row in records.values())
    total_count = len(records)
    for _symbol_name, (files, latest, size) in sorted(records.items(), key=lambda item: item[1][1]):
        if (
            latest + PUBLIC_STOCK_INACTIVE_TTL_SECONDS > now
            and total_count <= PUBLIC_STOCK_INACTIVE_MAX_TICKERS
            and total_bytes <= PUBLIC_STOCK_INACTIVE_MAX_BYTES
        ):
            continue
        for path in files:
            path.unlink(missing_ok=True)
        total_count -= 1
        total_bytes -= size


def _current_breakout_tickers() -> list[str]:
    from app.services.breakouts.config import get_breakout_settings
    from app.services.breakouts.repository import BreakoutRepository

    settings = get_breakout_settings()
    if not settings.enabled:
        return []
    try:
        scan = BreakoutRepository(settings.db_path, read_only=True).latest_completed_scan()
    except Exception:
        logger.warning("Public stock coverage could not read the current radar", exc_info=True)
        return []
    return _symbols(item.get("ticker") for item in (scan or {}).get("events", []) if isinstance(item, Mapping))


def public_stock_targets(
    entries: Mapping[str, Any], *, current_tickers: Iterable[str],
    default_tickers: Iterable[str], requested_tickers: Iterable[str] = (),
    now: float | None = None,
) -> dict[str, int]:
    """Match Home's six earnings, six movers and eight unique radar symbols."""
    observed = time.time() if now is None else float(now)
    today = datetime.fromtimestamp(observed, ZoneInfo("America/New_York")).date().isoformat()
    watch_payload = (entries.get("watchlist") or {}).get("payload") or {}
    rows = [
        row for group in watch_payload.get("groups", []) if isinstance(group, Mapping)
        for row in group.get("stocks", []) if isinstance(row, Mapping)
    ]
    pool = set(_symbols(row.get("ticker") for row in rows))
    earnings_payload = (entries.get("earnings") or {}).get("payload") or {}
    earnings = [
        row for row in earnings_payload.get("earnings", []) if isinstance(row, Mapping)
        and isinstance(row.get("earnings_date"), str) and row["earnings_date"] >= today
    ]

    def number(row: Mapping[str, Any], key: str) -> float | None:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        return float(value)

    earnings.sort(key=lambda row: (
        not (row.get("public_featured") is True or _symbol(row.get("ticker")) in pool),
        row["earnings_date"], -(number(row, "market_cap") or -1),
    ))
    movers = sorted(rows, key=lambda row: -(abs(value) if (value := number(row, "change_percent")) is not None else -1))
    current = _symbols(current_tickers)
    priority = _symbols([
        "NVDA", *current[:8], *(row.get("ticker") for row in earnings[:6]),
        *(row.get("ticker") for row in movers[:6]), *requested_tickers,
    ])
    result = dict.fromkeys(priority, 0)
    for symbol in current:
        result.setdefault(symbol, 1)
    for symbol in _symbols(default_tickers):
        result.setdefault(symbol, 2)
    return result


class PublicStockDataRefresh:
    """Two independent consumers, with per-symbol cooldown and graceful drain."""

    def __init__(
        self, *, root: Path | None = None, puller: Callable[..., Any] | None = None,
        current_reader: Callable[[], Iterable[str]] | None = None,
        default_reader: Callable[[], Iterable[str]] | None = None,
        clock: Callable[[], float] = time.time, concurrency: int = 2,
        interval_seconds: float = 30 * 60, priority_interval_seconds: float = 5 * 60,
        closed_interval_seconds: float = 6 * 60 * 60,
        start_interval_seconds: float = 1.0,
        phase_reader: Callable[[float], str] | None = None,
    ) -> None:
        self.root = root
        self._puller = puller
        self._current_reader = current_reader or _current_breakout_tickers
        self._default_reader = default_reader or _default_tickers
        self._clock = clock
        self._concurrency = max(1, min(4, int(concurrency)))
        self._interval_seconds = interval_seconds
        self._priority_interval_seconds = priority_interval_seconds
        self._closed_interval_seconds = closed_interval_seconds
        self._start_interval_seconds = max(0.0, float(start_interval_seconds))
        self._next_start = 0.0
        self._start_lock = asyncio.Lock()
        self._phase_reader = phase_reader
        self._targets: dict[str, int] = {}
        self._due: dict[str, float] = {}
        self._completed: dict[str, float] = {}
        self._attempted: dict[str, float] = {}
        self._failures: dict[str, int] = {}
        self._active: set[str] = set()
        self._consumers: set[asyncio.Task[None]] = set()
        self._closed = False
        self._claim_number = 0
        self._last_pruned_at = 0.0

    def _interval(self, priority: int, now: float) -> float:
        if self._phase_reader is None:
            phase = _market_phase(now)
        else:
            phase = self._phase_reader(now)
        interval = self._priority_interval_seconds if priority < 2 else self._interval_seconds
        return max(interval, self._closed_interval_seconds) if phase == "closed" else interval

    def _collect(self, entries: Mapping[str, Any], now: float) -> tuple[dict[str, int], dict[str, tuple[float, float | None, int]]]:
        demands = _active_demands(_base(self.root) / "demand", now, prune=False)
        requested = sorted(demands, key=lambda symbol: -demands[symbol])
        targets = public_stock_targets(
            entries, current_tickers=self._current_reader(),
            default_tickers=self._default_reader(), requested_tickers=requested, now=now,
        )
        if now - self._last_pruned_at >= 3600:
            _prune_inactive_storage(self.root, set(targets) | set(self._targets) | set(self._active), now)
            self._last_pruned_at = now
        initial: dict[str, tuple[float, float | None, int]] = {}
        for symbol, priority in targets.items():
            if symbol in self._due:
                continue
            resources = [read_public_stock_resource(symbol, resource, root=self.root, now=now) for resource in _RESOURCES]
            saved = min(float(entry["saved_at"]) for entry in resources if entry) if all(resources) else None
            metadata = _read_metadata(_base(self.root) / "status" / f"{symbol}.json") or {}
            retry = _finite_time(metadata.get("retry_after")) if metadata.get("ticker") == symbol else None
            due = saved + self._interval(priority, now) if saved is not None else 0.0
            failures = 0
            if metadata.get("ticker") == symbol and metadata.get("status") == "failed":
                raw_failures = metadata.get("failure_count")
                failures = max(1, min(raw_failures, 10)) if isinstance(raw_failures, int) and not isinstance(raw_failures, bool) else 1
                due = min(due, now)
            if retry is not None and now < retry <= now + self._closed_interval_seconds:
                due = retry
                failures = max(1, failures)
            initial[symbol] = (due, saved, failures)
        return targets, initial

    async def poll(self, entries: Mapping[str, Any]) -> dict[str, Any]:
        if self._closed:
            return self.summary()
        now = float(self._clock())
        targets, initial = await asyncio.to_thread(self._collect, entries, now)
        for symbol, (due, saved, failures) in initial.items():
            self._due.setdefault(symbol, due)
            if saved is not None:
                self._completed.setdefault(symbol, saved)
            if failures:
                self._failures.setdefault(symbol, failures)
        for symbol, priority in targets.items():
            if symbol in self._completed and symbol not in self._failures:
                self._due[symbol] = self._completed[symbol] + self._interval(priority, now)
        self._targets = targets
        # Expired historical demand must not accumulate scheduling state forever.
        for symbol in set(self._due) - set(targets) - self._active:
            self._due.pop(symbol, None)
            self._completed.pop(symbol, None)
            self._attempted.pop(symbol, None)
            self._failures.pop(symbol, None)
        self._consumers = {task for task in self._consumers if not task.done()}
        while len(self._consumers) < self._concurrency:
            task = asyncio.create_task(self._consume(), name="public-stock-data")
            self._consumers.add(task)
        return self.summary()

    def summary(self) -> dict[str, Any]:
        now = float(self._clock())
        return {
            "target_count": len(self._targets), "running": sorted(self._active),
            "pending_count": sum(symbol not in self._active and self._due.get(symbol, 0) <= now for symbol in self._targets),
            "failed_count": len(self._failures),
        }

    async def _status(self, symbol: str, status: str, *, retry_after: float | None = None) -> None:
        try:
            await asyncio.to_thread(_write_metadata, _base(self.root) / "status" / f"{symbol}.json", {
                "ticker": symbol, "status": status, "as_of": float(self._clock()), "retry_after": retry_after,
                "priority": self._targets.get(symbol, 2),
                "failure_count": self._failures.get(symbol, 0),
            })
        except (OSError, ValueError):
            logger.warning("Could not save public stock scheduling status for %s", symbol)

    async def _consume(self) -> None:
        while not self._closed:
            now = float(self._clock())
            candidates = [ticker for ticker in self._targets if ticker not in self._active and self._due.get(ticker, 0) <= now]
            if not candidates:
                return
            # Reserve one of every four claims for the default universe. A long
            # history list must not monopolize the queue as its five-minute
            # refreshes become due. Within each tier, first coverage comes first.
            preferred = (0, 0, 1, 2)[self._claim_number % 4]
            tier = [ticker for ticker in candidates if self._targets[ticker] == preferred]
            symbol = min(tier or candidates, key=lambda item: (
                item in self._attempted, self._attempted.get(item, 0), self._targets[item],
            ))
            self._claim_number += 1
            self._active.add(symbol)
            self._attempted[symbol] = now
            try:
                await self._status(symbol, "running")
                puller = self._puller
                if puller is None:
                    from app.api.stocks import _pull_stock_data_once

                    puller = _pull_stock_data_once
                async with self._start_lock:
                    loop = asyncio.get_running_loop()
                    delay = max(0.0, self._next_start - loop.time())
                    if delay:
                        await asyncio.sleep(delay)
                    self._next_start = loop.time() + self._start_interval_seconds
                result = await puller(symbol, snapshot_path=public_stock_snapshot_path(symbol, root=self.root), include_options=False)
                pulled = result.get("resources") if isinstance(result, Mapping) else None
                if (
                    not isinstance(result, Mapping) or result.get("status") != "completed"
                    or result.get("persistence_status") != "completed" or not isinstance(pulled, Mapping)
                    or any(not isinstance(pulled.get(name), Mapping) or pulled[name].get("status") != "available" or pulled[name].get("persisted") is not True for name in _RESOURCES)
                ):
                    raise RuntimeError("public_stock_pull_incomplete")
                resources = await asyncio.to_thread(lambda: [
                    read_public_stock_resource(symbol, resource, root=self.root, now=float(self._clock()))
                    for resource in _RESOURCES
                ])
                if not all(resources):
                    raise RuntimeError("public_stock_resources_incomplete")
                saved = min(float(entry["saved_at"]) for entry in resources if entry)
                self._completed[symbol] = saved
                self._failures.pop(symbol, None)
                self._due[symbol] = float(self._clock()) + self._interval(self._targets.get(symbol, 0), float(self._clock()))
                await self._status(symbol, "ready")
            except Exception:
                failures = self._failures.get(symbol, 0) + 1
                self._failures[symbol] = failures
                self._due[symbol] = float(self._clock()) + min(1800, 60 * 2 ** min(failures - 1, 5))
                await self._status(symbol, "failed", retry_after=self._due[symbol])
                logger.warning("Public stock refresh failed for %s", symbol, exc_info=True)
            finally:
                self._active.discard(symbol)

    async def aclose(self) -> None:
        """Stop claiming work and drain provider/CPU/disk work already started."""
        self._closed = True
        if not self._consumers:
            return
        drain = asyncio.gather(*self._consumers, return_exceptions=True)
        cancelled: asyncio.CancelledError | None = None
        while not drain.done():
            try:
                await asyncio.shield(drain)
            except asyncio.CancelledError as exc:
                cancelled = exc
        self._consumers.clear()
        if cancelled is not None:
            raise cancelled
