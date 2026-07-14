from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from app.services.catalysts.models import (
    CalendarResponse,
    HotspotPreparationItem,
    HotspotPreparationResponse,
    HotspotPreparationStatus,
    MarketFocusCycleEnvelope,
    RemoteMarketFocusCycle,
)
from app.services.catalysts.errors import CatalystRepositoryError
from app.services.catalysts.repository import CatalystRepository
from app.services.catalysts.sync_service import CatalystSyncService
from test_catalyst_api import client_for, configured
from test_catalyst_sync_worker import SCHEMA_SHA, settings
from catalyst_support import catalyst_item, utc


def hotspot_status(*, revision: int = 7) -> HotspotPreparationStatus:
    return HotspotPreparationStatus(
        schema_sha256=SCHEMA_SHA,
        request_id="request-hotspot-status",
        prepared_revision=revision,
        last_consumed_revision=4,
        prepared_hot_count=1,
        prepared_since=utc(9),
        last_cycle_at=utc(8),
        next_scheduled_at=utc(12),
        active_cycle_id=None,
        cooldown_until=None,
        manual_enabled=True,
        capability="enabled",
        model="gpt-5.6-terra",
        reasoning="max",
        data_through=utc(10),
    )


def hotspot_item(*, revision: int = 7) -> HotspotPreparationItem:
    return HotspotPreparationItem(
        prepared_revision=revision,
        event_group_id="evt_fixture_1",
        event_group_version=2,
        gate_version="hotspot-bootstrap-v1",
        hot_score=82.5,
        component_scores={
            "severity": 90.0,
            "focus_relevance": 75.0,
            "market_confirmation": None,
        },
        active_weights={"severity": 0.6, "focus_relevance": 0.4},
        reasons=["hard_event"],
        event_snapshot_json='{"event_group_id":"evt_fixture_1"}',
        status="PREPARED",
        prepared_at=utc(9),
        leased_cycle_id=None,
        consumed_cycle_id=None,
        consumed_at=None,
        created_at=utc(9),
        representative_title="A bounded fixture event",
        event_type="regulatory",
        available_at=utc(9),
        first_published_at=utc(8),
        last_published_at=utc(9),
        source_count=2,
        source_names=["Fixture Wire", "Second Wire"],
        validated_tickers=["NVDA"],
    )


def remote_cycle(
    *,
    status: str = "queued",
    revision: int = 7,
    result: dict | None = None,
    retry_of_cycle_id: str | None = None,
    execution_number: int = 1,
    cycle_id: str = "mfc_" + "a" * 32,
    error_code: str | None = None,
) -> RemoteMarketFocusCycle:
    return RemoteMarketFocusCycle.model_validate(
        {
            "cycle_id": cycle_id,
            "scheduled_slot": None,
            "idempotency_key": f"manual:{revision}",
            "retry_of_cycle_id": retry_of_cycle_id,
            "execution_number": execution_number,
            "trigger_type": "manual",
            "status": status,
            "no_new_hot_events": False,
            "prepared_revision": revision,
            "last_consumed_revision_at_start": 4,
            "consumes_through_revision": revision,
            "focus_revision": 3,
            "snapshot_as_of": utc(10),
            "input_schema_version": "market-focus-schema-v1",
            "input_hash": "b" * 64,
            "event_group_count": 1,
            "focus_symbol_count": 1,
            "provider": "openai",
            "model": "gpt-5.6-terra",
            "reasoning_effort": "max",
            "execution_mode": "background",
            "max_output_tokens": 49152,
            "prompt_version": "market-focus-v1",
            "output_schema_version": "market-focus-schema-v1",
            "result": result,
            "error_code": error_code,
            "attempt_count": 1,
            "retrieve_error_count": 0,
            "cancel_attempt_count": 0,
            "next_attempt_at": utc(10, 1),
            "cancel_requested_at": None,
            "latency_ms": None,
            "usage_input_tokens": 0,
            "usage_cached_input_tokens": 0,
            "usage_cache_write_tokens": 0,
            "usage_reasoning_tokens": 0,
            "usage_output_tokens": 0,
            "usage_total_tokens": 0,
            "created_at": utc(10),
            "started_at": None,
            "completed_at": None,
            "updated_at": utc(10),
        }
    )


