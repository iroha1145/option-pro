from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.errors import BreakoutStageError
from app.services.breakouts.repository import BreakoutRepository
from app.services.breakouts.worker import BreakoutWorker


@dataclass
class Discovery:
    provider: str
    status: str
    as_of: datetime
    session: str
    schema_version: str = "fixture-v1"
    candidates: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    cache_key: str = "fixture-cache"


class Settings:
    def __init__(self, path: Path) -> None:
        self.enabled = True
        self.db_path = path
        self.discovery_provider = "fixture"
        self.scan_interval_premarket_seconds = 600
        self.scan_interval_regular_seconds = 300
        self.scan_interval_closed_seconds = 1800
        self.worker_health_stale_seconds = 120
        self.raw_payload_retention_hours = 24
        self.scan_retention_days = 30
        self.retention_batch_size = 500
        self.provider_result_limit = 10
        self.intraday_enrich_limit = 30
        self.expired_due_limit = 40
        self.event_ttl_seconds = 86_400
        self.api_schema_version = "breakout-api-v1"
        self.provider_schema_version = "fixture-v1"
        self.feature_version = "breakout-features-v1"
        self.detector_version = "breakout-detector-v1"
        self.scoring_version = "breakout-score-v1"
        self.range_persistence_version = "range-persistence-v1"

    def model_dump(self, mode: str = "python") -> dict:
        return {"enabled": self.enabled, "db_path": str(self.db_path)}


class RecordingProvider:
    def __init__(self) -> None:
        self.calls = 0

    @property
    def health(self) -> dict:
        return {
            "provider": "fixture",
            "status": "active",
            "consecutive_failures": 0,
            "stale_snapshot_available": False,
        }

    async def scan(self, *, session, as_of, profile) -> Discovery:
        self.calls += 1
        return Discovery(
            provider="fixture",
            status="active",
            as_of=as_of,
            session=session.value,
        )


class FailingService:
    async def build_snapshot(self, **_kwargs):
        raise ValueError("local calculation failed")


class DomainFailingService:
    def __init__(self, failure_domain: str) -> None:
        self.failure_domain = failure_domain

    async def build_snapshot(self, **_kwargs):
        raise BreakoutStageError(
            self.failure_domain,
            f"{self.failure_domain}_stage_failed",
        )


class BeginScanFailingRepository(BreakoutRepository):
    def begin_scan(self, *args, **kwargs):
        raise sqlite3.OperationalError("begin scan failed")


class InitialStatusFailingRepository(BreakoutRepository):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._failed_running_status = False

    def update_worker_status(self, worker_id, mode, status, **kwargs):
        if status == "running" and not self._failed_running_status:
            self._failed_running_status = True
            raise sqlite3.OperationalError("initial worker status failed")
        return super().update_worker_status(worker_id, mode, status, **kwargs)


@pytest.mark.parametrize(
    "observed_at,expected_session",
    [
        (datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc), "closed"),
        (datetime(2026, 7, 13, 21, 0, tzinfo=timezone.utc), "postmarket"),
    ],
)
def test_closed_and_postmarket_pause_without_provider_or_failed_scan(
    tmp_path: Path,
    observed_at: datetime,
    expected_session: str,
) -> None:
    settings = Settings(tmp_path / f"{expected_session}.db")
    provider = RecordingProvider()
    repository = BreakoutRepository(settings.db_path)
    result = asyncio.run(
        BreakoutWorker(
            settings,
            repository,
            provider=provider,
            clock=MarketClock(now=lambda: observed_at),
            owner_id=f"worker-{expected_session}",
        ).run_once()
    )

    assert result["status"] == "paused"
    assert result["reason"] == "market_closed"
    assert result["session"] == expected_session
    assert result["scan_run_id"] is None
    assert result["next_session_at"].endswith("Z")
    assert provider.calls == 0

    status = BreakoutRepository(settings.db_path, read_only=True).status()
    assert status["worker"]["status"] == "paused"
    assert status["worker"]["details"]["runtime_reason"] == "market_closed"
    assert status["provider_health"] == []
    with repository.open_read_connection() as connection:
        assert connection.execute("SELECT COUNT(*) FROM breakout_scan_runs").fetchone()[0] == 0


