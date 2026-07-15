from __future__ import annotations

import json
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Barrier

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.runtime_settings import router
from app.personal_config import load_personal_config
from app.services import runtime_settings as runtime_settings_module
from app.services.runtime_settings import (
    RuntimeAISettingsPatch,
    RuntimeCatalystSettingsPatch,
    RuntimeSettingsPatch,
    RuntimeSettingsRevisionNotFound,
    RuntimeSettingsStorageError,
    RuntimeSettingsStore,
    RuntimeSettingsVersionConflict,
    get_effective_runtime_settings,
    get_runtime_settings_store,
    runtime_settings_from_personal_config,
)


class SteppingClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 7, 16, 0, 0, tzinfo=timezone.utc)

    def __call__(self) -> datetime:
        current = self.value
        self.value += timedelta(seconds=1)
        return current


def make_store(path: Path, *, backup_keep: int = 7) -> RuntimeSettingsStore:
    return RuntimeSettingsStore(
        path,
        defaults=runtime_settings_from_personal_config(load_personal_config()),
        backup_keep=backup_keep,
        clock=SteppingClock(),
    )


def make_client(store: RuntimeSettingsStore) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_runtime_settings_store] = lambda: store
    return TestClient(app, base_url="http://localhost")


def test_defaults_follow_non_secret_personal_configuration(tmp_path: Path) -> None:
    personal = load_personal_config()
    store = make_store(tmp_path / "runtime-settings.json")

    document = store.read()

    assert document.version == 1
    assert document.settings.ai.daily_max_jobs == personal.ai.daily_max_jobs
    assert document.settings.ai.daily_budget_usd == personal.ai.daily_budget_usd
    assert (
        document.settings.ai.manual_analysis_enabled
        is personal.catalyst_manual_enabled
    )
    assert document.settings.catalyst.sync_seconds == personal.catalyst.sync_seconds
    assert document.settings.catalyst.focus_seconds == personal.catalyst.focus_seconds
    assert (
        document.settings.catalyst.manual_force_reanalysis
        is personal.catalyst.manual_force_reanalysis
    )
    assert (
        document.settings.catalyst.manual_refresh_cooldown_seconds
        == personal.catalyst.manual_refresh_cooldown_seconds
    )
    assert document.settings.ai.manual_analysis_cooldown_seconds == 30
    assert (
        document.settings.catalyst.scheduled_analysis_enabled
        is personal.catalyst_scheduled_enabled
    )
    assert document.settings.catalyst.scheduled_times_et == tuple(
        personal.catalyst.scheduled_times_et
    )
    assert not store.path.exists(), "读取默认值不应产生设置文件"


def test_default_store_uses_existing_data_dir_without_a_new_path_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    get_runtime_settings_store.cache_clear()
    try:
        store = get_runtime_settings_store()
        assert store.path == (tmp_path / "runtime-settings.json").resolve()
    finally:
        get_runtime_settings_store.cache_clear()


def test_update_is_atomic_versioned_backed_up_and_rollback_creates_new_version(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    original = store.read()

    second = store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(
                daily_budget_usd=3.25,
                manual_analysis_enabled=True,
            )
        ),
        expected_version=1,
    )
    third = store.update(
        RuntimeSettingsPatch(
            catalyst=RuntimeCatalystSettingsPatch(
                manual_refresh_cooldown_seconds=45,
            )
        ),
        expected_version=2,
    )

    assert second.version == 2
    assert third.version == 3
    assert store.read() == third
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600
    assert not list(tmp_path.rglob("*.tmp"))
    assert store._backup_path(1).exists()
    assert store._backup_path(2).exists()
    assert [item.version for item in store.history()] == [3, 2, 1]

    restored = store.rollback(1, expected_version=3)

    assert restored.version == 4
    assert restored.settings == original.settings
    assert store.read() == restored
    assert store._backup_path(3).exists()
    assert [item.version for item in store.history()] == [4, 3, 2, 1]


def test_version_conflict_does_not_overwrite_current_document(tmp_path: Path) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    current = store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(daily_budget_usd=2.5),
        ),
        expected_version=1,
    )

    with pytest.raises(RuntimeSettingsVersionConflict) as raised:
        store.update(
            RuntimeSettingsPatch(
                ai=RuntimeAISettingsPatch(daily_budget_usd=4.0),
            ),
            expected_version=1,
        )

    assert raised.value.current_version == 2
    assert store.read() == current


