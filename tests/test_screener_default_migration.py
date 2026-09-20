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


def test_unpersisted_store_keeps_code_default_without_writing(tmp_path: Path) -> None:
    store = _store(tmp_path / "runtime-settings.json")
    record = apply_screener_default_migration(store)
    assert record["status"] == STATUS_ALREADY_NEW
    assert store.read().settings.algorithms.screener_ranking_algorithm == EOD_LIMITED_V1
    assert not store.path.exists()


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
