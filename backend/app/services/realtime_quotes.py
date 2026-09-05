"""One Finnhub trade stream, shared subscriptions, and coalesced browser quotes.

This service owns no HTTP authentication policy. The API must authorize access
before subscribing or reading a snapshot. Radar callbacks receive individual
eligible trades; browser delivery is deliberately coalesced separately.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
import math
from pathlib import Path
import re
import time
from typing import Any, AsyncIterator, Awaitable, Callable
from urllib.parse import quote as urlquote
import uuid

import httpx
from websockets.asyncio.client import connect

from app.data_paths import get_data_paths
from app.services.finnhub_budget import (
    async_reserve_finnhub_request,
    mark_finnhub_rate_limited,
)
from app.services.market_calendar import ET, early_close_minutes, is_trading_day, next_trading_day


TOP_SYMBOLS = ("SPY", "QQQ", "DIA", "IWM")
MAX_CLIENT_SYMBOLS = 200
MAX_CLIENTS = 256
MAX_CACHED_QUOTES = 2048
MAX_RADAR_EVENTS = 512
_SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,14}$")
_US_CLASS_ALIAS = re.compile(r"^([A-Z]{1,10})-([A-Z])$")
# Finnhub's official websocket documentation links this condition table:
# https://docs.google.com/spreadsheets/d/1PUxiSWPHSODbaTaoL2Vef6DgU-yFtlRGZf19oBb9Hp0
# Only unconditional consolidated Update Last=Yes conditions qualify. Codes
# requiring participant history are not safe to infer from a partial feed.
_LAST_PRICE_CONDITIONS = frozenset({"1", "2", "4", "6", "7", "8", "13", "14", "19", "23", "28", "29", "33", "35"})
_transport_logger = logging.Logger("option_pro.quotes.transport")
_transport_logger.disabled = True  # Never log a websocket URL containing a key.


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _timestamp(value: Any, *, milliseconds: bool = False) -> datetime | None:
    number = _positive(value)
    if number is None:
        return None
    try:
        result = datetime.fromtimestamp(number / 1000 if milliseconds else number, timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None
    return result if result.year >= 2000 else None


def market_session(at: datetime) -> str:
    local = at.astimezone(ET)
    if not is_trading_day(local.date()):
        return "closed"
    minutes = local.hour * 60 + local.minute
    if 4 * 60 <= minutes < 9 * 60 + 30:
        return "premarket"
    if 9 * 60 + 30 <= minutes < (early_close_minutes(local.date()) or 16 * 60):
        return "regular"
    if (early_close_minutes(local.date()) or 16 * 60) <= minutes < 20 * 60:
        return "postmarket"
    return "closed"


def normalize_symbols(symbols: list[str] | tuple[str, ...], *, limit: int = MAX_CLIENT_SYMBOLS) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in symbols:
        if not isinstance(raw, str):
            raise ValueError("Invalid stock symbol")
        symbol = raw.strip().upper()
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid stock symbol")
        if symbol not in seen:
            seen.add(symbol)
            result.append(symbol)
        if len(result) > limit:
            raise ValueError("Too many stock symbols")
    return result


def _provider_symbol(symbol: str) -> str:
    # Yahoo-style US class shares use a hyphen; Finnhub uses a dot. Keep this
    # deliberately narrow so exchange suffixes and preferred-share formats
    # are not accidentally reinterpreted as ordinary US class shares.
    match = _US_CLASS_ALIAS.fullmatch(symbol)
    return f"{match[1]}.{match[2]}" if match else symbol


@dataclass
class _Client:
    symbols: list[str]
    focus: list[str]
    last_seen: float
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=lambda: asyncio.Queue(maxsize=8))
    resync_required: bool = False
    radar_versions: dict[str, int] = field(default_factory=dict)
    provider_symbols: set[str] = field(init=False)

    def __post_init__(self) -> None:
        self.provider_symbols = {_provider_symbol(symbol) for symbol in self.symbols}


class QuoteHub:
    def __init__(
        self,
        settings: Any,
        *,
        radar_loader: Callable[[], Awaitable[list[Any]]] | None = None,
        radar_event_loader: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None,
        trade_handler: Callable[[dict[str, Any]], Awaitable[Any]] | None = None,
    ) -> None:
        self.enabled = bool(getattr(settings, "quotes_enabled", False))
        self.public_enabled = bool(getattr(settings, "quotes_public_enabled", False))
        self.signals_enabled = bool(getattr(settings, "quotes_signals_enabled", False))
        key = getattr(settings, "finnhub_api_key", "")
        self._api_key = key.get_secret_value() if hasattr(key, "get_secret_value") else str(key or "").strip()
        self._base_url = str(getattr(settings, "finnhub_base_url", "https://finnhub.io/api/v1")).rstrip("/")
        self.max_symbols = min(50, max(4, int(getattr(settings, "quotes_max_symbols", 50))))
        self._interval = min(1.0, max(0.05, int(getattr(settings, "quotes_publish_interval_ms", 250)) / 1000))
        self._release_seconds = min(30.0, max(1.0, float(getattr(settings, "quotes_release_seconds", 30))))
        root = Path(getattr(settings, "data_dir", None) or get_data_paths().root)
        lock_id = hashlib.sha256(self._api_key.encode()).hexdigest()[:24]
        self._lock_path = Path(getattr(settings, "quotes_lock_path", None) or root / f"finnhub-stream-{lock_id}.lock")
        self._budget_path = root / "finnhub-budget.sqlite"
        self._radar_loader = radar_loader
        self._radar_event_loader = radar_event_loader
        self._trade_handler = trade_handler
        self._clients: OrderedDict[str, _Client] = OrderedDict()
        self._quotes: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._baselines: dict[str, dict[str, Any]] = {}
        self._rest_attempts: dict[str, float] = {}
        self._recent_trades: dict[str, deque[tuple[Any, ...]]] = {}
        self._provider_unavailable: set[str] = set()
        self._radar_symbols: list[str] = []
        self._radar_events: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._radar_event_sequences: dict[str, int] = {}
        self._radar_sequence = 0
        self._radar_events_loaded = False
        self._desired_symbols: list[str] = []
        self._signal_symbols: dict[str, list[str]] = {}
        self._sent_symbols: set[str] = set()
        self._dirty_symbols: set[str] = set()
        self._freshness: dict[str, str] = {}
        self._market_session = market_session(_utcnow())
        self._all_dirty = False
        self._status_dirty = False
        self._subscription_changed = asyncio.Event()
        self._radar_refresh = asyncio.Event()
        self._radar_events_refresh = asyncio.Event()
        self._signals_resync_required = False
        self._tasks: list[asyncio.Task[Any]] = []
        self._lock_file: Any = None
        self._http: httpx.AsyncClient | None = None
        self._running = False
        self._connected = False
        self._connection_status = "disabled" if not self._active else "unconfigured" if not self._api_key else "stopped"
        self._last_error: str | None = None
        self._reconnect_count = 0
        self._last_message_at: str | None = None

    @property
    def _active(self) -> bool:
        return self.enabled or self.signals_enabled

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._allocate()
        self._tasks = [asyncio.create_task(self._housekeeping()), asyncio.create_task(self._publisher())]
        if self._active and self._radar_loader:
            self._tasks.append(asyncio.create_task(self._radar_loop()))
        if self._active and self._radar_event_loader:
            self._tasks.append(asyncio.create_task(self._radar_events_loop()))
        if self._active and self._api_key:
            self._http = httpx.AsyncClient(timeout=10.0, follow_redirects=False)
            self._tasks.extend([asyncio.create_task(self._connection_loop()), asyncio.create_task(self._warm_loop())])

    async def close(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._http is not None:
            await self._http.aclose()
            self._http = None
        self._release_lock()
        self._connected = False
        self._set_connection_status("stopped")
        for client in self._clients.values():
            self._emit(client, {"event": "status", "data": self._status()})
        self._clients.clear()

    async def subscribe(self, symbols: list[str], focus: list[str] | None = None) -> str:
        symbols = normalize_symbols(symbols)
        focused = normalize_symbols(focus or [])
        if not set(focused).issubset(symbols):
            raise ValueError("Focused stocks must be part of the page subscription")
        self._expire_clients()
        if len(self._clients) >= MAX_CLIENTS:
            raise ValueError("Too many quote connections")
        client_id = uuid.uuid4().hex
        self._clients[client_id] = _Client(symbols, focused, time.monotonic())
        self._allocate()
        self._radar_refresh.set()
        self._radar_events_refresh.set()
        return client_id

    def unsubscribe(self, client_id: str) -> None:
        if self._clients.pop(client_id, None) is not None:
            self._allocate()

    async def snapshot(self, symbols: list[str]) -> dict[str, Any]:
        return {"quotes": [self._quote_view(symbol) for symbol in normalize_symbols(symbols)], "status": self._status()}

    async def events(self, client_id: str) -> AsyncIterator[dict[str, Any]]:
        client = self._clients.get(client_id)
        if client is None:
            return
        try:
            client.last_seen = time.monotonic()
            yield {"event": "quotes", "data": await self.snapshot(client.symbols)}
            if self._radar_event_loader:
                if self._radar_events_loaded:
                    self._emit_radar(client, list(self._radar_events.values()))
                else:
                    yield {"event": "status", "data": {**self._status(), "resync_required": True}}
            while client_id in self._clients and self._running:
                client.last_seen = time.monotonic()
                if client.resync_required:
                    # A slow browser may have missed a radar event. It must
                    # refetch the durable radar state, not infer the gap.
                    client.resync_required = False
                    yield {"event": "status", "data": {**self._status(), "resync_required": True}}
                try:
                    event = await asyncio.wait_for(client.queue.get(), timeout=min(10, self._release_seconds / 2))
                except TimeoutError:
                    event = {"event": "status", "data": self._status()}
                yield event
        finally:
            self.unsubscribe(client_id)

    def _status(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "signals_enabled": self.signals_enabled,
            "configured": bool(self._api_key),
            "public_enabled": self.public_enabled,
            "connected": self._connected,
            "connection_status": self._connection_status,
            "max_symbols": self.max_symbols,
            "allocated_symbols": len(self._desired_symbols),
            "client_count": len(self._clients),
            "market_session": market_session(_utcnow()),
            "last_message_at": self._last_message_at,
            "reconnect_count": self._reconnect_count,
            "last_error": self._last_error,
            "signals_resync_required": self._signals_resync_required,
            "as_of": _iso(_utcnow()),
        }

    def _quote_view(self, symbol: str) -> dict[str, Any]:
        provider_symbol = _provider_symbol(symbol)
        cached = self._quotes.get(provider_symbol)
        result = {"symbol": symbol, "price": None, "previous_close": None, "change": None, "change_pct": None,
                  "trade_at": None, "received_at": None, "session": market_session(_utcnow()), "source": None}
        if cached:
            result.update({key: value for key, value in cached.items() if not key.startswith("_")})
        result["symbol"] = symbol
        if not self._active:
            subscription = "disabled"
        elif not self._api_key:
            subscription = "unconfigured"
        elif provider_symbol not in self._desired_symbols:
            subscription = "limited"
        elif provider_symbol in self._provider_unavailable and not cached:
            subscription = "unavailable"
        elif self._connected and provider_symbol in self._sent_symbols and cached and cached["source"] == "finnhub_websocket":
            subscription = "live"
        else:
            subscription = "pending"
        result["subscription_status"] = subscription
        if subscription == "unavailable":
            result["subscription_reason"] = "provider_quote_unavailable"
        if not cached:
            result["freshness"] = "missing"
        elif cached["source"] == "finnhub_rest":
            result["freshness"] = "snapshot"
        elif subscription == "live" and (_utcnow() - cached["_trade_time"]).total_seconds() <= 60:
            result["freshness"] = "live"
        else:
            result["freshness"] = "stale"
        return result

    def _allocate(self) -> None:
        ordered = list(TOP_SYMBOLS) if self.enabled else []
        ordered.extend(self._radar_symbols)
        for client in self._clients.values():
            ordered.extend(client.focus)
        for client in self._clients.values():
            ordered.extend(client.symbols)
        desired = list(dict.fromkeys(_provider_symbol(symbol) for symbol in ordered))[:self.max_symbols] if self._active and self._api_key else []
        radar_aliases: dict[str, list[str]] = {}
        page_aliases: dict[str, list[str]] = {}
        for symbol in self._radar_symbols:
            radar_aliases.setdefault(_provider_symbol(symbol), []).append(symbol)
        for client in self._clients.values():
            for symbol in client.symbols:
                provider = _provider_symbol(symbol)
                if provider in desired and provider not in radar_aliases:
                    page_aliases.setdefault(provider, []).append(symbol)
        self._signal_symbols = {
            provider: list(dict.fromkeys(radar_aliases.get(provider) or page_aliases.get(provider) or [provider]))
            for provider in desired
        }
        if desired != self._desired_symbols:
            self._desired_symbols = desired
            self._subscription_changed.set()
            self._all_dirty = True
        self._status_dirty = True

    def _expire_clients(self) -> None:
        cutoff = time.monotonic() - self._release_seconds
        expired = [key for key, client in self._clients.items() if client.last_seen <= cutoff]
        for key in expired:
            self._clients.pop(key, None)
        if expired:
            self._allocate()

    def _emit(self, client: _Client, event: dict[str, Any]) -> None:
        if client.queue.full():
            client.queue.get_nowait()
            client.resync_required = True
        client.queue.put_nowait(event)

    async def _publisher(self) -> None:
        while self._running:
            await asyncio.sleep(self._interval)
            dirty, self._dirty_symbols = self._dirty_symbols, set()
            all_dirty, self._all_dirty = self._all_dirty, False
            status_dirty, self._status_dirty = self._status_dirty, False
            for client in list(self._clients.values()):
                if all_dirty or dirty.intersection(client.provider_symbols):
                    self._emit(client, {"event": "quotes", "data": await self.snapshot(client.symbols)})
                elif status_dirty:
                    self._emit(client, {"event": "status", "data": self._status()})

    async def _housekeeping(self) -> None:
        while self._running:
            self._expire_clients()
            # Age and session can change without receiving another trade.
            # Publish those transitions so an idle quote never remains "live"
            # indefinitely in an already-open browser.
            current_session = market_session(_utcnow())
            if current_session != self._market_session:
                self._market_session = current_session
                self._status_dirty = self._all_dirty = True
            for symbol in list(self._quotes):
                freshness = self._quote_view(symbol)["freshness"]
                if self._freshness.get(symbol) != freshness:
                    self._freshness[symbol] = freshness
                    self._dirty_symbols.add(symbol)
            self._trim_cache()
            await asyncio.sleep(1)

    async def _radar_loop(self) -> None:
        while self._running:
            self._radar_refresh.clear()
            await self._poll_radar_inventory()
            try:
                # Inventory reads are local and cheap. A newly committed
                # five-minute scan should enter monitoring within one second.
                await asyncio.wait_for(self._radar_refresh.wait(), timeout=1)
            except TimeoutError:
                pass

    async def _radar_events_loop(self) -> None:
        while self._running:
            self._radar_events_refresh.clear()
            # Keep this independent of candidate reads and trade arrival. A
            # complete-bar worker commit must reach an idle browser as well.
            await self._poll_radar_events()
            try:
                await asyncio.wait_for(self._radar_events_refresh.wait(), timeout=1)
            except TimeoutError:
                pass

    async def _poll_radar_inventory(self) -> None:
        try:
            rows = await asyncio.wait_for(self._radar_loader(), timeout=10)
            symbols = []
            for row in rows[:500]:
                raw = row.get("symbol", row.get("ticker")) if isinstance(row, dict) else row
                try:
                    symbols.extend(normalize_symbols([raw]))
                except ValueError:
                    continue
            self._radar_symbols = list(dict.fromkeys(symbols))
            self._allocate()
        except Exception:
            self._last_error = "radar_refresh_failed"
            self._status_dirty = True

    async def _poll_radar_events(self) -> None:
        started_sequence = self._radar_sequence
        try:
            rows = await asyncio.wait_for(self._radar_event_loader(), timeout=10)
            if not isinstance(rows, list):
                raise ValueError("Invalid radar update inventory")
            self._publish_radar_updates(rows[:MAX_RADAR_EVENTS])
            self._radar_events_loaded = True
            if self._last_error == "radar_events_refresh_failed":
                self._last_error = None
                self._signals_resync_required = False
                self._status_dirty = True
            current_ids = {str(row.get("event_id")) for row in rows[:MAX_RADAR_EVENTS] if isinstance(row, dict)}
            # Remove events outside the repository's recent window. Preserve
            # callbacks committed while this (possibly older) read was running.
            for event_id in list(self._radar_events):
                if event_id not in current_ids and self._radar_event_sequences[event_id] <= started_sequence:
                    self._forget_radar_event(event_id)
        except Exception:
            self._radar_events_loaded = False
            self._last_error = "radar_events_refresh_failed"
            self._signals_resync_required = True
            self._status_dirty = True
            for client in self._clients.values():
                client.resync_required = True

    def _forget_radar_event(self, event_id: str) -> None:
        self._radar_events.pop(event_id, None)
        self._radar_event_sequences.pop(event_id, None)
        for client in self._clients.values():
            client.radar_versions.pop(event_id, None)

    def _publish_radar_updates(self, events: list[dict[str, Any]], *, fallback_symbol: str | None = None) -> None:
        changes = []
        for raw in events[:MAX_RADAR_EVENTS]:
            if not isinstance(raw, dict):
                continue
            symbol = raw.get("symbol", raw.get("ticker", fallback_symbol))
            if not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol):
                continue
            event = dict(raw)
            event_id, version = event.get("event_id"), event.get("state_version")
            if not isinstance(event_id, str) or not event_id or len(event_id) > 256 or not isinstance(version, int) or isinstance(version, bool) or version < 0:
                # A callback may return an unversioned transient notice. It is
                # deliverable but cannot replace a durable versioned state.
                if fallback_symbol:
                    changes.append(event)
                continue
            previous = self._radar_events.get(event_id)
            if previous and previous["state_version"] >= version:
                continue
            event.setdefault("symbol", symbol)
            self._radar_sequence += 1
            self._radar_events[event_id] = event
            self._radar_events.move_to_end(event_id)
            self._radar_event_sequences[event_id] = self._radar_sequence
            changes.append(event)
        while len(self._radar_events) > MAX_RADAR_EVENTS:
            self._forget_radar_event(next(iter(self._radar_events)))
        if changes:
            for client in list(self._clients.values()):
                self._emit_radar(client, changes, fallback_symbol=fallback_symbol)

    def _emit_radar(self, client: _Client, events: list[dict[str, Any]], *, fallback_symbol: str | None = None) -> None:
        relevant = []
        for event in events:
            symbol = event.get("symbol", event.get("ticker", fallback_symbol))
            if not isinstance(symbol, str) or _provider_symbol(symbol) not in client.provider_symbols:
                continue
            event_id, version = event.get("event_id"), event.get("state_version")
            if isinstance(event_id, str) and 0 < len(event_id) <= 256 and isinstance(version, int) and not isinstance(version, bool) and version >= 0:
                if client.radar_versions.get(event_id, -1) >= version:
                    continue
                client.radar_versions[event_id] = version
            relevant.append(event)
        if relevant:
            self._emit(client, {"event": "radar", "data": {"events": relevant}})

    def _trim_cache(self) -> None:
        # A page can name many stocks, but only admitted symbols create cache
        # entries. Keep a bounded recent cache for quick page back-navigation.
        protected = set(self._desired_symbols)
        for symbol in list(self._quotes):
            if len(self._quotes) <= MAX_CACHED_QUOTES:
                break
            if symbol not in protected:
                self._quotes.pop(symbol, None)
                self._baselines.pop(symbol, None)
                self._rest_attempts.pop(symbol, None)
                self._recent_trades.pop(symbol, None)
                self._freshness.pop(symbol, None)
        # Failed REST lookups must not accumulate when visitors change pages.
        for mapping in (self._baselines, self._rest_attempts, self._recent_trades):
            for symbol in list(mapping):
                if symbol not in protected and symbol not in self._quotes:
                    mapping.pop(symbol, None)
        self._provider_unavailable.intersection_update(protected | self._quotes.keys())

    def _try_lock(self) -> bool:
        if self._lock_file is not None:
            return True
        file = None
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            file = self._lock_path.open("a+b")
            fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._lock_file = file
            return True
        except OSError:
            if file is not None:
                file.close()
            return False

    def _release_lock(self) -> None:
        if self._lock_file is not None:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()
            self._lock_file = None

    def _set_connection_status(self, status: str) -> None:
        self._connection_status = status
        self._status_dirty = True
        self._all_dirty = True

    async def _send_subscriptions(self, socket: Any) -> None:
        while self._running:
            await self._subscription_changed.wait()
            self._subscription_changed.clear()
            desired = list(self._desired_symbols)
            # Unsubscribe first: allocation changes must never transiently
            # exceed the provider's 50-code limit.
            for symbol in sorted(self._sent_symbols - set(desired)):
                await socket.send(json.dumps({"type": "unsubscribe", "symbol": symbol}))
                self._sent_symbols.discard(symbol)
            for symbol in desired:
                if symbol not in self._sent_symbols:
                    await socket.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                    self._sent_symbols.add(symbol)
            self._all_dirty = True

    async def _connection_loop(self) -> None:
        delay = 1.0
        try:
            while self._running:
                if not self._try_lock():
                    self._set_connection_status("waiting_for_lock")
                    await asyncio.sleep(2)
                    continue
                sender: asyncio.Task[Any] | None = None
                receiver: asyncio.Task[Any] | None = None
                connected_at = time.monotonic()
                try:
                    self._set_connection_status("connecting" if not self._reconnect_count else "reconnecting")
                    async with connect(
                        f"wss://ws.finnhub.io?token={urlquote(self._api_key, safe='')}",
                        ping_interval=20, ping_timeout=20, open_timeout=15,
                        close_timeout=5, max_size=1024 * 1024, max_queue=32,
                        logger=_transport_logger,
                    ) as socket:
                        self._connected = True
                        connected_at = time.monotonic()
                        self._last_error = None
                        self._sent_symbols.clear()
                        self._set_connection_status("connected")
                        self._subscription_changed.set()
                        # Revalidate baselines after a connection gap, subject
                        # to the same global REST reservation and retry budget.
                        for baseline in self._baselines.values():
                            baseline["needs_refresh"] = True
                        sender = asyncio.create_task(self._send_subscriptions(socket))

                        async def receive() -> None:
                            async for raw in socket:
                                await self._process_message(raw)

                        receiver = asyncio.create_task(receive())
                        completed, _ = await asyncio.wait([sender, receiver], return_when=asyncio.FIRST_COMPLETED)
                        for task in completed:
                            task.result()
                except Exception:
                    # Never propagate provider payloads, URLs, or key text.
                    self._last_error = "upstream_unavailable"
                finally:
                    children = [task for task in (sender, receiver) if task is not None]
                    for task in children:
                        task.cancel()
                    await asyncio.gather(*children, return_exceptions=True)
                    self._connected = False
                    self._sent_symbols.clear()
                self._reconnect_count += 1
                self._set_connection_status("reconnecting")
                if time.monotonic() - connected_at >= 60:
                    delay = 1.0
                await asyncio.sleep(delay)
                delay = min(60.0, delay * 2)
        finally:
            self._connected = False
            self._release_lock()

    async def _process_message(self, raw: str | bytes) -> None:
        try:
            message = json.loads(raw)
        except (ValueError, TypeError, UnicodeDecodeError):
            return
        if not isinstance(message, dict):
            return
        self._last_message_at = _iso(_utcnow())
        if message.get("type") == "error":
            raise RuntimeError("Quote provider rejected the connection")
        if message.get("type") != "trade" or not isinstance(message.get("data"), list):
            return
        # Do not collapse this array to its last price. A single frame may
        # contain a breakout followed by a retreat below the same threshold.
        for raw_trade in message["data"]:
            await self._process_trade(raw_trade)

    async def _process_trade(self, raw: Any) -> None:
        if not isinstance(raw, dict) or not isinstance(raw.get("s"), str):
            return
        symbol = _provider_symbol(raw["s"])
        if symbol not in self._desired_symbols:
            return
        price = _positive(raw.get("p"))
        at = _timestamp(raw.get("t"), milliseconds=True)
        received = _utcnow()
        if price is None or at is None or (at - received).total_seconds() > 10:
            return
        session = market_session(at)
        conditions = raw.get("c", [])
        if conditions is None:
            conditions = []
        if not isinstance(conditions, list) or len(conditions) > 16:
            return
        if any(not isinstance(value, (str, int)) or isinstance(value, bool) for value in conditions):
            return
        conditions = [str(value) for value in conditions]
        allowed = _LAST_PRICE_CONDITIONS | ({"24"} if session in {"premarket", "postmarket"} else set())
        if any(condition not in allowed for condition in conditions):
            return
        if isinstance(raw.get("v"), bool):
            return
        try:
            volume = float(raw.get("v", 0))
        except (ValueError, TypeError, OverflowError):
            return
        if not math.isfinite(volume) or volume < 0:
            return
        previous = self._quotes.get(symbol)
        if previous and at < previous["_trade_time"]:
            return
        identity = (at, price, volume, tuple(conditions))
        seen = self._recent_trades.setdefault(symbol, deque(maxlen=128))
        if identity in seen:
            return
        seen.append(identity)
        trade = {"symbol": symbol, "price": price, "trade_at": _iso(at), "received_at": _iso(received),
                 "session": session, "source": "finnhub_websocket", "conditions": conditions, "volume": volume}
        self._store_quote(symbol, price, at, received, "finnhub_websocket")
        if self.signals_enabled and self._trade_handler and conditions and volume > 0 and session != "closed" and (received - at).total_seconds() <= 60:
            try:
                # Match the exact symbols saved by the radar worker while
                # sharing one physical quote/subscription across aliases.
                changes = []
                for original in self._signal_symbols.get(symbol, [symbol]):
                    updates = await self._trade_handler({**trade, "symbol": original})
                    if updates:
                        changes.extend(updates if isinstance(updates, list) else [updates])
            except Exception:
                self._last_error = "radar_trade_failed"
                self._status_dirty = True
                self._signals_resync_required = True
                self._radar_refresh.set()
                self._radar_events_refresh.set()
                for client in self._clients.values():
                    if symbol in client.provider_symbols:
                        client.resync_required = True
                return
            self._signals_resync_required = False
            if changes:
                self._radar_refresh.set()
                self._radar_events_refresh.set()
                events = changes if isinstance(changes, list) else [changes]
                self._publish_radar_updates(events, fallback_symbol=symbol)

    def _store_quote(self, symbol: str, price: float, at: datetime, received: datetime, source: str) -> None:
        symbol = _provider_symbol(symbol)
        self._provider_unavailable.discard(symbol)
        baseline = self._baselines.get(symbol)
        previous_close = None
        if baseline:
            # Before the first regular-session quote of a new trading day,
            # yesterday's REST `c` is today's comparison close; `pc` is older.
            trade_day = at.astimezone(ET).date()
            if trade_day == baseline["trade_day"]:
                previous_close = baseline["previous_close"]
            elif trade_day == next_trading_day(baseline["trade_day"]):
                previous_close = baseline["close"]
        change = price - previous_close if previous_close else None
        self._quotes[symbol] = {"symbol": symbol, "price": price, "previous_close": previous_close,
                                "change": change, "change_pct": change / previous_close * 100 if change is not None else None,
                                "trade_at": _iso(at), "received_at": _iso(received), "session": market_session(at),
                                "source": source, "_trade_time": at}
        self._quotes.move_to_end(symbol)
        self._dirty_symbols.add(symbol)
        if len(self._quotes) > MAX_CACHED_QUOTES:
            self._trim_cache()

    async def _warm_loop(self) -> None:
        while self._running:
            if self._lock_file is not None:
                today = _utcnow().astimezone(ET).date()
                for symbol in list(self._desired_symbols):
                    if symbol not in self._desired_symbols:
                        continue
                    baseline = self._baselines.get(symbol)
                    if baseline and baseline["fetched_day"] == today and not baseline.get("needs_refresh"):
                        continue
                    if time.monotonic() - self._rest_attempts.get(symbol, -1000) < 60:
                        continue
                    await self._warm_symbol(symbol)
            await asyncio.sleep(1)

    async def _warm_symbol(self, symbol: str) -> None:
        symbol = _provider_symbol(symbol)
        if symbol not in self._desired_symbols or self._http is None:
            return
        self._rest_attempts[symbol] = time.monotonic()
        if not await async_reserve_finnhub_request(self._api_key, db_path=self._budget_path):
            return
        if symbol not in self._desired_symbols:
            return
        try:
            # Header authentication prevents request loggers from exposing the
            # API key in an otherwise harmless /quote URL.
            response = await self._http.get(f"{self._base_url}/quote", params={"symbol": symbol}, headers={"X-Finnhub-Token": self._api_key})
            if response.status_code == 429:
                await asyncio.to_thread(mark_finnhub_rate_limited, self._api_key,
                                        retry_after=response.headers.get("Retry-After", "60"), db_path=self._budget_path)
                self._last_error = "rest_rate_limited"
                self._status_dirty = True
                return
            response.raise_for_status()
            if symbol in self._desired_symbols:
                self._apply_rest_quote(symbol, response.json())
        except Exception:
            self._last_error = "rest_quote_unavailable"
            self._status_dirty = True

    def _apply_rest_quote(self, symbol: str, payload: Any) -> None:
        symbol = _provider_symbol(symbol)
        if not isinstance(payload, dict):
            return
        price, previous_close = _positive(payload.get("c")), _positive(payload.get("pc"))
        at, received = _timestamp(payload.get("t")), _utcnow()
        if price is None and payload.get("c") == 0 and symbol not in self._quotes:
            self._provider_unavailable.add(symbol)
            self._dirty_symbols.add(symbol)
            return
        if price is None or at is None or (at - received).total_seconds() > 10:
            return
        baseline = self._baselines.get(symbol)
        if baseline is None or at >= baseline["trade_time"]:
            self._baselines[symbol] = {"close": price, "previous_close": previous_close,
                                        "trade_day": at.astimezone(ET).date(), "trade_time": at,
                                        "fetched_day": received.astimezone(ET).date(),
                                        "needs_refresh": previous_close is None}
        previous = self._quotes.get(symbol)
        if previous and (previous["_trade_time"] > at or (previous["_trade_time"] == at and previous["source"] == "finnhub_websocket")):
            # Refresh the comparison base but never replace a newer live price
            # with a slower HTTP response, including during reconnection.
            self._store_quote(symbol, previous["price"], previous["_trade_time"],
                              datetime.fromisoformat(previous["received_at"].replace("Z", "+00:00")), previous["source"])
        else:
            self._store_quote(symbol, price, at, received, "finnhub_rest")


__all__ = ["QuoteHub", "TOP_SYMBOLS", "market_session", "normalize_symbols"]
