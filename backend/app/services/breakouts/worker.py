"""Standalone Breakout Radar worker and command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import logging
import os
import random
import signal
import socket
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.services.breakouts.clock import MarketClock, MarketClockSnapshot
from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.errors import FAILURE_DOMAINS, BreakoutStageError
from app.services.breakouts.health import check_breakout_health
from app.services.breakouts.models import MarketSession
from app.services.breakouts.providers.base import ProviderError
from app.services.breakouts.repository import (
    DEFAULT_LOCK_NAME,
    BreakoutRepository,
    BreakoutRepositoryError,
    LeaseLostError,
    SCHEMA_VERSION,
    SchemaVersionError,
)
from pydantic import ValidationError

logger = logging.getLogger(__name__)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("worker JSON datetime must be timezone-aware")
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    raise TypeError(type(value).__name__)


def _stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        allow_nan=False,
        default=_json_default,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class _ProviderUnavailableSnapshotError(RuntimeError):
    code = "provider_unavailable"

    def __init__(self, discovery: Any) -> None:
        warnings = list(getattr(discovery, "warnings", ()) or ())
        self.provider_warning = str(warnings[0])[:120] if warnings else None
        super().__init__("discovery Provider returned an unavailable snapshot")


class _ProviderDegradedEmptySnapshotError(_ProviderUnavailableSnapshotError):
    code = "provider_degraded_empty"

    def __init__(self, discovery: Any) -> None:
        super().__init__(discovery)
        self.args = ("degraded discovery snapshot contained no usable candidates",)


def _failure_domain(exc: Exception) -> str:
    explicit = str(getattr(exc, "failure_domain", "") or "")
    if explicit in FAILURE_DOMAINS:
        return explicit
    if isinstance(exc, (ProviderError, _ProviderUnavailableSnapshotError)):
        return "provider"
    if str(getattr(exc, "code", "") or "").startswith("provider_"):
        return "provider"
    if isinstance(exc, (sqlite3.Error, BreakoutRepositoryError, SchemaVersionError)):
        return "database"
    if isinstance(exc, ValidationError):
        return "configuration"
    return "local_processing"


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _scan_event_payload(event: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the stored T1 feature, excluding its read-API projection alias."""
    payload = dict(event)
    payload.pop("t1_priority", None)
    return payload


