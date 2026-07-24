from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.api import breakouts as breakout_api
from app.main import app
from app.services.breakouts.config import BreakoutSettings
from app.services.breakouts.health import (
    assess_breakout_read_state,
    check_breakout_health,
)
from app.services.breakouts.providers.tradingview import TradingViewDiscoveryProvider
from app.services.breakouts.repository import BreakoutRepository


NOW = datetime(2026, 7, 10, 14, 30, tzinfo=timezone.utc)
VERSION_KEYS = {
    "api_schema",
    "provider_schema",
    "feature_version",
    "detector_version",
    "scoring_version",
    "range_persistence_version",
    "market_shape_version",
    "strength_score_version",
    "universe_version",
}
EVENT_KEYS = {
    "event_id",
    "ticker",
    "name",
    "exchange",
    "asset_type",
    "sector",
    "session",
    "setup_type",
    "lifecycle_state",
    "event_at",
    "event_age_seconds",
    "event_price",
    "current_price",
    "session_change_pct",
    "gap_pct",
    "rvol_time_of_day",
    "pivot_price",
    "support_zone",
    "resistance_zone",
    "invalidation_price",
    "intrinsic_strength_score",
    "base_quality_score",
    "breakout_confirmation_score",
    "liquidity_quality_score",
    "chase_risk_score",
    "sector_fit_score",
    "market_fit_score",
    "breakout_quality_score",
    "alert_priority_score",
    "data_confidence_score",
    "range_persistence",
    "range_persistence_slope_5d",
    "range_persistence_ratio_10d",
    "range_persistence_self_percentile",
    "range_persistence_global_percentile",
    "range_persistence_sector_percentile",
    "range_persistence_status",
    "range_persistence_interaction",
    "configured_weights",
    "effective_weights",
    "contribution_breakdown",
    "penalties",
    "missing_components",
    "score_version",
    "market_shape",
    "warnings",
    "source_status",
    "provenance",
    "versions",
}


class _LocalPeerAddress:
    def __init__(self, application) -> None:
        self.application = application

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            scope["client"] = ("127.0.0.1", 50000)
        await self.application(scope, receive, send)


def _client() -> TestClient:
    return TestClient(_LocalPeerAddress(app), base_url="http://localhost")


def _settings(path, *, enabled: bool = True) -> BreakoutSettings:
    return BreakoutSettings(
        _env_file=None,
        BREAKOUT_RADAR_ENABLED=enabled,
        db_path=path,
    )


def _event(event_id: str, ticker: str, at: datetime, priority: float) -> dict:
    return {
        "event_id": event_id,
        "trading_date": at.date(),
        "ticker": ticker,
        "name": f"{ticker} Incorporated",
        "exchange": "NASDAQ",
        "asset_type": "common_stock",
        "sector": "Technology",
        "session": "regular",
        "setup_type": "DAILY_BASE_BREAKOUT",
        "lifecycle_state": "TRIGGERED",
        "previous_state": "WATCHING",
        "transition_reason": "pivot_crossed",
        "event_at": at,
        "first_seen_at": at,
        "last_seen_at": at,
        "event_price": 105.0,
        "pivot_id": f"pivot-{ticker}",
        "source_snapshot_id": "fixture-snapshot",
        "structure": {
            "pivot_price": 100.0,
            "support_zone": {"low": 94.0, "high": 95.0},
            "resistance_zone": {"low": 99.5, "high": 100.0},
            "invalidation_price": 93.5,
        },
        "features": {
            "current_price": 105.2,
            "session_change_pct": 6.1,
            "gap_pct": 2.2,
            "rvol_time_of_day": 2.4,
            "range_persistence": 78.0,
            "range_persistence_slope_5d": 1.2,
            "range_persistence_ratio_10d": 70.0,
            "range_persistence_status": "active",
            "feature_cutoff_at": at,
            "raw_provider_fields": {"secret": "must-not-leak"},
        },
        "scores": {
            "intrinsic_strength_score": 82.0,
            "base_quality_score": 75.0,
            "breakout_confirmation_score": 80.0,
            "liquidity_quality_score": 90.0,
            "chase_risk_score": 30.0,
            "sector_fit_score": None,
            "market_fit_score": None,
            "breakout_quality_score": 79.0,
            "alert_priority_score": priority,
            "data_confidence_score": 88.0,
            "details": {
                "alert_priority": {
                    "effective_weights": {"breakout_quality_score": 0.5},
                    "contribution_breakdown": {"breakout_quality_score": 39.5},
                }
            },
        },
        "data_quality": {
            "discovery_source": "fixture",
            "daily_price_source": "fixture-daily",
            "intraday_price_source": "fixture-intraday",
            "strength_status": "active",
            "market_shape_status": "unavailable",
        },
        "versions": {"feature_version": "breakout-features-v1"},
        "warnings": [],
    }


