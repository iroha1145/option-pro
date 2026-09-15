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
from app.services.algorithm_modes import A0_ALGORITHM, PRODUCTION_ALGORITHM, T1_ALGORITHM
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.repository import BreakoutRepository
from app.services.view_preferences import ViewPreferenceStore, normalize_view_preferences
from tests.http_response_support import anonymous_get_request as _areq, response_payload as _rp
from tests.test_breakout_api_contract import _client, _event, _heartbeat, _publish
from tests.test_strength_worker_snapshot import NOW, _payload


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


def test_strength_scan_adds_compatible_algorithm_fields_on_default_path(
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
            )
        )
    )
    assert result["rows"][0]["ticker"] == "AAPL"
    assert result["effective_algorithm"] == PRODUCTION_ALGORITHM
    assert result["algorithm_version"] == "strength-v3"
    assert result["score_basis"] == "ranking_score"
    assert strength._strength_snapshot_path(dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)) == path


def test_a0_snapshot_uses_a_separate_identity(
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
            strength.scan(
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


def test_explicit_a0_conflict_returns_400() -> None:
    with pytest.raises(HTTPException) as caught:
        asyncio.run(
            strength.scan(
                _areq(),
                universe="themes",
                timeframe="mid",
                profile="balanced",
                top=20,
                sector_id=None,
                min_price=5.0,
                min_avg_dollar_volume=10_000_000.0,
                ranking_algorithm=A0_ALGORITHM,
            )
        )
    assert caught.value.status_code == 400
    assert caught.value.detail["code"] == "algorithm_view_conflict"


def test_overlay_keeps_scanner_fallback_instead_of_relabeling_a0() -> None:
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


def test_scheduled_strength_refresh_does_not_shadow_scan_a0_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength
    from app.worker.tasks import StrengthRefreshTask

    snapshot_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []

    async def fake_scanner(**kwargs):
        calls.append(kwargs)
        parameters = {
            key: value
            for key, value in kwargs.items()
            if key != "force_refresh"
        }
        return _payload(parameters=parameters)

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
    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=snapshot_path,
            clock=lambda: NOW,
        )()
    )
    assert result.status == "idle"
    assert len(calls) == 1
    assert calls[0].get("ranking_algorithm") in {None, PRODUCTION_ALGORITHM}
    assert "ranking_algorithm" not in strength.normalize_strength_scan_parameters(
        dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)
    )


def test_admin_a0_default_preheats_companion_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.worker.tasks import StrengthRefreshTask

    snapshot_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []

    async def fake_scanner(**kwargs):
        calls.append(kwargs)
        parameters = {
            key: value
            for key, value in kwargs.items()
            if key != "force_refresh"
        }
        return _payload(parameters=parameters)

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
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=snapshot_path,
            clock=lambda: NOW,
        )()
    )
    algorithms = [item.get("ranking_algorithm") for item in calls]
    assert None in algorithms or PRODUCTION_ALGORITHM in algorithms
    assert A0_ALGORITHM in algorithms


def test_owner_default_refresh_preheats_a0_when_admin_default_is_a0(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api import strength
    from app.worker.tasks import StrengthRefreshTask

    snapshot_path = tmp_path / "strength-snapshot-v1.json"
    calls: list[dict] = []

    async def fake_scanner(**kwargs):
        calls.append(kwargs)
        parameters = {
            key: value
            for key, value in kwargs.items()
            if key != "force_refresh"
        }
        return _payload(parameters=parameters)

    monkeypatch.setattr(
        "app.api.strength.get_effective_runtime_settings",
        lambda: type(
            "Settings",
            (),
            {
                "algorithms": type(
                    "Algos",
                    (),
                    {
                        "screener_ranking_algorithm": A0_ALGORITHM,
                        "radar_sort_algorithm": PRODUCTION_ALGORITHM,
                    },
                )()
            },
        )(),
    )
    result = asyncio.run(
        StrengthRefreshTask(
            scanner=fake_scanner,
            snapshot_path=snapshot_path,
            clock=lambda: NOW,
        ).run_for_actions(
            [
                {
                    "request_id": "act_default",
                    "details": {"parameters": dict(strength.DEFAULT_STRENGTH_SCAN_PARAMETERS)},
                }
            ]
        )
    )
    assert result.status == "idle"
    assert any(item.get("ranking_algorithm") == A0_ALGORITHM for item in calls)


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
    assert resolution.effective == PRODUCTION_ALGORITHM
    assert resolution.source == "user_preference"
