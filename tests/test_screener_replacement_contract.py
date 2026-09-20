from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import strength
from app.services.algorithm_modes import EOD_LIMITED_V1, resolve_radar_algorithm
from app.services.runtime_settings import (
    RuntimeAlgorithmSettingsPatch, RuntimeSettingsPatch, RuntimeSettingsStore,
    get_effective_runtime_settings,
)
from app.services.strength import variant_demand
from app.worker.tasks import StrengthRefreshTask
from tests.test_eod_refresh_batching import success
from tests.test_worker_actions_api import _client, _live_repository


@pytest.mark.parametrize("algorithm", [None, "production", "a0_mid_long", "follow_default", "eod_limited_v1", "original", "legacy", "strength-v3", "a0"])
@pytest.mark.parametrize("timeframe,expected", [(None, "mid"), ("all", "mid"), ("short", "short"), ("mid", "mid"), ("long", "long")])
def test_old_scan_requests_execute_only_new_engine(monkeypatch, algorithm, timeframe, expected):
    calls = []

    def eod_snapshot(*, parameters, list_kind, resolution):
        calls.append(parameters)
        return strength._overlay_algorithm_metadata({"rows": [], "count": 0}, resolution), 1_800_000_000.0, False

    def legacy_read(*args, **kwargs):
        raise AssertionError("A current scan must not read a retired ranking snapshot")

    monkeypatch.setattr(strength, "_read_eod_limited_snapshot", eod_snapshot)
    monkeypatch.setattr(strength, "_read_strength_snapshot", legacy_read)
    app = FastAPI()
    app.include_router(strength.router)
    query = {}
    if algorithm is not None:
        query["ranking_algorithm"] = algorithm
    if timeframe is not None:
        query["timeframe"] = timeframe
    response = TestClient(app).get("/api/strength/scan", params=query)
    assert response.status_code == 200
    assert response.json()["effective_algorithm"] == EOD_LIMITED_V1
    assert response.json()["resolved_timeframe"] == expected
    assert calls[0]["ranking_algorithm"] == EOD_LIMITED_V1
    assert calls[0]["timeframe"] == expected


@pytest.mark.parametrize("algorithm", [None, "production", "a0_mid_long", "follow_default", "eod_limited_v1"])
def test_old_refresh_requests_queue_current_parameters_and_hash(tmp_path, monkeypatch, algorithm):
    _live_repository(tmp_path, monkeypatch)
    raw = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    if algorithm is not None:
        raw["ranking_algorithm"] = algorithm
    response = _client().post("/api/worker/actions/strength_refresh", json={"parameters": raw})
    assert response.status_code == 202
    expected = strength.scheduled_strength_scan_parameters()
    details = response.json()["details"]
    assert details["parameters"] == expected
    assert details["parameters_hash"] == strength.strength_scan_parameters_hash(expected)


@pytest.mark.parametrize("algorithm", [None, "production", "a0_mid_long", "eod_limited_v1"])
def test_worker_direct_requests_never_execute_old_math(algorithm):
    raw = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    if algorithm is not None:
        raw["ranking_algorithm"] = algorithm
    calls = []

    def old_scanner(**kwargs):
        raise AssertionError("Retired ranking mathematics must not execute")

    def eod(**kwargs):
        calls.append(kwargs)
        return success()

    result = asyncio.run(StrengthRefreshTask(scanner=old_scanner, eod_runner=eod)._run(raw))
    assert result.status == "idle"
    assert calls == [{"profile": "balanced", "horizon": "mid", "purpose": "live_eod_inference", "all_variants": True}]
    expected = strength.strength_execution_parameters(raw)
    assert result.details["parameters"] == expected
    assert result.details["parameters_hash"] == strength.strength_scan_parameters_hash(expected)


def test_old_queued_hashes_are_checked_before_aliases_merge(monkeypatch):
    old = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    a0 = {**old, "ranking_algorithm": "a0_mid_long"}
    expected = strength.strength_execution_parameters(old)
    monkeypatch.setattr(variant_demand, "list_pending_strength_variant_demands", lambda: [])
    cleared = []
    monkeypatch.setattr(variant_demand, "complete_strength_variant_demand", lambda parameters, **kwargs: cleared.append(parameters))
    calls = []

    def eod(**kwargs):
        calls.append(kwargs)
        return success()

    queued = [{"request_id": label, "details": {
        "parameters": params, "parameters_hash": strength.strength_scan_parameters_hash(params),
    }} for label, params in [("old", old), ("a0", a0), ("new", expected)]]
    queued.append({"request_id": "tampered", "details": {
        "parameters": old, "parameters_hash": strength.strength_scan_parameters_hash(expected),
    }})
    result = asyncio.run(StrengthRefreshTask(eod_runner=eod).run_for_actions(queued))
    completions = {entry["request_id"]: entry for entry in result.details["action_completions"]}
    assert len(calls) == 1
    assert completions["tampered"]["succeeded"] is False
    assert completions["tampered"]["error_code"] == "invalid_parameters"
    for label in ("old", "a0", "new"):
        assert completions[label]["succeeded"] is True
        assert completions[label]["parameters"] == expected
        assert completions[label]["parameters_hash"] == strength.strength_scan_parameters_hash(expected)
    assert old in cleared and a0 in cleared and expected in cleared


@pytest.mark.parametrize("choice", ["production", "a0_mid_long", "eod_limited_v1"])
def test_runtime_preferences_are_read_without_migration(tmp_path, choice):
    path = tmp_path / "runtime-settings.json"
    store = RuntimeSettingsStore(path)
    document = store.update(RuntimeSettingsPatch(algorithms=RuntimeAlgorithmSettingsPatch(
        screener_ranking_algorithm=choice,
    )), expected_version=1)
    before = path.read_bytes() if path.exists() else None
    effective = get_effective_runtime_settings(store)
    assert effective.algorithms.screener_ranking_algorithm == choice
    assert store.read().version == document.version
    assert (path.read_bytes() if path.exists() else None) == before
    assert not (tmp_path / "screener-default-to-eod-limited-v1.json").exists()
    assert strength.scheduled_strength_scan_parameters(effective) == strength.strength_execution_parameters(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS))


def test_historical_parameter_hash_and_radar_contract_stay_unchanged(tmp_path):
    old = dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    assert "ranking_algorithm" not in strength.normalize_strength_scan_parameters(old)
    assert strength.strength_scan_parameters_hash(old) == "7f79249ba54b313847de"
    base = tmp_path / "strength-snapshot-v1.json"
    assert strength._strength_snapshot_path(old, base_path=base) == base
    assert strength.strength_scan_parameters_hash(strength.strength_execution_parameters(old)) != strength.strength_scan_parameters_hash(old)
    assert resolve_radar_algorithm(requested="production").effective == "production"
    assert resolve_radar_algorithm(requested="t1_daily_priority").effective == "t1_daily_priority"
