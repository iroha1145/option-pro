from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.health import check_breakout_health
from app.services.breakouts.repository import BreakoutRepository
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