def test_concurrent_updates_across_store_instances_commit_only_one_version(
    tmp_path: Path,
) -> None:
    path = tmp_path / "runtime-settings.json"
    stores = (make_store(path), make_store(path))
    barrier = Barrier(2)

    def update(index: int):
        barrier.wait()
        try:
            return stores[index].update(
                RuntimeSettingsPatch(
                    ai=RuntimeAISettingsPatch(daily_budget_usd=2.5 + index),
                ),
                expected_version=1,
            )
        except RuntimeSettingsVersionConflict as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, range(2)))

    committed = [item for item in results if not isinstance(item, Exception)]
    conflicts = [
        item for item in results if isinstance(item, RuntimeSettingsVersionConflict)
    ]
    assert len(committed) == 1
    assert committed[0].version == 2
    assert len(conflicts) == 1
    assert conflicts[0].current_version == 2
    assert stores[0].read() == committed[0]


def test_failed_atomic_replace_keeps_previous_document_and_cleans_temp_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    current = store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(daily_budget_usd=2.5),
        ),
        expected_version=1,
    )
    real_replace = runtime_settings_module.os.replace

    def fail_current_replace(source: Path, destination: Path) -> None:
        if Path(destination) == store.path:
            raise OSError("simulated replace failure")
        real_replace(source, destination)

    monkeypatch.setattr(runtime_settings_module.os, "replace", fail_current_replace)

    with pytest.raises(RuntimeSettingsStorageError):
        store.update(
            RuntimeSettingsPatch(
                ai=RuntimeAISettingsPatch(daily_budget_usd=3.0),
            ),
            expected_version=2,
        )

    assert store.read() == current
    assert not list(tmp_path.rglob("*.tmp"))


def test_failed_current_write_does_not_prune_existing_rollback_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json", backup_keep=2)
    store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(daily_budget_usd=2.5),
        ),
        expected_version=1,
    )
    current = store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(daily_budget_usd=3.0),
        ),
        expected_version=2,
    )
    history_before = [item.version for item in store.history()]
    real_replace = runtime_settings_module.os.replace

    def fail_current_replace(source: Path, destination: Path) -> None:
        if Path(destination) == store.path:
            raise OSError("simulated current write failure")
        real_replace(source, destination)

    monkeypatch.setattr(runtime_settings_module.os, "replace", fail_current_replace)

    with pytest.raises(RuntimeSettingsStorageError):
        store.update(
            RuntimeSettingsPatch(
                ai=RuntimeAISettingsPatch(daily_budget_usd=3.5),
            ),
            expected_version=3,
        )

    assert store.read() == current
    assert history_before == [3, 2, 1]
    assert [item.version for item in store.history()] == history_before
    assert store._backup_path(1).exists()
    assert not list(tmp_path.rglob("*.tmp"))


def test_backup_retention_is_bounded_and_old_revision_cannot_be_restored(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json", backup_keep=2)
    for expected, budget in ((1, 2.5), (2, 3.0), (3, 3.5)):
        store.update(
            RuntimeSettingsPatch(
                ai=RuntimeAISettingsPatch(daily_budget_usd=budget),
            ),
            expected_version=expected,
        )

    assert [item.version for item in store.history()] == [4, 3, 2]
    with pytest.raises(RuntimeSettingsRevisionNotFound):
        store.rollback(1, expected_version=4)


def test_invalid_or_secret_bearing_file_is_never_returned(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": 1,
                "updated_at": "2026-07-16T00:00:00Z",
                "settings": {
                    "ai": {"api_key": "must-not-leak"},
                    "catalyst": {},
                },
            }
        ),
        encoding="utf-8",
    )
    store = make_store(path)
    response = make_client(store).get("/api/runtime-settings")

    with pytest.raises(RuntimeSettingsStorageError):
        store.read()
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "settings_storage_unavailable"
    assert "must-not-leak" not in response.text


def test_api_reads_updates_history_and_rolls_back_without_secret_fields(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "environment-secret-marker")
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)

    initial = client.get("/api/runtime-settings")
    updated = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {
                "ai": {
                    "daily_budget_usd": 2.75,
                    "manual_analysis_enabled": True,
                    "manual_analysis_cooldown_seconds": 90,
                },
                "catalyst": {
                    "manual_force_reanalysis": True,
                    "manual_refresh_enabled": True,
                    "manual_refresh_cooldown_seconds": 40,
                    "scheduled_analysis_enabled": True,
                    "scheduled_times_et": ["08:00", "12:30", "08:00"],
                },
            },
        },
    )
    history = client.get("/api/runtime-settings/history")
    rolled_back = client.post(
        "/api/runtime-settings/rollback",
        json={"expected_version": 2, "target_version": 1},
    )

    assert initial.status_code == 200
    assert updated.status_code == 200
    assert updated.json()["version"] == 2
    assert updated.json()["settings"]["ai"]["daily_budget_usd"] == 2.75
    assert updated.json()["settings"]["catalyst"]["scheduled_analysis_enabled"]
    assert updated.json()["settings"]["catalyst"]["scheduled_times_et"] == [
        "08:00",
        "12:30",
    ]
    assert history.status_code == 200
    assert [item["version"] for item in history.json()["revisions"]] == [2, 1]
    assert rolled_back.status_code == 200
    assert rolled_back.json()["version"] == 3
    assert rolled_back.json()["settings"] == initial.json()["settings"]

    combined = json.dumps(
        [initial.json(), updated.json(), history.json(), rolled_back.json()]
    ).casefold()
    assert "environment-secret-marker" not in combined
    assert "api_key" not in combined
    assert "password" not in combined
    assert "token" not in combined


