"""Bounded worker-produced option snapshots shared with the HTTP process.

Public readers never contact a provider. Only symbols already in the trusted
public stock coverage (or the default collection) may request preparation, and
only expiration dates from a saved provider list may request a chain. A single
worker consumer shares the existing Yahoo option I/O gate and drains on close.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timezone
import json
import logging
import math
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from app.data_paths import get_data_paths
from app.services.option_capability import canonicalize_option_symbol, is_declared_unsupported

logger = logging.getLogger(__name__)
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_STORE_BYTES = 64 * 1024 * 1024
MAX_RESOURCES = 256
MAX_DEMANDS = 128
MAX_TARGETS = 64
MAX_AGE_SECONDS = 7 * 86400
DEMAND_SECONDS = 86400
FRESH_SECONDS = {"expirations": 900, "chain": 600}
_TICKER = re.compile(r"^(?:\^[A-Z0-9][A-Z0-9.^_=-]{0,30}|[A-Z0-9][A-Z0-9.^_=-]{0,31})$")
_ET = ZoneInfo("America/New_York")


def default_option_root() -> Path:
    return get_data_paths().root


def _key(ticker: str, expiration: str = "") -> tuple[str, str]:
    symbol = canonicalize_option_symbol(ticker)
    if not _TICKER.fullmatch(symbol):
        raise ValueError("invalid option snapshot ticker")
    if expiration and date.fromisoformat(expiration).isoformat() != expiration:
        raise ValueError("invalid option snapshot expiration")
    return symbol, expiration


def _path(root: Path | None = None) -> Path:
    path = (root or default_option_root()) / "public-options-v1.sqlite"
    if path.is_symlink() or any(parent.is_symlink() for parent in path.parents):
        raise ValueError("option snapshot storage cannot traverse symlinks")
    return path


@contextmanager
def _connection(root: Path | None = None, *, write: bool = False):
    path = _path(root)
    if not write and not path.is_file():
        yield None
        return
    if write:
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path) if write else f"file:{path}?mode=ro", uri=not write, timeout=2)
    connection.row_factory = sqlite3.Row
    try:
        if write:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS snapshots (
                    ticker TEXT NOT NULL, expiration TEXT NOT NULL, saved_at REAL NOT NULL,
                    payload TEXT NOT NULL, bytes INTEGER NOT NULL,
                    PRIMARY KEY(ticker, expiration)
                );
                CREATE TABLE IF NOT EXISTS demand (
                    ticker TEXT NOT NULL, expiration TEXT NOT NULL, requested_at REAL NOT NULL,
                    next_attempt REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(ticker, expiration)
                );
            """)
        yield connection
        if write:
            connection.commit()
    finally:
        connection.close()


def option_snapshot_payload_valid(symbol: str, expiration: str, payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("ticker") not in (None, symbol):
        return False
    if expiration:
        return (payload.get("expiration") in (None, expiration)
                and isinstance(payload.get("calls"), list) and isinstance(payload.get("puts"), list)
                and bool(payload["calls"] or payload["puts"]))
    values = payload.get("expirations")
    if not isinstance(values, list) or len(values) > 200:
        return False
    try:
        return all(isinstance(value, str) and date.fromisoformat(value).isoformat() == value for value in values)
    except ValueError:
        return False


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, timezone.utc).isoformat().replace("+00:00", "Z")


def _source_time(payload: Mapping[str, Any]) -> float | None:
    value = payload.get("as_of")
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except ValueError:
        return None


