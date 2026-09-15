from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from datetime import datetime, timezone

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.access import OwnerAccessRuntime, hash_owner_password, require_same_origin_action
from app.api import accounts as accounts_api
from app.api import strength, worker_actions
from app.personal_config import AccessConfig
from app.services.accounts import AccountStore, set_account_store
from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM
from app.services.strength.variant_demand import (
    VARIANT_DEMAND_MAX,
    VARIANT_DEMAND_TTL_SECONDS,
    VARIANT_DEMAND_WRITE_INTERVAL_SECONDS,
    enqueue_customer_variant_refresh,
    list_pending_strength_variant_demands,
    register_strength_variant_demand,
    strength_variant_unavailable_detail,
)
from app.worker.state import WorkerStateRepository
from app.worker.tasks import StrengthRefreshTask
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp
from tests.test_strength_worker_snapshot import NOW, _payload


def _signed_in_request() -> object:
    request = _areq()
    return request


def test_anonymous_missing_a0_does_not_register_demand(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    default_path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: False)
    monkeypatch.setattr(strength, "request_has_account_session", lambda _req: False)
    monkeypatch.setattr(strength, "request_account_session", lambda _req: None)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _signed_in_request(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "strength_snapshot_unavailable"
    assert list_pending_strength_variant_demands(root=tmp_path / "strength-variant-demand") == []


def test_signed_in_customer_missing_a0_registers_demand_then_reads_full_pool(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    default_path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: False)
    monkeypatch.setattr(strength, "request_has_account_session", lambda _req: True)
    monkeypatch.setattr(
        strength,
        "request_account_session",
        lambda _req: SimpleNamespace(user_id="alice"),
    )
    monkeypatch.setattr(strength, "principal_for_request", lambda **_kwargs: "account:alice")
    monkeypatch.setattr(
        strength,
        "get_effective_runtime_settings",
        lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": PRODUCTION_ALGORITHM,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _signed_in_request(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "strength_snapshot_preparing"
    pending = list_pending_strength_variant_demands(root=tmp_path / "strength-variant-demand")
    assert pending
    assert pending[0]["ranking_algorithm"] == A0_ALGORITHM

    a0_parameters = strength.a0_companion_scan_parameters()
    a0_path = strength._strength_snapshot_path(a0_parameters, base_path=default_path)

    async def fake_scanner(**kwargs):
        parameters = {key: value for key, value in kwargs.items() if key != "force_refresh"}
        return _payload(parameters=parameters, ticker="A0ROW")

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=default_path,
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {
                    "request_id": "act_a0",
                    "details": {
                        "parameters": a0_parameters,
                        "parameters_hash": strength.strength_scan_parameters_hash(a0_parameters),
                    },
                }
            ]
        )
    )
    assert result.status == "idle"
    assert a0_path.is_file()
    published = _rp(
        asyncio.run(
            strength.scan(
                _signed_in_request(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    )
    assert published["rows"][0]["ticker"] == "A0ROW"
    assert published["effective_algorithm"] == A0_ALGORITHM


def test_variant_demand_rejects_non_a0_parameters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        register_strength_variant_demand(
            dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
            principal="account:alice",
            root=tmp_path / "strength-variant-demand",
        )


def _a0_parameters(**updates) -> dict:
    payload = {
        **strength.DEFAULT_STRENGTH_SCAN_PARAMETERS,
        "ranking_algorithm": A0_ALGORITHM,
        **updates,
    }
    return strength.normalize_strength_scan_parameters(payload)


def _init_strength_worker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool = True):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    repository = WorkerStateRepository(tmp_path / "optix-worker.db")
    observed = datetime.now(timezone.utc)
    repository.initialize(now=observed)
    token = repository.acquire("test-worker", lease_seconds=300, now=observed)
    assert token is not None
    repository.record_task(
        "test-worker",
        token,
        "strength_refresh",
        enabled=enabled,
        status="idle",
        now=observed,
    )
    return repository, token


def test_variant_demand_dedupes_within_write_interval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = tmp_path / "strength-variant-demand"
    first = register_strength_variant_demand(
        _a0_parameters(),
        principal="account:alice",
        root=root,
        now=NOW,
        enqueue=False,
    )
    second = register_strength_variant_demand(
        _a0_parameters(),
        principal="account:alice",
        root=root,
        now=NOW + VARIANT_DEMAND_WRITE_INTERVAL_SECONDS - 1,
        enqueue=False,
    )
    assert first["status"] == "preparing"
    assert second["reused"] is True
    assert second["reason"] == "deduped"
    assert second["parameters_hash"] == first["parameters_hash"]
    assert len(list(root.glob("*.json"))) == 1


def test_variant_demand_expires_after_ttl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = tmp_path / "strength-variant-demand"
    register_strength_variant_demand(
        _a0_parameters(),
        principal="account:alice",
        root=root,
        now=NOW,
        enqueue=False,
    )
    assert list_pending_strength_variant_demands(root=root, now=NOW)
    assert list_pending_strength_variant_demands(
        root=root,
        now=NOW + VARIANT_DEMAND_TTL_SECONDS + 1,
    ) == []
    assert list(root.glob("*.json")) == []


def test_variant_demand_cap_marks_limit_as_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    root = tmp_path / "strength-variant-demand"
    sectors = ("semiconductors", "software", "ai_cloud", "biotech")
    assert len(sectors) == VARIANT_DEMAND_MAX
    for sector_id in sectors:
        accepted = register_strength_variant_demand(
            _a0_parameters(sector_id=sector_id),
            principal="account:alice",
            root=root,
            now=NOW,
            enqueue=False,
        )
        assert accepted["status"] == "preparing"
    overflow = register_strength_variant_demand(
        _a0_parameters(sector_id="healthcare"),
        principal="account:alice",
        root=root,
        now=NOW,
        enqueue=False,
    )
    assert overflow["error_code"] == "variant_demand_limit"
    detail = strength_variant_unavailable_detail(overflow)
    assert detail["code"] == "strength_snapshot_unavailable"
    assert detail["status"] == "failed"
    pending = list_pending_strength_variant_demands(root=root, now=NOW)
    assert len(pending) == VARIANT_DEMAND_MAX
    assert all(item["sector_id"] != "healthcare" for item in pending)


def test_customer_variant_enqueue_reuses_existing_worker_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, token = _init_strength_worker(tmp_path, monkeypatch)
    parameters = _a0_parameters()
    first = enqueue_customer_variant_refresh(parameters, now=NOW)
    second = enqueue_customer_variant_refresh(parameters, now=NOW + 5)
    assert first["status"] == "queued"
    assert first["request_id"]
    assert second["request_id"] == first["request_id"]
    assert second["reused"] is True
    assert second["reason"] in {"idempotent", "already_running"}
    claimed = repository.claim_actions("test-worker", token, "strength_refresh")
    assert len(claimed) == 1
    assert claimed[0]["action_type"] == "strength_variant_refresh"
    assert claimed[0]["task_name"] == "strength_refresh"
    assert claimed[0]["details"]["parameters"]["ranking_algorithm"] == A0_ALGORITHM
    assert claimed[0]["details"]["source"] == "customer_variant"


def test_disabled_strength_task_marks_demand_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_strength_worker(tmp_path, monkeypatch, enabled=False)
    demand = register_strength_variant_demand(
        _a0_parameters(),
        principal="account:alice",
        now=NOW,
    )
    assert demand["error_code"] == "worker_task_disabled"
    assert demand["status"] == "failed"
    detail = strength_variant_unavailable_detail(demand)
    assert detail["code"] == "strength_snapshot_unavailable"


def test_signed_in_customer_cannot_post_owner_strength_refresh(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    set_account_store(AccountStore(tmp_path / "accounts.db"))
    accounts_api.reset_rate_limits()
    app = FastAPI()
    app.state.access_runtime = OwnerAccessRuntime(
        AccessConfig(mode="password"),
        password_hash=hash_owner_password("customer-cannot-refresh"),
    )
    app.include_router(accounts_api.router)
    app.include_router(
        worker_actions.router,
        dependencies=[Depends(require_same_origin_action)],
    )
    headers = {
        "Origin": "https://testserver",
        "Content-Type": "application/json",
        "X-Optix-Action": "1",
        "Sec-Fetch-Site": "same-origin",
    }
    try:
        with TestClient(app, base_url="https://testserver") as client:
            registered = client.post(
                "/api/account/register",
                json={"username": "a0_customer", "password": "fixture-password-for-tests"},
                headers=headers,
            )
            assert registered.status_code == 201
            response = client.post(
                "/api/worker/actions/strength_refresh",
                json={"parameters": dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)},
                headers=headers,
            )
        assert response.status_code == 401
        assert response.json()["detail"]["code"] == "owner_login_required"
    finally:
        set_account_store(None)
        accounts_api.reset_rate_limits()


def test_worker_picks_up_pending_a0_demand_without_scheduled_preheat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    snapshot_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []
    clock = {"now": NOW}

    async def fake_scanner(**kwargs):
        calls.append(kwargs)
        parameters = {key: value for key, value in kwargs.items() if key != "force_refresh"}
        return _payload(parameters=parameters, ticker="A0ROW" if parameters.get("ranking_algorithm") == A0_ALGORITHM else "PROD")

    monkeypatch.setattr(
        strength,
        "get_effective_runtime_settings",
        lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": PRODUCTION_ALGORITHM,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    task = StrengthRefreshTask(
        scanner=fake_scanner,
        snapshot_path=snapshot_path,
        clock=lambda: clock["now"],
    )
    first = asyncio.run(task())
    assert first.status == "idle"
    assert len(calls) == 1
    assert calls[0].get("ranking_algorithm") in {None, PRODUCTION_ALGORITHM}
    register_strength_variant_demand(
        _a0_parameters(),
        principal="account:alice",
        enqueue=False,
    )
    clock["now"] = NOW + 10
    second = asyncio.run(task())
    assert second.status == "idle"
    assert any(call.get("ranking_algorithm") == A0_ALGORITHM for call in calls)
    a0_path = strength._strength_snapshot_path(_a0_parameters(), base_path=snapshot_path)
    assert a0_path.is_file()


def test_signed_in_customer_enqueue_claim_and_read_a0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, token = _init_strength_worker(tmp_path, monkeypatch)
    default_path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "current_request_is_owner", lambda: False)
    monkeypatch.setattr(strength, "request_has_account_session", lambda _req: True)
    monkeypatch.setattr(
        strength,
        "request_account_session",
        lambda _req: SimpleNamespace(user_id="alice"),
    )
    monkeypatch.setattr(strength, "principal_for_request", lambda **_kwargs: "account:alice")
    monkeypatch.setattr(
        strength,
        "get_effective_runtime_settings",
        lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": PRODUCTION_ALGORITHM,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _signed_in_request(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    assert caught.value.status_code == 503
    assert caught.value.detail["code"] == "strength_snapshot_preparing"
    claimed = repository.claim_actions("test-worker", token, "strength_refresh")
    assert claimed
    assert claimed[0]["action_type"] == "strength_variant_refresh"

    async def fake_scanner(**kwargs):
        parameters = {key: value for key, value in kwargs.items() if key != "force_refresh"}
        return _payload(parameters=parameters, ticker="A0ROW")

    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=default_path,
            clock=lambda: NOW,
        ).run_for_actions(claimed)
    )
    assert result.status == "idle"
    published = _rp(
        asyncio.run(
            strength.scan(
                _signed_in_request(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    )
    assert published["rows"][0]["ticker"] == "A0ROW"
    assert published["effective_algorithm"] == A0_ALGORITHM