def test_premarket_recovers_after_closed_pause_without_manual_reset(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "resume.db")
    provider = RecordingProvider()
    repository = BreakoutRepository(settings.db_path)

    paused = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        clock=MarketClock(
            now=lambda: datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)
        ),
        owner_id="worker-paused",
    )
    assert asyncio.run(paused.run_once())["status"] == "paused"

    resumed = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        clock=MarketClock(
            now=lambda: datetime(2026, 7, 13, 12, 0, tzinfo=timezone.utc)
        ),
        owner_id="worker-resumed",
    )
    result = asyncio.run(resumed.run_once())

    assert result["status"] == "completed"
    assert provider.calls == 1


def test_local_processing_error_does_not_modify_provider_health(tmp_path: Path) -> None:
    settings = Settings(tmp_path / "failure-domain.db")
    provider = RecordingProvider()
    repository = BreakoutRepository(settings.db_path)

    seeded = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        clock=MarketClock(
            now=lambda: datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
        ),
        owner_id="worker-seed",
    )
    assert asyncio.run(seeded.run_once())["status"] == "completed"
    before = BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ]

    failed = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        scan_service=FailingService(),
        clock=MarketClock(
            now=lambda: datetime(2026, 7, 13, 14, 5, tzinfo=timezone.utc)
        ),
        owner_id="worker-local-error",
    )
    result = asyncio.run(failed.run_once())

    assert result["status"] == "degraded"
    assert result["failure_domain"] == "local_processing"
    assert result["provider_health_unchanged"] is True
    after = BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ]
    assert after == before


@pytest.mark.parametrize(
    "failure_domain",
    ["price_data", "strength", "market_shape", "persistence"],
)
def test_stage_failure_domain_does_not_modify_provider_health(
    tmp_path: Path,
    failure_domain: str,
) -> None:
    settings = Settings(tmp_path / f"{failure_domain}.db")
    provider = RecordingProvider()
    repository = BreakoutRepository(settings.db_path)
    observed_at = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)

    seeded = BreakoutWorker(
        settings,
        repository,
        provider=provider,
        clock=MarketClock(now=lambda: observed_at),
        owner_id=f"worker-{failure_domain}-seed",
    )
    assert asyncio.run(seeded.run_once())["status"] == "completed"
    before = BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ]

    result = asyncio.run(
        BreakoutWorker(
            settings,
            repository,
            provider=provider,
            scan_service=DomainFailingService(failure_domain),
            clock=MarketClock(now=lambda: observed_at.replace(minute=5)),
            owner_id=f"worker-{failure_domain}-failed",
        ).run_once()
    )

    assert result == {
        "status": "degraded",
        "scan_run_id": result["scan_run_id"],
        "error_code": f"{failure_domain}_stage_failed",
        "failure_domain": failure_domain,
        "provider_health_unchanged": True,
    }
    assert BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ] == before


@pytest.mark.parametrize(
    "repository_type",
    [BeginScanFailingRepository, InitialStatusFailingRepository],
)
def test_scan_initialization_database_failure_is_structured_and_provider_safe(
    tmp_path: Path,
    repository_type,
) -> None:
    settings = Settings(tmp_path / f"{repository_type.__name__}.db")
    provider = RecordingProvider()
    healthy_repository = BreakoutRepository(settings.db_path)
    observed_at = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)
    assert asyncio.run(
        BreakoutWorker(
            settings,
            healthy_repository,
            provider=provider,
            clock=MarketClock(now=lambda: observed_at),
            owner_id="worker-database-seed",
        ).run_once()
    )["status"] == "completed"
    before = BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ]
    provider_calls_before = provider.calls

    result = asyncio.run(
        BreakoutWorker(
            settings,
            repository_type(settings.db_path),
            provider=provider,
            clock=MarketClock(now=lambda: observed_at.replace(minute=5)),
            owner_id=f"worker-{repository_type.__name__}",
        ).run_once()
    )

    assert result["status"] == "degraded"
    assert result["error_code"] == "scan_initialization_failed"
    assert result["failure_domain"] == "database"
    assert result["provider_health_unchanged"] is True
    assert provider.calls == provider_calls_before
    assert BreakoutRepository(settings.db_path, read_only=True).status()[
        "provider_health"
    ] == before
