from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.health import check_breakout_health
from app.services.breakouts.repository import DEFAULT_LOCK_NAME, BreakoutRepository
from app.services.breakouts.worker import BreakoutWorker


NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


class Settings:
    def __init__(self, db_path: Path, enabled: bool = True):
        self.enabled = enabled
        self.db_path = db_path
        self.discovery_provider = "fixture"
        self.scan_interval_premarket_seconds = 600
        self.scan_interval_regular_seconds = 300
        self.scan_interval_closed_seconds = 1800
        self.api_schema_version = "breakout-api-v1"
        self.provider_schema_version = "fixture-v1"
        self.feature_version = "breakout-features-v1"
        self.detector_version = "breakout-detector-v1"
        self.scoring_version = "breakout-score-v1"
        self.range_persistence_version = "range-persistence-v1"

    def model_dump(self, mode="python"):
        return {
            "enabled": self.enabled,
            "db_path": str(self.db_path),
            "discovery_provider": self.discovery_provider,
        }


@dataclass
class Discovery:
    provider: str
    status: str
    as_of: datetime
    session: str
    schema_version: str = "fixture-v1"
    candidates: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


class Provider:
    def __init__(self, *, fail: bool = False):
        self.calls = 0
        self.fail = fail

    @property
    def health(self):
        return {
            "provider": "fixture",
            "status": "active" if not self.fail else "unavailable",
            "consecutive_failures": int(self.fail),
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile):
        self.calls += 1
        if self.fail:
            error = RuntimeError("body must not be persisted")
            error.code = "provider_fixture_failed"
            raise error
        return Discovery(
            provider="fixture",
            status="active",
            as_of=as_of,
            session=session.value,
        )


class UnavailableProvider(Provider):
    @property
    def health(self):
        return {
            "provider": "fixture",
            "status": "unavailable",
            "consecutive_failures": 1,
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile):
        self.calls += 1
        return Discovery(
            provider="fixture",
            status="unavailable",
            as_of=as_of,
            session=session.value,
            warnings=["provider_timeout"],
        )


class DegradedEmptyProvider(Provider):
    @property
    def health(self):
        return {
            "provider": "fixture",
            "status": "degraded",
            "consecutive_failures": 0,
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile):
        self.calls += 1
        return Discovery(
            provider="fixture",
            status="degraded",
            as_of=as_of,
            session=session.value,
            warnings=["provider_column_count_changed"],
        )


class ScanService:
    def __init__(self):
        self.calls = 0

    async def build_snapshot(self, discovery, as_of):
        self.calls += 1
        return {
            "events": [
                {
                    "event_id": "worker-event-1",
                    "trading_date": as_of.date(),
                    "ticker": "AAPL",
                    "setup_type": "DAILY_BASE_BREAKOUT",
                    "lifecycle_state": "TRIGGERED",
                    "previous_state": "WATCHING",
                    "transition_reason": "pivot_crossed",
                    "event_at": as_of,
                    "first_seen_at": as_of,
                    "last_seen_at": as_of,
                    "pivot_id": "pivot-worker-aapl",
                    "source_snapshot_id": "fixture",
                    "scores": {
                        "alert_priority_score": 88.0,
                        "data_confidence_score": 91.0,
                    },
                }
            ]
        }


class CarryoverRecordingService:
    def __init__(self):
        self.calls = 0
        self.discovery_candidates = None
        self.carryover_events = None
        self.previous_events = None
        self.expired_due_event_ids = None
        self.carryover_has_more = None

    async def build_snapshot(
        self,
        discovery,
        as_of,
        carryover_events,
        previous_events,
        expired_due_event_ids,
        carryover_has_more,
    ):
        self.calls += 1
        self.discovery_candidates = list(discovery.candidates)
        self.carryover_events = list(carryover_events)
        self.previous_events = previous_events
        self.expired_due_event_ids = expired_due_event_ids
        self.carryover_has_more = carryover_has_more
        return {"events": []}