@pytest.mark.parametrize(
    "content_type",
    [
        "text/plain",
        "application/x-www-form-urlencoded",
        "application/merge-patch+json",
        "",
    ],
)
def test_api_write_rejects_every_media_type_except_application_json(
    tmp_path: Path,
    content_type: str,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)
    headers = {"Content-Type": content_type} if content_type else {}
    body = json.dumps(
        {
            "expected_version": 1,
            "settings": {"ai": {"daily_budget_usd": 2.75}},
        }
    )

    response = client.put(
        "/api/runtime-settings",
        content=body,
        headers=headers,
    )

    assert response.status_code == 415
    assert response.json()["detail"]["code"] == "unsupported_media_type"
    assert store.read().version == 1
    assert not store.path.exists()


def test_api_write_accepts_application_json_with_charset(tmp_path: Path) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)

    response = client.put(
        "/api/runtime-settings",
        content=json.dumps(
            {
                "expected_version": 1,
                "settings": {"ai": {"daily_budget_usd": 2.75}},
            }
        ),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 200
    assert response.json()["version"] == 2


def test_effective_settings_reader_observes_updates_without_process_restart(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    initial = get_effective_runtime_settings(store)
    updated_budget = 3.5 if initial.ai.daily_budget_usd != 3.5 else 4.5
    store.update(
        RuntimeSettingsPatch(
            ai=RuntimeAISettingsPatch(
                daily_max_jobs=3,
                daily_budget_usd=updated_budget,
                manual_analysis_enabled=True,
                manual_analysis_cooldown_seconds=45,
            ),
            catalyst=RuntimeCatalystSettingsPatch(
                focus_seconds=900,
                scheduled_analysis_enabled=True,
                scheduled_times_et=("09:15", "15:45"),
            ),
        ),
        expected_version=1,
    )

    effective = get_effective_runtime_settings(store)

    assert initial.ai.daily_budget_usd != effective.ai.daily_budget_usd
    assert effective.ai.daily_max_jobs == 3
    assert effective.ai.daily_budget_usd == updated_budget
    assert effective.ai.manual_analysis_enabled is True
    assert effective.ai.manual_analysis_cooldown_seconds == 45
    assert effective.catalyst.focus_seconds == 900
    assert effective.catalyst.scheduled_analysis_enabled is True
    assert effective.catalyst.scheduled_times_et == ("09:15", "15:45")


@pytest.mark.parametrize(
    "secret_payload",
    [
        {"api_key": "submitted-secret-marker"},
        {"Token": "submitted-secret-marker"},
        {"nested": {"PASSWORD": "submitted-secret-marker"}},
        {"nested": [{"clientSecret": "submitted-secret-marker"}]},
    ],
)
def test_api_rejects_sensitive_field_names_at_any_depth_without_echoing_values(
    tmp_path: Path,
    secret_payload: dict,
) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)

    response = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {
                "ai": {"daily_budget_usd": 2.0},
                "unknown": secret_payload,
            },
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "sensitive_field_rejected"
    assert "submitted-secret-marker" not in response.text
    assert store.read().version == 1
    assert not store.path.exists()


def test_api_validation_errors_do_not_echo_submitted_values(tmp_path: Path) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)

    invalid_value = "private-value-that-must-not-be-reflected"
    response = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {"ai": {"daily_budget_usd": invalid_value}},
        },
    )
    invalid_schedule = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {"catalyst": {"scheduled_times_et": ["25:00"]}},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_settings"
    assert invalid_value not in response.text
    assert invalid_schedule.status_code == 422
    assert invalid_schedule.json()["detail"]["code"] == "invalid_settings"


def test_api_reports_version_conflict_without_returning_settings(tmp_path: Path) -> None:
    store = make_store(tmp_path / "runtime-settings.json")
    client = make_client(store)
    first = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {"ai": {"daily_budget_usd": 2.5}},
        },
    )
    conflict = client.put(
        "/api/runtime-settings",
        json={
            "expected_version": 1,
            "settings": {"ai": {"daily_budget_usd": 3.5}},
        },
    )

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "version_conflict",
        "message": "运行设置已被其他请求更新，请重新读取后再保存",
        "current_version": 2,
    }
    assert "settings" not in conflict.text
