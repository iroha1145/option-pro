"""Trade-only triggers over published radar candidates; bars own confirmation."""

from __future__ import annotations

import asyncio
import logging
import math
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.repository import BreakoutRepository, BreakoutRepositoryError

logger = logging.getLogger(__name__)


def _time(value: Any) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("trade times must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


class BreakoutRealtimeAdapter:
    def __init__(
        self, settings: BreakoutSettings | None = None,
        repository: BreakoutRepository | None = None,
        *, now: Callable[[], datetime] | None = None,
    ) -> None:
        self.settings = settings or get_breakout_settings()
        self.repository = repository or BreakoutRepository(self.settings.db_path)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._events: dict[str, list[dict[str, Any]]] = {}
        self._watermarks: dict[str, datetime] = {}
        self._loaded = False
        self._serial = asyncio.Lock()
        self._clock = MarketClock()

    async def radar_symbols(self) -> list[str]:
        """Read-only inventory; triggered events first, then existing scan rank."""
        if not self.settings.enabled:
            return []
        try:
            events = await asyncio.to_thread(self._load_events)
        except (FileNotFoundError, OSError, sqlite3.Error, BreakoutRepositoryError, ValueError):
            self._events = {}
            self._loaded = True
            return []
        self._events = {}
        for event in events:
            self._events.setdefault(str(event["ticker"]), []).append(event)
        self._watermarks = {symbol: value for symbol, value in self._watermarks.items() if symbol in self._events}
        self._loaded = True
        return list(self._events)

    @staticmethod
    def _change(event: Mapping[str, Any]) -> dict[str, Any]:
        return {key: event.get(key) for key in (
            "event_id", "ticker", "lifecycle_state", "state_version", "evidence_at",
            "trigger_source", "triggered_at", "state_changed_at", "event_at", "last_seen_at",
        )} | {"symbol": str(event["ticker"]),
             "current_price": (event.get("features") or {}).get("current_price", event.get("event_price"))}

    async def radar_updates(self) -> list[dict[str, Any]]:
        """Recent durable revisions for SSE initial delivery and scan updates."""
        if not self.settings.enabled:
            return []
        try:
            events = await asyncio.to_thread(self.repository.recent_live_events, as_of=self._now())
        except (FileNotFoundError, OSError, sqlite3.Error, BreakoutRepositoryError, ValueError):
            return []
        return [self._change(event) for event in events]

    def _load_events(self) -> list[dict[str, Any]]:
        now = self._now()
        batch = self.repository.load_carryover_events(
            as_of=now, event_ttl_seconds=self.settings.event_ttl_seconds,
            limit=150, expired_due_limit=40,
        )
        events = self.repository.overlay_live_events(batch.events, as_of=now)
        latest = self.repository.latest_completed_scan() or {}
        ranks = {str(event["event_id"]): rank for rank, event in enumerate(latest.get("events") or [])}
        active = [event for event in events
                  if str(event.get("lifecycle_state")) not in {"FAILED", "EXPIRED", "DISCOVERED"}
                  and 0 <= (now - _time(event["first_seen_at"])).total_seconds() <= self.settings.event_ttl_seconds]
        active.sort(key=lambda event: (
            str(event.get("lifecycle_state")) == "WATCHING",
            ranks.get(str(event["event_id"]), 10_000),
            -(_finite((event.get("scores") or {}).get("alert_priority_score")) or 0),
            str(event["event_id"]),
        ))
        return active

    def _eligible(self, event: Mapping[str, Any], price: float, trade_at: datetime) -> bool:
        if event.get("lifecycle_state") != "WATCHING":
            return False
        if not (0 <= (trade_at - _time(event["first_seen_at"])).total_seconds() <= self.settings.event_ttl_seconds):
            return False
        # A trade must occur after the published observation, never backfill a
        # trigger from before discovery or from a stale market filter snapshot.
        observed = _time(event["last_seen_at"])
        if not (0 < (trade_at - observed).total_seconds() <= max(900, self.settings.scan_interval_regular_seconds * 3)):
            return False
        features = event.get("features") or {}
        quality = event.get("data_quality") or {}
        if features.get("status") != "active":
            return False
        eligibility = quality.get("market_eligibility") or features.get("market_eligibility")
        if eligibility not in {"allowed", "preferred", "caution"}:
            return False
        if quality.get("market_shape_status") not in {"active", "degraded"}:
            return False
        setup = str(event.get("setup_type"))
        structure = event.get("structure") or {}
        if setup == "DAILY_BASE_BREAKOUT":
            if structure.get("status") != "active":
                return False
            resistance = _finite((structure.get("resistance_zone") or {}).get("high"))
        elif setup == "OPENING_RANGE_BREAKOUT" and features.get("opening_range_complete"):
            resistance = _finite(features.get("opening_range_high"))
        else:
            # Premarket gaps, unstructured movers and retests retain their
            # existing completed-bar classification and eligibility rules.
            return False
        atr = _finite(features.get("atr20"))
        if resistance is None or resistance <= 0 or atr is None or atr <= 0:
            return False
        buffer = max(price * self.settings.break_buffer_pct, atr * self.settings.break_buffer_atr)
        return price > resistance + buffer

    async def handle_trade(self, trade: dict[str, Any]) -> list[dict[str, Any]]:
        """Evaluate each eligible trade in order; never infer a gap or a bar."""
        async with self._serial:
            try:
                symbol = str(trade["symbol"]).strip().upper()
                price = _finite(trade["price"])
                trade_at, received_at = _time(trade["trade_at"]), _time(trade["received_at"])
                if (price is None or price <= 0 or trade.get("session") != "regular"
                    or trade.get("source", "finnhub") not in {"finnhub", "finnhub_websocket"}
                    or not -2 <= (received_at - trade_at).total_seconds() <= 30
                    or not -2 <= (self._now() - received_at).total_seconds() <= 30
                    or self._clock.snapshot(trade_at).session.value != "regular"):
                    return []
                if self._watermarks.get(symbol, trade_at) > trade_at:
                    return []
                self._watermarks[symbol] = trade_at
                if not self._loaded:
                    await self.radar_symbols()
                changes = []
                for event in self._events.get(symbol, []):
                    if not self._eligible(event, price, trade_at):
                        continue
                    evidence = trade_at.isoformat()
                    updated = {
                        **event, "lifecycle_state": "TRIGGERED", "previous_state": "WATCHING",
                        "event_at": evidence, "triggered_at": evidence, "state_changed_at": evidence,
                        "last_seen_at": evidence, "evidence_at": evidence, "trigger_source": "finnhub",
                        "event_price": price, "event_bar_interval": "trade",
                        "transition_reason": "realtime_trade_above_breakout_buffer",
                        "features": {**dict(event.get("features") or {}), "current_price": price,
                                     "realtime_received_at": received_at.isoformat()},
                    }
                    committed = await asyncio.to_thread(self.repository.commit_live_trigger, updated)
                    if committed is None:
                        continue
                    event.update(committed)
                    changes.append(self._change(committed))
                return changes
            except (KeyError, TypeError, ValueError, OSError, sqlite3.Error, BreakoutRepositoryError):
                logger.warning("Could not evaluate realtime radar trade for %s", trade.get("symbol"))
                return []