def read_option_snapshot(ticker: str, expiration: str = "", *, root: Path | None = None,
                         now: float | None = None) -> dict[str, Any] | None:
    symbol, expiration = _key(ticker, expiration)
    observed = time.time() if now is None else now
    try:
        with _connection(root) as connection:
            if connection is None:
                return None
            row = connection.execute("SELECT saved_at,payload FROM snapshots WHERE ticker=? AND expiration=?",
                                     (symbol, expiration)).fetchone()
        if row is None or not math.isfinite(row["saved_at"]) or not 0 <= observed - row["saved_at"] <= MAX_AGE_SECONDS:
            return None
        if expiration and expiration < datetime.fromtimestamp(observed, _ET).date().isoformat():
            return None
        payload = json.loads(row["payload"])
        if not option_snapshot_payload_valid(symbol, expiration, payload):
            return None
    except (OSError, ValueError, sqlite3.Error):
        return None
    kind = "chain" if expiration else "expirations"
    source_time = _source_time(payload)
    if source_time is not None and not -300 <= observed - source_time <= MAX_AGE_SECONDS:
        return None
    reference_time = min(row["saved_at"], source_time) if source_time is not None else row["saved_at"]
    stale = bool(payload.get("_stale") or payload.get("cache_stale")
                 or observed - reference_time >= FRESH_SECONDS[kind])
    result = {**payload, "ticker": symbol, "snapshot_saved_at": _iso(row["saved_at"]),
              "cache_stale": stale, "_stale": stale,
              "source_status": "stale" if stale else payload.get("source_status", "active")}
    if not expiration:
        today = datetime.fromtimestamp(observed, _ET).date().isoformat()
        result["expirations"] = sorted(set(value for value in payload["expirations"] if value >= today))
        if not result["expirations"] and payload.get("options_status") != "unsupported_by_provider":
            result.update(options_status="empty_unconfirmed", retryable=True)
    return result


def write_option_snapshot(ticker: str, payload: dict[str, Any], expiration: str = "", *,
                          root: Path | None = None, now: float | None = None) -> None:
    symbol, expiration = _key(ticker, expiration)
    observed = time.time() if now is None else now
    if not math.isfinite(observed) or not option_snapshot_payload_valid(symbol, expiration, payload):
        raise ValueError("invalid option snapshot")
    # Never renew the clock of a provider's retained stale result.
    if payload.get("_stale") or payload.get("cache_stale") or payload.get("source_status") == "stale":
        return
    body = json.dumps(payload, allow_nan=False, ensure_ascii=False, separators=(",", ":"))
    size = len(body.encode("utf-8"))
    if size > MAX_SNAPSHOT_BYTES:
        raise ValueError("option snapshot exceeds size limit")
    with _connection(root, write=True) as connection:
        # HTTP and worker processes may finish in a different order than their
        # Yahoo reads. Compare source time while holding the write transaction;
        # a later save alone must not replace a newer quote (audit Q-02/Q-03).
        connection.execute("BEGIN IMMEDIATE")
        existing = connection.execute("SELECT saved_at,payload FROM snapshots WHERE ticker=? AND expiration=?",
                                      (symbol, expiration)).fetchone()
        if existing is not None:
            try:
                previous_source = _source_time(json.loads(existing["payload"]))
            except (ValueError, TypeError, AttributeError):
                previous_source = None
            incoming_source = _source_time(payload)
            if previous_source is not None and (incoming_source is None or incoming_source <= previous_source):
                return
            if incoming_source is None and observed <= existing["saved_at"]:
                return
            observed = max(observed, existing["saved_at"])
        connection.execute("INSERT INTO snapshots VALUES(?,?,?,?,?) ON CONFLICT(ticker,expiration) DO UPDATE SET "
                           "saved_at=excluded.saved_at,payload=excluded.payload,bytes=excluded.bytes",
                           (symbol, expiration, observed, body, size))
        connection.execute("DELETE FROM snapshots WHERE saved_at<?", (observed - MAX_AGE_SECONDS,))
        rows = connection.execute("SELECT ticker,expiration,bytes FROM snapshots ORDER BY saved_at DESC").fetchall()
        count = total = 0
        for row in rows:
            count += 1
            total += row["bytes"]
            if count > MAX_RESOURCES or total > MAX_STORE_BYTES:
                connection.execute("DELETE FROM snapshots WHERE ticker=? AND expiration=?", (row["ticker"], row["expiration"]))


def _trusted_symbol(symbol: str, root: Path | None) -> bool:
    from app.public_stock_data import read_public_stock_resource
    from app.services.watchlist_scope import collection_watchlist_tickers
    from app.stock_pull_snapshot import read_stock_pull_resource

    return (symbol in collection_watchlist_tickers()
            or read_public_stock_resource(symbol, "overview", root=root) is not None
            or read_stock_pull_resource(symbol, "overview", path=(root or default_option_root()) / "stock-pull-snapshots-v1.json") is not None)


