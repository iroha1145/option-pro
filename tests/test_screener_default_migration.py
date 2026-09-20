from __future__ import annotations

import json
from pathlib import Path

from app.personal_config import load_personal_config
from app.services.algorithm_modes import A0_ALGORITHM, EOD_LIMITED_V1, PRODUCTION_ALGORITHM
from app.services.runtime_settings import (
    RuntimeAlgorithmSettingsPatch,
    RuntimeSettingsPatch,
    RuntimeSettingsStore,
    get_effective_runtime_settings,
    runtime_settings_from_personal_config,
)
from app.services.screener_default_migration import (
    MIGRATION_ID,
    STATUS_ADMIN_LOCKED,
    STATUS_ALREADY_NEW,
    STATUS_APPLIED,
    STATUS_DISABLED,
    STATUS_ROLLED_BACK,
    apply_screener_default_migration,
    rollback_screener_default_migration,
)
from tests.test_runtime_settings import SteppingClock


def _store(path: Path) -> RuntimeSettingsStore:
    return RuntimeSettingsStore(
        path,
        defaults=runtime_settings_from_personal_config(load_personal_config()),
        clock=SteppingClock(),
    )


def _write_production_document(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "version": 1,
                "updated_at": "2026-07-16T00:00:00Z",
                "settings": {
                    "ai": {
                        "daily_max_jobs": 0,
                        "daily_budget_usd": 0.0,
                        "daily_token_limit": 10_000_000,
                        "manual_analysis_enabled": False,
                        "manual_analysis_cooldown_seconds": 30,
                    },
                    "catalyst": {
                        "sync_seconds": 120,
                        "focus_seconds": 1800,
                        "manual_refresh_cooldown_seconds": 30,
                        "scheduled_analysis_enabled": False,
                        "scheduled_times_et": ["08:00", "12:00", "16:00"],
                    },
                    "earnings": {
                        "scheduled_analysis_enabled": False,
                        "lookahead_days": 5,
                    },
                    "algorithms": {
                        "screener_ranking_algorithm": "production",
                        "radar_sort_algorithm": "production",
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_unpersisted_store_records_completion_without_persisting_settings(tmp_path: Path) -> None:
    store = _store(tmp_path / "runtime-settings.json")
    record = apply_screener_default_migration(store)
    assert record["status"] == STATUS_ALREADY_NEW
    assert store.read().settings.algorithms.screener_ranking_algorithm == EOD_LIMITED_V1
    assert not store.path.exists()


def test_first_admin_production_choice_survives_new_install(tmp_path: Path) -> None:
    store = _store(tmp_path / "runtime-settings.json")
    assert get_effective_runtime_settings(store).algorithms.screener_ranking_algorithm == EOD_LIMITED_V1
    store.update(
        RuntimeSettingsPatch(
            algorithms=RuntimeAlgorithmSettingsPatch(screener_ranking_algorithm=PRODUCTION_ALGORITHM)
        ),
        expected_version=1,
    )
    assert get_effective_runtime_settings(store).algorithms.screener_ranking_algorithm == PRODUCTION_ALGORITHM


def test_concurrent_startup_preserves_migration_rollback_record(tmp_path: Path, monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from app.services import screener_default_migration as migration

    path = tmp_path / "runtime-settings.json"
    _write_production_document(path)
    first, second = _store(path), _store(path)
    publishing, release, peer_started, peer_read = Event(), Event(), Event(), Event()
    original_write = migration._write_record
    original_read = second.read

    def delayed_write(record_path, record):
        if record["status"] == STATUS_APPLIED:
            publishing.set()
            assert release.wait(5)
        return original_write(record_path, record)

    def observed_read():
        peer_read.set()
        return original_read()

    def run_peer():
        peer_started.set()
        return apply_screener_default_migration(second)

    monkeypatch.setattr(migration, "_write_record", delayed_write)
    monkeypatch.setattr(second, "read", observed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first_result = pool.submit(apply_screener_default_migration, first)
        try:
            assert publishing.wait(5)
            second_result = pool.submit(run_peer)
            assert peer_started.wait(5)
            # The peer must not classify the newly written EOD setting before
            # the first worker has committed its migration record.
            assert not peer_read.wait(0.2)
        finally:
            release.set()
        assert first_result.result(timeout=5)["status"] == STATUS_APPLIED
        assert second_result.result(timeout=5)["status"] == STATUS_APPLIED
    assert rollback_screener_default_migration(first)["status"] == STATUS_ROLLED_BACK
    assert first.read().settings.algorithms.screener_ranking_algorithm == PRODUCTION_ALGORITHM


def test_record_replacement_failure_preserves_previous_record(tmp_path: Path, monkeypatch) -> None:
    import pytest
    from app.services import screener_default_migration as migration

    path = tmp_path / "migration.json"
    previous = {"status": STATUS_APPLIED}
    migration._write_record(path, previous)

    def fail_replace(*args):
        raise OSError("replacement failed")

    monkeypatch.setattr(migration.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replacement failed"):
        migration._write_record(path, {"status": STATUS_ROLLED_BACK})
    assert json.loads(path.read_text()) == previous
    assert list(tmp_path.iterdir()) == [path]


def test_init_frozen_production_is_migrated_once(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    _write_production_document(path)
    store = _store(path)
    first = apply_screener_default_migration(store)
    second = apply_screener_default_migration(store)
    assert first["id"] == MIGRATION_ID
    assert first["status"] == STATUS_APPLIED
    assert first["from_algorithm"] == PRODUCTION_ALGORITHM
    assert first["to_algorithm"] == EOD_LIMITED_V1
    assert second == first
    assert store.read().settings.algorithms.screener_ranking_algorithm == EOD_LIMITED_V1
    assert store.read().settings.algorithms.radar_sort_algorithm == PRODUCTION_ALGORITHM


def test_admin_a0_lock_is_not_overwritten(tmp_path: Path) -> None:
    store = _store(tmp_path / "runtime-settings.json")
    store.update(
        RuntimeSettingsPatch(
            algorithms=RuntimeAlgorithmSettingsPatch(
                screener_ranking_algorithm=A0_ALGORITHM,
            )
        ),
        expected_version=1,
    )
    record = apply_screener_default_migration(store)
    assert record["status"] == STATUS_ADMIN_LOCKED
    assert store.read().settings.algorithms.screener_ranking_algorithm == A0_ALGORITHM


def test_effective_reader_applies_frozen_production_migration(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    _write_production_document(path)
    store = _store(path)
    settings = get_effective_runtime_settings(store)
    assert settings.algorithms.screener_ranking_algorithm == EOD_LIMITED_V1


def test_migration_rollback_restores_production_and_does_not_reapply(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    _write_production_document(path)
    store = _store(path)
    apply_screener_default_migration(store)
    rolled = rollback_screener_default_migration(store)
    assert rolled["status"] == STATUS_ROLLED_BACK
    assert store.read().settings.algorithms.screener_ranking_algorithm == PRODUCTION_ALGORITHM
    again = apply_screener_default_migration(store)
    assert again["status"] == STATUS_ROLLED_BACK
    assert store.read().settings.algorithms.screener_ranking_algorithm == PRODUCTION_ALGORITHM


def test_disabled_env_skips_persisted_production(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "runtime-settings.json"
    _write_production_document(path)
    monkeypatch.setenv("OPTIX_SCREENER_DEFAULT_MIGRATION", "0")
    store = _store(path)
    record = apply_screener_default_migration(store)
    assert record["status"] == STATUS_DISABLED
    assert store.read().settings.algorithms.screener_ranking_algorithm == PRODUCTION_ALGORITHM
