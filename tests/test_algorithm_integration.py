from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.api import breakouts as breakout_api
from app.api import strength
from app.api import view_preferences as view_preferences_api
from app.services.algorithm_modes import A0_ALGORITHM, EOD_LIMITED_V1, PRODUCTION_ALGORITHM, T1_ALGORITHM
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.repository import BreakoutRepository
from app.services.view_preferences import ViewPreferenceStore, normalize_view_preferences
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp
from tests.test_breakout_api_contract import _client, _event, _heartbeat, _publish
from tests.test_strength_worker_snapshot import NOW, _payload
from tests.legacy_strength_support import read_legacy_snapshot


@pytest.fixture
def eod_snapshot(tmp_path, monkeypatch):
    from app.services.eod_limited import store
    from tests.test_eod_limited_product import _scored
    monkeypatch.setattr(store, "snapshot_dir", lambda root=None: tmp_path / "eod-limited-v1")
    outcome = store.publish_batch({
        "purpose": "historical_example", "served_session": "2024-06-28",
        "variants": {store.variant_key("balanced", "mid"): _scored()},
    })
    assert outcome["ok"] is True


def test_view_preferences_keep_explicit_original_choices(tmp_path: Path) -> None:
    store = ViewPreferenceStore(tmp_path / "view-preferences.json")
    saved = store.write(
        "account:alice",
        normalize_view_preferences(
            {
                "screener_ranking_algorithm": PRODUCTION_ALGORITHM,
                "radar_sort_algorithm": PRODUCTION_ALGORITHM,
            }
        ),
    )
    assert saved.screener_ranking_algorithm == PRODUCTION_ALGORITHM
    other = store.read("account:bob")
    assert other.screener_ranking_algorithm == "follow_default"
    again = store.read("account:alice")
    assert again.screener_ranking_algorithm == PRODUCTION_ALGORITHM
    assert again.radar_sort_algorithm == PRODUCTION_ALGORITHM


def test_view_preferences_api_requires_login_to_write() -> None:
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(view_preferences_api.router)
    client = TestClient(app, base_url="http://localhost")
    response = client.put(
        "/api/view-preferences",
        json={"screener_ranking_algorithm": "a0_mid_long"},
        headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
    )
    assert response.status_code == 401


def test_strength_scan_replaces_existing_default_snapshot(
    eod_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "strength-snapshot-v1.json"
    strength._write_strength_snapshot(
        path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(),
        saved_at=NOW - 10,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    result = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=PRODUCTION_ALGORITHM,
            )
        )
    )
    assert result["rows"][0]["ticker"] == "NVDA"
    assert result["effective_algorithm"] == EOD_LIMITED_V1
    assert result["algorithm_version"] == "eod-limited-v1.5"
    assert result["score_basis"] == "price_only_diagnostic + m1_consensus"
    assert strength._strength_snapshot_path(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)) == path


