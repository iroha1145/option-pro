from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import view_preferences as view_preferences_api
from app.services import view_preferences
from app.services.view_preferences import (
    ViewPreferenceStorageError,
    ViewPreferenceStore,
    normalize_view_preferences,
)


def _preference_record(index: int) -> dict[str, str]:
    return {
        "screener_ranking_algorithm": (
            "production" if index % 2 else "follow_default"
        ),
        "radar_sort_algorithm": (
            "t1_daily_priority" if index % 3 else "production"
        ),
        "updated_at": "2026-09-16T00:00:00Z",
    }


def test_valid_document_larger_than_legacy_limit_keeps_every_principal(
    tmp_path: Path,
) -> None:
    path = tmp_path / "view-preferences.json"
    principals = {
        f"account:{index:04d}": _preference_record(index) for index in range(700)
    }
    original = json.dumps(
        {"version": 1, "principals": principals},
        separators=(",", ":"),
    ).encode("utf-8")
    assert 64 * 1024 < len(original) < view_preferences._MAX_DOCUMENT_BYTES
    path.write_bytes(original)

    store = ViewPreferenceStore(path)
    store.write(
        "account:new",
        normalize_view_preferences(
            {
                "screener_ranking_algorithm": "a0_mid_long",
                "radar_sort_algorithm": "t1_daily_priority",
            }
        ),
    )

    saved = json.loads(path.read_text(encoding="utf-8"))["principals"]
    assert len(saved) == len(principals) + 1
    assert saved["account:0000"] == principals["account:0000"]
    assert saved["account:0699"] == principals["account:0699"]
    assert saved["account:new"]["screener_ranking_algorithm"] == "a0_mid_long"


def test_oversized_document_rejects_write_without_replacing_existing_bytes(
    tmp_path: Path,
) -> None:
    path = tmp_path / "view-preferences.json"
    original = json.dumps(
        {
            "version": 1,
            "principals": {"account:kept": _preference_record(1)},
            "padding": "x" * view_preferences._MAX_DOCUMENT_BYTES,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    assert len(original) > view_preferences._MAX_DOCUMENT_BYTES
    path.write_bytes(original)

    store = ViewPreferenceStore(path)
    with pytest.raises(ViewPreferenceStorageError, match="too large"):
        store.write(
            "account:new",
            normalize_view_preferences(
                {"screener_ranking_algorithm": "a0_mid_long"}
            ),
        )

    assert path.read_bytes() == original


def test_store_supports_two_thousand_accounts_plus_owner(tmp_path: Path) -> None:
    path = tmp_path / "view-preferences.json"
    principals = {
        f"account:{index:04d}": _preference_record(index) for index in range(2_000)
    }
    path.write_text(
        json.dumps(
            {"version": 1, "principals": principals},
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    store = ViewPreferenceStore(path)

    store.patch("owner", {"screener_ranking_algorithm": "a0_mid_long"})
    full_document = path.read_bytes()
    assert len(json.loads(full_document)["principals"]) == 2_001

    with pytest.raises(ViewPreferenceStorageError, match="capacity exceeded"):
        store.patch(
            "account:overflow",
            {"radar_sort_algorithm": "t1_daily_priority"},
        )
    assert path.read_bytes() == full_document


def test_concurrent_different_principals_keep_all_writes(tmp_path: Path) -> None:
    path = tmp_path / "view-preferences.json"
    stores = [ViewPreferenceStore(path) for _ in range(64)]

    def write(index_and_store: tuple[int, ViewPreferenceStore]) -> None:
        index, store = index_and_store
        store.write(
            f"account:{index:04d}",
            normalize_view_preferences(_preference_record(index)),
        )

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(write, enumerate(stores)))

    saved = json.loads(path.read_text(encoding="utf-8"))["principals"]
    assert set(saved) == {f"account:{index:04d}" for index in range(len(stores))}
    for index in range(len(stores)):
        preferences = stores[0].read(f"account:{index:04d}")
        assert preferences == normalize_view_preferences(_preference_record(index))


def test_concurrent_patches_for_one_principal_keep_both_fields(
    tmp_path: Path,
) -> None:
    path = tmp_path / "view-preferences.json"
    stores = [ViewPreferenceStore(path), ViewPreferenceStore(path)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [
            pool.submit(
                stores[0].patch,
                "account:alice",
                {"screener_ranking_algorithm": "a0_mid_long"},
            ),
            pool.submit(
                stores[1].patch,
                "account:alice",
                {"radar_sort_algorithm": "t1_daily_priority"},
            ),
        ]
        for future in futures:
            future.result()

    saved = stores[0].read("account:alice")
    assert saved.screener_ranking_algorithm == "a0_mid_long"
    assert saved.radar_sort_algorithm == "t1_daily_priority"


def test_api_reports_bounded_storage_failure_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "view-preferences.json"
    path.write_bytes(b"x" * (view_preferences._MAX_DOCUMENT_BYTES + 1))
    store = ViewPreferenceStore(path)
    monkeypatch.setattr(
        view_preferences_api,
        "get_view_preference_store",
        lambda: store,
    )
    monkeypatch.setattr(
        view_preferences_api,
        "current_request_is_owner",
        lambda: True,
    )
    app = FastAPI()
    app.include_router(view_preferences_api.router)
    client = TestClient(app, base_url="http://localhost")

    read_response = client.get("/api/view-preferences")
    assert read_response.status_code == 503
    assert read_response.json()["detail"]["code"] == (
        "view_preferences_storage_unavailable"
    )

    write_response = client.put(
        "/api/view-preferences",
        json={"screener_ranking_algorithm": "a0_mid_long"},
        headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
    )
    assert write_response.status_code == 503
    assert path.stat().st_size == view_preferences._MAX_DOCUMENT_BYTES + 1


def test_failed_atomic_replace_keeps_document_and_api_reports_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "view-preferences.json"
    store = ViewPreferenceStore(path)
    store.patch("owner", {"screener_ranking_algorithm": "production"})
    original = path.read_bytes()
    real_replace = view_preferences.os.replace

    def fail_replace(source: str, destination: str | Path) -> None:
        if Path(destination) == path:
            raise OSError("simulated atomic replacement failure")
        real_replace(source, destination)

    monkeypatch.setattr(view_preferences.os, "replace", fail_replace)
    monkeypatch.setattr(
        view_preferences_api,
        "get_view_preference_store",
        lambda: store,
    )
    monkeypatch.setattr(
        view_preferences_api,
        "current_request_is_owner",
        lambda: True,
    )
    app = FastAPI()
    app.include_router(view_preferences_api.router)
    client = TestClient(app, base_url="http://localhost")

    response = client.put(
        "/api/view-preferences",
        json={"radar_sort_algorithm": "t1_daily_priority"},
        headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["code"] == (
        "view_preferences_storage_unavailable"
    )
    assert path.read_bytes() == original
    leftovers = [
        candidate
        for candidate in tmp_path.glob("view-preferences.*")
        if candidate != path
    ]
    assert leftovers == []
