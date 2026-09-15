from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import strength
from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM
from app.services.strength.variant_demand import (
    list_pending_strength_variant_demands,
    register_strength_variant_demand,
)
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