def _publish(repo: BreakoutRepository, at: datetime, events: list[dict]) -> str:
    scan_id = repo.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=at,
        config_hash="config-v1",
        versions_hash="versions-v1",
        versions={"api_schema": "breakout-api-v1"},
    )
    repo.publish_scan(
        scan_id,
        {
            "provider_snapshot": {
                "provider": "fixture",
                "status": "active",
                "as_of": at,
                "session": "regular",
                "schema_version": "fixture-v1",
                "warnings": [],
                "candidates": [
                    {
                        "ticker": "SECRET",
                        "source": "fixture",
                        "provider_timestamp": at,
                        "raw_provider_fields": {"token": "must-not-leak"},
                    }
                ],
            },
            "events": events,
        },
        now=at,
    )
    return scan_id


def _heartbeat(
    repo: BreakoutRepository,
    at: datetime,
    *,
    status: str = "idle",
    details: dict | None = None,
) -> None:
    repo.update_worker_status(
        "api-test-worker",
        "continuous",
        status,
        heartbeat_at=at,
        details=details,
        now=at,
    )


def _read_statuses(client: TestClient) -> dict[str, dict]:
    return {
        "status": client.get("/api/breakouts/status").json(),
        "current": client.get("/api/breakouts/current").json(),
        "events": client.get("/api/breakouts/events").json(),
    }


def test_disabled_api_does_not_create_database(tmp_path, monkeypatch) -> None:
    path = tmp_path / "disabled.db"
    monkeypatch.setattr(
        breakout_api,
        "get_breakout_settings",
        lambda: _settings(path, enabled=False),
    )
    response = _client().get("/api/breakouts/current")
    assert response.status_code == 200
    assert response.json()["status"] == "disabled"
    assert response.json()["events"] == []
    assert not path.exists()


def test_current_without_completed_scan_keeps_database_source_active(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "empty-current.db"
    BreakoutRepository(path).initialize()
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))

    payload = _client().get(
        "/api/breakouts/current"
    ).json()

    assert payload["status"] == "unavailable"
    assert payload["source_status"]["database"] == "active"
    assert payload["source_status"]["warnings"] == ["no_completed_scan"]


def test_current_reads_only_completed_scan_and_never_calls_provider(tmp_path, monkeypatch) -> None:
    path = tmp_path / "api.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-aapl", "AAPL", NOW, 91.0)])
    repository.begin_scan(
        provider="fixture",
        session="regular",
        scheduled_at=NOW + timedelta(minutes=5),
        config_hash="config-v2",
        versions_hash="versions-v2",
    )
    settings = _settings(path)
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW + timedelta(hours=1))

    async def forbidden_scan(*_args, **_kwargs):
        raise AssertionError("GET API must not call the discovery Provider")

    monkeypatch.setattr(TradingViewDiscoveryProvider, "scan", forbidden_scan)
    response = _client().get("/api/breakouts/current")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "stale"
    assert payload["source_status"]["runtime_reason"] == "completed_snapshot_stale"
    assert [item["ticker"] for item in payload["events"]] == ["AAPL"]
    assert VERSION_KEYS.issubset(payload["versions"])
    assert EVENT_KEYS.issubset(payload["events"][0])
    assert payload["events"][0]["event_age_seconds"] == 3600
    assert datetime.fromisoformat(payload["as_of"].replace("Z", "+00:00")).utcoffset() is not None
    assert (
        datetime.fromisoformat(
            payload["events"][0]["event_at"].replace("Z", "+00:00")
        ).utcoffset()
        is not None
    )
    assert "must-not-leak" not in response.text
    assert "raw_provider_fields" not in response.text