def test_legacy_a0_snapshot_retains_a_separate_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_path = tmp_path / "strength-snapshot-v1.json"
    a0_parameters = strength.a0_companion_scan_parameters()
    a0_path = strength._strength_snapshot_path(a0_parameters, base_path=default_path)
    assert a0_path != default_path
    strength._write_strength_snapshot(
        a0_path,
        parameters=a0_parameters,
        payload=_payload(parameters=a0_parameters, ticker="MSFT"),
        saved_at=NOW - 10,
        base_path=default_path,
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    result = _rp(
        asyncio.run(
            read_legacy_snapshot(
                _areq(),
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
    assert result["rows"][0]["ticker"] == "MSFT"
    assert result["effective_algorithm"] == A0_ALGORITHM


def test_explicit_a0_mid_request_uses_replacement_snapshot(eod_snapshot) -> None:
    result = _rp(asyncio.run(strength.scan(
        _areq(), universe="themes", timeframe="mid", profile="balanced", top=20,
        sector_id=None, min_price=5.0, min_avg_dollar_volume=10_000_000.0,
        ranking_algorithm=A0_ALGORITHM,
    )))
    assert result["effective_algorithm"] == EOD_LIMITED_V1
    assert result["rows"][0]["ticker"] == "NVDA"


def test_historical_overlay_keeps_scanner_fallback_instead_of_relabeling_a0() -> None:
    payload = {
        "effective_algorithm": PRODUCTION_ALGORITHM,
        "fallback_reason": "a0_scores_unavailable",
        "algorithm_version": "strength-v3",
        "score_basis": "ranking_score",
    }
    resolution = strength.resolve_screener_algorithm(
        requested=A0_ALGORITHM,
        timeframe="all",
        profile="balanced",
        explicit_request=True,
    )
    from dataclasses import replace
    resolution = replace(resolution, effective=A0_ALGORITHM)
    overlay = strength._overlay_algorithm_metadata(payload, resolution)
    assert overlay["effective_algorithm"] == PRODUCTION_ALGORITHM
    assert overlay["fallback_reason"] == "a0_scores_unavailable"
    assert overlay["requested_algorithm"] == A0_ALGORITHM


def test_radar_current_and_events_apply_t1_after_full_set_sort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=True,
        db_path=tmp_path / "breakouts.db",
    )
    repo = BreakoutRepository(settings.db_path)
    repo.initialize()
    at = datetime(2026, 9, 14, 20, 0, tzinfo=timezone.utc)
    first = _event("unmet-late", "AAA", at, 99.0)
    first["features"]["t1_priority"] = {"status": "unmet"}
    second = _event("met-early", "BBB", at.replace(hour=19), 40.0)
    second["features"]["t1_priority"] = {"status": "met"}
    third = _event("unmet-older", "CCC", at.replace(day=13), 80.0)
    third["features"]["t1_priority"] = {"status": "unmet"}
    _publish(repo, at, [first, second, third])
    _heartbeat(repo, at)
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(breakout_api, "_now", lambda: at)
    client = _client()

    production = client.get(
        "/api/breakouts/current",
        params={"sort_algorithm": PRODUCTION_ALGORITHM},
    ).json()
    assert [event["event_id"] for event in production["events"]] == [
        "unmet-late",
        "met-early",
        "unmet-older",
    ]
    assert production["effective_algorithm"] == PRODUCTION_ALGORITHM

    boosted = client.get(
        "/api/breakouts/current",
        params={"sort_algorithm": T1_ALGORITHM},
    ).json()
    assert [event["event_id"] for event in boosted["events"]] == [
        "met-early",
        "unmet-late",
        "unmet-older",
    ]
    assert boosted["effective_algorithm"] == T1_ALGORITHM
    assert {event["lifecycle_state"] for event in boosted["events"]} == {"TRIGGERED"}
    assert boosted["events"][0]["t1_status"] == "met"

    page = client.get(
        "/api/breakouts/events",
        params={"sort_algorithm": T1_ALGORITHM, "limit": 2},
    ).json()
    assert [event["event_id"] for event in page["events"]] == ["met-early", "unmet-late"]
    assert page["next_cursor"]
    more = client.get(
        "/api/breakouts/events",
        params={
            "sort_algorithm": T1_ALGORITHM,
            "limit": 2,
            "cursor": page["next_cursor"],
        },
    ).json()
    assert [event["event_id"] for event in more["events"]] == ["unmet-older"]
    assert more["next_cursor"] is None

    production_page = client.get(
        "/api/breakouts/events",
        params={"sort_algorithm": PRODUCTION_ALGORITHM, "limit": 2},
    ).json()
    assert [event["event_id"] for event in production_page["events"]] == [
        "unmet-late",
        "met-early",
    ]
    production_more = client.get(
        "/api/breakouts/events",
        params={
            "sort_algorithm": PRODUCTION_ALGORITHM,
            "limit": 2,
            "cursor": production_page["next_cursor"],
        },
    ).json()
    assert [event["event_id"] for event in production_more["events"]] == ["unmet-older"]


@pytest.mark.parametrize("admin_choice", [PRODUCTION_ALGORITHM, A0_ALGORITHM, EOD_LIMITED_V1])
def test_every_admin_default_schedules_only_current_engine(tmp_path, monkeypatch, admin_choice):
    from app.worker.tasks import StrengthRefreshTask
    from tests.test_eod_refresh_batching import success
    calls = []
    monkeypatch.setattr(strength, "get_effective_runtime_settings", lambda: {
        "algorithms": {"screener_ranking_algorithm": admin_choice},
    })

    def old_scanner(**kwargs):
        raise AssertionError("The retired scanner must not execute")

    def eod_runner(**kwargs):
        calls.append(kwargs)
        return success()

    result = asyncio.run(StrengthRefreshTask(scanner=old_scanner, eod_runner=eod_runner)())
    assert result.status == "idle"
    assert calls == [{"profile": "balanced", "horizon": "mid", "purpose": "live_eod_inference", "all_variants": True}]
    assert result.details["parameters"]["ranking_algorithm"] == EOD_LIMITED_V1
    assert strength.a0_companion_for_admin_default() is None


def test_saved_user_production_is_not_overwritten_by_admin_a0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    store = ViewPreferenceStore(tmp_path / "view-preferences.json")
    store.write(
        "account:alice",
        normalize_view_preferences({"screener_ranking_algorithm": PRODUCTION_ALGORITHM}),
    )
    monkeypatch.setattr(strength, "get_view_preference_store", lambda: store)
    monkeypatch.setattr(
        strength,
        "principal_for_request",
        lambda **_kwargs: "account:alice",
    )
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
                        "screener_ranking_algorithm": A0_ALGORITHM,
                        "radar_sort_algorithm": T1_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    request = Request({"type": "http", "headers": []})
    resolution = strength._request_screener_resolution(
        request,
        requested=None,
        timeframe="all",
        profile="balanced",
    )
    assert resolution.effective == EOD_LIMITED_V1
    assert resolution.source == "user_preference"


def test_damaged_preference_file_falls_back_to_the_admin_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from starlette.requests import Request

    damaged = ViewPreferenceStore(tmp_path / "view-preferences.json")
    damaged.path.write_text("{not json", encoding="utf-8")
    admin_settings = type(
        "Settings",
        (),
        {
            "algorithms": type(
                "Algos",
                (),
                {
                    "screener_ranking_algorithm": A0_ALGORITHM,
                    "radar_sort_algorithm": T1_ALGORITHM,
                },
            )()
        },
    )()
    for module in (strength, breakout_api):
        monkeypatch.setattr(module, "get_view_preference_store", lambda: damaged)
        monkeypatch.setattr(module, "principal_for_request", lambda **_kwargs: "account:alice")
        monkeypatch.setattr(module, "get_effective_runtime_settings", lambda: admin_settings)
    request = Request({"type": "http", "headers": []})

    screener = strength._request_screener_resolution(
        request,
        requested=None,
        timeframe="all",
        profile="balanced",
    )
    radar = breakout_api.resolve_radar_for_request(request)

    assert screener.source == "admin_default"
    assert radar.effective == T1_ALGORITHM
    assert radar.source == "admin_default"


def test_explicit_follow_default_and_saved_production_use_same_new_engine(
    eod_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    default_path = tmp_path / "strength-snapshot-v1.json"
    a0_parameters = strength.a0_companion_scan_parameters()
    a0_path = strength._strength_snapshot_path(a0_parameters, base_path=default_path)
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    strength._write_strength_snapshot(
        a0_path,
        parameters=a0_parameters,
        payload=_payload(parameters=a0_parameters, ticker="A0ROW"),
        saved_at=NOW - 10,
        base_path=default_path,
    )
    store = ViewPreferenceStore(tmp_path / "view-preferences.json")
    store.write(
        "account:alice",
        normalize_view_preferences({"screener_ranking_algorithm": PRODUCTION_ALGORITHM}),
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "get_view_preference_store", lambda: store)
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
                        "screener_ranking_algorithm": A0_ALGORITHM,
                        "radar_sort_algorithm": T1_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    omitted = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
            )
        )
    )
    assert omitted["rows"][0]["ticker"] == "NVDA"
    followed = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm="follow_default",
            )
        )
    )
    assert followed["rows"][0]["ticker"] == "NVDA"
    assert followed["effective_algorithm"] == EOD_LIMITED_V1
    assert followed["requested_algorithm"] == "follow_default"