class BreakoutWorker:
    """Coordinate discovery and publication without holding a database lock."""

    def __init__(
        self,
        settings: BreakoutSettings,
        repository: BreakoutRepository,
        provider: Any = None,
        scan_service: Any = None,
        clock: MarketClock | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], Awaitable[None] | None] = asyncio.sleep,
        *,
        owner_id: str | None = None,
        lease_ttl_seconds: float | None = None,
        maximum_loop_stall_seconds: float | None = None,
        jitter: Callable[[float], float] | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.provider = provider
        self.scan_service = scan_service
        self.clock = clock or MarketClock()
        self._monotonic = monotonic
        self._sleeper = sleeper
        self.owner_id = owner_id or (
            f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:12]}"
        )
        self.lease_ttl_seconds = float(
            lease_ttl_seconds
            if lease_ttl_seconds is not None
            else getattr(settings, "worker_lease_ttl_seconds", 90.0)
        )
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be positive")
        self.maximum_loop_stall_seconds = float(
            maximum_loop_stall_seconds
            if maximum_loop_stall_seconds is not None
            else self.lease_ttl_seconds * 3.0
        )
        if self.maximum_loop_stall_seconds < self.lease_ttl_seconds:
            raise ValueError(
                "maximum_loop_stall_seconds cannot be shorter than the lease"
            )
        self._jitter = jitter or (lambda bound: random.uniform(0.0, bound))
        self._stop_requested = False
        self._owns_provider = False
        self._mode = "continuous"
        self._last_completed_scan_id: str | None = None
        self._last_completed_at: datetime | None = None
        self._wait_status = "idle"
        self._wait_details: Mapping[str, Any] | None = None

    def request_stop(self) -> None:
        self._stop_requested = True

    def _provider_instance(self) -> Any:
        if self.provider is None:
            from app.services.breakouts.providers import TradingViewDiscoveryProvider

            self.provider = TradingViewDiscoveryProvider(
                self.settings,
                monotonic=self._monotonic,
                sleeper=self._sleeper,
            )
            self._owns_provider = True
        return self.provider

    def _settings_hash(self) -> str:
        model_dump = getattr(self.settings, "model_dump", None)
        values = model_dump(mode="json") if callable(model_dump) else vars(self.settings)
        return _stable_hash(values)

    def _versions(self) -> dict[str, str]:
        names = {
            "api": "api_schema_version",
            "provider": "provider_schema_version",
            "features": "feature_version",
            "detector": "detector_version",
            "scoring": "scoring_version",
            "range_persistence": "range_persistence_version",
            "database": None,
        }
        result = {
            key: str(getattr(self.settings, attribute))
            for key, attribute in names.items()
            if attribute is not None
        }
        result["database"] = SCHEMA_VERSION
        return result

    async def _invoke_scan_service(
        self,
        discovery: Any,
        clock_snapshot: MarketClockSnapshot,
    ) -> Any:
        service = self.scan_service
        if service is None:
            return None
        target = None
        for name in ("build_snapshot", "run_scan", "scan", "process"):
            method = getattr(service, name, None)
            if callable(method):
                target = method
                break
        if target is None and callable(service):
            target = service
        if target is None:
            raise TypeError("scan_service must be callable")

        candidates = list(getattr(discovery, "candidates", ()) or ())
        carryover_limit = min(
            200,
            max(1, int(getattr(self.settings, "provider_result_limit", 150))),
        )
        expired_due_limit = min(
            carryover_limit,
            40,
            max(1, int(getattr(self.settings, "intraday_enrich_limit", 40))),
            max(1, int(getattr(self.settings, "expired_due_limit", 40))),
        )
        carryover_batch = self.repository.load_carryover_events(
            as_of=clock_snapshot.as_of,
            event_ttl_seconds=float(
                getattr(self.settings, "event_ttl_seconds", 86_400)
            ),
            limit=carryover_limit,
            expired_due_limit=expired_due_limit,
        )
        carryover_events = list(carryover_batch.events)
        try:
            carryover_events = self.repository.overlay_t1_evaluations(carryover_events)
        except Exception as exc:
            logger.warning(
                "Breakout T1 carryover overlay failed (%s)", type(exc).__name__,
            )
        # The read overlay exposes a top-level alias that BreakoutEvent forbids.
        # Retain features.t1_priority for evaluation and all previous-event paths.
        carryover_events = [_scan_event_payload(event) for event in carryover_events]
        effective_events = self.repository.overlay_live_events(
            carryover_events, as_of=clock_snapshot.as_of,
        )
        realtime_events = [
            _scan_event_payload(event)
            for event in effective_events
            if event.get("state_version")
        ]
        previous_events: dict[str, list[Mapping[str, Any]]] = {}
        for event in carryover_events:
            ticker = str(event.get("ticker") or "").strip().upper()
            if ticker:
                previous_events.setdefault(ticker, []).append(event)
        available = {
            "discovery": discovery,
            "discovery_snapshot": discovery,
            "provider_snapshot": discovery,
            "candidates": candidates,
            "previous_events": previous_events,
            "carryover_events": carryover_events,
            "realtime_events": realtime_events,
            "expired_due_event_ids": carryover_batch.expired_due_event_ids,
            "carryover_has_more": carryover_batch.has_more,
            "clock_snapshot": clock_snapshot,
            "as_of": clock_snapshot.as_of,
            "session": clock_snapshot.session,
            "trading_date": clock_snapshot.trading_date,
            "settings": self.settings,
        }
        signature = inspect.signature(target)
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )
        kwargs = {
            name: value
            for name, value in available.items()
            if accepts_kwargs or name in signature.parameters
        }
        required_positional = [
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
            and parameter.default is inspect.Parameter.empty
            and parameter.name not in kwargs
        ]
        if required_positional:
            if len(required_positional) != 1:
                raise TypeError("scan_service has unsupported required parameters")
            return await _maybe_await(target(discovery, **kwargs))
        return await _maybe_await(target(**kwargs))

    @staticmethod
    def _publication_payload(discovery: Any, service_result: Any) -> dict[str, Any]:
        if service_result is None:
            result: dict[str, Any] = {"events": []}
        elif isinstance(service_result, Mapping):
            result = dict(service_result)
        else:
            model_dump = getattr(service_result, "model_dump", None)
            if not callable(model_dump):
                raise TypeError("scan_service result must be mapping-compatible")
            result = dict(model_dump(mode="python"))
        result.setdefault("provider_snapshot", discovery)
        result.setdefault("candidates", list(getattr(discovery, "candidates", ()) or ()))
        result.setdefault("events", [])
        return result

    def _provider_health(self, provider: Any, *, error_code: str | None = None) -> dict[str, Any]:
        value = getattr(provider, "health", None)
        health = dict(value) if isinstance(value, Mapping) else {}
        health.setdefault("provider", str(getattr(self.settings, "discovery_provider", "unknown")))
        if error_code is not None:
            health["status"] = "unavailable"
            health["error_code"] = error_code
            health["last_failure_at"] = self.clock.now()
            health["consecutive_failures"] = int(health.get("consecutive_failures") or 0) or 1
        elif health.get("last_error_code") and not health.get("error_code"):
            health["error_code"] = health["last_error_code"]
        health.pop("last_error_code", None)
        health["details"] = {
            "circuit_open": bool(health.pop("circuit_open", False)),
            "circuit_open_remaining_seconds": float(
                health.pop("circuit_open_remaining_seconds", 0.0) or 0.0
            ),
        }
        return health

    def _status(
        self,
        status: str,
        *,
        current_scan_id: str | None = None,
        error_code: str | None = None,
        details: Mapping[str, Any] | None = None,
    ) -> None:
        self.repository.update_worker_status(
            self.owner_id,
            self._mode,
            status,
            heartbeat_at=self.clock.now(),
            current_scan_run_id=current_scan_id,
            last_completed_scan_run_id=self._last_completed_scan_id,
            last_completed_at=self._last_completed_at,
            error_code=error_code,
            details=details,
        )

    async def _run_with_lease_heartbeat(
        self,
        operation: Awaitable[Any],
        lease_token: int,
        scan_id: str,
    ) -> Any:
        """Keep the fencing lease alive while Provider/enrichment is running."""
        task = asyncio.create_task(operation)
        # Renew with a wider safety margin than the lease's one-third point.
        # Busy shared runners and short test leases can otherwise consume the
        # whole remaining window before SQLite records the heartbeat.
        interval = max(0.01, min(30.0, self.lease_ttl_seconds / 4.0))
        loop = asyncio.get_running_loop()
        heartbeat_stop = threading.Event()
        lease_lost = threading.Event()
        last_loop_pulse = time.monotonic()
        last_successful_renewal = time.monotonic()
        maximum_loop_stall = self.maximum_loop_stall_seconds

        def heartbeat() -> None:
            nonlocal last_successful_renewal
            next_delay = interval
            try:
                while not heartbeat_stop.wait(next_delay):
                    if time.monotonic() - last_loop_pulse > maximum_loop_stall:
                        lease_lost.set()
                        loop.call_soon_threadsafe(task.cancel)
                        return
                    try:
                        renewed = self.repository.heartbeat_lock(
                            DEFAULT_LOCK_NAME,
                            self.owner_id,
                            lease_token,
                            self.lease_ttl_seconds,
                            self.clock.now(),
                        )
                    except Exception:
                        if (
                            time.monotonic() - last_successful_renewal
                            >= self.lease_ttl_seconds
                        ):
                            lease_lost.set()
                            loop.call_soon_threadsafe(task.cancel)
                            return
                        next_delay = min(1.0, max(0.01, interval / 4.0))
                        continue
                    if not renewed:
                        lease_lost.set()
                        loop.call_soon_threadsafe(task.cancel)
                        return
                    last_successful_renewal = time.monotonic()
                    next_delay = interval
                    try:
                        self._status("running", current_scan_id=scan_id)
                    except Exception:
                        # The fencing lease is authoritative. A best-effort status
                        # refresh must not turn a successful renewal into lease loss.
                        pass
            finally:
                if not heartbeat_stop.is_set() and not lease_lost.is_set():
                    lease_lost.set()
                    loop.call_soon_threadsafe(task.cancel)

        heartbeat_thread = threading.Thread(
            target=heartbeat,
            name="breakout-lease-heartbeat",
        )
        try:
            heartbeat_thread.start()
        except Exception:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=min(0.05, interval))
                last_loop_pulse = time.monotonic()
                if task in done:
                    return await task
        except asyncio.CancelledError:
            if lease_lost.is_set():
                raise LeaseLostError("worker lost its lease while scanning") from None
            raise
        finally:
            async def cleanup() -> None:
                heartbeat_stop.set()
                while heartbeat_thread.is_alive():
                    await asyncio.sleep(0.01)
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)

            cleanup_task = asyncio.create_task(cleanup())
            cleanup_cancelled: asyncio.CancelledError | None = None
            while not cleanup_task.done():
                try:
                    await asyncio.shield(cleanup_task)
                except asyncio.CancelledError as exc:
                    cleanup_cancelled = cleanup_cancelled or exc
                    continue
            await cleanup_task
            if cleanup_cancelled is not None:
                raise cleanup_cancelled

    async def _complete_pending_t1(
        self,
        market: MarketClockSnapshot,
        lease_token: int,
    ) -> dict[str, Any]:
        """Finish T1 for existing events after the regular close.

        This does not rediscover candidates, republish the immutable scan, or
        emit original breakout notifications.
        """

        from app.services.algorithm_modes import T1_ALGORITHM
        from app.services.breakouts.models import TemporalCutoff, normalize_ticker
        from app.services.breakouts.repository import T1_RETRY_MAX_ATTEMPTS
        from app.services.breakouts.t1_priority import (
            T1_MET,
            T1_NOT_APPLICABLE,
            T1_RETRYABLE_REASONS,
            T1_UNMET,
            attach_t1_features,
            t1_needs_close_eval,
        )

        try:
            scan = self.repository.latest_completed_scan()
        except Exception as exc:
            logger.warning(
                "Breakout T1 recent scan read failed (%s)", type(exc).__name__,
            )
            return {
                "attempted": 0, "completed": 0, "pending": 0,
                "retry": True, "reason": "t1_scan_read_failed",
                "retry_after_seconds": 30.0,
            }
        events = list((scan or {}).get("events") or [])
        if not events:
            return {"attempted": 0, "completed": 0, "pending": 0, "retry": False}
        try:
            events = self.repository.overlay_t1_evaluations(events)
        except Exception as exc:
            logger.warning(
                "Breakout T1 evaluation overlay failed (%s)", type(exc).__name__,
            )
            return {
                "attempted": 0, "completed": 0, "pending": len(events),
                "retry": True, "reason": "t1_overlay_read_failed",
                "retry_after_seconds": 30.0,
            }
        pending = [item for item in events if t1_needs_close_eval(item)]
        if not pending:
            return {"attempted": 0, "completed": 0, "pending": 0, "retry": False}

        def retry_identity(event: Mapping[str, Any]) -> tuple[str, str, str]:
            payload = event.get("t1_priority") if isinstance(event.get("t1_priority"), Mapping) else {}
            session_date = str(
                (payload or {}).get("session_date") or event.get("trading_date") or ""
            )
            event_id = str(event.get("event_id") or "")
            return f"{event_id}|{session_date}|{T1_ALGORITHM}", event_id, session_date

        def parse_eligible(raw: Any) -> bool:
            if not raw:
                return True
            try:
                when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            except ValueError:
                return True
            if when.tzinfo is None:
                when = when.replace(tzinfo=timezone.utc)
            return when <= market.as_of

        def persist_states(states: list[dict[str, Any]], *, required: bool = False) -> None:
            if not states:
                return
            try:
                self.repository.save_t1_retry_states(states)
            except Exception:
                if required:
                    raise

        stored = {}
        try:
            stored = self.repository.load_t1_retry_states(
                [retry_identity(item)[0] for item in pending]
            )
        except Exception:
            stored = {}
        eligible: list[dict[str, Any]] = []
        blocked_delays: list[float] = []
        exhausted_count = 0
        for event in pending:
            key, event_id, session_date = retry_identity(event)
            state = dict(stored.get(key) or {})
            if state.get("exhausted"):
                exhausted_count += 1
                continue
            if not parse_eligible(state.get("next_eligible_at")):
                try:
                    when = datetime.fromisoformat(
                        str(state.get("next_eligible_at")).replace("Z", "+00:00")
                    )
                    if when.tzinfo is None:
                        when = when.replace(tzinfo=timezone.utc)
                    blocked_delays.append(max(0.0, (when - market.as_of).total_seconds()))
                except (TypeError, ValueError):
                    blocked_delays.append(30.0)
                continue
            eligible.append(event)
        if not eligible:
            if blocked_delays:
                delay = min(blocked_delays)
                return {
                    "attempted": 0,
                    "completed": 0,
                    "pending": len(pending) - exhausted_count,
                    "retry": True,
                    "reason": "t1_retry_not_due",
                    "retry_after_seconds": delay,
                }
            return {
                "attempted": 0,
                "completed": 0,
                "pending": exhausted_count,
                "retry": False,
                "reason": "t1_retry_budget_exhausted",
            }

        def bump_states(
            events: Sequence[Mapping[str, Any]],
            reason: str,
            *,
            persist_required: bool = False,
        ) -> tuple[list[dict[str, Any]], float | None, int]:
            updates: list[dict[str, Any]] = []
            delays: list[float] = []
            max_attempt = 0
            for event in events:
                key, event_id, session_date = retry_identity(event)
                current = dict(stored.get(key) or {
                    "retry_key": key,
                    "event_id": event_id,
                    "session_date": session_date,
                    "algorithm": T1_ALGORITHM,
                    "attempt": 0,
                    "max_attempts": T1_RETRY_MAX_ATTEMPTS,
                })
                attempt = int(current.get("attempt") or 0) + 1
                cap = int(current.get("max_attempts") or T1_RETRY_MAX_ATTEMPTS)
                max_attempt = max(max_attempt, attempt)
                if attempt >= cap:
                    current.update(
                        {
                            "retry_key": key,
                            "event_id": event_id,
                            "session_date": session_date,
                            "algorithm": T1_ALGORITHM,
                            "attempt": attempt,
                            "max_attempts": cap,
                            "next_eligible_at": None,
                            "exhausted": True,
                            "last_reason": reason,
                        }
                    )
                else:
                    delay = min(300.0, 30.0 * (2 ** min(attempt - 1, 3)))
                    next_at = market.as_of.astimezone(timezone.utc) + timedelta(seconds=delay)
                    current.update(
                        {
                            "retry_key": key,
                            "event_id": event_id,
                            "session_date": session_date,
                            "algorithm": T1_ALGORITHM,
                            "attempt": attempt,
                            "max_attempts": cap,
                            "next_eligible_at": next_at.isoformat().replace("+00:00", "Z"),
                            "exhausted": False,
                            "last_reason": reason,
                        }
                    )
                    delays.append(delay)
                stored[key] = current
                updates.append(current)
            persist_states(updates, required=persist_required)
            return updates, (min(delays) if delays else None), max_attempt

        def retry_result(
            *,
            attempted: int,
            completed: int,
            pending_count: int,
            reason: str,
            events: Sequence[Mapping[str, Any]],
        ) -> dict[str, Any]:
            _, delay, attempt = bump_states(events, reason)
            if delay is None:
                return {
                    "attempted": attempted,
                    "completed": completed,
                    "pending": pending_count,
                    "retry": False,
                    "reason": "t1_retry_budget_exhausted",
                    "attempt": attempt,
                }
            return {
                "attempted": attempted,
                "completed": completed,
                "pending": pending_count,
                "retry": True,
                "reason": reason,
                "retry_after_seconds": delay,
                "attempt": attempt,
            }

        price_data = getattr(self.scan_service, "price_data", None)
        if price_data is None or not hasattr(price_data, "daily"):
            return retry_result(
                attempted=len(eligible),
                completed=0,
                pending_count=len(pending),
                reason="price_adapter_unavailable",
                events=eligible,
            )
        if not self.repository.heartbeat_lock(
            DEFAULT_LOCK_NAME,
            self.owner_id,
            lease_token,
            self.lease_ttl_seconds,
            self.clock.now(),
        ):
            raise LeaseLostError("worker lost its lease before T1 close completion")
        _, reserved_delay, reserved_attempt = bump_states(
            eligible,
            "t1_dispatch_reserved",
            persist_required=True,
        )

        def reserved_retry(
            *,
            attempted: int,
            completed: int,
            pending_count: int,
            reason: str,
        ) -> dict[str, Any]:
            if reserved_delay is None:
                return {
                    "attempted": attempted,
                    "completed": completed,
                    "pending": pending_count,
                    "retry": False,
                    "reason": "t1_retry_budget_exhausted",
                    "attempt": reserved_attempt,
                }
            return {
                "attempted": attempted,
                "completed": completed,
                "pending": pending_count,
                "retry": True,
                "reason": reason,
                "retry_after_seconds": reserved_delay,
                "attempt": reserved_attempt,
            }

        tickers = list(
            dict.fromkeys(
                normalize_ticker(item.get("ticker"))
                for item in eligible
                if item.get("ticker")
            )
        )
        cutoff = TemporalCutoff(event_at=market.as_of, session=market.session)

        async def fetch_daily() -> Any:
            return await price_data.daily(tickers, cutoff=cutoff, period="2y")

        try:
            daily_map = await self._run_with_lease_heartbeat(
                fetch_daily(),
                lease_token,
                None,
            )
        except (asyncio.CancelledError, LeaseLostError):
            raise
        except Exception:
            return reserved_retry(
                attempted=len(eligible),
                completed=0,
                pending_count=len(pending),
                reason="daily_fetch_failed",
            )
        if not isinstance(daily_map, Mapping):
            daily_map = {}
        completed = 0
        still_pending_events: list[dict[str, Any]] = []
        finished_keys: list[str] = []
        updated_events: list[dict[str, Any]] = []
        for event in eligible:
            ticker = normalize_ticker(event.get("ticker")) if event.get("ticker") else ""
            snapshot = daily_map.get(ticker) if ticker else None
            frame = getattr(snapshot, "frame", None)
            attached = attach_t1_features(
                event,
                frame if frame is not None else None,
                as_of=market.as_of,
                session=market.session,
            )
            updated_events.append(attached)
            evaluation = attached.get("t1_priority") if isinstance(attached.get("t1_priority"), Mapping) else {}
            status = str((evaluation or {}).get("status") or "")
            reason = str((evaluation or {}).get("reason") or "")
            key = retry_identity(event)[0]
            if status in {T1_MET, T1_UNMET, T1_NOT_APPLICABLE}:
                completed += 1
                finished_keys.append(key)
            elif status == "unavailable" and reason not in T1_RETRYABLE_REASONS:
                completed += 1
                finished_keys.append(key)
            else:
                still_pending_events.append(attached)
        if updated_events:
            try:
                self.repository.persist_t1_evaluations(updated_events)
            except Exception:
                return reserved_retry(
                    attempted=len(eligible),
                    completed=0,
                    pending_count=len(pending),
                    reason="t1_store_unavailable",
                )
        if finished_keys:
            try:
                self.repository.clear_t1_retry_states(finished_keys)
            except Exception:
                pass
        if still_pending_events:
            return reserved_retry(
                attempted=len(eligible),
                completed=completed,
                pending_count=len(still_pending_events) + exhausted_count + len(blocked_delays),
                reason="daily_incomplete",
            )
        leftover_pending = len(pending) - len(eligible) - exhausted_count
        if leftover_pending > 0 and blocked_delays:
            return {
                "attempted": len(eligible),
                "completed": completed,
                "pending": leftover_pending,
                "retry": True,
                "reason": "t1_retry_not_due",
                "retry_after_seconds": min(blocked_delays),
            }
        return {
            "attempted": len(eligible),
            "completed": completed,
            "pending": leftover_pending + exhausted_count,
            "retry": False,
        }

    async def _run_cycle(
        self,
        lease_token: int,
        clock_snapshot: MarketClockSnapshot | None = None,
    ) -> Mapping[str, Any]:
        market = clock_snapshot or self.clock.snapshot()
        if market.session in {MarketSession.CLOSED, MarketSession.POSTMARKET}:
            t1_completion = await self._complete_pending_t1(market, lease_token)
            t1_error = (
                t1_completion.get("reason")
                if t1_completion.get("reason") in {"t1_scan_read_failed", "t1_overlay_read_failed"}
                else None
            )
            next_session_at = self.clock.next_supported_session_at(market)
            details = {
                "runtime_reason": "market_closed",
                "market_session": market.session.value,
                "next_session_at": next_session_at,
                "t1_completion": t1_completion,
            }
            self._wait_status = "degraded" if t1_error else "paused"
            self._wait_details = details
            self._status(self._wait_status, error_code=t1_error, details=details)
            return {
                "status": "paused",
                "error_code": t1_error,
                "reason": "market_closed",
                "session": market.session.value,
                "scan_run_id": None,
                "next_session_at": next_session_at,
                "t1_completion": t1_completion,
                "t1_retry_after_seconds": t1_completion.get("retry_after_seconds"),
            }
        provider_name = str(getattr(self.settings, "discovery_provider", "tradingview"))
        profile = self.clock.profile_for(market.session)
        versions = self._versions()
        scan_id: str | None = None
        provider: Any = None
        try:
            try:
                scan_id = self.repository.begin_scan(
                    provider=provider_name,
                    profile=profile,
                    session=market.session,
                    scheduled_at=market.as_of,
                    config_hash=self._settings_hash(),
                    versions_hash=_stable_hash(versions),
                    versions=versions,
                )
                self._status("running", current_scan_id=scan_id)
            except Exception as exc:
                raise BreakoutStageError(
                    "database",
                    "scan_initialization_failed",
                    "failed to initialize breakout scan state",
                ) from exc
            provider = self._provider_instance()
            async def discover_and_enrich() -> tuple[Any, Any]:
                discovery_value = await provider.scan(
                    session=market.session,
                    as_of=market.as_of,
                    profile=profile,
                )
                provider_status = getattr(discovery_value, "status", None)
                if isinstance(provider_status, Enum):
                    provider_status = provider_status.value
                if str(provider_status) == "unavailable":
                    raise _ProviderUnavailableSnapshotError(discovery_value)
                if (
                    str(provider_status) == "degraded"
                    and not list(getattr(discovery_value, "candidates", ()) or ())
                ):
                    raise _ProviderDegradedEmptySnapshotError(discovery_value)
                service_value = await self._invoke_scan_service(
                    discovery_value,
                    market,
                )
                return discovery_value, service_value

            discovery, service_result = await self._run_with_lease_heartbeat(
                discover_and_enrich(),
                lease_token,
                scan_id,
            )
            if not self.repository.heartbeat_lock(
                DEFAULT_LOCK_NAME,
                self.owner_id,
                lease_token,
                self.lease_ttl_seconds,
                self.clock.now(),
            ):
                raise LeaseLostError("worker lost its lease while scanning")
            publication = self._publication_payload(discovery, service_result)
            publication["provider_health"] = self._provider_health(provider)
            publication["worker_status"] = {
                "worker_id": self.owner_id,
                "mode": self._mode,
                "status": "publishing",
                "heartbeat_at": self.clock.now(),
                "current_scan_run_id": scan_id,
            }
            self.repository.publish_scan(
                scan_id,
                publication,
                owner_id=self.owner_id,
                lease_token=lease_token,
                now=self.clock.now(),
            )
            t1_persistence = "completed"
            try:
                self.repository.persist_t1_evaluations(publication.get("events") or [])
            except Exception as exc:
                t1_persistence = "failed"
                logger.warning(
                    "Breakout scan %s was published but T1 persistence failed (%s)",
                    scan_id, type(exc).__name__,
                )
            self._last_completed_scan_id = scan_id
            self._last_completed_at = self.clock.now()
            self._wait_status = "idle"
            self._wait_details = None
            event_count = len(publication.get("events") or ())
            maintenance: Mapping[str, int] | None = None
            try:
                maintenance = self.repository.prune_retention(
                    owner_id=self.owner_id,
                    lease_token=lease_token,
                    raw_payload_hours=self.settings.raw_payload_retention_hours,
                    scan_days=self.settings.scan_retention_days,
                    batch_size=self.settings.retention_batch_size,
                    now=self.clock.now(),
                )
            except Exception:
                maintenance = None
            self._status(
                "idle",
                details={
                    "last_event_count": event_count,
                    "session": market.session.value,
                    "retention": maintenance,
                    "t1_persistence": t1_persistence,
                },
            )
            return {
                "status": "completed",
                "scan_run_id": scan_id,
                "event_count": event_count,
                "session": market.session.value,
                "t1_persistence": t1_persistence,
            }
        except LeaseLostError:
            if scan_id is not None:
                self.repository.fail_scan(
                    scan_id,
                    "lease_lost",
                    "LeaseLostError",
                    now=self.clock.now(),
                )
            try:
                self._status("lease_lost", error_code="lease_lost")
            except Exception:
                pass
            raise
        except Exception as exc:
            error_code = str(getattr(exc, "code", "scan_failed"))[:120]
            failure_domain = _failure_domain(exc)
            provider_health_unchanged = failure_domain != "provider"
            try:
                if scan_id is not None:
                    self.repository.fail_scan(
                        scan_id,
                        error_code,
                        type(exc).__name__,
                        now=self.clock.now(),
                    )
            except Exception:
                pass
            details = {
                "error_type": type(exc).__name__,
                "session": market.session.value,
                "failure_domain": failure_domain,
                "provider_health_unchanged": provider_health_unchanged,
            }
            provider_warning = getattr(exc, "provider_warning", None)
            if provider_warning:
                details["provider_warning"] = str(provider_warning)[:120]
            self._wait_status = "degraded"
            self._wait_details = details
            try:
                if not provider_health_unchanged and provider is not None:
                    health = self._provider_health(provider, error_code=error_code)
                    self.repository.record_provider_health(health, now=self.clock.now())
                self._status(
                    "degraded",
                    error_code=error_code,
                    details=details,
                )
            except Exception:
                pass
            return {
                "status": "degraded",
                "scan_run_id": scan_id,
                "error_code": error_code,
                "failure_domain": failure_domain,
                "provider_health_unchanged": provider_health_unchanged,
            }

    async def run_once(self) -> Mapping[str, Any]:
        """Run one scan; a lock loser exits without touching the Provider."""
        self._mode = "once"
        if not bool(getattr(self.settings, "enabled", False)):
            return {"status": "disabled", "scan_run_id": None}
        self.repository.initialize()
        lease_token = self.repository.acquire_lock(
            DEFAULT_LOCK_NAME,
            self.owner_id,
            self.lease_ttl_seconds,
            self.clock.now(),
        )
        if lease_token is None:
            return {"status": "locked", "scan_run_id": None}
        try:
            self.repository.abandon_running_scans(
                owner_id=self.owner_id,
                lease_token=lease_token,
                now=self.clock.now(),
            )
            return await self._run_cycle(lease_token)
        finally:
            self.repository.release_lock(
                DEFAULT_LOCK_NAME,
                self.owner_id,
                lease_token,
                self.clock.now(),
            )
            await self.aclose()

    async def _sleep(self, seconds: float) -> None:
        if seconds <= 0 or self._stop_requested:
            return
        await _maybe_await(self._sleeper(seconds))

    async def _wait_until(self, deadline: float, lease_token: int) -> None:
        heartbeat_interval = max(1.0, self.lease_ttl_seconds / 3.0)
        while not self._stop_requested:
            remaining = deadline - float(self._monotonic())
            if remaining <= 0:
                return
            await self._sleep(min(remaining, heartbeat_interval))
            if self._stop_requested:
                return
            if not self.repository.heartbeat_lock(
                DEFAULT_LOCK_NAME,
                self.owner_id,
                lease_token,
                self.lease_ttl_seconds,
                self.clock.now(),
            ):
                self._status("lease_lost", error_code="lease_lost")
                raise LeaseLostError("worker lease expired while waiting")
            self._status(self._wait_status, details=self._wait_details)

    async def run_forever(self) -> None:
        """Run on monotonic absolute deadlines until SIGTERM requests a stop."""
        self._mode = "continuous"
        if not bool(getattr(self.settings, "enabled", False)):
            return
        self.repository.initialize()
        lease_token: int | None = None
        try:
            while not self._stop_requested and lease_token is None:
                lease_token = self.repository.acquire_lock(
                    DEFAULT_LOCK_NAME,
                    self.owner_id,
                    self.lease_ttl_seconds,
                    self.clock.now(),
                )
                if lease_token is None:
                    await self._sleep(min(5.0, self.lease_ttl_seconds / 3.0))
            if lease_token is None:
                return
            self.repository.abandon_running_scans(
                owner_id=self.owner_id,
                lease_token=lease_token,
                now=self.clock.now(),
            )
            self._status("idle")
            deadline = float(self._monotonic())
            consecutive_degraded = 0
            while not self._stop_requested:
                await self._wait_until(deadline, lease_token)
                if self._stop_requested:
                    break
                market = self.clock.snapshot()
                result = await self._run_cycle(lease_token, market)
                if result["status"] == "degraded":
                    consecutive_degraded += 1
                else:
                    consecutive_degraded = 0
                interval = float(self.clock.interval_seconds(market, self.settings))
                retry_after = result.get("t1_retry_after_seconds")
                if retry_after:
                    interval = min(interval, float(retry_after))
                if consecutive_degraded:
                    degraded_base = max(
                        interval,
                        float(
                            getattr(
                                self.settings,
                                "scan_interval_closed_seconds",
                                interval,
                            )
                        ),
                    )
                    interval = degraded_base * (
                        2 ** min(max(consecutive_degraded - 1, 0), 3)
                    )
                jitter_bound = min(30.0, max(0.0, interval * 0.05))
                jitter = min(jitter_bound, max(0.0, float(self._jitter(jitter_bound))))
                deadline += interval + jitter
                now_mono = float(self._monotonic())
                while deadline <= now_mono:
                    deadline += interval
        finally:
            if lease_token is not None:
                try:
                    self._status("stopped")
                finally:
                    self.repository.release_lock(
                        DEFAULT_LOCK_NAME,
                        self.owner_id,
                        lease_token,
                        self.clock.now(),
                    )
            await self.aclose()

    async def aclose(self) -> None:
        if not self._owns_provider or self.provider is None:
            return
        close = getattr(self.provider, "aclose", None)
        if callable(close):
            await _maybe_await(close())
        self.provider = None
        self._owns_provider = False


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Breakout Radar worker")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true", help="run one scheduled scan")
    mode.add_argument("--healthcheck", action="store_true", help="check worker liveness")
    return parser


async def _async_main(args: argparse.Namespace) -> int:
    settings = get_breakout_settings()
    if args.healthcheck:
        health = check_breakout_health(settings)
        print(json.dumps(health.as_dict(), allow_nan=False, separators=(",", ":")))
        return health.exit_code

    if not settings.enabled and not args.once:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        await stop.wait()
        return 0

    repository = BreakoutRepository(settings.db_path)
    from app.services.breakouts.service import BreakoutRadarService

    worker = BreakoutWorker(
        settings,
        repository,
        scan_service=BreakoutRadarService(settings),
    )
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(signum, worker.request_stop)
        except (NotImplementedError, RuntimeError):
            pass
    if args.once:
        result = await worker.run_once()
        print(json.dumps(result, allow_nan=False, separators=(",", ":")))
    else:
        await worker.run_forever()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["BreakoutWorker", "main"]
