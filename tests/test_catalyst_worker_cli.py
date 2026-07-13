from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from datetime import datetime, timezone

from app.services.catalysts.config import CatalystSettings
from app.services.catalysts.repository import CatalystRepository
from app.services.catalysts import worker


READ_SECRET = "read-secret-0123456789abcdef-0001"


def disabled_settings(path) -> CatalystSettings:
    return CatalystSettings(
        _env_file=None,
        MACROLENS_ENABLED=False,
        MACROLENS_CACHE_DB_PATH=path,
    )


def enabled_settings(path) -> CatalystSettings:
    return CatalystSettings(
        _env_file=None,
        MACROLENS_ENABLED=True,
        MACROLENS_BASE_URL="http://localhost:9876",
        MACROLENS_ALLOW_LOCAL_HTTP=True,
        MACROLENS_READ_KEY_ID="read-key",
        MACROLENS_READ_SECRET=READ_SECRET,
        MACROLENS_SCHEMA_SHA256="",
        MACROLENS_CACHE_DB_PATH=path,
    )


def contract_fixture(path) -> str:
    raw = (
        json.dumps(
            {
                "contract": "fixture",
                "models": {},
                "schema_version": "macrolens-option-pro-v2",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
    path.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def test_disabled_health_is_healthy_without_database_or_remote(tmp_path) -> None:
    path = tmp_path / "must-not-exist.db"
    payload = worker.health_payload(disabled_settings(path))
    assert payload == {
        "healthy": True,
        "status": "disabled",
        "enabled": False,
        "schema_version": "macrolens-option-pro-v2",
        "contract": {"status": "not_checked", "valid": None},
        "database": {"status": "not_checked"},
        "remote_checked": False,
    }
    assert not path.exists()


def test_disabled_continuous_worker_stays_alive_until_stop() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(worker._wait_disabled(stop))
        await asyncio.sleep(0)
        assert not task.done()
        stop.set()
        await task

    asyncio.run(scenario())


def test_once_disabled_prints_structured_result_without_connecting(monkeypatch, capsys, tmp_path) -> None:
    settings = disabled_settings(tmp_path / "must-not-exist.db")
    monkeypatch.setattr(worker, "get_catalyst_settings", lambda: settings)
    code = asyncio.run(
        worker._async_main(argparse.Namespace(once=True, healthcheck=False))
    )
    payload = json.loads(capsys.readouterr().out)
    assert code == 0
    assert payload == {"status": "disabled", "enabled": False, "processed": []}


def test_health_validates_contract_database_lock_and_heartbeat_but_not_remote(tmp_path) -> None:
    contract_path = tmp_path / "contract.json"
    digest = contract_fixture(contract_path)
    settings = enabled_settings(tmp_path / "catalysts.db")
    repository = CatalystRepository(settings.cache_db_path)
    repository.initialize()
    token = repository.acquire_worker_lock(
        "catalyst-sync-worker", "worker-health", lease_seconds=60
    )
    assert token
    repository.heartbeat("worker-health", "idle", {"remote": "degraded"})
    repository.record_stream_failure("feed", "network_error")

    payload = worker.health_payload(
        settings,
        repository=repository,
        contract_path=contract_path,
        pinned_sha256=digest,
    )
    assert payload["healthy"] is True
    assert payload["status"] == "ok"
    assert payload["database"]["lock_live"] is True
    assert payload["remote_checked"] is False
    serialized = json.dumps(payload)
    assert "localhost:9876" not in serialized
    assert "read-secret-value" not in serialized


def test_health_fails_locally_for_missing_or_mismatched_contract(tmp_path) -> None:
    settings = enabled_settings(tmp_path / "catalysts.db")
    repository = CatalystRepository(settings.cache_db_path)
    repository.initialize()
    repository.acquire_worker_lock(
        "catalyst-sync-worker", "worker-health", lease_seconds=60
    )
    repository.heartbeat("worker-health", "idle", {})
    payload = worker.health_payload(
        settings,
        repository=repository,
        contract_path=tmp_path / "missing.json",
        pinned_sha256="a" * 64,
    )
    assert payload["healthy"] is False
    assert payload["contract"]["status"] == "missing"