def test_read_endpoints_are_active_with_fresh_worker_and_snapshot(tmp_path, monkeypatch) -> None:
    path = tmp_path / "fresh-read-state.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-fresh", "AAPL", NOW, 91.0)])
    _heartbeat(repository, NOW + timedelta(seconds=30))
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW + timedelta(seconds=60))

    payloads = _read_statuses(_client())

    assert {payload["status"] for payload in payloads.values()} == {"active"}
    assert payloads["current"]["events"][0]["ticker"] == "AAPL"
    assert payloads["events"]["events"][0]["ticker"] == "AAPL"


def test_idle_gap_between_scheduled_scans_stays_active(
    tmp_path,
    monkeypatch,
) -> None:
    """A worker that only heartbeats at scan boundaries is healthy, not stale.

    Regular-session cadence is one scan per scan_interval_regular_seconds
    (default 300s); a heartbeat older than the flat 120s grace but younger
    than interval+grace is the worker sleeping between runs by design.
    """

    path = tmp_path / "idle-gap.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-idle", "AAPL", NOW, 91.0)])
    _heartbeat(repository, NOW)
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW + timedelta(seconds=290))

    payloads = _read_statuses(_client())

    assert {payload["status"] for payload in payloads.values()} == {"active"}
    assert payloads["status"]["worker"]["health_reason"] == "fresh"


def test_stale_worker_heartbeat_marks_reads_stale_without_hiding_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "stale-heartbeat.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    # Snapshot published later than the last heartbeat: the snapshot stays
    # fresh while the heartbeat alone exceeds the session-aware allowance
    # (regular interval 300s + grace 120s).
    _heartbeat(repository, NOW)
    _publish(repository, NOW + timedelta(seconds=400), [
        _event("event-retained", "AAPL", NOW, 91.0)
    ])
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW + timedelta(seconds=421))

    payloads = _read_statuses(_client())

    assert {payload["status"] for payload in payloads.values()} == {"stale"}
    assert payloads["status"]["worker"]["health_reason"] == "worker_heartbeat_stale"
    assert payloads["current"]["source_status"]["runtime_reason"] == "worker_heartbeat_stale"
    assert payloads["current"]["events"][0]["event_id"] == "event-retained"
    assert payloads["events"]["events"][0]["event_id"] == "event-retained"


def test_overdue_completed_snapshot_marks_reads_stale_with_fresh_heartbeat(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "stale-snapshot.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-old", "MSFT", NOW, 88.0)])
    observed_at = NOW + timedelta(seconds=421)
    _heartbeat(repository, observed_at)
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: observed_at)

    payloads = _read_statuses(_client())

    assert {payload["status"] for payload in payloads.values()} == {"stale"}
    assert payloads["status"]["latest_completed_scan"]["freshness_status"] == "stale"
    assert payloads["status"]["latest_completed_scan"]["snapshot_age_seconds"] == 421.0
    assert payloads["current"]["source_status"]["runtime_reason"] == "completed_snapshot_stale"
    assert payloads["events"]["events"][0]["ticker"] == "MSFT"


def test_fresh_snapshot_with_degraded_worker_marks_reads_degraded(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "degraded-read-state.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-degraded", "NVDA", NOW, 86.0)])
    observed_at = NOW + timedelta(seconds=30)
    _heartbeat(repository, observed_at, status="degraded")
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: observed_at)

    payloads = _read_statuses(_client())

    assert {payload["status"] for payload in payloads.values()} == {"degraded"}
    assert payloads["current"]["events"][0]["ticker"] == "NVDA"
    assert payloads["events"]["events"][0]["ticker"] == "NVDA"


