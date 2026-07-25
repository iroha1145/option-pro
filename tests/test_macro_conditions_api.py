"""Access matrix and read behaviour for the Optix Macro Conditions API.

Everything here is offline: no FRED call, no ETF download, no OpenAI call, no
production server. The macro store is seeded from deterministic synthetic panels
in ``tests/macro_fixtures.py``.
"""

from __future__ import annotations

import datetime as dt
from datetime import datetime, timezone
from typing import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import (
    OwnerAccessRuntime,
    hash_owner_password,
    require_public_read_or_owner_access,
)
from app.api import macro_conditions as macro_api
from app.personal_config import AccessConfig
from app.services.macro_conditions.registry import FACTORS, MODULES, SCORING_VERSION
from app.services.macro_conditions.repository import MacroRepository
from app.services.macro_conditions.service import (
    MacroConditionsService,
    invalidate_read_cache,
)
from app.worker.state import WorkerStateRepository
from macro_fixtures import FIXED_NOW, fixed_clock, seed_repository


PASSWORD = "owner-password-for-macro-tests"
ORIGIN = "https://testserver"
SEED_START = dt.date(2018, 7, 1)
SEED_END = dt.date(2026, 7, 23)
AS_OF = "2026-07-24T22:30:00Z"


def _action_headers() -> dict[str, str]:
    return {
        "Origin": ORIGIN,
        "X-Optix-Action": "1",
        "Sec-Fetch-Site": "same-origin",
    }


@pytest.fixture(autouse=True)
def _clear_macro_read_cache() -> Iterator[None]:
    invalidate_read_cache()
    yield
    invalidate_read_cache()


def _seeded_store(tmp_path) -> MacroRepository:
    repository = MacroRepository(tmp_path / "macro-conditions.db", clock=fixed_clock())
    seed_repository(repository, start=SEED_START, end=SEED_END)
    service = MacroConditionsService(repository, clock=fixed_clock())
    bundle, _summary = service.build_snapshot(as_of=AS_OF)
    assert bundle is not None
    repository.publish(bundle)
    return repository


def _worker_ready(tmp_path) -> WorkerStateRepository:
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("test-worker", lease_seconds=300, now=observed)
    assert token is not None
    repository.record_task(
        "test-worker",
        token,
        macro_api.MACRO_TASK_NAME,
        enabled=True,
        status="idle",
        now=observed,
    )
    return repository


def _app(*, mode: str = "password") -> FastAPI:
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode=mode),
        password_hash=hash_owner_password(PASSWORD) if mode == "password" else "",
    )
    from app.api import access as access_api

    app.include_router(access_api.router)
    app.include_router(
        macro_api.router,
        dependencies=[Depends(require_public_read_or_owner_access)],
    )
    return app


def _environment(monkeypatch, tmp_path, *, fred_key: str | None = None) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    if fred_key is None:
        monkeypatch.delenv("FRED_API_KEY", raising=False)
    else:
        monkeypatch.setenv("FRED_API_KEY", fred_key)
    from app.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setattr(
        macro_api,
        "_service",
        lambda: MacroConditionsService(
            MacroRepository(tmp_path / "macro-conditions.db", read_only=True),
            clock=fixed_clock(),
        ),
    )