class FocusClient:
    def __init__(self) -> None:
        self.status_calls = 0

    async def hotspot_status(self):
        self.status_calls += 1
        return hotspot_status()

    async def hotspots(self, **_kwargs):
        return HotspotPreparationResponse(
            schema_sha256=SCHEMA_SHA,
            request_id="request-hotspot-items",
            as_of=utc(10),
            items=[hotspot_item()],
        )

    async def latest_market_focus_cycle(self):
        return MarketFocusCycleEnvelope(
            schema_sha256=SCHEMA_SHA,
            request_id="request-focus-latest",
            cycle=remote_cycle(),
        )


class RetryFocusClient:
    def __init__(self, parent_remote_cycle_id: str) -> None:
        self.parent_remote_cycle_id = parent_remote_cycle_id
        self.create_calls: list[dict] = []

    async def create_market_focus_cycle(self, **kwargs):
        self.create_calls.append(kwargs)
        return MarketFocusCycleEnvelope(
            schema_sha256=SCHEMA_SHA,
            request_id="request-focus-retry",
            cycle=remote_cycle(
                cycle_id="mfc_" + "b" * 32,
                retry_of_cycle_id=self.parent_remote_cycle_id,
                execution_number=2,
            ),
        )


class CalendarClient:
    def __init__(self) -> None:
        self.calls = 0

    async def calendar(self, **_kwargs):
        self.calls += 1
        return CalendarResponse(
            schema_sha256=SCHEMA_SHA,
            request_id="request-calendar-sync",
            as_of=utc(10),
            data_through=utc(10),
            items=[],
        )

def test_market_focus_models_reject_unknown_remote_fields() -> None:
    payload = remote_cycle().model_dump(mode="json")
    payload["openai_response_id"] = "must-not-cross-service-boundary"
    with pytest.raises(ValidationError):
        RemoteMarketFocusCycle.model_validate(payload)


def test_calendar_sync_keeps_its_independent_remote_publish_path(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    client = CalendarClient()
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-calendar",
        clock=lambda: utc(10),
    )
    assert service.acquire()
    assert asyncio.run(service.sync_calendar())
    assert client.calls == 1
    state = repository.sync_state("calendar")
    assert state["last_success_at"] == "2026-07-11T10:00:00Z"
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT status FROM catalyst_sync_runs WHERE stream='calendar'"
        ).fetchone()[0] == "completed"


def test_worker_atomically_publishes_hotspots_and_opaque_cycle_id(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    client = FocusClient()
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        client,  # type: ignore[arg-type]
        worker_id="worker-focus",
        clock=lambda: utc(10),
    )
    assert service.acquire()
    assert asyncio.run(service.sync_market_focus())
    assert client.status_calls == 2

    snapshot = repository.market_focus_snapshot(
        stale_ttl_seconds=3600, now=utc(10, 1)
    )
    assert snapshot["status"] == "active"
    assert snapshot["hotspot_status"]["prepared_revision"] == 7
    assert snapshot["items"][0]["event_group_id"] == "evt_fixture_1"
    assert snapshot["cycle"]["cycle_id"].startswith("mfc_")
    assert snapshot["cycle"]["cycle_id"] != "mfc_" + "a" * 32
    assert "openai_response_id" not in str(snapshot)


