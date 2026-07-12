from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.services.breakouts.clock import MarketClock
from app.services.breakouts.repository import (
    DEFAULT_LOCK_NAME,
    BreakoutRepository,
    LeaseLostError,
)
from app.services.breakouts.worker import BreakoutWorker
from test_breakout_worker import Provider, ScanService, Settings


NOW = datetime(2026, 7, 13, 14, 0, tzinfo=timezone.utc)


def _payload(at):
    return {
        "provider_snapshot": {
            "provider": "fixture",
            "status": "active",
            "as_of": at,
            "session": "regular",
            "schema_version": "fixture-v1",
            "candidates": [],
        },
        "events": [],
    }


def test_second_worker_loses_lock_before_provider_access(tmp_path):
    settings = Settings(tmp_path / "breakouts.db")
    repo = BreakoutRepository(settings.db_path)
    repo.initialize()
    first_token = repo.acquire_lock(DEFAULT_LOCK_NAME, "worker-one", 60, NOW)
    assert first_token == 1

    provider = Provider()
    second = BreakoutWorker(
        settings,
        repo,
        provider=provider,
        scan_service=ScanService(),
        clock=MarketClock(now=lambda: NOW),
        owner_id="worker-two",
    )
    result = asyncio.run(second.run_once())
    assert result["status"] == "locked"
    assert provider.calls == 0


def test_expired_lock_is_taken_over_with_new_fencing_token(tmp_path):
    repo = BreakoutRepository(tmp_path / "breakouts.db")
    repo.initialize()
    token_one = repo.acquire_lock(DEFAULT_LOCK_NAME, "worker-one", 10, NOW)
    assert token_one == 1
    assert repo.acquire_lock(DEFAULT_LOCK_NAME, "worker-two", 10, NOW) is None

    after_expiry = NOW + timedelta(seconds=11)
    token_two = repo.acquire_lock(
        DEFAULT_LOCK_NAME,
        "worker-two",
        10,
        after_expiry,
    )
    assert token_two == 2
    assert repo.heartbeat_lock(
        DEFAULT_LOCK_NAME,
        "worker-one",
        token_one,
        10,
        after_expiry,
    ) is False

    scan = repo.begin_scan(
        "fixture",
        "regular",
        after_expiry,
        config_hash="config",
        versions_hash="versions",
    )
    with pytest.raises(LeaseLostError):
        repo.publish_scan(
            scan,
            _payload(after_expiry),
            owner_id="worker-one",
            lease_token=token_one,
            now=after_expiry,
        )
    repo.publish_scan(
        scan,
        _payload(after_expiry),
        owner_id="worker-two",
        lease_token=token_two,
        now=after_expiry,
    )
    assert repo.latest_completed_scan()["scan_run_id"] == scan


def test_heartbeat_does_not_revive_expired_token_and_release_increments_token(tmp_path):
    repo = BreakoutRepository(tmp_path / "breakouts.db")
    repo.initialize()
    token = repo.acquire_lock(DEFAULT_LOCK_NAME, "worker-one", 10, NOW)
    assert repo.heartbeat_lock(
        DEFAULT_LOCK_NAME,
        "worker-one",
        token,
        10,
        NOW + timedelta(seconds=5),
    ) is True
    assert repo.heartbeat_lock(
        DEFAULT_LOCK_NAME,
        "worker-one",
        token,
        10,
        NOW + timedelta(seconds=16),
    ) is False

    takeover = repo.acquire_lock(
        DEFAULT_LOCK_NAME,
        "worker-two",
        10,
        NOW + timedelta(seconds=16),
    )
    assert takeover == token + 1
    assert repo.release_lock(
        DEFAULT_LOCK_NAME,
        "worker-two",
        takeover,
        NOW + timedelta(seconds=17),
    ) is True
    reacquired = repo.acquire_lock(
        DEFAULT_LOCK_NAME,
        "worker-three",
        10,
        NOW + timedelta(seconds=17),
    )
    assert reacquired == takeover + 1
