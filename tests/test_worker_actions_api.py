from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import worker_actions
from app.api.strength import (
    DEFAULT_STRENGTH_SCAN_PARAMETERS,
    strength_scan_parameters_hash,
)
from app.worker.state import WorkerStateRepository


def _live_repository(tmp_path, monkeypatch) -> tuple[WorkerStateRepository, int]:
    path = tmp_path / "optix-worker.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repository = WorkerStateRepository(path)
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire(
        "test-worker",
        lease_seconds=300,
        now=observed,
    )
    assert token is not None
    for task_name in (
        "focus_refresh",
        "strength_refresh",
        "breakout_refresh",
        "retention",
    ):
        repository.record_task(
            "test-worker",
            token,
            task_name,
            enabled=True,
            status="idle",
            now=observed,
        )
    return repository, token


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(worker_actions.router)
    return TestClient(app)


def _strength_parameters(**updates) -> dict:
    return {**DEFAULT_STRENGTH_SCAN_PARAMETERS, **updates}


def test_manual_actions_queue_and_reuse_the_same_minute(tmp_path, monkeypatch) -> None:
    _live_repository(tmp_path, monkeypatch)
    with _client() as client:
        first = client.post("/api/worker/actions/focus_refresh", json={})
        duplicate = client.post("/api/worker/actions/focus_refresh", json={})
        second_kind = client.post(
            "/api/worker/actions/strength_refresh",
            json={"idempotency_key": "strength:manual-test"},
        )

        assert first.status_code == 202
        assert first.json()["status"] == "queued"
        assert first.json()["reason"] == "queued"
        assert duplicate.status_code == 200
        assert duplicate.json()["request_id"] == first.json()["request_id"]
        assert duplicate.json()["reason"] == "idempotent"
        assert second_kind.status_code == 202
        assert first.json()["task_name"] == "focus_refresh"
        assert second_kind.json()["task_name"] == "strength_refresh"

        status_response = client.get("/api/worker/status")
        action_response = client.get(
            f"/api/worker/actions/{first.json()['request_id']}"
        )
        assert status_response.status_code == 200
        assert status_response.json()["healthy"] is True
        assert action_response.status_code == 200
        assert action_response.json()["request_id"] == first.json()["request_id"]
        assert "idempotency_key" not in action_response.json()


def test_manual_action_reports_active_and_cooldown_states(tmp_path, monkeypatch) -> None:
    repository, token = _live_repository(tmp_path, monkeypatch)
    with _client() as client:
        first = client.post(
            "/api/worker/actions/breakout_refresh",
            json={"idempotency_key": "breakout:first"},
        )
        active = client.post(
            "/api/worker/actions/breakout_refresh",
            json={"idempotency_key": "breakout:second"},
        )
        assert first.status_code == 202
        assert active.status_code == 200
        assert active.json()["request_id"] == first.json()["request_id"]
        assert active.json()["reason"] == "already_running"

        claimed = repository.claim_actions(
            "test-worker",
            token,
            "breakout_refresh",
        )
        repository.finish_actions(
            "test-worker",
            token,
            [item["request_id"] for item in claimed],
            succeeded=True,
        )
        cooling = client.post(
            "/api/worker/actions/breakout_refresh",
            json={"idempotency_key": "breakout:third"},
        )
        assert cooling.status_code == 200
        assert cooling.json()["status"] == "completed"
        assert cooling.json()["reason"] == "cooldown"
        assert cooling.json()["cooldown_until"] is not None


def test_manual_action_fails_closed_when_worker_is_unavailable(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with _client() as client:
        response = client.post("/api/worker/actions/retention", json={})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "worker_unavailable"


def test_manual_action_rejects_disabled_task(tmp_path, monkeypatch) -> None:
    repository, token = _live_repository(tmp_path, monkeypatch)
    repository.record_task(
        "test-worker",
        token,
        "retention",
        enabled=False,
        status="disabled",
    )
    with _client() as client:
        response = client.post("/api/worker/actions/retention", json={})
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "worker_task_disabled",
            "task": "retention",
        }


@pytest.mark.parametrize(
    ("action_type", "task_name"),
    [
        ("focus_refresh", "focus_refresh"),
        ("strength_refresh", "strength_refresh"),
        ("breakout_refresh", "breakout_refresh"),
        ("retention", "retention"),
    ],
)
def test_manual_action_reports_unavailable_when_its_task_is_missing(
    action_type,
    task_name,
    tmp_path,
    monkeypatch,
) -> None:
    repository, _token = _live_repository(tmp_path, monkeypatch)
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            "DELETE FROM worker_task_status WHERE task_name=?",
            (task_name,),
        )
    with _client() as client:
        response = client.post(f"/api/worker/actions/{action_type}", json={})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "worker_task_unavailable",
        "task": task_name,
    }