def test_scan_lease_heartbeat_survives_a_blocked_scan_event_loop(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    worker = BreakoutWorker(
        settings,
        repository,
        clock=MarketClock(),
        owner_id="blocked-scan-worker",
        lease_ttl_seconds=0.2,
    )
    lease_token = repository.acquire_lock(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )
    assert lease_token is not None
    heartbeat_threads: list[str] = []
    original_heartbeat = repository.heartbeat_lock

    def observed_heartbeat(*args, **kwargs):
        heartbeat_threads.append(threading.current_thread().name)
        return original_heartbeat(*args, **kwargs)

    repository.heartbeat_lock = observed_heartbeat  # type: ignore[method-assign]

    async def blocked_operation():
        threading.Event().wait(0.55)
        return "completed"

    async def scenario():
        return await worker._run_with_lease_heartbeat(
            blocked_operation(),
            int(lease_token),
            "scan-blocked-loop",
        )

    assert asyncio.run(scenario()) == "completed"
    assert len(heartbeat_threads) >= 2
    assert all(name.startswith("breakout-lease-heartbeat") for name in heartbeat_threads)
    assert original_heartbeat(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        int(lease_token),
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )


def test_scan_lease_heartbeat_retries_a_transient_database_error(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    worker = BreakoutWorker(
        settings,
        repository,
        clock=MarketClock(),
        owner_id="transient-renew-worker",
        lease_ttl_seconds=0.2,
    )
    lease_token = repository.acquire_lock(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )
    assert lease_token is not None
    original_heartbeat = repository.heartbeat_lock
    attempts = 0

    def transient_heartbeat(*args, **kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise sqlite3.OperationalError("simulated busy database")
        return original_heartbeat(*args, **kwargs)

    repository.heartbeat_lock = transient_heartbeat  # type: ignore[method-assign]

    async def blocked_operation():
        threading.Event().wait(0.3)
        return "completed"

    result = asyncio.run(
        worker._run_with_lease_heartbeat(
            blocked_operation(),
            int(lease_token),
            "scan-transient-renewal",
        )
    )
    assert result == "completed"
    assert attempts >= 2


def test_scan_heartbeat_start_failure_cancels_the_operation(tmp_path, monkeypatch):
    settings = Settings(tmp_path / "breakouts.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    worker = BreakoutWorker(
        settings,
        repository,
        clock=MarketClock(),
        owner_id="heartbeat-start-failure-worker",
        lease_ttl_seconds=0.2,
    )
    lease_token = repository.acquire_lock(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )
    assert lease_token is not None
    original_start = threading.Thread.start

    def fail_heartbeat_start(thread):
        if thread.name == "breakout-lease-heartbeat":
            raise RuntimeError("simulated heartbeat thread failure")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", fail_heartbeat_start)

    async def operation():
        await asyncio.Future()

    async def scenario():
        with pytest.raises(RuntimeError, match="simulated heartbeat thread failure"):
            await worker._run_with_lease_heartbeat(
                operation(),
                int(lease_token),
                "scan-heartbeat-start-failure",
            )
        assert not any(
            task is not asyncio.current_task() and not task.done()
            for task in asyncio.all_tasks()
        )

    asyncio.run(scenario())


def test_scan_heartbeat_waits_for_operation_cleanup_on_external_cancel(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    worker = BreakoutWorker(
        settings,
        repository,
        clock=MarketClock(),
        owner_id="external-cancel-worker",
        lease_ttl_seconds=0.2,
    )
    lease_token = repository.acquire_lock(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )
    assert lease_token is not None

    async def scenario():
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        running = asyncio.create_task(
            worker._run_with_lease_heartbeat(
                operation(),
                int(lease_token),
                "scan-external-cancel",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert cleaned.is_set()

    asyncio.run(scenario())


def test_scan_cleanup_survives_repeated_external_cancellation(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repository = BreakoutRepository(settings.db_path)
    repository.initialize()
    worker = BreakoutWorker(
        settings,
        repository,
        clock=MarketClock(),
        owner_id="repeated-cancel-worker",
        lease_ttl_seconds=0.2,
    )
    lease_token = repository.acquire_lock(
        DEFAULT_LOCK_NAME,
        worker.owner_id,
        worker.lease_ttl_seconds,
        worker.clock.now(),
    )
    assert lease_token is not None
    renewal_started = threading.Event()
    renewal_release = threading.Event()
    original_heartbeat = repository.heartbeat_lock

    def blocked_heartbeat(*args, **kwargs):
        renewal_started.set()
        renewal_release.wait(timeout=1)
        return original_heartbeat(*args, **kwargs)

    repository.heartbeat_lock = blocked_heartbeat  # type: ignore[method-assign]

    async def scenario():
        started = asyncio.Event()
        cleaned = asyncio.Event()

        async def operation():
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()

        running = asyncio.create_task(
            worker._run_with_lease_heartbeat(
                operation(),
                int(lease_token),
                "scan-repeated-cancel",
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        for _ in range(100):
            if renewal_started.is_set():
                break
            await asyncio.sleep(0.01)
        assert renewal_started.is_set()

        running.cancel()
        await asyncio.sleep(0)
        running.cancel()
        await asyncio.sleep(0)
        assert not running.done()
        renewal_release.set()
        with pytest.raises(asyncio.CancelledError):
            await running
        assert cleaned.is_set()
        assert not any(
            thread.name == "breakout-lease-heartbeat" and thread.is_alive()
            for thread in threading.enumerate()
        )

    asyncio.run(scenario())


def test_once_disabled_does_not_create_database_or_call_provider(tmp_path):
    settings = Settings(tmp_path / "breakouts.db", enabled=False)
    provider = Provider()
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=provider,
        clock=MarketClock(now=lambda: NOW),
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "disabled"
    assert provider.calls == 0
    assert not settings.db_path.exists()


def test_once_injects_provider_and_service_then_survives_restart(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    provider = Provider()
    service = ScanService()
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=provider,
        scan_service=service,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-one",
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "completed"
    assert result["event_count"] == 1
    assert provider.calls == service.calls == 1

    restarted = BreakoutRepository(settings.db_path, read_only=True)
    latest = restarted.latest_completed_scan()
    assert latest is not None
    assert latest["scan_run_id"] == result["scan_run_id"]
    assert latest["events"][0]["ticker"] == "AAPL"
    health = check_breakout_health(settings, restarted, now=NOW)
    assert health.healthy is True
    assert health.status == "active"


def test_active_empty_snapshot_is_a_valid_completed_scan(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=Provider(),
        scan_service=None,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-empty",
    )

    result = asyncio.run(worker.run_once())

    assert result["status"] == "completed"
    assert result["event_count"] == 0
    latest = BreakoutRepository(
        settings.db_path,
        read_only=True,
    ).latest_completed_scan()
    assert latest is not None
    assert latest["events"] == []


def test_empty_discovery_still_passes_carryover_to_scan_service(tmp_path):
    settings = Settings(tmp_path / "carryover.db")
    seed_at = NOW - timedelta(minutes=5)
    seed_worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=Provider(),
        scan_service=ScanService(),
        clock=MarketClock(now=lambda: seed_at),
        owner_id="worker-seed",
    )
    seeded = asyncio.run(seed_worker.run_once())
    assert seeded["status"] == "completed"

    recorder = CarryoverRecordingService()
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=Provider(),
        scan_service=recorder,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-carryover",
    )
    result = asyncio.run(worker.run_once())

    assert result["status"] == "completed"
    assert recorder.calls == 1
    assert recorder.discovery_candidates == []
    assert [event["event_id"] for event in recorder.carryover_events] == [
        "worker-event-1"
    ]
    assert [event["event_id"] for event in recorder.previous_events["AAPL"]] == [
        "worker-event-1"
    ]
    assert recorder.expired_due_event_ids == frozenset()
    assert recorder.carryover_has_more is False


def test_provider_failure_marks_scan_failed_but_health_is_degraded_not_fatal(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    provider = Provider(fail=True)
    worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=provider,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-failure",
    )
    result = asyncio.run(worker.run_once())
    assert result["status"] == "degraded"
    assert result["error_code"] == "provider_fixture_failed"

    reader = BreakoutRepository(settings.db_path, read_only=True)
    assert reader.latest_completed_scan() is None
    status = reader.status()
    assert status["worker"]["status"] == "degraded"
    assert status["provider_health"][0]["status"] == "unavailable"
    health = check_breakout_health(settings, reader, now=NOW)
    assert health.healthy is True
    assert health.status == "degraded"


def test_unavailable_snapshot_does_not_replace_previous_completed_scan(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    service = ScanService()
    first_worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=Provider(),
        scan_service=service,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-success",
    )
    first = asyncio.run(first_worker.run_once())
    assert first["status"] == "completed"
    assert service.calls == 1

    unavailable_worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=UnavailableProvider(),
        scan_service=service,
        clock=MarketClock(now=lambda: NOW + timedelta(minutes=5)),
        owner_id="worker-unavailable",
    )
    degraded = asyncio.run(unavailable_worker.run_once())

    assert degraded["status"] == "degraded"
    assert degraded["error_code"] == "provider_unavailable"
    assert service.calls == 1
    reader = BreakoutRepository(settings.db_path, read_only=True)
    latest = reader.latest_completed_scan()
    assert latest is not None
    assert latest["scan_run_id"] == first["scan_run_id"]
    assert latest["events"][0]["ticker"] == "AAPL"
    status = reader.status()
    assert status["worker"]["status"] == "degraded"
    assert status["worker"]["details"]["provider_warning"] == "provider_timeout"
    assert status["provider_health"][0]["status"] == "unavailable"
    connection = reader.open_read_connection()
    try:
        scan_states = [
            row[0]
            for row in connection.execute(
                "SELECT status FROM breakout_scan_runs ORDER BY scheduled_at"
            ).fetchall()
        ]
    finally:
        connection.close()
    assert scan_states == ["completed", "failed"]


def test_degraded_empty_snapshot_does_not_replace_previous_completed_scan(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    service = ScanService()
    first_worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=Provider(),
        scan_service=service,
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-success",
    )
    first = asyncio.run(first_worker.run_once())
    assert first["status"] == "completed"

    degraded_worker = BreakoutWorker(
        settings,
        BreakoutRepository(settings.db_path),
        provider=DegradedEmptyProvider(),
        scan_service=service,
        clock=MarketClock(now=lambda: NOW + timedelta(minutes=5)),
        owner_id="worker-degraded-empty",
    )
    degraded = asyncio.run(degraded_worker.run_once())

    assert degraded["status"] == "degraded"
    assert degraded["error_code"] == "provider_degraded_empty"
    assert service.calls == 1
    latest = BreakoutRepository(
        settings.db_path,
        read_only=True,
    ).latest_completed_scan()
    assert latest is not None
    assert latest["scan_run_id"] == first["scan_run_id"]
    assert latest["events"][0]["ticker"] == "AAPL"


def test_restart_abandons_legacy_running_scan_without_touching_completed(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repo = BreakoutRepository(settings.db_path)
    repo.initialize()
    orphan = repo.begin_scan(
        "old-provider",
        "regular",
        NOW - timedelta(minutes=10),
        config_hash="old",
        versions_hash="old",
    )
    worker = BreakoutWorker(
        settings,
        repo,
        provider=Provider(),
        scan_service=ScanService(),
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-restart",
    )
    assert asyncio.run(worker.run_once())["status"] == "completed"
    connection = repo.open_read_connection()
    try:
        assert connection.execute(
            "SELECT status FROM breakout_scan_runs WHERE scan_run_id=?", (orphan,)
        ).fetchone()[0] == "abandoned"
    finally:
        connection.close()


def test_slow_scan_renews_lease_and_prevents_takeover(tmp_path):
    settings = Settings(tmp_path / "slow.db")
    current_time = NOW
    scan_started: asyncio.Event | None = None
    release_scan: asyncio.Event | None = None
    renewed: asyncio.Event | None = None

    def controlled_market_time() -> datetime:
        return current_time

    market_clock = MarketClock(now=controlled_market_time)

    class SlowProvider(Provider):
        async def scan(self, *, session, as_of, profile):
            self.calls += 1
            assert scan_started is not None
            assert release_scan is not None
            scan_started.set()
            await release_scan.wait()
            return Discovery(
                provider="fixture",
                status="active",
                as_of=as_of,
                session=session.value,
            )

    repository = BreakoutRepository(settings.db_path)
    original_heartbeat_lock = repository.heartbeat_lock

    def observed_heartbeat_lock(*args, **kwargs):
        did_renew = original_heartbeat_lock(*args, **kwargs)
        if did_renew and current_time >= NOW + timedelta(seconds=0.4):
            assert renewed is not None
            renewed.set()
        return did_renew

    repository.heartbeat_lock = observed_heartbeat_lock
    worker = BreakoutWorker(
        settings,
        repository,
        provider=SlowProvider(),
        scan_service=ScanService(),
        clock=market_clock,
        owner_id="slow-worker",
        lease_ttl_seconds=0.5,
    )

    async def scenario():
        nonlocal current_time, scan_started, release_scan, renewed
        scan_started = asyncio.Event()
        release_scan = asyncio.Event()
        renewed = asyncio.Event()
        running = asyncio.create_task(worker.run_once())
        await asyncio.wait_for(scan_started.wait(), timeout=2.0)
        current_time = NOW + timedelta(seconds=0.4)
        await asyncio.wait_for(renewed.wait(), timeout=2.0)

        # The original lease expired at NOW + 0.5 seconds. The heartbeat at
        # NOW + 0.4 seconds extended it through NOW + 0.9 seconds.
        current_time = NOW + timedelta(seconds=0.6)
        takeover = repository.acquire_lock(
            "breakout-worker",
            "contender",
            1.0,
            current_time,
        )
        release_scan.set()
        return takeover, await running

    takeover, result = asyncio.run(scenario())
    assert takeover is None
    assert result["status"] == "completed"