def request_option_snapshot(ticker: str, expiration: str = "", *, root: Path | None = None,
                            now: float | None = None, trusted: bool = False) -> bool:
    """Record a bounded local request, never an external/provider request."""
    symbol, expiration = _key(ticker, expiration)
    observed = time.time() if now is None else now
    if is_declared_unsupported(symbol) or (not trusted and not _trusted_symbol(symbol, root)):
        return False
    if expiration:
        saved = read_option_snapshot(symbol, root=root, now=observed)
        if saved is None or expiration not in saved["expirations"]:
            return False
    with _connection(root, write=True) as connection:
        connection.execute("DELETE FROM demand WHERE requested_at<?", (observed - DEMAND_SECONDS,))
        connection.execute("INSERT INTO demand(ticker,expiration,requested_at) VALUES(?,?,?) "
                           "ON CONFLICT(ticker,expiration) DO UPDATE SET requested_at=excluded.requested_at "
                           "WHERE excluded.requested_at-demand.requested_at>=30", (symbol, expiration, observed))
        connection.execute("DELETE FROM demand WHERE rowid IN (SELECT rowid FROM demand ORDER BY requested_at DESC LIMIT -1 OFFSET ?)", (MAX_DEMANDS,))
    return True


def option_preparation_status(ticker: str, expiration: str = "", *, root: Path | None = None,
                              now: float | None = None) -> dict[str, Any]:
    symbol, expiration = _key(ticker, expiration)
    observed = time.time() if now is None else now
    with _connection(root) as connection:
        row = connection.execute("SELECT failures,next_attempt FROM demand WHERE ticker=? AND expiration=?",
                                 (symbol, expiration)).fetchone() if connection is not None else None
    cooling = bool(row and row["failures"] and row["next_attempt"] > observed)
    return {"status": "cooling" if cooling else "pending",
            "retry_after_seconds": max(1, math.ceil(row["next_attempt"] - observed)) if cooling else 30}