def test_market_closed_api_is_paused_and_preserves_latest_completed_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "paused-api.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    next_session = NOW + timedelta(days=3)
    _heartbeat(
        repository,
        NOW,
        status="paused",
        details={
            "runtime_reason": "market_closed",
            "market_session": "closed",
            "next_session_at": next_session,
        },
    )
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW)
    client = _client()

    empty_current = client.get("/api/breakouts/current").json()
    empty_events = client.get("/api/breakouts/events").json()
    status = client.get("/api/breakouts/status").json()
    for payload in (empty_current, empty_events, status):
        assert payload["status"] == "paused"
        assert payload["runtime_status"] == "paused"
        assert payload["runtime_reason"] == "market_closed"
        assert payload["next_session_at"] == next_session.isoformat().replace(
            "+00:00", "Z"
        )
    for payload in (empty_current, empty_events):
        assert payload["market_session"] == "closed"
    # /status reports the live market clock, not the worker's stored memory:
    # NOW (2026-07-10 14:30Z) is 10:30 ET on a trading Friday.
    assert status["market_session"] == "regular"
    assert empty_current["events"] == []
    assert empty_events["events"] == []
    assert empty_current["source_status"]["database"] == "active"
    assert empty_events["source_status"]["database"] == "active"
    assert status["provider_health"] == []

    _publish(repository, NOW - timedelta(hours=1), [_event("event-paused", "AAPL", NOW, 91.0)])
    _heartbeat(
        repository,
        NOW,
        status="paused",
        details={
            "runtime_reason": "market_closed",
            "market_session": "closed",
            "next_session_at": next_session,
        },
    )
    retained = client.get("/api/breakouts/current").json()
    assert retained["status"] == "paused"
    assert retained["events"][0]["event_id"] == "event-paused"
    assert retained["as_of"] is not None


def test_ticker_history_distinguishes_healthy_empty_from_database_failure(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "ticker-empty.db"
    BreakoutRepository(path).initialize()
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    client = _client()

    empty = client.get("/api/breakouts/tickers/AAPL").json()
    assert empty["status"] == "empty"
    assert empty["events"] == []

    missing = tmp_path / "missing.db"
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(missing))
    unavailable = client.get("/api/breakouts/tickers/AAPL").json()
    assert unavailable["status"] == "unavailable"
    assert unavailable["events"] == []


def test_event_cursor_remains_bound_to_original_completed_scan(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cursor.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    first_events = [
        _event("event-aapl", "AAPL", NOW, 90.0),
        _event("event-msft", "MSFT", NOW - timedelta(seconds=1), 80.0),
        _event("event-nvda", "NVDA", NOW - timedelta(seconds=2), 70.0),
    ]
    _publish(repository, NOW, first_events)
    monkeypatch.setattr(
        breakout_api,
        "get_breakout_settings",
        lambda: _settings(path),
    )
    client = _client()
    first_page = client.get("/api/breakouts/events?limit=2").json()
    assert [item["ticker"] for item in first_page["events"]] == ["AAPL", "MSFT"]
    assert first_page["next_cursor"]

    _publish(
        repository,
        NOW + timedelta(minutes=5),
        [_event("event-tsla", "TSLA", NOW + timedelta(minutes=5), 99.0)],
    )
    second_page = client.get(
        "/api/breakouts/events",
        params={"limit": 2, "cursor": first_page["next_cursor"]},
    ).json()
    assert [item["ticker"] for item in second_page["events"]] == ["NVDA"]
    assert second_page["scan_run_id"] == first_page["scan_run_id"]
    changed_filter = client.get(
        "/api/breakouts/events",
        params={
            "limit": 2,
            "ticker": "AAPL",
            "cursor": first_page["next_cursor"],
        },
    )
    assert changed_filter.status_code == 400
    tampered = first_page["next_cursor"][:-1] + (
        "A" if first_page["next_cursor"][-1] != "A" else "B"
    )
    assert client.get(
        "/api/breakouts/events",
        params={"limit": 2, "cursor": tampered},
    ).status_code == 400


def test_event_cursor_uses_bound_snapshot_age_not_latest_scan_age(tmp_path, monkeypatch) -> None:
    path = tmp_path / "cursor-freshness.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(
        repository,
        NOW,
        [
            _event("event-old-a", "AAPL", NOW, 90.0),
            _event("event-old-b", "MSFT", NOW - timedelta(seconds=1), 80.0),
        ],
    )
    _heartbeat(repository, NOW)
    observed = {"at": NOW}
    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: _settings(path))
    monkeypatch.setattr(breakout_api, "_now", lambda: observed["at"])
    client = _client()
    first = client.get("/api/breakouts/events?limit=1").json()
    assert first["status"] == "active"

    latest_at = NOW + timedelta(minutes=10)
    _publish(
        repository,
        latest_at,
        [_event("event-new", "NVDA", latest_at, 99.0)],
    )
    _heartbeat(repository, latest_at)
    observed["at"] = latest_at

    retained_page = client.get(
        "/api/breakouts/events",
        params={"limit": 1, "cursor": first["next_cursor"]},
    ).json()
    current = client.get("/api/breakouts/current").json()

    assert retained_page["status"] == "stale"
    assert retained_page["source_status"]["runtime_reason"] == "completed_snapshot_stale"
    assert retained_page["events"][0]["ticker"] == "MSFT"
    assert current["status"] == "active"
    assert current["events"][0]["ticker"] == "NVDA"