def test_same_prepared_revision_is_one_local_persistent_job(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first = repository.enqueue_market_focus_cycle(
        7,
        last_consumed_revision=4,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    second = repository.enqueue_market_focus_cycle(
        7,
        last_consumed_revision=4,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )
    assert first["cycle_id"] == second["cycle_id"]
    assert first["cycle_id"].startswith("mfc_")
    with repository.open_read_connection() as connection:
        assert connection.execute(
            "SELECT count(*) FROM catalyst_market_focus_jobs"
        ).fetchone()[0] == 1


def test_same_prepared_revision_starts_next_batch_after_watermark_advances(
    tmp_path,
) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    first = repository.enqueue_market_focus_cycle(
        12,
        last_consumed_revision=4,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    second = repository.enqueue_market_focus_cycle(
        12,
        last_consumed_revision=8,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10, 1),
    )

    assert first["cycle_id"] != second["cycle_id"]
    with repository.open_read_connection() as connection:
        keys = connection.execute(
            "SELECT request_key FROM catalyst_market_focus_jobs ORDER BY created_at"
        ).fetchall()
    assert [row[0] for row in keys] == ["batch:12:4", "batch:12:8"]


def test_retry_job_preserves_remote_parent_and_is_locally_idempotent(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    parent = repository.enqueue_market_focus_cycle(
        7,
        last_consumed_revision=4,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    claimed = repository.due_market_focus_jobs(
        "worker-retry", now=utc(10, 1)
    )
    assert [job["local_cycle_id"] for job in claimed] == [parent["cycle_id"]]
    assert repository.begin_market_focus_submission(
        parent["cycle_id"], "worker-retry", now=utc(10, 1)
    )
    failed_remote = remote_cycle(status="failed")
    repository.apply_remote_market_focus_cycle(
        parent["cycle_id"],
        failed_remote,
        worker_id="worker-retry",
        now=utc(10, 2),
    )

    first_retry = repository.enqueue_market_focus_retry(
        parent["cycle_id"], now=utc(10, 3)
    )
    replay = repository.enqueue_market_focus_retry(
        parent["cycle_id"], now=utc(10, 4)
    )

    assert first_retry["cycle_id"] == replay["cycle_id"]
    assert first_retry["cycle_id"] != parent["cycle_id"]
    assert first_retry["retry_of_cycle_id"] == parent["cycle_id"]
    assert first_retry["execution_number"] == 2
    with repository.open_read_connection() as connection:
        retry_row = connection.execute(
            "SELECT retry_remote_cycle_id,request_key FROM catalyst_market_focus_jobs "
            "WHERE local_cycle_id=?",
            (first_retry["cycle_id"],),
        ).fetchone()
    assert tuple(retry_row) == (
        failed_remote.cycle_id,
        f"retry:{parent['cycle_id']}",
    )

    retry_client = RetryFocusClient(failed_remote.cycle_id)
    service = CatalystSyncService(
        settings(repository.path),
        repository,
        retry_client,  # type: ignore[arg-type]
        worker_id="worker-retry-submit",
        clock=lambda: utc(10, 5),
    )
    assert service.acquire()
    assert asyncio.run(service.process_market_focus_jobs()) == 1
    assert retry_client.create_calls == [
        {
            "expected_prepared_revision": None,
            "retry_cycle_id": failed_remote.cycle_id,
        }
    ]
    stored = repository.get_market_focus_cycle(first_retry["cycle_id"])
    assert stored["retry_of_cycle_id"] == parent["cycle_id"]
    assert failed_remote.cycle_id not in str(stored)


def test_unknown_remote_submission_cannot_be_queued_as_retry(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    parent = repository.enqueue_market_focus_cycle(
        7,
        last_consumed_revision=4,
        model="gpt-5.6-terra",
        reasoning="max",
        now=utc(10),
    )
    repository.due_market_focus_jobs("worker-unknown", now=utc(10, 1))
    assert repository.begin_market_focus_submission(
        parent["cycle_id"], "worker-unknown", now=utc(10, 1)
    )
    repository.apply_remote_market_focus_cycle(
        parent["cycle_id"],
        remote_cycle(
            status="failed",
            error_code="submission_outcome_unknown",
        ),
        worker_id="worker-unknown",
        now=utc(10, 2),
    )

    with pytest.raises(
        CatalystRepositoryError, match="market_focus_retry_outcome_unknown"
    ):
        repository.enqueue_market_focus_retry(parent["cycle_id"], now=utc(10, 3))


def test_failed_refresh_keeps_previous_market_focus_snapshot(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    repository.publish_market_focus_snapshot(
        hotspot_status(), [hotspot_item()], remote_cycle(), now=utc(10)
    )
    repository.record_stream_failure(
        "market_focus", "remote_timeout", retry_after_seconds=60, now=utc(10, 1)
    )
    snapshot = repository.market_focus_snapshot(
        stale_ttl_seconds=30, now=utc(10, 2)
    )
    assert snapshot["status"] == "stale"
    assert snapshot["items"][0]["prepared_revision"] == 7
    assert "remote_timeout" in snapshot["warnings"]


def test_same_origin_routes_are_local_only_and_revision_idempotent(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    repository.publish_market_focus_snapshot(
        hotspot_status(), [hotspot_item()], remote_cycle(), now=utc(10)
    )
    client = client_for(configured(path))

    status_response = client.get("/api/catalysts/hotspots/status")
    hotspots_response = client.get("/api/catalysts/hotspots?limit=1")
    latest_response = client.get("/api/catalysts/market-focus-cycles/latest")
    assert status_response.status_code == 200
    assert status_response.json()["prepared_revision"] == 7
    assert hotspots_response.json()["items"][0]["hot_score"] == 82.5
    assert latest_response.json()["cycle"]["cycle_id"] != "mfc_" + "a" * 32

    first = client.post(
        "/api/catalysts/market-focus-cycles",
        json={"expected_prepared_revision": 7},
    )
    second = client.post(
        "/api/catalysts/market-focus-cycles",
        json={"expected_prepared_revision": 7},
    )
    assert first.status_code == second.status_code == 202
    assert first.json()["cycle_id"] == second.json()["cycle_id"]
    assert "remote_cycle_id" not in first.text
    assert "openai" not in first.text.lower()

    cycle_id = first.json()["cycle_id"]
    assert client.get(f"/api/catalysts/market-focus-cycles/{cycle_id}").status_code == 200
    cancelled = client.post(
        f"/api/catalysts/market-focus-cycles/{cycle_id}/cancel"
    )
    assert cancelled.status_code == 202
    assert cancelled.json()["status"] == "cancelled"


def test_anonymous_focus_routes_expose_display_projection_only(tmp_path) -> None:
    path = tmp_path / "catalysts.db"
    repository = CatalystRepository(path)
    repository.initialize(now=utc(9))
    analysis = {
        "cycle_id": "mfc_" + "a" * 32,
        "as_of": utc(10),
        "market_summary": "A bounded market-focus summary.",
        "dominant_events": [],
        "market_uncertainties": ["Fixture uncertainty"],
        "affected_sectors": ["Technology"],
        "focus_ticker_assessments": [],
        "no_new_material_catalyst": False,
        "insufficient_context": False,
        "display_only": True,
    }
    repository.publish_market_focus_snapshot(
        hotspot_status(),
        [hotspot_item()],
        remote_cycle(status="completed", result=analysis),
        now=utc(10),
    )

    anonymous = client_for(configured(path), public_read=True)
    authenticated = client_for(
        configured(path), public_read=True, app_authenticated=True
    )

    anonymous_status = anonymous.get("/api/catalysts/hotspots/status").json()
    anonymous_hotspots = anonymous.get("/api/catalysts/hotspots?limit=1").json()
    anonymous_latest = anonymous.get(
        "/api/catalysts/market-focus-cycles/latest"
    ).json()
    authenticated_status = authenticated.get(
        "/api/catalysts/hotspots/status"
    ).json()
    authenticated_hotspots = authenticated.get(
        "/api/catalysts/hotspots?limit=1"
    ).json()
    authenticated_latest = authenticated.get(
        "/api/catalysts/market-focus-cycles/latest"
    ).json()

    assert anonymous_status["manual_enabled"] is False
    assert anonymous_status["action_enabled"] is False
    assert anonymous_status["capability"] == "disabled"
    assert "active_cycle_id" not in anonymous_status
    assert "cooldown_until" not in anonymous_status
    assert "schema_sha256" not in anonymous_status
    assert authenticated_status["manual_enabled"] is True
    assert authenticated_status["action_enabled"] is True
    assert "active_cycle_id" in authenticated_status

    anonymous_item = anonymous_hotspots["items"][0]
    assert anonymous_item["representative_title"] == "A bounded fixture event"
    assert anonymous_item["validated_tickers"] == ["NVDA"]
    assert "event_snapshot_json" not in anonymous_item
    assert "active_weights" not in anonymous_item
    assert "leased_cycle_id" not in anonymous_item
    assert "consumed_cycle_id" not in anonymous_item
    assert "consumed_at" not in anonymous_item
    assert "created_at" not in anonymous_item
    assert "event_snapshot_json" in authenticated_hotspots["items"][0]
    assert "active_weights" in authenticated_hotspots["items"][0]
    assert set(anonymous_hotspots) == {
        "status",
        "as_of",
        "data_through",
        "warnings",
        "items",
    }

    anonymous_cycle = anonymous_latest["cycle"]
    authenticated_cycle = authenticated_latest["cycle"]
    assert set(anonymous_latest) == {
        "status",
        "as_of",
        "data_through",
        "warnings",
        "cycle",
    }
    assert set(anonymous_cycle) == {
        "cycle_id",
        "status",
        "no_new_hot_events",
        "prepared_revision",
        "focus_revision",
        "snapshot_as_of",
        "event_group_count",
        "focus_symbol_count",
        "model",
        "reasoning_effort",
        "result",
        "error_code",
        "created_at",
        "completed_at",
        "updated_at",
    }
    assert anonymous_cycle["result"]["market_summary"] == analysis["market_summary"]
    for hidden_field in (
        "scheduled_slot",
        "idempotency_key",
        "retry_of_cycle_id",
        "execution_number",
        "trigger_type",
        "last_consumed_revision_at_start",
        "consumes_through_revision",
        "provider",
        "execution_mode",
        "input_schema_version",
        "input_hash",
        "max_output_tokens",
        "prompt_version",
        "output_schema_version",
        "attempt_count",
        "retrieve_error_count",
        "cancel_attempt_count",
        "next_attempt_at",
        "cancel_requested_at",
        "latency_ms",
        "usage_input_tokens",
        "usage_cached_input_tokens",
        "usage_cache_write_tokens",
        "usage_reasoning_tokens",
        "usage_output_tokens",
        "usage_total_tokens",
        "started_at",
    ):
        assert hidden_field not in anonymous_cycle
    for full_field in (
        "scheduled_slot",
        "idempotency_key",
        "execution_number",
        "trigger_type",
        "last_consumed_revision_at_start",
        "consumes_through_revision",
        "provider",
        "execution_mode",
        "input_schema_version",
        "input_hash",
        "max_output_tokens",
        "prompt_version",
        "output_schema_version",
        "attempt_count",
        "retrieve_error_count",
        "cancel_attempt_count",
        "next_attempt_at",
        "cancel_requested_at",
        "latency_ms",
        "usage_input_tokens",
        "usage_cached_input_tokens",
        "usage_cache_write_tokens",
        "usage_reasoning_tokens",
        "usage_output_tokens",
        "usage_total_tokens",
        "started_at",
    ):
        assert full_field in authenticated_cycle

    cycle_id = authenticated_cycle["cycle_id"]
    anonymous_detail = anonymous.get(
        f"/api/catalysts/market-focus-cycles/{cycle_id}"
    ).json()
    authenticated_detail = authenticated.get(
        f"/api/catalysts/market-focus-cycles/{cycle_id}"
    ).json()
    assert anonymous_detail == anonymous_cycle
    assert authenticated_detail == authenticated_cycle


def test_confidence_filter_consistently_controls_unanalyzed_items(tmp_path) -> None:
    repository = CatalystRepository(tmp_path / "catalysts.db")
    repository.initialize(now=utc(9))
    run_id = repository.begin_sync_run(
        "feed", snapshot_token="snapshot-confidence", now=utc(10, 8)
    )
    repository.stage_latest_page(
        run_id,
        [
            catalyst_item(sequence=2, updated_at=utc(10, 6), analysis=True),
            catalyst_item(
                sequence=3,
                updated_at=utc(10, 7),
                analysis=False,
                news_id=102,
            ),
        ],
    )
    repository.publish_latest(
        run_id,
        snapshot_token="snapshot-confidence",
        data_through=utc(10, 8),
        next_updated_after=utc(10, 8),
        watermark_sequence=3,
        now=utc(10, 8),
    )

    common = {"as_of": utc(10, 8), "window_hours": 72, "limit": 20}
    assert len(
        repository.list_feed(
            **common, min_confidence=0, include_unanalyzed=True
        )["items"]
    ) == 2
    assert len(
        repository.list_feed(
            **common, min_confidence=0, include_unanalyzed=False
        )["items"]
    ) == 1
    assert len(
        repository.ticker_feed(
            "NVDA",
            **common,
            min_confidence=1,
            include_unanalyzed=True,
        )["items"]
    ) == 1
    batch = repository.batch_tickers(
        ["NVDA"],
        **common,
        min_confidence=1,
        include_unanalyzed=True,
        include_neutral=True,
    )
    assert len(batch["results"]["NVDA"]["items"]) == 1