class PublicOptionDataRefresh:
    """One provider resource at a time, persistent cooling and bounded demand."""

    def __init__(self, *, root: Path | None = None, loader: Callable | None = None,
                 target_reader: Callable | None = None, clock: Callable[[], float] = time.time,
                 start_interval_seconds: float = 5.0, phase_reader: Callable | None = None) -> None:
        self.root, self._loader, self._target_reader, self._clock = root, loader, target_reader, clock
        self._start_interval_seconds = max(0, start_interval_seconds)
        self._phase_reader = phase_reader
        self._consumer: asyncio.Task | None = None
        self._closed = False
        self._targets: list[str] = []
        self._running: str | None = None
        self._last_start = 0.0

    def _targets_from_entries(self, entries: Mapping[str, Any]) -> list[str]:
        from app.public_stock_data import _current_breakout_tickers, _default_tickers, public_stock_targets
        values = self._target_reader(entries) if self._target_reader else public_stock_targets(
            entries, current_tickers=_current_breakout_tickers(), default_tickers=_default_tickers(), now=self._clock())
        return list(dict.fromkeys(_key(value)[0] for value in values if not is_declared_unsupported(value)))[:MAX_TARGETS]

    def _prepare(self, entries: Mapping[str, Any]) -> list[str]:
        targets = self._targets_from_entries(entries)
        now = float(self._clock())
        for symbol in targets:
            request_option_snapshot(symbol, root=self.root, now=now, trusted=True)
            saved = read_option_snapshot(symbol, root=self.root, now=now)
            if saved and saved["expirations"]:
                request_option_snapshot(symbol, saved["expirations"][0], root=self.root, now=now, trusted=True)
        return targets

    async def poll(self, entries: Mapping[str, Any]) -> dict[str, Any]:
        if not self._closed:
            self._targets = await asyncio.to_thread(self._prepare, entries)
            if self._consumer is None or self._consumer.done():
                self._consumer = asyncio.create_task(self._consume(), name="public-option-data")
        return self.summary()

    def summary(self) -> dict[str, Any]:
        return {"target_count": len(self._targets), "running": self._running}

    def _next(self) -> tuple[str, str] | None:
        now = float(self._clock())
        if self._phase_reader is None:
            from app.public_stock_data import _market_phase
            phase = _market_phase(now)
        else:
            phase = self._phase_reader(now)
        with _connection(self.root) as connection:
            if connection is None:
                return None
            rows = connection.execute("SELECT * FROM demand WHERE requested_at>=? AND next_attempt<=? "
                                      "ORDER BY next_attempt,requested_at", (now - DEMAND_SECONDS, now)).fetchall()
        for row in rows:
            symbol, expiration = row["ticker"], row["expiration"]
            if is_declared_unsupported(symbol):
                continue
            saved = read_option_snapshot(symbol, expiration, root=self.root, now=now)
            if saved is not None:
                # Empty success is a retryable discovery result, not a claim of
                # permanent unavailability. Recheck it at a bounded cadence.
                stamp = datetime.fromisoformat(saved["snapshot_saved_at"].replace("Z", "+00:00")).timestamp()
                interval = 60 if not expiration and not saved["expirations"] else FRESH_SECONDS["chain" if expiration else "expirations"]
                if phase == "closed":
                    interval = max(interval, 6 * 3600)
                if now < stamp + interval:
                    continue
            if expiration:
                available = read_option_snapshot(symbol, root=self.root, now=now)
                if available is None or expiration not in available["expirations"]:
                    continue
            return symbol, expiration
        return None

    def _record_attempt(self, symbol: str, expiration: str, *, failed: bool) -> None:
        now = float(self._clock())
        with _connection(self.root, write=True) as connection:
            row = connection.execute("SELECT failures FROM demand WHERE ticker=? AND expiration=?", (symbol, expiration)).fetchone()
            failures = min(6, (row["failures"] if row else 0) + 1) if failed else 0
            delay = min(1800, 60 * 2 ** max(0, failures - 1)) if failed else 60
            connection.execute("UPDATE demand SET failures=?,next_attempt=? WHERE ticker=? AND expiration=?",
                               (failures, now + delay, symbol, expiration))

    async def _load(self, symbol: str, expiration: str) -> dict[str, Any]:
        if self._loader is not None:
            return await self._loader(symbol, expiration)
        from app.services import yahoo
        return await asyncio.to_thread(yahoo.get_option_chain, symbol, expiration) if expiration else await asyncio.to_thread(yahoo.get_expirations_snapshot, symbol)

    async def _consume(self) -> None:
        while not self._closed:
            resource = await asyncio.to_thread(self._next)
            if resource is None:
                return
            symbol, expiration = resource
            loop = asyncio.get_running_loop()
            delay = self._last_start + self._start_interval_seconds - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            if self._closed:
                return
            self._last_start = loop.time()
            self._running = f"{symbol}:{expiration or 'expirations'}"
            try:
                payload = await self._load(symbol, expiration)
                if not option_snapshot_payload_valid(symbol, expiration, payload) or payload.get("_stale") or payload.get("cache_stale"):
                    raise ValueError("option source did not provide a fresh snapshot")
                if not expiration and not payload["expirations"]:
                    previous = await asyncio.to_thread(read_option_snapshot, symbol, root=self.root, now=self._clock())
                    if previous and previous["expirations"]:
                        raise ValueError("empty discovery cannot replace a usable saved expiration list")
                await asyncio.to_thread(write_option_snapshot, symbol, payload, expiration, root=self.root, now=self._clock())
                await asyncio.to_thread(self._record_attempt, symbol, expiration, failed=False)
                if not expiration:
                    saved = await asyncio.to_thread(read_option_snapshot, symbol, root=self.root, now=self._clock())
                    if saved and saved["expirations"]:
                        await asyncio.to_thread(request_option_snapshot, symbol, saved["expirations"][0], root=self.root, now=self._clock(), trusted=True)
            except Exception:
                logger.warning("Public option preparation failed for %s", self._running, exc_info=True)
                await asyncio.to_thread(self._record_attempt, symbol, expiration, failed=True)
            finally:
                self._running = None

    async def aclose(self) -> None:
        self._closed = True
        task = self._consumer
        if task is None:
            return
        cancelled = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                cancelled = exc
        await task
        if cancelled is not None:
            raise cancelled
