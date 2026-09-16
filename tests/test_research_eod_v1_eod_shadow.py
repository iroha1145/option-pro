from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import research_eod_v1
from app.services.research_eod_v1.eod_shadow import (
    NETWORK_COUNTER,
    cache_key,
    intraday_view,
    publish_snapshot,
    research_enabled,
)


def test_research_flag_defaults_off() -> None:
    assert research_enabled() is False


def test_intraday_view_does_not_increment_provider_calls(tmp_path) -> None:
    before = NETWORK_COUNTER["intraday_provider_calls"]
    path = tmp_path / "snap.json"
    payload = publish_snapshot(
        path,
        session_date="2026-09-15",
        config_hash="abc",
        universe_version="u",
        rows=[
            {"security_id": "AAA", "status": "eligible", "score": 90, "sector_context": "software", "algorithm_id": "A_trend_quality"},
            {"security_id": "BBB", "status": "rejected", "score": 99, "sector_context": "software", "algorithm_id": "A_trend_quality"},
        ],
    )
    view = intraday_view(payload, sector_id="software", top_k=5)
    assert view["network_calls"] == 0
    assert view["eligible_count"] == 1
    assert "本次合格 1 / 上限 5" in view["display"]
    assert NETWORK_COUNTER["intraday_provider_calls"] == before
    assert cache_key(session_date="2026-09-15", feature_version="x", config_hash="abc", universe_version="u")


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(research_eod_v1.router)
    return TestClient(app)


def test_refresh_stays_disabled_without_flag() -> None:
    with _client() as client:
        response = client.post("/api/research/eod/v1/refresh", json={})
    assert response.status_code == 409
    body = response.json()
    assert body["network_calls"] == 0
    assert body["production_default_unchanged"] is True
    assert body["status"] == "DISABLED"


def test_concurrent_refresh_never_fetches() -> None:
    import concurrent.futures

    with _client() as client:
        def hit(_: int) -> dict:
            return client.post("/api/research/eod/v1/refresh", json={}).json()

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            bodies = list(pool.map(hit, range(8)))
    assert all(body["network_calls"] == 0 for body in bodies)
    assert all(body["status"] == "DISABLED" for body in bodies)
    assert all(body["production_default_unchanged"] is True for body in bodies)


def test_status_endpoint_exists() -> None:
    with _client() as client:
        response = client.get("/api/research/eod/v1/status")
    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is False
    assert body["production_default_unchanged"] is True
    assert "production" in body["production_algorithms_untouched"]