def _login(client: TestClient) -> None:
    response = client.post(
        "/api/access/login",
        json={"password": PASSWORD},
        headers=_action_headers(),
    )
    assert response.status_code == 200


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def test_public_get_serves_the_snapshot_without_any_provider_call(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("a public macro read must not contact a provider")

    import app.services.signals as signals_module
    from app.services.macro_conditions import fred_client as fred_module

    monkeypatch.setattr(fred_module.FredClient, "fetch", unexpected)
    monkeypatch.setattr(signals_module, "daily_adjusted_history", unexpected)

    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.get("/api/macro/conditions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] in {"active", "degraded"}
    assert payload["scoring_version"] == SCORING_VERSION
    assert payload["composite"]["score"] is not None
    assert payload["composite"]["valid_module_count"] == len(MODULES)
    assert len(payload["modules"]) == len(MODULES)
    assert payload["history_basis"] == "latest_revised_backfill"


def test_customer_and_anonymous_readers_receive_identical_macro_payloads(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        anonymous = client.get("/api/macro/conditions").json()
    with TestClient(_app(), base_url="https://testserver") as client:
        # A customer account cookie carries no macro-specific entitlement, and
        # macro data is not partitioned per account.
        client.cookies.set("optix_account", "customer-session-token")
        customer = client.get("/api/macro/conditions").json()
    assert anonymous == customer


def test_owner_reads_the_same_macro_snapshot_as_a_visitor(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        anonymous = client.get("/api/macro/conditions").json()
        _login(client)
        owner = client.get("/api/macro/conditions").json()
    assert anonymous == owner


def test_missing_snapshot_reports_unavailable_rather_than_an_error(
    tmp_path,
    monkeypatch,
) -> None:
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.get("/api/macro/conditions")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "unavailable"
    assert payload["composite"] is None
    assert payload["modules"] == []


def test_missing_fred_key_reports_disabled_without_leaking_anything(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key=None)
    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.get("/api/macro/conditions")
    payload = response.json()
    assert payload["status"] == "disabled"
    assert payload["warnings"] == ["fred_api_key_missing"]
    assert payload["composite"] is None


def test_history_is_bounded_and_rejects_out_of_range_windows(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        assert client.get("/api/macro/conditions/history?days=29").status_code == 422
        assert client.get("/api/macro/conditions/history?days=3651").status_code == 422
        response = client.get("/api/macro/conditions/history?days=90")
    assert response.status_code == 200
    payload = response.json()
    assert payload["days"] == 90
    assert payload["points"]
    first = payload["points"][0]
    assert set(first) == {
        "date",
        "score",
        "confidence",
        "regime",
        "history_basis",
        "module_scores",
    }
    assert set(first["module_scores"]) <= {module.module_id for module in MODULES}


def test_module_detail_lists_every_factor_with_units_and_formulas(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.get("/api/macro/conditions/modules/funding")
    assert response.status_code == 200
    payload = response.json()
    assert payload["module_id"] == "funding"
    assert len(payload["factors"]) == 6
    for factor in payload["factors"]:
        assert factor["formula_version"]
        assert factor["description_zh"]
        assert factor["unit"]["unit"]
        assert factor["source"]


def test_unknown_module_and_factor_return_not_found(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        assert client.get("/api/macro/conditions/modules/nope").status_code == 404
        assert (
            client.get("/api/macro/conditions/factors/nope/history").status_code == 404
        )


def test_factor_history_returns_only_that_factor(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.get("/api/macro/conditions/factors/vix/history?days=60")
    payload = response.json()
    assert payload["factor_id"] == "vix"
    assert payload["module_id"] == "risk"
    assert payload["points"]
    assert all(set(point) == {
        "date",
        "raw_value",
        "signed_value",
        "score",
        "status",
        "data_through",
        "history_basis",
    } for point in payload["points"])


def test_every_registered_factor_is_addressable(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        for factor in FACTORS:
            response = client.get(
                f"/api/macro/conditions/factors/{factor.factor_id}/history?days=30"
            )
            assert response.status_code == 200, factor.factor_id


def test_macro_responses_never_contain_a_secret_or_a_database_path(
    tmp_path,
    monkeypatch,
) -> None:
    secret = "b" * 32
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key=secret)
    with TestClient(_app(), base_url="https://testserver") as client:
        bodies = [
            client.get("/api/macro/conditions").text,
            client.get("/api/macro/conditions/history?days=30").text,
            client.get("/api/macro/conditions/modules/risk").text,
            client.get("/api/macro/conditions/factors/vix/history").text,
        ]
    for body in bodies:
        assert secret not in body
        assert "FRED_API_KEY" not in body
        assert "api_key" not in body
        assert "macro-conditions.db" not in body
        assert str(tmp_path) not in body
        assert "Traceback" not in body


# ---------------------------------------------------------------------------
# Refresh action
# ---------------------------------------------------------------------------


def test_anonymous_refresh_is_rejected(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 401


def test_customer_account_cookie_cannot_refresh(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        client.cookies.set("optix_account", "customer-session-token")
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 401


def test_owner_refresh_enqueues_one_worker_action(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    repository = _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("the request thread must not contact FRED")

    from app.services.macro_conditions import fred_client as fred_module

    monkeypatch.setattr(fred_module.FredClient, "__init__", unexpected)

    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 202
    payload = response.json()
    assert payload["task_name"] == macro_api.MACRO_TASK_NAME
    assert payload["action_type"] == macro_api.MACRO_ACTION_TYPE
    assert payload["status"] == "queued"
    assert payload["reason"] == "queued"
    assert payload["error_code"] is None
    assert payload["cooldown_seconds"] == 300.0
    queued = repository.action_requests(action_type=macro_api.MACRO_ACTION_TYPE)
    assert len(queued) == 1
    assert queued[0]["task_name"] == macro_api.MACRO_TASK_NAME


def test_owner_refresh_reuses_an_identical_idempotency_key(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    repository = _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        first = client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-key-1"},
            headers=_action_headers(),
        )
        second = client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-key-1"},
            headers=_action_headers(),
        )
    assert first.status_code == 202
    assert second.status_code == 200
    assert second.json()["reused"] is True
    assert second.json()["reason"] == "idempotent"
    assert first.json()["request_id"] == second.json()["request_id"]
    assert len(repository.action_requests(action_type=macro_api.MACRO_ACTION_TYPE)) == 1


def test_a_second_queued_refresh_reports_in_progress(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-key-a"},
            headers=_action_headers(),
        )
        second = client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-key-b"},
            headers=_action_headers(),
        )
    assert second.status_code == 200
    assert second.json()["reason"] == "already_running"
    assert second.json()["error_code"] == "macro_refresh_in_progress"


def test_refresh_reports_cooldown_after_a_completed_run(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    repository = _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    observed = datetime.now(timezone.utc)
    token = repository.acquire("test-worker", lease_seconds=300, now=observed)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        first = client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-cool-1"},
            headers=_action_headers(),
        )
        request_id = first.json()["request_id"]
        claimed = repository.claim_actions(
            "test-worker",
            token if token is not None else 1,
            macro_api.MACRO_TASK_NAME,
        )
        assert [item["request_id"] for item in claimed] == [request_id]
        repository.finish_actions(
            "test-worker",
            token if token is not None else 1,
            [request_id],
            succeeded=True,
        )
        second = client.post(
            "/api/macro/conditions/refresh",
            json={"idempotency_key": "macro-cool-2"},
            headers=_action_headers(),
        )
    assert second.status_code == 200
    assert second.json()["reason"] == "cooldown"
    assert second.json()["error_code"] == "macro_refresh_cooldown"


def test_refresh_without_the_action_header_is_rejected(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers={"Origin": ORIGIN},
        )
    assert response.status_code == 403


def test_refresh_is_rejected_when_the_fred_key_is_absent(tmp_path, monkeypatch) -> None:
    _seeded_store(tmp_path)
    _worker_ready(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key=None)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "fred_api_key_missing"


def test_refresh_reports_worker_unavailable_without_a_live_worker(
    tmp_path,
    monkeypatch,
) -> None:
    _seeded_store(tmp_path)
    _environment(monkeypatch, tmp_path, fred_key="a" * 32)
    with TestClient(_app(), base_url="https://testserver") as client:
        _login(client)
        response = client.post(
            "/api/macro/conditions/refresh",
            json={},
            headers=_action_headers(),
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "worker_unavailable"