def test_status_and_detail_degrade_without_hiding_contract(tmp_path, monkeypatch) -> None:
    path = tmp_path / "status.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-aapl", "AAPL", NOW, 91.0)])
    repository.record_provider_health(
        {
            "provider": "fixture",
            "status": "stale",
            "consecutive_failures": 2,
            "stale_snapshot_available": True,
            "last_failure_at": NOW,
            "error_code": "timeout",
        }
    )
    monkeypatch.setattr(
        breakout_api,
        "get_breakout_settings",
        lambda: _settings(path),
    )
    client = _client()
    status = client.get("/api/breakouts/status").json()
    assert status["status"] == "stale"
    assert status["database"]["status"] == "active"
    assert status["range_persistence_mode"] == "shadow"
    assert status["market_shape_adapter"]["status"] == "available"

    detail = client.get("/api/breakouts/events/event-aapl")
    assert detail.status_code == 200
    assert detail.json()["event"]["ticker"] == "AAPL"
    assert detail.json()["structure"]["pivot_price"] == 100.0
    assert isinstance(detail.json()["transitions"], list)


def test_local_worker_degradation_is_not_reported_as_a_provider_failure(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "local-degraded.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-local", "AAPL", NOW, 88.0)])
    _heartbeat(
        repository,
        NOW,
        status="degraded",
        details={
            "failure_domain": "local_processing",
            "provider_health_unchanged": True,
        },
    )
    repository.record_provider_health(
        {
            "provider": "fixture",
            "status": "active",
            "consecutive_failures": 0,
            "last_success_at": NOW,
        },
        now=NOW,
    )
    settings = _settings(path)
    runtime = repository.status()
    latest = repository.latest_completed_scan()
    read_state = assess_breakout_read_state(
        settings,
        runtime,
        now=NOW,
        completed_snapshot=latest,
    )
    assert read_state.status == "degraded"
    assert read_state.reason == "local_processing_degraded"
    assert read_state.details["failure_domain"] == "local_processing"

    health = check_breakout_health(settings, repository, now=NOW)
    assert health.status == "degraded"
    assert health.reason == "local_processing_degraded"
    assert health.details["failure_domain"] == "local_processing"
    assert health.details["provider_health_unchanged"] is True

    monkeypatch.setattr(breakout_api, "get_breakout_settings", lambda: settings)
    monkeypatch.setattr(breakout_api, "_now", lambda: NOW)
    payload = _client().get(
        "/api/breakouts/status"
    ).json()
    assert payload["status"] == "degraded"
    assert payload["runtime_reason"] == "local_processing_degraded"
    assert payload["failure_domain"] == "local_processing"
    assert "provider" not in payload["runtime_reason"]


def test_invalid_ticker_and_cursor_are_rejected(tmp_path, monkeypatch) -> None:
    path = tmp_path / "invalid.db"
    repository = BreakoutRepository(path)
    repository.initialize()
    _publish(repository, NOW, [_event("event-aapl", "AAPL", NOW, 91.0)])
    monkeypatch.setattr(
        breakout_api,
        "get_breakout_settings",
        lambda: _settings(path),
    )
    client = _client()
    assert client.get("/api/breakouts/tickers/AAPL%3BDROP").status_code == 400
    assert client.get("/api/breakouts/events?cursor=not-a-cursor").status_code == 400
    assert client.get("/api/breakouts/events?date=2026-99-99").status_code == 422
    assert client.get("/api/breakouts/events?setup_type=NOT_REAL").status_code == 422
    assert client.get("/api/breakouts/events?lifecycle_state=NOT_REAL").status_code == 422
