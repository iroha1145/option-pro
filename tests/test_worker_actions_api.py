from __future__ import annotations

from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import worker_actions
from app.worker.state import WorkerStateRepository


def _live_repository(tmp_path, monkeypatch) -> tuple[WorkerStateRepository, int]:
    path = tmp_path / "optix-worker.db"
    monkeypatch.setenv("OPTIX_WORKER_DB_PATH", str(path))
    repository = WorkerStateRepository(path)
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire(
        "test-worker",
        lease_seconds=300,
        now=observed,
    )
    assert token is not None
    for task_name in ("focus", "breakout", "maintenance"):
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
        assert second_kind.json()["task_name"] == "focus"

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
            "breakout",
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
    monkeypatch.setenv("OPTIX_WORKER_DB_PATH", str(tmp_path / "missing.db"))
    with _client() as client:
        response = client.post("/api/worker/actions/retention", json={})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "worker_unavailable"


def test_manual_action_rejects_disabled_task(tmp_path, monkeypatch) -> None:
    repository, token = _live_repository(tmp_path, monkeypatch)
    repository.record_task(
        "test-worker",
        token,
        "maintenance",
        enabled=False,
        status="disabled",
    )
    with _client() as client:
        response = client.post("/api/worker/actions/retention", json={})
        assert response.status_code == 409
        assert response.json()["detail"] == {
            "code": "worker_task_disabled",
            "task": "maintenance",
        }