def test_strength_action_persists_full_parameters_and_hashes_default_idempotency(
    tmp_path,
    monkeypatch,
) -> None:
    repository, _token = _live_repository(tmp_path, monkeypatch)
    parameters = _strength_parameters(
        timeframe="mid",
        profile="conservative",
        top=30,
        sector_id="semiconductors",
        min_price=12.5,
        min_avg_dollar_volume=25_000_000.0,
        include_options=False,
    )
    expected_hash = strength_scan_parameters_hash(parameters)
    with _client() as client:
        first = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": parameters},
        )
        duplicate = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": parameters},
        )

    assert first.status_code == 202
    assert first.json()["details"] == {
        "parameters": parameters,
        "parameters_hash": expected_hash,
    }
    assert duplicate.status_code == 200
    assert duplicate.json()["request_id"] == first.json()["request_id"]
    assert duplicate.json()["reason"] == "idempotent"
    with sqlite3.connect(repository.path) as connection:
        key = connection.execute(
            "SELECT idempotency_key FROM worker_action_requests WHERE request_id=?",
            (first.json()["request_id"],),
        ).fetchone()[0]
    assert key.endswith(f":{expected_hash}")


def test_strength_action_reuses_active_actual_parameters_for_a_different_request(
    tmp_path,
    monkeypatch,
) -> None:
    _live_repository(tmp_path, monkeypatch)
    running_parameters = _strength_parameters(top=30, profile="aggressive")
    requested_parameters = _strength_parameters(top=50, profile="conservative")
    with _client() as client:
        first = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": running_parameters},
        )
        reused = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": requested_parameters},
        )

    assert first.status_code == 202
    assert reused.status_code == 200
    assert reused.json()["reason"] == "already_running"
    assert reused.json()["request_id"] == first.json()["request_id"]
    assert reused.json()["details"]["parameters"] == running_parameters


def test_strength_action_cooldown_reuses_the_completed_actual_parameters(
    tmp_path,
    monkeypatch,
) -> None:
    repository, token = _live_repository(tmp_path, monkeypatch)
    completed_parameters = _strength_parameters(top=10, timeframe="short")
    requested_parameters = _strength_parameters(top=50, timeframe="long")
    with _client() as client:
        first = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": completed_parameters},
        )
        claimed = repository.claim_actions(
            "test-worker",
            token,
            "strength_refresh",
        )
        repository.finish_actions(
            "test-worker",
            token,
            [item["request_id"] for item in claimed],
            succeeded=True,
            details={"result": {"snapshot": "variant.json"}},
        )
        reused = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": requested_parameters},
        )

    assert first.status_code == 202
    assert reused.status_code == 200
    assert reused.json()["reason"] == "cooldown"
    assert reused.json()["details"]["parameters"] == completed_parameters
    assert reused.json()["details"]["result"] == {"snapshot": "variant.json"}


@pytest.mark.parametrize(
    ("action_type", "body", "expected_code"),
    [
        (
            "focus_refresh",
            {"parameters": _strength_parameters()},
            "action_parameters_not_allowed",
        ),
        ("strength_refresh", {"parameters": None}, "strength_parameters_required"),
        (
            "strength_refresh",
            {"parameters": {**_strength_parameters(), "sector_id": "not-a-sector"}},
            "strength_parameters_invalid",
        ),
    ],
)
def test_action_parameter_boundaries_fail_closed(
    action_type,
    body,
    expected_code,
    tmp_path,
    monkeypatch,
) -> None:
    _live_repository(tmp_path, monkeypatch)
    with _client() as client:
        response = client.post(f"/api/worker/actions/{action_type}", json=body)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == expected_code


def test_strength_parameter_object_is_complete_and_strict(tmp_path, monkeypatch) -> None:
    _live_repository(tmp_path, monkeypatch)
    incomplete = _strength_parameters()
    incomplete.pop("include_options")
    coerced = {**_strength_parameters(), "top": "30"}
    with _client() as client:
        missing = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": incomplete},
        )
        wrong_type = client.post(
            "/api/worker/actions/strength_refresh",
            json={"parameters": coerced},
        )

    assert missing.status_code == 422
    assert wrong_type.status_code == 422
