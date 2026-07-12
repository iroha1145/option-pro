"""Standalone Breakout Radar worker and command-line entry point."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import inspect
import json
import os
import random
import signal
import socket
import time
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from app.services.breakouts.clock import MarketClock, MarketClockSnapshot
from app.services.breakouts.config import BreakoutSettings, get_breakout_settings
from app.services.breakouts.health import check_breakout_health
from app.services.breakouts.repository import (
    DEFAULT_LOCK_NAME,
    BreakoutRepository,
    LeaseLostError,
)


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


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


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
        self._jitter = jitter or (lambda bound: random.uniform(0.0, bound))
        self._stop_requested = False
        self._owns_provider = False
        self._mode = "continuous"
        self._last_completed_scan_id: str | None = None
        self._last_completed_at: datetime | None = None

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
        result["database"] = "breakout-db-v1"
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
        previous_events = self.repository.latest_events_for_tickers(
            [getattr(candidate, "ticker", "") for candidate in candidates]
        )
        available = {
            "discovery": discovery,
            "discovery_snapshot": discovery,
            "provider_snapshot": discovery,
            "candidates": candidates,
            "previous_events": previous_events,
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
        interval = max(0.05, min(30.0, self.lease_ttl_seconds / 3.0))
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    return await task
                if not self.repository.heartbeat_lock(
                    DEFAULT_LOCK_NAME,
                    self.owner_id,
                    lease_token,
                    self.lease_ttl_seconds,
                    self.clock.now(),
                ):
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    raise LeaseLostError("worker lost its lease while scanning")
                self._status("running", current_scan_id=scan_id)
        finally:
            if not task.done():
                task.cancel()

    async def _run_cycle(
        self,
        lease_token: int,
        clock_snapshot: MarketClockSnapshot | None = None,
    ) -> Mapping[str, Any]:
        market = clock_snapshot or self.clock.snapshot()
        provider_name = str(getattr(self.settings, "discovery_provider", "tradingview"))
        profile = self.clock.profile_for(market.session)
        versions = self._versions()
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
        provider = self._provider_instance()
        try:
            async def discover_and_enrich() -> tuple[Any, Any]:
                discovery_value = await provider.scan(
                    session=market.session,
                    as_of=market.as_of,
                    profile=profile,
                )
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
            self._last_completed_scan_id = scan_id
            self._last_completed_at = self.clock.now()
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
                },
            )
            return {
                "status": "completed",
                "scan_run_id": scan_id,
                "event_count": event_count,
                "session": market.session.value,
            }
        except LeaseLostError:
            self.repository.fail_scan(scan_id, "lease_lost", "LeaseLostError", now=self.clock.now())
            try:
                self._status("lease_lost", error_code="lease_lost")
            except Exception:
                pass
            raise
        except Exception as exc:
            error_code = str(getattr(exc, "code", "scan_failed"))[:120]
            self.repository.fail_scan(scan_id, error_code, type(exc).__name__, now=self.clock.now())
            health = self._provider_health(provider, error_code=error_code)
            try:
                self.repository.record_provider_health(health, now=self.clock.now())
                self._status(
                    "degraded",
                    error_code=error_code,
                    details={"error_type": type(exc).__name__},
                )
            except Exception:
                pass
            return {
                "status": "degraded",
                "scan_run_id": scan_id,
                "error_code": error_code,
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
            self._status("idle")

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
                if consecutive_degraded:
                    interval = min(
                        interval * (2 ** min(consecutive_degraded, 3)),
                        float(getattr(self.settings, "scan_interval_closed_seconds")),
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