def test_failed_preference_put_does_not_change_follow_default_scan(
    eod_snapshot,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI

    default_path = tmp_path / "strength-snapshot-v1.json"
    a0_parameters = strength.a0_companion_scan_parameters()
    a0_path = strength._strength_snapshot_path(a0_parameters, base_path=default_path)
    strength._write_strength_snapshot(
        default_path,
        parameters=dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS),
        payload=_payload(ticker="PROD"),
        saved_at=NOW - 10,
    )
    strength._write_strength_snapshot(
        a0_path,
        parameters=a0_parameters,
        payload=_payload(parameters=a0_parameters, ticker="A0ROW"),
        saved_at=NOW - 10,
        base_path=default_path,
    )
    store = ViewPreferenceStore(tmp_path / "view-preferences.json")
    store.write(
        "account:alice",
        normalize_view_preferences({"screener_ranking_algorithm": PRODUCTION_ALGORITHM}),
    )
    monkeypatch.setattr(strength, "_STRENGTH_SNAPSHOT_PATH", default_path)
    monkeypatch.setattr(strength.time, "time", lambda: NOW)
    monkeypatch.setattr(strength, "get_view_preference_store", lambda: store)
    monkeypatch.setattr(view_preferences_api, "get_view_preference_store", lambda: store)
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
                        "screener_ranking_algorithm": A0_ALGORITHM,
                        "radar_sort_algorithm": T1_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    app = FastAPI()
    app.include_router(view_preferences_api.router)
    with TestClient(app, base_url="http://localhost") as client:
        failed_put = client.put(
            "/api/view-preferences",
            json={"screener_ranking_algorithm": "follow_default"},
            headers={"Origin": "http://localhost", "X-Optix-Action": "1"},
        )
    assert failed_put.status_code == 401
    assert store.read("account:alice").screener_ranking_algorithm == PRODUCTION_ALGORITHM
    followed = _rp(
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="all",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm="follow_default",
            )
        )
    )
    assert followed["rows"][0]["ticker"] == "NVDA"
    assert followed["effective_algorithm"] == EOD_LIMITED_V1
    assert followed["requested_algorithm"] == "follow_default"
